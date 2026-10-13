"""The /_fw/review* endpoints the browser overlay talks to."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from trellum.review.inject import (
    WORKSPACE_SCRIPT_MODULE,
    overlay_js,
    workspace_overlay_js,
)
from trellum.review.state import _MAX_POLL_SECONDS, STATE, ReviewInactive

#: Every endpoint this module answers lives under this prefix.
_REVIEW_PREFIX = "/_fw/review"
_MAX_BODY_BYTES = 1024 * 1024


def _send_json(handler, code: int, obj: dict) -> None:
    # No Cache-Control here: both dev-server handlers add their own in
    # end_headers(), and doubling the header is worse than trusting them.
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def _read_body(handler, limit: int = _MAX_BODY_BYTES) -> dict | None:
    """Parse the JSON request body; None means an error was already sent."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        _send_json(handler, 400, {"error": "invalid Content-Length"})
        return None
    if length < 0 or handler.headers.get("Transfer-Encoding"):
        _send_json(handler, 400, {"error": "invalid body framing"})
        return None
    if length > limit:
        _send_json(handler, 413, {"error": "body too large"})
        return None
    raw = handler.rfile.read(length) if length else b""
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        _send_json(handler, 400, {"error": "invalid JSON"})
        return None
    if not isinstance(parsed, dict):
        _send_json(handler, 400, {"error": "expected a JSON object"})
        return None
    return parsed

def _is_loopback(handler) -> bool:
    host = handler.client_address[0]
    return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def _analysis_origin_allowed(handler, require_origin: bool = False) -> bool:
    port = handler.server.server_address[1]
    hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
    host = handler.headers.get("Host", "")
    if host not in hosts or handler.headers.get("Sec-Fetch-Site", "") not in ("", "same-origin", "none"):
        return False
    origin = handler.headers.get("Origin")
    return (origin == f"http://{host}" if origin is not None else not require_origin)


def _handle_analysis_request(handler) -> bool:
    from trellum.analysis_capture import MAX_CAPTURE_BYTES
    from trellum.analysis_inbox import analysis_target
    from trellum.project import get_project_root

    path = handler.path.split("?")[0]
    prefix = f"{_REVIEW_PREFIX}/analysis"
    if not path.startswith(prefix):
        return False
    if not _analysis_origin_allowed(handler, require_origin=path == prefix + "/capture"):
        _send_json(handler, 403, {"error": "analysis endpoints require an allowed local origin"})
        return True
    method = handler.command
    if path == prefix and method == "GET":
        _send_json(handler, 200, STATE.analysis_status())
        return True
    if method != "POST" or path not in (prefix + "/start", prefix + "/capture", prefix + "/end"):
        _send_json(handler, 404, {"error": "unknown analysis endpoint"})
        return True
    body = _read_body(handler, MAX_CAPTURE_BYTES + 16384 if path.endswith("/capture") else _MAX_BODY_BYTES)
    if body is None:
        return True
    try:
        if path.endswith("/start"):
            slug = body.get("slug")
            if not isinstance(slug, str):
                raise ValueError("Analysis slug required")
            analysis_target(get_project_root(), slug)
            _send_json(handler, 200, STATE.start_analysis(slug))
        elif path.endswith("/end"):
            STATE.end_analysis()
            _send_json(handler, 200, {"ok": True})
        else:
            request_id, note, capture = body.get("request_id"), body.get("note", ""), body.get("capture")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", request_id):
                raise ValueError("A unique request ID of 16–100 characters is required")
            if not isinstance(note, str) or len(note) > 2000:
                raise ValueError("Capture note must be text of at most 2000 characters")
            capture_id, duplicate = STATE.submit_capture(
                get_project_root(), handler.headers.get("X-Trellum-Analysis-Token", ""),
                request_id, capture, note)
            _send_json(handler, 200, {"ok": True, "capture_id": capture_id, "duplicate": duplicate})
    except ReviewInactive:
        _send_json(handler, 409, {"error": "analysis session not active"})
    except PermissionError:
        _send_json(handler, 403, {"error": "invalid analysis session token"})
    except FileExistsError as exc:
        _send_json(handler, 409, {"error": str(exc)})
    except (ValueError, TypeError, FileNotFoundError) as exc:
        _send_json(handler, 400, {"error": str(exc)})
    except OSError:
        _send_json(handler, 500, {"error": "capture could not be stored; try again"})
    return True

