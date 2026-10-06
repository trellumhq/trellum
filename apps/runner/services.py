"""Queue operations shared by web views and the worker."""
from __future__ import annotations

from django.db import transaction
from django.db.models import Count, Min

from apps.runner.models import Run


def enqueue(report, *, cache_mode: str = "normal", trigger: str = "manual", user=None) -> str:
    """Insert a queued Run unless one is already active for this report.

    Returns the legacy status string portal.js expects:
    "queued" | "already_running".
    """
    if cache_mode not in dict(Run.CACHE_MODES):
        cache_mode = "normal"
    with transaction.atomic():
        active = (
            Run.objects.select_for_update()
            .filter(report=report, status__in=Run.ACTIVE_STATUSES)
            .first()
        )
        if active is not None:
            return "already_running" if active.status != Run.QUEUED else "queued"
        Run.objects.create(
            report=report,
            studio=report.studio,
            slug=report.slug,
            pool=report.studio.pool,
            cache_mode=cache_mode,
            priority=report.priority,
            trigger=trigger,
            requested_by=user,
        )
    return "queued"


def next_run_ids(pools=None, limit: int = 8) -> list:
    """Queued run ids in the order they *should* start, fairest first.

    Why not simply ``ORDER BY priority, created_at``: that ordering is global,
    so one organization queueing two hundred reports pushes every other tenant
    behind them. Worse, ``priority`` comes from ``report.yaml`` — tenant-authored
    — so a tenant could promote itself to the front of everyone else's queue.

    So orgs are scheduled round-robin: the one with the fewest builds in flight
    goes next, ties broken by who has waited longest. Tenant priority (lower
    first, like the dashboard) still applies, but only *within* an org, where
    it is nobody else's business.

    Returns several candidates because a racing runner may take the first one.
    """
    from apps.orgs import quotas

    queued = Run.objects.filter(status=Run.QUEUED, stop_requested=False)
    if pools:
        queued = queued.filter(pool__in=pools)

    waiting = list(
        queued.values("studio__org_id").annotate(oldest=Min("created_at"))
    )
    if not waiting:
        return []

    active = {
        row["studio__org_id"]: row["n"]
        for row in Run.objects.filter(status__in=(Run.STARTING, Run.RUNNING))
        .values("studio__org_id")
        .annotate(n=Count("id"))
    }
    caps = quotas.concurrency_caps([row["studio__org_id"] for row in waiting])

    eligible = []
    for row in waiting:
        org_id = row["studio__org_id"]
        running = active.get(org_id, 0)
        cap = caps.get(org_id, 0)
        if cap and running >= cap:
            continue  # at its own limit; other orgs go first
        eligible.append((running, row["oldest"], org_id))
    if not eligible:
        return []

    eligible.sort()  # fewest in flight, then longest waiting
    # We need at most `limit` candidates, so at most `limit` orgs can appear.
    eligible = eligible[:limit]

    # One run per org per round, so a single pass spreads across tenants
    # instead of handing the whole batch to whichever org sorted first.
    buckets = [
        list(
            queued.filter(studio__org_id=org_id)
            .order_by("priority", "created_at")
            .values_list("pk", flat=True)[:limit]
        )
        for _, _, org_id in eligible
    ]

    ids: list = []
    for depth in range(limit):
        progressed = False
        for bucket in buckets:
            if depth < len(bucket):
                ids.append(bucket[depth])
                progressed = True
                if len(ids) >= limit:
                    return ids
        if not progressed:
            break
    return ids


def request_stop(report) -> bool:
    """Stop a queued run outright; flag a running one for the worker."""
    stopped_queued = (
        Run.objects.filter(report=report, status=Run.QUEUED).update(
            status=Run.STOPPED, stop_requested=True
        )
        > 0
    )
    flagged_running = (
        Run.objects.filter(
            report=report, status__in=(Run.STARTING, Run.RUNNING)
        ).update(stop_requested=True)
        > 0
    )
    return stopped_queued or flagged_running


def report_state(report) -> tuple[str, float | None]:
    """("running"|"queued"|"idle", elapsed_seconds) — the legacy status pair."""
    from django.utils import timezone

    active = (
        Run.objects.filter(report=report, status__in=Run.ACTIVE_STATUSES)
        .order_by("created_at")
        .first()
    )
    if active is None:
        return "idle", None
    if active.status == Run.QUEUED:
        return "queued", None
    elapsed = None
    if active.started_at:
        elapsed = round((timezone.now() - active.started_at).total_seconds(), 1)
    return "running", elapsed
