"""Offline failure injection through real children and durable recovery records."""
from datetime import timedelta
import os
import time
from unittest.mock import Mock

import pytest
from django.utils import timezone

from apps.runner.executor import Executor
from apps.runner.management.commands.runworker import _enqueue_scheduled
from apps.runner.models import Run, RunRecovery
from apps.runner.recovery import dispatch_due
from apps.runner.services import enqueue
from trellum.diagnostics import read_diagnostics

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.heavy,
              pytest.mark.skipif(os.name == "nt", reason="requires POSIX process-group fencing")]

GENERATOR = '''
import os
import threading
from trellum import BaseReport
from trellum.components import RawHTML
from trellum.data import query_df
from trellum.data.connections import register_source, forget_source
from trellum.data.retry import ManagedConnection
from trellum.data import retry

class FaultReport(BaseReport):
    def generate(self, ctx):
        fault = os.environ.get("TRELLUM_TEST_FAULT", "none")
        retry.time.sleep = lambda seconds: None
        class Cursor:
            description = [("value",)]
            def execute(self, sql):
                if fault == "execute":
                    raise ConnectionError("connection reset by peer")
            def fetchall(self):
                if fault == "fetch":
                    raise ConnectionError("connection reset by peer")
                if fault == "hang_fetch":
                    threading.Event().wait(60)
                return [(42,)]
            def close(self):
                pass
        class Connection:
            def cursor(self):
                return Cursor()
            def close(self):
                pass
        def factory():
            if fault == "connect":
                raise ConnectionError("connection reset by peer")
            return Connection()
        conn = ManagedConnection("postgres", factory)
        register_source(conn, "offline-fault-source")
        try:
            frame = query_df(conn, "SELECT value FROM fixture", cache_ttl=0)
        finally:
            conn.close()
            forget_source(conn)
        ctx.add_section("Result", [RawHTML("<p>Recovered value: " + str(frame.value.iloc[0]) + "</p>")])
'''


@pytest.fixture
def fault_report(report_row, write_report, settings, monkeypatch):
    settings.DEBUG = True
    settings.TRELLUM_SANDBOX = "off"
    settings.TRELLUM_STORAGE_BACKEND = "local"
    settings.TRELLUM_JOB_MEMORY_ENFORCE = False
    config = {
        "name": "Offline recovery fixture", "studio": "Fixture", "category": "Tests",
        "description": "An offline controlled transport fault fixture exercising full report recovery and publication.",
        "schedule": {"cron": "0 * * * *"}, "recovery": {"replay_safe": True},
    }
    report_dir = write_report(report_row.slug, **config)
    (report_dir / "generator.py").write_text(GENERATOR)
    report_row.config = config
    report_row.save(update_fields=["config"])
    # The process/transaction path is real. External notification/alert adapters
    # are observed without invoking providers or starting attribution DB threads.
    notifications, alerts = Mock(), Mock()
    monkeypatch.setattr("apps.reports.notify.run_finished", notifications)
    monkeypatch.setattr("apps.alerts.evaluator.evaluate_after_build", alerts)
    monkeypatch.setattr(Executor, "_attribute_failure", staticmethod(lambda *args: None))
    monkeypatch.setattr("apps.reports.models.should_suspend_refresh", lambda *args: False)
    published = report_row.studio.output_dir / report_row.slug
    published.mkdir(parents=True, exist_ok=True)
    (published / "index.html").write_bytes(b"last successful report")
    (published / "data.json").write_bytes(b'{"previous":true}')
    return report_row, published, notifications, alerts


def _queued(report):
    return Run.objects.get(report=report, status=Run.QUEUED)


