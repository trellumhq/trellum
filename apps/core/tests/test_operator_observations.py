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
    original_clock = signing.time.time
    monkeypatch.setattr(signing.time, "time", lambda: original_clock() + 301)
    with pytest.raises(observations.ObservationError):
        observations.admit(actor.pk, "full-health", key)


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


def test_system_page_prefers_a_fresh_measurement_over_local_checks(operator_probe, monkeypatch):
    actor, client, node = operator_probe
    def forbidden(*args, **kwargs):
        raise AssertionError("request ran checks despite a fresh coordinator measurement")
    OpsState.record("operator.health", completed_at=timezone.now().isoformat(),
                    checks=[{"label": "coordinator-check", "ok": True, "detail": "measured"}], producer_node=node)
    monkeypatch.setattr(health, "run_checks", forbidden)
    response = client.get("/system")
    assert response.status_code == 200
    assert b"coordinator-check" in response.content and b"data-operator-observation" in response.content


def test_system_page_runs_local_checks_when_the_coordinator_has_nothing_usable(operator_probe, monkeypatch):
    actor, client, node = operator_probe
    monkeypatch.setattr(health, "run_checks", lambda **kw: [{"label": "local-check", "ok": True, "detail": "ran here"}])
    # Never measured, coordinator alive: the table is never empty and the page asks the coordinator itself.
    first = client.get("/system").content
    assert b"local-check" in first and b"Running the first check" not in first
    assert b"data-operator-observation" in first and b"Checking" in first
    # An old measurement and no live coordinator: do not present it as current.
    OpsState.record("operator.health", completed_at=(timezone.now() - timedelta(hours=3)).isoformat(),
                    checks=[{"label": "old-check", "ok": True, "detail": "old"}], producer_node=node)
    WorkerHeartbeat.objects.all().delete()
    stale = client.get("/system").content
    assert b"local-check" in stale and b"old-check" not in stale
    assert b"data-operator-observation" not in stale and b"unavailable" not in stale


def test_header_summary_never_depends_on_the_coordinator(operator_probe):
    actor, client, node = operator_probe
    assert not OpsState.objects.filter(key=observations.SLOT).exists()
    summary = client.get("/api/system/health").json()
    assert summary["message"] != "System health unavailable"
    assert not OpsState.objects.filter(key=observations.SLOT).exists()


def test_nothing_is_measured_unless_a_page_asks(operator_probe):
    actor, client, node = operator_probe
    assert not observations.has_work()
    assert observations.claim(node, "epoch") is None
    observations.admit(actor.pk, "full-health", observations.nonce(actor.pk, "full-health"))
    assert observations.has_work()
    assert observations.claim(node, "epoch")["kind"] == "full-health"


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


def test_admission_audit_once_and_read_access_revocation(operator_probe, settings):
    settings.DEBUG = True
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


def test_csrf_and_borrowed_sessions_cannot_admit_or_read(operator_probe):
    from django.test import Client
    from apps.core.impersonation import SESSION_EXPIRES_AT, SESSION_OPERATOR_ID
    actor, client, node = operator_probe
    key = observations.nonce(actor.pk, "full-health")
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(actor)
    assert strict.post("/api/system/diagnostics/refresh", {"request_key": key}, HTTP_ACCEPT="application/json").status_code == 403
    assert not OpsState.objects.filter(key=observations.SLOT).exists()
    entry, created = observations.admit(actor.pk, "full-health", key)
    session = client.session
    session[SESSION_OPERATOR_ID] = actor.pk
    session[SESSION_EXPIRES_AT] = (timezone.now() + timedelta(minutes=5)).isoformat()
    session.save()
    assert client.post("/api/system/diagnostics/refresh", {"request_key": key}).status_code == 404
    assert client.get(f"/api/system/diagnostics/{entry['id']}").status_code == 404


def _post(client, actor, kind="full-health", key=None):
    return client.post("/api/system/diagnostics/refresh",
                       {"request_key": key or observations.nonce(actor.pk, kind)}, HTTP_ACCEPT="application/json")


