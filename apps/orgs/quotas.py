"""Per-organization quota enforcement.

Deliberately the same shape as ``apps.assistant.budget``: ask ``precheck`` before
doing the work, measure usage after. Two rules keep this safe for the
single-tenant install that is our default:

1. A missing ``OrgQuota`` row, or a zero on any field, means *no limit*.
2. Nothing is enforced unless the operator enables ``TRELLUM_QUOTAS_ENABLED``.

Usage is derived from the ``Run`` table rather than a parallel ledger — runs
already carry ``started_at``/``finished_at``, and a second source of truth for
"how much did this org build" would only drift.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_tz
from typing import NamedTuple

from django.conf import settings
from django.db import models
from django.db.models import F, Sum


class Decision(NamedTuple):
    """Whether an action may proceed, and what to do when it may not.

    ``fatal`` is the distinction that matters to the claim loop: a concurrency
    cap clears itself when a sibling build finishes, so the run waits. An
    exhausted month or an oversized build never clears, so the run fails now
    rather than sitting in the queue forever pretending it might start.
    """

    allowed: bool
    reason: str = ""
    fatal: bool = False


ALLOWED = Decision(True)

LIMIT_FIELDS = (
    "max_concurrent_runs",
    "monthly_build_minutes",
    "max_studios",
    "max_reports",
    "max_memory_mb",
    "max_storage_mb",
)


def month_start() -> datetime:
    now = datetime.now(dt_tz.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def limits_for(org) -> dict:
    """This org's operator-configured limits, or {} when it has none."""
    if not settings.TRELLUM_QUOTAS_ENABLED:
        return {}

    from apps.orgs.models import OrgQuota

    return OrgQuota.objects.filter(org=org).values(*LIMIT_FIELDS).first() or {}


def limit(org, field: str) -> int:
    """One numeric limit. 0 (or absent) means unlimited."""
    return int(limits_for(org).get(field) or 0)


def quota_for(org):
    """The org's quota row, or None when it has no limits.

    Prefer :func:`limits_for` for enforcement; this exists for the operator
    console, which edits the row itself.
    """
    from apps.orgs.models import OrgQuota

    if not settings.TRELLUM_QUOTAS_ENABLED:
        return None
    return OrgQuota.objects.filter(org=org).first()


# ── measurement ──────────────────────────────────────────────────────────────

def active_runs(org) -> int:
    """Builds in flight for this org, across every runner."""
    from apps.runner.models import Run

    return Run.objects.filter(
        studio__org=org, status__in=(Run.STARTING, Run.RUNNING)
    ).count()


def build_minutes_this_month(org) -> float:
    """Wall-clock build minutes this calendar month.

    Postgres subtracts the two timestamps for us, so this stays one query even
    with a large history.
    """
    from apps.runner.models import Run

    rows = Run.objects.filter(
        studio__org=org,
        finished_at__isnull=False,
        started_at__isnull=False,
        finished_at__gte=month_start(),
    ).aggregate(total=Sum(F("finished_at") - F("started_at")))
    total = rows["total"]
    return round(total.total_seconds() / 60.0, 2) if total else 0.0


def studio_count(org) -> int:
    from apps.studios.models import Studio

    return Studio.objects.filter(org=org).count()


def report_count(org) -> int:
    from apps.reports.models import Report

    return Report.objects.filter(studio__org=org, present_in_scan=True).count()


def storage_bytes_used(org) -> int:
    """Bytes of uploaded data-source files this org holds.

    Measured from disk rather than from a stored counter: the files are the
    truth, and a counter would drift the first time an upload half-failed or an
    operator cleaned up by hand. Only sources the portal OWNS are counted —
    a repo-backed file arrived by git push and is already bounded by the repo.
    """
    from apps.datasources.materialize import stored_file
    from apps.datasources.models import DataSource

    total = 0
    rows = DataSource.objects.filter(
        models.Q(org=org) | models.Q(studio__org=org)
    ).select_related("org", "studio", "studio__org")
    for ds in rows:
        if not ds.is_uploaded:
            continue
        path = stored_file(ds)
        try:
            if path is not None and path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def usage_state(org) -> dict:
    """Snapshot for the operator console and the org's own settings page."""
    caps = limits_for(org)
    return {
        "enforced": bool(caps),
        "active_runs": active_runs(org),
        "max_concurrent_runs": int(caps.get("max_concurrent_runs") or 0),
        "build_minutes": build_minutes_this_month(org),
        "monthly_build_minutes": int(caps.get("monthly_build_minutes") or 0),
        "studios": studio_count(org),
        "max_studios": int(caps.get("max_studios") or 0),
        "reports": report_count(org),
        "max_reports": int(caps.get("max_reports") or 0),
        "max_memory_mb": int(caps.get("max_memory_mb") or 0),
        # Measured unconditionally, like studios and reports above: the console
        # shows real usage even where quotas are recorded but not enforced.
        "storage_mb": round(storage_bytes_used(org) / (1024 * 1024), 1),
        "max_storage_mb": int(caps.get("max_storage_mb") or 0),
        "month": month_start().date().isoformat(),
    }


