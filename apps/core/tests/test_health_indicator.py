"""Persistent operator health navigation and bounded summary checks."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import health
from apps.core.models import OpsState
from apps.runner.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db


def _prepare_local_health(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    settings.CLEANUP_ENABLED = False
    settings.BACKUP_DIR = tmp_path / "backups"
    settings.BACKUP_DIR.mkdir()
    (settings.BACKUP_DIR / "status.json").write_text(
        json.dumps(
            {
                "last_success_at": timezone.now().isoformat(),
                "ok": True,
                "detail": "verified",
                "off_host_copy": True,
            }
        ),
        encoding="utf-8",
    )
    health._quick_schema_cache = None


def test_quick_checks_are_the_bounded_subset(settings, tmp_path, monkeypatch):
    _prepare_local_health(settings, tmp_path)
    settings.CLEANUP_ENABLED = True
    OpsState.record("cleanup", removed={})
    WorkerHeartbeat.objects.create(worker_id="combined", role=WorkerHeartbeat.ROLE_ALL)

    def heavy_probe():
        raise AssertionError("quick health called a heavy probe")

    monkeypatch.setattr(health, "_reclaimable_detail", heavy_probe)
    monkeypatch.setattr(health.shutil, "which", heavy_probe)
    monkeypatch.setattr(health.subprocess, "check_output", heavy_probe)

    checks = health.run_checks(quick=True)
    assert [check["label"] for check in checks] == [
        "data volume free space",
        "migrations",
        "backups",
        "retention",
        "worker",
    ]
    assert all(check["ok"] for check in checks)


def test_quick_schema_check_is_cached_for_one_poll_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "apps.core.version.schema_state",
        lambda: calls.append(True) or ("ok", []),
    )
    health._quick_schema_cache = None

    assert health._cached_schema_state() == ("ok", [])
    assert health._cached_schema_state() == ("ok", [])
    assert len(calls) == 1


def test_stale_worker_heartbeat_is_reported(settings, tmp_path):
    _prepare_local_health(settings, tmp_path)
    WorkerHeartbeat.objects.create(
        worker_id="stale",
        role=WorkerHeartbeat.ROLE_ALL,
        last_beat_at=timezone.now() - timedelta(seconds=61),
    )

    summary = health.health_summary(health.run_checks(quick=True))
    assert summary["message"] == "Worker offline"
    assert "Repository sync" in summary["detail"]


@pytest.mark.parametrize(
    ("role", "message", "impact"),
    [
        (WorkerHeartbeat.ROLE_COORDINATOR, "Worker offline", "Report builds"),
        (WorkerHeartbeat.ROLE_RUNNER, "Coordinator offline", "Repository sync"),
    ],
)
def test_split_worker_failures_are_specific(settings, tmp_path, role, message, impact):
    _prepare_local_health(settings, tmp_path)
    WorkerHeartbeat.objects.create(worker_id=role, role=role)

    summary = health.health_summary(health.run_checks(quick=True))
    assert summary["message"] == message
    assert impact in summary["detail"]


def test_worker_failure_is_prioritized_over_other_failures():
    summary = health.health_summary(
        [
            {"label": "backups", "ok": False, "detail": "private path"},
            {"label": "worker", "ok": False, "detail": "no live coordinator"},
        ]
    )
    assert summary == {
        "status": "error",
        "message": "Coordinator offline",
        "detail": "Repository sync and scheduled reports are paused",
        "failure_count": 2,
    }


def test_database_failure_does_not_expose_raw_error():
    summary = health.health_summary(
        [{"label": "worker", "ok": False, "detail": "password=secret host=db"}]
    )
    assert summary == {
        "status": "unknown",
        "message": "System health unavailable",
        "detail": "Open System health for details",
        "failure_count": 1,
    }


def test_health_endpoint_recovers_when_worker_returns(
    settings, tmp_path, login, superuser
):
    _prepare_local_health(settings, tmp_path)
    client = login(superuser)
    failed = client.get("/api/system/health")
    assert failed.status_code == 200
    assert failed.json()["message"] == "Worker offline"
    assert failed["Cache-Control"] == "no-store"

    WorkerHeartbeat.objects.create(worker_id="combined", role=WorkerHeartbeat.ROLE_ALL)
    recovered = client.get("/api/system/health")
    assert recovered.json()["status"] == "ok"


@pytest.mark.parametrize("fixture_name", ["org_admin", "member"])
def test_non_operators_cannot_read_health_or_see_navigation(
    request, login, fixture_name
):
    client = login(request.getfixturevalue(fixture_name))
    assert client.get("/api/system/health").status_code == 404
    html = client.get("/").content.decode()
    assert "data-console-health-url" not in html
    assert 'data-console-nav-group="operator"' not in html


def test_anonymous_health_request_requires_authentication(client):
    assert client.get("/api/system/health").status_code == 401


def test_instance_administrator_gets_health_navigation(
    login, superuser, monkeypatch
):
    monkeypatch.setattr(
        health,
        "quick_health_summary",
        lambda: {"status": "ok", "message": "", "detail": "", "failure_count": 0},
    )
    html = login(superuser).get("/").content.decode()
    assert 'data-console-health-url="/api/system/health"' in html
    assert 'data-console-nav-group="operator"' in html
    assert 'href="/system"' in html


def test_operator_flag_gets_health_endpoint(login, make_user, monkeypatch):
    operator = make_user("operator-health@example.test")
    operator.is_operator_flag = True
    operator.save(update_fields=["is_operator_flag"])
    monkeypatch.setattr(
        health,
        "quick_health_summary",
        lambda: {"status": "ok", "message": "", "detail": "", "failure_count": 0},
    )
    assert login(operator).get("/api/system/health").status_code == 200


def test_impersonation_hides_and_blocks_instance_health(
    login, superuser, member, monkeypatch
):
    from apps.core.impersonation import SESSION_OPERATOR_ID

    monkeypatch.setattr(
        health,
        "quick_health_summary",
        lambda: {"status": "ok", "message": "", "detail": "", "failure_count": 0},
    )
    client = login(superuser)
    session = client.session
    session[SESSION_OPERATOR_ID] = member.pk
    session.save()

    assert client.get("/api/system/health").status_code == 404
    assert client.get("/system").status_code == 404
    html = client.get("/").content.decode()
    assert "data-console-health-url" not in html
    assert 'data-console-nav-group="operator"' not in html
