"""Synthetic PostgreSQL admission and mocked external execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.utils import timezone

from apps.assistant import source_queries as service
from apps.assistant.models import AssistantSession, SourceQuery as Job
from apps.assistant.tests.test_source_access import source_context  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def executor(monkeypatch):
    result = {"columns": ["total"], "rows": [[42]], "truncated": False, "elapsed_ms": 1,
              "query": "SELECT count(*) FROM analytics.sales", "executed_at": "2026-11-01T12:00:00+00:00"}
    mock = SimpleNamespace(validate_query=Mock(side_effect=lambda sql, relations: sql.strip()),
                           execute_query=Mock(return_value=result), describe_source=Mock(return_value=result))
    monkeypatch.setattr(service, "_executor", lambda: mock)
    return mock


def admit(ctx, key="key", sql="SELECT count(*) FROM analytics.sales", kind="query", session=None):
    studio, user, conversation, grant = ctx
    return service.admit(studio=studio, actor=user, session=session or conversation, grant_id=grant.pk,
                         sql=sql, kind=kind, idempotency_key=key)


def public(job, ctx):
    return service.public_result(job, studio=ctx[0], actor=ctx[1], session=ctx[2])


def cancel(job, ctx):
    return service.request_cancel(job, studio=ctx[0], actor=ctx[1], session=ctx[2])


def test_idempotency_and_sql_validation(source_context, executor):
    job, created = admit(source_context)
    assert created
    same, created = admit(source_context)
    assert same.pk == job.pk and not created
    with pytest.raises(service.QueryAdmissionError, match="another source read"):
        admit(source_context, sql="SELECT 2 FROM analytics.sales")
    with pytest.raises(service.PendingLimitReached) as exc:
        admit(source_context, key="other")
    assert exc.value.scope == "session"
    executor.validate_query.assert_called()
    executor.execute_query.assert_not_called()


@pytest.mark.parametrize("key,sql,kind", [("", "SELECT 1", "query"), ("k" * 81, "SELECT 1", "query"), ("a b", "SELECT 1", "query"), ("key", "x" * 16385, "query"), ("key", "SELECT 1", "describe"), ("key", "", "unknown")])
def test_invalid_admission(source_context, executor, key, sql, kind):
    with pytest.raises(service.QueryAdmissionError):
        admit(source_context, key, sql, kind)
    assert not Job.objects.exists()


def test_worker_success_and_fresh_delivery(source_context, executor):
    job, _ = admit(source_context)
    assert public(job, source_context)["state"] == Job.ACCEPTED
    assert "result" not in public(job, source_context)
    assert service.process_next(worker_id="synthetic-worker")
    payload = public(job, source_context)
    assert payload["state"] == Job.SUCCEEDED and payload["result"]["rows"] == [[42]]
    assert executor.validate_query.call_count == 2
    _, kwargs = executor.execute_query.call_args
    assert kwargs["timeout_seconds"] == 10 and kwargs["max_rows"] == 200
    info = executor.execute_query.call_args.args[0]
    assert info["connect_timeout"] == 5
    assert not service.process_next(worker_id="other")
    source_context[3].users.clear()
    with pytest.raises(PermissionDenied):
        public(job, source_context)


def test_describe_uses_trusted_executor_path(source_context, executor):
    job, _ = admit(source_context, sql="", kind="describe")
    service.process_next(worker_id="worker")
    executor.validate_query.assert_not_called()
    executor.execute_query.assert_not_called()
    assert executor.describe_source.call_args.args[1] == ["analytics.sales"]
    assert public(job, source_context)["state"] == Job.SUCCEEDED


@pytest.mark.parametrize("stage", ["before", "during", "delivery"])
def test_revocation_and_config_changes(source_context, executor, stage, assistant_config):
    job, _ = admit(source_context)

    def revoke(*args, **kwargs):
        type(assistant_config).objects.filter(pk=assistant_config.pk).update(config_revision=99)
        assert kwargs["cancelled"]()
        return executor.describe_source.return_value

    if stage == "before":
        source_context[3].users.clear()
    elif stage == "during":
        executor.execute_query.side_effect = revoke
    service.process_next(worker_id="worker")
    if stage == "before":
        executor.execute_query.assert_not_called()
    if stage == "delivery":
        type(assistant_config).objects.filter(pk=assistant_config.pk).update(config_revision=99)
    with pytest.raises(PermissionDenied):
        public(job, source_context)
    job.refresh_from_db()
    if stage != "delivery":
        assert job.result == {}


def test_cancel_accepted_and_running(source_context, executor):
    job, _ = admit(source_context)
    assert cancel(job, source_context)["state"] == Job.CANCELLED
    assert not service.process_next(worker_id="worker")
    second, _ = admit(source_context, key="second")

    def execute(*args, **kwargs):
        kwargs["on_started"](12345)
        state = cancel(second, source_context)
        assert state["state"] == Job.RUNNING and state["cancel_requested"]
        return executor.describe_source.return_value

    executor.execute_query.side_effect = execute
    service.process_next(worker_id="worker")
    second.refresh_from_db()
    assert second.state == Job.CANCELLED and second.backend_pid == 12345 and not second.result


def test_executor_error_is_safe_and_holds_capacity(source_context, executor):
    job, _ = admit(source_context)
    executor.execute_query.side_effect = RuntimeError("password=secret SELECT private FROM hidden")
    service.process_next(worker_id="worker")
    job.refresh_from_db()
    assert job.state == Job.UNCONFIRMED and job.result == {}
    assert "secret" not in job.message and "private" not in job.message
    with pytest.raises(service.PendingLimitReached):
        admit(source_context, key="second")
    Job.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert admit(source_context, key="second")[1]


def test_recovery_ttl_and_no_lease_replay(source_context, executor):
    job, _ = admit(source_context)
    Job.objects.filter(pk=job.pk).update(accepted_at=timezone.now() - service.ACCEPTED_TTL - timedelta(seconds=1))
    assert service.recover_expired() == 1
    assert public(job, source_context)["state"] == Job.FAILED
    second, _ = admit(source_context, key="second")
    Job.objects.filter(pk=second.pk).update(state=Job.RUNNING, lease_expires_at=timezone.now() - timedelta(seconds=1))
    assert service.recover_expired() == 1
    assert public(second, source_context)["state"] == Job.UNCONFIRMED
    assert not service.process_next(worker_id="worker")
    executor.execute_query.assert_not_called()


def test_stale_lease_cannot_publish(source_context, executor):
    job, _ = admit(source_context)

    def execute(*args, **kwargs):
        Job.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        service.recover_expired()
        return executor.describe_source.return_value

    executor.execute_query.side_effect = execute
    service.process_next(worker_id="worker")
    assert public(job, source_context)["state"] == Job.UNCONFIRMED
    job.refresh_from_db()
    assert not job.result


def test_results_and_ownership_are_bounded(source_context, executor, member):
    job, _ = admit(source_context)
    executor.execute_query.return_value["rows"] = [["x" * 65536]]
    service.process_next(worker_id="worker")
    job.refresh_from_db()
    assert job.state == Job.FAILED and not job.result
    with pytest.raises(PermissionDenied):
        service.public_result(job, actor=member, studio=source_context[0], session=source_context[2])


def test_pending_limits_pool_and_retention(source_context, executor, settings):
    studio, user, _, _ = source_context
    job, _ = admit(source_context)
    assert not service.process_next(worker_id="wrong-pool", pools=["large"])
    settings.TRELLUM_SOURCE_QUERY_ORG_PENDING_LIMIT = 1
    other_session = AssistantSession.objects.create(org=studio.org, studio=studio, user=user)
    with pytest.raises(service.PendingLimitReached) as exc:
        admit(source_context, "second", session=other_session)
    assert exc.value.scope == "org"
    settings.TRELLUM_SOURCE_QUERY_ORG_PENDING_LIMIT = 4
    settings.TRELLUM_SOURCE_QUERY_FLEET_PENDING_LIMIT = 1
    with pytest.raises(service.PendingLimitReached) as exc:
        admit(source_context, "second", session=other_session)
    assert exc.value.scope == "fleet"
    Job.objects.filter(pk=job.pk).update(state=Job.SUCCEEDED, accepted_at=timezone.now() - service.KEY_RETENTION - timedelta(seconds=1), result={"old": "data"})
    replacement, created = admit(source_context)
    assert created and replacement.pk != job.pk
    job.refresh_from_db()
    assert job.idempotency_key == "" and job.result == {"old": "data"}


def test_audit_does_not_store_sql_credentials_or_data(source_context, executor):
    from apps.core.models import AuditLog
    admit(source_context)
    service.process_next(worker_id="worker")
    entries = list(AuditLog.objects.filter(action__startswith="assistant.source_").values_list("metadata", flat=True))
    assert len(entries) == 2
    assert all("sql" not in entry and "result" not in entry for entry in entries)
    assert "test-only" not in str(entries)


@pytest.mark.django_db(transaction=True)
def test_concurrent_admission_is_atomic(source_context, executor):
    from threading import Barrier
    barrier = Barrier(2)

    def submit(key):
        try:
            barrier.wait(timeout=5)
            try:
                return admit(source_context, key)[0].pk
            except service.PendingLimitReached:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["a", "b"]))
    assert sum(r is not None for r in results) == 1
    assert Job.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_workers_claim_once(source_context, executor):
    admit(source_context)
    from threading import Barrier
    barrier = Barrier(2)

    def run(worker):
        try:
            barrier.wait(timeout=5)
            return service.process_next(worker_id=worker)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, ["a", "b"])) == [False, True]
    assert executor.execute_query.call_count == 1


@pytest.mark.django_db(transaction=True)
def test_worker_service_lifecycle(source_context, executor):
    from threading import Event
    completed = Event()
    real_finish = service._finish

    def finish(*args, **kwargs):
        value = real_finish(*args, **kwargs)
        completed.set()
        return value

    from unittest.mock import patch
    admit(source_context)
    assert service.start_for_worker(is_runner=False, pools=[], worker_id="coordinator") is None
    with patch.object(service, "_finish", side_effect=finish):
        thread = service.start_for_worker(is_runner=True, pools=[], worker_id="worker")
        try:
            assert completed.wait(5)
        finally:
            thread.stop()
    assert not thread.is_alive()
    assert Job.objects.get().state == Job.SUCCEEDED


def test_on_started_rechecks_revocation_before_sql(source_context, executor):
    job, _ = admit(source_context)

    def execute(*args, **kwargs):
        source_context[3].users.clear()
        kwargs["on_started"](12345)
        pytest.fail("Revoked source SQL must not execute")

    executor.execute_query.side_effect = execute
    service.process_next(worker_id="worker")
    job.refresh_from_db()
    assert job.state == Job.FAILED and job.backend_pid is None and not job.result


def test_invalid_query_identifier_is_safely_denied(source_context):
    with pytest.raises(PermissionDenied):
        public("not-a-uuid", source_context)
