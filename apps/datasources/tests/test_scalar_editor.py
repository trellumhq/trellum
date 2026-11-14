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
