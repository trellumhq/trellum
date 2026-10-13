"""Worker-produced artifact projections; request handlers consume DB rows only.

Generations fence I/O observations against concurrent runs/expiry/scan updates.
No transaction or row lock spans artifact I/O. Failure keeps historical claims;
coverage is explicit and must never be interpreted as a complete zero.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core import storage
from .models import MetricCatalogState, Report, ReportMetricClaim, ReportMetricPreview, ReportSourceSummary

META_BYTES = 2 * 1024 * 1024
PREVIEW_BYTES = 32 * 1024 * 1024
LEASE_SECONDS = 180
RECHECK_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600


def _revision(studio_id, *, definitions=False):
    state, _ = MetricCatalogState.objects.get_or_create(studio_id=studio_id)
    field = "definition_revision" if definitions else "summary_revision"
    MetricCatalogState.objects.filter(pk=state.pk).update(**{field: F(field) + 1})


def definitions_changed(studio_id):
    with transaction.atomic():
        _revision(studio_id, definitions=True)


def begin_attempt(report_id, operation):
    """Fence before subprocess can mutate a local serving directory."""
    with transaction.atomic():
        summary, _ = ReportSourceSummary.objects.get_or_create(report_id=report_id)
        summary = ReportSourceSummary.objects.select_for_update().get(pk=summary.pk)
        if summary.state == "expiring":
            raise RuntimeError("report output expiry in progress")
        summary.generation += 1
        summary.operation = str(operation)
        summary.attempt_at = timezone.now()
        summary.attempt_status = "running"
        summary.state = "pending"
        summary.lease_token = ""
        summary.lease_until = None
        summary.next_check_at = timezone.now()
        summary.save()
        _revision(summary.report.studio_id)


def finish_attempt(report_id, operation, status):
    """Enqueue projection without storage I/O on the executor tick."""
    with transaction.atomic():
        summary = ReportSourceSummary.objects.select_for_update().filter(report_id=report_id).first()
        if summary is None or summary.operation != str(operation):
            return False
        summary.generation += 1
        summary.attempt_at = timezone.now()
        summary.attempt_status = status
        summary.state = "pending" if status == "success" or storage.is_remote() else "uncertain"
        summary.lease_token = ""
        summary.lease_until = None
        summary.next_check_at = timezone.now()
        summary.save()
        _revision(summary.report.studio_id)
        return True


def invalidate_reports(report_ids, state):
    """Call in the same transaction as authoritative expiry/removal markers."""
    rows = list(Report.objects.filter(pk__in=report_ids).values_list("pk", "studio_id"))
    for pk, _ in rows:
        ReportSourceSummary.objects.get_or_create(report_id=pk)
    ReportSourceSummary.objects.filter(report_id__in=report_ids).update(
        state=state, generation=F("generation") + 1, lease_token="", lease_until=None,
        next_check_at=timezone.now(), quality="unknown", preview_state="unavailable",
    )
    # Historical rows remain readable as history, never part of live counts.
    for studio_id in {studio for _, studio in rows}:
        _revision(studio_id)


def acquire(report_id):
    """Short lease transaction. Token and generation must match at publication."""
    now = timezone.now()
    with transaction.atomic():
        summary, _ = ReportSourceSummary.objects.get_or_create(report_id=report_id, defaults={"next_check_at": now})
        summary = ReportSourceSummary.objects.select_for_update(skip_locked=True).filter(pk=summary.pk).first()
        if summary is None or summary.next_check_at > now or (summary.lease_until and summary.lease_until > now):
            return None
        # Active runs own the directory/publish fence. Restart repair waits for
        # normal worker stale-run recovery rather than racing unfinished builds.
        from apps.runner.models import Run
        active_statuses = (Run.STARTING, Run.RUNNING) if summary.operation.startswith("expiry:") else Run.ACTIVE_STATUSES
        if Run.objects.filter(report_id=report_id, status__in=active_statuses).exists():
            return None
        report = Report.objects.select_related("studio__org").get(pk=report_id)
        if not report.present_in_scan or report.data_expired_at:
            summary.state = "removed" if not report.present_in_scan else "expired"
            summary.quality = "unknown"
            summary.next_check_at = now + timedelta(seconds=RECHECK_SECONDS)
            summary.save()
            return None
        summary.lease_token = uuid.uuid4().hex
        summary.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        summary.save(update_fields=["lease_token", "lease_until"])
        return report, summary.generation, summary.lease_token


def _parse_time(value):
    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed
    except (ValueError, TypeError):
        return None


def inspect_source(report, summary):
    """One report inspection, bounded bytes; pointer before/after verifies identity."""
    from trellum.meta import normalize_metrics_used

    remote = storage.is_remote()
    build = storage.summary_build_identity(report.studio, report.slug) if remote else ""
    raw = storage.read_summary_artifact(report.studio, report.slug, "_meta.json", build=build, max_bytes=META_BYTES) if not remote or build else None
    if remote and build and raw is None:
        raise FileNotFoundError("referenced immutable metadata missing")
    if raw == b"":
        raise ValueError("empty metadata artifact")
    used = len(raw or b"")
    attempt_raw = storage.read_summary_artifact(report.studio, report.slug, "_meta.json", max_bytes=META_BYTES, mutable_status=True) if remote else raw
    if remote:
        used += len(attempt_raw or b"")
    attempt_meta = json.loads(attempt_raw) if attempt_raw else {}
    if not isinstance(attempt_meta, dict):
        raise ValueError("invalid attempt metadata object")
    meta = json.loads(raw) if raw else {}
    if not isinstance(meta, dict):
        raise ValueError("invalid metadata object")
    entries = meta.get("metrics_used") or []
    if not isinstance(entries, list):
        raise ValueError("invalid metric claim list")
    for entry in entries:
        if not isinstance(entry, (str, dict)) or not (entry if isinstance(entry, str) else entry.get("id")):
            raise ValueError("invalid metric claim")
    claims = normalize_metrics_used(entries)
    # Preserve the framework normalizer: richer v2 entries win over v1 IDs;
    # repeated v2 IDs use the documented last-entry semantics. Never truncate.
    unique = {}
    for claim in claims:
        if claim["version"] is not None and (not isinstance(claim["version"], int) or isinstance(claim["version"], bool) or claim["version"] < 1):
            raise ValueError("invalid metric version")
        if claim["definition_hash"] is not None and (not isinstance(claim["definition_hash"], str) or len(claim["definition_hash"]) > 64):
            raise ValueError("invalid metric hash")
        if len(claim["id"]) > 200:
            raise ValueError("metric identity exceeds index limit")
        unique[claim["id"]] = claim
    digest = hashlib.sha256(json.dumps(sorted(unique.values(), key=lambda c: c["id"]), sort_keys=True).encode()).hexdigest()
    stamp = _parse_time(meta.get("last_run"))
    status = str(meta.get("last_status") or "")
    previews = {}
    preview_state = "not-applicable"
    generated = report.slug == "metrics" and "metrics" in (report.tags or [])
    if generated and raw:
        preview_state = "missing"
        payload = None
        try:
            payload = storage.read_summary_artifact(report.studio, report.slug, "data.json", build=build, max_bytes=PREVIEW_BYTES)
            used += len(payload or b"")
            if payload:
                # Existing pure series routine bounds each output to60points.
                from .metrics_catalog import _spark_series
                previews = _spark_series(json.loads(payload))
                if any(len(name) > 200 or len(points.split()) > 60 for name, points in previews.items()):
                    raise ValueError("preview projection limit exceeded")
                preview_state = "ready"
        except Exception:
            if payload is None:
                used += PREVIEW_BYTES + 1
            previews = {}
            preview_state = "invalid-or-oversized"
    if remote:
        if storage.summary_build_identity(report.studio, report.slug) != build:
            raise ValueError("serving pointer changed during inspection")
        identity, quality, state = build, "verified", "ready" if build else "missing"
    else:
        # A failed local run can overwrite assets while retaining previous
        # claims. Observation alone is not proof of a successful served build.
        failed_attempt = summary.attempt_status not in ("", "success")
        newer_success = status == "success" and stamp and summary.attempt_at and stamp > summary.attempt_at
        uncertain = status not in ("", "success") or (failed_attempt and not newer_success)
        identity = f"observed:{hashlib.sha256(raw or b'').hexdigest()}"
        quality = "observed"
        state = "uncertain" if uncertain else "ready" if raw else "missing"
        from apps.runner.models import Run
        try:
            operation_id = uuid.UUID(summary.operation)
        except ValueError:
            operation_id = None
        known_run = Run.objects.filter(pk=operation_id, status=Run.SUCCESS).first() if operation_id else None
        if known_run and stamp and known_run.started_at and known_run.finished_at and known_run.started_at <= stamp <= known_run.finished_at and not uncertain:
            identity = f"run:{summary.operation}:{digest}"
            quality = "verified"
        # Catch a concurrent external write to local metadata before commit.
        again = storage.read_summary_artifact(report.studio, report.slug, "_meta.json", max_bytes=META_BYTES)
        used += len(again or b"")
        if again != raw:
            raise ValueError("local metadata changed during inspection")
    if summary.operation.startswith("expiry:"):
        state = "expired" if raw is None else "uncertain"
    return dict(attempt_stamp=_parse_time(attempt_meta.get("last_run")), attempt_status=str(attempt_meta.get("last_status") or ""), identity=identity, quality=quality, state=state, digest=digest,
                stamp=stamp, status=status, claims=list(unique.values()), previews=previews,
                preview_state=preview_state, bytes=used)


def reconcile(report_id):
    lease = acquire(report_id)
    if lease is None:
        return {"status": "ineligible", "bytes": 0}
    report, generation, token = lease
    summary = ReportSourceSummary.objects.get(report_id=report_id)
    try:
        result = inspect_source(report, summary)
    except Exception as exc:
        # Never turn store outage, malformed or oversize data into empty claims.
        with transaction.atomic():
            row = ReportSourceSummary.objects.select_for_update().get(report_id=report_id)
            if row.generation == generation and row.lease_token == token:
                row.failures += 1
                row.error_code = type(exc).__name__
                row.state = "unavailable"
                row.lease_token = ""
                row.lease_until = None
                row.next_check_at = timezone.now() + timedelta(seconds=min(MAX_BACKOFF_SECONDS, 5 * 2 ** min(row.failures, 10)))
                row.save()
                _revision(report.studio_id)
        return {"status": "unavailable", "bytes": 2 * META_BYTES + PREVIEW_BYTES + 8192, "byte_measurement": "conservative-upper-bound", "error": type(exc).__name__}
    with transaction.atomic():
        row = ReportSourceSummary.objects.select_for_update().get(report_id=report_id)
        report.refresh_from_db(fields=["present_in_scan", "data_expired_at"])
        if row.generation != generation or row.lease_token != token or not report.present_in_scan or report.data_expired_at:
            return {"status": "superseded", "bytes": result["bytes"]}
        if result["state"] not in ("uncertain", "expired"):
            row.claims.all().delete()
            ReportMetricClaim.objects.bulk_create([ReportMetricClaim(
                summary=row, metric_name=c["id"], definition_hash=c["definition_hash"], version=c["version"]
            ) for c in result["claims"]])
            row.previews.all().delete()
            ReportMetricPreview.objects.bulk_create([ReportMetricPreview(summary=row, metric_name=name, points=points)
                for name, points in result["previews"].items()])
            row.source_identity = result["identity"]
            row.claim_digest = result["digest"]
            row.served_at = result["stamp"]
            row.quality = result["quality"]
            row.preview_state = result["preview_state"]
        row.backend = storage.backend_name()
        row.state = result["state"]
        row.observed_at = timezone.now()
        if result["attempt_stamp"] and (not row.attempt_at or result["attempt_stamp"] > row.attempt_at):
            row.attempt_at = result["attempt_stamp"]
            row.attempt_status = result["attempt_status"]
        if result["state"] == "expired":
            Report.objects.filter(pk=report_id).update(data_expired_at=timezone.now())
            row.quality = "unknown"
        row.failures = 0
        row.error_code = ""
        row.lease_token = ""
        row.lease_until = None
        row.next_check_at = timezone.now() + timedelta(seconds=RECHECK_SECONDS)
        row.save()
        _revision(report.studio_id)
    return {"status": result["state"], "bytes": result["bytes"]}


def begin_expiry(report_id):
    from apps.runner.models import Run

    with transaction.atomic():
        row, _ = ReportSourceSummary.objects.get_or_create(report_id=report_id)
        row = ReportSourceSummary.objects.select_for_update().get(pk=row.pk)
        if Run.objects.filter(report_id=report_id, status__in=Run.ACTIVE_STATUSES).exists():
            return None
        row.generation += 1
        row.operation = "expiry:" + uuid.uuid4().hex
        row.state = "expiring"
        row.lease_token = ""
        row.lease_until = timezone.now() + timedelta(seconds=LEASE_SECONDS)
        row.save()
        _revision(row.report.studio_id)
        return row.operation


def finish_expiry(report_id, operation, *, expired_at=None):
    with transaction.atomic():
        row = ReportSourceSummary.objects.select_for_update().get(report_id=report_id)
        if row.operation != operation:
            return False
        row.state = "expired" if expired_at else "uncertain"
        row.quality = "unknown"
        row.generation += 1
        if expired_at:
            row.operation = ""
        row.lease_until = None
        row.next_check_at = timezone.now()
        row.save()
        if expired_at:
            Report.objects.filter(pk=report_id).update(data_expired_at=expired_at)
        _revision(row.report.studio_id)
        return True
