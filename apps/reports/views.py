"""Studio-scoped dashboard, JSON API (legacy-shape), report serving,
and per-user favorites."""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q, Sum
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import roles, storage
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.http import int_param, json_body
from apps.core.permissions import (
    effective_roles,
    get_effective,
    require_org_role,
    require_studio_role,
)
from apps.core.report_access import (
    bulk_can_view_report,
    require_full_studio_visibility,
    selected_report_access_block_reason,
    visible_reports,
)
from apps.reports import notify
from apps.reports.models import (
    DEFAULT_LIVE_QUERY_RATE_LIMIT,
    EmailSchedule,
    MetricDefinition,
    OrgLiveQueryPolicy,
    OrgSharePolicy,
    Report,
    ReportFavorite,
    ReportViewDaily,
    ReportViewEvent,
    ShareLink,
    get_share_policy,
    live_query_rate_limit,
    record_view,
)
from apps.reports.scan import (
    _built_recently,
    _get_html_entry,
    _view_stats_for,
    build_registry_payload,
    sync_studio_registry,
)
from apps.runner import stats as run_stats
from apps.runner.models import Run, WorkerHeartbeat
from apps.runner.services import enqueue, report_state, request_stop

logger = logging.getLogger(__name__)

_SERVER_START = time.time()


def _get_report(request, slug: str) -> Report:
    report = Report.objects.filter(
        studio=request.studio, slug=slug, present_in_scan=True
    ).select_related("studio", "studio__org").first()
    if report is None:
        raise Http404
    scope = request.org_roles.report_scope_for(request.studio)
    if scope is not None and report.pk not in scope:
        raise Http404
    return report


def _selected_content_block(request, report):
    if report.audience != Report.AUDIENCE_PRIVATE and request.org_roles.has_full_studio_visibility(request.studio):
        return None
    if not selected_report_access_block_reason():
        return None
    return HttpResponse(
        "Selected report access is temporarily unavailable.",
        status=503,
        content_type="text/plain",
    )


def _visible_registry_payload(request) -> dict:
    payload = build_registry_payload(request.studio)
    scope = request.org_roles.report_scope_for(request.studio)
    if scope is None:
        return payload
    return {
        **payload,
        "reports": [row for row in payload.get("reports", []) if row.get("id") in scope],
        # Source inventory belongs to the studio-wide Operations surface.
        "source_states": [],
    }


# ── Dashboard shell ─────────────────────────────────────────────────────────

def _dashboard_page(request, *, initial_view: str):
    """The SPA shell shared by Reports, Analyses and Operations.
    ``initial_view`` ("reports" | "analyses" | "ops") rides in the portal context;
    portal.js boots the matching surface — these are pages with URLs, not
    client-side view modes (the studio tab bar navigates between them)."""
    from django.middleware.csrf import get_token

    get_token(request)  # guarantee the csrftoken cookie for portal.js
    ctx_blob = {
        "org": {"slug": request.org.slug, "name": request.org.name},
        "studio": {"slug": request.studio.slug, "name": request.studio.name},
        "prefix": f"/s/{request.org.slug}/{request.studio.slug}",
        "user": {
            "email": request.user.email,
            "name": request.user.display_name,
            "role": request.studio_role,
            "is_org_admin": request.org_roles.is_org_admin,
        },
        "production": not settings.DEBUG,
        "initial_view": initial_view,
    }
    page_title = {"ops": "Operations", "analyses": "Analyses"}.get(initial_view, "Reports")
    return render(
        request,
        "portal/index.html",
        {
            "org": request.org,
            "studio": request.studio,
            "portal_ctx_json": json.dumps(ctx_blob),
            "studio_role": request.studio_role,
            "console_page_title": page_title,
            # studio_theme (the viewer's named theme, or "" for the Trellum
            # default) comes from the shell context processor, which already
            # merges into every render() call -- no need to duplicate it here.
        },
    )


@require_studio_role(roles.VIEWER)
def dashboard(request, org_slug, studio_slug):  # noqa: ARG001
    # Operations used to be a client-side view mode here (?view=ops, and
    # ?view=health before that); it is its own page now. Old deep links and
    # bookmarks keep working via this redirect, other params intact.
    if request.GET.get("view") in ("ops", "health"):
        params = request.GET.copy()
        del params["view"]
        qs = f"?{params.urlencode()}" if params else ""
        return redirect(f"/s/{org_slug}/{studio_slug}/operations{qs}")
    return _dashboard_page(request, initial_view="reports")


@require_studio_role(roles.VIEWER)
def analyses(request, org_slug, studio_slug):  # noqa: ARG001
    return _dashboard_page(request, initial_view="analyses")


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def operations(request, org_slug, studio_slug):  # noqa: ARG001
    """The Operations tab: run/queue state, system actions, data sources.
    Same template and permission gate as the dashboard — portal.js already
    rendered ops as a distinct surface, this promotes it to a page."""
    return _dashboard_page(request, initial_view="ops")


# ── Studio analytics (internal planning#4 follow-up) ──────────────────────────────
#
# "Who's actually looking at these reports" for the whole studio, one table.
# Developers/admins only -- the same gate as the run/registry-refresh
# controls the dashboard already has, and stricter than the dashboard's own
# quiet per-card meta line (VIEWER-visible): the raw who/when detail here is
# closer to the report page's own Activity panel (also developer/admin-only,
# see api_report_views above) than to the dashboard's aggregate badge.

#: The range selector's choices. Capped at 90: that's exactly as far back as
#: the raw event log goes (apps.core.retention.purge_report_view_events, 90
#: days by default) -- a wider window would silently under-count share
#: views and unique viewers (both read from the raw log) rather than answer
#: the question asked, so the cap keeps every column in the table honest
#: about the same window instead of quietly disagreeing with each other.
_ANALYTICS_DEFAULT_DAYS = 30
_ANALYTICS_MAX_DAYS = 90


def _analytics_days(raw) -> int:
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return _ANALYTICS_DEFAULT_DAYS
    if days <= 0:
        return _ANALYTICS_DEFAULT_DAYS
    return min(days, _ANALYTICS_MAX_DAYS)


@require_studio_role(roles.DEVELOPER)
def analytics(request, org_slug, studio_slug):  # noqa: ARG001
    """One row per report, computed with a handful of aggregate queries
    across the WHOLE studio -- never one query per report. Reports with no
    views still get a row: the loop below starts from every
    present-in-scan Report and reads each stat dict with ``.get(pk,
    default)``, which is what makes this a left join rather than an inner
    one (a query built the other way around -- starting from
    ReportViewEvent/ReportViewDaily -- would silently drop a report nobody
    has ever looked at, exactly the report this page most needs to surface).
    """
    from datetime import timedelta

    days = _analytics_days(request.GET.get("days"))
    since = timezone.now() - timedelta(days=days)

    reports = list(Report.objects.filter(studio=request.studio, present_in_scan=True))
    report_ids = [r.pk for r in reports]

    # "Views" for the selected window: the daily rollup, which (unlike the
    # raw event log below) is kept forever -- see
    # apps.reports.models.record_view.
    views_by_report = {
        row["report"]: row["total"]
        for row in ReportViewDaily.objects.filter(
            report_id__in=report_ids, date__gte=since.date()
        ).values("report").annotate(total=Sum("views"))
    }

    # Share views and unique viewers both need the raw event log, so both
    # are bounded by however far back IT goes (capped to the same 90 days
    # above) -- one query for the share-only count, one for both distinct
    # counts together (a user viewing from two browsers still counts once;
    # a link opened by two different people still counts once each --
    # counted separately per spec, then summed for the table's one column).
    windowed_events = ReportViewEvent.objects.filter(report_id__in=report_ids, created_at__gte=since)
    share_views_by_report = {
        row["report"]: row["n"]
        for row in windowed_events.filter(share_link__isnull=False)
        .values("report").annotate(n=Count("id"))
    }
    unique_by_report = {
        row["report"]: (row["unique_users"], row["unique_links"])
        for row in windowed_events.values("report").annotate(
            unique_users=Count("user", distinct=True, filter=Q(user__isnull=False)),
            unique_links=Count("share_link", distinct=True, filter=Q(share_link__isnull=False)),
        )
    }

    # "Last viewed" and the stale flag deliberately are NOT re-scoped to the
    # selected window -- they answer "ever" / "still true right now", same
    # as the dashboard cards. Reused from apps.reports.scan rather than
    # reimplemented, so the two surfaces can never disagree about what
    # "stale" means.
    views_30d_by_report, last_viewed_by_report = _view_stats_for(reports)

    rows = []
    for report in reports:
        unique_users, unique_links = unique_by_report.get(report.pk, (0, 0))
        runtime_meta: dict = {}
        try:
            runtime_meta = storage.read_meta(request.studio, report.slug)
        except Exception as exc:  # noqa: BLE001 - a stale flag is not worth a broken page
            logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
        rows.append(
            {
                "report": report,
                "views": views_by_report.get(report.pk, 0),
                "unique_users": unique_users,
                "unique_links": unique_links,
                "unique_total": unique_users + unique_links,
                "share_views": share_views_by_report.get(report.pk, 0),
                "last_viewed": last_viewed_by_report.get(report.pk),
                "stale": (
                    views_30d_by_report.get(report.pk, 0) == 0
                    and _built_recently(runtime_meta.get("last_status"), runtime_meta.get("last_run"))
                ),
            }
        )
    rows.sort(key=lambda r: (r["report"].name or r["report"].slug).lower())

    # Header subline counts (ui-style-guide.md §1's canon header) -- summed
    # in Python from the rows already built above, not a fifth query, so the
    # page's query count still does not scale with the report count.
    reports_viewed_count = sum(1 for r in rows if r["views"] > 0)
    total_share_views = sum(r["share_views"] for r in rows)

    return render(
        request,
        "reports/analytics.html",
        {
            "org": request.org,
            "studio": request.studio,
            "rows": rows,
            "days": days,
            "day_choices": (7, 30, 90),
            "reports_viewed_count": reports_viewed_count,
            "total_share_views": total_share_views,
        },
    )


# ── JSON API (legacy handlers.py shapes) ────────────────────────────────────

@require_studio_role(roles.VIEWER)
def api_registry(request, org_slug, studio_slug):  # noqa: ARG001
    return JsonResponse(_role_safe(_visible_registry_payload(request), request))


def _safe_error(report: dict) -> str | None:
    """What a viewer may learn from a failed build: which source holds or
    fails it, or that it failed -- never the driver's message."""
    from apps.datasources.status import WAITING_PREFIX, source_failure

    error = report.get("last_error")
    if not error:
        return error
    if report.get("blocked_by"):
        return f"{WAITING_PREFIX}{report['blocked_by'][0]}'"
    failure = source_failure(error)
    if failure:
        return f"Data source '{failure[0]}': connection check failed"
    return "Build failed"


def _role_safe(payload: dict, request) -> dict:
    """The registry payload as this role may see it. Check errors and build
    output can carry hosts and user names; below developer they are reduced
    to what the source is, not what it said. The cached payload is not
    touched."""
    if roles.at_least(request.studio_role, roles.DEVELOPER):
        return payload
    return {
        **payload,
        "reports": [{**r, "last_error": _safe_error(r)} for r in payload.get("reports", [])],
        "source_states": [{**s, "detail": ""} for s in payload.get("source_states", [])],
    }


@require_studio_role(roles.VIEWER)
def api_system_status(request, org_slug, studio_slug):  # noqa: ARG001
    studio = request.studio
    now = timezone.now()
    scope = request.org_roles.report_scope_for(studio)

    running = {}
    running_runs = Run.objects.filter(studio=studio, status__in=(Run.STARTING, Run.RUNNING))
    if scope is not None:
        running_runs = running_runs.filter(report_id__in=scope)
    for run in running_runs:
        started = run.started_at or run.created_at
        running[run.slug] = {
            "slug": run.slug,
            "started_at": started.isoformat(),
            "elapsed_seconds": round((now - started).total_seconds(), 1),
            "pid": run.pid,
        }
    queued_runs = Run.objects.filter(studio=studio, status=Run.QUEUED).order_by(
        "priority", "created_at"
    )
    if scope is not None:
        queued_runs = queued_runs.filter(report_id__in=scope)
    queue = [
        {"slug": r.slug, "priority": r.priority, "cache_mode": r.cache_mode}
        for r in queued_runs
    ]

    scheduled = []
    scheduled_reports = Report.objects.filter(
        studio=studio, present_in_scan=True, disabled=False, kind=Report.KIND_REPORT
    ).exclude(schedule_cron="")
    if scope is not None:
        scheduled_reports = scheduled_reports.filter(pk__in=scope)
    for report in scheduled_reports:
        scheduled.append(
            {
                "id": f"report-{report.pk}",
                "slug": report.slug,
                "next_run": _next_cron_fire(report.schedule_cron, report.schedule_timezone),
            }
        )

    beat = WorkerHeartbeat.alive().order_by("-last_beat_at").first()
    payload = _role_safe(_visible_registry_payload(request), request)
    reports = payload.get("reports", [])
    return JsonResponse(
        {
            "running": running,
            "queue": queue,
            "max_concurrent": beat.max_concurrent if beat else settings.WORKER_MAX_CONCURRENT,
            "scheduled_jobs": scheduled,
            "production": not settings.DEBUG,
            "worker_alive": beat is not None,
            "server_uptime_seconds": round(time.time() - _SERVER_START, 1),
            "reports_total": len(reports),
            "reports_success": sum(1 for r in reports if r.get("last_status") == "success"),
            "reports_error": sum(1 for r in reports if r.get("last_status") == "error"),
            "reports_not_run": sum(
                1 for r in reports if r.get("last_status") in (None, "not_run")
            ),
            "reports_waiting": sum(1 for r in reports if r.get("waiting")),
            "source_states": payload.get("source_states", []),
        }
    )


def _next_cron_fire(cron: str, tz_name: str) -> str | None:
    try:
        from zoneinfo import ZoneInfo

        from apscheduler.triggers.cron import CronTrigger

        tz = ZoneInfo(tz_name or "UTC")
        trigger = CronTrigger.from_crontab(cron.strip(), timezone=tz)
        from datetime import datetime

        nxt = trigger.get_next_fire_time(None, datetime.now(tz))
        return nxt.isoformat() if nxt else None
    except Exception:
        return None


