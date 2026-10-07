"""Registry sync + the legacy registry JSON payload, per studio.

Port of ``portal/registry.py``. The scan uses only explicit-path framework
contract functions (``scan_report_configs(reports_dir)``, ``read_meta(dir)``)
— never the process-global project root, which does not exist in a
multi-studio web process.
"""
from __future__ import annotations

import logging
import os
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from django.db.models import Max, Sum
from django.utils import timezone as dj_tz

from apps.datasources.status import (
    SourceState,
    blocked_names,
    build_error,
    latest_runs,
    referenced_names,
    source_states,
)
from apps.reports.models import MetricDefinition, Report

#: A build counts as "recent" for the stale badge (build_registry_payload)
#: when it succeeded within this many days.
_STALE_BUILD_WINDOW_DAYS = 7
#: The window build_registry_payload's "views (30d)" meta line sums over.
_VIEWS_WINDOW_DAYS = 30

logger = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_registry_cache: dict[int, tuple[float, dict]] = {}
_REGISTRY_CACHE_TTL = 5.0


class ReportQuotaExceeded(Exception):
    """The studio's repo holds more reports than the org's quota allows.

    Raised *after* the reports that fit have been synced, so the studio keeps
    working and the operator sees exactly what was left out.
    """


def _apply_report_cap(studio, entries: list) -> tuple[int, int] | None:
    """Trim ``entries`` in place to the org's report cap.

    Returns (dropped, cap) when anything was trimmed, else None. Reports
    already registered for this studio keep their place: a cap must never make
    an existing report vanish because an unrelated one was pushed first.
    """
    from apps.orgs import quotas

    cap = quotas.limit(studio.org, "max_reports")
    if not cap:
        return None

    # Everything this org has outside the studio being scanned is already
    # spending the budget.
    from apps.reports.models import Report

    elsewhere = (
        Report.objects.filter(studio__org=studio.org, present_in_scan=True)
        .exclude(studio=studio)
        .count()
    )
    budget = max(0, cap - elsewhere)
    if len(entries) <= budget:
        return None

    known = set(
        Report.objects.filter(studio=studio, present_in_scan=True).values_list(
            "slug", flat=True
        )
    )
    entries.sort(key=lambda e: (e["slug"] not in known, e["slug"]))
    dropped = len(entries) - budget
    del entries[budget:]
    return dropped, cap


def _priority(config: dict) -> int:
    """``display.priority``: lower sorts first; missing or unusable is 99
    (last), the same reading as the framework's gallery."""
    try:
        return int((config.get("display") or {}).get("priority"))
    except (TypeError, ValueError):
        return 99


def sync_studio_registry(studio) -> int:
    """Scan ``<studio>/project/reports`` and upsert Report rows.

    Returns the number of reports currently present. Reports that vanish
    from the scan are kept (runs/favorites reference them) but flagged
    ``present_in_scan=False``.

    Raises :class:`ReportQuotaExceeded` when the org's report cap would be
    exceeded, *after* syncing everything that fits: reports arrive by git push,
    so there is no request to refuse, and dropping the whole sync would make a
    single report over the line look like the portal had lost the studio.
    """
    from trellum.runner import scan_report_configs

    reports_dir = str(studio.reports_dir)
    entries = scan_report_configs(reports_dir, include_hidden=False) if os.path.isdir(reports_dir) else []

    # Sorted so "which reports fit under the cap" is stable between scans
    # rather than depending on directory iteration order.
    entries = sorted(entries, key=lambda e: e["slug"])
    overage = _apply_report_cap(studio, entries)

    now = dj_tz.now()
    seen: set[str] = set()
    for entry in entries:
        config = entry.get("config") or {}
        slug = entry["slug"]
        seen.add(slug)
        kind = config.get("kind", Report.KIND_REPORT)
        # Analyses are published snapshots; a YAML cron must never refresh them.
        schedule = (config.get("schedule") or {}) if kind == Report.KIND_REPORT else {}
        Report.objects.update_or_create(
            studio=studio,
            slug=slug,
            defaults={
                "kind": kind,
                "name": config.get("name", slug),
                "description": config.get("description", ""),
                "category": config.get("category", "Uncategorized"),
                "tags": config.get("tags", []) or [],
                "schedule_cron": (schedule.get("cron") or "").strip(),
                "schedule_timezone": str(schedule.get("timezone") or "UTC"),
                "disabled": bool(config.get("disabled", False)),
                "priority": _priority(config),
                "config": config,
                "present_in_scan": True,
                "last_scanned_at": now,
            },
        )
    Report.objects.filter(studio=studio, present_in_scan=True).exclude(slug__in=seen).update(
        present_in_scan=False, last_scanned_at=now
    )
    invalidate_registry_cache(studio)
    try:
        sync_studio_metrics(studio)
    except Exception:  # noqa: BLE001 - a metrics.yaml problem must never sink the report scan
        logger.exception("metrics.yaml sync failed for studio=%s", studio.pk)
    try:
        sync_studio_repo_theme(studio)
    except Exception:  # noqa: BLE001 - a config.yaml problem must never sink the report scan
        logger.exception("config.yaml theme sync failed for studio=%s", studio.pk)
    if overage:
        dropped, cap = overage
        raise ReportQuotaExceeded(
            f"{dropped} report(s) were not registered: this organization is "
            f"limited to {cap} reports. Raise the quota, or remove reports "
            f"from the repository."
        )
    return len(seen)


