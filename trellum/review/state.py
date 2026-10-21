"""The review session: one per server process, shared by handler threads."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time

#: Upper bound on a blocking poll, so a client cannot pin a handler
#: thread indefinitely.
_MAX_POLL_SECONDS = 30.0
_PRESENCE_SECONDS = 90
_MAX_ACTIVITY = 120
_TERMINAL_ACTIVITY = {"completed", "failed"}


class ReviewState:
    """One review session per server process, shared by all handler threads.

    The durable core (session id, queue, replies, ended flag) also lives in
    a sidecar file in the served directory: dev servers get replaced -- by a
    second agent session in the same folder, a --restart-server, a crash --
    and a review session that died with its process burned a user mid-test.
    The successor process loads the sidecar on its first review request.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._dir: str | None = None
        self._reset_locked()

    def _reset_locked(self) -> None:
        self.enabled = False
        self.ended = False
        self.session = 0
        self.queue: list[dict] = []
        self.replies: list[dict] = []
        self.agent_working = False
        self.agent_seen: float | None = None
        self.listening_count = 0
        self.last_delivered: list[dict] | None = None
        self.analysis_slug = ""
        self.analysis_token = ""
        self.analysis_requests: dict[str, dict] = {}
        self.workspace_requests: dict[str, dict] = {}
        self.workspace_captures: dict[str, dict] = {}
        self.workspace_feedback: dict[str, dict] = {}
        self.workspace_builds: dict[str, dict] = {}
        self.activities: dict[str, dict] = {}
        self.feedback_requests: dict[str, dict] = {}
        self.agent_scopes: dict[str, dict] = {}

    def reset_for_tests(self) -> None:
        with self._cond:
            self._reset_locked()
            if self._dir:
                try:
                    os.unlink(self._sidecar())
                except OSError:
                    pass

    # ── persistence across server replacement ─────────────────

    def _sidecar(self) -> str:
        return os.path.join(self._dir or ".", ".review-state.json")

    def bind_dir(self, served_dir: str) -> None:
        """First review request of this process: adopt the predecessor's
        session if one was persisted for this output directory."""
        with self._cond:
            if self._dir is not None:
                return
            self._dir = served_dir
            try:
                with open(self._sidecar(), encoding="utf-8") as fh:
                    saved = json.load(fh)
            except (OSError, ValueError):
                return
            self.enabled = bool(saved.get("enabled"))
            self.ended = bool(saved.get("ended"))
            self.session = int(saved.get("session") or 0)
            self.queue = list(saved.get("queue") or [])
            self.replies = list(saved.get("replies") or [])
            self.last_delivered = saved.get("last_delivered")
            self.analysis_slug = str(saved.get("analysis_slug") or "")
            self.analysis_token = str(saved.get("analysis_token") or "")
            self.analysis_requests = dict(saved.get("analysis_requests") or {})
            self.workspace_requests = dict(saved.get("workspace_requests") or {})
            self.workspace_captures = dict(saved.get("workspace_captures") or {})
            self.workspace_feedback = dict(saved.get("workspace_feedback") or {})
            self.workspace_builds = dict(saved.get("workspace_builds") or {})
            self.activities = dict(saved.get("activities") or {})
            self.feedback_requests = dict(saved.get("feedback_requests") or {})
            self._cond.notify_all()

    def _save_locked(self) -> None:
        if self._dir is None:
            return
        payload = json.dumps({
            "enabled": self.enabled,
            "ended": self.ended,
            "session": self.session,
            "queue": self.queue,
            "replies": self.replies,
            "last_delivered": self.last_delivered,
            "analysis_slug": self.analysis_slug,
            "analysis_token": self.analysis_token,
            "analysis_requests": self.analysis_requests,
            "workspace_requests": self.workspace_requests,
            "workspace_captures": self.workspace_captures,
            "workspace_feedback": self.workspace_feedback,
            "workspace_builds": self.workspace_builds,
            "activities": self.activities,
            "feedback_requests": self.feedback_requests,
        })
        try:
            temporary = self._sidecar() + ".tmp"
            with open(temporary, "w", encoding="utf-8") as fh:
                os.chmod(temporary, 0o600)
                fh.write(payload)
            os.replace(temporary, self._sidecar())
        except OSError:
            pass                              # persistence is best-effort

    # ── session lifecycle ─────────────────────────────────────

    def start(self) -> int:
        """Enable review mode. Idempotent while a session is active; a start
        after an end (or the first ever) begins a fresh session."""
        with self._cond:
            if self.enabled and not self.ended:
                return self.session
            session = self.session + 1
            self._reset_locked()
            self.enabled = True
            self.session = session
            self._save_locked()
            return self.session

    def end(self) -> None:
        with self._cond:
            if not self.enabled:
                return
            self.ended = True
            self.analysis_slug = ""
            self.analysis_token = ""
            self._save_locked()
            self._cond.notify_all()

    # ── browser side ──────────────────────────────────────────

    def submit(self, batch: dict, end_session: bool = False) -> int:
        """Queue one feedback batch. Returns total queued batches."""
        with self._cond:
            if not self.enabled or self.ended:
                raise ReviewInactive
            if batch.get("session") not in (None, self.session):
                raise ValueError("Review session changed")
            batch["session"] = self.session
            self._track_locked(batch)
            self.queue.append(batch)
            if end_session:
                self.ended = True
                self.analysis_slug = ""
                self.analysis_token = ""
            self._save_locked()
            self._cond.notify_all()
            return len(self.queue)

    def _track_locked(self, batch: dict) -> None:
        """Keep one bounded observation per feedback, never discard active work."""
        feedback_id = batch.setdefault("feedback_id", secrets.token_urlsafe(18))
        if feedback_id in self.activities:
            return
        self._check_activity_capacity_locked()
        while len(self.activities) >= _MAX_ACTIVITY:
            oldest = next((key for key, row in self.activities.items()
                           if row["state"] in _TERMINAL_ACTIVITY), None)
            del self.activities[oldest]
        now = time.time()
        count = len(batch.get("items") or [])
        text = str(batch.get("note") or "").strip()
        prompts = [str(item.get("prompt") or "")[:500]
                   for item in (batch.get("items") or [])[:4] if isinstance(item, dict)]
        if count > 4:
            prompts.append(f"… and {count - 4} more change requests.")
        self.activities[feedback_id] = {
            "feedback_id": feedback_id, "slug": batch.get("slug", ""),
            "state": "queued", "summary": "", "submitted_at": now,
            "updated_at": now, "picked_up_at": None, "completed_at": None,
            "text": (text + ("\n" if text and prompts else "") + "\n".join(prompts))[:4000]
                    or f"Sent {count} change request{'s' if count != 1 else ''}.",
        }

    def _check_activity_capacity_locked(self) -> None:
        if len(self.activities) >= _MAX_ACTIVITY and not any(
                row["state"] in _TERMINAL_ACTIVITY for row in self.activities.values()):
            raise ValueError("Review activity limit reached; finish pending feedback first")

    def submit_once(self, batch: dict, request_id: str, end_session: bool = False) -> tuple[int, bool]:
        """Idempotent standalone submission; uncertain sends can safely reconnect."""
        with self._cond:
            fingerprint = hashlib.sha256(json.dumps(
                {k: v for k, v in batch.items() if k not in ("ts", "feedback_id")},
                sort_keys=True).encode()).hexdigest()
            fingerprint += str(end_session)
            previous = self.feedback_requests.get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise FileExistsError("Request ID already used for different feedback")
                batch["feedback_id"] = previous["feedback_id"]
                return len(self.queue), True
            if len(self.feedback_requests) >= 10000:
                raise ValueError("Feedback limit reached; start a new session")
            batch["feedback_id"] = request_id
            queued = self.submit(batch, end_session)
            self.feedback_requests[request_id] = {
                "fingerprint": fingerprint, "feedback_id": batch["feedback_id"]}
            self._save_locked()
            return queued, False

    def _touch_agent_locked(self, slug: str) -> dict:
        if slug not in self.agent_scopes:
            if len(self.agent_scopes) >= _MAX_ACTIVITY:
                oldest = min(self.agent_scopes, key=lambda k: self.agent_scopes[k]["seen"])
                del self.agent_scopes[oldest]
            self.agent_scopes[slug] = {"seen": time.monotonic(), "listening": 0}
        self.agent_scopes[slug]["seen"] = time.monotonic()
        return self.agent_scopes[slug]

    def activity(self, session: int, slug: str, feedback_id: str,
                 state: str, summary: str) -> None:
        """An explicit observation, never a reply or completion acknowledgement."""
        if state not in {"working", "rebuilding", "waiting", "failed"}:
            raise ValueError("Activity state must be working, rebuilding, waiting or failed")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
            raise ValueError("Activity summary must contain 1–500 characters")
        with self._cond:
            row = self.activities.get(feedback_id)
            if type(session) is not int or session != self.session or not row or row["slug"] != slug:
                raise ValueError("Activity does not match this session, report and feedback")
            if row["state"] == "queued" or (row["state"] in _TERMINAL_ACTIVITY
                    and (row["state"] != state or row["summary"] != summary.strip())):
                raise ValueError("Activity requires picked-up, unfinished feedback")
            if row["state"] != state or row["summary"] != summary.strip():
                row.update(state=state, summary=summary.strip(), updated_at=time.time())
                if state == "failed":
                    row["completed_at"] = row["updated_at"]
            self._touch_agent_locked(slug)
            self._save_locked()
            self._cond.notify_all()

    def start_analysis(self, slug: str) -> dict:
        self.start()
        with self._cond:
            if slug != self.analysis_slug or not self.analysis_token:
                self.analysis_slug = slug
                self.analysis_token = secrets.token_urlsafe(32)
                self.analysis_requests = {}
                self.workspace_requests = {}
                self.workspace_captures = {}
                self.workspace_feedback = {}
                self._save_locked()
            return self.analysis_status()

    def analysis_status(self) -> dict:
        with self._cond:
            active = self.enabled and not self.ended and bool(self.analysis_slug)
            return {"active": active, "slug": self.analysis_slug if active else "",
                    "token": self.analysis_token if active else "",
                    "session": self.session if active else 0}

    def end_analysis(self) -> None:
        with self._cond:
            self.analysis_slug = ""
            self.analysis_token = ""
            self._save_locked()
            self._cond.notify_all()

    def submit_capture(self, project_root: str, token: str, request_id: str,
                       capture: dict, note: str) -> tuple[str, bool]:
        from trellum.analysis_inbox import analysis_target, store_capture

        with self._cond:
            if not self.enabled or self.ended or not self.analysis_slug:
                raise ReviewInactive
            if not secrets.compare_digest(token, self.analysis_token):
                raise PermissionError("Invalid analysis session token")
            fingerprint = hashlib.sha256(json.dumps(
                {"capture": capture, "note": note}, sort_keys=True,
                allow_nan=False).encode("utf-8")).hexdigest()
            previous = self.analysis_requests.get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise FileExistsError("Request ID already used for a different capture")
                return previous["capture_id"], True
            # Bound session bookkeeping without silently forgetting duplicate requests.
            if len(self.analysis_requests) >= 10000:
                raise ValueError("Analysis session capture limit reached; start a new session")
            self._check_activity_capacity_locked()
            analysis_target(project_root, self.analysis_slug)
            capture_id = store_capture(project_root, self.analysis_slug, capture)
            source = capture["source"]
            self.analysis_requests[request_id] = {"capture_id": capture_id,
                                                  "fingerprint": fingerprint}
            batch = {"slug": self.analysis_slug, "session": self.session,
                "page": "", "note": note, "ts": time.time(), "items": [{
                    "tag": "analysis-capture", "data": {"type": "analysis-capture",
                    "capture_id": capture_id,
                    "source_slug": (source["report_slug"] or "")[:100],
                    "component_title": (source["component_title"] or "")[:200]}}]}
            self._track_locked(batch)
            self.queue.append(batch)
            self._save_locked()
            self._cond.notify_all()
            return capture_id, False

    def status(self, slug: str = "") -> dict:
        with self._cond:
            scopes = [value for key, value in self.agent_scopes.items()
                      if not slug or key in ("", slug)]
            seen = max((value["seen"] for value in scopes), default=None)
            seen_ms = int((time.monotonic() - seen) * 1000) if seen is not None else None
            rows = [row for row in self.activities.values() if not slug or row["slug"] == slug]
            activities = []
            for row in rows:
                current = dict(row)
                row_scopes = [self.agent_scopes[key] for key in ("", row["slug"])
                              if key in self.agent_scopes]
                row_fresh = any(time.monotonic() - value["seen"] <= _PRESENCE_SECONDS
                                for value in row_scopes)
                current["display_state"] = (
                    "ended" if self.ended and row["state"] not in _TERMINAL_ACTIVITY else
                    "unknown" if row["state"] in {"picked_up", "working", "rebuilding"} and not row_fresh
                    else row["state"])
                activities.append(current)
            if any(value["listening"] and time.monotonic() - value["seen"] <= _PRESENCE_SECONDS
                   for value in scopes):
                agent = "listening"
            elif any(row["display_state"] in {"picked_up", "working", "rebuilding"}
                     for row in activities):
                agent = "working"
            else:
                agent = "absent"
            return {
                "active": self.enabled and not self.ended,
                "ended": self.ended,
                "session": self.session,
                "agent": {"state": agent, "seen_ms_ago": seen_ms},
                "queued": sum(1 for batch in self.queue if not slug or batch.get("slug") == slug),
                "replies": list(self.replies),
                "activities": activities,
            }

    # ── agent side ────────────────────────────────────────────

    def poll(self, timeout: float, slug: str = "") -> tuple[str, list[dict] | None]:
        """Blocking read: ("feedback", batches) | ("waiting", None) | ("ended", None).

        Feedback is a destructive read (the queue drains), but the drained
        batches stay in ``last_delivered`` for crash recovery. Delivering the
        final batch of a Send & End reports feedback first; the NEXT poll
        returns ended and disables the session.

        ``slug`` narrows the read to one report's batches, which is what
        lets several agents share one project: each polls its own report
        and never steals a sibling's feedback.
        """
        timeout = max(0.0, min(timeout, _MAX_POLL_SECONDS))

        def _mine():
            return [b for b in self.queue if not slug or b.get("slug") == slug]

        with self._cond:
            self.agent_seen = time.monotonic()
            self.listening_count += 1
            scope = self._touch_agent_locked(slug)
            scope["listening"] += 1
            try:
                self._cond.wait_for(lambda: _mine() or self.ended, timeout)
            finally:
                self.listening_count -= 1
                self.agent_seen = time.monotonic()
                scope["listening"] -= 1
                scope["seen"] = time.monotonic()

            mine = _mine()
            if mine:
                for batch in mine:
                    self._track_locked(batch)
                    self.activities[batch["feedback_id"]].update(
                        state="picked_up", picked_up_at=time.time(), updated_at=time.time())
                    self.queue.remove(batch)
                self.last_delivered = mine
                self.agent_working = True
                self._save_locked()
                self._cond.notify_all()
                return "feedback", mine
            if self.ended:
                self.enabled = False
                self._save_locked()
                return "ended", None
            return "waiting", None

    def last(self) -> list[dict] | None:
        with self._cond:
            return self.last_delivered

    def reply(self, text: str, slug: str = "", feedback_id: str = "") -> int:
        """Record an agent reply; shown in the browser's chat.

        A slug-tagged reply reaches only that report's pages; an untagged
        one is global (single-agent sessions never need the tag).
        """
        with self._cond:
            if feedback_id and (feedback_id not in self.activities
                    or self.activities[feedback_id]["slug"] != slug):
                raise ValueError("Reply does not match this report and feedback")
            if feedback_id and self.activities[feedback_id]["state"] == "queued":
                raise ValueError("Reply requires picked-up feedback")
            completed = []
            for row in self.activities.values():
                if ((not slug or row["slug"] == slug)
                        and (not feedback_id or row["feedback_id"] == feedback_id)
                        and row["state"] not in _TERMINAL_ACTIVITY | {"queued"}):
                    row.update(state="completed", summary="", completed_at=time.time(), updated_at=time.time())
                    completed.append(row["feedback_id"])
            n = len(self.replies) + 1
            self.replies.append({"n": n, "text": text, "slug": slug, "ts": time.time(),
                                 "feedback_ids": completed})
            self.agent_working = False
            self.agent_seen = time.monotonic()
            self._touch_agent_locked(slug)
            self._save_locked()
            self._cond.notify_all()
            return n

class ReviewInactive(Exception):
    """Feedback arrived while review mode is off or already ended."""

STATE = ReviewState()
