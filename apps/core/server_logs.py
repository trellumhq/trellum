"""Best-effort shared diagnostics. Console output remains the fallback.

Each process owns a bounded queue and daemon writer with a separate connection;
request transactions never contain these inserts. Text scrubbing covers common
credential formats, not arbitrary secrets embedded in free text.
"""
from __future__ import annotations

import datetime
import json
import logging
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import uuid
from itertools import islice

from django.apps import apps
from django.conf import settings

MESSAGE_LIMIT = 8192
EXCEPTION_LIMIT = 16384
QUEUE_LIMIT = 256
BATCH_LIMIT = 100
CONTEXT_KEYS = {
    "reason", "status", "exit_code", "duration_seconds", "memory_limit_mb",
    "memory_cap_mb", "memory_cap_kind", "peak_memory_mb", "timeout_seconds",
    "container_id", "studio", "org", "signal", "source", "run_id",
    "worker_id", "slug", "report_slug", "trigger",
    "memory_limit_kind", "memory_allocation_mb", "max_concurrent",
    "memory_budget_mb", "sandbox_mode", "role", "active_run_ids",
    "memory_headroom",
}
_SECRET_KEY = re.compile(r"password|passwd|token|secret|api[_-]?key|authorization|cookie", re.I)
_CREDENTIAL = re.compile(
    r'''(?i)(["']?\b(?:password|passwd|token|access_token|refresh_token|secret|api[_-]?key|authorization)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}]+)'''
)
_BEARER = re.compile(r"(?i)\bBearer\s+[a-z0-9._~+/=-]+")
_URL_AUTH = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/\s@]+@", re.I)
_AUTH_HEADER = re.compile(r"(?i)(\bauthorization\s*[:=]\s*)(?:Basic|Bearer)\s+[^\s,;]+")
_COOKIE_HEADER = re.compile(r"(?i)(\b(?:set-cookie|cookie)\s*[:=]\s*)[^\r\n]+")


def redact_text(text: str) -> str:
    text = _AUTH_HEADER.sub(r"\1[redacted]", str(text))
    text = _COOKIE_HEADER.sub(r"\1[redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", str(text))
    text = _URL_AUTH.sub(r"\1[redacted]@", text)
    return _CREDENTIAL.sub(r"\1[redacted]", text)


def bounded_text(value, limit: int) -> str:
    text = str(value)
    truncated = len(text) > limit
    text = redact_text(text[:limit])
    marker = "\n[truncated]"
    return text if not truncated and len(text) <= limit else text[:limit - len(marker)] + marker


def _safe_value(value, depth=0):
    if depth > 3:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            bounded_text(k, 80): _safe_value(v, depth + 1)
            for k, v in islice(value.items(), 16)
            if isinstance(k, str) and not _SECRET_KEY.search(k)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(v, depth + 1) for v in value[:16]]
    if value is None or isinstance(value, (bool, int)):
        return value if not isinstance(value, int) or abs(value) < 10**30 else str(value)[:30]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, uuid.UUID)):
        return bounded_text(value, 1000)
    return "[unsupported value]"


def safe_context(context: dict) -> dict:
    result = {}
    for key in sorted(CONTEXT_KEYS.intersection(context)):
        value = _safe_value(context[key])
        if len(json.dumps({**result, key: value})) > 8192:
            result["truncated"] = True
            break
        result[key] = value
    return result


def service_name() -> str:
    override = getattr(settings, "SERVER_LOG_SERVICE", "")
    if override:
        return bounded_text(override, 32)
    args = " ".join(sys.argv).lower()
    if "runworker" in args and "--role" not in args:
        role = getattr(settings, "TRELLUM_RUNNER_ROLE", "all")
        if role in ("runner", "coordinator"):
            return role
    if "coordinator" in args:
        return "coordinator"
    if "--role runner" in args or "--role=runner" in args:
        return "runner"
    if "worker" in args:
        return "worker"
    if any(name in args for name in ("gunicorn", "uvicorn", "runserver", "wsgi", "asgi")):
        return "web"
    return "management"


def event_from_record(record: logging.LogRecord) -> dict:
    def field(name, limit):
        value = getattr(record, name, "")
        return bounded_text(value, limit) if isinstance(value, (str, uuid.UUID, int)) else ""

    run_id = field("run_id", 100)
    try:
        run_id = str(uuid.UUID(run_id)) if run_id else ""
    except (ValueError, TypeError):
        run_id = ""
    exception = logging.Formatter().formatException(record.exc_info) if record.exc_info else ""
    host = socket.gethostname()[:255]
    worker_id = field("worker_id", 100)
    if not worker_id and not hasattr(record, "worker_id") and "runworker" in sys.argv:
        worker_id = f"{host}-{os.getpid()}"[:100]
    return {
        "timestamp": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc),
        "level": record.levelname[:16], "service": service_name(),
        "host": host, "process": os.getpid(),
        "logger": record.name[:200], "message": bounded_text(record.getMessage(), MESSAGE_LIMIT),
        "exception": bounded_text(exception, EXCEPTION_LIMIT),
        "context": safe_context(record.__dict__), "run_id": run_id,
        "worker_id": worker_id,
        "report_slug": field("report_slug", 200) or field("slug", 200),
        "request_id": field("request_id", 200), "trigger": field("trigger", 32),
    }


