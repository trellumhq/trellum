from datetime import timedelta
import socket
import os

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import health, operator_observations as observations
from apps.core.models import AuditLog, OpsState
from apps.core.workspace import initialize_workspace_identity
from apps.runner.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def operator_probe(settings, tmp_path, client):
    assert connection.vendor == "postgresql", "operator gate requires disposable PostgreSQL"
    settings.DATA_DIR = tmp_path
    settings.TRELLUM_SANDBOX = "docker"
    initialize_workspace_identity()
    operator = get_user_model().objects.create_user(email="probe-operator@portal.local", is_operator_flag=True)
    client.force_login(operator)
    node = f"{socket.gethostname()}-{os.getpid()}"
    WorkerHeartbeat.objects.create(worker_id=node, sandbox_mode="docker")
    return operator, client, node


def completed(entry):
    return {"version": 1, "kind": entry["kind"], "quality": "complete", "scope": "coordinator-local",
            "workspace_id": entry["producer_workspace_id"], "producer_fingerprint": entry["producer_fingerprint"],
            "checks": [{"label": "migrations", "ok": True, "detail": "Passed on coordinator."}]}


def test_duplicate_admission_is_immutable_and_busy_is_not_a_queue(operator_probe):
    actor, client, node = operator_probe
    key = observations.nonce(actor.pk, "full-health")
    first, created = observations.admit(actor.pk, "full-health", key)
    assert created
    assert observations.admit(actor.pk, "full-health", key)[0]["id"] == first["id"]
    with pytest.raises(observations.ObservationError) as error:
        observations.admit(actor.pk, "full-health", observations.nonce(actor.pk, "full-health"))
    assert error.value.status == 409
    claimed = observations.claim(node, "epoch")
    assert observations.finish(claimed, result=completed(claimed))
    same, created = observations.admit(actor.pk, "full-health", key)
    assert same["state"] == "succeeded" and not created


def test_nonce_actor_scope_and_ttl(operator_probe, monkeypatch):
    actor, client, node = operator_probe
    key = observations.nonce(actor.pk, "full-health")
    with pytest.raises(observations.ObservationError):
        observations.admit(actor.pk + 1, "full-health", key)
    with pytest.raises(observations.ObservationError):
        observations.admit(actor.pk, "upload-usage", key, org_id=1)
    from django.core import signing
    original = signing.loads
    monkeypatch.setattr(signing, "loads", lambda *args, **kwargs: (_ for _ in ()).throw(signing.SignatureExpired()))
    with pytest.raises(observations.ObservationError):
        observations.admit(actor.pk, "full-health", key)
    monkeypatch.setattr(signing, "loads", original)


def test_fenced_late_completion_and_invalid_output_keep_previous(operator_probe):
    actor, client, node = operator_probe
    observations.admit(actor.pk, "full-health", observations.nonce(actor.pk, "full-health"))
    entry = observations.claim(node, "first-epoch")
    wrong = {**entry, "lease_token": "wrong"}
    assert not observations.finish(wrong, result=completed(entry))
    assert observations.finish(entry, result=completed(entry))
    assert observations.snapshot("full-health")["id"] == entry["id"]
    assert not observations.finish(entry, result=completed(entry))


def test_expired_lease_never_publishes(operator_probe):
    actor, client, node = operator_probe
    observations.admit(actor.pk, "full-health", observations.nonce(actor.pk, "full-health"))
    entry = observations.claim(node, "epoch")
    row = OpsState.objects.get(key=observations.SLOT)
    row.payload["active"]["lease_until"] = (timezone.now() - timedelta(seconds=1)).isoformat()
    row.save(update_fields=["payload"])
    entry["lease_until"] = row.payload["active"]["lease_until"]
    observations.finish(entry, result=completed(entry))
    assert observations.snapshot("full-health")["state"] == "unknown"


def test_reads_never_execute_checks_or_local_io(operator_probe, monkeypatch):
    actor, client, node = operator_probe
    def forbidden(*args, **kwargs):
        raise AssertionError("request triggered a diagnostic")
    monkeypatch.setattr(health, "run_checks", forbidden)
    monkeypatch.setattr(health, "_cached_schema_state", forbidden)
    monkeypatch.setattr(health.shutil, "disk_usage", forbidden)
    response = client.get("/system")
    assert response.status_code == 200
    assert b"coordinator" in response.content and b"unverified" in response.content
    assert client.get("/api/system/health").json()["status"] == "unknown"
    assert not OpsState.objects.filter(key=observations.SLOT).exists()


def test_current_workers_and_sandbox_mismatch_are_full_aggregate(operator_probe):
    actor, client, node = operator_probe
    WorkerHeartbeat.objects.bulk_create([
        WorkerHeartbeat(worker_id=f"aggregate-{i}", role="runner", sandbox_mode="", max_concurrent=4)
        for i in range(1000)
    ])
    with CaptureQueriesContext(connection) as queries:
        facts = health.worker_facts()
    assert len(queries) == 2
    assert facts["runners"] == 1001 and facts["sandbox_mismatches"] == 1000
    assert len(facts["mismatched_ids"]) == 5
    assert client.get("/api/system/health").json()["status"] == "error"


def test_admission_audit_once_and_read_access_revocation(operator_probe):
    actor, client, node = operator_probe
    key = observations.nonce(actor.pk, "full-health")
    first = client.post("/api/system/diagnostics/refresh", {"request_key": key}, HTTP_ACCEPT="application/json")
    assert first.status_code == 202
    repeated = client.post("/api/system/diagnostics/refresh", {"request_key": key}, HTTP_ACCEPT="application/json")
    assert repeated.status_code == 200
    assert repeated.json()["action"]["id"] == first.json()["action"]["id"]
    assert AuditLog.objects.filter(action="operator.diagnostics.refresh").count() == 1
    url = first.json()["action"]["status_url"]
    assert client.get(url)["Cache-Control"] == "no-store"
    get_user_model().objects.filter(pk=actor.pk).update(is_operator_flag=False)
    assert client.get(url).status_code == 404


def test_scheduler_refresh_preserves_probe_and_removes_obsolete_report(operator_probe, monkeypatch):
    from apps.runner.management.commands.runworker import _SchedulerManager
    from types import SimpleNamespace
    manager = _SchedulerManager()
    removed = []
    manager._scheduler = SimpleNamespace(
        get_jobs=lambda: [SimpleNamespace(id="operator-probe"), SimpleNamespace(id="report-999999")],
        remove_job=removed.append, add_job=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("apps.reports.notify.flush_broken_buffers", lambda: None)
    manager.refresh()
    assert removed == ["report-999999"]