@require_studio_role(roles.DEVELOPER)
def api_system_log(request, org_slug, studio_slug):  # noqa: ARG001
    """Recent worker activity for this studio, as plain text lines.

    (The legacy endpoint tailed the single process's stdout ring buffer;
    in the split web/worker world the durable Run history is the log.)
    """
    n = int_param(request.GET.get("lines"), 500)
    lines = []
    for run in Run.objects.filter(studio=request.studio).order_by("-created_at")[:200]:
        stamp = (run.finished_at or run.started_at or run.created_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        dur = f" in {run.duration_seconds}s" if run.duration_seconds is not None else ""
        by = f" by {run.requested_by.email}" if run.requested_by else ""
        lines.append(f"[{stamp}] {run.slug}: {run.status}{dur} ({run.trigger}{by})")
    return JsonResponse({"log": "\n".join(lines), "lines": n})


@require_studio_role(roles.VIEWER)
def api_report_status(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    state, elapsed = report_state(report)
    history = [
        r.to_history_dict()
        for r in Run.objects.filter(report=report)
        .exclude(status__in=Run.ACTIVE_STATUSES)
        .select_related("requested_by")
        .order_by("-created_at")[:20]
    ]
    return JsonResponse(
        {
            "slug": slug,
            "state": state,
            "elapsed_seconds": elapsed,
            "history": history,
            "aggregates": run_stats.report_aggregates(report),
        }
    )


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def api_run_stats(request, org_slug, studio_slug):  # noqa: ARG001
    """Per-report run aggregates for the Operations view (30-day window)."""
    return JsonResponse(
        {"stats": run_stats.studio_run_stats(request.studio), "window_days": 30}
    )


@require_studio_role(roles.DEVELOPER)
def api_report_log(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    run_idx = int_param(request.GET.get("run"), 0)
    runs = list(
        Run.objects.filter(report=report)
        .exclude(status__in=Run.ACTIVE_STATUSES)
        .order_by("-created_at")[run_idx : run_idx + 1]
    )
    if not runs:
        return JsonResponse({"error": "No log"}, status=404)
    return JsonResponse(runs[0].to_history_dict(include_output=True))


@require_studio_role(roles.DEVELOPER)
def api_report_log_live(request, org_slug, studio_slug, slug):  # noqa: ARG001
    from apps.runner.executor import read_tail

    report = _get_report(request, slug)
    run = (
        Run.objects.filter(report=report, status__in=(Run.STARTING, Run.RUNNING))
        .order_by("-created_at")
        .first()
    )
    tail = ""
    if run is not None and run.log_dir:
        tail = read_tail(Path(run.log_dir) / "stdout.log")
    return JsonResponse({"slug": slug, "stdout_tail": tail or ""})


@require_studio_role(roles.VIEWER)
def api_report_validation(request, org_slug, studio_slug, slug):  # noqa: ARG001
    _get_report(request, slug)
    try:
        meta = storage.read_meta(request.studio, slug)
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
        return JsonResponse({"error": "report storage unavailable"}, status=503)
    payload = dict(meta.get("validation") or {})
    details = meta.get("details") or meta.get("health")
    if details:
        payload["details"] = details
    return JsonResponse(payload)


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_report_run(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    body = json_body(request)
    # portal.js historically sent {"cache": mode}; accept both keys.
    cache_mode = (
        body.get("cache_mode") or body.get("cache") or request.GET.get("cache_mode", "normal")
    )
    status = enqueue(report, cache_mode=cache_mode, trigger="manual", user=request.user)
    audit(request, "run.enqueue", target=report, slug=slug, cache_mode=cache_mode)
    return JsonResponse({"ok": True, "status": status})


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_report_stop(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    stopped = request_stop(report)
    audit(request, "run.stop", target=report, slug=slug)
    return JsonResponse({"stopped": stopped})


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_run_all(request, org_slug, studio_slug):  # noqa: ARG001
    n = 0
    for report in Report.objects.filter(
        studio=request.studio, present_in_scan=True, disabled=False
    ).select_related("studio", "studio__org"):
        if enqueue(report, trigger="manual", user=request.user) == "queued":
            n += 1
    audit(request, "run.enqueue_all", org=request.org, count=n)
    return JsonResponse({"ok": True, "enqueued": n, "message": f"Queued {n} reports"})


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_stop_all(request, org_slug, studio_slug):  # noqa: ARG001
    for report in Report.objects.filter(studio=request.studio, present_in_scan=True):
        request_stop(report)
    audit(request, "run.stop_all", org=request.org)
    return JsonResponse({"ok": True})


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_cache_clear(request, org_slug, studio_slug):  # noqa: ARG001
    cache_dir = request.studio.output_dir / ".query_cache"
    audit(request, "cache.clear", org=request.org)
    if cache_dir.is_dir():
        try:
            shutil.rmtree(cache_dir)
            return JsonResponse({"ok": True, "message": "Query cache cleared"})
        except OSError as exc:
            return JsonResponse(
                {"ok": False, "message": f"Failed to clear cache: {exc}"}, status=500
            )
    return JsonResponse({"ok": True, "message": "Cache directory does not exist (already clean)"})


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_registry_refresh(request, org_slug, studio_slug):  # noqa: ARG001
    from apps.reports.scan import ReportQuotaExceeded
    from apps.runner.gitsync import studio_sync_lock

    # Same cross-process lock GitSync.sync holds while it copies the checkout:
    # scanning mid-copy would transiently mark half the reports absent.
    with studio_sync_lock(request.studio.pk) as acquired:
        if not acquired:
            return JsonResponse(
                {
                    "ok": False,
                    "message": "A repository sync is in progress; the registry will refresh when it finishes.",
                },
                status=409,
            )
        warning = ""
        try:
            n = sync_studio_registry(request.studio)
        except ReportQuotaExceeded as exc:
            # Reports that fit are registered; say what was left out rather than
            # reporting a failure the user cannot distinguish from a broken scan.
            warning = str(exc)
            n = Report.objects.filter(studio=request.studio, present_in_scan=True).count()
    return JsonResponse({"ok": True, "reports": n, "warning": warning})


def git_status_payload(studio) -> dict:
    """One studio's git-sync state, as ``api/system/git/status`` answers it
    and the assistant's ``check_repo_changes`` reads it."""
    from apps.studios.models import RepoPublish

    repo = getattr(studio, "repo", None)
    if repo is None or not repo.repo_url:
        return {"configured": False}
    last_published = (
        RepoPublish.objects.filter(studio=studio, status="ok")
        .values_list("published_at", flat=True).first()
    )
    return {
        "configured": True,
        "repo": repo.repo_url,
        "branch": repo.branch,
        "interval_minutes": repo.sync_interval_minutes,
        "auto_run": repo.auto_run_changed,
        "sync_requested": repo.sync_requested,
        "last_sync": repo.last_sync_at.isoformat() if repo.last_sync_at else None,
        "last_synced_sha": repo.last_synced_sha,
        "last_published": last_published.isoformat() if last_published else None,
        "last_error": repo.last_error,
        "publish_mode": repo.publish_mode,
        "remote_sha": repo.remote_sha,
        "remote_checked_at": (
            repo.remote_checked_at.isoformat() if repo.remote_checked_at else None
        ),
        "pending": repo.pending_changes or None,
        "publishing": repo.publish_requested,
    }


@require_studio_role(roles.DEVELOPER)
def api_git_status(request, org_slug, studio_slug):  # noqa: ARG001
    return JsonResponse(git_status_payload(request.studio))


def _configured_repo(request):
    repo = getattr(request.studio, "repo", None)
    return repo if repo is not None and repo.repo_url else None


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_git_check(request, org_slug, studio_slug):  # noqa: ARG001
    """Fetch now: flag the repo; the worker's git thread picks it up and
    publishes too when the mode allows. Also mounted as the older
    ``git/sync`` path the ops panel calls."""
    repo = _configured_repo(request)
    if repo is None:
        return JsonResponse({"ok": False, "error": "Git sync not configured"}, status=400)
    repo.sync_requested = True
    repo.sync_reason = "manual"
    repo.save(update_fields=["sync_requested", "sync_reason"])
    audit(request, "git.check", target=request.studio)
    return JsonResponse(
        {"ok": True, "scheduled": True, "message": "Sync scheduled — the worker pulls within seconds"},
        status=202,
    )


api_git_sync = api_git_check


@require_studio_role(roles.DEVELOPER)
@require_POST
def api_git_publish(request, org_slug, studio_slug):  # noqa: ARG001
    """Publish what is pending on the next tick. Body may carry
    ``{"rebuild": bool}`` to override auto_run_changed for this publish and
    ``{"to": sha}`` naming the remote head that was reviewed: the runner
    drops the request if the branch has moved past it."""
    repo = _configured_repo(request)
    if repo is None:
        return JsonResponse({"ok": False, "error": "Git sync not configured"}, status=400)
    body = {}
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body)
        except ValueError:
            return JsonResponse({"ok": False, "error": "Body must be JSON"}, status=400)
    if not isinstance(body, dict):
        body = {}
    request_publish(request, repo, rebuild=body.get("rebuild"), to=body.get("to"))
    return JsonResponse({"ok": True, "scheduled": True}, status=202)


def request_publish(request, repo, *, rebuild=None, to=None, **audit_meta) -> None:
    """Flag ``repo`` for the worker's next tick to publish what is pending.
    ``rebuild`` (when not None) overrides auto_run_changed for this publish;
    ``to`` pins the reviewed remote head. Shared by the publish endpoint and
    the assistant's approved proposal."""
    repo.publish_requested = True
    repo.publish_requested_by = request.user
    repo.sync_requested = True
    repo.sync_reason = "manual"
    if rebuild is not None:
        repo.publish_rebuild_override = bool(rebuild)
    repo.publish_requested_to = str(to or "")[:64]
    repo.save(update_fields=[
        "publish_requested", "publish_requested_by", "publish_requested_to",
        "publish_rebuild_override", "sync_requested", "sync_reason",
    ])
    audit(request, "git.publish", target=request.studio, to=repo.remote_sha, **audit_meta)


@require_studio_role(roles.DEVELOPER)
def api_git_history(request, org_slug, studio_slug):  # noqa: ARG001
    """Publish history, newest first. Every row carries counts; the newest
    five carry the full summary."""
    from apps.studios.models import RepoPublish

    try:
        limit = max(1, min(int(request.GET.get("limit", 20)), 100))
    except ValueError:
        limit = 20
    rows = RepoPublish.objects.filter(studio=request.studio).select_related("published_by")[:limit]

    def _counts(summary: dict) -> dict:
        return {
            "commits": len(summary.get("commits") or ()),
            "reports": {k: len(v) for k, v in (summary.get("reports") or {}).items()},
            "datasources": {k: len(v) for k, v in (summary.get("datasources") or {}).items()},
            "root_files": len(summary.get("root_files") or ()),
            "warnings": len(summary.get("warnings") or ()),
        }

    publishes = []
    for i, row in enumerate(rows):
        by = row.published_by
        publishes.append({
            "id": row.pk,
            "from_sha": row.from_sha,
            "to_sha": row.to_sha,
            "published_at": row.published_at.isoformat(),
            "published_by": (by.name or by.email) if by else None,
            "trigger": row.trigger,
            "status": row.status,
            "error": row.error,
            "counts": _counts(row.summary or {}),
            "summary": row.summary if i < 5 else None,
        })
    return JsonResponse({"publishes": publishes})


@csrf_exempt
def api_git_webhook(request, org_slug, studio_slug):
    """Push webhook (GitHub-style HMAC). Unauthenticated by design: the
    caller is the git host, authenticated by the shared webhook secret."""
    import hashlib
    import hmac as hmac_mod

    from apps.studios.models import Studio

    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    studio = (
        Studio.objects.filter(org__slug=org_slug, slug=studio_slug, org__is_active=True)
        .select_related("org")
        .first()
    )
    repo = getattr(studio, "repo", None) if studio else None
    secret = (repo.webhook_secret or "") if repo else ""
    if not repo or not repo.repo_url or not secret:
        raise Http404

    signature = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac_mod.new(
        secret.encode("utf-8"), request.body, hashlib.sha256
    ).hexdigest()
    if not hmac_mod.compare_digest(signature, expected):
        return JsonResponse({"error": "bad signature"}, status=403)

    repo.sync_requested = True
    repo.sync_reason = "webhook"
    repo.save(update_fields=["sync_requested", "sync_reason"])
    return JsonResponse({"ok": True})



# ── Options menu widget (self-injecting bundle for built report pages) ─────
#
# The menu HOST: mounts the single "Options" header button and dropdown, and
# owns window.__reportMenu.register(). Must be listed before the delivery/
# share/views widgets in apps.runner.executor.portal_extensions()["scripts"]
# -- they each register an item into this file's registry instead of
# mounting their own header button. Same frozen-URL trick as
# delivery_widget_js below (see its docstring for why).

_MENU_WIDGET_LOCK = threading.Lock()
_MENU_WIDGET_CACHE: str | None = None


def menu_widget_js(request):  # noqa: ARG001
    global _MENU_WIDGET_CACHE
    with _MENU_WIDGET_LOCK:
        if _MENU_WIDGET_CACHE is None:
            _MENU_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_menu.js"
            ).read_text(encoding="utf-8")
    response = HttpResponse(_MENU_WIDGET_CACHE, content_type="application/javascript")
    # This URL is intentionally stable across report builds.  Keep it cheap,
    # but make browsers check back soon enough to pick up host upgrades.
    response["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


# ── Delivery widget (self-injecting bundle for built report pages) ─────────
#
# Report pages are static builds with no template to attach a `{% static %}`
# tag to, and ManifestStaticFilesStorage renames files under collectstatic —
# a hardcoded `/static/report_delivery.js` path would break in production.
# Serving the file's raw content at a fixed URL sidesteps both problems; see
# `apps.assistant.views.widget_js` for the identical precedent this mirrors.
# `apps.runner.executor.portal_extensions()` wires the URL into every built
# report via the framework's `extensions.scripts` contract.

_DELIVERY_WIDGET_LOCK = threading.Lock()
_DELIVERY_WIDGET_CACHE: str | None = None


def delivery_widget_js(request):  # noqa: ARG001
    # No auth: static code: every API call it makes is auth-gated same as
    # the dashboard's own portal.js.
    global _DELIVERY_WIDGET_CACHE
    with _DELIVERY_WIDGET_LOCK:
        if _DELIVERY_WIDGET_CACHE is None:
            _DELIVERY_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_delivery.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_DELIVERY_WIDGET_CACHE, content_type="application/javascript")


# ── Theme picker POST-back (self-injecting bundle) ──────────────────────────
#
# Theming redesign: the report page's own theme picker (trellum/components/
# header.py, id="fwThemeSelect") used to write localStorage only. This wires
# its onChange to the per-studio setter (apps.studios.views.theme_set)
# instead, from OUTSIDE the framework's own JS -- same frozen-URL trick as
# delivery_widget_js above, and injected into every report the same way
# (apps.runner.executor.portal_extensions(), plus a serve-time retrofit in
# apps.reports.views._inject_report_chrome for builds from before this
# landed).

_THEME_WIDGET_LOCK = threading.Lock()
_THEME_WIDGET_CACHE: str | None = None


def theme_widget_js(request):  # noqa: ARG001
    # No auth: static code, same as delivery_widget_js -- the POST it makes
    # is auth-gated by the studio-membership decorator on the setter itself.
    global _THEME_WIDGET_CACHE
    with _THEME_WIDGET_LOCK:
        if _THEME_WIDGET_CACHE is None:
            _THEME_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_theme_widget.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_THEME_WIDGET_CACHE, content_type="application/javascript")


# ── Alerts & scheduled delivery ─────────────────────────────────────────────
#
# VIEWER, not DEVELOPER: unlike the run/stop/registry-refresh endpoints above,
# these are personal — "alert me" and "email me this report" are things any
# studio member should be able to set up for themselves without needing
# report-run permission.

def _recipient_summary(schedule) -> str:
    """Human summary of who a schedule reaches, e.g. "All developers +
    Leadership + 4 people" -- role chips and group chips by name/label, then
    the individually-picked count. The individual count is the *eligible*
    count (see ``notify.intersect_with_studio``), not a raw ``.count()``:
    someone picked by hand who has since left the studio silently drops from
    it, same as everywhere else dynamic resolution applies."""
    studio = schedule.report.studio
    parts = []
    for role in schedule.recipient_roles:
        if role == "everyone":
            parts.append(f"Everyone in {studio.name}")
        else:
            label = notify.RECIPIENT_ROLE_LABELS.get(role)
            if label:
                parts.append(label)
    parts.extend(g.name for g in schedule.recipient_groups.all())
    individuals = notify.intersect_with_studio(schedule.recipients.all(), studio)
    allowed = bulk_can_view_report(individuals, schedule.report)
    individuals = [user for user in individuals if allowed.get(user.pk, False)]
    if individuals:
        n = len(individuals)
        parts.append(f"{n} {'person' if n == 1 else 'people'}")
    return " + ".join(parts) if parts else "No recipients"


def _schedule_dict(schedule, *, viewer=None) -> dict:
    """``viewer=None`` (every call site except the drawer's own GET below)
    means "render as the owner" -- every existing caller (schedule create/
    update responses, the always-own-schedules /me/deliveries rows) only
    ever shows a user their own schedule. The drawer's GET lists schedules
    the viewer doesn't own too (see api_email_schedules), so it passes the
    actual requester through and the client renders a non-owned row
    read-only (see report_delivery.js)."""
    editable = viewer is None or schedule.created_by_id == viewer.pk
    recipients = list(schedule.recipients.all())
    allowed = bulk_can_view_report(recipients, schedule.report)
    return {
        "id": schedule.pk,
        "freq": schedule.freq,
        "send_hour": schedule.send_hour,
        "send_minute": schedule.send_minute,
        "weekday": schedule.weekday,
        "month_day": schedule.month_day,
        "timezone": schedule.timezone,
        "attach_pdf": schedule.attach_pdf,
        "enabled": schedule.enabled,
        "last_sent_at": schedule.last_sent_at.isoformat() if schedule.last_sent_at else None,
        "recipients": [
            {"id": user.pk, "email": user.email}
            for user in recipients
            if user.is_active and allowed.get(user.pk, False)
        ],
        "recipient_roles": schedule.recipient_roles,
        "recipient_groups": [
            {"id": g.pk, "name": g.name} for g in schedule.recipient_groups.all()
        ],
        "recipient_summary": _recipient_summary(schedule),
        "editable": editable,
        "owner_name": schedule.created_by.display_name or schedule.created_by.email,
    }


def _apply_schedule_fields(schedule: EmailSchedule, body: dict) -> None:
    """Mutates ``schedule`` in place from a JSON body. Raises (TypeError,
    ValueError) on a malformed value; range/combination checks are left to
    ``full_clean()`` so there is exactly one place that owns them."""
    if "freq" in body:
        schedule.freq = str(body["freq"])
    if "send_hour" in body:
        schedule.send_hour = int(body["send_hour"])
    if "send_minute" in body:
        schedule.send_minute = int(body["send_minute"])
    if "weekday" in body:
        schedule.weekday = int(body["weekday"])
    if "month_day" in body:
        schedule.month_day = int(body["month_day"])
    if "timezone" in body:
        schedule.timezone = str(body["timezone"] or "UTC")
    if "attach_pdf" in body:
        schedule.attach_pdf = bool(body["attach_pdf"])
    if "enabled" in body:
        schedule.enabled = bool(body["enabled"])
    if "recipient_roles" in body:
        value = body["recipient_roles"]
        if not isinstance(value, list):
            raise TypeError("recipient_roles must be a list")
        # Membership in RECIPIENT_ROLE_CHOICES is checked by full_clean()
        # (EmailSchedule.clean()) below -- this only enforces shape.
        schedule.recipient_roles = [str(v) for v in value]


def _recipient_payload(request, report=None) -> dict:
    """Studio members eligible as ``EmailSchedule`` recipients, plus the
    recipient picker's quick-add chip data — role counts and this org's
    permission groups with member counts. No dedicated endpoint existed for
    the members list (the server-rendered members settings page is
    ADMIN-gated and returns HTML, not JSON), so this is new, minimal, and
    reuses the same eligibility rule ``_valid_recipients`` below already
    enforces: anyone with an effective role on this studio, not just an
    explicit ``StudioMembership`` row — org admins and permission-group
    defaults grant access too, and both must be pickable as recipients.

    Role counts ("admins"/"developers"/"everyone") come straight from this
    studio's ``StudioMembership`` rows — the same audience
    ``notify.alert_recipients``/``notify.resolve_recipients`` draw the role
    chips from — so they're already studio-scoped. Group counts are this
    org's permission groups (never another org's — a schedule's chips can
    only reference groups the studio's own org owns) intersected with the
    studio via ``notify.intersect_with_studio``, so a chip's number is "how
    many would actually receive", not the group's raw org-wide size.
    """
    from apps.core.permissions import bulk_role_for_studio
    from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupMembership
    from apps.studios.models import StudioMembership

    org = request.org
    studio = request.studio

    # bulk_role_for_studio computes every org member's effective role in a
    # handful of queries total -- calling effective_roles() once per member
    # here used to cost several queries per member (see notify.
    # intersect_with_studio, which had the identical shape for the group
    # counts below and shares the fix).
    org_members = list(OrgMembership.objects.filter(org=org).select_related("user"))
    users = [om.user for om in org_members if om.user.is_active]
    role_by_id = bulk_role_for_studio(users, studio)
    report_access = bulk_can_view_report(users, report) if report is not None else None
    out = [
        {"id": om.user_id, "email": om.user.email, "name": om.user.display_name or om.user.email}
        for om in org_members
        if om.user.is_active
        and role_by_id.get(om.user_id) is not None
        and (report_access is None or report_access.get(om.user_id, False))
    ]
    out.sort(key=lambda m: m["email"])

    role_counts = {"admins": 0, "developers": 0, "everyone": 0}
    for user_id, role in StudioMembership.objects.filter(
        studio=studio, user__is_active=True
    ).values_list("user_id", "role"):
        if report_access is not None and not report_access.get(user_id, False):
            continue
        role_counts["everyone"] += 1
        if role == roles.ADMIN:
            role_counts["admins"] += 1
        elif role == roles.DEVELOPER:
            role_counts["developers"] += 1

    from django.contrib.auth import get_user_model

    User = get_user_model()
    groups = []
    for group in PermissionGroup.objects.filter(org=org).order_by("name"):
        member_ids = list(
            PermissionGroupMembership.objects.filter(group=group).values_list("user_id", flat=True)
        )
        group_users = (
            list(User.objects.filter(pk__in=member_ids, is_active=True)) if member_ids else []
        )
        if report is None:
            count = len(notify.intersect_with_studio(group_users, studio))
        else:
            allowed = bulk_can_view_report(group_users, report)
            count = sum(allowed.values())
        if report is None or count:
            groups.append({"id": group.pk, "name": group.name, "count": count})

    return {"members": out, "studio_name": studio.name, "roles": role_counts, "groups": groups}


@require_studio_role(roles.VIEWER)
@require_full_studio_visibility
def api_studio_members(request, org_slug, studio_slug):  # noqa: ARG001
    return JsonResponse(_recipient_payload(request))


@require_studio_role(roles.VIEWER)
def api_report_recipients(request, org_slug, studio_slug, slug):  # noqa: ARG001
    return JsonResponse(_recipient_payload(request, _get_report(request, slug)))


def _valid_recipients(request, report, ids) -> list:
    """Recipient ids narrowed to users who can actually see this studio —
    the schedule owner picks from their org, not an arbitrary user id. No
    fallback here any more (an omitted/empty list is a legitimate "no
    individuals picked" when roles or groups cover the schedule instead) —
    callers decide what an empty result means."""
    if not isinstance(ids, list) or not ids:
        return []
    from django.contrib.auth import get_user_model

    User = get_user_model()
    users = list(User.objects.filter(pk__in=ids, is_active=True))
    allowed = bulk_can_view_report(users, report)
    valid = [user for user in users if allowed.get(user.pk, False)]
    if {str(user.pk) for user in valid} != {str(value) for value in ids}:
        raise ValidationError("One or more recipients cannot view this report.")
    return valid


def _valid_groups(request, report, ids) -> list:
    """Group ids narrowed to permission groups belonging to this studio's
    own org — a schedule may never reference another org's group."""
    if not isinstance(ids, list) or not ids:
        return []
    from apps.orgs.models import PermissionGroup

    groups = list(PermissionGroup.objects.filter(pk__in=ids, org=request.org))
    valid = []
    for group in groups:
        users = [
            membership.user
            for membership in group.memberships.select_related("user")
            if membership.user.is_active
        ]
        if any(bulk_can_view_report(users, report).values()):
            valid.append(group)
    if {str(group.pk) for group in valid} != {str(value) for value in ids}:
        raise ValidationError("One or more groups have no recipients who can view this report.")
    return valid


_RECIPIENT_BODY_KEYS = ("recipient_ids", "recipient_roles", "recipient_group_ids")


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET", "POST"])
def api_email_schedules(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    if request.method == "GET":
        # Every schedule the requester can see for this report: their own
        # (any state -- they can still edit/enable a disabled one they
        # created) plus any *enabled* schedule that currently addresses them
        # as a resolved recipient (creator/role-chip/group-chip), mirroring
        # api_my_subscriptions' own "enabled + resolved recipient" contract
        # -- see _schedule_dict's `editable` flag, which the drawer
        # (report_delivery.js) uses to render a non-owned row read-only with
        # its owner's name instead of edit/sample/delete actions. Without
        # this, a schedule addressed to "All developers" was invisible here
        # to every developer but its creator, even though it counted toward
        # their own envelope badge (api_my_subscriptions) -- the drawer's
        # own bulk-broadcast count must equal that same badge's count, so
        # `subscribed_count` below uses the identical audience check.
        from apps.orgs.models import PermissionGroupMembership
        from apps.studios.models import StudioMembership

        user_role = (
            StudioMembership.objects.filter(user=request.user, studio=request.studio)
            .values_list("role", flat=True)
            .first()
        )
        user_group_ids = set(
            PermissionGroupMembership.objects.filter(
                user=request.user, group__org=request.org
            ).values_list("group_id", flat=True)
        )
        rows = (
            EmailSchedule.objects.filter(report=report)
            .select_related("created_by")
            .prefetch_related("recipients", "recipient_groups")
        )
        visible = []
        subscribed_count = 0
        for schedule in rows:
            is_owner = schedule.created_by_id == request.user.pk
            is_recipient = schedule.enabled and notify.user_in_schedule_audience(
                request.user, schedule, user_role=user_role, user_group_ids=user_group_ids
            )
            if is_recipient:
                subscribed_count += 1
            if is_owner or is_recipient:
                visible.append(_schedule_dict(schedule, viewer=request.user))
        return JsonResponse(
            {
                "schedules": visible,
                "default_timezone": report.schedule_timezone or "UTC",
                "subscribed_count": subscribed_count,
            }
        )

    body = json_body(request)
    schedule = EmailSchedule(report=report, created_by=request.user)
    try:
        _apply_schedule_fields(schedule, body)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid field value"}, status=400)
    try:
        schedule.full_clean(exclude=["report", "created_by"])
    except ValidationError as exc:
        return JsonResponse({"ok": False, "errors": exc.message_dict}, status=400)

    payload_given = any(k in body for k in _RECIPIENT_BODY_KEYS)
    try:
        individuals = _valid_recipients(request, report, body.get("recipient_ids"))
        groups = _valid_groups(request, report, body.get("recipient_group_ids"))
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)
    if not payload_given:
        individuals = [request.user]  # legacy default: nothing picked at all -> just you
    elif not (individuals or schedule.recipient_roles or groups):
        return JsonResponse(
            {"ok": False, "error": "select at least one recipient (person, role, or group)"},
            status=400,
        )

    schedule.save()
    schedule.recipients.set(individuals)
    schedule.recipient_groups.set(groups)
    audit(request, "notify.schedule_create", target=report, schedule_id=schedule.pk)
    return JsonResponse({"ok": True, "schedule": _schedule_dict(schedule)})


@require_studio_role(roles.VIEWER)
@require_http_methods(["POST", "DELETE"])
def api_email_schedule_detail(request, org_slug, studio_slug, slug, schedule_id):  # noqa: ARG001
    report = _get_report(request, slug)
    schedule = EmailSchedule.objects.filter(
        pk=schedule_id, report=report, created_by=request.user
    ).first()
    if schedule is None:
        raise Http404  # not yours (or doesn't exist) — same response either way

    if request.method == "DELETE":
        schedule.delete()
        audit(request, "notify.schedule_delete", target=report, schedule_id=schedule_id)
        return JsonResponse({"ok": True})

    body = json_body(request)
    try:
        _apply_schedule_fields(schedule, body)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid field value"}, status=400)
    try:
        schedule.full_clean(exclude=["report", "created_by"])
    except ValidationError as exc:
        return JsonResponse({"ok": False, "errors": exc.message_dict}, status=400)

    try:
        new_individuals = (
            _valid_recipients(request, report, body["recipient_ids"])
            if "recipient_ids" in body
            else None
        )
        new_groups = (
            _valid_groups(request, report, body["recipient_group_ids"])
            if "recipient_group_ids" in body
            else None
        )
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)
    if any(k in body for k in _RECIPIENT_BODY_KEYS):
        final_individuals = (
            new_individuals if new_individuals is not None else list(schedule.recipients.all())
        )
        final_groups = (
            new_groups if new_groups is not None else list(schedule.recipient_groups.all())
        )
        if not (final_individuals or schedule.recipient_roles or final_groups):
            return JsonResponse(
                {"ok": False, "error": "select at least one recipient (person, role, or group)"},
                status=400,
            )

    schedule.save()
    if new_individuals is not None:
        schedule.recipients.set(new_individuals)
    if new_groups is not None:
        schedule.recipient_groups.set(new_groups)
    audit(request, "notify.schedule_update", target=report, schedule_id=schedule.pk)
    return JsonResponse({"ok": True, "schedule": _schedule_dict(schedule)})


#: Shown to the user immediately; the actual send (snapshot render + SMTP)
#: keeps running on the background thread _run_sample_send starts.
_SAMPLE_SENDING_MESSAGE = "Sending — check your inbox shortly"

#: Process-wide cap on simultaneous sample sends: each one is a browser
#: render plus an SMTP round trip, and the endpoints are open to every studio
#: viewer. Acquired non-blocking in the request thread, released by the
#: worker (same shape as apps.reports.livequery's slot pool).
# ponytail: fixed pool of 2 per process; make it a setting if a deployment needs more.
_SAMPLE_SLOTS = threading.BoundedSemaphore(2)


def _sample_busy() -> JsonResponse:
    response = JsonResponse(
        {"error": "too many sample sends in progress; retry shortly"}, status=429
    )
    response["Retry-After"] = "5"
    return response


def _run_sample_send(schedule_or_report, user, *, report_slug: str, schedule_id=None) -> None:
    """Background-thread body for the "send me a sample now" button (see
    api_email_schedule_sample / api_report_sample below).

    Runs off the request thread because a sample does a full Playwright
    snapshot render plus an SMTP round trip -- both too slow to hold an
    interactive request open for. Runs off the build queue
    (apps.runner.services.enqueue), not on it, deliberately: that queue is
    shaped for the build pipeline (slow, resource-heavy, one concurrent run
    per report, worth waiting behind); a one-off, cheap "email me a
    preview" click has none of those properties, and queuing it there would
    make it wait behind whatever reports already happen to be building.

    Owns the failure-handling notify.send_sample's own catch-and-log used to
    (see notify.send_sample_or_raise, which this calls instead) so the log
    line carries the request's own context -- which user, which report,
    which schedule -- instead of notify.py's generic repr of its arguments.
    """
    try:
        notify.send_sample_or_raise(schedule_or_report, user)
    except Exception:  # noqa: BLE001 - background thread: log, never crash silently
        logger.exception(
            "notify: sample send failed for %s (report=%s, schedule=%s)",
            user.email, report_slug, schedule_id,
        )
    finally:
        _SAMPLE_SLOTS.release()


@require_studio_role(roles.VIEWER)
@require_POST
def api_email_schedule_sample(request, org_slug, studio_slug, slug, schedule_id):  # noqa: ARG001
    report = _get_report(request, slug)
    schedule = EmailSchedule.objects.filter(
        pk=schedule_id, report=report, created_by=request.user
    ).first()
    if schedule is None:
        raise Http404
    if not _SAMPLE_SLOTS.acquire(blocking=False):
        return _sample_busy()
    try:
        threading.Thread(
            target=_run_sample_send,
            args=(schedule, request.user),
            kwargs={"report_slug": report.slug, "schedule_id": schedule.pk},
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 - could not spawn; give the slot back
        _SAMPLE_SLOTS.release()
        raise
    audit(request, "notify.sample_send", target=report, schedule_id=schedule.pk)
    return JsonResponse({"ok": True, "message": _SAMPLE_SENDING_MESSAGE}, status=202)


@require_studio_role(roles.VIEWER)
@require_POST
def api_report_sample(request, org_slug, studio_slug, slug):  # noqa: ARG001
    """"Send me a sample now" — no EmailSchedule required."""
    report = _get_report(request, slug)
    if not _SAMPLE_SLOTS.acquire(blocking=False):
        return _sample_busy()
    try:
        threading.Thread(
            target=_run_sample_send,
            args=(report, request.user),
            kwargs={"report_slug": report.slug},
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 - could not spawn; give the slot back
        _SAMPLE_SLOTS.release()
        raise
    audit(request, "notify.sample_send", target=report)
    return JsonResponse({"ok": True, "message": _SAMPLE_SENDING_MESSAGE}, status=202)


@require_studio_role(roles.VIEWER)
def api_my_subscriptions(request, org_slug, studio_slug):  # noqa: ARG001
    """Bulk per-report envelope state for the current user: how many enabled
    email schedules is *this* user a recipient of, on each report in the
    studio? One call for the whole dashboard rather than a round trip per
    row (same shape reasoning as ``api_studio_members`` and the dashboard's
    own ``api_registry``) — the dashboard's envelope icons and the report
    page's header button both call this to decide whether to accent-tint
    themselves.

    Failure/recovery alerts aren't part of this contract: they go to every
    developer/admin automatically, with nothing to subscribe to (see
    ``apps.reports.notify.alert_recipients``).

    Sparse on purpose: a report the user isn't a recipient on is simply
    absent from ``reports`` rather than carrying an explicit ``0``, keeping
    the payload small on studios with many reports.

    "A recipient" is resolved, not just the ``recipients`` M2M: a user
    reached only via a role or group chip counts too (see
    ``apps.reports.notify.resolve_recipients``) — otherwise a colleague
    swept in by "All developers" would never see their own envelope badge
    light up.

    The requester's own studio role and permission-group membership are
    fetched once here and matched against each schedule's chips in Python
    (``notify.user_in_schedule_audience``) rather than calling
    ``resolve_recipients`` -- which resolves a schedule's *entire* audience,
    fanning out a per-user access check for every studio member -- once per
    schedule. A studio with many schedules used to cost several queries for
    every one of them; this costs a fixed handful for the whole request.
    """
    from apps.orgs.models import PermissionGroupMembership
    from apps.studios.models import StudioMembership

    studio = request.studio
    user = request.user
    reports = list(
        visible_reports(
            user, Report.objects.filter(studio=studio, present_in_scan=True)
        ).values("pk", "slug")
    )
    ids = [r["pk"] for r in reports]
    counts: dict[int, int] = {}
    if user.is_active:
        user_role = (
            StudioMembership.objects.filter(user=user, studio=studio)
            .values_list("role", flat=True)
            .first()
        )
        user_group_ids = set(
            PermissionGroupMembership.objects.filter(
                user=user, group__org=request.org
            ).values_list("group_id", flat=True)
        )
        schedules = EmailSchedule.objects.filter(report_id__in=ids, enabled=True).prefetch_related(
            "recipients", "recipient_groups"
        )
        for schedule in schedules:
            if notify.user_in_schedule_audience(
                user, schedule, user_role=user_role, user_group_ids=user_group_ids
            ):
                counts[schedule.report_id] = counts.get(schedule.report_id, 0) + 1
    out = {}
    for r in reports:
        count = counts.get(r["pk"], 0)
        if count:
            out[r["slug"]] = {"schedules": count}
    return JsonResponse({"reports": out})


# ── "My deliveries & alerts" (personal, cross-studio) ───────────────────────
#
# Linked from the shell user menu (templates/_shell.html). Global, not
# studio-scoped -- a user's schedules and alert subscriptions span every
# studio they have access to, the same way favorites do.


def _visible_row(user, report, er_cache: dict) -> bool:
    """Same visibility rule as ``_visible_favorite_rows`` above: a row
    survives losing studio access (so regaining it restores the view) but
    is not displayed while access is gone."""
    studio = report.studio
    org = studio.org
    er = er_cache.get(org.pk)
    if er is None:
        er = er_cache[org.pk] = effective_roles(user, org)
    scope = er.report_scope_for(studio)
    return scope is None or report.pk in scope


def _schedule_cadence_words(schedule: EmailSchedule) -> str:
    """Human cadence string for the deliveries list -- mirrors
    ``scheduleSummary()`` in static/report_delivery.js, server-side."""
    import calendar

    when = f"{schedule.send_hour:02d}:{schedule.send_minute:02d}"
    if schedule.freq == EmailSchedule.FREQ_WEEKLY and 0 <= schedule.weekday <= 6:
        when += f" on {calendar.day_name[schedule.weekday]}"
    elif schedule.freq == EmailSchedule.FREQ_MONTHLY:
        when += f" on day {schedule.month_day}"
    return f"{schedule.get_freq_display()} at {when} {schedule.timezone}"


#: Same escape table django.utils.html.json_script uses -- a report/group
#: name that happens to contain "</script>" must not be able to break out
#: of the <script type="application/json"> tag it's embedded in.
_JSON_SCRIPT_ESCAPES = {ord(">"): "\\u003E", ord("<"): "\\u003C", ord("&"): "\\u0026"}


def _schedule_json_for_script(schedule) -> str:
    return json.dumps(_schedule_dict(schedule)).translate(_JSON_SCRIPT_ESCAPES)


def _my_schedule_rows(user, *, org_id=None) -> list[dict]:
    """One dict per ``EmailSchedule`` the user created, across every studio
    they can still see -- the only per-user subscription left to list here.
    Failure/recovery alerts have no rows of their own: they go to every
    studio developer/admin automatically (see
    ``apps.reports.notify.alert_recipients``)."""
    rows = EmailSchedule.objects.filter(created_by=user)
    if org_id is not None:
        rows = rows.filter(report__studio__org_id=org_id)
    rows = (
        rows
        .select_related("created_by", "report", "report__studio", "report__studio__org")
        .prefetch_related("recipients", "recipient_groups")
        .order_by("report__studio__org__name", "report__studio__name", "report__name")
    )
    er_cache: dict = {}
    out = []
    for schedule in rows:
        report = schedule.report
        studio = report.studio
        if not report.present_in_scan or not _visible_row(user, report, er_cache):
            continue
        out.append(
            {
                "schedule": schedule,
                "report": report,
                "studio": studio,
                "org": studio.org,
                "cadence": _schedule_cadence_words(schedule),
                "recipient_summary": _recipient_summary(schedule),
                # Reused verbatim by templates/reports/my_deliveries.html's
                # data-schedule-json script tag, so the inline picker
                # (report_delivery.js) seeds from exactly the same shape the
                # AJAX endpoints return -- one contract, not a hand-built
                # second copy of it in the template.
                "schedule_json": _schedule_json_for_script(schedule),
                "edit_url": f"/s/{studio.org.slug}/{studio.slug}/r/{report.slug}/?rdw=1",
            }
        )
    return out


def my_deliveries(request):
    """Cross-studio "Deliveries": every email schedule belonging to the
    current user. Read-only list -- editing happens in the drawer the "Edit"
    links open on the report's own page (static/report_delivery.js), the
    same UI a studio member already uses to create these, so there is
    exactly one place that knows how to mutate a schedule.

    Failure/recovery alerts have nothing to list here: they go to every
    studio developer/admin automatically, with no per-user subscription to
    manage (see ``apps.reports.notify.alert_recipients``)."""
    if not request.user.is_authenticated:
        return redirect("login")
    return render(
        request,
        "reports/my_deliveries.html",
        {
            "schedule_rows": _my_schedule_rows(
                request.user,
                org_id=getattr(getattr(request, "api_key", None), "org_id", None),
            ),
        },
    )


@require_http_methods(["GET", "POST"])
def notify_unsubscribe(request, token):
    """No-login unsubscribe link mailed at the bottom of every scheduled
    delivery. GET renders a confirm button; POST performs the removal — a
    mail scanner that pre-fetches GET links must never unsubscribe someone
    on its own.

    Only email schedules carry an unsubscribe link — failure/recovery/
    summary alerts are operational duty mail to a studio's developers and
    admins and have none (see ``apps.reports.notify.alert_recipients``), so
    ``kind`` is always "schedule" for any token minted today. A stale or
    forged token of any other shape is simply invalid.

    Known gap: this only removes the recipient from the individual
    ``recipients`` M2M. A user reached solely through a role or group chip
    (``notify.resolve_recipients``) isn't in that M2M, so clicking
    unsubscribe on that mail currently does nothing to stop the next one —
    pulling them out would mean editing their studio role or the org's
    permission group, not this schedule. A per-recipient opt-out that
    survives dynamic resolution is unbuilt; flagged here rather than
    silently mismodelled.
    """
    from django.contrib.auth import get_user_model

    payload = notify.read_unsubscribe_token(token)
    kind = payload.get("kind") if payload else None
    user = schedule = report = None
    if payload is not None:
        User = get_user_model()
        user = User.objects.filter(pk=payload.get("uid")).first()
        if kind == "schedule":
            schedule = (
                EmailSchedule.objects.filter(pk=payload.get("id"))
                .select_related("report", "report__studio", "report__studio__org")
                .first()
            )
            report = schedule.report if schedule else None

    if user is None or report is None:
        return render(request, "notify/unsubscribe.html", {"invalid": True}, status=400)

    label = report.name or report.slug
    if request.method == "POST":
        if schedule is not None:
            schedule.recipients.remove(user)
        return render(request, "notify/unsubscribe.html", {"done": True, "label": label})

    return render(
        request,
        "notify/unsubscribe.html",
        {"confirm": True, "label": label, "target_email": user.email},
    )


# ── Report content serving ──────────────────────────────────────────────────

_CONTENT_TYPES = {
    ".html": "text/html",
    ".json": "application/json",
    ".css": "text/css",
    ".js": "application/javascript",
}


def _storage_outage(request, exc: Exception, *, as_page: bool):
    """A store outage reads as an outage: 503, never a stale answer.

    The cache is deliberately not a fallback (see apps.core.storage), so when
    the store is unreachable the honest response is "temporarily unavailable"
    — contained and named, rather than a raw 500 that looks like a bug in the
    report or the portal.
    """
    logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
    if as_page:
        return render(request, "report_storage_outage.html", status=503)
    return HttpResponse(
        "Report storage is temporarily unreachable. Try again shortly.",
        status=503,
        content_type="text/plain",
    )


# SAMEORIGIN, not the project default of DENY: the portal frames its own
# report content in-place -- the Metrics catalog embeds one metric's block
# from the generated metrics report (`?only=`) in its overlay. Only this
# origin may frame it, and only after require_studio_role has passed, so the
# report stays as protected as the page framing it. Anonymous /share/ links
# are a different trust boundary and keep DENY.
@xframe_options_sameorigin
@require_studio_role(roles.VIEWER)
def report_page(request, org_slug, studio_slug, slug):
    report = _get_report(request, slug)

    # Edge read path (hosted deployments): the permission check just ran, so
    # hand the viewer to the edge. Not one content byte flows through this
    # process — the redirect names the current build's immutable URL, and (in
    # edge-signed mode) the grant cookie, scoped to exactly this report's
    # content prefix, is what the edge verifies on every request. A report that
    # never published falls through to the unbuilt page below.
    from apps.core import cdn
    blocked = _selected_content_block(request, report)
    if blocked:
        return blocked

    if cdn.serves_from_edge():
        # Refuse rather than leak or silently fall back: if the guard cannot
        # prove a stranger is denied at the edge, do not emit a content URL.
        # (A proxy fallback here would shove every report byte back through the
        # web tier — the exact failure the edge posture exists to avoid.)
        ok, why = cdn.exposure_ok()
        if not ok:
            logger.error(f"refusing to serve report content: {why}")
            return HttpResponse(
                "Report serving is misconfigured and has been disabled to "
                "avoid exposing report data. An administrator has been alerted.",
                status=503,
                content_type="text/plain",
            )
        try:
            build = storage.current_build(request.studio, slug)
        except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
            return _storage_outage(request, exc, as_page=True)
        if build:
            try:
                names = storage.html_index(request.studio).get(slug, [])
            except Exception as exc:  # noqa: BLE001
                return _storage_outage(request, exc, as_page=True)
            entry = storage.entry_for(names)
            target = cdn.content_path(request.studio, slug, build, entry)
            qs = request.META.get("QUERY_STRING", "")
            if qs:
                target += f"?{qs}"
            response = redirect(target)
            cdn.attach_grant(response, request, request.studio, slug)
            return response

    try:
        output_dir = storage.output_root(request.studio, slug)
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        return _storage_outage(request, exc, as_page=True)
    entry = _get_html_entry(str(output_dir))
    if (output_dir / entry).is_file():
        target = f"/s/{org_slug}/{studio_slug}/r/{slug}/{entry}"
        # Forward the query string (e.g. "My deliveries & alerts"' ?rdw=1
        # deep link, apps.reports.views.my_deliveries) -- a bare redirect
        # would otherwise drop it silently.
        qs = request.META.get("QUERY_STRING", "")
        if qs:
            target += f"?{qs}"
        return redirect(target)

    # Retention deleted this report's output. Both timestamps survive the
    # sweep on the row itself (apps.reports.models.Report.data_expired_at),
    # so this reads straight off the row rather than being inferred from the
    # missing file -- and it must be checked before the generic "never
    # built" page below, which would otherwise tell the wrong story for a
    # report that used to have output.
    if report.data_expired_at is not None:
        next_run = None
        if report.schedule_cron:
            from datetime import datetime

            iso = _next_cron_fire(report.schedule_cron, report.schedule_timezone)
            next_run = datetime.fromisoformat(iso) if iso else None
        return render(
            request,
            "report_data_expired.html",
            {
                "report": report,
                "prefix": f"/s/{org_slug}/{studio_slug}",
                "studio_name": request.studio.name,
                "org_name": request.org.name,
                "next_run": next_run,
                # DEVELOPER is the same gate api_report_run itself enforces
                # -- reusing roles.at_least here means the button this
                # renders can never drift from what actually works if clicked.
                "can_rebuild": roles.at_least(request.studio_role, roles.DEVELOPER),
            },
        )

    # The report is in the registry but has no built output — it errored or has
    # never run. Redirecting to the (missing) entry file would 404 with "this
    # page doesn't exist", which is misleading: the report DOES exist. Show its
    # build status and last error instead.
    try:
        meta = storage.read_meta(request.studio, slug)
    except Exception as exc:  # noqa: BLE001 - the unbuilt page beats a 500
        logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
        meta = {}
    from apps.datasources.status import (
        blocked_names,
        build_error,
        source_failure,
        source_states,
    )

    latest = Run.objects.filter(report=report).order_by("-created_at").only("status", "stderr_tail").first()
    error = build_error(meta, latest)
    blocked_by = blocked_names(meta, latest)
    declared = {s.name for s in source_states(request.studio) if s.declared} if blocked_by else set()
    return render(
        request,
        "report_unbuilt.html",
        {
            "report": report,
            "prefix": f"/s/{org_slug}/{studio_slug}",
            "studio_name": request.studio.name,
            "status": meta.get("last_status"),
            "error": error or None,
            # A report held on its data sources, or failed on one: who may fix
            # it decides what the page says (a viewer never sees host/user).
            "blocked": [{"name": n, "declared": n in declared} for n in blocked_by],
            "failure": None if blocked_by else source_failure(error),
            "can_configure": roles.at_least(request.studio_role, roles.ADMIN),
            "can_operate": roles.at_least(request.studio_role, roles.DEVELOPER),
        },
    )


@require_studio_role(roles.VIEWER)
@require_http_methods(["GET"])
def report_shell(request, org_slug, studio_slug):  # noqa: ARG001
    """Authenticated console chrome for one report page.

    Report HTML is immutable and may live at the edge, so the small shell is
    fetched separately.  Looking the report up through the request's resolved
    studio prevents this endpoint becoming a way to mount navigation around a
    report the viewer cannot access.
    """
    report = _get_report(request, request.GET.get("report", ""))
    response = render(
        request,
        "reports/report_shell.html",
        {
            "report": report,
            "console_active": "analysis" if report.kind == Report.KIND_ANALYSIS else "report",
            "console_page_title": report.name or report.slug,
        },
    )
    response["Cache-Control"] = "no-store"
    return response


def _traversal_guarded_path(output_root: Path, asset: str) -> Path:
    """Resolve ``asset`` under ``output_root``, refusing anything that would
    escape it.

    Shared by every route that serves one file out of a report's built
    output — the authenticated ``report_asset`` and the public share asset
    route (``share_asset``) below — so the guard exists exactly once rather
    than being reimplemented per caller.
    """
    from trellum.artifacts import is_private_artifact

    if is_private_artifact(asset):
        raise Http404
    target = (output_root / asset).resolve()
    if not str(target).startswith(str(output_root) + os.sep) and target != output_root:
        raise Http404
    if not target.is_file():
        raise Http404
    if is_private_artifact(str(target)):
        raise Http404
    return target


def _report_theme_attrs(raw: bytes, theme: str, studio_theme: str) -> bytes:
    """Serve-time override of the report shell's ``<html>`` theme
    attributes — byte-exact and idempotent: strips whatever
    ``data-theme``/``data-studio-theme`` the build baked in (or a PREVIOUS
    call to this function left behind, e.g. an earlier request under a
    different viewer/lock state), then re-adds them from the freshly
    resolved values, touching nothing else in the tag.

    ``theme`` (``apps.core.themes.resolve_studio_theme``) always replaces
    ``data-theme`` — the framework's own generated CSS keys every variable
    off it (``trellum/rendering/html_builder.py``), so the report must
    always carry a concrete registry key to render at all. ``studio_theme``
    (``apps.core.themes.explicit_studio_theme``) adds ``data-studio-theme``
    only when non-empty, mirroring the management surface's identical
    convention (``templates/base.html``) so a script reading either surface
    sees the same "was anything explicitly picked" signal.
    """
    import re as _re

    def _rewrite(m: "_re.Match") -> bytes:
        tag = m.group(0)
        tag = _re.sub(rb'\s*data-theme="[^"]*"', b"", tag)
        tag = _re.sub(rb'\s*data-studio-theme="[^"]*"', b"", tag)
        attrs = f' data-theme="{theme}"'.encode("ascii")
        if studio_theme:
            attrs += f' data-studio-theme="{studio_theme}"'.encode("ascii")
        return tag[:-1] + attrs + b">"

    return _re.sub(rb"<html\b[^>]*>", _rewrite, raw, count=1, flags=_re.IGNORECASE)


def _sync_theme_select(raw: bytes, theme: str) -> bytes:
    """Keeps the report's own theme ``<select id="fwThemeSelect">``
    (``trellum/components/header.py``) in visual agreement with the
    serve-time-overridden ``data-theme`` above -- otherwise a report built
    while a different theme was resolved would still show the OLD pick in
    its own dropdown, even though the page around it now renders the new
    one. Idempotent: always clears every option's ``selected`` first, then
    marks the one option whose ``value`` matches ``theme`` fresh. A no-op
    when the picker is absent (switcher disabled, or fewer than two
    registered themes -- ``trellum.rendering.html_builder._theme_select_html``)."""
    import re as _re

    def _rewrite(m: "_re.Match") -> bytes:
        select_html = m.group(0).replace(b" selected>", b">")
        needle = b'value="' + theme.encode("ascii") + b'"'
        idx = select_html.find(needle)
        if idx == -1:
            return select_html
        end = select_html.find(b">", idx)
        return select_html[:end] + b" selected" + select_html[end:]

    return _re.sub(
        rb'<select\b[^>]*\bid="fwThemeSelect"[^>]*>.*?</select>',
        _rewrite, raw, count=1, flags=_re.DOTALL,
    )


def _inject_report_chrome(raw: bytes, org, studio, user=None) -> bytes:
    """Serve-time upgrades applied to every report HTML response, so a report
    built before one of these landed doesn't need a rebuild to carry it.
    Shared by every route that serves a report's HTML (``report_asset`` and
    the public share entry/asset routes — the latter always pass
    ``user=None``: share links are anonymous by design, so theme resolution
    for them never consults a viewer override, only the studio/org chain)."""
    # Theming redesign: the served report always carries the SAME resolved
    # theme the studio's management chrome does (apps.core.context_
    # processors.shell computes the identical pair) — one resolver, two
    # consumers, so the two surfaces can never disagree.
    #
    # ONE carve-out: a repo-declared CUSTOM theme (Studio.repo_theme naming
    # something outside THEME_REGISTRY, registered only inside that repo's
    # own report-build process). The chrome has no CSS for it and resolves
    # to Trellum, but the report was BUILT with real CSS for it baked in —
    # overwriting its data-theme with "trellum dark" would repaint working
    # CSS variables onto a page with no rules for them. Leave the report's
    # own baked theme (and its own in-page theme select) untouched instead.
    from apps.core.themes import explicit_studio_theme, has_custom_repo_theme, resolve_studio_theme

    resolved_theme = resolve_studio_theme(user, studio)
    if not has_custom_repo_theme(studio):
        raw = _report_theme_attrs(raw, resolved_theme, explicit_studio_theme(user, studio))
        raw = _sync_theme_select(raw, resolved_theme)
    # The report page's own theme picker POSTs its choice back to the
    # per-studio setter instead of only writing localStorage (see static/
    # report_theme_widget.js). New builds already carry this via apps.
    # runner.executor.portal_extensions()["scripts"]; this covers reports
    # built before that landed (no rebuild needed). Harmless to inject on
    # public share pages too -- the script no-ops without a session-
    # authenticated /s/<org>/<studio>/r/<slug>/ path to derive an endpoint
    # from.
    if b"/api/reports/theme-widget.js" not in raw:
        raw = raw.replace(
            b"</body>",
            b'<script src="/api/reports/theme-widget.js" defer></script></body>',
            1,
        )
    # Reports built before the AGPL source offer landed get the running
    # portal's exact source at serve time. New reports already carry the
    # fuller notice and local licence file, so do not duplicate it.
    if b'class="trellum-source"' not in raw:
        import html as _html

        from apps.core.version import source_url

        source = _html.escape(source_url(), quote=True).encode("utf-8")
        notice = (
            b'<footer class="trellum-source" style="margin:2rem 1rem 1rem;'
            b'font-size:.75rem;text-align:center"><a href="'
            + source
            + b'" rel="noopener">Trellum runtime source</a></footer>'
        )
        raw = raw.replace(b"</body>", notice + b"</body>", 1)
    # The AI assistant follows the user onto report pages. New builds carry the
    # widget tag; this serve-time injection covers reports built before
    # that change (no rebuild needed).
    if b"/api/assistant/widget.js" not in raw:
        raw = raw.replace(
            b"</body>",
            b'<script src="/api/assistant/widget.js" defer></script></body>',
            1,
        )
    # Proxy-served reports can be upgraded without rebuilding them.  Public
    # share responses pass user=None and deliberately receive no authenticated
    # display chrome.  Immutable edge outputs still need a rebuild when they
    # predate the menu host.
    if user is not None and b"/api/reports/menu-widget.js" not in raw:
        menu_tag = b'<script src="/api/reports/menu-widget.js" defer></script>'
        # Put the registry before any already-baked widget scripts.  Appending
        # it at </body> breaks partially upgraded builds whose Share script
        # executes first and finds no window.__reportMenu to register with.
        head_start = raw.find(b"<head")
        head_open_end = raw.find(b">", head_start) if head_start >= 0 else -1
        if head_open_end >= 0:
            raw = raw[: head_open_end + 1] + menu_tag + raw[head_open_end + 1 :]
        else:
            body_start = raw.find(b"<body")
            body_open_end = raw.find(b">", body_start) if body_start >= 0 else -1
            if body_open_end >= 0:
                raw = raw[: body_open_end + 1] + menu_tag + raw[body_open_end + 1 :]
            else:
                raw = raw.replace(b"</body>", menu_tag + b"</body>", 1)
    # Share-link management was already injected into new builds through
    # portal_extensions(), but legacy builds only received the menu host at
    # serve time.  Without this companion script there is nothing to run the
    # developer-role probe and register Share in that menu.  Keep the script
    # authenticated-report-only; its API remains the source of truth for role
    # and organization sharing/embed policy.
    if user is not None and b"/api/reports/share-widget.js" not in raw:
        raw = raw.replace(
            b"</body>",
            b'<script src="/api/reports/share-widget.js" defer></script></body>',
            1,
        )
    # Same pattern for the breadcrumb: builds from before the trellum
    # header carry a lone "Portal" back-link pill; upgrade it in place.
    if b"fw-crumb" not in raw and b'class="fw-back-link"' in raw:
        import re as _re

        from apps.runner.executor import breadcrumb_nav_html

        nav = breadcrumb_nav_html(org, studio).encode("utf-8")
        raw = _re.sub(
            rb'<a class="fw-back-link".*?</a>',
            lambda _m: nav,
            raw,
            count=1,
            flags=_re.DOTALL,
        )
    # The org half of the crumb used to hard-code "/", which opens whatever
    # org the session last remembered rather than THIS report's org. New
    # builds carry /?org=<slug>; fix builds from in between at serve time
    # (byte-exact match, so already-correct crumbs are untouched).
    if b'class="fw-crumb" href="/"' in raw:
        raw = raw.replace(
            b'class="fw-crumb" href="/"',
            b'class="fw-crumb" href="/?org=' + org.slug.encode("ascii") + b'"',
            1,
        )
    # A report's PWA manifest sits behind the same auth as every other asset,
    # but the browser fetches <link rel="manifest"> WITHOUT credentials by
    # default, so the cookie-less request misses auth and gets an HTML page
    # back -- "Manifest: Line: 1, Syntax error" in the console. Adding
    # crossorigin="use-credentials" makes the browser send the session cookie
    # so the JSON is served. Byte-exact match, so a build already carrying it
    # is untouched (no double-inject).
    raw = raw.replace(
        b'<link rel="manifest" href="manifest.json">',
        b'<link rel="manifest" href="manifest.json" crossorigin="use-credentials">',
        1,
    )
    return raw


def _serve_report_file(
    target: Path, *, org, studio, user=None, extra_head: bytes = b"", kind="report"
) -> HttpResponse:
    """Content-type + HTML chrome injection for one already-guarded report
    output file. ``extra_head`` (used by the public share routes) is spliced
    in right before ``</head>`` — serve-time CSS hiding chrome that makes no
    sense to that viewer, without touching the file on disk. ``user`` feeds
    theme resolution (apps.core.themes.resolve_studio_theme) — omitted by
    the public share routes below, which are anonymous by design.

    Shared by ``report_asset`` and the public share entry/asset routes — one
    place owns "how a resolved report file becomes an HTTP response".
    """
    ext = target.suffix.lower()
    ct = _CONTENT_TYPES.get(ext) or mimetypes.guess_type(str(target))[0] or "text/plain"
    if ext == ".html":
        raw = target.read_bytes()
        if b"data-content-kind=" not in raw:
            marker = b"analysis" if kind == Report.KIND_ANALYSIS else b"report"
            raw = re.sub(rb"<html\b", b'<html data-content-kind="' + marker + b'"', raw, count=1)
        raw = _inject_report_chrome(raw, org, studio, user=user)
        if extra_head:
            if b"</head>" in raw:
                raw = raw.replace(b"</head>", extra_head + b"</head>", 1)
            else:
                raw = extra_head + raw
        return HttpResponse(raw, content_type=ct)
    return FileResponse(open(target, "rb"), content_type=ct)


@require_studio_role(roles.VIEWER)
@xframe_options_sameorigin  # the entry HTML report_page redirects to — see there
def report_asset(request, org_slug, studio_slug, slug, asset):  # noqa: ARG001
    report = _get_report(request, slug)
    blocked = _selected_content_block(request, report)
    if blocked:
        return blocked
    try:
        output_root = storage.output_root(request.studio, slug).resolve()
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        return _storage_outage(request, exc, as_page=False)
    target = _traversal_guarded_path(output_root, asset)
    # View analytics (internal planning#4): capture exactly once per page view --
    # when the requested asset IS the report's entry HTML (report_page
    # always redirects here with that entry filename), never on the other
    # assets (css/js/data) the entry page then fetches. Comparing against
    # _get_html_entry -- the same helper report_page used to find that entry
    # in the first place -- is what "is this the entry" means here.
    is_entry = asset == _get_html_entry(str(output_root))
    if is_entry:
        record_view(report, user=request.user, request=request)
    elif _is_export_asset(asset):
        # Data egress: the prebuilt export files (.csv/.xlsx/.parquet/.zip)
        # are what actually carries data out, unlike the client-rendered
        # PNG/PDF exports the framework draws in-browser and this server
        # never sees.
        audit(
            request, "report.export_download", target=report,
            asset=asset, ext=Path(asset).suffix.lower(),
        )
    response = _serve_report_file(
        target, org=request.org, studio=request.studio, user=request.user, kind=report.kind
    )
    if (
        is_entry
        and target.suffix.lower() == ".html"
        and request.GET.get("_console_host") == "1"
        and request.GET.get("display") == "focus"
    ):
        response["X-Frame-Options"] = "SAMEORIGIN"
        response["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


# ── Public share links (internal planning#6) ──────────────────────────────────────
#
# Anonymous, revocable public links to one report. No studio membership --
# the token itself is the credential -- so every view below sits outside
# require_studio_role entirely.
#
# Edge read path: in an edge-signed/edge-external posture (apps.core.cdn),
# the authenticated grant is not issued to anonymous share visitors. Rather
# than mint a portal-session grant for the share credential, share
# routes fail closed (503) whenever the edge read path is active, and fall
# through to the ordinary local/materialised-cache path otherwise -- see
# _share_storage_locked below.

_EXPORT_ASSET_EXTS = (".csv", ".xlsx", ".parquet", ".zip")

#: Whenever a link carries a password at all -- whether the org's policy
#: demands one or the creator just chose to set one -- it has to actually
#: slow down a guesser. Length-only, no composition rules: length is what
#: makes a random string hard to guess, and composition rules mostly just
#: push people toward predictable substitutions.
_MIN_SHARE_PASSWORD_LENGTH = 12

#: One embed-link origin (internal planning ticket #12): ``scheme://host[:port]`` with no path,
#: or the ``*`` wildcard. Entries are lowercased before this runs and land
#: verbatim in a ``frame-ancestors`` directive, so nothing else may pass.
_EMBED_ORIGIN_RE = re.compile(r"^(https?://[a-z0-9.-]+(:\d+)?|\*)$", re.IGNORECASE)
_MAX_EMBED_ORIGINS = 20

#: A theme name an embed link may ask for (``ShareLink.embed_theme``).
#: Deliberately NOT checked against ``trellum.themes.THEME_REGISTRY``: a repo
#: can register its own theme inside its build (see
#: ``apps.core.themes.has_custom_repo_theme``), and that name is legitimate
#: for its own reports while being absent from the portal's registry. Registry
#: names carry spaces ("trellum dark"), so those are in. The value lands in a
#: JSON literal inside an inline <script>, which is what this keeps boring.
_EMBED_THEME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")

#: Portal chrome that means nothing to an anonymous share visitor -- the AI
#: assistant's openers and panel, and the Email & alerts header button, are
#: baked into every build (apps.runner.executor.portal_extensions) regardless
#: of who ends up viewing it, so a share serve hides them with CSS rather than
#: trying to keep them from mounting. Selectors mirror apps.reports.snapshot
#: ._CHROME_HIDE_CSS's assistant/#fwDeliveryBtn entries.
#:
#: The opener ids are the widget's own (static/assistant.js): #assistantAsk on
#: the desktop header, #assistantPill on a phone. They are named here rather
#: than matched loosely because this CSS is the second layer -- the first is
#: the widget only unhiding itself once /available says yes, which an
#: anonymous visitor never gets. An embed is meant to be chromeless inside a
#: customer's page whatever that check answers, so if these ids move, they
#: move here too.
_SHARE_HIDE_CHROME_CSS = (
    "#assistantAsk,#assistantPill,#assistantPanel,#fwDeliveryBtn{display:none!important}"
)
#: Framework's own Export (PNG/PDF/CSV/...) button + menu, PLUS our own
#: Options menu (static/report_menu.js). When export isn't allowed, the
#: Options menu would have nothing to hold on a share page anyway -- the
#: delivery/share/activity items never register here (their own
#: autoMount() path regex only matches the authenticated
#: /s/<org>/<studio>/r/<slug>/ route, never /share/<token>/), and the
#: client-side detectExport() in report_menu.js already checks this same
#: wrap's computed display and skips registering when it's hidden, which is
#: what actually keeps the button from ever mounting. Hiding #fwOptionsWrap
#: here too is belt-and-suspenders, matching the existing #fwDeliveryBtn
#: entry above. See apps.reports.snapshot._CHROME_HIDE_CSS for the
#: emailed-snapshot sibling of this selector.
_SHARE_HIDE_EXPORT_CSS = ".fw-export-wrap,#fwOptionsWrap{display:none!important}"
#: Embed links render chromeless: the host page owns title and navigation,
#: so ``.fw-header`` is hidden (it is one sticky wrapper with no body
#: offset -- print CSS already hides it the same way). And the frame never
#: shows scrollbars of its own: the host page sizes the iframe from
#: ``trellum:height`` (content stays wheel-scrollable should a host never
#: resize).
# ponytail: chromeless drops multi-page nav (.fw-nav-group); upgrade = hide only .fw-header-right + crumb
_EMBED_CSS = (
    ".fw-header{display:none!important}"
    "html{scrollbar-width:none}"
    "html::-webkit-scrollbar{display:none}"
)
#: Added for a link created with "Hide the filter bar" (internal planning ticket #12): the host
#: gets a static view of the data the report was built with, rather than one
#: its visitors can re-filter.
_EMBED_HIDE_FILTERS_CSS = ".fw-filter-bar{display:none!important}"


def _share_session_key(token: str) -> str:
    return f"share_unlocked:{token}"


def _get_share_link(token: str) -> ShareLink | None:
    return (
        ShareLink.objects.filter(token=token)
        .select_related("report", "report__studio", "report__studio__org")
        .first()
    )


def _share_gone(request):
    """Revoked or expired: the link existed, but is no longer live."""
    return render(request, "reports/share_gone.html", status=410)


def _share_storage_locked(request):
    """Anonymous share credentials are not portal-session CDN grants.

    Local disk and the S3 materialized-cache path are unaffected.
    """
    return HttpResponse(
        "This shared report can't be served under the current storage "
        "configuration. Please contact whoever shared it with you.",
        status=503,
        content_type="text/plain",
    )


def _share_head_css(link) -> bytes:
    css = _SHARE_HIDE_CHROME_CSS
    if not link.allow_export:
        css += _SHARE_HIDE_EXPORT_CSS
    return f"<style>{css}</style>".encode("utf-8")


#: Stamped on every share-link view, in the page <head> so it runs before
#: the runtime's live-query binder (runtime/live_query.js, evaluated later
#: in <body>) ever checks it. The endpoint (apps.reports.livequery) already
#: refuses an anonymous viewer outright (require_studio_role(VIEWER) dies
#: in the decorator on a sessionless POST) -- this is the client-render
#: half of that same refusal: a live FilterBar on a share view renders
#: disabled with a share-specific note instead of ever attempting to arm,
#: rather than trying to query and surfacing a confusing 403. Read by
#: window._fwLiveQuery (see trellum/static/js/runtime/live_query.js) and
#: documented in trellum/docs/COMPATIBILITY.md.
_SHARE_LIVE_QUERY_SCRIPT = b"<script>window._fwShareLink=true;</script>"


def _share_head_extra(link) -> bytes:
    """Everything spliced into <head> for a share-link response: the
    chrome-hiding CSS plus the live-query-disable flag. One helper so
    share_entry/share_asset stay a single call site each. An embed link
    (internal planning ticket #12) additionally hides the report header, stamps the embed
    globals static/report_embed.js reads, and loads that runtime.

    This is also where an embed link's own appearance is applied, because it
    is the only hook that can win. The theme is NOT injected through
    ``_inject_report_chrome``/``_report_theme_attrs``: the report's own
    build-time flash-prevention script (``trellum.rendering.html_builder``)
    re-sets ``data-theme`` from ``localStorage['fw-theme']`` -- or from the
    build's default -- unconditionally, and the runtime does it once more in
    <body>, so a serve-time ``<html>`` attribute is overwritten before the
    page paints. This block is spliced in AFTER that flash script, so setting
    the attribute here sticks; clearing the stored key (shared by every
    report on this origin, so it can hold a theme a completely different host
    page picked) is what stops the runtime putting the stale one back. Both
    happen before first paint -- no flash, and charts are built from the right
    theme rather than repainted.
    """
    head = _share_head_css(link) + _SHARE_LIVE_QUERY_SCRIPT
    if link.embed:
        css = _EMBED_CSS + (_EMBED_HIDE_FILTERS_CSS if link.embed_hide_filters else "")
        # Origins and the theme name are regex-validated at create time, so
        # inlining the JSON into a <script> is safe.
        head += (
            f"<style>{css}</style>"
            f"<script>window._fwEmbed=true;window._fwEmbedOrigins={json.dumps(link.embed_origins)};"
            f"window._fwEmbedTheme={json.dumps(link.embed_theme)};"
            "window._fwEmbedBadge=false;"
            "if(window._fwEmbedTheme){"
            "document.documentElement.setAttribute('data-theme',window._fwEmbedTheme);"
            "try{localStorage.removeItem('fw-theme');}catch(e){}}</script>"
            '<script src="/api/reports/embed-widget.js" defer></script>'
        ).encode()
    return head


def _share_response(link, target, studio):
    """The one place a share/embed link becomes a response: an embed link
    swaps the global ``X-Frame-Options: DENY`` for a ``frame-ancestors``
    allow-list (the share routes are exempt from the management CSP, so
    this header stands alone).

    ``'self'`` leads that list so the portal's own pages may frame the link
    -- what the share panel's Preview (static/report_share.js) shows. It
    grants nothing a portal page couldn't already fetch directly, and
    ``frame-ancestors`` has no implicit self, so without it the preview is a
    blank frame."""
    # ponytail: 410/503 pages for an embed link keep X-Frame-Options: DENY and render blank in the frame; upgrade = pass link to _share_gone and exempt there too
    resp = _serve_report_file(target, org=studio.org, studio=studio, extra_head=_share_head_extra(link), kind=link.report.kind)
    if link.embed:
        resp.xframe_options_exempt = True  # honoured by the stock XFrameOptionsMiddleware
        resp["Content-Security-Policy"] = "frame-ancestors " + " ".join(
            ["'self'", *(link.embed_origins or [])]
        )
    return resp


def _is_export_asset(asset: str) -> bool:
    lowered = asset.lower()
    return any(lowered.endswith(ext) for ext in _EXPORT_ASSET_EXTS)


@require_http_methods(["GET", "POST"])
def share_entry(request, token):
    link = _get_share_link(token)
    if link is None:
        raise Http404
    policy = get_share_policy(link.report.studio.org)
    if not policy.share_links_enabled or not link.is_active_under(policy):
        # An anonymous visitor never learns *why* a link stopped working --
        # revoked, expired, "the org turned sharing off after this link was
        # minted", "the org now requires a password this link doesn't
        # have", and "the org capped lifetimes shorter than this link's
        # own/no expiry" all read as the identical "no longer active" page.
        return _share_gone(request)

    from apps.core import cdn

    if cdn.serves_from_edge():
        return _share_storage_locked(request)

    session_key = _share_session_key(token)
    if link.password_hash:
        from apps.accounts import throttle

        unlocked = bool(request.session.get(session_key))
        if request.method == "POST":
            # Same counters and cooloff as the login form, keyed per link (in
            # the "account" scope) and per client address. A throttled attempt
            # renders the identical "Incorrect password." form, so a visitor
            # never learns the limit exists.
            scope = f"share:{link.pk}"
            throttled = throttle.is_throttled(scope, request)
            if not throttled and link.check_password(request.POST.get("password", "")):
                throttle.clear(scope, request)
                request.session[session_key] = True
                unlocked = True
            else:
                if not throttled:
                    throttle.record_failure(scope, request)
                return render(
                    request,
                    "reports/share_password.html",
                    {"error": "Incorrect password."},
                )
        if not unlocked:
            return render(request, "reports/share_password.html", {})

    studio = link.report.studio
    slug = link.report.slug
    try:
        output_root = storage.output_root(studio, slug).resolve()
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        return _storage_outage(request, exc, as_page=True)
    entry = _get_html_entry(str(output_root))
    target = _traversal_guarded_path(output_root, entry)

    ShareLink.objects.filter(pk=link.pk).update(
        view_count=F("view_count") + 1, last_viewed_at=timezone.now()
    )
    # View analytics (internal planning#4): share_entry always serves the report's
    # entry HTML (the whole point of the route), so this is the one capture
    # point for share views -- share_asset, which serves everything else off
    # a link, never captures. The per-link running total above stays
    # untouched by this -- record_view feeds the report-level rollup, not
    # ShareLink.view_count.
    record_view(link.report, share_link=link, request=request)
    return _share_response(link, target, studio)


def share_asset(request, token, asset):  # noqa: ARG001
    link = _get_share_link(token)
    if link is None:
        raise Http404
    policy = get_share_policy(link.report.studio.org)
    if not policy.share_links_enabled or not link.is_active_under(policy):
        return _share_gone(request)

    from apps.core import cdn

    if cdn.serves_from_edge():
        return _share_storage_locked(request)

    if link.password_hash and not request.session.get(_share_session_key(token)):
        # Locked: the entry route never handed this browser a session flag,
        # so no asset of this report is reachable yet.
        return HttpResponse("This link is password-protected.", status=403, content_type="text/plain")

    if not link.allow_export and _is_export_asset(asset):
        return HttpResponse(
            "Export is disabled for this share link.", status=403, content_type="text/plain"
        )
    if _is_export_asset(asset):
        # Anonymous, but not unobserved: an exfil trail is the point of the
        # allow_export check above -- an org that turns it on should be able
        # to see who actually pulled data out through it.
        audit(
            request, "share_link.export_download", target=link.report,
            org=link.report.studio.org, token_suffix=link.token[-6:], asset=asset,
        )

    studio = link.report.studio
    slug = link.report.slug
    try:
        output_root = storage.output_root(studio, slug).resolve()
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        return _storage_outage(request, exc, as_page=False)
    target = _traversal_guarded_path(output_root, asset)
    return _share_response(link, target, studio)


# ── Share link management API (session-auth, studio developers/admins) ──────

def _share_link_dict(request, link: ShareLink, policy: OrgSharePolicy) -> dict:
    """``request`` (not ``apps.core.instance.base_url()``) is what the URL is
    built from: ``base_url()`` is the operator-configured public base, which
    on an instance where that setting hasn't been set to match the real host
    (e.g. this portal's own dev default) hands out a copy-URL that points
    nowhere. The request's own host is what actually served this response,
    so it's what the visitor's browser will actually be able to reach.

    ``policy`` is the org's *current* share policy -- needed for
    ``blocked_by_policy``, which is true for a link that is otherwise alive
    (not revoked, not past its own expiry) but fails the org's current
    require-password or max-expiry-days rule. Distinct from ``revoked_at``:
    a policy-blocked link resumes serving the moment the policy relaxes,
    with nothing to recreate, and the panel greys it with a different note.
    """
    creator = link.created_by
    return {
        "id": link.pk,
        "url": request.build_absolute_uri(f"/share/{link.token}/"),
        "created_by": (creator.display_name or creator.email) if creator else None,
        "created_at": link.created_at.isoformat(),
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "allow_export": link.allow_export,
        "has_password": bool(link.password_hash),
        "embed": link.embed,
        "embed_origins": link.embed_origins,
        "embed_theme": link.embed_theme,
        "embed_hide_filters": link.embed_hide_filters,
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "view_count": link.view_count,
        "last_viewed_at": link.last_viewed_at.isoformat() if link.last_viewed_at else None,
        "blocked_by_policy": link.is_active and not link.is_active_under(policy),
    }


@require_studio_role(roles.DEVELOPER)
@require_http_methods(["GET", "POST"])
def api_share_links(request, org_slug, studio_slug, slug):  # noqa: ARG001
    report = _get_report(request, slug)
    policy = get_share_policy(request.org)
    if request.method == "GET":
        links = ShareLink.objects.filter(report=report).select_related("created_by")
        return JsonResponse(
            {
                "share_links": [_share_link_dict(request, link, policy) for link in links],
                "sharing_enabled": policy.share_links_enabled,
                # Only an org admin can flip the policy (api_share_policy
                # below) -- the panel uses this to decide whether to point a
                # non-admin developer/admin at organization settings at all.
                "can_manage_policy": request.org_roles.is_org_admin,
                # So the create form can validate client-side and label its
                # fields accordingly (password "(required by org policy)",
                # expiry "(required, at most N days)").
                "require_password": policy.require_password,
                "max_expiry_days": policy.max_expiry_days,
                "embed_links_enabled": policy.embed_links_enabled,
            }
        )

    if not policy.share_links_enabled:
        return JsonResponse({"error": "sharing_disabled"}, status=403)

    from datetime import timedelta

    from django.utils.dateparse import parse_datetime

    body = json_body(request)
    embed = bool(body.get("embed"))
    origins: list[str] = []
    # Appearance is an embed-only choice: on an ordinary share link the host
    # page is the visitor's own browser tab, so both are ignored rather than
    # rejected (an older client that always sends them stays valid).
    embed_theme = ""
    embed_hide_filters = False
    if embed:
        if not policy.embed_links_enabled:
            return JsonResponse(
                {
                    "error": "embed_disabled",
                    "message": "This organization does not allow embed links.",
                },
                status=403,
            )
        if body.get("password") or body.get("expires_at"):
            return JsonResponse(
                {
                    "error": "embed_no_password_or_expiry",
                    "message": "An embed link never expires and takes no password.",
                },
                status=400,
            )
        raw_origins = body.get("embed_origins")
        origins = (
            [str(o).strip().lower() for o in raw_origins] if isinstance(raw_origins, list) else []
        )
        if "*" in origins:
            origins = ["*"]
        if (
            not origins
            or len(origins) > _MAX_EMBED_ORIGINS
            or not all(_EMBED_ORIGIN_RE.match(o) for o in origins)
        ):
            return JsonResponse(
                {
                    "error": "invalid_embed_origins",
                    "message": (
                        f"List 1-{_MAX_EMBED_ORIGINS} origins as scheme://host[:port] "
                        "(no path), or * for any site."
                    ),
                },
                status=400,
            )
        embed_theme = str(body.get("embed_theme") or "").strip()
        if embed_theme and not _EMBED_THEME_RE.match(embed_theme):
            return JsonResponse(
                {
                    "error": "invalid_embed_theme",
                    "message": "Pick one of the report's own themes, or leave it on the studio default.",
                },
                status=400,
            )
        embed_hide_filters = bool(body.get("embed_hide_filters"))

    password = str(body.get("password") or "")
    if password and len(password) < _MIN_SHARE_PASSWORD_LENGTH:
        return JsonResponse(
            {
                "error": "password_too_weak",
                "message": f"Use at least {_MIN_SHARE_PASSWORD_LENGTH} characters.",
            },
            status=400,
        )
    if policy.require_password and not password and not embed:
        return JsonResponse(
            {
                "error": "password_required",
                "message": "This organization requires a password on every share link.",
            },
            status=400,
        )

    raw_expires = body.get("expires_at")
    parsed_expires = None
    if raw_expires:
        parsed_expires = parse_datetime(str(raw_expires))
        if parsed_expires is None:
            return JsonResponse({"ok": False, "error": "invalid expires_at"}, status=400)
        if timezone.is_naive(parsed_expires):
            parsed_expires = timezone.make_aware(parsed_expires, timezone.get_current_timezone())

    if policy.max_expiry_days and not embed:
        plural = "s" if policy.max_expiry_days != 1 else ""
        if parsed_expires is None:
            return JsonResponse(
                {
                    "error": "expiry_required",
                    "message": (
                        f"This organization requires an expiry on every share link, "
                        f"at most {policy.max_expiry_days} day{plural} out."
                    ),
                },
                status=400,
            )
        cap = timezone.now() + timedelta(days=policy.max_expiry_days)
        if parsed_expires > cap:
            return JsonResponse(
                {
                    "error": "expiry_too_far",
                    "message": f"Expiry can be at most {policy.max_expiry_days} day{plural} from now.",
                },
                status=400,
            )

    link = ShareLink(
        report=report,
        created_by=request.user,
        allow_export=bool(body.get("allow_export", False)),
        expires_at=parsed_expires,
        embed=embed,
        embed_origins=origins,
        embed_theme=embed_theme,
        embed_hide_filters=embed_hide_filters,
    )
    link.set_password(password)
    link.save()
    audit(request, "share_link.create", target=report, token_suffix=link.token[-6:], embed=embed)
    return JsonResponse({"ok": True, "share_link": _share_link_dict(request, link, policy)}, status=201)


@require_POST
def api_share_link_revoke(request, link_id):
    """Not studio-scoped in the URL (unlike ``api_share_links`` above) --
    the revoke button only ever has the link's own id to hand, so permission
    is resolved from the link's report instead of ``require_studio_role``'s
    URL kwargs, using the same effective-role check that decorator wraps."""
    link = (
        ShareLink.objects.filter(pk=link_id)
        .select_related("report", "report__studio", "report__studio__org")
        .first()
    )
    if link is None:
        raise Http404
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication required"}, status=401)
    studio = link.report.studio
    role = get_effective(request, studio.org).role_for(studio)
    if role is None or not roles.at_least(role, roles.DEVELOPER):
        return JsonResponse({"error": "studio developer required"}, status=403)

    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at"])
    audit(request, "share_link.revoke", target=link.report, token_suffix=link.token[-6:])
    policy = get_share_policy(studio.org)
    return JsonResponse({"ok": True, "share_link": _share_link_dict(request, link, policy)})


# ── View analytics API (internal planning#4) ───────────────────────────────────────
#
# Backs the report page's "Activity" panel (static/report_views.js). Same
# role gate as the share-link manage API just above -- developers/admins --
# because both surface who has been looking at report content that may be
# sensitive; a plain viewer sees neither.

@require_studio_role(roles.DEVELOPER)
def api_report_views(request, org_slug, studio_slug, slug):  # noqa: ARG001
    from datetime import timedelta

    report = _get_report(request, slug)
    cutoff = timezone.now() - timedelta(days=30)
    views_30d = (
        ReportViewDaily.objects.filter(report=report, date__gte=cutoff.date())
        .aggregate(total=Sum("views"))
        .get("total")
        or 0
    )

    # Ordered newest-first, so the first row (if any) is also the answer to
    # "when was this last viewed" -- no separate query for that.
    recent_events = list(
        ReportViewEvent.objects.filter(report=report)
        .select_related("user")
        .order_by("-created_at")[:20]
    )
    recent = []
    for event in recent_events:
        via_share = event.share_link_id is not None
        who = None
        if event.user is not None:
            who = event.user.display_name or event.user.email
        # A share hit never names which link -- the token is the credential,
        # and this panel is not the place to expose it.
        recent.append({"when": event.created_at.isoformat(), "who": who, "via_share": via_share})

    last_viewed = recent_events[0].created_at.isoformat() if recent_events else None
    return JsonResponse(
        {
            "summary": {"views_30d": views_30d, "last_viewed": last_viewed},
            "recent": recent,
        }
    )


# ── "Metrics in this report" panel (semantic-layer Phase 2) ────────────────
#
# Backs the report page's "Metrics" panel (static/report_metrics.js). VIEWER,
# not DEVELOPER: unlike Activity above, a metric's definition is exactly the
# kind of thing every viewer of the report should be able to read -- the
# whole point of the feature is that the number's meaning is not hidden
# behind a role a plain viewer doesn't have.
#
# Reads only this report's own _meta.json (one small storage.read_meta call,
# never data.json) plus the studio's synced MetricDefinition rows -- the
# CURRENT definition's label/spec/description, cross-checked against the
# definition_hash this build claimed, so the panel never needs to open the
# build's own data.json either. See apps.reports.metrics_catalog's module
# docstring for why that comparison stays cheap.

@require_studio_role(roles.VIEWER)
def api_report_metrics(request, org_slug, studio_slug, slug):  # noqa: ARG001
    from trellum.meta import normalize_metrics_used

    report = _get_report(request, slug)
    studio = report.studio
    runtime_meta = storage.read_meta(studio, slug)
    claims = normalize_metrics_used(runtime_meta.get("metrics_used"))

    defs_by_id = {
        row.name: row
        for row in MetricDefinition.objects.filter(
            studio=studio, name__in=[c["id"] for c in claims]
        )
    }

    metrics = []
    for claim in claims:
        row = defs_by_id.get(claim["id"])
        if row is None:
            # Claimed by this build, but no longer (or not yet) in the synced
            # catalog -- metrics.yaml lost the entry, or the studio hasn't
            # synced since it was added. Shown, not dropped: the build did
            # claim it, and hiding that would be a stranger answer than an
            # honest "no longer defined".
            metrics.append({
                "id": claim["id"],
                "label": claim["id"],
                "format": "",
                "spec": "",
                "description": "",
                "current": False,
                "removed": True,
            })
            continue
        claimed_hash = claim["definition_hash"]
        current = bool(claimed_hash) and claimed_hash == row.definition_hash
        metrics.append({
            "id": row.name,
            "label": row.label or row.name,
            "format": row.format,
            "spec": row.spec_text(),
            "description": row.description,
            "current": current,
            "removed": False,
        })

    return JsonResponse(
        {
            "built_at": runtime_meta.get("last_run"),
            "studio_metrics_url": f"/s/{org_slug}/{studio_slug}/metrics",
            "metrics": metrics,
        }
    )


# ── Org-level share-links policy (org admins only) ──────────────────────────
#
# Disabled by default (apps.reports.models.sharing_enabled: no row = off).
# The toggle lives here, in apps.reports, rather than in apps.orgs -- it only
# ever governs this feature, so the endpoint stays with the feature it gates.
# ``require_org_role`` is the same decorator apps.orgs.views uses for every
# org-admin-gated settings view (members, groups, SSO, ...) -- reused as-is,
# not reimplemented.

_SHARE_POLICY_BOOL_FIELDS = {
    "enabled": "share_links_enabled",
    "require_password": "require_password",
    "embed_links_enabled": "embed_links_enabled",
}


@require_org_role(roles.ORG_ADMIN)
@require_POST
def api_share_policy(request, org_slug):  # noqa: ARG001
    """Partial updates: only the keys present in the body are touched. The
    settings page sends its complete policy in one Save action. ``enabled`` keeps its existing
    ``share_policy.enable``/``share_policy.disable`` audit actions (unchanged
    from before this endpoint grew the other two fields); a request that
    touches only the newer fields is recorded as ``share_policy.update``.
    Every action's metadata carries exactly the fields this call changed.
    """
    form_post = request.content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}
    body = {"enabled": bool(request.POST.get("enabled")), "require_password": bool(request.POST.get("require_password")), "embed_links_enabled": bool(request.POST.get("embed_links_enabled")), "max_expiry_days": request.POST.get("max_expiry_days", "")} if form_post else json_body(request)

    def invalid(message, field="__all__"):
        if is_settings_request(request):
            return settings_error(request, message, errors={field: [message]})
        if form_post:
            messages.error(request, message)
            return redirect(f"/orgs/{request.org.slug}/settings/sharing")
        return JsonResponse({"ok": False, "error": message}, status=400)
    recognized = set(_SHARE_POLICY_BOOL_FIELDS) | {"max_expiry_days"}
    if not any(key in body for key in recognized) or (form_post and not any(key in request.POST for key in recognized)):
        return invalid("no recognized fields")

    # Validate every field BEFORE touching the database: a rejected update
    # (e.g. a bad max_expiry_days) must leave no trace, not even an
    # empty-defaults OrgSharePolicy row for an org that never had one.
    changed: dict = {}
    for body_key in _SHARE_POLICY_BOOL_FIELDS:
        if body_key in body:
            changed[body_key] = bool(body[body_key])

    if "max_expiry_days" in body:
        raw = body["max_expiry_days"]
        if raw is None or raw == "":
            changed["max_expiry_days"] = None
        else:
            try:
                max_expiry_days = int(raw)
            except (TypeError, ValueError):
                return invalid("invalid max_expiry_days", "max_expiry_days")
            if max_expiry_days <= 0:
                return invalid("invalid max_expiry_days", "max_expiry_days")
            changed["max_expiry_days"] = max_expiry_days

    policy, _created = OrgSharePolicy.objects.get_or_create(org=request.org)
    update_fields = ["updated_by", "updated_at"]
    for body_key, model_field in _SHARE_POLICY_BOOL_FIELDS.items():
        if body_key in changed:
            setattr(policy, model_field, changed[body_key])
            update_fields.append(model_field)
    if "max_expiry_days" in changed:
        policy.max_expiry_days = changed["max_expiry_days"]
        update_fields.append("max_expiry_days")

    policy.updated_by = request.user
    policy.save(update_fields=update_fields)

    if "enabled" in changed:
        action = "share_policy.enable" if changed["enabled"] else "share_policy.disable"
    else:
        action = "share_policy.update"
    audit(request, action, org=request.org, **changed)

    if is_settings_request(request):
        return settings_success(request, "Saved.")
    if form_post:
        messages.success(request, "Saved.")
        return redirect(f"/orgs/{request.org.slug}/settings/sharing")

    return JsonResponse(
        {
            "ok": True,
            "share_links_enabled": policy.share_links_enabled,
            "require_password": policy.require_password,
            "max_expiry_days": policy.max_expiry_days,
            "embed_links_enabled": policy.embed_links_enabled,
        }
    )


@require_org_role(roles.ORG_ADMIN)
def org_share_settings(request, org_slug):  # noqa: ARG001
    """The "Report sharing" org-settings tab -- its own page (internal planning#6
    follow-up), not the Members-page include this replaced. Same
    org-admin gate, same settings-shell template, as every other tab
    apps.orgs.views renders (studios, members, groups, SSO, AI assistant, audit
    log); this one just lives with the feature it configures instead of in
    the org app."""
    return render(
        request,
        "reports/org_share_settings.html",
        {"org": request.org, "policy": get_share_policy(request.org)},
    )


# ── Org-level live-query rate limit (org admins only) ────────────────────────
#
# The live-query filter redesign lets one filter change fan out into N
# linked queries (LiveDataSource.propagate_to), so the process-wide 30/min
# default from M1 no longer fits every org equally. Same shape as the share
# policy pair just above: a partial-update POST endpoint plus the page itself.
# Lives in
# apps.reports (not apps.orgs) for the same reason OrgSharePolicy does --
# it only ever governs this one report-serving feature.

@require_org_role(roles.ORG_ADMIN)
@require_POST
def api_live_query_policy(request, org_slug):  # noqa: ARG001
    """Body: ``{"rate_limit_per_minute": int|null}`` -- null resets to the
    default (DEFAULT_LIVE_QUERY_RATE_LIMIT). Validated before touching the
    database, same discipline as api_share_policy: a rejected update leaves
    no trace, not even an empty-defaults row for an org that never had one.
    """
    form_post = request.content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}
    body = {"rate_limit_per_minute": request.POST.get("rate_limit_per_minute", "")} if form_post else json_body(request)

    def invalid(message, field="__all__"):
        if is_settings_request(request):
            return settings_error(request, message, errors={field: [message]})
        if form_post:
            messages.error(request, message)
            return redirect(f"/orgs/{request.org.slug}/settings/live-queries")
        return JsonResponse({"ok": False, "error": message}, status=400)
    if "rate_limit_per_minute" not in body or (form_post and "rate_limit_per_minute" not in request.POST):
        return invalid("no recognized fields")

    raw = body["rate_limit_per_minute"]
    if raw is None or raw == "":
        value = None
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return invalid("invalid rate_limit_per_minute", "rate_limit_per_minute")
        # 0 is the org's "off" switch: live_query_rate_limit(org) returns 0,
        # which blocks every live execution and drops the "Live" marker
        # (apps/reports/scan.py). Negatives are still nonsense.
        if value < 0:
            return invalid("invalid rate_limit_per_minute", "rate_limit_per_minute")

    policy, _created = OrgLiveQueryPolicy.objects.get_or_create(org=request.org)
    policy.rate_limit_per_minute = value
    policy.updated_by = request.user
    policy.save(update_fields=["rate_limit_per_minute", "updated_by", "updated_at"])

    audit(request, "live_query_policy.update", org=request.org, rate_limit_per_minute=value)

    if is_settings_request(request):
        return settings_success(request, "Saved.")
    if form_post:
        messages.success(request, "Saved.")
        return redirect(f"/orgs/{request.org.slug}/settings/live-queries")

    return JsonResponse({
        "ok": True,
        "rate_limit_per_minute": policy.rate_limit_per_minute,
        "effective_rate_limit_per_minute": live_query_rate_limit(request.org),
    })


@require_org_role(roles.ORG_ADMIN)
def org_live_query_settings(request, org_slug):  # noqa: ARG001
    """The "Live queries" org-settings tab. Same org-admin gate, same
    settings-shell template, as every other tab (see org_share_settings)."""
    policy = OrgLiveQueryPolicy.objects.filter(org=request.org).first()
    return render(
        request,
        "reports/org_live_query_settings.html",
        {
            "org": request.org,
            "rate_limit_per_minute": policy.rate_limit_per_minute if policy else None,
            "default_rate_limit_per_minute": DEFAULT_LIVE_QUERY_RATE_LIMIT,
        },
    )


# ── Share widget (self-injecting bundle for built report pages) ────────────
#
# Same frozen-URL trick as delivery_widget_js above (see its docstring): a
# report build has no template to attach a {% static %} tag to. The button
# this mounts is gated client-side on the viewer's own role (see
# static/report_share.js) -- the script tag itself is baked into every
# build unconditionally (apps.runner.executor.portal_extensions), same as
# the assistant and delivery widgets.

_SHARE_WIDGET_LOCK = threading.Lock()
_SHARE_WIDGET_CACHE: str | None = None


def share_widget_js(request):  # noqa: ARG001
    global _SHARE_WIDGET_CACHE
    with _SHARE_WIDGET_LOCK:
        if _SHARE_WIDGET_CACHE is None:
            _SHARE_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_share.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_SHARE_WIDGET_CACHE, content_type="application/javascript")


# ── Embed runtime (loaded by embed-link pages, see _share_head_extra) ──────

_EMBED_WIDGET_LOCK = threading.Lock()
_EMBED_WIDGET_CACHE: str | None = None


def embed_widget_js(request):  # noqa: ARG001
    global _EMBED_WIDGET_CACHE
    with _EMBED_WIDGET_LOCK:
        if _EMBED_WIDGET_CACHE is None:
            _EMBED_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_embed.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_EMBED_WIDGET_CACHE, content_type="application/javascript")


