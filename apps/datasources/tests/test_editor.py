"""Draft checks use effective configuration without changing saved evidence."""
from unittest.mock import Mock
from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.test import Client
from django.utils import timezone
from playwright.sync_api import sync_playwright

from apps.core import roles
from apps.core.models import AuditLog
from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import binding_state
from apps.datasources.testing import check_revision, draft_revision
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree, name="warehouse", type="postgres", config={"host": "saved-host"},
        credentials={"user": "saved-user", "password": "saved-secret"},
        last_check_at=timezone.now(), last_check_ok=True,
    )


@pytest.fixture
def editor_url(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/settings/datasources/warehouse/edit"


def draft_data(source, studio_tree, **values):
    return {"action": "test_draft", "name": source.name, "type": source.type,
            "host": "draft-host", "user": "draft-user", "password": "replacement-secret",
            "revision": draft_revision(binding_state(source, studio_tree)), **values}


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("ok", [False, True])
def test_draft_replacements_are_transient(login, org_admin, source, editor_url, studio_tree, monkeypatch, declared, ok):
    if declared:
        RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo-host"})
    before = (source.config, source.credentials, source.last_check_at, source.last_check_ok, source.updated_at)
    def probe(candidate):
        assert candidate.config["host"] == ("repo-host" if declared else "draft-host")
        assert candidate.credentials == {"user": "draft-user", "password": "replacement-secret"}
        return ok, "replacement-secret rejected" if not ok else "Confirmed."
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    response = login(org_admin).post(editor_url, draft_data(source, studio_tree))
    assert response.status_code == 200 and response.json()["ok"] == ok
    assert response.json()["draft"] and "replacement-secret" not in response.content.decode()
    source.refresh_from_db()
    assert (source.config, source.credentials, source.last_check_at, source.last_check_ok, source.updated_at) == before
    assert not Run.objects.exists() and not AuditLog.objects.filter(action="datasource.test").exists()


def test_blank_secret_and_declared_lifecycle_override(login, org_admin, source, editor_url, studio_tree, monkeypatch):
    RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres",
                                 config={"host": "repo-host", "new_connection_per_query": True})
    def probe(candidate):
        assert candidate.credentials["password"] == "saved-secret"
        assert candidate.config["new_connection_per_query"] is False
        return True, "ok"
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    response = login(org_admin).post(editor_url, draft_data(source, studio_tree, password=""))
    assert response.json()["ok"]


@pytest.mark.parametrize("during", [False, True])
@pytest.mark.parametrize("declaration_changed", [False, True])
def test_obsolete_draft_is_rejected(login, org_admin, source, editor_url, studio_tree, monkeypatch, during, declaration_changed):
    decl = RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo-host"})
    data = draft_data(source, studio_tree)
    def change():
        if declaration_changed:
            decl.config = {"host": "new-repo-host"}
            decl.save()
        else:
            changed = DataSource.objects.get(pk=source.pk)
            changed.config = {"host": "new-saved-host"}
            changed.save()
    def probe(candidate):
        change()
        return True, "Old success."
    mock = Mock(side_effect=probe)
    monkeypatch.setattr("apps.datasources.testing.test_datasource", mock)
    if not during:
        change()
    response = login(org_admin).post(editor_url, data)
    assert response.status_code == 409 and response.json()["stale"]
    assert "Old success" not in response.content.decode() and not Run.objects.exists()
    assert mock.call_count == int(during)


def test_new_declared_source_can_test_before_first_save(login, org_admin, editor_url, studio_tree, monkeypatch):
    RepoDataSource.objects.create(studio=studio_tree, name="warehouse", type="postgres", config={"host": "repo-host"})
    client = login(org_admin)
    page = client.get(editor_url)
    assert page.context["console_page_title"] == "Edit data source"
    monkeypatch.setattr("apps.datasources.testing.test_datasource", lambda candidate: (True, "ok"))
    response = client.post(editor_url, {"action": "test_draft", "revision": page.context["revision"], "user": "u", "password": "p"})
    assert response.json()["ok"] and not DataSource.objects.exists()
    html = page.content.decode()
    assert "Test draft" in html and "Return to data sources" in html and 'id="ds-form"' not in html


def test_editor_save_acknowledges_without_probe_and_native_errors_keep_draft(login, org_admin, source, editor_url, monkeypatch):
    probe = Mock(side_effect=AssertionError("save must not probe"))
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    client = login(org_admin)
    response = client.post(editor_url, {"action": "save", "name": "renamed", "type": "postgres", "host": "new"}, HTTP_X_TRELLUM_FORM="1")
    assert response.json()["edit_url"].endswith("/renamed/edit")
    source.refresh_from_db()
    assert source.name == "renamed" and source.last_check_ok is None
    assert response.json()["revision"] == check_revision(source)
    new_url = response.json()["edit_url"]
    response = client.post(new_url, {"action": "save", "name": "bad name", "type": "postgres", "host": "retained-host"})
    assert response.status_code == 200 and 'value="retained-host"' in response.content.decode()
    assert response.context["form"].errors["name"]
    probe.assert_not_called()