# ── enforcement ──────────────────────────────────────────────────────────────

def check_run_allowed(org, memory_mb: int | None = None) -> Decision:
    """The single gate before starting a build for this org.

    Ordered cheapest-first, and fatal checks before waitable ones: there is no
    point telling someone to wait for capacity they may never be allowed to use.
    """
    caps = limits_for(org)
    if not caps:
        return ALLOWED

    cap = int(caps.get("monthly_build_minutes") or 0)
    if cap:
        used = build_minutes_this_month(org)
        if used >= cap:
            return Decision(
                False,
                f"monthly build-minute quota reached: {used:.0f} / {cap} minutes. "
                f"Raise the quota or wait for next month.",
                fatal=True,
            )

    cap = int(caps.get("max_memory_mb") or 0)
    if cap and memory_mb and memory_mb > cap:
        return Decision(
            False,
            f"builds get {memory_mb} MB, above the organization's {cap} MB "
            f"per-build limit. Lower TRELLUM_DEFAULT_JOB_MEMORY_MB or raise the quota.",
            fatal=True,
        )

    cap = int(caps.get("max_concurrent_runs") or 0)
    if cap and active_runs(org) >= cap:
        return Decision(
            False,
            f"organization concurrency limit reached ({cap} builds); "
            f"this run waits for one to finish.",
        )
    return ALLOWED


def check_studio_allowed(org) -> Decision:
    """Before creating a studio.

    Unlike the run reasons, which land in a log line, this one is shown to a
    person as a flash message — so it reads as a sentence.
    """
    cap = limit(org, "max_studios")
    if cap and studio_count(org) >= cap:
        return Decision(
            False,
            f"This organization is limited to {cap} studio"
            f"{'s' if cap != 1 else ''}.",
            fatal=True,
        )
    return ALLOWED


def check_upload_allowed(org, incoming_bytes: int, replacing_bytes: int = 0) -> Decision:
    """Before writing an uploaded data-source file.

    ``replacing_bytes`` is the size of the file this upload overwrites, which
    is freed by the same operation — otherwise replacing a 200 MB file with an
    identical one would be refused at exactly 100% of quota.

    Like :func:`check_studio_allowed`, the reason is shown to a person.
    """
    cap_mb = limit(org, "max_storage_mb")
    if not cap_mb:
        return ALLOWED
    cap = cap_mb * 1024 * 1024
    projected = storage_bytes_used(org) - max(0, replacing_bytes) + incoming_bytes
    if projected > cap:
        used_mb = (storage_bytes_used(org) - max(0, replacing_bytes)) / (1024 * 1024)
        return Decision(
            False,
            f"This organization is limited to {cap_mb} MB of uploaded files and "
            f"is using {used_mb:.0f} MB. Delete a file or raise the quota.",
            fatal=True,
        )
    return ALLOWED


def reports_over_cap(org, prospective: int) -> int:
    """How many reports beyond the org's cap ``prospective`` would be.

    Reports arrive by git push, not by a form, so there is no request to refuse.
    The scan keeps the ones that fit and reports the overage — dropping rows
    silently would look like the portal had lost the reports.
    """
    cap = limit(org, "max_reports")
    if not cap:
        return 0
    return max(0, prospective - cap)


def concurrency_caps(org_ids=None) -> dict:
    """{org_id: max_concurrent_runs} for orgs with a nonzero cap.

    A bulk read for the claim loop, which needs caps for every org with queued
    work and must not issue one query per org.
    """
    if not settings.TRELLUM_QUOTAS_ENABLED:
        return {}

    from apps.orgs.models import OrgQuota

    rows = OrgQuota.objects.filter(max_concurrent_runs__gt=0)
    if org_ids is not None:
        rows = rows.filter(org_id__in=list(org_ids))
    return dict(rows.values_list("org_id", "max_concurrent_runs"))
