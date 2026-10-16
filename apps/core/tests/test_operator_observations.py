"""System health and uploaded-file usage: measured only while someone looks.

Page views and polls note a request; the coordinator's tick measures what was
asked for and has gone stale. No leases or in-flight state exist to recover.
"""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import health, operator_observations as observations
from apps.core.audit_actions import ACTIONS
from apps.core.models import AuditLog, OpsState
from apps.operator import bounded
from apps.orgs.models import Organization
from apps.runner.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db

CHECKS = [{"label": "database", "ok": True, "detail": "PostgreSQL 16"}]


@pytest.fixture
def operator():
    return get_user_model().objects.create_user(email="obs-operator@portal.local", is_operator_flag=True)


@pytest.fixture
def operator_client(operator):
    client = Client()
    client.force_login(operator)
    return client


@pytest.fixture
def coordinator():
    return WorkerHeartbeat.objects.create(worker_id="obs-coordinator")


def seconds_ago(seconds):
    return (timezone.now() - timedelta(seconds=seconds)).isoformat()


def store(key, *, asked=None, tried=None, result=None, completed=None):
    """An OpsState row as the pages and the coordinator leave it."""
    payload = {}
    if asked is not None:
        payload["requested_at"] = seconds_ago(asked)
    if tried is not None:
        payload["attempted_at"] = seconds_ago(tried)
    if result is not None:
        payload.update(result=result, completed_at=seconds_ago(tried if completed is None else completed))
    return OpsState.objects.update_or_create(key=key, defaults={"ran_at": timezone.now(), "payload": payload})[0]


@pytest.fixture
def full_checks(monkeypatch):
    """Record the full checks the request runs; the header's quick summary is not one."""
    calls = []

    def run_checks(*, quick=False):
        if quick:
            return []
        calls.append(1)
        return [{"label": "local-check", "ok": True, "detail": "ran here"}]
    monkeypatch.setattr(health, "run_checks", run_checks)
    return calls


@pytest.fixture
def measured(monkeypatch):
    """Replace the child process: record which figures the tick measures."""
    calls = []
    monkeypatch.setattr(observations, "measure", calls.append)
    return calls


# ── the pages only ask ───────────────────────────────────────────────────────

def test_page_view_notes_the_request_and_runs_no_checks(operator_client, coordinator, full_checks):
    audits = AuditLog.objects.count()
    html = operator_client.get("/system").content.decode()
    assert full_checks == []  # a coordinator is alive: the request waits on nothing
    assert "Checking…" in html and "data-operator-observation" in html
    assert OpsState.objects.get(key=observations.HEALTH).payload["requested_at"]
    assert AuditLog.objects.count() == audits  # no audit row, however often the page asks