def _query_params(path: str) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlsplit

    return dict(parse_qsl(urlsplit(path).query))

def _data_version(served_dir: str, slug: str) -> str | None:
    """Version key for the browser's reload watch: _meta.json's mtime.

    Every rebuild rewrites _meta.json (meta.write_meta), so its mtime moves
    exactly once per build -- cheaper and more honest than hashing HTML.
    """
    candidates = []
    if slug:
        candidates.append(Path(served_dir) / slug / "_meta.json")
    candidates.append(Path(served_dir) / "_meta.json")
    for p in candidates:
        try:
            return str(os.path.getmtime(p))
        except OSError:
            continue
    return None


def _workspace_catalog(root):
    from trellum.review.workspace import catalog
    return catalog(root)


def _workspace_auth(handler, body=None, query=False, allow_ended=False):
    """Validate the active analysis identity and its session token on writes."""
    status = STATE.analysis_status()
    if not status["active"] and not (allow_ended and STATE.ended and STATE.analysis_slug):
        raise ReviewInactive
    if body is not None:
        analysis_slug = body.get("analysis_slug")
        if (type(body.get("session")) is not int or body.get("session") != STATE.session
                or analysis_slug != (status["slug"] if status["active"] else STATE.analysis_slug)
                or not secrets_compare(handler.headers.get("X-Trellum-Analysis-Token", ""),
                                       status["token"] if status["active"] else STATE.analysis_token)):
            raise PermissionError("Invalid analysis workspace session")
    elif query:
        params = _query_params(handler.path)
        if params.get("fw_session") and params.get("fw_session") != str(STATE.session):
            raise PermissionError("Workspace session is no longer active")
    if allow_ended and STATE.ended and STATE.analysis_slug:
        return {"active": False, "slug": STATE.analysis_slug,
                "token": STATE.analysis_token, "session": STATE.session}
    return status


def secrets_compare(left, right):
    import secrets
    return isinstance(left, str) and secrets.compare_digest(left, right)


def _script_json(value: dict) -> str:
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _workspace_state(handler, root, output_root) -> None:
    from trellum.review.workspace import WORKSPACE

    active = STATE.analysis_status()
    status = STATE.status()
    status["replies"] = [reply for reply in status["replies"]
                          if not reply.get("slug") or (active["active"]
                          and reply.get("slug") == active["slug"])]
    status["queued"] = sum(1 for batch in STATE.queue
                           if not active["active"] or batch.get("slug") == active["slug"])
    if not active["active"]:
        _send_json(handler, 200, {**status, "active": False, "ended": True,
            "session": STATE.session, "token": "", "analysis": None,
            "reports": [], "initial_source": _query_params(handler.path).get("source", "")})
        return
    entries = _workspace_catalog(root)
    analysis = entries.get(active["slug"])
    if not analysis or analysis["kind"] != "analysis":
        raise ValueError("Active analysis is no longer available")
    reports = []
    for row in entries.values():
        if row["kind"] != "report" or row["slug"] == active["slug"]:
            continue
        build, version, built_at = _workspace_output(output_root, row)
        job = WORKSPACE.job(root, row["slug"])
        if job.get("state") == "building":
            build = "building"
        elif job.get("state") == "failed":
            build = "failed"
        reports.append({"slug": row["slug"], "name": row["name"], "kind": "report",
            "url": f"/{quote(row['slug'])}/index.html", "build_state": build,
            "version": version, "source_built_at": built_at, "error": job.get("error")})
    abuild, aversion, abuilt = _workspace_output(output_root, analysis)
    agent = STATE.status()["agent"]
    if agent["state"] == "working" and (agent.get("seen_ms_ago") or 0) > 90000:
        agent = {"state": "absent", "seen_ms_ago": agent.get("seen_ms_ago")}
    _send_json(handler, 200, {**status, "active": True, "ended": False,
        "session": STATE.session, "token": active["token"],
        "analysis": {"slug": active["slug"], "name": analysis["name"],
                     "url": f"/{quote(active['slug'])}/index.html",
                     "version": aversion, "build_state": abuild, "source_built_at": abuilt},
        "agent": agent, "reports": reports,
        "initial_source": _query_params(handler.path).get("source", "")})