# ── Embed element (<trellum-report>, loaded by the *host* page) ────────────
#
# The mirror image of embed_widget_js above: that one runs inside the frame,
# this one runs on the customer's own site and creates the frame. Same
# frozen-URL delivery, because the host writes the <script src> by hand from
# the docs and it must never move. No CSP or CORS work: a classic <script>
# is not a cross-origin fetch, and the management CSP this response carries
# applies to documents, not to script bodies (internal planning ticket #12).

_EMBED_ELEMENT_LOCK = threading.Lock()
_EMBED_ELEMENT_CACHE: str | None = None


def embed_element_js(request):  # noqa: ARG001
    global _EMBED_ELEMENT_CACHE
    with _EMBED_ELEMENT_LOCK:
        if _EMBED_ELEMENT_CACHE is None:
            _EMBED_ELEMENT_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_embed_element.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_EMBED_ELEMENT_CACHE, content_type="application/javascript")


# ── View analytics widget (self-injecting bundle for built report pages) ───
#
# Same frozen-URL trick as delivery_widget_js/share_widget_js above. The
# "Activity" header button this mounts only self-mounts for a viewer who can
# actually see the panel (developers/admins) -- see static/report_views.js,
# which gates itself the same way static/report_share.js gates the Share
# button.

_VIEWS_WIDGET_LOCK = threading.Lock()
_VIEWS_WIDGET_CACHE: str | None = None