def test_requests_are_noted_at_most_every_thirty_seconds(operator_client, coordinator):
    operator_client.get("/api/system/diagnostics")
    first = OpsState.objects.get(key=observations.HEALTH).payload["requested_at"]
    with CaptureQueriesContext(connection) as queries:
        operator_client.get("/api/system/diagnostics")
    assert not [q for q in queries if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT"))]
    assert OpsState.objects.get(key=observations.HEALTH).payload["requested_at"] == first
    store(observations.HEALTH, asked=45)
    operator_client.get("/api/system/diagnostics")
    assert observations.stamp(OpsState.objects.get(key=observations.HEALTH).payload["requested_at"]) > timezone.now() - timedelta(seconds=5)


def test_a_new_request_never_overwrites_a_stored_result(operator_client, coordinator):
    store(observations.HEALTH, asked=60, tried=5, result={"checks": CHECKS})
    body = operator_client.get("/api/system/diagnostics").json()
    assert body["checks"] == CHECKS and 4 <= body["age_seconds"] <= 8 and body["failed"] is False
    assert OpsState.objects.get(key=observations.HEALTH).payload["result"] == {"checks": CHECKS}


# ── the coordinator measures what was asked for, when stale ──────────────────

def test_tick_measures_only_when_requested_and_stale(measured):
    assert observations.tick() is None and measured == []  # nobody asked
    store(observations.HEALTH, asked=5)  # asked, never measured
    assert observations.tick() == observations.HEALTH and measured == [observations.HEALTH]
    store(observations.HEALTH, asked=5, tried=20, result={"checks": CHECKS})  # fresh enough
    assert observations.tick() is None and len(measured) == 1
    store(observations.HEALTH, asked=5, tried=61, result={"checks": CHECKS})  # a minute old
    assert observations.tick() == observations.HEALTH and len(measured) == 2
    store(observations.HEALTH, asked=200, tried=3600, result={"checks": CHECKS})  # nobody is looking any more
    assert observations.tick() is None and len(measured) == 2


def test_uploads_are_remeasured_after_five_minutes_not_one(measured, org):
    key = observations.usage_key(org.pk)
    store(key, asked=5, tried=120, result={"bytes": 1})
    assert observations.tick() is None
    store(key, asked=5, tried=301, result={"bytes": 1})
    assert observations.tick() == key and measured == [key]


def test_tick_runs_one_measurement_with_health_first(measured, org):
    store(observations.usage_key(org.pk), asked=1)
    store(observations.HEALTH, asked=1)
    assert observations.tick() == observations.HEALTH
    assert measured == [observations.HEALTH]


def test_an_idle_tick_is_one_query(measured):
    store(observations.HEALTH, asked=3600, tried=3600, result={"checks": CHECKS})
    with CaptureQueriesContext(connection) as queries:
        observations.tick()
    assert len(queries) == 1 and measured == []


def test_a_failed_attempt_waits_its_interval_and_keeps_the_old_result(monkeypatch):
    store(observations.HEALTH, asked=5, tried=90, result={"checks": CHECKS})
    monkeypatch.setattr(observations, "run_bounded", lambda argv, timeout: None)  # killed, crashed or garbled
    observations.measure(observations.HEALTH)
    row = OpsState.objects.get(key=observations.HEALTH)
    assert row.ok is False and row.payload["result"] == {"checks": CHECKS}
    shown = observations.snapshot(row.payload)
    assert shown["failed"] is True and shown["checks"] == CHECKS and shown["age_seconds"] >= 90
    assert not observations.due(observations.HEALTH, row.payload, timezone.now())  # retried a minute later, not at once
    assert observations.due(observations.HEALTH, row.payload, timezone.now() + timedelta(seconds=61))


def test_a_good_result_is_stored_with_its_time(monkeypatch):
    note = observations.note(observations.HEALTH)
    assert note["requested_at"]
    out = json.dumps({"checks": [{"label": "db", "ok": True, "detail": "fine", "extra": 1}]}).encode()
    seen = []
    monkeypatch.setattr(observations, "run_bounded", lambda argv, timeout: seen.append((argv, timeout)) or out)
    observations.measure(observations.HEALTH)
    row = OpsState.objects.get(key=observations.HEALTH)
    assert row.ok and row.payload["result"] == {"checks": [{"label": "db", "ok": True, "detail": "fine"}]}
    assert row.payload["completed_at"] == row.payload["attempted_at"] and row.payload["requested_at"] == note["requested_at"]
    assert seen[0][0][-2:] == ["operator_measure", "health"] and seen[0][1] == observations.TIMEOUT["health"]


@pytest.mark.parametrize("kind, output", [
    ("health", b"not json"), ("health", b"[]"), ("health", b'{"checks": "x"}'),
    ("health", json.dumps({"checks": [{"label": "x", "ok": "yes", "detail": ""}]}).encode()),
    ("health", json.dumps({"checks": [{"label": "x" * 81, "ok": True, "detail": ""}]}).encode()),
    ("usage", b'{"bytes": -1}'), ("usage", b'{"bytes": true}'), ("usage", b'{"bytes": 1.5}'), ("usage", b""),
])
def test_malformed_output_is_not_stored(kind, output):
    assert observations._clean(kind, output) is None


def test_log_noise_before_the_json_line_is_ignored():
    assert observations._clean("usage", b'warning: something\n{"bytes": 7}\n') == {"bytes": 7}


def test_a_coordinator_restart_needs_no_recovery(measured):
    # Whatever a killed coordinator was doing left nothing behind but the row:
    # a fresh tick sees a stale figure someone asked for and measures it.
    store(observations.HEALTH, asked=10, tried=900, result={"checks": CHECKS})
    assert OpsState.objects.filter(key__startswith="operator.").count() == 1
    assert observations.tick() == observations.HEALTH


def test_other_operator_rows_do_not_break_the_tick(measured):
    store("operator.probe", asked=1)  # a row an earlier build left behind
    store(observations.HEALTH, asked=1)
    assert observations.tick() == observations.HEALTH


def test_the_job_is_registered_and_survives_the_scheduler_refresh(monkeypatch):
    from apps.runner.management.commands.runworker import _SchedulerManager

    manager = _SchedulerManager()
    removed = []
    manager._scheduler = SimpleNamespace(
        get_jobs=lambda: [SimpleNamespace(id="operator-probe"), SimpleNamespace(id="report-999999")],
        remove_job=removed.append, add_job=lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("apps.reports.notify.flush_broken_buffers", lambda: None)
    manager.refresh()
    assert removed == ["report-999999"]


# ── the pages without a usable coordinator or measurement ────────────────────

def test_system_page_runs_the_checks_in_the_request_without_a_coordinator(operator_client, full_checks):
    html = operator_client.get("/system").content.decode()
    assert full_checks == [1]
    assert "local-check" in html and "data-operator-observation" not in html and "Checked" not in html
    assert not OpsState.objects.filter(key__startswith="operator.").exists()  # nobody to ask


def test_system_page_does_not_present_an_old_measurement_without_a_coordinator(operator_client, full_checks):
    store(observations.HEALTH, asked=5, tried=10800, result={"checks": [{"label": "old-check", "ok": True, "detail": "old"}]})
    html = operator_client.get("/system").content.decode()
    assert "local-check" in html and "old-check" not in html


def test_system_page_falls_back_when_the_coordinator_never_manages_a_measurement(operator_client, coordinator, full_checks):
    store(observations.HEALTH, asked=5, tried=5)  # attempted, nothing to show
    html = operator_client.get("/system").content.decode()
    assert "local-check" in html and "Checking…" not in html


def test_system_page_shows_a_stale_measurement_with_its_true_age(operator_client, coordinator, full_checks):
    store(observations.HEALTH, asked=5, tried=7300, result={"checks": CHECKS})
    html = operator_client.get("/system").content.decode()
    assert full_checks == []
    assert "PostgreSQL 16" in html and "Checked 2 hours ago" in html and "data-operator-observation" in html


def test_system_page_uses_the_shared_version_info(operator_client, monkeypatch):
    from apps.core import version

    monkeypatch.setattr(version, "version_info", lambda detailed=False: {
        "version": "9.9.9", "sha": "abc", "build_time": "x", "source_url": "https://example.test/src",
        "framework": {"tag": "", "meta_schema": ""}, "unapplied_migrations": ["core.0099_x"] if detailed else []})
    html = operator_client.get("/system").content.decode()
    assert "9.9.9" in html and "core.0099_x" in html


def test_org_without_a_coordinator_is_measured_in_the_request(operator_client, org, tmp_path):
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "a.csv").write_bytes(b"x" * 2 * 1048576)
    from apps.datasources.models import DataSource

    DataSource.objects.create(org=org, name="a", type="file", config={"upload": True, "path": "a.csv"})
    html = operator_client.get(f"/operator/orgs/{org.slug}/").content.decode()
    assert "2.0 MB" in html and "measuring" not in html and "data-operator-observation" not in html


def test_org_with_no_uploaded_files_shows_zero(operator_client, org, coordinator):
    from apps.datasources.models import DataSource

    # An upload source with nothing stored yet, and an org with no sources at all.
    DataSource.objects.create(org=org, name="empty", type="file", config={"upload": True, "path": "gone.csv"})
    assert bounded.usage(org)["storage_mb"] is None  # coordinator alive: the first measurement is on its way
    store(observations.usage_key(org.pk), asked=5, tried=5, result={"bytes": 0})
    html = operator_client.get(f"/operator/orgs/{org.slug}/").content.decode()
    assert "0.0 MB" in html and "measured just now" in html and "measuring…</span>" not in html
    WorkerHeartbeat.objects.all().delete()
    assert bounded.usage(org)["storage_mb"] == 0.0
    assert bounded.usage(Organization.objects.create(slug="bare", name="Bare"))["storage_mb"] == 0.0


def test_uploads_use_one_size_implementation(org, settings):
    from apps.datasources.models import DataSource
    from apps.orgs import quotas

    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "a.csv").write_bytes(b"x" * 1048576)
    DataSource.objects.create(org=org, name="a", type="file", config={"upload": True, "path": "a.csv"})
    assert bounded.usage(org)["storage_mb"] == 1.0 == round(quotas.storage_bytes_used(org) / 1048576, 1)
    assert "storage_mb" not in quotas.usage_state(org)  # the studios page no longer walks the files


