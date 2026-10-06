"""``trellum serve``'s DEV-ONLY live-query endpoint.

A report maker running ``python -m trellum.run reports/<slug>`` (or
``trellum serve`` over a whole output tree) sees the baked snapshot only —
there is no host behind the page, so a live FilterBar always renders
disabled with the standalone note (see
``trellum.components.live_filterable``). Testing the live path has meant
standing up the portal. This module closes that gap: when the report being
served actually declared a live query (its output directory carries
``_live_queries.json``), the dev server advertises a loopback-only endpoint
and answers it by running the manifest's SQL against the PROJECT's own
datasource — through the exact same guard rails
(``coerce_params``/``check_sql_safe``/``inject_limit``) the portal's
production endpoint enforces. Both hosts import
``trellum.data.live_query_guard`` so the two cannot quietly diverge on what
"safe" means here.

Not a security boundary. There is no auth, and it answers any loopback
caller — this is a convenience for the one person running ``trellum
serve`` on their own machine, never something a host is expected to
expose to anyone else. It only ever activates over ``window._fwHasHost``
like every other host extension, and only for a report that actually
declared a live query, so a page opened from disk, a share export, or a
build with nothing live never issues a request here at all.
"""
from __future__ import annotations

import json
import os
import time

from trellum.data.live_query_guard import ParamError, check_sql_safe, coerce_params, inject_limit

#: Every dev live-query request lands under this suffix of the REPORT's own
#: URL — nested per report (not a single global path) because, unlike the
#: review overlay, which endpoint answers depends on which report's
#: manifest and datasource are in play.
_LQ_SUFFIX = "_fw/live-query"


def _is_loopback(handler) -> bool:
    host = handler.client_address[0]
    return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _report_url_prefix(handler) -> str:
    """URL path (ending in ``/``) of the report currently being requested."""
    path = handler.path.split("?")[0]
    if path.endswith("/"):
        return path
    idx = path.rfind("/")
    return path[: idx + 1] if idx != -1 else "/"


def _manifest_path(report_dir: str) -> str:
    return os.path.join(report_dir, "_live_queries.json")


def _live_query_flag_script(handler) -> str | None:
    """The host-flag ``<script>`` for THIS report, or None when it declared
    no live query — see the module docstring on why that check matters: an
    armed page with nothing behind it is exactly the standalone-build
    invariant the framework otherwise guarantees never fires a request."""
    prefix = _report_url_prefix(handler)
    report_dir = handler.translate_path(prefix)
    if not os.path.isfile(_manifest_path(report_dir)):
        return None
    url = prefix + _LQ_SUFFIX
    return (
        "<script>window._fwHasHost=true;"
        f"window._fwLiveQueryUrl={json.dumps(url)};</script>"
    )


def inject_dev_live_query_flags(body: bytes, handler) -> bytes:
    """Insert the host-flag script into a served report page, right before
    ``</head>`` — ahead of the runtime's own IIFE, which reads these flags
    once at load (see ``trellum.rendering.js_runtime``). A report whose
    manifest check above finds nothing live is returned byte-for-byte
    unchanged: the file on disk never carries this tag, exactly like the
    review overlay it mirrors (``trellum.review.inject``)."""
    flag = _live_query_flag_script(handler)
    if flag is None:
        return body
    tag = flag.encode("utf-8")
    idx = body.rfind(b"</head>")
    if idx == -1:
        idx = body.rfind(b"</HEAD>")
    if idx == -1:
        return body
    return body[:idx] + tag + body[idx:]


def maybe_serve_injected_html(handler) -> bool:
    """Serve report HTML with the live-query host flags injected, when (and
    only when) the report being requested actually declared a live query.

    Mirrors ``trellum.review.inject.maybe_serve_injected_html`` — same
    shape, same place in the caller's dispatch chain — but deliberately
    independent of review mode: the two dev conveniences do not need to
    know about each other, and a page with no live query pays only the one
    cheap manifest-existence check before falling through unchanged to the
    server's normal (gzip-optimized) file serving.
    """
    if handler.command != "GET":
        return False
    fs_path = handler.translate_path(handler.path)
    if os.path.isdir(fs_path):
        fs_path = os.path.join(fs_path, "index.html")
    if os.path.splitext(fs_path)[1].lower() not in (".html", ".htm"):
        return False
    flag = _live_query_flag_script(handler)
    if flag is None:
        return False
    try:
        with open(fs_path, "rb") as fh:
            body = fh.read()
    except OSError:
        return False
    body = inject_dev_live_query_flags(body, handler)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


