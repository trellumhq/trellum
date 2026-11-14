"""Portal scalar values remain encrypted and never appear in saved responses."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import sync_playwright

from apps.datasources.forms import DataSourceForm
from apps.datasources.models import DataSource, RepoDataSource, SCALAR_TYPES
from apps.datasources.repo_sync import parse_declaration
from apps.datasources.status import binding_state

pytestmark = pytest.mark.django_db


def form_for(studio, kind="secret", instance=None, **extra):
    return DataSourceForm({"name": "litellm_key", "type": kind, "value": "dummy-scalar-value", **extra},
                          instance=instance, studio=studio, fixed_scope="studio")


@pytest.mark.parametrize("kind,value", [("string", "gateway"), ("secret", "key"), ("text", "a\nb"),
                                         ("number", "0"), ("boolean", "false"), ("json", '{"a":1}')])
def test_scalar_storage_and_visible_initial(studio_tree, kind, value):
    form = form_for(studio_tree, kind, value=value)
    assert form.is_valid(), form.errors
    ds = form.save()
    assert ds.credentials == {"value": value}
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT credentials FROM datasources_datasource WHERE id = %s", [ds.pk])
        raw = cursor.fetchone()[0]
    assert raw != {"value": value} and '"value":' not in str(raw)
    assert "value" not in ds.config and not ds.is_uploaded
    initial = DataSourceForm(instance=ds, studio=studio_tree)
    assert initial.initial.get("value", "") == ("" if kind == "secret" else value)
    assert not ds.missing_fields()


@pytest.mark.parametrize("kind,value", [("number", "1_0"), ("number", "NaN"), ("number", "oops"), ("number", "Infinity"),
                                         ("boolean", "yes"), ("json", '{"a": NaN}'), ("json", "[]"), ("json", "bad")])
def test_invalid_scalar_values_do_not_echo_input(studio_tree, kind, value):
    form = form_for(studio_tree, kind, value=value, secret="on")
    assert not form.is_valid()
    assert value not in str(form.errors)
    assert value not in str(form["value"])


@pytest.mark.parametrize("kind", SCALAR_TYPES)
def test_scalar_test_is_successful_without_requiring_a_value(studio_tree, kind):
    from apps.datasources.testing import test_datasource
    ok, detail = test_datasource(DataSource(studio=studio_tree, name="scalar", type=kind))
    assert ok and detail == "Scalar values are resolved when the report requests them."


def test_scalar_utf8_value_is_byte_bounded(studio_tree):
    form = form_for(studio_tree, value="é" * 9000)
    assert not form.is_valid()
    assert "16 KiB" in str(form.errors)


def test_keep_and_clear_secret_and_plain_empty(studio_tree):
    form = form_for(studio_tree)
    assert form.is_valid()
    ds = form.save()
    form = form_for(studio_tree, instance=ds, value="")
    assert form.is_valid()
    assert form.save().credentials["value"] == "dummy-scalar-value"
    form = form_for(studio_tree, instance=ds, value="", clear_value="on")
    assert form.is_valid()
    assert form.save().credentials["value"] == ""
    form = form_for(studio_tree, "string", instance=ds, value="")
    assert form.is_valid()
    assert form.save().credentials["value"] == ""


def test_org_reference_requires_org_admin_and_does_not_copy(studio_tree):
    form = form_for(studio_tree, secret_reference="assistant_api_key")
    assert not form.is_valid()
    form.allow_secret_reference = True
    form = DataSourceForm({"name": "litellm_key", "type": "secret", "secret_reference": "assistant_api_key"},
                          studio=studio_tree, allow_secret_reference=True)
    assert form.is_valid(), form.errors
    ds = form.save()
    assert ds.config["secret_reference"] == "assistant_api_key" and ds.credentials is None
    clear = DataSourceForm({"name": "litellm_key", "type": "secret", "secret_reference": "assistant_api_key", "clear_value": "on"},
                           instance=ds, studio=studio_tree, allow_secret_reference=True)
    assert clear.is_valid()
    ds = clear.save()
    assert "secret_reference" not in ds.config and ds.credentials["value"] == ""


@pytest.mark.parametrize("kind", SCALAR_TYPES)
def test_declared_missing_scalar_never_blocks(studio_tree, kind):
    decl = RepoDataSource.objects.create(studio=studio_tree, name="scalar", type=kind)
    assert not binding_state(DataSource(studio=studio_tree, name="scalar", type=kind), studio_tree).blocking
    from apps.datasources.status import _state
    assert not _state("scalar", decl, None, []).blocking
    ds = DataSource(studio=studio_tree, name="scalar", type=kind, last_check_ok=False,
                    last_check_error="Not testable")
    assert not _state("scalar", decl, ds, []).blocking


def test_secret_create_edit_clear_and_declared_editor(login, org_admin, studio_tree):
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources"
    client = login(org_admin)
    response = client.post(base + "/new", {"name": "litellm_key", "type": "secret", "value": "secret-fixture-line"},
                           HTTP_X_TRELLUM_FORM="1")
    assert response.status_code == 200
    assert "secret-fixture-line" not in response.content.decode()
    ds = DataSource.objects.get(studio=studio_tree, name="litellm_key")
    RepoDataSource.objects.create(studio=studio_tree, name=ds.name, type="secret")
    response = client.get(base + "/litellm_key/edit")
    assert response.status_code == 200
    assert "secret-fixture-line" not in response.content.decode()
    assert response.context["form"].fields["name"].disabled
    response = client.post(base + "/litellm_key/edit", {"action": "save", "clear_value": "on"})
    assert response.status_code in (200, 302)
    ds.refresh_from_db()
    assert ds.credentials["value"] == ""


def test_scalar_declaration_drops_inline_value():
    entries = parse_declaration("sources:\n  litellm_key:\n    type: string\n    secret: true\n    value: do-not-store\n    secret_reference: assistant_api_key\n")
    assert "do-not-store" not in str(entries)
    assert "assistant_api_key" not in str(entries)


def test_file_text_upload_mode(login, org_admin, studio_tree, tmp_path, monkeypatch):
    ds = DataSource.objects.create(studio=studio_tree, name="prompt", type="file", config={"upload": True, "mode": "text"})
    monkeypatch.setattr("apps.datasources.views.health.free_bytes", lambda: 10**12)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/prompt/upload"
    response = login(org_admin).post(base, {"file": SimpleUploadedFile("bad.csv", b"x")})
    assert response.status_code == 400
    response = login(org_admin).post(base, {"file": SimpleUploadedFile("prompt.txt", b"hello")})
    assert response.status_code == 200, response.content
    page = login(org_admin).get(f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources")
    assert "Text file" in page.content.decode()


def test_scalar_browser_switches_fields_and_masks():
    from apps.datasources.tests.test_settings_browser import page_html
    html = page_html().replace('<option value="postgres">Postgres</option>', '<option value="postgres">Postgres</option><option value="secret">Secret</option><option value="text">Text</option>')
    html = html.replace('<input name="host"', '<div data-f="value"><input name="value"></div><div data-f="secret"><input name="secret" type="checkbox"></div><input name="host"')
    html = html.replace('{"postgres":[]}', json.dumps({"postgres": [], "secret": ["value", "secret"], "text": ["value", "secret"]}))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.route("http://datasources.test/settings", lambda route: route.fulfill(content_type="text/html", body=html))
        page.goto("http://datasources.test/settings")
        page.select_option('[name="type"]', "secret")
        assert page.locator('[name="value"]').get_attribute("type") == "password"
        assert not page.locator('[data-f="path"]').is_visible()
        page.select_option('[name="type"]', "text")
        assert page.locator('textarea[name="value"]').is_visible()
        browser.close()


@pytest.mark.parametrize("kind", ["string", "text", "number", "boolean", "json"])
def test_plain_clear_normalizes_editor_and_stays_cleared(login, org_admin, studio_tree, kind):
    values = {"string": "https://gateway.test", "text": "first\nsecond", "number": "0.25", "boolean": "false", "json": '{"a": 1}'}
    ds = DataSource.objects.create(studio=studio_tree, name="setting", type=kind, credentials={"value": values[kind]})
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources/setting/edit"
    client = login(org_admin)
    payload = {"action": "save", "name": ds.name, "type": kind, "value": values[kind], "clear_value": "on"}
    response = client.post(url, payload, HTTP_X_TRELLUM_FORM="1")
    assert response.status_code == 200
    data = response.json()
    assert data["values"]["value"] == ""
    assert data["values"]["clear_value"] is False
    assert data["label"] == "Empty" and not data["test_url"]
    assert data["message"] == "Saved."
    payload.update(data["values"], description="Changed description")
    assert client.post(url, payload, HTTP_X_TRELLUM_FORM="1").status_code == 200
    ds.refresh_from_db()
    assert ds.credentials["value"] == ""


@pytest.mark.parametrize("kind", ["text", "json"])
def test_multiline_secret_widget_is_write_only_on_edit_and_error(studio_tree, kind):
    value = "first\nsecond" if kind == "text" else '{\n  "a": 1\n}'
    ds = DataSource.objects.create(studio=studio_tree, name="setting", type=kind,
                                   config={"secret": True}, credentials={"value": value})
    form = DataSourceForm(instance=ds, studio=studio_tree)
    assert "<textarea" in str(form["value"]) and value not in str(form["value"])
    bound = DataSourceForm({"name": "invalid name", "type": kind, "secret": "on", "value": value}, instance=ds, studio=studio_tree)
    assert not bound.is_valid()
    assert value not in str(bound["value"])


@pytest.mark.parametrize("credentials,config,label", [({"value": "model-name"}, {}, "Saved"), ({"value": ""}, {}, "Empty"), ({}, {"secret_reference": "assistant_api_key"}, "Referenced")])
def test_scalar_status_is_factual_and_connection_controls_are_absent(login, org_admin, studio_tree, credentials, config, label):
    ds = DataSource.objects.create(studio=studio_tree, name="setting", type="secret", credentials=credentials, config=config)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources"
    client = login(org_admin)
    response = client.get(base)
    assert response.context["rows"][0]["label"] == label
    assert 'onclick="testSource(' not in response.content.decode()
    assert not binding_state(ds, studio_tree).blocking


def _rendered_browser(page, html, url):
    """Use the real server-rendered template and shared JS/CSS in Chromium."""
    from django.contrib.staticfiles import finders
    from pathlib import Path
    import mimetypes
    def route(request):
        from urllib.parse import urlsplit
        path = urlsplit(request.request.url).path
        if path.startswith("/static/"):
            file = finders.find(path.removeprefix("/static/"))
            if file:
                request.fulfill(body=Path(file).read_bytes(), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")
                return
        request.fulfill(body=html, content_type="text/html")
    page.route("http://datasources.test/**", route)
    page.goto("http://datasources.test" + url)


def test_real_scalar_editor_multiline_types_and_reference(login, org_admin, studio_tree):
    client = login(org_admin)
    base = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources"
    html = client.get(base + "/new").content.decode()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        _rendered_browser(page, html, base + "/new")
        assert page.locator('[name="clear_value"]').count() == 0
        page.select_option('[name="type"]', "text")
        value = "first line\nsecond line\nthird line"
        page.fill('[name="value"]', value)
        page.check('[name="secret"]')
        assert page.locator('textarea[name="value"]').input_value() == value
        page.uncheck('[name="secret"]')
        assert page.locator('textarea[name="value"]').input_value() == value
        page.select_option('[name="type"]', "boolean")
        assert page.locator('select[name="value"] option').all_text_contents() == ["No value", "True", "False"]
        page.select_option('[name="type"]', "number")
        assert page.locator('[name="value"]').get_attribute("type") == "number"
        assert page.locator('[name="value"]').get_attribute("step") == "any"
        page.select_option('[name="type"]', "secret")
        page.select_option('[name="secret_reference"]', "assistant_api_key")
        assert not page.locator('[data-f="value"]').is_visible()
        assert not page.locator('[data-f="path"]').is_visible()
        browser.close()


def test_real_scalar_clear_response_preserves_newer_draft(login, org_admin, studio_tree):
    ds = DataSource.objects.create(studio=studio_tree, name="gateway", type="string", credentials={"value": "https://old.test"})
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources/gateway/edit"
    client = login(org_admin)
    html = client.get(url).content.decode()
    response = client.post(url, {"action": "save", "name": ds.name, "type": ds.type, "clear_value": "on", "value": "https://old.test"}, HTTP_X_TRELLUM_FORM="1").json()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for newer in (False, True, "type"):
            page = browser.new_page()
            # Keep the pre-clear rendered value to exercise normalization in place.
            _rendered_browser(page, html, url)
            assert not page.locator('[data-ds-test]').is_visible()
            assert not page.locator('[data-ds-draft-test]').is_visible()
            page.evaluate("""data => { window.fetch = () => new Promise(resolve => {window.finishSave = () => resolve({ok: true, json: () => Promise.resolve(data)});}); }""", response)
            page.check('[name="clear_value"]')
            page.get_by_role("button", name="Save source", exact=True).click()
            page.wait_for_function("!!window.finishSave")
            if newer == "type":
                page.select_option('[name="type"]', "text")
            if newer:
                page.fill('[name="value"]', "https://new-draft.test")
            page.evaluate("window.finishSave()")
            page.wait_for_function("document.querySelector('#ds-editor').getAttribute('aria-busy') !== 'true'")
            assert page.locator('#ds-editor').get_attribute('data-settings-state') == 'saved', page.locator('[data-settings-dirty-status]').inner_text()
            assert page.locator('[name="value"]').input_value() == ("https://new-draft.test" if newer else "")
            assert not page.locator('[name="clear_value"]').is_checked()
            assert page.locator('[data-ds-badge]').inner_text() == "Empty"
            assert page.locator('#ds-editor').evaluate("form => form.classList.contains('is-dirty')") == bool(newer)
            if newer:
                assert page.locator('[name="value"]').evaluate("field => field === document.activeElement")
            page.select_option('[name="type"]', "postgres")
            assert page.locator('[data-ds-test]').is_visible()
            assert page.locator('[data-ds-draft-test]').is_visible()
            page.select_option('[name="type"]', "secret")
            assert not page.locator('[data-ds-test]').is_visible()
            assert not page.locator('[data-ds-draft-test]').is_visible()
            page.close()
        browser.close()


def test_org_secret_picker_is_hidden_for_studio_admin_without_breaking_existing_reference(studio_tree):
    ds = DataSource.objects.create(studio=studio_tree, name="setting", type="secret", config={"secret_reference": "assistant_api_key"})
    form = DataSourceForm({"name": "setting", "type": "secret", "secret_reference": "assistant_api_key", "description": "Updated"}, instance=ds, studio=studio_tree)
    assert form["secret_reference"].is_hidden
    assert form.is_valid(), form.errors
    assert form.save().config["secret_reference"] == "assistant_api_key"


def test_secret_declaration_upgrade_never_renders_old_plain_binding(login, org_admin, studio_tree):
    DataSource.objects.create(studio=studio_tree, name="gateway", type="string", credentials={"value": "previously-visible-fixture"})
    RepoDataSource.objects.create(studio=studio_tree, name="gateway", type="secret")
    client = login(org_admin)
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources/gateway/edit"
    response = client.get(url)
    assert response.status_code == 200
    assert "previously-visible-fixture" not in response.content.decode()
    assert response.context["form"]["value"].field.widget.input_type == "password"
