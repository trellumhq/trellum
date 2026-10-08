"""Live queries M1 — the governed endpoint behind interactive report pages.

A built report may publish ``_live_queries.json`` beside ``_meta.json``::

    {"version": 1, "queries": {"<query_id>": {
        "sql": str, "datasource": str,
        "params": [{"name": str, "type": "int"|"float"|"str"|"date"|"enum",
                    "required": bool, "values": [...]?, "max_length": int?}]}}}

The page then POSTs ``{"query_id", "params"}`` to
``/s/<org>/<studio>/api/reports/<slug>/live-query`` and gets fresh rows. The
client never sends SQL, column names, or a datasource name — it only picks a
query the build declared and supplies values for its declared parameters.

Why every layer below exists (each is an independent line of defense):

* **Auth**: the same ``require_studio_role(VIEWER)`` that guards report
  serving. Anonymous share-link viewers are excluded in M1 — their pages do
  not carry the URL, and a sessionless POST dies in the decorator.
* **Strict coercion is the injection defense.** The framework's
  ``bind_params`` substitutes *escaped literals*, not driver bindings, so
  nothing may reach the SQL text that was not first forced into the declared
  scalar type. ``bool`` is an ``int`` subclass in Python, so int params
  reject bools explicitly; dates are strict ISO; enums are membership with
  type-exact comparison (``True == 1`` must not sneak through a values list).
* **Read-only guard + LIMIT injection** on the manifest SQL at serve time —
  the manifest is authored by the report developer, and a doctored manifest
  must still not write. (Logic recovered from the removed assistant helpers
  ``check_sql_safe``/``inject_limit`` — apps/assistant/tools.py before
  b72a422 — minus assistant's LLM style rules banning GROUP BY/HAVING, which
  were never part of the read-only posture.)
* **Bounded execution**: connect + execute on a worker thread with a hard
  deadline (the join pattern of apps/datasources/testing.py), NEVER through
  ``trellum.data.query.query_df`` — its process-wide blocking ``Semaphore(4)``
  has no timeout and would convoy gunicorn threads. ``bind_params`` itself
  is imported as a pure function (importing the module only *creates* an
  inert semaphore; only ``query_df`` acquires it), so the portal reuses the
  framework's exact escaping rules instead of maintaining a copy.
* **Caps that fail fast**: a non-blocking process-wide slot pool (429 +
  ``Retry-After`` when full — never a queue that holds a web thread), a
  per-org pool, a row cap, a response byte cap (rows are *truncated* to fit,
  documented below, rather than 413ing a page that asked in good faith), and
  a per-user rate limit.
* **Result cache** in the shared database cache, so a dashboard full of
  identical widgets across gunicorn workers costs one execution per 45s.
* **Audit** one row per executed (non-cache-hit) query — query_id and
  timing only, NEVER parameter values or SQL text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

from apps.core import roles, storage
from apps.core.audit import audit
from apps.core.permissions import require_studio_role

logger = logging.getLogger(__name__)

#: Hard wall-clock deadline for one query, connect included.
DEADLINE_SECONDS = 10
#: Approximate ceiling on the response JSON. Rows are dropped from the tail
#: (and ``truncated`` set) rather than answering 413: the page asked a
#: well-formed question and partial rows beat no rows for a filter widget.
RESPONSE_BYTE_CAP = 2 * 1024 * 1024
#: Result cache TTL.
RESULT_TTL_SECONDS = 45
#: Parsed-manifest cache TTL (keyed per report+build, so this only bounds
#: how long a *deleted* manifest keeps answering).
MANIFEST_TTL_SECONDS = 30
#: Simultaneous executions per organization.
PER_ORG_MAX = 2

#: SQL_TYPES / ROW_CAP / DEFAULT_STR_MAX live in the framework now (shared
#: with ``trellum serve``'s dev live-query endpoint — see the import below).


# ── Concurrency slots ────────────────────────────────────────────────────────
# Acquired NON-BLOCKING in the request thread; released by the worker thread
# when the driver actually returns. That last part matters: a query that blows
# the deadline gets its request answered 504, but its slot stays taken until
# the driver gives the thread back — so the caps count queries genuinely
# running against the warehouse, and a hung host cannot stack unbounded
# abandoned queries behind a wall of fresh 429-dodging requests.

_global_sem: threading.BoundedSemaphore | None = None
_global_sem_guard = threading.Lock()

_org_counts: dict[int, int] = {}
_org_guard = threading.Lock()


def _get_global_sem() -> threading.BoundedSemaphore:
    global _global_sem
    with _global_sem_guard:
        if _global_sem is None:
            limit = int(
                getattr(settings, "TRELLUM_LIVEQUERY_MAX_CONCURRENT", 4) or 4
            )
            _global_sem = threading.BoundedSemaphore(limit)
        return _global_sem


def _acquire_org(org_id: int) -> bool:
    with _org_guard:
        if _org_counts.get(org_id, 0) >= PER_ORG_MAX:
            return False
        _org_counts[org_id] = _org_counts.get(org_id, 0) + 1
        return True


def _release_org(org_id: int) -> None:
    with _org_guard:
        n = _org_counts.get(org_id, 0) - 1
        if n <= 0:
            _org_counts.pop(org_id, None)
        else:
            _org_counts[org_id] = n


# ── Read-only guard + LIMIT injection, strict parameter coercion ────────────
# Pure functions, no host dependency — moved to the framework so
# `trellum serve`'s dev live-query endpoint executes a manifest under
# exactly the same rules this production endpoint does, rather than a
# second copy that can quietly drift. See trellum/data/live_query_guard.py.
from trellum.data.live_query_guard import (
    ROW_CAP,
    SQL_TYPES,
    ParamError,
    check_sql_safe,
    coerce_params,
    inject_limit,
)
from trellum.data.live_query_guard import shape_rows as _fw_shape_rows

# ── Manifest ─────────────────────────────────────────────────────────────────

def load_manifest(studio, slug: str) -> dict | None:
    """The parsed ``_live_queries.json`` for a report's current build, or
    None when absent. Cached briefly per report+build in the shared Django
    cache — the version tag (build id remote, file mtime local) is part of
    the key, so a rebuild is picked up immediately."""
    version = storage.live_queries_version(studio, slug)
    if not version:
        return None
    key = f"livequery:manifest:{studio.pk}:{slug}:{version}"
    hit = cache.get(key)
    if hit is None:
        hit = storage.read_live_queries(studio, slug)
        cache.set(key, hit, MANIFEST_TTL_SECONDS)
    return hit or None


# ── Datasource resolution + connection info ──────────────────────────────────
# Small pure helpers recreated from assistant's removed run_query tool (its
# `_conn_info`/`_default_source`, apps/assistant/tools.py before b72a422) — the
# same studio-shadows-org resolution report builds use, and the same
# row→driver-conn_info mapping, without importing ghosts.

def resolve_datasource(studio, name: str):
    """The DataSource this studio knows by ``name``, or None."""
    from apps.datasources.models import sources_for_studio

    for ds in sources_for_studio(studio):
        if ds.name == name:
            return ds
    return None


class SourceUnusable(ValueError):
    """The resolved source cannot run a live query. Always a 400; the
    message is plain and names no credentials."""


def conn_info_for(ds) -> dict:
    """DataSource row → the framework driver's ``conn_info`` dict."""
    info = {
        key: value
        for key, value in ds.all_fields().items()
        if key != "upload" and str(value or "").strip() != ""
    }
    if "port" in info:
        try:
            info["port"] = int(info["port"])
        except (TypeError, ValueError):
            pass
    if ds.type in ("sqlite", "duckdb"):
        # Resolve to the absolute stored file (org- vs studio-scoped, with
        # the traversal guard) and refuse a missing one: sqlite3.connect (and
        # duckdb.connect) on a missing path would silently create an empty
        # database.
        from apps.datasources.materialize import stored_file

        path = stored_file(ds)
        if path is None or not path.is_file():
            raise SourceUnusable("the data source's file is missing")
        info["path"] = str(path)
    if ds.type == "duckdb":
        # Trusted portal policy, never an author-provided driver setting.
        info["portal_live_query"] = True
    return info