def handle_auth_me_request(handler) -> bool:
    """Dev-only stand-in for the portal's ``/api/auth/me``
    (``apps/core/views.py`` ``auth_me``) -- the request
    ``data_loader.js``'s admin-only filter-health badge check makes
    whenever ``window._fwHasHost`` is true, which is now true for any
    report ``maybe_serve_injected_html`` above has armed. Without this,
    every such report would 404 on that request in local dev -- the exact
    class of console noise the portal endpoint exists to eliminate, just
    relocated here. Reachable only for a report that actually declared a
    live query (nothing else ever sets ``_fwHasHost``, so nothing else
    ever issues the request), and only from loopback. Always answers
    admin: there is exactly one person able to reach this server at all.
    """
    path = handler.path.split("?")[0]
    if handler.command != "GET" or path != "/api/auth/me":
        return False
    if not _is_loopback(handler):
        return False
    _send_json(handler, 200, {"is_admin": True})
    return True


def _send_json(handler, code: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_live_query_request(handler) -> bool:
    """Answer a POST to this report's ``.../_fw/live-query``. False for
    anything else, so the caller's existing dispatch chain falls through to
    its normal 404 unchanged."""
    path = handler.path.split("?")[0]
    if handler.command != "POST" or not path.endswith("/" + _LQ_SUFFIX):
        return False
    if not _is_loopback(handler):
        _send_json(handler, 403, {"error": "the dev live-query endpoint is loopback-only"})
        return True

    prefix = path[: -len(_LQ_SUFFIX)]
    report_dir = handler.translate_path(prefix)
    try:
        with open(_manifest_path(report_dir), encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        _send_json(handler, 404, {"error": "this report has no _live_queries.json"})
        return True
    if manifest.get("version") != 1:
        _send_json(handler, 404, {"error": "unrecognized _live_queries.json version"})
        return True

    length = int(handler.headers.get("Content-Length") or 0)
    try:
        body = json.loads(handler.rfile.read(length) or b"{}")
    except ValueError:
        _send_json(handler, 400, {"error": "invalid JSON"})
        return True
    if not isinstance(body, dict):
        _send_json(handler, 400, {"error": "expected a JSON object"})
        return True

    query_id = body.get("query_id")
    spec = (manifest.get("queries") or {}).get(query_id)
    if not isinstance(spec, dict):
        _send_json(handler, 400, {"error": "unknown query_id"})
        return True

    try:
        params = coerce_params(spec.get("params") or [], body.get("params") or {})
    except ParamError as exc:
        _send_json(handler, 400, {"error": str(exc)})
        return True

    sql = spec.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        _send_json(handler, 400, {"error": "this query has no SQL"})
        return True
    guard_error = check_sql_safe(sql)
    if guard_error:
        _send_json(handler, 400, {"error": guard_error})
        return True
    sql = inject_limit(sql)

    started = time.monotonic()
    try:
        columns, rows, truncated = _run(str(spec.get("datasource") or ""), sql, params)
    except Exception as exc:  # noqa: BLE001 - surfaced to the local dev page
        _send_json(handler, 400, {"error": f"query failed: {type(exc).__name__}: {exc}"})
        return True
    elapsed_ms = int((time.monotonic() - started) * 1000)

    _send_json(handler, 200, {
        "columns": columns, "rows": rows, "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    })
    return True


def _run(ds_name: str, sql: str, params: dict) -> tuple[list, list, bool]:
    """Resolve the project's datasource and run *sql* (already LIMIT-capped
    and read-only-checked) against it, returning JSON-safe rows.

    Resolution is central-config-only
    (``trellum.data.datasource_config`` / ``data-sources/config.yaml``) —
    the same path a bare string entry in a report's own ``data_sources``
    already goes through (``ReportContext._resolve_source``). A datasource
    declared ONLY as an inline dict in one report's ``report.yaml`` (rather
    than centrally) is not resolvable from here without that report's own
    source tree; this is a dev convenience over the project's *shared*
    sources, not a full report build.
    """
    from trellum.data.connections import resolve_connection
    from trellum.data.live_query_guard import ROW_CAP, shape_rows
    from trellum.data.query import _sql_dialect, bind_params

    conn = resolve_connection(ds_name, [ds_name] if ds_name else [])
    try:
        bound = bind_params(sql, params, dialect=_sql_dialect(conn))
        cur = conn.cursor()
        cur.execute(bound)
        columns = [d[0] for d in (cur.description or [])]
        fetchmany = getattr(cur, "fetchmany", None)
        raw_rows = fetchmany(ROW_CAP + 1) if callable(fetchmany) else list(cur.fetchall())[: ROW_CAP + 1]
        rows, truncated = shape_rows(raw_rows)
        return columns, rows, truncated
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