def views_widget_js(request):  # noqa: ARG001
    global _VIEWS_WIDGET_CACHE
    with _VIEWS_WIDGET_LOCK:
        if _VIEWS_WIDGET_CACHE is None:
            _VIEWS_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_views.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_VIEWS_WIDGET_CACHE, content_type="application/javascript")


# ── Metrics widget (self-injecting bundle for built report pages) ──────────
#
# Same frozen-URL trick as delivery_widget_js/share_widget_js/views_widget_js
# above -- see delivery_widget_js's docstring for why a raw-content view and
# not a {% static %} tag. "Metrics in this report" (static/report_metrics.js)
# self-mounts for every viewer (see api_report_metrics's VIEWER gate above),
# so unlike the Activity widget it never has to probe permission first.

_METRICS_WIDGET_LOCK = threading.Lock()
_METRICS_WIDGET_CACHE: str | None = None


def metrics_widget_js(request):  # noqa: ARG001
    global _METRICS_WIDGET_CACHE
    with _METRICS_WIDGET_LOCK:
        if _METRICS_WIDGET_CACHE is None:
            _METRICS_WIDGET_CACHE = (
                Path(settings.BASE_DIR) / "static" / "report_metrics.js"
            ).read_text(encoding="utf-8")
    return HttpResponse(_METRICS_WIDGET_CACHE, content_type="application/javascript")


