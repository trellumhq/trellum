"""The /_fw/review* endpoints the browser overlay talks to."""

from __future__ import annotations

import json
import os
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

def _read_body(handler) -> dict | None:
    """Parse the JSON request body; None means an error was already sent."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = 0
    if length > _MAX_BODY_BYTES:
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
