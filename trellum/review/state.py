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
            self.queue.append(batch)
            if end_session:
                self.ended = True
                self.analysis_slug = ""
                self.analysis_token = ""
            self._save_locked()
            self._cond.notify_all()
            return len(self.queue)

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
            analysis_target(project_root, self.analysis_slug)
            capture_id = store_capture(project_root, self.analysis_slug, capture)
            source = capture["source"]
            self.analysis_requests[request_id] = {"capture_id": capture_id,
                                                  "fingerprint": fingerprint}
            self.queue.append({"slug": self.analysis_slug, "session": self.session,
                "page": "", "note": note, "ts": time.time(), "items": [{
                    "tag": "analysis-capture", "data": {"type": "analysis-capture",
                    "capture_id": capture_id,
                    "source_slug": (source["report_slug"] or "")[:100],
                    "component_title": (source["component_title"] or "")[:200]}}]})
            self._save_locked()
            self._cond.notify_all()
            return capture_id, False

    def status(self) -> dict:
        with self._cond:
            if self.listening_count > 0:
                agent = "listening"
            elif self.agent_working:
                agent = "working"
            else:
                agent = "absent"
            seen_ms = (
                int((time.monotonic() - self.agent_seen) * 1000)
                if self.agent_seen is not None
                else None
            )
            return {
                "active": self.enabled and not self.ended,
                "ended": self.ended,
                "session": self.session,
                "agent": {"state": agent, "seen_ms_ago": seen_ms},
                "queued": len(self.queue),
                "replies": list(self.replies),
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
            try:
                self._cond.wait_for(lambda: _mine() or self.ended, timeout)
            finally:
                self.listening_count -= 1
                self.agent_seen = time.monotonic()

            mine = _mine()
            if mine:
                for batch in mine:
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

    def reply(self, text: str, slug: str = "") -> int:
        """Record an agent reply; shown in the browser's chat.

        A slug-tagged reply reaches only that report's pages; an untagged
        one is global (single-agent sessions never need the tag).
        """
        with self._cond:
            n = len(self.replies) + 1
            self.replies.append({"n": n, "text": text, "slug": slug, "ts": time.time()})
            self.agent_working = False
            self.agent_seen = time.monotonic()
            self._save_locked()
            self._cond.notify_all()
            return n

class ReviewInactive(Exception):
    """Feedback arrived while review mode is off or already ended."""

STATE = ReviewState()