# ── permissions and audit ────────────────────────────────────────────────────

def borrowed(client, operator):
    from apps.core.impersonation import SESSION_EXPIRES_AT, SESSION_OPERATOR_ID

    session = client.session
    session[SESSION_OPERATOR_ID] = operator.pk
    session[SESSION_EXPIRES_AT] = (timezone.now() + timedelta(minutes=5)).isoformat()
    session.save()


def test_health_poll_is_for_the_current_operator_only(operator_client, operator, coordinator, make_user):
    assert Client().get("/api/system/diagnostics").status_code == 401
    other = Client()
    other.force_login(make_user("plain@portal.local"))
    assert other.get("/api/system/diagnostics").status_code == 404
    assert operator_client.post("/api/system/diagnostics").status_code == 405
    assert operator_client.get("/api/system/diagnostics").status_code == 200
    borrowed(operator_client, operator)
    assert operator_client.get("/api/system/diagnostics").status_code == 404
    assert operator_client.get("/system").status_code == 404


def test_usage_poll_is_for_the_current_operator_only(operator_client, operator, coordinator, org, make_user):
    url = f"/operator/orgs/{org.slug}/usage"
    assert Client().get(url, HTTP_ACCEPT="application/json").status_code == 401
    other = Client()
    other.force_login(make_user("plain2@portal.local", org=org))
    assert other.get(url).status_code == 404
    assert operator_client.post(url).status_code == 405
    assert operator_client.get("/operator/orgs/missing/usage").status_code == 404
    body = operator_client.get(url).json()
    assert body == {"failed": False}
    assert OpsState.objects.filter(key=observations.usage_key(org.pk)).exists()
    borrowed(operator_client, operator)
    assert operator_client.get(url).status_code == 404


