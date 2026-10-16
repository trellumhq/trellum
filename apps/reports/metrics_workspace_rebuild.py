"""Build the generated Metrics report once after the 0.8 workspace change.

A Metrics artifact built before the paged comparison workspace draws no
sparklines or charts until it is built again, and a Metrics report without a
schedule would never be. The coordinator calls ``run_once`` when it starts. The
outcome is recorded under ``OpsState`` key ``metrics_workspace_rebuild``, so
only the first coordinator start after an upgrade (or on a fresh install, where
nothing is queued) does anything; a failed rebuild is never retried this way.
Bump ``WORKSPACE_VERSION`` to ask for one more rebuild after a later change.
"""
from __future__ import annotations

import logging

from django.db import transaction

from apps.core.models import OpsState

logger = logging.getLogger("trellum.worker")

KEY = "metrics_workspace_rebuild"
WORKSPACE_VERSION = 1


def run_once() -> int | None:
    """Queue the one rebuild per Metrics report. Returns how many were queued,
    or None when it already ran (or could not, in which case the next
    coordinator start tries again)."""
    try:
        return _queue_rebuilds()
    except Exception:  # a coordinator must start without it
        logger.exception("metrics workspace rebuild failed; retried at the next start")
        return None


def _queue_rebuilds() -> int | None:
    from apps.core.audit import audit_system
    from apps.reports.models import Report
    from apps.runner.models import Run
    from apps.runner.services import enqueue

    with transaction.atomic():
        done = OpsState.objects.select_for_update().filter(key=KEY).first()
        previous = done.payload.get("workspace_version") if done else None
        if isinstance(previous, int) and previous >= WORKSPACE_VERSION:
            return None
        queued = 0
        reports = (
            Report.objects.filter(
                present_in_scan=True, disabled=False, data_expired_at__isnull=True,
                refresh_suspended_at__isnull=True, kind=Report.KIND_REPORT,
                slug="metrics", tags__contains=["metrics"],
            ).select_related("studio__org")
        )
        for report in reports:
            if Run.objects.filter(report=report, status__in=Run.ACTIVE_STATUSES).exists():
                continue  # already building or waiting to
            # "catchup" is the existing trigger for work the system queues itself.
            enqueue(report, trigger="catchup")
            audit_system(
                "run.auto_enqueue", org=report.studio.org, report_id=report.pk,
                slug=report.slug, studio=report.studio.slug, trigger="catchup",
                reason="metrics_workspace",
            )
            queued += 1
        OpsState.record(KEY, workspace_version=WORKSPACE_VERSION, queued=queued)
    if queued:
        logger.info(f"queued {queued} Metrics report build(s) for the 0.8 workspace")
    return queued
