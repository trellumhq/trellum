"""Page-scoped catalog indicators; no artifact or network work."""
from datetime import timedelta
from types import SimpleNamespace

from django.db.models import Case, Count, Exists, F, Func, IntegerField, JSONField, OuterRef, Q, Sum, TextField, Value, When
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Cast, Coalesce, JSONObject, Substr
from django.utils import timezone

from apps.core.report_access import visible_reports
from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import SourceState, _state, blocked_names, build_error, referenced_names
from apps.orgs.models import PermissionGroupMembership
from apps.runner.models import Run
from apps.studios.models import StudioMembership
from .catalog import FRESH_SECONDS, bounded_payload, check_catalog_context
from .models import EmailSchedule, Report, ReportSourceSummary, ReportViewDaily, ReportViewEvent, live_query_rate_limit


class ByteLength(Func):
    function = "octet_length"
    output_field = IntegerField()


def authorized_ids(request, report_ids):
    if not isinstance(report_ids, (list, tuple)) or len(report_ids) > 64 or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in report_ids):
        raise ValueError("Invalid report identities")
    check_catalog_context(request)
    relation = visible_reports(request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, pk__in=set(report_ids)), request=request)
    return relation if request.user.is_active else relation.none()


def scoped_subscription_counts(request, reports):
    """Count a matching schedule once, regardless of matching audience channels."""
    if not request.user.is_active:
        return {}
    role = StudioMembership.objects.filter(studio=request.studio, user=request.user).values_list("role", flat=True).first()
    group_members = PermissionGroupMembership.objects.filter(user=request.user, group__org=request.org).values("group_id")
    direct = EmailSchedule.recipients.through.objects.filter(emailschedule_id=OuterRef("pk"), user_id=request.user.pk)
    groups = EmailSchedule.recipient_groups.through.objects.filter(emailschedule_id=OuterRef("pk"), permissiongroup_id__in=group_members)
    eligible = Q(_direct=True) | Q(_group=True) | Q(recipient_roles__contains=["everyone"])
    if role == "admin":
        eligible |= Q(recipient_roles__contains=["admins"])
    elif role == "developer":
        eligible |= Q(recipient_roles__contains=["developers"])
    schedules = EmailSchedule.objects.filter(report_id__in=reports.order_by().values("pk"), enabled=True).alias(_direct=Exists(direct), _group=Exists(groups)).filter(eligible)
    return {r["report_id"]: r["count"] for r in schedules.order_by().values("report_id").annotate(count=Count("pk"))}


def _references(reports):
    refs = KeyTransform("data_sources", "config")
    rows = reports.annotate(ref_bytes=Coalesce(ByteLength(Cast(refs, TextField())), Value(0))).annotate(refs=Case(When(ref_bytes__lte=16384, then=refs), default=Value(None), output_field=JSONField())).values("pk", "slug", "kind", "disabled", "refs", "ref_bytes", "last_built_at")
    out, names = {}, set()
    for row in rows:
        raw = row.pop("refs")
        state = "ready"
        try:
            if row["ref_bytes"] > 16384:
                state = "oversized"
            elif raw is not None and (not isinstance(raw, list) or len(raw) > 32 or any(not isinstance(v, (str, dict)) for v in raw)):
                state = "oversized" if isinstance(raw, list) and len(raw) > 32 else "unavailable"
            refs = referenced_names({"data_sources": raw}) if state == "ready" else []
            if any(len(n) > 200 for n in refs):
                state, refs = "oversized", []
        except (AttributeError, TypeError, ValueError):
            state, refs = "unavailable", []
        row.update(references=refs, source_status=state)
        out[row["pk"]] = row
        names.update(refs)
    if len(names) > 128:
        for row in out.values():
            row["source_status"] = "oversized"
        names = set()
    return out, names