def _serve_workspace_get(handler, path: str, active: dict) -> None:
    from trellum.assets import load_css, load_js

    prefix = _REVIEW_PREFIX + "/workspace"
    if path == prefix:
        source = Path(__file__).parents[1] / "static" / "analysis-workspace.html"
        raw = source.read_text(encoding="utf-8")
        config = {"session": STATE.session, "analysis_slug": active["slug"],
                  "token": active["token"],
                  "initial_source": _query_params(handler.path).get("source", "")}
        raw = raw.replace("__FW_WORKSPACE_CONFIG__", _script_json(config))
        body = raw.encode()
        content_type = "text/html; charset=utf-8"
    elif path == "/_fw/review/workspace.js":
        body = load_js(f"review/{WORKSPACE_SCRIPT_MODULE}.js").encode()
        content_type = "application/javascript"
    elif path == "/_fw/review/workspace-bridge.js":
        body = workspace_overlay_js().encode("utf-8")
        content_type = "application/javascript"
    else:
        body = load_css("analysis-workspace.css").encode()
        content_type = "text/css"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _workspace_json(handler, served_dir):
    """Handle the development-only analysis workspace routes."""
    from trellum.analysis_capture import MAX_CAPTURE_BYTES, validate_capture
    from trellum.analysis_inbox import store_capture
    from trellum.project import get_project_root
    from trellum.review.workspace import WORKSPACE, fingerprint

    path = handler.path.split("?", 1)[0]
    prefix = _REVIEW_PREFIX + "/workspace"
    asset_paths = {"/_fw/review/workspace.js", "/_fw/review/workspace-bridge.js",
                   "/_fw/review/workspace.css"}
    if not path.startswith(prefix) and path not in asset_paths:
        return False
    method = handler.command
    if not _analysis_origin_allowed(handler, require_origin=method == "POST"):
        _send_json(handler, 403, {"error": "workspace endpoints require an allowed local origin"})
        return True
    root = Path(get_project_root()).resolve()
    output_root = Path(served_dir).resolve()
    WORKSPACE.bind(str(output_root))
    try:
        if path == prefix + "/state" and method == "GET":
            _workspace_state(handler, root, output_root)
            return True
        if (path == prefix or path in asset_paths) and method == "GET":
            active = _workspace_auth(handler, query=True)
            _serve_workspace_get(handler, path, active)
            return True
        if method != "POST" or path not in (prefix + "/open", prefix + "/capture", prefix + "/feedback"):
            _send_json(handler, 404, {"error": "unknown workspace endpoint"})
            return True
        limit = MAX_CAPTURE_BYTES + 16384 if path.endswith("/capture") else _MAX_BODY_BYTES
        body = _read_body(handler, limit)
        if body is None:
            return True
        active = _workspace_auth(handler, body=body, allow_ended=path.endswith("/feedback"))
        entries = _workspace_catalog(root)
        analysis = entries.get(active["slug"])
        if not analysis or analysis["kind"] != "analysis":
            raise ValueError("Active analysis is unavailable")
        if path.endswith("/open"):
            slug = body.get("slug")
            if slug != active["slug"] and (slug not in entries or entries[slug]["kind"] != "report"):
                raise ValueError("Source is not an available report in this project")
            request_id = body.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", request_id):
                raise ValueError("A request ID of 16–100 characters is required")
            key = "open:" + request_id
            fp = fingerprint({"slug": slug, "analysis": active["slug"]})
            with STATE._cond:
                _workspace_auth(handler, body=body)
                prior = STATE.workspace_requests.get(key)
                if prior and prior["fingerprint"] != fp:
                    raise FileExistsError("Request ID already used")
                row = entries[slug]
                state, _, _ = _workspace_output(output_root, row)
                job = WORKSPACE.job(root, slug)
                if job.get("state") == "building":
                    state = "building"
                elif job.get("state") == "failed" and prior:
                    state = "failed"
                elif state not in ("ready", "building") and not prior:
                    if len(STATE.workspace_requests) >= 10000:
                        raise ValueError("Workspace request limit reached; start a new session")
                    STATE.workspace_requests[key] = {"fingerprint": fp}
                    WORKSPACE._persist_fields()
                    STATE._save_locked()
                    job = WORKSPACE.start_build(root, slug)
                    state = job.get("state", "building")
                elif not prior:
                    STATE.workspace_requests[key] = {"fingerprint": fp}
                    WORKSPACE._persist_fields()
                    STATE._save_locked()
            _send_json(handler, 200, {"build_state": state})
            return True
        if path.endswith("/capture"):
            capture, source_slug = body.get("capture"), body.get("slug")
            request_id = body.get("request_id")
            if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", request_id):
                raise ValueError("A request ID of 16–100 characters is required")
            if source_slug not in entries:
                raise ValueError("Capture source is not available in this project")
            validate_capture(capture)
            cap_source = capture.get("source") or {}
            if cap_source.get("report_slug") != source_slug:
                raise ValueError("Capture source does not match the selected report")
            if source_slug != active["slug"] and entries[source_slug]["kind"] != "report":
                raise ValueError("Capture source must be the active analysis or a report")
            fingerprint_value = fingerprint({"capture": capture, "analysis": active["slug"], "slug": source_slug})
            with STATE._cond:
                current = _workspace_auth(handler, body=body)
                if current["slug"] != active["slug"]:
                    raise PermissionError("Analysis session changed")
                prior = STATE.workspace_captures.get(request_id)
                if prior:
                    if prior["fingerprint"] != fingerprint_value:
                        raise FileExistsError("Request ID already used")
                    _send_json(handler, 200, {"capture_id": prior["capture_id"], "duplicate": True})
                    return True
                if len(STATE.workspace_captures) >= 10000:
                    raise ValueError("Workspace capture limit reached; start a new session")
                capture_id = store_capture(root, active["slug"], capture)
                STATE.workspace_captures[request_id] = {"fingerprint": fingerprint_value,
                    "capture_id": capture_id, "slug": source_slug}
                WORKSPACE._persist_fields()
                STATE._save_locked()
            _send_json(handler, 200, {"capture_id": capture_id})
            return True
        return _workspace_feedback(handler, body, active, entries, WORKSPACE)
    except ReviewInactive:
        _send_json(handler, 409, {"error": "analysis workspace session not active"})
    except PermissionError:
        _send_json(handler, 403, {"error": "invalid analysis workspace session"})
    except FileExistsError as exc:
        _send_json(handler, 409, {"error": str(exc)})
    except (ValueError, TypeError, FileNotFoundError) as exc:
        _send_json(handler, 400, {"error": str(exc)})
    except OSError:
        _send_json(handler, 500, {"error": "workspace request could not be stored"})
    return True


