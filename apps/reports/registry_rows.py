"""The one registry row, and the one rule for a report's last status.

The dashboard cards, the Operations table and API clients (``/api/registry``
and ``/api/system/status``) all describe a report with the row built here.
Config comes from the synced ``Report`` row, build facts from the
worker-projected ``ReportSourceSummary`` and the ``Run`` table, source health
from the data-source rows. Nothing reads storage or runs a query per report:
a page of rows costs the same handful of queries whatever the studio's size.

Last status. A report has two witnesses of its latest finished build, the
newest finished Run and the summary's attempt facts. They describe the same
attempt when the summary's attempt time falls within that Run (from its start
to a minute past its end); the summary's text is then used (it is the build's
own, short ``last_error``) while the Run supplies the granular status (out of
memory, timed out). A Run newer than the summary wins, the summary being
behind; a summary later than every Run (a build that has no Run row) stands on
its own. A build that is still queued or running does not replace the last
outcome, it only changes ``build_status``.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta
from types import SimpleNamespace

from django.db.models import (
    BooleanField,
    Case,
    F,
    Func,
    IntegerField,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    TextField,
    Value,
    When,
)
from django.db.models.fields.json import KeyTransform
from django.db.models.functions import Cast, Coalesce, JSONObject, NullIf, Substr

from apps.datasources.models import INLINE_TYPES, DataSource, RepoDataSource
from apps.datasources.status import (
    SourceState,
    _state,
    blocked_names,
    build_error,
    effective_fields,
)
from apps.reports.models import (
    Report,
    ReportViewDaily,
    ReportViewEvent,
    live_query_rate_limit,
)
from apps.reports.scan import _STALE_BUILD_WINDOW_DAYS
from apps.runner.models import Run

#: Rows read, and bulk-resolved together, per dashboard slice.
ROWS_PER_PAGE = 50
#: The same for a whole-registry read, which assembles fewer, larger slices.
ROWS_PER_SLICE = 200
#: Registry order: priority, then slug.
REGISTRY_ORDER = ("priority", "slug", "pk")
#: Distinct data-source names one row may resolve, and in one page of rows.
DEPENDENCY_LIMIT = 25
DEPENDENCY_CAP = 200
#: Characters of a Run's stderr tail read for attribution and the error text.
TAIL_CHARS = 4096
ERROR_CHARS = 500
#: A compact summary projection above this is not read at all.
RUNTIME_BYTES = 16 * 1024
#: A build counts as "recent" for the stale badge when it succeeded this recently.
STALE_BUILD_WINDOW = timedelta(days=_STALE_BUILD_WINDOW_DAYS)
VIEWS_WINDOW = timedelta(days=30)
#: How far past a Run's end the summary's attempt time may fall and still be that Run's build.
SAME_ATTEMPT_SLACK = timedelta(seconds=60)

S = "source_summary__"
FINISHED = (Run.SUCCESS, Run.ERROR, Run.STOPPED, Run.TIMEOUT, Run.OOM_KILLED)
VALIDATION_KEYS = ("pass", "fail", "warn", "info", "suppressed", "total")
DETAIL_KEYS = ("datasource_count", "chart_count", "total_rows", "total_size_bytes")
SUMMARY_FIELDS = tuple(S + name for name in (
    "schema_version", "state", "runtime_state", "quality", "observed_at", "attempt_at", "attempt_status",
    "attempt_error", "has_output", "html_entry", "has_live_queries",
))
ROW_FIELDS = (
    "id", "slug", "kind", "display_name", "display_category", "description", "tags", "schedule_cron",
    "schedule_timezone", "priority", "disabled", "refresh_suspended_at", "last_built_at", "data_expired_at",
    "studio__slug", "metrics_report", "references", "referenced_count", "blocked_preview", "blocked_count",
    "runtime_projection", *SUMMARY_FIELDS,
)


class ByteLength(Func):
    function = "octet_length"
    output_field = IntegerField()


class ReferenceNames(Func):
    """Bounded distinct string/object references; counts stay in PostgreSQL."""
    output_field = JSONField()

    def __init__(self, expression, *, count=False, limit=DEPENDENCY_LIMIT + 1):
        super().__init__(expression, output_field=IntegerField() if count else JSONField())
        self.count, self.limit = count, limit

    def as_sql(self, compiler, connection):
        sql, params = compiler.compile(self.source_expressions[0])
        source = f"jsonb_array_elements(CASE WHEN jsonb_typeof({sql})='array' THEN {sql} ELSE '[]'::jsonb END)"
        name = "CASE WHEN jsonb_typeof(item)='string' THEN item#>>'{}' WHEN jsonb_typeof(item)='object' AND jsonb_typeof(item->'name')='string' THEN item->>'name' ELSE '' END"
        names = f"SELECT DISTINCT {name} AS name FROM {source} AS entries(item)"
        if self.count:
            query = f"SELECT count(*)::integer FROM ({names}) AS names WHERE name<>''"
        else:
            query = f"SELECT COALESCE(jsonb_agg(left(name,200) ORDER BY name),'[]'::jsonb) FROM (SELECT name FROM ({names}) AS names WHERE name<>'' ORDER BY name LIMIT {self.limit}) AS bounded"
        return f"({query})", params * 2


def annotate_rows(relation):
    """The columns one page of registry rows reads, all computed in SQL."""
    runtime = S + "runtime"
    projection = JSONObject(
        validation=KeyTransform("summary", KeyTransform("validation", runtime)),
        totals=KeyTransform("totals", KeyTransform("details", runtime)),
        data_source=KeyTransform("data_source", KeyTransform("details", runtime)),
        framework_version=KeyTransform("framework_version", runtime),
    )
    return relation.annotate(
        display_name=Coalesce(NullIf("name", Value("")), "slug"),
        display_category=Coalesce(NullIf("category", Value("")), Value("Uncategorized")),
        metrics_report=Case(When(slug="metrics", tags__contains=["metrics"], then=Value(True)), default=Value(False), output_field=BooleanField()),
        references=ReferenceNames(F("config__data_sources")),
        referenced_count=ReferenceNames(F("config__data_sources"), count=True),
        blocked_preview=ReferenceNames(F(S + "attempt_blocked_by")),
        blocked_count=ReferenceNames(F(S + "attempt_blocked_by"), count=True),
        runtime_bytes=Coalesce(ByteLength(Cast(runtime, TextField())), Value(0)),
    ).annotate(
        runtime_projection=Case(When(runtime_bytes__lte=RUNTIME_BYTES, then=projection), default=Value(None), output_field=JSONField()),
    )


def page_of_rows(relation, order, limit, offset):
    """One page of raw rows (dicts) in ``order``; ``relation`` is already authorized."""
    return list(annotate_rows(relation).order_by(*order).values(*ROW_FIELDS)[offset:offset + limit])


# ── Bulk reads, one set per page ────────────────────────────────────────────

def _runs(studio, slugs):
    """``(latest, finished)``: slug -> the newest Run, and the newest finished
    Run (the same row unless a build is queued or running). At most two queries."""
    if not slugs:
        return {}, {}
    fields = ("slug", "status", "created_at", "started_at", "finished_at", "tail")
    base = Run.objects.filter(studio=studio, slug__in=slugs)

    def newest(queryset):
        rows = queryset.annotate(tail=Substr("stderr_tail", 1, TAIL_CHARS)).order_by("slug", "-created_at", "-pk").distinct("slug")
        return {row["slug"]: row for row in rows.values(*fields)}

    latest = newest(base)
    finished = {slug: run for slug, run in latest.items() if run["status"] in FINISHED}
    active = [slug for slug in latest if slug not in finished]
    if active:
        finished.update(newest(base.filter(slug__in=active, status__in=FINISHED)))
    return latest, finished


def _views(ids, now):
    """``(views_30d, last_viewed)`` by report id; two queries."""
    if not ids:
        return {}, {}
    daily = {
        row["report_id"]: row["count"]
        for row in ReportViewDaily.objects.filter(report_id__in=ids, date__gte=(now - VIEWS_WINDOW).date())
        .values("report_id").annotate(count=Sum("views")).order_by()
    }
    newest = ReportViewEvent.objects.filter(report_id=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
    last = dict(Report.objects.filter(pk__in=ids).annotate(last=Subquery(newest)).values_list("pk", "last"))
    return daily, last


#: A data-source row above this many bytes is not decoded; its name stays unresolved.
SOURCE_BYTES = 64 * 1024


def source_states_for(studio, org, names, cap=DEPENDENCY_CAP):
    """The state of each named source; unresolvable names are left out.

    Only the named identities may cause encrypted bindings to be decoded, and
    a row whose stored size is over ``SOURCE_BYTES`` is admitted before any
    decryption or long diagnostic text is loaded: its name stays unresolved
    and the rows that reference it fall back to the last observed wait."""
    names = set(names)
    if not names:
        return {}
    declarations = RepoDataSource.objects.filter(studio=studio, present=True, name__in=names).annotate(
        payload_size=ByteLength(Cast("config", TextField())))
    bindings = DataSource.objects.filter(Q(org=org) | Q(studio=studio), name__in=names).annotate(
        payload_size=ByteLength(Cast("config", TextField())) + Coalesce(ByteLength("credentials"), Value(0)))
    # Admit encrypted columns before the model field's from_db_value decrypts.
    d_sizes = list(declarations.values("pk", "name", "payload_size")[:cap + 1])
    b_sizes = list(bindings.values("pk", "name", "payload_size")[:2 * cap + 1])
    if len(d_sizes) > cap or len(b_sizes) > 2 * cap:
        return {}
    bad = {row["name"] for row in d_sizes + b_sizes if row["payload_size"] > SOURCE_BYTES}
    declared = {}
    # Narrow declaration objects, so arbitrarily long diagnostic text cannot be
    # loaded as a deferred field during _state.
    for row in declarations.filter(pk__in=[r["pk"] for r in d_sizes if r["name"] not in bad]).annotate(
            error_preview=Substr("file_error", 1, 600)).values("pk", "name", "type", "config", "error_preview"):
        declared[row["name"]] = SimpleNamespace(pk=row["pk"], name=row["name"], type=row["type"], config=row["config"], file_error=row["error_preview"])
    bound = {}
    for binding in bindings.filter(pk__in=[r["pk"] for r in b_sizes if r["name"] not in bad]).only(
            "name", "type", "config", "credentials", "studio_id", "org_id", "last_check_at", "last_check_ok", "awaiting_first_sync",
    ).annotate(error_preview=Substr("last_check_error", 1, 600)).order_by("studio_id"):
        # Scoped rows shadow shared rows, independent of NULL sort convention.
        binding.last_check_error = binding.error_preview
        if binding.name not in bound or binding.studio_id is not None:
            bound[binding.name] = binding
    states = {}
    for name in names - bad:
        try:
            states[name] = _state(name, declared.get(name), bound.get(name), [])
        except (AttributeError, TypeError, ValueError):
            continue
    return states


# ── The status rule ─────────────────────────────────────────────────────────

def card_status(status):
    """success / error / not_run, as the cards and API clients have always read it."""
    if status == Run.SUCCESS:
        return "success"
    return "error" if status in (Run.ERROR, Run.STOPPED, Run.TIMEOUT, Run.OOM_KILLED) else "not_run"


def derive_status(summary, latest, done):
    """A report's last outcome from its two witnesses (see the module docstring).

    ``summary`` is ``None`` or ``dict(status, at, error, blocked)`` (the
    summary's attempt facts); ``latest`` and ``done`` are the newest Run and
    the newest finished Run as dicts, or ``None``. Returns a dict of
    ``last_status``, ``last_run`` (datetime or None), ``build_status``,
    ``error`` (text), ``meta`` and ``run`` (what ``blocked_names`` reads).
    """
    at = summary["at"] if summary else None
    if done is not None:
        started = done["started_at"] or done["created_at"]
        ended = done["finished_at"] or done["created_at"]
    # The summary saw this Run's build, or a later build that has no Run row.
    same = bool(summary and done and at is not None and started <= at <= ended + SAME_ATTEMPT_SLACK)
    later = bool(summary and (done is None or (at is not None and at > ended + SAME_ATTEMPT_SLACK)))
    if same or later:
        source, detail = "summary", done["status"] if same else summary["status"]
        meta = {"last_status": summary["status"], "blocked_by": summary["blocked"]}
        error = summary["error"]
    elif done:
        source, at, detail, meta, error = "run", ended, done["status"], {}, ""
    else:
        source, at, detail, meta, error = None, None, "", {}, ""
    run = SimpleNamespace(status=done["status"], stderr_tail=done["tail"]) if same or source == "run" else None
    last_status = card_status(detail) if source else "not_run"
    if last_status != "error":
        error = ""
    else:
        if source == "summary":
            error = build_error({"last_error": error}, run)
        # A build killed before it wrote its own error leaves only the Run's tail.
        error = error or (done["tail"] if run else "")
    build_status = detail or last_status
    if latest is not None and latest["status"] in Run.ACTIVE_STATUSES and (at is None or latest["created_at"] >= at):
        build_status = latest["status"]
    return {"last_status": last_status, "last_run": at, "build_status": build_status, "error": error[:ERROR_CHARS],
            "meta": meta, "run": run}


def _summary(raw, now):
    """The summary's attempt facts when it holds a compatible attempt."""
    status = raw[S + "attempt_status"]
    at = raw[S + "attempt_at"]
    if raw[S + "schema_version"] != 2 or not status or (at is not None and at > now):
        return None
    blocked = raw["blocked_preview"] if isinstance(raw["blocked_preview"], list) else []
    return {"status": status, "at": at, "error": raw[S + "attempt_error"] or "", "blocked": blocked}


def _whitelist(source, keys):
    return {key: value for key, value in source.items()
            if key in keys and isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0} if isinstance(source, dict) else {}


def _safe_entry(value):
    if not isinstance(value, str) or not value or len(value) > 255 or value.startswith("/") or "\\" in value or ":" in value or re.search(r"[\x00-\x20\x7f?#]", value):
        return ""
    return value if all(part not in ("", ".", "..") for part in value.split("/")) else ""


def _iso(value):
    return value.isoformat() if value else None


# ── The row ─────────────────────────────────────────────────────────────────

def _output(raw, now):
    """``(has_output, html_entry, live, framework_version, validation, details)``
    of the last compatible observation; ``None`` where it is not known."""
    quality = raw[S + "quality"] in ("observed", "verified")
    observed = raw[S + "schema_version"] == 2 and quality and raw[S + "observed_at"] is not None and raw[S + "observed_at"] <= now
    ready = observed and raw[S + "state"] in ("ready", "missing") and raw[S + "runtime_state"] == "ready"
    known = ready and raw[S + "has_output"] is not None
    has_output = raw[S + "has_output"] if known else None
    entry = _safe_entry(raw[S + "html_entry"]) if known and has_output else ""
    if raw["data_expired_at"]:
        has_output, entry = False, ""
    compact = raw["runtime_projection"] if ready and isinstance(raw["runtime_projection"], dict) else {}
    validation = _whitelist(compact.get("validation"), VALIDATION_KEYS)
    totals = _whitelist(compact.get("totals"), DETAIL_KEYS)
    version = compact.get("framework_version")
    return {
        "has_output": has_output, "html_entry": entry,
        "live": bool(raw[S + "has_live_queries"]) if known else None,
        "framework_version": version[:200] if isinstance(version, str) else None,
        "validation": {"summary": validation} if validation else None,
        "details": {"totals": totals, "data_source": compact.get("data_source") if compact.get("data_source") in ("mock", "real") else None} if totals else None,
    }


def serialize_row(raw, *, now, latest, done, sources, views_30d, last_viewed, live_enabled):
    summary = _summary(raw, now)
    status = derive_status(summary, latest, done)
    refs = [name for name in raw["references"] if isinstance(name, str)]
    complete = raw["referenced_count"] <= len(refs) and all(name in sources for name in refs)
    observed_blocks = blocked_names(status["meta"], status["run"])
    if raw["disabled"]:
        blocked_by = []
    elif complete:
        # What held the last run only counts while it still would: a source
        # configured since, or a reference dropped since, clears the wait
        # before the next build does.
        holding = [name for name in refs if sources[name].blocking]
        blocked_by = [name for name in observed_blocks if name in holding]
    else:
        blocked_by = observed_blocks
    failing = next((sources[name] for name in refs if name in sources and sources[name].state == SourceState.FAILING), None)
    output = _output(raw, now)
    last_run = status["last_run"]
    built_recently = status["last_status"] == "success" and last_run is not None and now - last_run <= STALE_BUILD_WINDOW
    return {
        "id": raw["id"], "kind": raw["kind"], "slug": raw["slug"], "name": raw["display_name"], "description": raw["description"] or "",
        "category": raw["display_category"], "studio": raw["studio__slug"], "tags": raw["tags"] if isinstance(raw["tags"], list) else [],
        "schedule": raw["schedule_cron"], "schedule_timezone": raw["schedule_timezone"], "disabled": raw["disabled"],
        "last_run": _iso(last_run), "last_status": status["last_status"], "build_status": status["build_status"],
        "last_error": status["error"] or None, "blocked_by": blocked_by, "waiting": bool(blocked_by),
        "failing_source": {"name": failing.name, "since": _iso(failing.last_check_at)} if failing is not None else None,
        "last_built_at": _iso(raw["last_built_at"]), "has_output": output["has_output"], "html_entry": output["html_entry"],
        "validation": output["validation"], "details": output["details"], "framework_version": output["framework_version"],
        "views_30d": views_30d, "last_viewed": _iso(last_viewed), "refresh_suspended_at": _iso(raw["refresh_suspended_at"]),
        # "Nobody has looked at a build that finished recently": a report that
        # may need pointing out to whoever it is for, not one that is broken.
        "stale": views_30d == 0 and built_recently,
        "live": bool(raw["kind"] == Report.KIND_REPORT and live_enabled and output["live"]),
        "metrics_report": raw["metrics_report"],
    }


def registry_rows(studio, org, raw_rows, *, now, cap=DEPENDENCY_CAP):
    """Serialize a page of raw rows (``page_of_rows``) with the page's bulk reads.

    ``cap`` bounds the distinct data sources the page resolves."""
    if not raw_rows:
        return []
    latest, finished = _runs(studio, [raw["slug"] for raw in raw_rows])
    daily, last = _views([raw["id"] for raw in raw_rows], now)
    names = sorted({name for raw in raw_rows for name in raw["references"] if isinstance(name, str)})[:cap]
    sources = source_states_for(studio, org, names, cap)
    live_enabled = live_query_rate_limit(org) > 0
    return [
        serialize_row(raw, now=now, latest=latest.get(raw["slug"]), done=finished.get(raw["slug"]), sources=sources,
                      views_30d=daily.get(raw["id"], 0), last_viewed=last.get(raw["id"]), live_enabled=live_enabled)
        for raw in raw_rows
    ]


def all_rows(relation, studio, org, *, now):
    """Every row of ``relation`` (already authorized), read as consecutive slices."""
    rows, offset = [], 0
    while True:
        raw = page_of_rows(relation, REGISTRY_ORDER, ROWS_PER_SLICE, offset)
        rows += registry_rows(studio, org, raw, now=now, cap=4 * DEPENDENCY_CAP)
        offset += ROWS_PER_SLICE
        if len(raw) < ROWS_PER_SLICE:
            return rows


# ── Data source rows (legacy ``source_states`` of the status payloads) ──────

def download_available(state) -> bool:
    """Whether a portal-held file backs this source: the binding of an
    uploadable file source holds a stored path. Stored metadata only; the
    file is not touched, so this costs nothing per source."""
    binding = state.binding
    if binding is None or state.type not in INLINE_TYPES:
        return False
    return bool(effective_fields(state.declaration, binding).get("upload")) and bool(str((binding.config or {}).get("path") or "").strip())


def source_states_payload(studio) -> list[dict]:
    """The Operations tab's data source rows of the status payloads: each
    source's state, the reports it holds, and what Upload/Download need."""
    from apps.datasources.status import source_states

    disabled = set(Report.objects.filter(studio=studio, present_in_scan=True, disabled=True).values_list("slug", flat=True))
    rows = []
    for state in source_states(studio):
        fields = effective_fields(state.declaration, state.binding) if (state.declaration or state.binding) else {}
        rows.append({
            "name": state.name, "type": state.type, "state": state.state, "detail": state.detail,
            "used_by": state.used_by, "binding_scope": state.binding_scope,
            "blocks": sorted(slug for slug in state.used_by if slug not in disabled) if state.blocking else [],
            "upload": state.type in INLINE_TYPES and bool(fields.get("upload")),
            "file": download_available(state),
        })
    return rows