def test_refusals_let_the_page_carry_on_by_itself(operator_probe, settings):
    """Another measurement or the 30 s gap is a wait, never an error needing a click."""
    settings.DEBUG = True
    actor, client, node = operator_probe
    accepted = _post(client, actor)
    assert accepted.status_code == 202
    operation_id = accepted.json()["action"]["id"]
    # One in flight: the refusal names the measurement to watch.
    busy = _post(client, actor)
    assert busy.status_code == 409
    assert busy.json()["busy"]["status_url"] == accepted.json()["action"]["status_url"]
    assert busy.json()["next_nonce"]
    claimed = observations.claim(node, "epoch")
    assert observations.finish(claimed, result=completed(claimed))
    # Thirty seconds between admissions: how long to wait, what is known, and a fresh key.
    gap = _post(client, actor)
    assert gap.status_code == 429
    body = gap.json()
    assert 1 <= body["retry_after"] <= 31
    assert body["snapshot"]["id"] == operation_id and body["snapshot"]["age_seconds"] < 30
    row = OpsState.objects.get(key=observations.SLOT)
    row.payload["admitted"] = {"full-health": (timezone.now() - timedelta(seconds=40)).isoformat()}
    row.save(update_fields=["payload"])
    assert _post(client, actor, key=body["next_nonce"]).status_code == 202
    # An invalid or expired key still hands back a usable one.
    bad = _post(client, actor, key="expired")
    assert bad.status_code == 400 and bad.json()["next_nonce"]


def test_status_of_a_retired_measurement_still_returns_a_key(operator_probe):
    actor, client, node = operator_probe
    gone = client.get("/api/system/diagnostics/00000000-0000-4000-8000-000000000000")
    assert gone.status_code == 404
    assert gone.json()["next_nonce"]


def test_snapshot_age_and_relative_text(operator_probe):
    actor, client, node = operator_probe
    OpsState.record("operator.health", completed_at=(timezone.now() - timedelta(seconds=130)).isoformat(),
                    checks=[], producer_node="coordinator-1")
    assert 129 <= observations.snapshot("full-health")["age_seconds"] <= 135
    assert observations.age_text(3) == "just now"
    assert observations.age_text(42) == "42 seconds ago"
    assert observations.age_text(60) == "1 minute ago"
    assert observations.age_text(130) == "2 minutes ago"
    assert observations.age_text(7300) == "2 hours ago"
    assert observations.age_text(None) == ""


def test_check_detail_keeps_the_explanation_without_url_secrets():
    from apps.core.management.commands.operator_probe import _detail
    assert _detail("PostgreSQL 16.15 on x86_64") == "PostgreSQL 16.15 on x86_64"
    assert _detail("no live worker heartbeat (is the worker container running?)").endswith("running?)")
    assert _detail("cannot reach https://user:secret@bucket.example/path?X-Amz-Signature=abc now") == \
        "cannot reach https://bucket.example/path now"
    assert _detail("first\nsecond\x00third") == "first second third"
    assert len(_detail("x" * 5000)) == 400


def test_the_thirty_second_gap_is_per_kind(operator_probe, settings):
    """A System health visit must not hold back an organization's uploaded-files refresh."""
    settings.DEBUG = True
    from apps.orgs.models import Organization
    actor, client, node = operator_probe
    org = Organization.objects.create(slug="gap-org", name="Gap")
    _post(client, actor)
    claimed = observations.claim(node, "epoch")
    assert observations.finish(claimed, result=completed(claimed))
    # full-health is now inside its gap; upload-usage is a different kind and is admitted at once.
    assert _post(client, actor).status_code == 429
    usage = observations.admit(actor.pk, "upload-usage", observations.nonce(actor.pk, "upload-usage", org.pk), org_id=org.pk)
    assert usage[1] and usage[0]["kind"] == "upload-usage"


def test_a_page_can_ask_before_posting_and_gets_a_plain_200(operator_probe, settings):
    settings.DEBUG = True
    actor, client, node = operator_probe
    ready = client.get("/api/system/diagnostics/refresh")
    assert ready.status_code == 200 and ready.json()["ready"] is True and ready.json()["next_nonce"]
    assert not OpsState.objects.filter(key=observations.SLOT).exists()  # asking admits nothing
    accepted = _post(client, actor, key=ready.json()["next_nonce"])
    assert accepted.status_code == 202
    busy = client.get("/api/system/diagnostics/refresh")
    assert busy.status_code == 200
    body = busy.json()
    assert body["ready"] is False and body["status"] == 409 and body["busy"]["status_url"] == accepted.json()["action"]["status_url"]
    claimed = observations.claim(node, "epoch")
    observations.finish(claimed, result=completed(claimed))
    waiting = client.get("/api/system/diagnostics/refresh").json()
    assert waiting["ready"] is False and waiting["status"] == 429 and 1 <= waiting["retry_after"] <= 31
    assert waiting["snapshot"]["id"] == accepted.json()["action"]["id"]
