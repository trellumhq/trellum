"""Bounded, resumable worker background repair of artifact projections."""
from __future__ import annotations

import logging
import threading
import time

from django.db import close_old_connections
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.core import storage
from .models import Report, ReportSourceSummary
from .source_summary import INSPECTION_BYTES, reconcile

logger = logging.getLogger(__name__)
MAX_INSPECTIONS = 25
BATCH_SECONDS = 5.0  # Admission window, not a preemption guarantee for one I/O.
BATCH_BYTES = 64 * 1024 * 1024
MAX_INSPECTION_BYTES = INSPECTION_BYTES
POLL_SECONDS = 5.0


def eligible_reports(*, pools=None):
    from apps.runner.models import Run

    active = Run.objects.filter(report_id=OuterRef("pk"), status__in=Run.ACTIVE_STATUSES)
    rows = Report.objects.filter(present_in_scan=True, data_expired_at__isnull=True).annotate(
        active_source_run=Exists(active)
    ).filter(Q(active_source_run=False) | Q(source_summary__operation__startswith="expiry:"))
    if pools:
        rows = rows.filter(studio__pool__in=pools)
    return rows.filter(
        Q(source_summary__isnull=True) | Q(source_summary__next_check_at__lte=timezone.now())
    ).filter(
        Q(source_summary__lease_until__isnull=True) | Q(source_summary__lease_until__lte=timezone.now())
    ).order_by(F("source_summary__next_check_at").asc(nulls_first=True), "pk")


def run_batch(*, pools=None, limit=MAX_INSPECTIONS):
    if not 1 <= limit <= MAX_INSPECTIONS:
        raise ValueError("batch limit must be between 1 and 25")
    started = time.monotonic()
    # Limit selection too: no unbounded catalog list in worker memory.
    ids = list(eligible_reports(pools=pools).values_list("pk", flat=True)[:limit])
    results = []
    used = 0
    for pk in ids:
        if time.monotonic() - started >= BATCH_SECONDS or used > BATCH_BYTES - MAX_INSPECTION_BYTES:
            break
        result = reconcile(pk)
        used += result["bytes"]
        results.append({"report_id": pk, **result})
        # Outage/oversize has already installed per-row backoff. A batch stops
        # on it so a broken provider does not receive25successive probes.
        if result["status"] == "unavailable" or result.get("projection_unavailable"):
            # Selective extension-store outages also latch this admission pass.
            break
    return {"inspections": len(results), "bytes": used, "seconds": time.monotonic() - started, "results": results}


class ReconciliationThread(threading.Thread):
    def __init__(self, *, pools=None):
        super().__init__(name="source-reconciliation", daemon=True)
        self.pools = pools
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.is_set():
            close_old_connections()
            try:
                result = run_batch(pools=self.pools)
                if result["inspections"]:
                    logger.info("source reconciliation batch: %s", result)
            except Exception:
                logger.exception("source reconciliation batch failed")
            finally:
                close_old_connections()
            self.stopping.wait(POLL_SECONDS)

    def stop(self):
        self.stopping.set()
        self.join(timeout=5)


def start_for_worker(*, is_coordinator, is_runner, pools=None):
    # Remote shared storage is coordinator-owned; local files belong to the
    # runner's pool. Multiple local runners coordinate using row leases.
    if (storage.is_remote() and not is_coordinator) or (not storage.is_remote() and not is_runner):
        return None
    thread = ReconciliationThread(pools=None if storage.is_remote() else pools)
    thread.start()
    return thread