def sync_studio_metrics(studio) -> int:
    """Scan ``<studio>/project/metrics.yaml`` and upsert ``MetricDefinition``
    rows (semantic-layer Phase 2).

    Called from :func:`sync_studio_registry` -- "synced in the same pass
    that scans report.yaml" is the whole design: git stays the source of
    truth for both, and a metric goes stale on the catalog page the moment a
    studio's repo re-syncs, exactly like a report does. Returns the number
    of metrics currently defined.

    ``trellum.metrics.load_metrics_result`` already treats an absent or
    malformed ``metrics.yaml`` as "no metrics defined" (with problems
    recorded, never raised) rather than an error -- most studios have no
    ``metrics.yaml`` at all, and a hand-authored typo in one must not take
    the report scan down with it. A metric that disappears from the file is
    flagged ``present_in_scan=False``, like ``Report``, rather than deleted:
    a report built against it keeps its history and its claim readable.

    Explicit ``project_root`` throughout (never the process-global project
    root, which does not exist in a multi-studio web process) -- see this
    module's own docstring.
    """
    from trellum.metrics import invalidate_metrics_cache, load_metrics_result

    # The loader caches by path for the life of the process; a git sync just
    # rewrote the file on disk, so the cache must not answer from before it.
    # Clears every studio's cached registry, not just this one -- sync is not
    # a hot path, and a per-path invalidation hook does not exist upstream.
    invalidate_metrics_cache()
    result = load_metrics_result(str(studio.project_root))

    now = dj_tz.now()
    seen: set[str] = set()
    for metric in result.metrics.values():
        seen.add(metric.name)
        MetricDefinition.objects.update_or_create(
            studio=studio,
            name=metric.name,
            defaults={
                "label": metric.label,
                "description": metric.description,
                "owner": metric.owner,
                "format": metric.format,
                "version": metric.version,
                "agg": metric.agg or "",
                "column": metric.column or "",
                "numerator": metric.numerator or "",
                "denominator": metric.denominator or "",
                "sql": metric.sql,
                "dimensions": list(metric.dimensions),
                "tags": list(metric.tags),
                "definition_hash": metric.definition_hash,
                "present_in_scan": True,
                "last_scanned_at": now,
            },
        )
    MetricDefinition.objects.filter(studio=studio, present_in_scan=True).exclude(
        name__in=seen
    ).update(present_in_scan=False, last_scanned_at=now)
    return len(seen)


def sync_studio_repo_theme(studio) -> str:
    """Stamp ``Studio.repo_theme`` from ``<studio>/project/config.yaml``'s
    ``theme:`` key -- the file ``apps.runner.gitsync`` mirrors in from the
    studio's own repository alongside ``events.yaml``/``metrics.yaml`` (see
    ``apps.runner.executor.PROJECT_ROOT_BUILD_FILES``).

    Called from :func:`sync_studio_registry`, same "never sink the scan over
    a tenant file problem" rule as :func:`sync_studio_metrics`: a missing
    file, malformed YAML, a non-mapping document, or a blank/non-string
    ``theme:`` are all just "no repo theme declared" -> ``""``, not an
    error. The portal does NOT check this name against
    ``trellum.themes.THEME_REGISTRY`` -- it may be a theme the repo
    registers itself at build time (``trellum.themes.register_theme``),
    invisible to the portal outside that one repo's own build process; a
    typo is instead caught by the framework's own ``theme-invalid``
    validation check when the report actually builds. See
    ``apps.core.themes.explicit_studio_theme`` / ``has_custom_repo_theme``
    for what a registry vs. custom name means for resolution.

    Explicit ``project_root`` off ``studio``, never the process-global
    project root -- see this module's own docstring.
    """
    import yaml

    path = studio.project_root / "config.yaml"
    theme = ""
    if path.is_file():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
        if isinstance(data, dict):
            theme = str(data.get("theme") or "").strip()
    if theme != studio.repo_theme:
        studio.repo_theme = theme
        studio.save(update_fields=["repo_theme"])
    return theme