def test_polling_and_measuring_write_no_audit_rows(operator_client, coordinator, org, monkeypatch):
    before = AuditLog.objects.count()
    for url in ("/system", "/api/system/diagnostics", f"/operator/orgs/{org.slug}/", f"/operator/orgs/{org.slug}/usage"):
        assert operator_client.get(url).status_code == 200
    monkeypatch.setattr(observations, "run_bounded", lambda argv, timeout: b'{"bytes": 1}')
    store(observations.usage_key(org.pk), asked=1)
    observations.tick()
    assert AuditLog.objects.count() == before
    assert not [name for name in ACTIONS if name.endswith(".refresh") and name.startswith("operator.")]


def test_header_summary_never_depends_on_the_coordinator(operator_client):
    summary = operator_client.get("/api/system/health").json()
    assert summary["message"] != "System health unavailable"
    assert not OpsState.objects.filter(key__startswith="operator.").exists()


def test_snapshot_age_and_relative_text():
    shown = observations.snapshot(store(observations.HEALTH, tried=130, result={"checks": []}).payload)
    assert 129 <= shown["age_seconds"] <= 135
    assert observations.age_text(3) == "just now"
    assert observations.age_text(42) == "42 seconds ago"
    assert observations.age_text(60) == "1 minute ago"
    assert observations.age_text(130) == "2 minutes ago"
    assert observations.age_text(7300) == "2 hours ago"
    assert observations.age_text(None) == ""
    assert observations.snapshot({}) == {"failed": False}


def test_check_detail_keeps_the_explanation_without_url_secrets():
    from apps.core.management.commands.operator_measure import _line

    assert _line("PostgreSQL 16.15 on x86_64") == "PostgreSQL 16.15 on x86_64"
    assert _line("no live worker heartbeat (is the worker container running?)").endswith("running?)")
    assert _line("cannot reach https://user:secret@bucket.example/path?X-Amz-Signature=abc now") == \
        "cannot reach https://bucket.example/path now"
    assert _line("first\nsecond\x00third") == "first second third"
    assert len(_line("x" * 5000)) == 400


def test_current_workers_and_sandbox_mismatch_are_full_aggregate(settings):
    settings.TRELLUM_SANDBOX = "docker"
    WorkerHeartbeat.objects.create(worker_id="aggregate-ok", sandbox_mode="docker")
    WorkerHeartbeat.objects.bulk_create([
        WorkerHeartbeat(worker_id=f"aggregate-{i}", role="runner", sandbox_mode="", max_concurrent=4)
        for i in range(1000)
    ])
    with CaptureQueriesContext(connection) as queries:
        facts = health.worker_facts()
    assert len(queries) == 2
    assert facts["runners"] == 1001 and facts["sandbox_mismatches"] == 1000
    assert len(facts["mismatched_ids"]) == 5
