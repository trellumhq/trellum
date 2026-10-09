import logging
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import ServerLogEvent
from apps.core.server_logs import (
    DatabaseLogHandler, MESSAGE_LIMIT, QUEUE_LIMIT, bounded_text,
    event_from_record, redact_text, safe_context, service_name,
)


def record(message="hello", **extras):
    value = logging.LogRecord("runner", logging.INFO, "", 0, message, (), None)
    value.__dict__.update(extras)
    return value


@pytest.mark.parametrize("text", [
    "password=hunter2 token=abc api_key=def",
    '"password": "hunter2", "token": "abc"',
    "Authorization: Bearer abc.def",
    "https://user:hunter2@example.com/path",
    "https://hunter2@example.com/path",
    "Authorization: Basic hunter2",
    "Cookie: session=hunter2; token=abc",
    "Set-Cookie: session=hunter2; Secure; HttpOnly",
])
def test_credentials_scrubbed(text):
    result = redact_text(text)
    assert "hunter2" not in result and "abc" not in result and "def" not in result
    assert "[redacted]" in result


def test_context_allowlist_nested_secret_removal_and_bounds():
    context = safe_context({
        "reason": {"password": "hunter2", "note": "token=abc", "more": ["api_key=def"]},
        "headers": {"Cookie": "sensitive"}, "status": "x" * 5000,
        "memory_budget_mb": 500, "active_run_ids": [uuid.uuid4()],
    })
    assert "headers" not in context and "password" not in context["reason"]
    assert context["reason"]["note"] == "token=[redacted]"
    assert len(context["status"]) == 1000
    assert context["memory_budget_mb"] == 500
    assert len(bounded_text("x" * 20_000, MESSAGE_LIMIT)) == MESSAGE_LIMIT
    assert "[truncated]" in bounded_text("x" * 20_000, MESSAGE_LIMIT)


def test_record_correlation_validation_and_exception():
    run_id = uuid.uuid4()
    value = event_from_record(record(run_id=run_id, worker_id="w1", slug="alpha", request_id="req"))
    assert value["run_id"] == str(run_id) and value["report_slug"] == "alpha"
    assert value["worker_id"] == "w1" and value["request_id"] == "req"
    assert event_from_record(record(run_id="invalid", worker_id={"x": 1}))["run_id"] == ""
    try:
        raise ValueError("password=hunter2")
    except ValueError:
        import sys
        logged = record()
        logged.exc_info = sys.exc_info()
    assert "hunter2" not in event_from_record(logged)["exception"]


def test_service_inference(settings, monkeypatch):
    settings.SERVER_LOG_SERVICE = ""
    monkeypatch.setattr("sys.argv", ["manage.py", "runworker", "--role", "runner"])
    assert service_name() == "runner"
    assert event_from_record(record())["worker_id"]
    assert event_from_record(record(worker_id="explicit"))["worker_id"] == "explicit"
    settings.SERVER_LOG_SERVICE = "custom"
    assert service_name() == "custom"


def test_disabled_handler_never_starts(settings):
    settings.SERVER_LOG_CAPTURE_ENABLED = False
    handler = DatabaseLogHandler()
    handler.handle(record())
    assert handler._thread is None and handler._queue.empty()
    handler.close()


def test_queue_bounds_fail_open_and_process_reset(settings, monkeypatch):
    settings.SERVER_LOG_CAPTURE_ENABLED = True
    handler = DatabaseLogHandler()
    # A running placeholder keeps this deterministic without a database thread.
    handler._thread = object()
    for _ in range(QUEUE_LIMIT + 5):
        handler.handle(record("x" * 20_000))
    assert handler._queue.qsize() == QUEUE_LIMIT and handler._dropped == 5
    assert len(handler._queue.get_nowait()["message"]) == MESSAGE_LIMIT
    handler._pid = -1
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    handler.handle(record("child"))
    assert handler._queue.qsize() == 1 and handler._pid != -1
    handler._thread = None
    handler.close()


def test_formatter_failure_does_not_escape(settings):
    settings.SERVER_LOG_CAPTURE_ENABLED = True
    handler = DatabaseLogHandler()
    broken = record()
    broken.getMessage = lambda: (_ for _ in ()).throw(ValueError("bad format"))
    handler.handle(broken)
    assert handler._dropped == 1 and handler._queue.empty()
    handler.close()


def test_thread_start_failure_can_recover(settings, monkeypatch):
    settings.SERVER_LOG_CAPTURE_ENABLED = True
    handler = DatabaseLogHandler()
    starts = []

    def start(thread):
        starts.append(thread)
        if len(starts) == 1:
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr("threading.Thread.start", start)
    handler.handle(record("first"))
    assert handler._thread is None and handler._dropped == 1 and handler._queue.empty()
    handler.handle(record("second"))
    assert len(starts) == 2 and handler._queue.qsize() == 1
    handler._thread = None
    handler.close()


def test_batch_failure_reports_recovery_without_recursion(settings, monkeypatch):
    settings.SERVER_LOG_CAPTURE_ENABLED = True
    handler = DatabaseLogHandler()
    attempts = []

    def write(batch):
        attempts.append(batch)
        # A writer logging its own error cannot recursively enqueue.
        handler.handle(record("internal"))
        if len(attempts) == 1:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(handler, "_write_batch", write)
    handler.handle(record("first"))
    handler.flush()
    assert handler._dropped == 1
    handler.handle(record("second"))
    handler.flush()
    handler.close()
    assert len(attempts) == 2 and handler._dropped == 0
    assert len(attempts[1]) == 2 and "dropped 1" in attempts[1][1]["message"]
    assert handler._queue.empty()


@pytest.mark.django_db
def test_retention_days_and_row_cap(settings):
    settings.SERVER_LOG_RETENTION_DAYS = 7
    settings.SERVER_LOG_MAX_ROWS = 3
    handler = DatabaseLogHandler()
    old = event_from_record(record("old"))
    old["timestamp"] = timezone.now() - timedelta(days=8)
    handler._write_batch([old, *[event_from_record(record(str(i))) for i in range(5)]])
    assert list(ServerLogEvent.objects.values_list("message", flat=True)) == ["4", "3", "2"]
    handler.close()


@pytest.mark.django_db(transaction=True)
def test_capture_commits_independently_of_business_rollback(settings):
    from django.db import transaction
    settings.SERVER_LOG_CAPTURE_ENABLED = True
    handler = DatabaseLogHandler()
    with pytest.raises(RuntimeError), transaction.atomic():
        handler.handle(record("survives rollback"))
        handler.flush()
        raise RuntimeError("rollback")
    handler.close()
    assert ServerLogEvent.objects.filter(message="survives rollback").exists()