# ── Favorites (per-user, cross-studio) ──────────────────────────────────────

def _favorites_auth(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication required"}, status=401)
    return None


def _visible_favorite_rows(request):
    rows = (
        ReportFavorite.objects.filter(user=request.user)
        .select_related("report", "report__studio", "report__studio__org")
        .order_by("created_at")
    )
    out = []
    er_cache: dict[int, object] = {}
    for fav in rows:
        studio = fav.report.studio
        org = studio.org
        er = er_cache.get(org.pk)
        if er is None:
            er = er_cache[org.pk] = get_effective(request, org)
        scope = er.report_scope_for(studio)
        if (scope is not None and fav.report_id not in scope) or not fav.report.present_in_scan:
            continue
        out.append(fav)
    return out


@require_http_methods(["GET"])
def api_favorites(request):
    resp = _favorites_auth(request)
    if resp:
        return resp
    favorites = [
        {
            "report_id": fav.report_id,
            "org_slug": fav.report.studio.org.slug,
            "studio_slug": fav.report.studio.slug,
            "slug": fav.report.slug,
            "name": fav.report.name or fav.report.slug,
            "kind": fav.report.kind,
        }
        for fav in _visible_favorite_rows(request)
    ]
    return JsonResponse({"favorites": favorites})


@require_http_methods(["PUT", "DELETE"])
def api_favorite_toggle(request, report_id: int):
    resp = _favorites_auth(request)
    if resp:
        return resp
    report = (
        Report.objects.filter(pk=report_id)
        .select_related("studio", "studio__org")
        .first()
    )
    if report is None:
        raise Http404
    scope = get_effective(request, report.studio.org).report_scope_for(report.studio)
    if scope is not None and report.pk not in scope:
        raise Http404  # can't see the studio -> can't see the report exists
    if request.method == "PUT":
        ReportFavorite.objects.get_or_create(user=request.user, report=report)
        return JsonResponse({"ok": True, "favorited": True})
    ReportFavorite.objects.filter(user=request.user, report=report).delete()
    return JsonResponse({"ok": True, "favorited": False})


@require_POST
def api_favorites_import(request):
    """One-time migration of the legacy localStorage favorites list.

    Body: {"slugs": ["player-overview", ...]} — matched against reports in
    every studio the user can view (first match per slug wins).
    """
    resp = _favorites_auth(request)
    if resp:
        return resp
    slugs = json_body(request).get("slugs") or []
    if not isinstance(slugs, list):
        return JsonResponse({"ok": False, "error": "slugs must be a list"}, status=400)
    imported = 0
    for slug in slugs[:200]:
        candidates = visible_reports(
            request.user,
            Report.objects.filter(slug=str(slug), present_in_scan=True).select_related(
                "studio", "studio__org"
            ),
        )
        key = getattr(request, "api_key", None)
        if key is not None:
            candidates = candidates.filter(studio__org_id=key.org_id)
        for report in candidates:
            _, created = ReportFavorite.objects.get_or_create(user=request.user, report=report)
            imported += int(created)
            break
    return JsonResponse({"ok": True, "imported": imported})