def _workspace_output(output_root, row):
    from trellum.review.workspace import _output_info
    return _output_info(output_root, row["slug"], row["dir"])


def _workspace_feedback(handler, body, active, entries, workspace):
    from trellum.review.workspace import fingerprint
    request_id, note, items = body.get("request_id"), body.get("note", ""), body.get("items", [])
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", request_id):
        raise ValueError("A request ID of 16–100 characters is required")
    if not isinstance(note, str) or len(note) > 4000:
        raise ValueError("Note must be at most 4000 characters")
    if "end_session" in body and not isinstance(body["end_session"], bool):
        raise ValueError("end_session must be a boolean")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("At most 50 feedback items are allowed")
    normalized = []
    string_limits = {"prompt": 2000, "selector": 1000, "tag": 100, "text": 1000,
                     "origin_name": 200, "origin_kind": 20, "source_built_at": 80,
                     "frame_id": 100, "capture_id": 100}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each feedback item must be an object")
        source_slug = item.get("origin_slug")
        if source_slug not in entries:
            raise ValueError("Feedback source is not available in this project")
        if source_slug != active["slug"] and entries[source_slug]["kind"] != "report":
            raise ValueError("Feedback source must be the active analysis or a report")
        for field, limit in string_limits.items():
            if field in item and item[field] is not None and (not isinstance(item[field], str) or len(item[field]) > limit):
                raise ValueError(f"{field} must be text of at most {limit} characters")
        for field in ("section", "component", "data"):
            if field in item and item[field] is not None and not isinstance(item[field], dict):
                raise ValueError(f"{field} must be an object or null")
        if "filters" in item and item["filters"] is not None and not isinstance(item["filters"], dict):
            raise ValueError("filters must be an object or null")
        if item.get("capture_id"):
            capture_id = item["capture_id"]
            matches = [v for v in STATE.workspace_captures.values()
                       if v.get("capture_id") == capture_id and v.get("slug") == source_slug]
            if not matches:
                raise ValueError("Capture reference does not match this analysis and source")
        if item.get("destination") not in (None, active["slug"]):
            raise ValueError("Workspace items must target the active analysis")
        filtered = {k: item[k] for k in ("prompt", "selector", "tag", "text", "section", "component", "data") if item.get(k) is not None}
        filtered.update({k: item[k] for k in ("origin_slug", "origin_name", "origin_kind", "filters", "source_built_at", "capture_id", "frame_id") if item.get(k) is not None})
        filtered["origin_name"] = entries[source_slug]["name"]
        filtered["origin_kind"] = entries[source_slug]["kind"]
        filtered["analysis_slug"] = active["slug"]
        filtered["default_target_slug"] = active["slug"]
        filtered["destination"] = active["slug"]
        filtered["workspace_action"] = (
            "edit-analysis" if source_slug == active["slug"] else "add-evidence"
        )
        normalized.append(filtered)
    payload = {"analysis": active["slug"], "note": note, "items": normalized,
               "end_session": bool(body.get("end_session"))}
    fp = fingerprint(payload)
    batch = {"slug": active["slug"], "analysis_slug": active["slug"], "page": "workspace",
             "session": STATE.session, "note": note, "items": normalized, "ts": time.time(),
             "workspace": True}
    with STATE._cond:
        current = _workspace_auth(handler, body=body, allow_ended=True)
        if current["slug"] != active["slug"]:
            raise PermissionError("Analysis session changed")
        previous = STATE.workspace_feedback.get(request_id)
        if previous:
            if previous["fingerprint"] != fp:
                raise FileExistsError("Request ID already used for different feedback")
            _send_json(handler, 200, {"ok": True, "duplicate": True})
            return True
        if not STATE.enabled or STATE.ended:
            raise ReviewInactive
        if len(STATE.workspace_feedback) >= 10000:
            raise ValueError("Workspace feedback limit reached; start a new session")
        STATE.queue.append(batch)
        if body.get("end_session"):
            STATE.ended = True
        STATE.workspace_feedback[request_id] = {"fingerprint": fp}
        workspace._persist_fields()
        STATE._save_locked()
        STATE._cond.notify_all()
    _send_json(handler, 200, {"ok": True})
    return True

