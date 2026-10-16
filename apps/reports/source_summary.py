"""Worker-produced artifact projections; request handlers consume DB rows only.

Generations fence I/O observations against concurrent runs/expiry/scan updates.
No transaction or row lock spans artifact I/O. Failure keeps historical claims;
coverage is explicit and must never be interpreted as a complete zero.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core import storage
from .models import MetricCatalogState, Report, ReportMetricClaim, ReportMetricPreview, ReportSourceSummary, ReportExperimentSummary

logger = logging.getLogger(__name__)

META_BYTES = 2 * 1024 * 1024
PREVIEW_BYTES = 32 * 1024 * 1024
LEASE_SECONDS = 180
RECHECK_SECONDS = 60
MAX_BACKOFF_SECONDS = 3600
# Every reader treats an observation older than five minutes as stale and shows
# "unknown" (catalog, operations, analytics, metrics). The idle back-off must
# therefore stay well inside that window: an unchanged, healthy source is
# re-observed at least this often, so it never ages into "unknown" while the
# worker is running.
MAX_IDLE_SECONDS = 180
# A file written within this many seconds of the stamp may still be rewritten
# inside the same timestamp tick, so a matching stamp proves nothing yet.
STAMP_RACY_SECONDS = 2.0
INSPECTION_BYTES = 62 * 1024 * 1024 + 128 * 1024


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
        summary.idle_checks = 0
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
        summary.idle_checks = 0
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
        next_check_at=timezone.now(), idle_checks=0, quality="unknown", preview_state="unavailable",
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
        # Not due yet, unless a healthy schedule was written under an older,
        # longer idle cap (see eligible_reports).
        scheduled_later = summary is not None and summary.next_check_at > now and not (
            summary.failures == 0 and summary.next_check_at > now + timedelta(seconds=MAX_IDLE_SECONDS))
        if summary is None or scheduled_later or (summary.lease_until and summary.lease_until > now):
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
    budget = storage.SummaryReadBudget(INSPECTION_BYTES)
    # Pointer bodies are bounded independently; reservation includes both.
    if remote:
        budget.charge(8194, 8194)
    def read(name, **kwargs):
        return storage.read_summary_artifact(report.studio, report.slug, name, budget=budget, **kwargs)
    main = [False, None, None]
    def main_payload():
        if not main[0]:
            main[0] = True
            if remote and not build:
                # Verified absent pointer means unbuilt, not a store outage.
                # Never inspect unpublished uploads or a mutable data path.
                return None
            try:
                from .report_experiment_projection import AB_BYTES, has_declarations
                has_ab = has_declarations(report.config)
                cap = PREVIEW_BYTES if generated else AB_BYTES if has_ab else PREVIEW_BYTES
                before = budget.used
                main[1] = read('data.json', build=build, max_bytes=cap)
                main_payload.cost = budget.used - before
                main_payload.parsed = json.loads(main[1]) if main[1] else None
            except Exception as exc:
                main[2] = exc
        if main[2]:
            raise main[2]
        return main[1]
    build = storage.summary_build_identity(report.studio, report.slug) if remote else ""
    if len(build) > 300:
        raise ValueError("serving identity exceeds index limit")
    raw = read("_meta.json", build=build, max_bytes=META_BYTES) if not remote or build else None
    if remote and build and raw is None:
        raise FileNotFoundError("referenced immutable metadata missing")
    if raw == b"":
        raise ValueError("empty metadata artifact")
    attempt_raw = read("_meta.json", max_bytes=META_BYTES, mutable_status=True) if remote else raw
    attempt_meta = json.loads(attempt_raw) if attempt_raw else {}
    if not isinstance(attempt_meta, dict):
        raise ValueError("invalid attempt metadata object")
    meta = json.loads(raw) if raw else {}
    if not isinstance(meta, dict):
        raise ValueError("invalid metadata object")
    # Legacy artifacts may omit claims; explicitly present malformed values
    # must not become a verified empty catalog through truthiness coercion.
    entries = meta["metrics_used"] if "metrics_used" in meta else []
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
        if claim["version"] is not None and (not isinstance(claim["version"], int) or isinstance(claim["version"], bool) or not 1 <= claim["version"] <= 2147483647):
            raise ValueError("invalid metric version")
        if claim["definition_hash"] is not None and (not isinstance(claim["definition_hash"], str) or len(claim["definition_hash"]) > 64):
            raise ValueError("invalid metric hash")
        if len(claim["id"]) > 200:
            raise ValueError("metric identity exceeds index limit")
        unique[claim["id"]] = claim
    digest = hashlib.sha256(json.dumps(sorted(unique.values(), key=lambda c: c["id"]), sort_keys=True).encode()).hexdigest()
    stamp = _parse_time(meta.get("last_run"))
    status = str(meta.get("last_status") or "")
    if len(status) > 32 or len(str(attempt_meta.get("last_status") or "")) > 32:
        raise ValueError("attempt status exceeds index limit")
    previews = {}
    preview_state = "not-applicable"
    generated = report.slug == "metrics" and "metrics" in (report.tags or [])
    workspace_version = None
    if generated and raw:
        workspace_version = 0
        if "metrics_workspace" in meta:
            marker = meta["metrics_workspace"]
            if not isinstance(marker, dict):
                raise ValueError("invalid metrics workspace marker")
            workspace_version = marker.get("version")
            if (not isinstance(workspace_version, int) or isinstance(workspace_version, bool)
                    or not 0 <= workspace_version <= 32767):
                raise ValueError("invalid metrics workspace version")
    reuse_previews = remote and build == summary.source_identity and digest == summary.claim_digest and summary.preview_state == "ready"
    if generated and raw and reuse_previews:
        previews = None
        preview_state = summary.preview_state
    if generated and raw and not reuse_previews:
        preview_state = "missing"
        payload = None
        try:
            payload = main_payload()
            if payload:
                # Existing pure series routine bounds each output to60points.
                from .metrics_catalog import _spark_series
                previews = _spark_series(main_payload.parsed)
                if any(len(name) > 200 or len(points.split()) > 60 for name, points in previews.items()):
                    raise ValueError("preview projection limit exceeded")
                preview_state = "ready"
        except Exception:
            previews = {}
            preview_state = "invalid-or-oversized"
    extension = inspect_extensions(report, meta, attempt_meta, build, main_payload, read, budget)
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
        again = read("_meta.json", max_bytes=META_BYTES)
        if again != raw:
            raise ValueError("local metadata changed during inspection")
    if summary.operation.startswith("expiry:"):
        state = "expired" if raw is None else "uncertain"
    return dict(attempt_stamp=_parse_time(attempt_meta.get("last_run")), attempt_status=str(attempt_meta.get("last_status") or ""), identity=identity, quality=quality, state=state, digest=digest,
                stamp=stamp, status=status, claims=list(unique.values()), previews=previews,
                preview_state=preview_state, workspace_version=workspace_version, extension=extension, bytes=budget.used, encoded_bytes=budget.encoded, decoded_bytes=budget.decoded)


def _public_identity(row):
    return (row.state, row.quality, row.source_identity, row.claim_digest,
            row.served_at, row.attempt_at, row.attempt_status, row.preview_state, row.backend,
            row.workspace_version, row.runtime_state, row.runtime, row.has_output, row.html_entry,
            row.has_live_queries, row.output_digest, row.attempt_error, row.attempt_blocked_by,
            row.experiment_state, row.declaration_digest)


def _change_stamp(report, summary):
    """``(digest, taken, newest_mtime, declaration)`` for an unchanged-source check, or None.

    Everything an inspection reads is identified cheaply (stat, or ETag on remote
    storage) together with what else decides its outcome: the generation, the
    backend, the report's tags and A/B declarations. Local A/B results read scope
    files whose names only the payload knows, so those are always read in full.
    """
    from .report_experiment_projection import declaration_identity, has_declarations

    if not storage.is_remote() and has_declarations(report.config):
        return None
    taken = time.time()
    try:
        stamp = storage.summary_change_stamp(report.studio, report.slug)
    except Exception:  # noqa: BLE001 - a failing probe just means a full inspection
        return None
    if stamp is None:
        return None
    declaration = declaration_identity(report.config)
    digest = hashlib.sha256(json.dumps(
        [storage.backend_name(), report.slug, sorted(report.tags or []), declaration,
         summary.generation, stamp[0]]).encode()).hexdigest()
    return digest, taken, stamp[1], declaration


def _unchanged(summary, stamp):
    digest, taken, newest, _ = stamp
    stored, _, when = (summary.change_stamp or "").partition(":")
    try:
        stored_taken = float(when)
    except ValueError:
        return False
    return (summary.state in ("ready", "missing") and stored == digest
            and (newest is None or newest < stored_taken - STAMP_RACY_SECONDS))


def _confirm_unchanged(report_id, report, generation, token, declaration):
    """Nothing the summary reads has changed: renew its freshness without reading it."""
    with transaction.atomic():
        row = ReportSourceSummary.objects.select_for_update().get(report_id=report_id)
        report.refresh_from_db(fields=["present_in_scan", "data_expired_at", "config"])
        from .report_experiment_projection import declaration_identity

        if (row.generation != generation or row.lease_token != token or not report.present_in_scan
                or report.data_expired_at or declaration_identity(report.config) != declaration):
            return {"status": "superseded", "bytes": 0}
        now = timezone.now()
        row.observed_at = now
        row.failures = 0
        row.error_code = ""
        row.lease_token = ""
        row.lease_until = None
        row.idle_checks = min(row.idle_checks + 1, 12)
        row.next_check_at = now + timedelta(seconds=min(MAX_IDLE_SECONDS, RECHECK_SECONDS * 2 ** row.idle_checks))
        row.save(update_fields=["observed_at", "failures", "error_code", "lease_token", "lease_until",
                                "idle_checks", "next_check_at"])
        return {"status": row.state, "bytes": 0, "encoded_bytes": 0, "decoded_bytes": 0,
                "projection_unavailable": False, "unchanged": True}


def reconcile(report_id):
    lease = acquire(report_id)
    if lease is None:
        return {"status": "ineligible", "bytes": 0}
    return _reconcile_leased(report_id, *lease)


def _reconcile_leased(report_id, report, generation, token):
    summary = ReportSourceSummary.objects.get(report_id=report_id)
    stamp = _change_stamp(report, summary)
    if stamp is not None and _unchanged(summary, stamp):
        return _confirm_unchanged(report_id, report, generation, token, stamp[3])
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
        return {"status": "unavailable", "bytes": INSPECTION_BYTES, "byte_measurement": "conservative-upper-bound", "error": type(exc).__name__}
    with transaction.atomic():
        row = ReportSourceSummary.objects.select_for_update().get(report_id=report_id)
        report.refresh_from_db(fields=["present_in_scan", "data_expired_at", "config"])
        if row.generation != generation or row.lease_token != token or not report.present_in_scan or report.data_expired_at:
            return {"status": "superseded", "bytes": result["bytes"]}
        from .report_experiment_projection import declaration_identity
        if declaration_identity(report.config) != result['extension']['declaration_digest']:
            return {"status": "superseded", "bytes": result["bytes"]}
        previous_identity = _public_identity(row)
        preview_changed = False
        if result["state"] not in ("uncertain", "expired"):
            if row.claim_digest != result["digest"]:
                row.claims.all().delete()
                ReportMetricClaim.objects.bulk_create([ReportMetricClaim(
                    summary=row, metric_name=c["id"], definition_hash=c["definition_hash"], version=c["version"]
                ) for c in result["claims"]])
            if result["previews"] is not None:
                existing_previews = dict(row.previews.values_list("metric_name", "points"))
                if existing_previews != result["previews"]:
                    preview_changed = True
                    row.previews.all().delete()
                    ReportMetricPreview.objects.bulk_create([ReportMetricPreview(summary=row, metric_name=name, points=points)
                        for name, points in result["previews"].items()])
            row.source_identity = result["identity"]
            row.claim_digest = result["digest"]
            row.served_at = result["stamp"]
            row.quality = result["quality"]
            row.preview_state = result["preview_state"]
            row.workspace_version = result["workspace_version"]
        extension = result['extension']
        if result['state'] in ('uncertain', 'expired'):
            # Declarations are current DB authority even when artifact results
            # are untrustworthy. Retain same-declaration historical results,
            # but never borrow their numbers for a changed declaration.
            from .report_experiment_projection import declared_rows, ProjectionOversized
            try:
                declared, _ = declared_rows(report.config)
                if row.declaration_digest != extension['declaration_digest'] or row.experiments.count() != len(declared):
                    row.experiments.all().delete()
                    for item in declared:
                        item['payload_state'] = 'unavailable' if result['state'] == 'uncertain' else 'expired'
                    ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=row, **item) for item in declared])
                    preview_changed = True
                row.experiment_state = 'unavailable' if result['state'] == 'uncertain' else 'expired'
            except ProjectionOversized:
                row.experiment_state = 'oversized'
                row.experiments.all().delete()
            row.declaration_digest = extension['declaration_digest']
            row.runtime_state = 'unavailable' if result['state'] == 'uncertain' else 'expired'
        if result['state'] not in ('uncertain', 'expired'):
            for field in ('runtime_state', 'experiment_state', 'declaration_digest'):
                setattr(row, field, extension[field])
            if extension['runtime_state'] == 'ready':
                for field in ('has_output', 'html_entry', 'has_live_queries', 'output_digest', 'runtime'):
                    setattr(row, field, extension[field])
            if extension['experiment_rows'] is not None:
                rows = [ReportExperimentSummary(summary=row, **r) for r in extension['experiment_rows']]
                fields = [f.name for f in ReportExperimentSummary._meta.fields if f.name not in ('id','summary')]
                old = list(row.experiments.order_by('ordinal').values(*fields))
                comparable = [{field:getattr(r,field) for field in fields} for r in rows]
                if old != comparable:
                    preview_changed = True
                    row.experiments.all().delete()
                    ReportExperimentSummary.objects.bulk_create(rows)
        row.attempt_error = extension['attempt_error']
        row.attempt_blocked_by = extension['attempt_blocked_by']
        row.schema_version = 2
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
        # Only a settled, fully readable observation can later be confirmed by
        # its stamp alone; anything else is read in full again.
        settled = (result["state"] in ("ready", "missing") and result["preview_state"] in ("ready", "not-applicable")
                   and "unavailable" not in (extension["runtime_state"], extension["experiment_state"]))
        row.change_stamp = f"{stamp[0]}:{stamp[1]:.3f}" if stamp is not None and settled else None
        changed = _public_identity(row) != previous_identity or preview_changed
        # Nothing new: back off (60 s, 120 s, ... capped) so an idle catalog
        # stops re-reading every artifact; any change or run resets to 0.
        row.idle_checks = 0 if changed else min(row.idle_checks + 1, 12)
        row.next_check_at = timezone.now() + timedelta(
            seconds=min(MAX_IDLE_SECONDS, RECHECK_SECONDS * 2 ** row.idle_checks))
        row.save()
        if changed:
            _revision(report.studio_id)
    return {"status": result["state"], "bytes": result["bytes"], "encoded_bytes": result["encoded_bytes"], "decoded_bytes": result["decoded_bytes"], "projection_unavailable": result['extension']['runtime_state'] == 'unavailable' or result['extension']['experiment_state'] == 'unavailable'}


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


def inspect_extensions(report, meta, attempt_meta, build, main_payload, read, budget):
    """Independent compact projections; errors never invalidate proven claims."""
    from .report_experiment_projection import (
        AB_BYTES, MAX_SCOPES, ProjectionOversized, declaration_identity, declarations, declared_rows,
        project_results, text,
    )
    digest = declaration_identity(report.config)
    try:
        entries, _ = declarations(report.config)
        oversized_declarations = False
    except ProjectionOversized:
        entries, oversized_declarations = [], True
    result = dict(runtime_state='pending', experiment_state='not-applicable', declaration_digest=digest,
                  experiment_rows=None, attempt_error='', attempt_blocked_by=[])
    try:
        result['attempt_error'] = str(attempt_meta.get('last_error') or '')[:500]
        blocked = attempt_meta.get('blocked_by') or []
        if not isinstance(blocked,list) or len(blocked)>256 or any(not isinstance(n,str) or len(n)>200 for n in blocked):
            raise ValueError('invalid blocked source names')
        result['attempt_blocked_by'] = blocked
    except Exception:
        result['attempt_error'] = ''
        result['attempt_blocked_by'] = []
    try:
        facts_before = budget.used
        facts = storage.summary_output_facts(report.studio, report.slug, build=build, budget=budget)
        runtime = {}
        validation = meta.get('validation')
        if isinstance(validation,dict) and isinstance(validation.get('summary'),dict):
            runtime['validation'] = {'summary': {key:value for key,value in validation['summary'].items()
                if key in ('pass','fail','warn','info','suppressed','total') and isinstance(value,int) and not isinstance(value,bool) and value>=0}}
        details = meta.get('details') or meta.get('health')
        if isinstance(details,dict) and isinstance(details.get('totals'),dict):
            # Numeric aggregate totals only: no datasets, paths, check messages,
            # query parameters, filter matrices or private source payloads.
            runtime['details'] = {'totals': {key:value for key,value in details['totals'].items()
                if key in ('datasource_count','chart_count','total_rows','total_size_bytes') and isinstance(value,(int,float)) and not isinstance(value,bool)
                and __import__('math').isfinite(value) and value>=0}}
        if isinstance(details,dict) and details.get('data_source') in ('mock','real') and 'details' in runtime:
            runtime['details']['data_source'] = details['data_source']
        runtime['framework_version'] = text(meta.get('framework_version'))
        if len(json.dumps(runtime).encode())>16*1024:
            raise ProjectionOversized('runtime projection exceeded')
        result.update(facts, runtime=runtime, runtime_state='ready')
    except Exception as exc:
        if budget.used == facts_before:
            budget.charge(96*1024,96*1024)
        result['runtime_state'] = 'oversized' if 'oversiz' in str(exc) or isinstance(exc,ProjectionOversized) else 'unavailable'
    if oversized_declarations:
        result.update(experiment_state='oversized', experiment_rows=[])
        return result
    if not entries:
        result['experiment_rows'] = []
        return result
    try:
        declared, _ = declared_rows(report.config)
        raw = main_payload()
        if not raw:
            for row in declared:
                row['payload_state'] = 'missing'
            result.update(experiment_state='missing', experiment_rows=declared)
            return result
        if max(len(raw),getattr(main_payload,'cost',len(raw)))>AB_BYTES:
            raise ProjectionOversized('AB main payload oversized')
        main = main_payload.parsed
        if not isinstance(main,dict):
            raise ValueError('invalid AB payload')
        observed = [('data.json', raw)]
        from .overviews.experiments import _find_ab_components
        components = _find_ab_components(main.get('components'))
        if not components:
            scopes = main.get('_scopeFiles') or {}
            if not isinstance(scopes,dict):
                raise ValueError('invalid scope map')
            if len(scopes)>MAX_SCOPES:
                raise ProjectionOversized('scope count exceeded')
            total = max(len(raw), getattr(main_payload,'cost',len(raw)))
            seen = {'data.json'}
            for name in scopes.values():
                if not isinstance(name,str):
                    continue
                if name in seen:
                    continue
                seen.add(name)
                if total>=AB_BYTES:
                    raise ProjectionOversized('AB aggregate byte limit exceeded')
                before = budget.used
                scope_raw = read(name, build=build, max_bytes=AB_BYTES-total, scope=True)
                total += budget.used-before
                if scope_raw:
                    observed.append((name,scope_raw))
                    try:
                        data = json.loads(scope_raw)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(data,dict):
                        continue
                    components = _find_ab_components(data.get('components'))
                    if components:
                        break
        rows, _ = project_results(report.config, main, components)
        if not storage.is_remote():
            # Re-read all participating local content, never a path/mtime cache.
            rechecked = 0
            for name, old in observed:
                before = budget.used
                again = read(name, build=build, max_bytes=AB_BYTES-rechecked, scope=name!='data.json')
                rechecked += budget.used-before
                if again!=old:
                    raise ValueError('local AB source changed during inspection')
        result.update(experiment_state='ready', experiment_rows=rows)
    except Exception as exc:
        result['experiment_state'] = 'oversized' if isinstance(exc,ProjectionOversized) or 'byte limit' in str(exc) else 'unavailable'
        # Replace declarations without accepting a success prefix; historical
        # results remain gated unavailable when declarations themselves invalid.
        try:
            rows,_ = declared_rows(report.config)
            for row in rows:
                row['payload_state'] = result['experiment_state']
            result['experiment_rows'] = rows
        except ProjectionOversized:
            result['experiment_rows'] = []
    return result
