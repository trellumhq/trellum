"""Request ids and log formatting.

A single report build crosses four processes — web, the Run row, the worker, and
a sandbox container — each of which used to print to its own stdout with no
shared identifier. "What happened to this build" meant comparing timestamps
across containers.
"""
import json
import logging

import pytest

from apps.core.logging import JsonFormatter, RequestIdFilter, current_request_id

pytestmark = pytest.mark.django_db


class TestRequestId:
    def test_a_response_carries_one(self, client):
        assert client.get("/healthz")["X-Request-ID"]

    def test_each_request_gets_its_own(self, client):
        first = client.get("/healthz")["X-Request-ID"]
        second = client.get("/healthz")["X-Request-ID"]
        assert first != second

    def test_an_inbound_id_is_honoured(self, client):
        """A proxy that already stamps one stays the source of truth, so the
        id in their logs and ours is the same string."""
        response = client.get("/healthz", HTTP_X_REQUEST_ID="edge-abc123")
        assert response["X-Request-ID"] == "edge-abc123"

    def test_an_inbound_id_is_sanitised(self, client):
        """It is client-controlled and ends up in files people grep, so
        newlines and control characters must not survive into a log line."""
        response = client.get("/healthz", HTTP_X_REQUEST_ID="abc\ndef INJECTED")
        assert response["X-Request-ID"] == "abcdefINJECTED"

    def test_an_inbound_id_is_bounded(self, client):
        response = client.get("/healthz", HTTP_X_REQUEST_ID="x" * 500)
        assert len(response["X-Request-ID"]) == 64

    def test_the_context_does_not_leak_between_requests(self, client):
        """The var is reset in a finally: a leaked id would label unrelated
        background work with somebody's request."""
        client.get("/healthz")
        assert current_request_id.get() == ""


class TestRequestIdFilter:
    def test_records_get_the_current_id(self):
        token = current_request_id.set("req-42")
        try:
            record = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
            RequestIdFilter().filter(record)
            assert record.request_id == "req-42"
        finally:
            current_request_id.reset(token)

    def test_outside_a_request_it_is_empty_not_missing(self):
        """The text formatter interpolates it unconditionally; a missing
        attribute would raise inside logging itself."""
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", None, None)
        RequestIdFilter().filter(record)
        assert record.request_id == ""


class TestJsonFormatter:
    def _format(self, record):
        return json.loads(JsonFormatter().format(record))

    def test_core_fields(self):
        record = logging.LogRecord("trellum.worker", logging.WARNING, __file__, 1,
                                   "queue is deep", None, None)
        payload = self._format(record)
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "trellum.worker"
        assert payload["message"] == "queue is deep"
        assert "time" in payload

    def test_extra_fields_are_included(self):
        """`extra=` is how a build's identity reaches the log line."""
        record = logging.LogRecord("trellum.runner", logging.INFO, __file__, 1,
                                   "done", None, None)
        record.run_id = "abc-123"
        record.duration_seconds = 4.2
        payload = self._format(record)
        assert payload["run_id"] == "abc-123"
        assert payload["duration_seconds"] == 4.2

    def test_secrets_are_never_emitted(self):
        """Nothing should ever pass one, but a log line is a bad place to
        discover that something did."""
        record = logging.LogRecord("bi", logging.INFO, __file__, 1, "x", None, None)
        record.password = "hunter2"
        record.api_key = "sk-live-xyz"
        record.run_id = "keep-me"
        payload = self._format(record)
        assert "password" not in payload
        assert "api_key" not in payload
        assert payload["run_id"] == "keep-me"

    def test_unserialisable_values_do_not_break_the_line(self):
        """A log call must never be the thing that raises."""
        record = logging.LogRecord("bi", logging.INFO, __file__, 1, "x", None, None)
        record.thing = object()
        assert isinstance(self._format(record)["thing"], str)

    def test_exceptions_are_captured(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord("bi", logging.ERROR, __file__, 1, "failed",
                                       None, sys.exc_info())
        payload = self._format(record)
        assert "ValueError: boom" in payload["exception"]

    def test_request_id_is_included_when_present(self):
        record = logging.LogRecord("bi", logging.INFO, __file__, 1, "x", None, None)
        record.request_id = "req-9"
        assert self._format(record)["request_id"] == "req-9"

    def test_an_empty_request_id_is_omitted(self):
        """The filter sets it on every record; worker logs have no request, and
        an empty field on every line is noise."""
        record = logging.LogRecord("bi", logging.INFO, __file__, 1, "x", None, None)
        record.request_id = ""
        assert "request_id" not in self._format(record)


class TestWorkerModulesUseLoggers:
    """The LOGGING config used to configure a root handler that no application
    code ever wrote to: 53 bare print() calls were the real log."""

    @pytest.mark.parametrize(
        "module",
        ["apps/runner/management/commands/runworker.py",
         "apps/runner/executor.py",
         "apps/runner/gitsync.py"],
    )
    def test_no_bare_prints_remain(self, module):
        from pathlib import Path

        from django.conf import settings

        source = (Path(settings.BASE_DIR) / module).read_text(encoding="utf-8")
        assert "print(" not in source, f"{module} still prints instead of logging"
