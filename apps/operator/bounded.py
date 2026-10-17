"""Bounded reads behind the operator console's tables.

The tables look as they always did: every row, no pager and no filter. Reads
stay flat by selecting only the columns a table shows and by counting related
rows for the rows on the page alone. Far above any realistic size a table stops
growing and says how many rows it left out.
"""
from apps.core.limits import INLINE_LIMIT
from apps.orgs import quotas

LIST_CAP = INLINE_LIMIT


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
    """Live DB usage plus the org's uploaded files.

    Uploaded files are measured by the coordinator while the page is open; the
    page shows the last measurement and updates itself. Without a live
    coordinator they are measured here, as they always were. Limits come from
    ``quotas.usage_state``: they are the enforced ones, so they read "unlimited"
    while quotas are off.
    """
    from apps.core import operator_observations as observations

    live = observations.available()
    measured = observations.poll(observations.usage_key(org.pk)) if live else {}
    if "bytes" in measured:
        used = measured["bytes"]
    else:
        used = None if live else quotas.storage_bytes_used(org)  # None: the first measurement is on its way
    return {
        **quotas.usage_state(org),
        "storage_mb": None if used is None else round(used / (1024 * 1024), 1),
        "storage_age": observations.age_text(measured.get("age_seconds")),
        "storage_age_seconds": measured.get("age_seconds"),
        "storage_measured_at": measured.get("completed_at"),
        "polling": live,
    }