def invalidate_registry_cache(studio=None) -> None:
    with _cache_lock:
        if studio is None:
            _registry_cache.clear()
        else:
            _registry_cache.pop(studio.pk, None)


def _get_html_entry(report_output_dir: str) -> str:
    if not os.path.isdir(report_output_dir):
        return "index.html"
    if os.path.isfile(os.path.join(report_output_dir, "index.html")):
        return "index.html"
    for f in sorted(os.listdir(report_output_dir)):
        if f.endswith(".html"):
            return f
    return "index.html"


def _view_stats_for(rows: list) -> tuple[dict, dict]:
    """(views_30d_by_report_id, last_viewed_by_report_id) for ``rows``, each
    ONE query across every report rather than one per report -- see the
    call site in build_registry_payload for why that matters.
    """
    from apps.reports.models import ReportViewDaily, ReportViewEvent

    if not rows:
        return {}, {}
    report_ids = [r.pk for r in rows]
    cutoff_date = (dj_tz.now() - timedelta(days=_VIEWS_WINDOW_DAYS)).date()
    views_30d = {
        entry["report"]: entry["total"]
        for entry in ReportViewDaily.objects.filter(
            report_id__in=report_ids, date__gte=cutoff_date
        ).values("report").annotate(total=Sum("views"))
    }
    last_viewed = {
        entry["report"]: entry["latest"]
        for entry in ReportViewEvent.objects.filter(report_id__in=report_ids)
        .values("report").annotate(latest=Max("created_at"))
    }
    return views_30d, last_viewed


def _built_recently(last_status: str, last_run: str | None) -> bool:
    """Whether the report's last build both succeeded and finished within
    ``_STALE_BUILD_WINDOW_DAYS`` -- the framework writes ``last_run`` on
    every attempt (success or error), so both conditions are required (see
    ``trellum.meta.is_fresh``, the identical check for cache freshness)."""
    if last_status != "success" or not last_run:
        return False
    try:
        last_dt = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (dj_tz.now() - last_dt) <= timedelta(days=_STALE_BUILD_WINDOW_DAYS)