def bind_sql(sql: str, params: dict, ds_type: str) -> str:
    """Substitute coerced params as escaped literals, reusing the framework's
    own escaping (`trellum.data.query.bind_params` is pure — the module's
    query semaphore is only ever acquired by ``query_df``, which this path
    deliberately never calls). The dialect is the framework's own
    ``sql_dialect_for`` keyed on the source type."""
    from trellum.data.query import bind_params, sql_dialect_for

    return bind_params(sql, params, sql_dialect_for(ds_type))


# ── Bounded execution ────────────────────────────────────────────────────────

def _execute_sql(ds, conn_info: dict, sql: str, result: dict) -> None:
    """Connect, execute, fetch — run on the worker thread (connections such
    as sqlite's are thread-bound, so the connection must be born here)."""
    conn = None
    try:
        from trellum.data.drivers import connect

        conn = connect(ds.type, conn_info)

        cursor_factory = getattr(conn, "cursor", None)
        if not callable(cursor_factory):
            result["error"] = "this data source does not support live queries"
            return
        cur = conn.cursor()
        cur.execute(sql)
        result["columns"] = [d[0] for d in (cur.description or [])]
        fetchmany = getattr(cur, "fetchmany", None)
        if callable(fetchmany):
            rows = fetchmany(ROW_CAP + 1)
        else:
            rows = list(cur.fetchall())[: ROW_CAP + 1]
        result["rows"] = rows
    except Exception as exc:  # noqa: BLE001 - logged in full, surfaced as a plain 400
        msg = ds.scrub(f"{type(exc).__name__}: {exc}")
        logger.warning("live query failed on data source %s (%s): %s", ds.name, ds.type, msg[:1000])
        # Driver text names hosts, objects and SQL fragments a viewer must not
        # learn from an error page; the log has the detail.
        result["error"] = "query failed"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ── Serialization ────────────────────────────────────────────────────────────
