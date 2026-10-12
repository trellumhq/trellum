"""The /_fw/review* endpoints the browser overlay talks to."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from trellum.review.inject import overlay_js
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

def handle_review_request(handler, served_dir: str) -> bool:
    """Answer a ``/_fw/review*`` request. Returns False for any other path."""
    path = handler.path.split("?")[0]
    if not path.startswith(_REVIEW_PREFIX):
        return False
    if not _is_loopback(handler):
        _send_json(handler, 403, {"error": "review endpoints are loopback-only"})
        return True
    STATE.bind_dir(served_dir)
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
