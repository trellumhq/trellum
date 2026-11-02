"""Registry sync, per studio (the registry JSON itself is ``registry_rows``).

Port of ``portal/registry.py``. The scan uses only explicit-path framework
contract functions (``scan_report_configs(reports_dir)``, ``read_meta(dir)``)
— never the process-global project root, which does not exist in a
multi-studio web process.
"""
from __future__ import annotations

import json
import logging
import os

from django.db import models
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.utils import timezone as dj_tz

from apps.reports.models import MetricDefinition, Report

#: A build counts as "recent" for the stale badge when it succeeded within
#: this many days (``apps.reports.registry_rows`` and the studio analytics).
_STALE_BUILD_WINDOW_DAYS = 7

logger = logging.getLogger(__name__)


class RegistrySyncWarning(Exception):
    """Some entries were rejected after the rest of the registry was synced."""


class ReportAdmissionRejected(ValidationError, RegistrySyncWarning):
    """New report audiences were rejected; existing rows remain authoritative."""


class ReportQuotaExceeded(RegistrySyncWarning):
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
        Report.objects.filter(studio__org=studio.org).filter(
            models.Q(present_in_scan=True) | models.Q(archived_in_scan=True))
        .exclude(studio=studio)
        .count()
    )
    budget = max(0, cap - elsewhere)
    if len(entries) <= budget:
        return None

    known = set(
        Report.objects.filter(studio=studio).filter(
            models.Q(present_in_scan=True) | models.Q(archived_in_scan=True)).values_list(
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


def _creation_audience(studio, config: dict, kind: str) -> str:
    """Validate the initial audience for a new registry row."""
    from apps.core.report_access import selected_report_access_block_reason

    audience = config.get("initial_audience", (
        studio.default_analysis_audience if kind == Report.KIND_ANALYSIS
        else studio.default_report_audience
    ))
    if audience not in (Report.AUDIENCE_STUDIO, Report.AUDIENCE_PRIVATE):
        raise ReportAdmissionRejected("initial_audience must be studio or private.")
    if audience == Report.AUDIENCE_PRIVATE:
        reason = selected_report_access_block_reason()
        if reason:
            raise ReportAdmissionRejected(
                f"Private audience is unavailable: {reason}. Configure private "
                "report access before publishing this new item."
            )
    return audience


def sync_studio_registry(studio) -> int:
    """Scan ``<studio>/project/reports`` and upsert Report rows.

    Returns the number of reports currently present. Reports that vanish
    from the scan are kept (runs/favorites reference them) but flagged
    ``present_in_scan=False``.

    Raises :class:`RegistrySyncWarning` for rejected new audiences or when the
    org's report cap would be exceeded, *after* syncing valid entries. Reports arrive by git push,
    so there is no request to refuse, and dropping the whole sync would make a
    single report over the line look like the portal had lost the studio.
    """
    from trellum.runner import scan_report_configs

    # A repository sync may have retained this instance while settings changed.
    studio.refresh_from_db(fields=["default_report_audience", "default_analysis_audience"])
    reports_dir = str(studio.reports_dir)
    entries = scan_report_configs(reports_dir, include_hidden=True, include_disabled=True) if os.path.isdir(reports_dir) else []
    # include_hidden also includes incomplete framework drafts. Keep the same
    # publishability requirement as the ordinary scan, while indexing hidden
    # items for administrator discovery. Repository hidden state is authoritative.
    entries = [entry for entry in entries if os.path.isfile(os.path.join(
        entry["dir"],
        ("slides.yaml" if entry["config"].get("format") == "presentation" else "content.md")
        if entry["config"].get("kind") == Report.KIND_ANALYSIS else "generator.py",
    ))]

    rejected = 0
    rejected_slugs: set[str] = set()
    problems: list[str] = []

    def record_rejection(slug, exc):
        nonlocal rejected
        rejected += 1
        rejected_slugs.add(slug)
        if len(problems) < 3:
            problems.append(f"{slug[:64]}: {'; '.join(exc.messages)}")

    # Reject unsupported creations before assigning quota slots, so they do
    # not crowd out valid entries. A second check in create_defaults protects
    # creation if an existing row is deleted after this snapshot.
    known = set(Report.objects.filter(
        studio=studio, slug__in=[entry["slug"] for entry in entries],
    ).values_list("slug", flat=True))
    eligible = []
    for entry in entries:
        if entry["slug"] not in known:
            config = entry.get("config") or {}
            try:
                _creation_audience(studio, config, config.get("kind", Report.KIND_REPORT))
            except ReportAdmissionRejected as exc:
                record_rejection(entry["slug"], exc)
                continue
        eligible.append(entry)
    # Stable quota admission regardless of directory iteration order.
    entries = sorted(eligible, key=lambda e: e["slug"])
    overage = _apply_report_cap(studio, entries)

    now = dj_tz.now()
    # A rejection must not mark an existing/concurrently registered identity
    # missing: the manifest is still present even if creation was refused.
    seen: set[str] = set(rejected_slugs)
    for entry in entries:
        # YAML timestamps are native date/datetime objects. Store their ISO
        # representation so the JSON config column accepts annotation dates.
        config = json.loads(json.dumps(entry.get("config") or {}, cls=DjangoJSONEncoder))
        slug = entry["slug"]
        seen.add(slug)
        kind = config.get("kind", Report.KIND_REPORT)
        # Analyses are published snapshots; a YAML cron must never refresh them.
        schedule = (config.get("schedule") or {}) if kind == Report.KIND_REPORT else {}
        defaults = {
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
            "present_in_scan": not bool(config.get("hidden")),
            "archived_in_scan": bool(config.get("hidden")),
            "last_scanned_at": now,
        }
        from django.db import transaction
        from .report_experiment_projection import sync_declarations
        try:
            with transaction.atomic():
                row, _ = Report.objects.update_or_create(
                    studio=studio,
                    slug=slug,
                    defaults=defaults,
                    # Django resolves this callable only on creation. Existing
                    # rows (including removed/re-added ones) keep their stored
                    # audience under update_or_create's row lock.
                    create_defaults={**defaults, "audience": lambda: _creation_audience(studio, config, kind)},
                )
                sync_declarations(row)
        except ReportAdmissionRejected as exc:
            record_rejection(slug, exc)
    from django.db import transaction
    from .source_summary import invalidate_reports

    with transaction.atomic():
        missing = Report.objects.filter(studio=studio).filter(
            models.Q(present_in_scan=True) | models.Q(archived_in_scan=True)).exclude(slug__in=seen)
        missing_ids = list(missing.values_list("pk", flat=True))
        missing.update(present_in_scan=False, archived_in_scan=False, last_scanned_at=now)
        invalidate_reports(missing_ids, "removed")
    try:
        sync_studio_metrics(studio)
    except Exception:  # noqa: BLE001 - a metrics.yaml problem must never sink the report scan
        logger.exception("metrics.yaml sync failed for studio=%s", studio.pk)
    try:
        sync_studio_repo_theme(studio)
    except Exception:  # noqa: BLE001 - a config.yaml problem must never sink the report scan
        logger.exception("config.yaml theme sync failed for studio=%s", studio.pk)
    quota_message = ""
    if overage:
        dropped, cap = overage
        quota_message = (
            f"{dropped} report(s) were not registered: this organization is "
            f"limited to {cap} reports. Raise the quota, or remove reports "
            f"from the repository."
        )
    if rejected:
        # Put both causes before entry details: the maintenance outcome keeps
        # only the first 300 characters of this author-facing warning.
        message = f"{rejected} new item(s) were not registered: unsupported initial audience. "
        if quota_message:
            message += f"{quota_message} "
        message += " ".join(problems)
        if rejected > len(problems):
            message += f" ({rejected - len(problems)} more rejected entries.)"
        raise ReportAdmissionRejected(message)
    if quota_message:
        raise ReportQuotaExceeded(quota_message)
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

    from django.db import transaction
    from .source_summary import definitions_changed

    with transaction.atomic():
        now = dj_tz.now()
        seen: set[str] = set()
        for metric in result.metrics.values():
            seen.add(metric.name)
            dataset = result.datasets.get(metric.dataset)
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
                    "binding": {"dataset": metric.dataset,
                                "time_column": dataset.time_column if dataset else None,
                                "grain": dataset.grain if dataset else None,
                                "time_agg": metric.time_agg},
                    "present_in_scan": True,
                    "last_scanned_at": now,
                },
            )
        MetricDefinition.objects.filter(studio=studio, present_in_scan=True).exclude(
            name__in=seen
        ).update(present_in_scan=False, last_scanned_at=now)
        definitions_changed(studio.pk)
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


def _get_html_entry(report_output_dir: str) -> str:
    if not os.path.isdir(report_output_dir):
        return "index.html"
    if os.path.isfile(os.path.join(report_output_dir, "index.html")):
        return "index.html"
    for f in sorted(os.listdir(report_output_dir)):
        if f.endswith(".html"):
            return f
    return "index.html"