def test_editor_authorization_csrf_and_unknown_action(login, make_user, grant_studio, source, org, studio_tree, editor_url, org_admin):
    viewer = make_user("editor-viewer@example.test", org=org)
    grant_studio(viewer, studio_tree, roles.VIEWER)
    assert login(viewer).get(editor_url).status_code == 403
    assert login(viewer).post(editor_url, draft_data(source, studio_tree)).status_code == 403
    client = Client(enforce_csrf_checks=True)
    client.force_login(org_admin)
    assert client.post(editor_url, draft_data(source, studio_tree)).status_code == 403
    response = login(org_admin).post(editor_url, {"name": "unintended", "type": "postgres"})
    assert response.status_code == 400
    source.refresh_from_db()
    assert source.name == "warehouse"


def test_native_save_and_test_uses_saved_configuration(login, org_admin, source, editor_url, monkeypatch):
    def probe(candidate):
        assert candidate.config["host"] == "native-host"
        assert candidate.credentials["password"] == "saved-secret"
        return False, "Native test failed."
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    client = login(org_admin)
    response = client.post(editor_url, {"action": "save", "name": source.name, "type": "postgres", "host": "native-host"}, follow=True)
    source.refresh_from_db()
    assert source.config["host"] == "native-host" and source.last_check_ok is False
    assert response.status_code == 200 and "Native test failed" in response.content.decode()


def test_non_org_admin_cannot_choose_shared_credentials(login, make_user, grant_studio, source, org, studio_tree, editor_url, monkeypatch):
    admin = make_user("studio-editor@example.test", org=org)
    grant_studio(admin, studio_tree, roles.ADMIN)
    RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo"})
    probe = Mock()
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    response = login(admin).post(editor_url, {"action": "test_draft", "org_level": "1", "password": "x"})
    assert response.status_code == 403
    probe.assert_not_called()


def test_org_draft_uses_selected_binding_and_revision(login, org_admin, org, source, editor_url, studio_tree, monkeypatch):
    RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo"})
    shared = DataSource.objects.create(org=org, name=source.name, type="postgres", credentials={"user": "org-user", "password": "org-secret"})
    client = login(org_admin)
    page = client.get(editor_url)
    probe = Mock(return_value=(True, "ok"))
    monkeypatch.setattr("apps.datasources.testing.test_datasource", probe)
    response = client.post(editor_url, {"action": "test_draft", "org_level": "1", "password": "", "revision": page.context["org_revision"]})
    assert response.json()["ok"] and probe.call_args.args[0].credentials["password"] == "org-secret"
    response = client.post(editor_url, {"action": "save", "org_level": "1", "password": "new-org-secret"}, HTTP_X_TRELLUM_FORM="1")
    assert response.json()["editor_revision"] == draft_revision(binding_state(source, studio_tree))
    shared.refresh_from_db()
    assert response.json()["org_revision"] == draft_revision(binding_state(shared, studio_tree))
    assert source.credentials["password"] == "saved-secret"


def test_unverified_label_keeps_build_eligibility(login, org_admin, source, org, studio_tree, editor_url):
    source.last_check_at = source.last_check_ok = None
    source.save()
    RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo"})
    assert not binding_state(source, studio_tree).blocking
    client = login(org_admin)
    html = client.get(editor_url).content.decode()
    assert "Configured / not tested" in html and ">Connected<" not in html
    listing = client.get(f"/s/{org.slug}/{studio_tree.slug}/settings/datasources")
    assert ">Connected<" not in listing.content.decode()


@pytest.mark.parametrize("width", [1280, 390])
def test_rendered_editor_draft_failure_layout(login, org_admin, source, studio_tree, editor_url, monkeypatch, width):
    RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="postgres", config={"host": "repo-host"}, source_file="data-sources/config.yaml")
    client = login(org_admin)
    rendered = client.get(editor_url)
    monkeypatch.setattr("apps.datasources.testing.test_datasource", lambda candidate: (False, "Draft refused."))
    draft_response = client.post(editor_url, {"action": "test_draft", "revision": rendered.context["revision"], "password": "draft-only-secret"})
    source.refresh_from_db()
    assert source.credentials["password"] == "saved-secret" and source.last_check_ok is True
    root = Path(__file__).resolve().parents[3]
    artifacts = root / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / ".gitignore").write_text("*\n", encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def serve(route):
            path = urlparse(route.request.url).path
            if path.startswith("/static/"):
                file = root / "static" / path.removeprefix("/static/")
                if file.is_file():
                    route.fulfill(path=str(file), content_type="text/css" if file.suffix == ".css" else "application/javascript")
                else:
                    route.fulfill(status=404, body="")
            elif path.startswith("/_vendor/") or path == "/api/assistant/widget.js":
                route.fulfill(body="")
            elif route.request.method == "POST":
                route.fulfill(status=draft_response.status_code, content_type="application/json", body=draft_response.content)
            else:
                route.fulfill(content_type="text/html", body=rendered.content)
        page.route("**/*", serve)
        page.goto("http://datasources.test" + editor_url)
        assert page.locator('[name="password"]').input_value() == ""
        page.locator('[name="password"]').fill("draft-only-secret")
        assert page.locator('[data-ds-badge]').text_content() == "Draft changed"
        page.locator('[data-ds-draft-test]').click()
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('Draft refused.')")
        assert page.locator('[name="password"]').input_value() == "draft-only-secret"
        assert page.url.endswith(editor_url)
        assert "Last confirmed at" in page.locator('[data-ds-saved-evidence]').text_content()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(artifacts / f"datasource-editor-{width}.png"), full_page=True)
        assert not errors
        browser.close()