def _sources(request, names):
    if not names:
        return {}, set()
    declarations = RepoDataSource.objects.filter(studio=request.studio, present=True, name__in=names)
    bindings = DataSource.objects.filter(Q(org=request.org) | Q(studio=request.studio), name__in=names)
    declarations = declarations.annotate(payload_size=ByteLength(Cast("config", TextField())))
    bindings = bindings.annotate(payload_size=ByteLength(Cast("config", TextField()))+Coalesce(ByteLength("credentials"), Value(0)))
    # Admit encrypted columns before the model field's from_db_value decrypts.
    d_sizes = list(declarations.values("pk", "name", "payload_size")[:129])
    b_sizes = list(bindings.values("pk", "name", "payload_size")[:257])
    if len(d_sizes) > 128 or len(b_sizes) > 256:
        return {}, set(names)
    bad = {r["name"] for r in d_sizes+b_sizes if r["payload_size"] > 65536}
    ds = {}
    # Construct narrow declaration objects so arbitrarily long diagnostic text
    # cannot be loaded as a deferred field during _state.
    for row in declarations.filter(pk__in=[r["pk"] for r in d_sizes if r["name"] not in bad]).annotate(error_preview=Substr("file_error", 1, 600)).values("name", "type", "config", "error_preview"):
        ds[row["name"]] = SimpleNamespace(name=row["name"], type=row["type"], config=row["config"], file_error=row["error_preview"])
    bs = {}
    for binding in bindings.filter(pk__in=[r["pk"] for r in b_sizes if r["name"] not in bad]).only("name", "type", "config", "credentials", "studio_id", "org_id", "last_check_at", "last_check_ok", "awaiting_first_sync").annotate(error_preview=Substr("last_check_error", 1, 600)).order_by("studio_id"):
        # Scoped rows shadow shared rows, independent of NULL sort convention.
        binding.last_check_error = binding.error_preview
        if binding.name not in bs or binding.studio_id is not None:
            bs[binding.name] = binding
    states = {}
    for name in names-bad:
        try:
            states[name] = _state(name, ds.get(name), bs.get(name), [])
        except (AttributeError, TypeError, ValueError):
            bad.add(name)
    return states, bad


