"""Bounded expiry cleanup; only a worker/management command may invoke it.

No worker hook is installed by this module. Each tick considers<=25 states
and physically deletes<=500 TOTAL rows, including state and receipt rows.
"""
from __future__ import annotations

import logging
import threading

from django.db import close_old_connections, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import AnnotationFilterMembership, AnnotationFilterReceipt, AnnotationFilterState

MAX_CANDIDATES = 25
MAX_DELETIONS = 500
INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)


def _expired(now):
    return Q(status="retiring") | Q(expires_at__lte=now)


def cleanup_once(*, now=None, max_candidates=MAX_CANDIDATES, max_rows=MAX_DELETIONS):
    if type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise ValueError("candidate budget must be 1..25")
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_DELETIONS:
        raise ValueError("delete budget must be 1..500")
    if connection.vendor != "postgresql":
        raise RuntimeError("annotation filter cleanup requires PostgreSQL")
    now = now or timezone.now()
    if not timezone.is_aware(now):
        raise ValueError("aware cleanup time required")
    candidates = list(AnnotationFilterState.objects.filter(_expired(now)).order_by("expires_at", "id").values_list("id", flat=True)[:max_candidates])
    deleted = dict(memberships=0, receipts=0, states=0)
    visited, marked = 0, 0
    for state_id in candidates:
        remaining = max_rows - sum(deleted.values())
        if remaining <= 0:
            break
        with transaction.atomic():
            # Same parent lock as mutations, skip rather than block. Recheck
            # expiry after discovery so a legitimate touch can win the race.
            state = AnnotationFilterState.objects.filter(_expired(now), pk=state_id).select_for_update(skip_locked=True).first()
            if state is None:
                continue
            visited += 1
            if state.status != "retiring":
                state.status = "retiring"
                state.save(update_fields=["status"])
                marked += 1
            for name, model in (("memberships", AnnotationFilterMembership), ("receipts", AnnotationFilterReceipt)):
                remaining = max_rows - sum(deleted.values())
                if remaining <= 0:
                    break
                ids = list(model.objects.filter(state_id=state_id).order_by("id").values_list("id", flat=True)[:remaining])
                if ids:
                    # Raw deletion deliberately bypasses Django's unbounded
                    # cascade collector. These two models have no dependents.
                    deleted[name] += model.objects.filter(pk__in=ids)._raw_delete("default")
            remaining = max_rows - sum(deleted.values())
            if remaining > 0 and not AnnotationFilterMembership.objects.filter(state_id=state_id).exists() and not AnnotationFilterReceipt.objects.filter(state_id=state_id).exists():
                # Database FK checks prevent deleting a parent with children;
                # never invoke ORM state.delete() and collect an entire map.
                deleted["states"] += AnnotationFilterState.objects.filter(pk=state_id)._raw_delete("default")
    return dict(candidates=len(candidates), visited=visited, marked=marked, deleted=deleted, total_deleted=sum(deleted.values()))


class CleanupWorker:
    """Optional maintenance lifecycle for the coordinating worker owner.

    First tick follows the60second wait; no startup backlog drain. stop() may
    report False if an in-progress bounded DB operation has not yet completed;
    it does not claim a preemptive shutdown deadline or kill another worker.
    """
    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="annotation-filter-cleanup", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=5):
        self._stop.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _run(self):
        close_old_connections()
        try:
            while not self._stop.wait(INTERVAL_SECONDS):
                try:
                    cleanup_once()
                except Exception as exc:  # maintenance errors must not expose SQL/source data
                    logger.warning("Annotation filter cleanup unavailable (%s)", type(exc).__name__)
                finally:
                    close_old_connections()
        finally:
            close_old_connections()
