"""Log formatting, and the request id that makes logs joinable.

A single report build crosses four processes: the web container accepts the
request, a row lands in `Run`, the worker claims it, and a sandbox container
actually runs the tenant's code. Until now each of those printed to its own
stdout with no shared identifier, so "what happened to this build" meant
eyeballing timestamps across containers.

Two pieces:

* ``RequestIdFilter`` attaches the current request id to every record, so the
  formatter can print it without every call site remembering to.
* ``JsonFormatter`` for anyone shipping logs somewhere that parses them. Text
  stays the default: the single-VM install reads its logs with `docker compose
  logs`, and JSON there is worse for the human it is for.
"""
from __future__ import annotations

import contextvars
import json
import logging

#: The id of the request being served on this thread/task, if any. A ContextVar
#: rather than thread-local because it survives async and is cleaned up per
#: context — a leaked id would mislabel unrelated work.
current_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trellum_request_id", default=""
)

#: Never logged, whatever a formatter is asked to include.
_NEVER_LOG = {"password", "token", "secret", "authorization", "cookie", "api_key"}


class RequestIdFilter(logging.Filter):
    """Puts ``request_id`` on every record, empty when outside a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Deliberately hand-rolled rather than a dependency: it is thirty lines, and
    the alternative is another package in an image where every package is CVE
    surface a customer's scanner will ask about.
    """

    #: LogRecord attributes that are plumbing, not content.
    _SKIP = {
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "module", "msecs", "message", "msg",
        "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "")
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed as `extra=` — run ids, studio slugs, durations.
        for key, value in record.__dict__.items():
            if key in self._SKIP or key in payload or key.startswith("_"):
                continue
            # The filter sets this on every record; outside a request it is
            # empty, and an empty field on every worker log line is noise.
            if key == "request_id" and not value:
                continue
            if key.lower() in _NEVER_LOG:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        return json.dumps(payload, default=str)
