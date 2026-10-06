"""Read-only run statistics for the dashboard.

Everything here aggregates the Run table in SQL — duration and queue wait are
derived from the timestamp columns (same precedent as
``apps.orgs.quotas.build_minutes_this_month``), never stored, and percentiles
run in Postgres via ``PERCENTILE_CONT``. Callers get plain dicts ready for a
JSON response; ``None`` means "no data" (empty window, or a metric the
platform can't report — peak memory is Linux-only).
"""
from __future__ import annotations

from django.db.models import (
    Aggregate,
    Avg,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Q,
)
from django.db.models.functions import Extract
from django.utils import timezone

from .models import Run

TERMINAL_STATUSES = (Run.SUCCESS, Run.ERROR, Run.STOPPED, Run.TIMEOUT, Run.OOM_KILLED)


class Percentile(Aggregate):
    """``PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)`` — Postgres only."""

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"

    def __init__(self, expression, percentile: float, **extra):
        super().__init__(expression, percentile=percentile, output_field=FloatField(), **extra)


def duration_s():
    """Run duration in seconds as a SQL expression (NULL until finished)."""
    # Extract defaults to an integer output field, which would truncate
    # fractional seconds client-side in Max()/direct annotations.
    return Extract(
        ExpressionWrapper(F("finished_at") - F("started_at"), output_field=DurationField()),
        "epoch",
        output_field=FloatField(),
    )


def queue_wait_s():
    """Enqueue→start wait in seconds as a SQL expression (NULL until started)."""
    return Extract(
        ExpressionWrapper(F("started_at") - F("created_at"), output_field=DurationField()),
        "epoch",
        output_field=FloatField(),
    )


def _round(value, digits: int = 1):
    return None if value is None else round(float(value), digits)


def summarize(qs) -> dict:
    """One aggregate query over an already-filtered run queryset.

    NULL timestamps and NULL peaks drop out of the aggregates on their own
    (both plain and ordered-set aggregates ignore NULL inputs).
    """
    agg = qs.aggregate(
        runs=Count("id"),
        success=Count("id", filter=Q(status=Run.SUCCESS)),
        avg_duration_s=Avg(duration_s()),
        p50_duration_s=Percentile(duration_s(), 0.5),
        p95_duration_s=Percentile(duration_s(), 0.95),
        avg_queue_wait_s=Avg(queue_wait_s()),
        p95_queue_wait_s=Percentile(queue_wait_s(), 0.95),
        avg_peak_memory_mb=Avg("peak_memory_mb"),
        max_peak_memory_mb=Max("peak_memory_mb"),
    )
    runs = agg["runs"] or 0
    return {
        "runs": runs,
        "success": agg["success"] or 0,
        "success_rate": round(agg["success"] / runs, 3) if runs else None,
        "avg_duration_s": _round(agg["avg_duration_s"]),
        "p50_duration_s": _round(agg["p50_duration_s"]),
        "p95_duration_s": _round(agg["p95_duration_s"]),
        "avg_queue_wait_s": _round(agg["avg_queue_wait_s"]),
        "p95_queue_wait_s": _round(agg["p95_queue_wait_s"]),
        "avg_peak_memory_mb": _round(agg["avg_peak_memory_mb"], 0),
        "max_peak_memory_mb": agg["max_peak_memory_mb"],
    }


def report_aggregates(report, *, window_days: int = 90) -> dict:
    """Aggregates for one report's drawer: summary + per-status counts."""
    cutoff = timezone.now() - timezone.timedelta(days=window_days)
    qs = report.runs.filter(status__in=TERMINAL_STATUSES, created_at__gte=cutoff)
    out = summarize(qs)
    out["by_status"] = {
        row["status"]: row["n"] for row in qs.values("status").annotate(n=Count("id"))
    }
    out["window_days"] = window_days
    return out


def studio_run_stats(studio, *, window_days: int = 30) -> dict[str, dict]:
    """Bulk per-slug stats for the Operations view, two queries total.

    Returns ``{slug: {"last": {...} | None, "agg": {...}}}`` over terminal
    runs in the window. Grouping by slug (not report FK) rides the existing
    ``(studio, slug, -created_at)`` index and spans report renames the same
    way the rest of the dashboard does.
    """
    cutoff = timezone.now() - timezone.timedelta(days=window_days)
    qs = Run.objects.filter(
        studio=studio, status__in=TERMINAL_STATUSES, created_at__gte=cutoff
    )

    stats: dict[str, dict] = {}
    for row in qs.values("slug").annotate(
        runs=Count("id"),
        success=Count("id", filter=Q(status=Run.SUCCESS)),
        avg_duration_s=Avg(duration_s()),
        p95_duration_s=Percentile(duration_s(), 0.95),
        avg_peak_memory_mb=Avg("peak_memory_mb"),
        max_peak_memory_mb=Max("peak_memory_mb"),
    ):
        runs = row["runs"] or 0
        stats[row["slug"]] = {
            "last": None,
            "agg": {
                "runs": runs,
                "success_rate": round(row["success"] / runs, 3) if runs else None,
                "avg_duration_s": _round(row["avg_duration_s"]),
                "p95_duration_s": _round(row["p95_duration_s"]),
                "avg_peak_memory_mb": _round(row["avg_peak_memory_mb"], 0),
                "max_peak_memory_mb": row["max_peak_memory_mb"],
            },
        }

    # Latest terminal run per slug: DISTINCT ON (Postgres) keyed to the same
    # order_by prefix.
    for run in qs.order_by("slug", "-created_at").distinct("slug"):
        entry = stats.get(run.slug)
        if entry is not None:
            entry["last"] = {
                "status": run.status,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "duration_s": run.duration_seconds,
                "queue_wait_s": run.queue_wait_seconds,
                "peak_memory_mb": run.peak_memory_mb,
            }
    return stats