def _run(run, fault, *, timeout=False):
    executor = Executor("fault-fixture-worker", default_timeout=3600)
    assert executor.start_run(run, extra_env={"TRELLUM_TEST_FAULT": fault})
    proc = executor._procs[run.pk]
    try:
        if timeout:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                evidence = read_diagnostics(proc.diagnostics_path)
                if evidence.get("stage") == "fetch" and evidence.get("state") == "active":
                    break
                assert proc.process.poll() is None, "child exited before injected timeout"
                time.sleep(.02)
            else:
                pytest.fail("child did not enter fetch before deadline")
            proc.started_at = timezone.now() - timedelta(seconds=3601)
            executor.tick()  # Real process-group SIGTERM; no replacement yet.
        proc.process.wait(timeout=15)
        executor.tick()
        run.refresh_from_db()
        assert run.termination_confirmed is True and not run.is_active
        assert executor.running_count == 0
        # Repeated coordinator ticks cannot duplicate terminal side effects.
        executor.tick()
        return run
    finally:
        if proc.process.poll() is None:
            executor._signal_process_tree(proc, 9)
            proc.process.wait(timeout=5)


def _dispatch_next(chain):
    RunRecovery.objects.filter(pk=chain.pk).update(due_at=timezone.now() - timedelta(seconds=1))
    # No in-memory recovery owner survives between these coordinator passes.
    dispatch_due()
    dispatch_due()
    return Run.objects.get(recovery=chain, status=Run.QUEUED)


def _preserved(published):
    assert (published / "index.html").read_bytes() == b"last successful report"
    assert (published / "data.json").read_bytes() == b'{"previous":true}'


@pytest.mark.parametrize("fault", ["connect", "execute", "fetch", "hang_fetch"])
def test_real_failure_durable_retry_success_preserves_output(fault_report, fault):
    report, published, notifications, alerts = fault_report
    assert enqueue(report) == "queued"
    root = _run(_queued(report), fault, timeout=fault == "hang_fetch")
    assert root.status == (Run.TIMEOUT if fault == "hang_fetch" else Run.ERROR)
    assert root.diagnostics["stage"] == ("fetch" if fault == "hang_fetch" else fault)
    assert root.diagnostics["query_retry_safe"] is True
    assert root.diagnostics["unsafe_operation"] is False
    assert root.diagnostics["state"] == ("active" if fault == "hang_fetch" else "failed")
    assert root.diagnostics["attempt"] == (1 if fault == "hang_fetch" else 3)
    assert root.recovery.state == "pending"
    _preserved(published)
    report.refresh_from_db()
    assert report.last_built_at is None and alerts.call_count == 0
    retry_run = _dispatch_next(root.recovery)
    assert retry_run.trigger == "retry" and retry_run.recovery_attempt == 1
    success = _run(retry_run, "none")
    assert success.status == Run.SUCCESS
    root.recovery.refresh_from_db()
    assert root.recovery.state == "succeeded"
    assert b"Recovered value: 42" in (published / "index.html").read_bytes()
    assert (published / "data.json").read_bytes() != b'{"previous":true}'
    report.refresh_from_db()
    assert report.last_built_at is not None
    assert notifications.call_count == 2 and alerts.call_count == 1
    assert Run.objects.filter(report=report).count() == 2


def test_real_persistent_fault_exhaustion_keeps_next_cron(fault_report):
    report, published, notifications, alerts = fault_report
    enqueue(report)
    root = _run(_queued(report), "fetch")
    chain = root.recovery
    for attempt in (1, 2):
        _preserved(published)
        retry_run = _dispatch_next(chain)
        assert retry_run.recovery_attempt == attempt
        failed = _run(retry_run, "fetch")
        assert failed.status == Run.ERROR and failed.diagnostics["attempt"] == 3
        chain.refresh_from_db()
    assert chain.state == "exhausted" and chain.due_at is None
    dispatch_due()
    assert Run.objects.filter(report=report).count() == 3
    _preserved(published)
    # Exercise the real ordinary scheduling callback after recovery stops.
    _enqueue_scheduled(report.pk)
    scheduled = _queued(report)
    assert scheduled.trigger == "schedule" and scheduled.recovery_id is None
    assert _run(scheduled, "none").status == Run.SUCCESS
    chain.refresh_from_db()
    assert chain.state == "exhausted"  # Historical failure chain stays inspectable.
    assert notifications.call_count == 4 and alerts.call_count == 1
    assert b"Recovered value: 42" in (published / "index.html").read_bytes()