def catalog_indicators_payload(request, report_ids, *, now=None):
    now = now or timezone.now()
    relation = authorized_ids(request, report_ids)
    rows, names = _references(relation)
    if not rows:
        return dict(version=1, reports={})
    states, bad = _sources(request, names)
    ids = list(rows)
    summary_fields = ("report_id", "schema_version", "state", "runtime_state", "quality", "observed_at", "attempt_at", "attempt_status", "attempt_error", "blocked", "blocked_bytes", "has_output", "has_live_queries", "html_entry", "runtime_projection")
    projection = JSONObject(validation=KeyTransform("summary", KeyTransform("validation", "runtime")), totals=KeyTransform("totals", KeyTransform("details", "runtime")), framework_version=KeyTransform("framework_version", "runtime"), data_source=KeyTransform("data_source", KeyTransform("details", "runtime")))
    summary_rows = ReportSourceSummary.objects.filter(report_id__in=ids).annotate(blocked_bytes=ByteLength(Cast("attempt_blocked_by", TextField())), runtime_bytes=ByteLength(Cast("runtime", TextField()))).annotate(blocked=Case(When(blocked_bytes__lte=16384, then=F("attempt_blocked_by")), default=Value(None), output_field=JSONField()), runtime_projection=Case(When(runtime_bytes__lte=16384, then=projection), default=Value(None), output_field=JSONField()))
    summaries = {s["report_id"]: s for s in summary_rows.values(*summary_fields)}
    latest = Run.objects.filter(studio=request.studio, report_id__in=ids, slug__in=[r["slug"] for r in rows.values()]).annotate(tail_bytes=ByteLength("stderr_tail")).annotate(tail=Case(When(tail_bytes__lte=4096, then=F("stderr_tail")), default=Value(""), output_field=TextField())).order_by("report_id", "-created_at", "-pk").distinct("report_id").values("report_id", "status", "created_at", "tail", "tail_bytes")
    runs = {r["report_id"]: SimpleNamespace(status=r["status"], stderr_tail=r["tail"], created_at=r["created_at"], incomplete=r["tail_bytes"] > 4096) for r in latest}
    subscriptions = scoped_subscription_counts(request, relation)
    daily = {r["report_id"]: r["count"] for r in ReportViewDaily.objects.filter(report_id__in=ids, date__gte=(now-timedelta(days=30)).date()).values("report_id").annotate(count=Sum("views"))}
    events = ReportViewEvent.objects.filter(report_id=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
    from django.db.models import Subquery
    last = {r["pk"]: r["last"] for r in relation.annotate(last=Subquery(events)).values("pk", "last")}
    result = {}
    live_enabled = live_query_rate_limit(request.org) > 0
    for report_id, row in rows.items():
        summary = summaries.get(report_id)
        observed_attempt = bool(summary and summary["observed_at"] and now-timedelta(seconds=FRESH_SECONDS) <= summary["observed_at"] <= now and (summary["state"] in ("ready", "missing") or (summary["state"] == "uncertain" and summary["attempt_status"])) and (summary["attempt_at"] is None or summary["attempt_at"] <= now))
        worker_attempt = bool(summary and summary["attempt_at"] and now-timedelta(seconds=FRESH_SECONDS) <= summary["attempt_at"] <= now and summary["attempt_status"])
        fresh = bool(summary and summary["schema_version"] == 2 and (observed_attempt or worker_attempt))
        output_observed = bool(summary and observed_attempt and summary["schema_version"] == 2 and summary["quality"] in ("observed", "verified") and summary["state"] in ("ready", "missing"))
        output_known = bool(output_observed and summary["runtime_state"] == "ready" and summary["has_output"] is not None)
        observation_state = summary["state"] if summary else "pending"
        if summary and summary["schema_version"] != 2:
            observation_state = "unsupported"
        elif observation_state in ("ready", "missing") and not output_observed:
            observation_state = "stale"
        blocked_projection = summary["blocked"] if summary else []
        invalid_blocked = not isinstance(blocked_projection, list) or len(blocked_projection) > 256 or any(not isinstance(n, str) or len(n) > 200 for n in blocked_projection)
        meta = {"last_status": summary["attempt_status"], "last_error": summary["attempt_error"], "blocked_by": blocked_projection if not invalid_blocked else []} if fresh else {}
        run = runs.get(report_id)
        source_status = row["source_status"]
        if source_status == "ready" and (set(row["references"]) & bad):
            source_status = "unavailable"
        if source_status == "ready" and row["references"] and invalid_blocked:
            source_status = "unavailable"
        referenced = [states[n] for n in row["references"] if n in states]
        holding = [s.name for s in referenced if s.blocking] if not row["disabled"] else []
        blocked = [n for n in blocked_names(meta, run) if n in holding]
        failing = next((s for s in referenced if s.state == SourceState.FAILING), None)
        if source_status == "ready" and row["references"] and (not fresh or (run and run.incomplete)):
            source_status = "unavailable"
        result[report_id] = dict(slug=row["slug"], source_status=source_status, waiting=bool(blocked) if source_status == "ready" else None, blocked_by=blocked, failing_source={"name": failing.name, "since": failing.last_check_at.isoformat() if failing.last_check_at else None} if failing else None, last_error=build_error(meta, run)[:500] if fresh or run else None, build_status=run.status if run else summary["attempt_status"] if fresh else None, subscriptions={"state": "ready", "count": subscriptions.get(report_id, 0)}, views_30d=daily.get(report_id, 0), last_viewed=last[report_id].isoformat() if last[report_id] else None, observation_state=observation_state, output_state="known" if output_known else observation_state if not output_observed else summary["runtime_state"])
        compact = summary["runtime_projection"] if output_known else None
        validation, totals = {}, {}
        if isinstance(compact, dict):
            for target, source, keys in ((validation, compact.get("validation"), ("pass", "fail", "warn", "info", "suppressed", "total")), (totals, compact.get("totals"), ("datasource_count", "chart_count", "total_rows", "total_size_bytes"))):
                if isinstance(source, dict):
                    import math
                    target.update({k: v for k, v in source.items() if k in keys and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0})
        result[report_id].update(has_output=summary["has_output"] if output_known else None, html_entry=summary["html_entry"] if output_known else "", validation={"summary": validation} if compact else None, details={"totals": totals, "data_source": compact.get("data_source") if compact.get("data_source") in ("mock", "real") else None} if compact else None, framework_version=compact.get("framework_version")[:200] if compact and isinstance(compact.get("framework_version"), str) else None, live=(row["kind"] == "report" and live_enabled and summary["has_live_queries"]) if output_known else None, stale=daily.get(report_id, 0) == 0 and summary["attempt_status"] == "success" and summary["attempt_at"] is not None and now-timedelta(days=7) <= summary["attempt_at"] <= now if fresh else None)
    return bounded_payload(dict(version=1, reports=result), 128*1024)
