"""Controlled failure timelines through real PostgreSQL intent/admission transactions."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, transaction
from django.utils import timezone

from apps.reports.models import Report
from apps.runner.management.commands.runworker import Command, _enqueue_scheduled
from apps.runner.models import Run, RunRecovery
from apps.runner.recovery import complete_run, dispatch_due, reconcile
from apps.runner.services import enqueue, request_stop

pytestmark = pytest.mark.django_db
EVIDENCE = {"v": 1, "stage": "fetch", "transient": True, "query_retry_safe": True}


def safe_report(report):
    report.config = {"recovery": {"replay_safe": True}}
    report.save(update_fields=["config"])
    return report


def finish(run, status=Run.ERROR, evidence=None, confirmed=True):
    with transaction.atomic():
        Report.objects.select_for_update().get(pk=run.report_id)
        run = Run.objects.select_for_update().get(pk=run.pk)
        run.status, run.finished_at = status, timezone.now()
        run.save(update_fields=["status", "finished_at"])
        complete_run(run, diagnostics=EVIDENCE if evidence is None else evidence, termination_confirmed=confirmed)
    return Run.objects.get(pk=run.pk)


def due(chain):
    RunRecovery.objects.filter(pk=chain.pk).update(due_at=timezone.now() - timedelta(seconds=1))


def test_failure_restart_retry_success_preserves_chain(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    assert chain.state == "pending" and chain.attempt == 0
    assert 59 <= (chain.due_at - root.finished_at).total_seconds() <= 67
    due(chain)
    # A fresh coordinator has no in-memory chain state to restore.
    dispatch_due()
    dispatch_due()
    attempt = Run.objects.get(trigger="retry")
    assert attempt.pool == report_row.studio.pool
    assert attempt.recovery_attempt == 1
    finish(attempt, Run.SUCCESS)
    chain.refresh_from_db()
    assert chain.state == "succeeded"
    assert Run.objects.count() == 2
    assert Run.objects.get(pk=root.pk).to_history_dict()["recovery"]["state"] == "succeeded"


def test_exhaustion_keeps_next_normal_cron(report_row, monkeypatch):
    monkeypatch.setattr("apps.runner.management.commands.runworker.close_old_connections", lambda: None)
    safe_report(report_row)
    monkeypatch.setattr("apps.reports.models.should_suspend_refresh", lambda *a: False)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    for attempt in (1, 2):
        due(chain)
        dispatch_due()
        run = Run.objects.get(status=Run.QUEUED)
        assert run.recovery_attempt == attempt
        finish(run)
        chain.refresh_from_db()
        if attempt == 1:
            assert 299 <= (chain.due_at - Run.objects.get(pk=run.pk).finished_at).total_seconds() <= 331
    assert chain.state == "exhausted"
    dispatch_due()
    _enqueue_scheduled(report_row.pk)
    assert Run.objects.get(status=Run.QUEUED).trigger == "schedule"
    assert Run.objects.count() == 4


@pytest.mark.parametrize("status,evidence,confirmed,reason", [
    (Run.OOM_KILLED, EVIDENCE, True, "memory_failure"),
    (Run.ERROR, {"stage": "execute", "transient": False}, True, "permanent_or_unknown_failure"),
    (Run.ERROR, {**EVIDENCE, "unsafe_operation": True}, True, "operation_outcome_unconfirmed"),
    (Run.ERROR, {**EVIDENCE, "stage": "publish"}, True, "publication_outcome_unconfirmed"),
    (Run.TIMEOUT, EVIDENCE, False, "unconfirmed"),
    (Run.STOPPED, EVIDENCE, True, "cancelled"),
    (Run.TIMEOUT, {"stage": "execute", "query_retry_safe": False}, True, "operation_outcome_unconfirmed"),
])
def test_distinct_non_replayable_outcomes(report_row, status, evidence, confirmed, reason):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row), status, evidence, confirmed)
    assert root.diagnostics["recovery"]["reason"] == reason
    assert not RunRecovery.objects.exists()


def test_safe_query_is_not_safe_report(report_row):
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    assert root.diagnostics["recovery"]["reason"] == "report_replay_not_safe"
    assert not RunRecovery.objects.exists()


def test_timeout_retries_only_when_confirmed_and_safe(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row), Run.TIMEOUT)
    assert root.recovery.state == "pending"


def test_cancel_pending_and_active_attempt(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    assert request_stop(report_row)
    due(root.recovery)
    dispatch_due()
    assert Run.objects.count() == 1
    root.recovery.refresh_from_db()
    assert root.recovery.state == "cancelled"


@pytest.mark.parametrize("change", ["disabled", "suspended", "unsafe", "expired"])
def test_admission_rechecks_policy_and_age(report_row, change):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    if change == "disabled":
        report_row.disabled = True
    elif change == "suspended":
        report_row.refresh_suspended_at = timezone.now()
    elif change == "unsafe":
        report_row.config = {}
    else:
        RunRecovery.objects.filter(pk=chain.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    report_row.save()
    due(chain)
    dispatch_due()
    chain.refresh_from_db()
    assert chain.state == ("expired" if change == "expired" else "cancelled")
    assert Run.objects.count() == 1


def test_manual_active_defers_then_success_supersedes(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    enqueue(report_row)
    due(chain)
    dispatch_due()
    chain.refresh_from_db()
    assert chain.reason == "active_run_deferred"
    manual = Run.objects.get(status=Run.QUEUED)
    finish(manual, Run.SUCCESS)
    chain.refresh_from_db()
    assert chain.state == "superseded"
    assert Run.objects.count() == 2


def test_rollback_cannot_leave_terminal_without_retry_intent(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = Run.objects.get(report=report_row)
    with pytest.raises(RuntimeError), transaction.atomic():
        finish(root)
        raise RuntimeError("injected database finalization failure")
    root.refresh_from_db()
    assert root.status == Run.QUEUED
    assert not RunRecovery.objects.exists()


def test_worker_heartbeat_loss_fences_all_admission(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = Run.objects.get(report=report_row)
    root.status, root.worker_id = Run.RUNNING, "missing-worker"
    root.save()
    Command()._recover_stale_runs()
    root.refresh_from_db()
    assert root.status == Run.RUNNING and root.termination_confirmed is False
    assert root.diagnostics["recovery"]["state"] == "unconfirmed"
    assert enqueue(report_row) == "already_running"
    assert Run.objects.count() == 1


def test_restart_reconciles_non_executor_terminal_attempt(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    due(root.recovery)
    dispatch_due()
    Run.objects.filter(trigger="retry").update(status=Run.ERROR, termination_confirmed=True)
    reconcile()
    root.recovery.refresh_from_db()
    assert root.recovery.state == "ineligible"


@pytest.mark.django_db(transaction=True)
def test_concurrent_manual_cron_retry_coalesce(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    due(root.recovery)
    barrier = Barrier(3)
    def act(kind):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if kind == "retry":
                dispatch_due()
            else:
                enqueue(Report.objects.get(pk=report_row.pk), trigger=kind)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(act, ["manual", "schedule", "retry"]))
    assert Run.objects.filter(status__in=Run.ACTIVE_STATUSES).count() == 1


def test_failed_manual_attempt_consumes_pending_chain_budget(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    enqueue(report_row)
    manual = finish(Run.objects.get(status=Run.QUEUED))
    chain.refresh_from_db()
    assert manual.recovery_id == chain.pk and chain.attempt == 1
    assert RunRecovery.objects.count() == 1
    due(chain)
    dispatch_due()
    finish(Run.objects.get(status=Run.QUEUED))
    chain.refresh_from_db()
    assert chain.state == "exhausted" and Run.objects.count() == 3
    assert all(row.diagnostics["recovery"]["state"] == "exhausted" for row in Run.objects.all())


@pytest.mark.parametrize("change", ["expired", "disabled", "suspended", "unsafe"])
def test_queued_retry_rechecks_before_process_claim(report_row, change):
    from apps.runner.tests.test_worker import StubExecutor
    from apps.orgs import quotas

    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    due(chain)
    dispatch_due()
    queued = Run.objects.get(status=Run.QUEUED)
    if change == "expired":
        RunRecovery.objects.filter(pk=chain.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    elif change == "disabled":
        report_row.disabled = True
    elif change == "suspended":
        report_row.refresh_suspended_at = timezone.now()
    else:
        report_row.config = {}
    report_row.save()
    executor = StubExecutor()
    assert Command()._try_claim(executor, queued.pk, quotas) is False
    assert executor.started == []
    queued.refresh_from_db()
    chain.refresh_from_db()
    assert queued.status == Run.STOPPED
    assert chain.state == ("expired" if change == "expired" else "cancelled")


def test_cancel_active_retry_cannot_restore_pending_intent(report_row):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    due(chain)
    dispatch_due()
    active = Run.objects.get(status=Run.QUEUED)
    Run.objects.filter(pk=active.pk).update(status=Run.RUNNING)
    assert request_stop(report_row)
    stopped = finish(active, Run.STOPPED)
    assert stopped.diagnostics["recovery"]["state"] == "cancelled"
    chain.refresh_from_db()
    assert chain.state == "cancelled"
    dispatch_due()
    assert Run.objects.count() == 2


@pytest.mark.parametrize("error,cause", [
    ({"class": "OperationalError", "code": "28P01"}, "authentication_failed"),
    ({"class": "ProgrammingError", "code": "42601"}, "sql_error"),
    ({"class": "OperationalError", "code": "57014"}, "query_cancelled_or_timeout"),
    ({"class": "ConfigurationError"}, "configuration_error"),
])
def test_permanent_causes_are_distinct_and_never_replayed(report_row, error, cause):
    safe_report(report_row)
    enqueue(report_row)
    result = finish(Run.objects.get(report=report_row), evidence={"stage": "execute", "errors": [error]})
    assert result.diagnostics["cause"] == cause
    assert not RunRecovery.objects.exists()


@pytest.mark.heavy
def test_dispatch_query_and_payload_cost_stays_bounded(report_row, django_assert_num_queries):
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    import json

    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    counts = []
    for count in (5, 1000):
        Run.objects.bulk_create([
            Run(report=report_row, studio=report_row.studio, slug=report_row.slug,
                status=Run.ERROR, created_at=root.created_at - timedelta(days=1))
            for _ in range(count)
        ])
        due(chain)
        with CaptureQueriesContext(connection) as queries:
            dispatch_due(limit=1)
        counts.append(len(queries))
        queued = Run.objects.get(status=Run.QUEUED)
        assert len(json.dumps(queued.to_history_dict())) < 20_000
        Run.objects.filter(pk=queued.pk).delete()
        # Reset only the fixture admission, never production retry policy.
        RunRecovery.objects.filter(pk=chain.pk).update(state="pending", attempt=0, current_run=root)
    assert counts[0] == counts[1] and counts[0] < 20


@pytest.mark.parametrize("evidence", [
    {"stage": "publish", "unknown_write_outcome": True},
    {**EVIDENCE, "unsafe_operation": True},
    {"stage": "execute", "errors": [{"class": "ProgrammingError", "code": "42601"}]},
])
def test_newer_unsafe_or_permanent_failure_closes_old_pending_intent(report_row, evidence):
    safe_report(report_row)
    enqueue(report_row)
    root = finish(Run.objects.get(report=report_row))
    chain = root.recovery
    enqueue(report_row)
    manual = finish(Run.objects.get(status=Run.QUEUED), evidence=evidence)
    assert manual.recovery_id == chain.pk
    chain.refresh_from_db()
    assert chain.state == "ineligible"
    due(chain)
    dispatch_due()
    assert Run.objects.count() == 2


@pytest.mark.parametrize("error", [
    {"class": "OperationalError", "code": "28P01"},
    {"class": "ProgrammingError", "code": "42601"},
    {"class": "ConfigurationError"},
    {"class": "OperationalError", "code": "08007"},
    {"class": "MemoryError"},
])
def test_outer_timeout_never_overrides_permanent_failure_evidence(report_row, error):
    safe_report(report_row)
    enqueue(report_row)
    run = finish(Run.objects.get(report=report_row), Run.TIMEOUT, {
        "stage": "execute", "state": "failed", "cause": "build_timeout",
        "query_retry_safe": True, "transient": False, "errors": [error],
    })
    assert not RunRecovery.objects.exists()
    assert run.diagnostics["recovery"]["state"] == "ineligible"


def test_unconfirmed_active_fence_is_visible_in_existing_status_history(report_row, org_admin, client):
    safe_report(report_row)
    enqueue(report_row)
    run = Run.objects.get(report=report_row)
    run.status, run.worker_id = Run.RUNNING, "missing-worker"
    run.save()
    Command()._recover_stale_runs()
    client.force_login(org_admin)
    url = f"/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/status"
    response = client.get(url)
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "running"
    assert body["history"][0]["recovery"]["state"] == "unconfirmed"
    assert body["history"][0]["diagnostics"]["cause"] == "worker_outcome_unconfirmed"


def test_ordinary_active_run_keeps_existing_completed_history_shape(report_row, org_admin, client):
    enqueue(report_row)
    Run.objects.filter(report=report_row).update(status=Run.RUNNING)
    client.force_login(org_admin)
    url = f"/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/status"
    assert client.get(url).json()["history"] == []
