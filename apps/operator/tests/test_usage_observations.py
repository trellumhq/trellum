from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.core import operator_observations as observations
from apps.core.models import OpsState
from apps.core.operator_probe_worker import ProbeSupervisor
from apps.core.workspace import initialize_workspace_identity
from apps.datasources.models import DataSource
from apps.operator import usage_observations as usage

from apps.core.tests.test_operator_probe_process import probe_process_env

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def storage_workspace(settings, tmp_path):
    assert connection.vendor == "postgresql", "usage gate requires disposable PostgreSQL"
    settings.DATA_DIR = tmp_path
    return initialize_workspace_identity()


def test_full_reference_total_and_narrow_source_fields(storage_workspace, org, studio):
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "org.csv").write_bytes(b"a" * 1048576)
    studio.project_root.mkdir(parents=True)
    (studio.project_root / "studio.csv").write_bytes(b"b" * 1048576)
    DataSource.objects.create(org=org, name="org-file", type="file", config={"upload": True, "path": "org.csv"}, credentials={"password": "private" * 10000})
    for name in ("studio-file", "same-reference"):
        DataSource.objects.create(studio=studio, name=name, type="file", config={"upload": True, "path": "studio.csv"})
    result = usage.scan(org.pk, storage_workspace)
    assert result["quality"] == "complete" and result["bytes"] == 3 * 1048576
    assert result["inspected"] == 3
    assert "credentials" not in repr(result)


def test_missing_is_disclosed_and_nonregular_never_becomes_zero(storage_workspace, org):
    source = DataSource.objects.create(org=org, name="missing", type="file", config={"upload": True, "path": "missing.csv"})
    missing = usage.scan(org.pk, storage_workspace)
    assert missing["bytes"] == 0 and missing["missing"] == 1
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "folder").mkdir()
    source.config["path"] = "folder"
    source.save(update_fields=["config", "updated_at"])
    result = usage.scan(org.pk, storage_workspace)
    assert result["quality"] == "incomplete" and "bytes" not in result


def test_symlink_containment_and_metadata_budget(storage_workspace, org, tmp_path, monkeypatch):
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "inside.csv").write_bytes(b"inside")
    (org.datasources_dir / "alias.csv").symlink_to("inside.csv")
    source = DataSource.objects.create(org=org, name="alias", type="file", config={"upload": True, "path": "alias.csv"})
    assert usage.scan(org.pk, storage_workspace)["bytes"] == 6
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"private")
    (org.datasources_dir / "escape.csv").symlink_to(outside)
    source.config["path"] = "escape.csv"
    source.save(update_fields=["config", "updated_at"])
    escaped = usage.scan(org.pk, storage_workspace)
    assert escaped["quality"] == "incomplete" and "bytes" not in escaped
    monkeypatch.setattr(usage, "METADATA_PER_BATCH", 1)
    limited = usage.scan(org.pk, storage_workspace)
    assert limited["reason"] == "metadata_limit" and limited["metadata_calls"] == 1


def test_unreadable_and_changed_workspace_never_publish_partial(storage_workspace, org, monkeypatch):
    DataSource.objects.create(org=org, name="file", type="file", config={"upload": True, "path": "file.csv"})
    monkeypatch.setattr(usage.Metadata, "size", lambda *args: (_ for _ in ()).throw(PermissionError()))
    result = usage.scan(org.pk, storage_workspace)
    assert result["quality"] == "incomplete" and result["reason"] == "unreadable"
    assert "bytes" not in result
    assert usage.scan(org.pk, "foreign-workspace")["quality"] == "incomplete"


def test_candidate_limit_fails_before_file_metadata(storage_workspace, org):
    DataSource.objects.bulk_create([
        DataSource(org=org, name=f"file-{i}", type="file", config={"upload": True, "path": "missing.csv"})
        for i in range(10001)
    ])
    result = usage.scan(org.pk, storage_workspace)
    assert result["reason"] == "candidate_limit" and result["metadata_calls"] == 0
    assert "bytes" not in result


def test_actual_supervised_usage_is_shared_scoped_and_stales(probe_process_env, org):
    from django.contrib.auth import get_user_model
    actor = get_user_model().objects.create_user(email="usage@portal.local", is_operator_flag=True)
    org.datasources_dir.mkdir(parents=True)
    (org.datasources_dir / "file.csv").write_bytes(b"x" * 2 * 1048576)
    DataSource.objects.create(org=org, name="file", type="file", config={"upload": True, "path": "file.csv"})
    key = observations.nonce(actor.pk, "upload-usage", org.pk)
    entry, created = observations.admit(actor.pk, "upload-usage", key, org_id=org.pk)
    ProbeSupervisor(probe_process_env).tick()
    stored = observations.snapshot("upload-usage", org.pk)
    assert stored["quality"] == "complete" and stored["bytes"] == 2 * 1048576
    assert stored["id"] == entry["id"] and stored["scope"] == "shared-workspace"
    row = OpsState.objects.get(key=observations.snapshot_key("upload-usage", org.pk))
    row.payload["completed_at"] = (timezone.now() - timedelta(minutes=6)).isoformat()
    row.save(update_fields=["payload"])
    stale = observations.snapshot("upload-usage", org.pk)
    assert stale["state"] == "stale" and stale["bytes"] == 2 * 1048576


def test_incomplete_result_retains_previous_total(storage_workspace, org):
    from django.contrib.auth import get_user_model
    from apps.runner.models import WorkerHeartbeat
    actor = get_user_model().objects.create_user(email="incomplete@portal.local", is_operator_flag=True)
    WorkerHeartbeat.objects.create(worker_id="usage-node")
    OpsState.record(observations.snapshot_key("upload-usage", org.pk), workspace_id=storage_workspace,
                    completed_at=timezone.now().isoformat(), quality="complete", bytes=42)
    observations.admit(actor.pk, "upload-usage", observations.nonce(actor.pk, "upload-usage", org.pk), org_id=org.pk)
    entry = observations.claim("usage-node", "epoch")
    result = {"version": 1, "kind": "upload-usage", "workspace_id": storage_workspace,
              "producer_fingerprint": entry["producer_fingerprint"], "quality": "incomplete", "inspected": 3}
    observations.finish(entry, result=result)
    assert observations.snapshot("upload-usage", org.pk)["bytes"] == 42
    assert observations.observation(entry["id"], kind="upload-usage", org_id=org.pk)["state"] == "failed"


def test_org_identity_status_and_live_operator_boundary(storage_workspace, org, other_org, client):
    from django.contrib.auth import get_user_model
    from apps.runner.models import WorkerHeartbeat
    actor = get_user_model().objects.create_user(email="scope-usage@portal.local", is_operator_flag=True)
    WorkerHeartbeat.objects.create(worker_id="scope-usage-node")
    client.force_login(actor)
    key = observations.nonce(actor.pk, "upload-usage", org.pk)
    response = client.post(f"/operator/orgs/{org.slug}/usage/refresh", {"request_key": key}, HTTP_ACCEPT="application/json")
    assert response.status_code == 202
    operation_id = response.json()["action"]["id"]
    assert client.get(f"/operator/orgs/{other_org.slug}/usage/{operation_id}").status_code == 404
    assert client.get(response.json()["action"]["status_url"]).status_code == 200
    get_user_model().objects.filter(pk=actor.pk).update(is_operator_flag=False)
    assert client.get(response.json()["action"]["status_url"]).status_code == 404