# jsonable_value/shape_rows live in the framework now too (trellum.data.
# live_query_guard) -- this is a thin wrapper applying this endpoint's own
# byte-cap policy on top of the shared row/cap logic.

def _shape_rows(raw_rows: list) -> tuple[list, bool]:
    """JSON-safe rows within both the row cap and the byte cap."""
    return _fw_shape_rows(raw_rows, row_cap=ROW_CAP, byte_cap=RESPONSE_BYTE_CAP)


# ── Rate limit ───────────────────────────────────────────────────────────────

def _rate_limited(user_id: int, org) -> bool:
    """Sliding-ish per-user counter in the shared cache (same add/incr shape
    as apps.accounts.throttle). Only *executions* count — cache hits are
    nearly free and a dashboard of widgets should not starve itself.

    The ceiling is per-org (apps.reports.models.live_query_rate_limit),
    defaulting to DEFAULT_LIVE_QUERY_RATE_LIMIT for an org that never
    configured one -- the live-query filter redesign turns one filter
    change into N linked queries, so the right budget varies by how an
    org's reports are built. The counter itself stays per-USER (not
    per-org): two users at the same org filtering independently should not
    share one bucket."""
    from apps.reports.models import live_query_rate_limit

    window = int(time.time() // 60)
    key = f"livequery:rate:{user_id}:{window}"
    try:
        cache.add(key, 0, 120)
        count = cache.incr(key)
    except ValueError:
        return False  # expired between add and incr; next call starts fresh
    return count > live_query_rate_limit(org)


# ── The endpoint ─────────────────────────────────────────────────────────────

def _bad(message: str) -> JsonResponse:
    return JsonResponse({"error": message}, status=400)


def _busy() -> JsonResponse:
    response = JsonResponse({"error": "too many live queries; retry shortly"}, status=429)
    response["Retry-After"] = "1"
    return response


@require_studio_role(roles.VIEWER)
@require_POST
def api_live_query(request, org_slug, studio_slug, slug):  # noqa: ARG001
    """POST {"query_id", "params"} → {"columns", "rows", "truncated",
    "elapsed_ms"}. See the module docstring for the full posture."""
    from apps.core.http import json_body
    from apps.reports.views import _get_report, _selected_content_block

    report = _get_report(request, slug)  # 404: absent (or invisible) report
    if _selected_content_block(request, report):
        return JsonResponse({"error": "selected_report_access_unavailable"}, status=503)
    if report.kind == "analysis":
        return _bad("Analyses do not support live queries")

    body = json_body(request)
    query_id = body.get("query_id")
    if not isinstance(query_id, str) or not query_id:
        return _bad("query_id is required")

    try:
        manifest = load_manifest(request.studio, slug)
    except Exception as exc:  # noqa: BLE001 - store outage reads as an outage
        logger.warning(f"live-query manifest unreachable: {type(exc).__name__}: {exc}")
        return JsonResponse(
            {"error": "report storage is temporarily unreachable"}, status=503
        )
    if not manifest or manifest.get("version") != 1:
        # No manifest (or one this portal cannot parse) is indistinguishable
        # from "report has no live queries": 404, never a hint of which.
        raise Http404

    spec = (manifest.get("queries") or {}).get(query_id)
    if not isinstance(spec, dict):
        return _bad("unknown query_id")

    try:
        params = coerce_params(spec.get("params") or [], body.get("params") or {})
    except ParamError as exc:
        return _bad(str(exc))

    ds = resolve_datasource(request.studio, str(spec.get("datasource") or ""))
    if ds is None:
        return _bad("this report's data source is not configured in this studio")
    if ds.type not in SQL_TYPES:
        return _bad("this data source type does not support live queries")
    missing = ds.missing_fields()
    if missing:
        return _bad("the data source is not fully configured")

    sql = spec.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return _bad("this query has no SQL")
    guard_error = check_sql_safe(sql)
    if guard_error:
        return _bad(guard_error)
    sql = inject_limit(sql)

    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    cache_key = f"livequery:{request.studio.pk}:{slug}:{query_id}:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    if _rate_limited(request.user.pk, request.org):
        response = JsonResponse({"error": "rate limit exceeded"}, status=429)
        response["Retry-After"] = str(60 - int(time.time() % 60) or 60)
        return response

    try:
        sql = bind_sql(sql, params, ds.type)
        conn_info = conn_info_for(ds)
    except SourceUnusable as exc:
        return _bad(str(exc))

    sem = _get_global_sem()
    if not sem.acquire(blocking=False):
        return _busy()
    org_id = request.org.pk
    if not _acquire_org(org_id):
        sem.release()
        return _busy()

    result: dict = {}

    def _run() -> None:
        try:
            _execute_sql(ds, conn_info, sql, result)
        finally:
            # The worker owns the slots: they free when the driver actually
            # returns, deadline or not, so the caps always count queries
            # genuinely running.
            _release_org(org_id)
            sem.release()

    worker = threading.Thread(target=_run, daemon=True)
    started = time.monotonic()
    try:
        worker.start()
    except Exception:  # noqa: BLE001 - could not even spawn; free the slots
        _release_org(org_id)
        sem.release()
        return JsonResponse({"error": "temporarily unavailable"}, status=503)
    worker.join(DEADLINE_SECONDS)

    if worker.is_alive():
        # outcome is the column now (apps.core.models.AuditLog), not a
        # metadata key -- "failure" so it lands in the same non-success
        # bucket as a query error; timed_out=True keeps the distinction the
        # old metadata-only "timeout" string used to carry.
        audit(
            request, "report.live_query", target=report, outcome="failure",
            query_id=query_id, elapsed_ms=DEADLINE_SECONDS * 1000, timed_out=True,
        )
        return JsonResponse(
            {"error": f"query exceeded the {DEADLINE_SECONDS}s deadline"}, status=504
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if "error" in result:
        audit(
            request, "report.live_query", target=report, outcome="failure",
            query_id=query_id, elapsed_ms=elapsed_ms,
        )
        return _bad(result["error"])

    rows, truncated = _shape_rows(result.get("rows") or [])
    payload = {
        "columns": result.get("columns") or [],
        "rows": rows,
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }
    cache.set(cache_key, payload, RESULT_TTL_SECONDS)
    audit(
        request, "report.live_query", target=report,
        query_id=query_id, elapsed_ms=elapsed_ms,
        rows=len(rows), truncated=truncated,
    )
    return JsonResponse(payload)