def build_registry_payload(studio) -> dict:
    """The legacy ``/api/registry`` shape, resolved for one studio.

    Config comes from the synced Report rows; run state comes live from
    ``output/<slug>/_meta.json`` exactly as before (the framework owns it).
    """
    from apps.core import storage
    from apps.reports.metrics_catalog import is_generated_metrics_report

    now = _time.time()
    with _cache_lock:
        hit = _registry_cache.get(studio.pk)
        if hit and (now - hit[0]) < _REGISTRY_CACHE_TTL:
            return hit[1]

    # One listing for the whole studio, then one small metadata read per
    # report. Resolving the entry point per report from the filesystem would
    # be a round trip each on a remote store.
    #
    # A store outage degrades the registry rather than 500ing it: rows render
    # from their config with status "storage_unavailable", and after the first
    # failure no further reads are attempted — each one would eat its own
    # timeout against a store we already know is down. The (short-TTL) cache
    # keeps a dead store from being probed on every page load.
    storage_down = False
    try:
        html_index = storage.html_index(studio)
    except Exception as exc:  # noqa: BLE001 - driver raises per-operation classes
        logger.warning(f"report storage unreachable: {type(exc).__name__}: {exc}")
        html_index = {}
        storage_down = True

    # Report.Meta.ordering (priority, then slug) is the card order.
    rows = list(Report.objects.filter(studio=studio, present_in_scan=True))

    # View analytics (internal planning#4): two aggregate queries for the WHOLE
    # studio, not one per report -- the dashboard cards' "views (30d)" /
    # "last viewed" meta line and stale badge all stitch onto `rows` from
    # these two dicts in Python below, so a studio with a hundred reports
    # still costs exactly two extra queries here, not a hundred.
    views_30d_by_report, last_viewed_by_report = _view_stats_for(rows)

    # Which reports run live queries at view time (one bulk listing, like
    # html_index) AND whether this org permits them at all. A report whose
    # queries could never execute is not advertised as "live" -- a per-org
    # rate limit of 0 is the "off" switch (apps.reports.models
    # .live_query_rate_limit), so live_enabled folds the policy in here rather
    # than every consumer re-checking it.
    live_slugs: set = set()
    if not storage_down:
        try:
            live_slugs = storage.live_query_index(studio)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"live-query index unreachable: {type(exc).__name__}: {exc}")
    from apps.reports.models import live_query_rate_limit
    live_enabled = live_query_rate_limit(studio.org) > 0

    # Data source state, once per studio: which reports are waiting for a
    # source (blocked_by/waiting), which built report reads from a source that
    # is failing now, and a failure's attribution when only the run carries it.
    states = {s.name: s for s in source_states(studio)}
    runs = latest_runs(studio)
    blocks: dict[str, list[str]] = {}  # source name -> slugs it holds

    reports = []
    for row in rows:
        config = row.config or {}
        html_names = html_index.get(row.slug, [])
        runtime_meta: dict = {}
        if not storage_down:
            try:
                runtime_meta = storage.read_meta(studio, row.slug)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"report storage unreachable: {type(exc).__name__}: {exc}"
                )
                storage_down = True
        last_run = runtime_meta.get("last_run")
        last_status = (
            "storage_unavailable" if storage_down else runtime_meta.get("last_status", "not_run")
        )
        views_30d = views_30d_by_report.get(row.pk, 0)
        last_viewed = last_viewed_by_report.get(row.pk)
        latest = runs.get(row.slug)
        referenced = [n for n in referenced_names(config) if n in states]
        # What held the last run only counts while it still would: a source
        # configured since, or a reference dropped since, clears the wait
        # before the next build does. A disabled report waits for nothing.
        holding = [n for n in referenced if states[n].blocking] if not row.disabled else []
        blocked_by = [n for n in blocked_names(runtime_meta, latest) if n in holding]
        for n in holding:
            blocks.setdefault(n, []).append(row.slug)
        failing = next((states[n] for n in referenced if states[n].state == SourceState.FAILING), None)
        reports.append(
            {
                "id": row.pk,
                "kind": row.kind,
                "slug": row.slug,
                "name": row.name or row.slug,
                "description": row.description,
                "category": row.category or "Uncategorized",
                "studio": studio.slug,
                "tags": row.tags,
                "schedule": row.schedule_cron,
                "last_run": last_run,
                "last_status": last_status,
                "last_error": build_error(runtime_meta, latest)[:500] or None,
                "blocked_by": blocked_by,
                "waiting": bool(blocked_by),
                "failing_source": (
                    {
                        "name": failing.name,
                        "since": failing.last_check_at.isoformat() if failing.last_check_at else None,
                    }
                    if failing is not None else None
                ),
                "last_built_at": row.last_built_at.isoformat() if row.last_built_at else None,
                "has_output": bool(html_names),
                "html_entry": storage.entry_for(html_names),
                "validation": runtime_meta.get("validation"),
                # Legacy "health" fallback: reports generated before the
                # details-rename still surface totals without a re-run.
                "details": runtime_meta.get("details") or runtime_meta.get("health"),
                "framework_version": runtime_meta.get("framework_version"),
                "views_30d": views_30d,
                "last_viewed": last_viewed.isoformat() if last_viewed else None,
                # "Nobody has looked at a build that finished recently" --
                # the signal that a report may need pointing out to whoever
                # it's for, not that it's broken.
                "stale": views_30d == 0 and _built_recently(last_status, last_run),
                # Runs live queries at view time (and the org allows them), so
                # the overview can mark it "live" rather than a static snapshot.
                "live": row.kind == Report.KIND_REPORT and live_enabled and (row.slug in live_slugs),
                # The generated metrics report: the build behind the Metrics
                # page's charts, not a report anyone wrote. The dashboard
                # leaves it to that page instead of showing a card for it
                # (static/portal.js); Operations still lists and runs it.
                "metrics_report": is_generated_metrics_report(row),
            }
        )
    payload = {
        "reports": reports,
        "source_states": _source_states_json(states.values(), blocks),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if storage_down:
        payload["storage_unavailable"] = True
    with _cache_lock:
        _registry_cache[studio.pk] = (now, payload)
    return payload


def _source_states_json(states, blocks: dict[str, list[str]]) -> list[dict]:
    """The Operations tab's data source rows: each source's state, the
    reports it holds, and what its Upload/Download actions need. Built with
    the payload so the 2-second status poll reads it from the same cache."""
    from apps.datasources.materialize import stored_file
    from apps.datasources.models import INLINE_TYPES
    from apps.datasources.status import effective_fields

    rows = []
    for s in states:
        fields = effective_fields(s.declaration, s.binding) if (s.declaration or s.binding) else {}
        upload = s.type in INLINE_TYPES and bool(fields.get("upload"))
        # Only a portal-managed file is offered for download; a repository
        # file is in git, and the stat costs queries per source.
        stored = stored_file(s.binding) if upload and s.binding is not None else None
        rows.append(
            {
                "name": s.name,
                "type": s.type,
                "state": s.state,
                "detail": s.detail,
                "used_by": s.used_by,
                "binding_scope": s.binding_scope,
                "blocks": sorted(blocks.get(s.name, [])),
                "upload": upload,
                "file": bool(stored is not None and stored.is_file()),
            }
        )
    return rows