def prune_events(model):
    """Called only by the isolated writer, inside its batch transaction."""
    from django.utils import timezone

    days = max(1, int(settings.SERVER_LOG_RETENTION_DAYS))
    maximum = max(1, int(settings.SERVER_LOG_MAX_ROWS))
    model.objects.filter(timestamp__lt=timezone.now() - datetime.timedelta(days=days)).delete()
    cutoff = list(model.objects.order_by("-id").values_list("id", flat=True)[maximum - 1:maximum])
    if cutoff:
        model.objects.filter(id__lt=cutoff[0]).delete()


class DatabaseLogHandler(logging.Handler):
    """Never blocks a logging caller, and never logs its own database errors."""

    def __init__(self):
        super().__init__()
        self._reset()
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._reset)

    def _reset(self):
        self._pid = os.getpid()
        self._queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self._start_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._dropped = 0
        self._last_fallback = 0.0
        # Python's handler lock can also be inherited while held at fork.
        self.createLock()

    def handle(self, record):
        # No shared Handler lock: emit only uses nonblocking queue operations.
        try:
            if self.filter(record):
                self.emit(record)
        except Exception:
            self._drop()
        return True

    def _drop(self, count=1):
        self._dropped += count
        now = time.monotonic()
        if now - self._last_fallback >= 60:
            self._last_fallback = now
            try:
                sys.stderr.write("Trellum server log capture dropped events; console output remains available.\n")
            except Exception:
                pass

    def emit(self, record):
        if not getattr(settings, "SERVER_LOG_CAPTURE_ENABLED", False) or not apps.ready:
            return
        if self._pid != os.getpid():
            self._reset()
        if threading.current_thread() is self._thread or self._stop.is_set():
            return
        try:
            event = event_from_record(record)
            self._queue.put_nowait(event)
            if self._thread is None and self._start_lock.acquire(blocking=False):
                try:
                    if self._thread is None:
                        self._thread = threading.Thread(target=self._consume, name="server-log-writer", daemon=True)
                        try:
                            self._thread.start()
                        except Exception:
                            self._thread = None
                            count = 0
                            while True:
                                try:
                                    self._queue.get_nowait()
                                    self._queue.task_done()
                                    count += 1
                                except queue.Empty:
                                    break
                            self._drop(count)
                finally:
                    self._start_lock.release()
        except Exception:
            self._drop()

    def _write_batch(self, batch):
        from django.db import connection, transaction
        from apps.core.models import ServerLogEvent

        with transaction.atomic():
            # Serialize retention across hosts, so concurrent batches obey the cap.
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(1940014)")
            ServerLogEvent.objects.bulk_create([ServerLogEvent(**event) for event in batch])
            prune_events(ServerLogEvent)

    def _consume(self):
        from django.db import connections, close_old_connections

        try:
            while not self._stop.is_set() or not self._queue.empty():
                batch = []
                try:
                    batch.append(self._queue.get(timeout=0.5))
                except queue.Empty:
                    continue
                deadline = time.monotonic() + 0.5
                while len(batch) < BATCH_LIMIT - 1 and time.monotonic() < deadline:
                    try:
                        batch.append(self._queue.get(timeout=max(0.001, deadline - time.monotonic())))
                    except queue.Empty:
                        break
                dropped = self._dropped
                if dropped:
                    warning = logging.LogRecord("trellum.server_logs", logging.WARNING, "", 0,
                                                "Server log capture dropped %s events; console output is the fallback.",
                                                (dropped,), None)
                    batch.append(event_from_record(warning))
                try:
                    close_old_connections()
                    self._write_batch(batch)
                    self._dropped = max(0, self._dropped - dropped)
                except Exception:
                    self._drop(len(batch) - bool(dropped))
                    connections.close_all()
                finally:
                    for _ in range(len(batch) - bool(dropped)):
                        self._queue.task_done()
        finally:
            connections.close_all()

    def flush(self):
        if getattr(self, "_thread", None) is None or threading.current_thread() is self._thread:
            return
        deadline = time.monotonic() + 2
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    def close(self):
        self._stop.set()
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        super().close()
