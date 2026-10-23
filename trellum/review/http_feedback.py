"""Agent observations and feedback admission for the local review HTTP API."""

from __future__ import annotations

import re
import time

from trellum.review import http as transport
from trellum.review.state import ReviewInactive


def submit_feedback(handler) -> bool:
    body = transport._read_body(handler)
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
        transport._send_json(handler, 400, {"error": "items must be a list"})
        return True
    try:
        request_id = body.get("request_id")
        if request_id is not None and (not isinstance(request_id, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", request_id)):
            raise ValueError("A request ID of 16–100 characters is required")
        if request_id:
            queued, duplicate = transport.STATE.submit_once(batch, request_id, bool(body.get("end_session")))
        else:
            queued, duplicate = transport.STATE.submit(batch, bool(body.get("end_session"))), False
    except ReviewInactive:
        transport._send_json(handler, 409, {"error": "review not active"})
        return True
    except FileExistsError as exc:
        transport._send_json(handler, 409, {"error": str(exc)})
        return True
    except ValueError as exc:
        transport._send_json(handler, 400, {"error": str(exc)})
        return True
    transport._send_json(handler, 200, {"ok": True, "queued": queued,
                                "feedback_id": batch["feedback_id"], "duplicate": duplicate})
    return True


def update_activity(handler) -> bool:
    if not transport._analysis_origin_allowed(handler):
        transport._send_json(handler, 403, {"error": "activity requires an allowed local origin"})
        return True
    body = transport._read_body(handler, 8192)
    if body is None:
        return True
    try:
        if any(not isinstance(body.get(key), str) for key in ("slug", "feedback_id", "state", "summary")):
            raise ValueError("Activity requires slug, feedback_id, state and summary strings")
        transport.STATE.activity(body.get("session"), body["slug"], body["feedback_id"],
                                 body["state"], body["summary"])
    except ValueError as exc:
        transport._send_json(handler, 409, {"error": str(exc)})
        return True
    transport._send_json(handler, 200, {"ok": True})
    return True


def reply(handler) -> bool:
    body = transport._read_body(handler)
    if body is None:
        return True
    text = str(body.get("text") or "").strip()
    if not text:
        transport._send_json(handler, 400, {"error": "text required"})
        return True
    try:
        n = transport.STATE.reply(text, slug=str(body.get("slug") or ""),
                                  feedback_id=str(body.get("feedback_id") or ""),
                                  session=body.get("session"))
    except ValueError as exc:
        transport._send_json(handler, 409, {"error": str(exc)})
        return True
    transport._send_json(handler, 200, {"ok": True, "n": n})
    return True