def handle_review_request(handler, served_dir: str) -> bool:
    """Answer a ``/_fw/review*`` request. Returns False for any other path."""
    path = handler.path.split("?")[0]
    if not path.startswith(_REVIEW_PREFIX):
        return False
    if not _is_loopback(handler):
        _send_json(handler, 403, {"error": "review endpoints are loopback-only"})
        return True
    STATE.bind_dir(served_dir)
    if _workspace_json(handler, served_dir):
        return True
    if _handle_analysis_request(handler):
        return True

    method = handler.command
    if path == "/_fw/review.js" and method == "GET":
        try:
            body = overlay_js().encode("utf-8")
        except OSError:
            handler.send_error(404)
            return True
        handler.send_response(200)
        handler.send_header("Content-Type", "application/javascript")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True

    if path == f"{_REVIEW_PREFIX}/status" and method == "GET":
        params = _query_params(handler.path)
        out = STATE.status()
        after = int(params.get("after") or 0)
        slug = params.get("slug", "")
        # Untagged replies are global; tagged ones only reach their report.
        # A slug-less status (the CLI) sees everything.
        out["replies"] = [
            r for r in out["replies"]
            if r["n"] > after and (not slug or not r.get("slug") or r["slug"] == slug)
        ]
        out["data_version"] = _data_version(served_dir, slug)
        _send_json(handler, 200, out)
        return True

    if path == f"{_REVIEW_PREFIX}/start" and method == "POST":
        if _read_body(handler) is None:
            return True
        _send_json(handler, 200, {"ok": True, "session": STATE.start()})
        return True

    if path == f"{_REVIEW_PREFIX}/feedback" and method == "POST":
        body = _read_body(handler)
        if body is None:
            return True
        batch = {
            "slug": str(body.get("slug") or ""),
            "page": str(body.get("page") or ""),
            "session": body.get("session"),
            "note": str(body.get("note") or ""),
            "items": body.get("items") or [],
            "ts": time.time(),
        }
        if not isinstance(batch["items"], list):
            _send_json(handler, 400, {"error": "items must be a list"})
            return True
        try:
            queued = STATE.submit(batch, end_session=bool(body.get("end_session")))
        except ReviewInactive:
            _send_json(handler, 409, {"error": "review not active"})
            return True
        _send_json(handler, 200, {"ok": True, "queued": queued})
        return True

    if path == f"{_REVIEW_PREFIX}/poll" and method == "GET":
        params = _query_params(handler.path)
        try:
            timeout = float(params.get("timeout") or _MAX_POLL_SECONDS)
        except ValueError:
            timeout = _MAX_POLL_SECONDS
        status, batches = STATE.poll(timeout, slug=params.get("slug", ""))
        out: dict = {"status": status}
        if status == "feedback":
            out["session"] = STATE.session
            out["batches"] = batches
            out["session_ended"] = STATE.ended
        _send_json(handler, 200, out)
        return True

    if path == f"{_REVIEW_PREFIX}/last" and method == "GET":
        batches = STATE.last()
        if batches:
            _send_json(handler, 200, {"status": "feedback", "batches": batches})
        else:
            _send_json(handler, 200, {"status": "empty"})
        return True

    if path == f"{_REVIEW_PREFIX}/reply" and method == "POST":
        body = _read_body(handler)
        if body is None:
            return True
        text = str(body.get("text") or "").strip()
        if not text:
            _send_json(handler, 400, {"error": "text required"})
            return True
        n = STATE.reply(text, slug=str(body.get("slug") or ""))
        _send_json(handler, 200, {"ok": True, "n": n})
        return True

    if path == f"{_REVIEW_PREFIX}/end" and method == "POST":
        if _read_body(handler) is None:
            return True
        STATE.end()
        _send_json(handler, 200, {"ok": True})
        return True

    _send_json(handler, 404, {"error": f"unknown review endpoint {path}"})
    return True
