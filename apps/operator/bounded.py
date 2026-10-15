"""Bounded reads behind the operator console's tables.

The tables look as they always did: every row, no pager and no filter. Reads
stay flat by selecting only the columns a table shows and by counting related
rows for the rows on the page alone. Far above any realistic size a table stops
growing and says how many rows it left out.
"""
from apps.orgs import quotas

LIST_CAP = 500


def capped(query, cap=LIST_CAP):
    """``(rows, more)``: up to ``cap`` rows, and ``None`` unless rows were left out.

    ``more`` is ``{"shown": cap, "total": N}``; the total is only counted in
    that case.
    """
    rows = list(query[:cap + 1])
    if len(rows) <= cap:
        return rows, None
    return rows[:cap], {"shown": cap, "total": query.count()}


def usage(org):
    """Live DB usage; uploaded files come from the last coordinator measurement.

    Limits follow ``quotas.usage_state``: they are the enforced ones, so they
    read "unlimited" while quotas are off. Measuring uploaded files walks
    storage, so it never happens while a page renders.
    """
    from apps.core.operator_observations import age_text, snapshot

    caps = quotas.limits_for(org)
    observation = snapshot("upload-usage", org.pk)
    complete = observation.get("quality") == "complete" and type(observation.get("bytes")) is int
    return {
        "enforced": bool(caps),
        "active_runs": quotas.active_runs(org),
        "max_concurrent_runs": int(caps.get("max_concurrent_runs") or 0),
        "build_minutes": quotas.build_minutes_this_month(org),
        "monthly_build_minutes": int(caps.get("monthly_build_minutes") or 0),
        "studios": quotas.studio_count(org),
        "max_studios": int(caps.get("max_studios") or 0),
        "reports": quotas.report_count(org),
        "max_reports": int(caps.get("max_reports") or 0),
        "max_memory_mb": int(caps.get("max_memory_mb") or 0),
        "storage_mb": round(observation["bytes"] / (1024 * 1024), 1) if complete else None,
        "storage_observation": observation,
        "storage_age": age_text(observation.get("age_seconds")),
        "max_storage_mb": int(caps.get("max_storage_mb") or 0),
        "month": quotas.month_start().date().isoformat(),
    }
