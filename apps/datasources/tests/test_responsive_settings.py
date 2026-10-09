"""Saving is independent of network probes, whose results belong to one revision."""
from unittest.mock import Mock

import pytest
from django.test import Client
from django.utils import timezone

from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.testing import check_binding, check_revision

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("org_scope", [False, True])
def test_enhanced_save_persists_without_probe(login, org_admin, org, studio_tree, monkeypatch, org_scope):
    probe = Mock(side_effect=AssertionError("saving must not connect"))
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    owner = {"org": org} if org_scope else {"studio": studio_tree}
    ds = DataSource.objects.create(
        **owner, name="warehouse", type="postgres", config={"host": "old"},
        credentials={"user": "u", "password": "stored-secret"},
        last_check_ok=True, last_check_at=timezone.now(),
    )
    base = f"/orgs/{org.slug}" if org_scope else f"/s/{org.slug}/{studio_tree.slug}"
    response = login(org_admin).post(
        base + "/settings/datasources", {"id": ds.pk, "name": ds.name, "type": "postgres", "host": "new"},
        HTTP_X_TRELLUM_FORM="1",
    )
    assert response.status_code == 200
    body = response.json()
    ds.refresh_from_db()
    assert ds.config["host"] == "new" and ds.credentials["password"] == "stored-secret"
    assert ds.last_check_ok is None and ds.last_check_at is None and not ds.last_check_error
    assert body["message"] == "Saved. Testing connection…"
    assert body["revision"] == check_revision(ds)
    test_path = "/settings/datasources/warehouse/test" if org_scope else "/api/datasources/warehouse/test"
    assert body["test_url"] == base + test_path
    assert body["can_edit"] and body["edit_url"] == base + f"/settings/datasources?edit={ds.pk}"
    assert body["delete_url"] == base + "/settings/datasources" and body["values"]["id"] == ds.pk
    assert "stored-secret" not in response.content.decode()
    probe.assert_not_called()


def test_enhanced_credentials_persist_without_probe(login, org_admin, org, studio_tree, monkeypatch):
    probe = Mock(side_effect=AssertionError("saving must not connect"))
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    RepoDataSource.objects.create(studio=studio_tree, name="wh", type="postgres", config={"host": "h"})
    response = login(org_admin).post(
        f"/s/{org.slug}/{studio_tree.slug}/settings/datasources",
        {"action": "configure", "name": "wh", "user": "u", "password": "secret"}, HTTP_X_TRELLUM_FORM="1",
    )
    assert response.status_code == 200
    ds = DataSource.objects.get(studio=studio_tree, name="wh")
    assert ds.credentials == {"user": "u", "password": "secret"} and ds.last_check_ok is None
    body = response.json()
    assert body["configured"] and body["remove_credentials"] and not body["can_edit"]
    assert body["configure_url"] == "?configure=wh#configure"
    probe.assert_not_called()


def test_validation_stays_on_page(login, org_admin, org, studio_tree):
    response = login(org_admin).post(
        f"/s/{org.slug}/{studio_tree.slug}/settings/datasources",
        {"name": "bad name", "type": "file"}, HTTP_X_TRELLUM_FORM="1",
    )
    assert response.status_code == 400 and response.json()["errors"]["name"]
    assert not DataSource.objects.exists()


@pytest.fixture
def bound_source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree, name="wh", type="postgres", config={"host": "h"},
        credentials={"user": "u", "password": "p"},
    )


def test_explicit_test_requires_requested_revision(login, org_admin, org, studio_tree, bound_source, monkeypatch):
    probe = Mock(return_value=(True, "Connected."))
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    revision = check_revision(bound_source)
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/wh/test"
    client = login(org_admin)
    body = client.post(url, {"revision": revision}).json()
    assert body["ok"] and body["revision"] == revision and not body["stale"]
    bound_source.refresh_from_db()
    assert bound_source.last_check_ok is True
    bound_source.config = {"host": "new"}
    bound_source.save()
    probe.reset_mock()
    response = client.post(url, {"revision": revision})
    assert response.status_code == 409 and response.json()["stale"]
    probe.assert_not_called()


@pytest.mark.parametrize("change_declaration", [False, True])
def test_changed_configuration_during_probe_is_not_applied(bound_source, studio_tree, monkeypatch, change_declaration):
    declaration = RepoDataSource.objects.create(studio=studio_tree, name="wh", type="postgres", config={"host": "h"})
    def probe(source):
        if change_declaration:
            declaration.config = {"host": "new-repository-host"}
            declaration.save()
        else:
            changed = DataSource.objects.get(pk=bound_source.pk)
            changed.config = {"host": "new"}
            changed.save()
        return True, "Connected."
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    rebuild = Mock()
    monkeypatch.setattr("apps.datasources.testing.rebuild_unblocked", rebuild)
    ok, detail = check_binding(bound_source, studio_tree)
    assert not ok and "Configuration changed" in detail and bound_source._check_stale
    bound_source.refresh_from_db()
    assert bound_source.last_check_ok is None
    rebuild.assert_not_called()


def test_changed_before_enqueue_does_not_queue(bound_source, studio_tree, monkeypatch):
    from apps.reports.models import Report
    from apps.runner.models import Run
    from apps.datasources.testing import rebuild_unblocked

    Report.objects.create(studio=studio_tree, slug="sales", config={"data_sources": ["wh"]})
    monkeypatch.setattr("apps.datasources.testing.test_datasource", lambda source: (True, "Connected."))
    def rebuild_after_save(binding, **kwargs):
        newer = DataSource.objects.get(pk=binding.pk)
        newer.config = {"host": "new"}
        newer.last_check_ok = None
        newer.save()
        return rebuild_unblocked(binding, **kwargs)
    monkeypatch.setattr("apps.datasources.testing.rebuild_unblocked", rebuild_after_save)
    ok, detail = check_binding(bound_source, studio_tree)
    assert not ok and "Configuration changed" in detail
    assert not Run.objects.exists()


def test_changed_declaration_before_probe_refuses_requested_snapshot(bound_source, studio_tree, monkeypatch):
    revision = check_revision(bound_source, studio_tree)
    RepoDataSource.objects.create(studio=studio_tree, name="wh", type="postgres", config={"host": "new"})
    probe = Mock()
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    ok, detail = check_binding(bound_source, studio_tree, expected_revision=revision)
    assert not ok and "Configuration changed" in detail
    probe.assert_not_called()


def test_enhanced_posts_still_require_csrf(org_admin, org, studio_tree):
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    response = client.post(f"/s/{org.slug}/{studio_tree.slug}/settings/datasources", {"name": "wh", "type": "postgres"}, HTTP_X_TRELLUM_FORM="1")
    assert response.status_code == 403 and not DataSource.objects.exists()
