"""Keyboard and narrow-screen behavior of the standalone connection editor JS."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


def _rendered_page(html):
    for name in (
        "themes.generated.css", "ui.css", "choice-picker.css",
        "recipient-picker.css", "console-shell.css",
    ):
        css = (ROOT / "static" / name).read_text(encoding="utf-8")
        html = re.sub(rf'<link href="[^"]*{re.escape(name)}" rel="stylesheet">',
                      f"<style>{css}</style>", html)
    for name in (
        "choice-picker.js", "recipient-picker.js", "console-shell.js",
        "settings-forms.js", "email-connection-editor.js",
    ):
        script = (ROOT / "static" / name).read_text(encoding="utf-8")
        html = re.sub(
            rf'<script src="[^"]*{re.escape(name)}"(?: defer)?></script>',
            lambda match, script=script: f"<script>{script}</script>",
            html,
        )
    return html


def _open(page, html):
    page.route("http://email.test/**", lambda route: route.fulfill(
        body=html, content_type="text/html") if route.request.resource_type == "document"
        else route.fulfill(status=204))
    page.goto("http://email.test/", wait_until="domcontentloaded")


def test_provider_and_auth_sections_follow_keyboard_selection():
    script = (ROOT / "static" / "email-connection-editor.js").read_text(encoding="utf-8")
    html = """<form id="email-connection-form">
      <select id="id_provider" name="provider"><option value="sendgrid">SendGrid</option><option value="custom_https">Custom</option></select>
      <select name="auth_type"><option value="bearer">Bearer</option><option value="basic">Basic</option></select>
      <select name="credential_source"><option value="deployment">Deployment</option></select>
      <input name="region">
      <div id="custom" data-email-providers="custom_https" hidden>Custom controls</div>
      <div id="bearer" data-auth-types="bearer" hidden>Token</div>
      <div id="basic" data-auth-types="basic" hidden>Username</div>
      <textarea name="payload"></textarea><input name="kind">
      <select id="email-mapping-field"><option value="">Choose</option><option value="message.subject">Subject</option></select>
      <button type="button" data-preview-kind="report">Report preview</button>
    </form>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 375, "height": 700})
        page.set_content(html + f"<script>{script}</script>")
        assert page.locator("#custom").is_hidden()
        page.locator("#id_provider").focus()
        page.keyboard.press("End")
        assert page.locator("#custom").is_visible()
        assert page.locator("#bearer").is_visible()
        page.locator('[name="auth_type"]').select_option("basic")
        assert page.locator("#bearer").is_hidden()
        assert page.locator("#basic").is_visible()
        page.locator("#email-mapping-field").select_option("message.subject")
        assert '{"$value":"message.subject"}' in page.locator('[name="payload"]').input_value()
        page.locator('[data-preview-kind="report"]').click()
        assert page.locator('[name="kind"]').input_value() == "report"
        browser.close()


@pytest.mark.django_db
@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_saved_connection_edit_uses_in_place_save_and_field_errors(engine, login, superuser):
    from apps.core.models import EmailApiConnection

    client = login(superuser)
    profile = EmailApiConnection.objects.create(
        name="Saved route", provider="sendgrid", from_email="sender@example.com",
        provider_config={"region": "global"}, credentials={"api_key": "stored-secret"},
    )
    path = f"/system/email-connections/{profile.pk}"
    html = _rendered_page(client.get(path).content.decode())
    headers = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}
    data = {"action": "save", "name": "Renamed route", "provider": "sendgrid",
            "from_email": profile.from_email, "region": "global", "api_key": ""}
    responses = [client.post(path, data, **headers),
                 client.post(path, {**data, "name": ""}, **headers)]
    requests = []

    def serve(route):
        if route.request.method == "POST":
            requests.append((route.request.post_data_buffer, route.request.headers["content-type"]))
            response = responses[len(requests) - 1]
            route.fulfill(status=response.status_code, body=response.content, content_type="application/json")
        elif route.request.resource_type == "document":
            route.fulfill(body=html, content_type="text/html")
        else:
            route.fulfill(status=204)

    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 390, "height": 800})
        page.route("http://email.test/**", serve)
        page.goto("http://email.test" + path, wait_until="domcontentloaded")
        page.evaluate("window.documentIdentity = document")
        name = page.locator('[name="name"]')
        name.fill("Renamed route")
        name.focus()
        position = page.evaluate("scrollY")
        page.locator("#email-connection-form").evaluate("f => f.requestSubmit(f.querySelector('[value=save]'))")
        expect(page.locator("#email-connection-form")).to_have_attribute("data-settings-state", "saved")
        expect(page.get_by_role("heading", name="Edit Renamed route")).to_be_visible()
        assert page.evaluate("document === window.documentIdentity")
        assert abs(page.evaluate("scrollY") - position) <= 2
        assert name.evaluate("e => e === document.activeElement")
        expect(page.locator('[name="api_key"]')).to_have_value("")
        name.fill("")
        page.locator("#email-connection-form").evaluate("f => f.requestSubmit(f.querySelector('[value=save]'))")
        expect(page.locator("#email-connection-form")).to_have_attribute("data-settings-state", "error")
        expect(name).to_have_attribute("aria-invalid", "true")
        expect(name).to_have_value("")
        assert page.evaluate("document === window.documentIdentity")
        browser.close()
    for (body, content_type), expected in zip(requests, responses, strict=True):
        response = client.generic("POST", path, body, content_type=content_type, **headers)
        assert response.json() == expected.json()
    profile.refresh_from_db()
    assert profile.name == "Renamed route"
    assert profile.credentials["api_key"] == "stored-secret"


@pytest.mark.django_db
# Provider switching does not depend on width; the overflow check at the end does,
# so each engine runs at one width: desktop chromium, phone webkit.
@pytest.mark.parametrize("engine, width", (("chromium", 1280), ("webkit", 390)))
def test_rendered_editor_keeps_secrets_hidden_and_switches_fields(
    engine, width, login, superuser,
):
    from apps.core.models import EmailApiConnection

    client = login(superuser)
    profile = EmailApiConnection.objects.create(
        name="Saved SendGrid", provider="sendgrid", from_email="sender@example.com",
        provider_config={"region": "global"}, credentials={"api_key": "never-render-this-secret"},
    )
    edit = client.get(f"/system/email-connections/{profile.pk}")
    assert edit.status_code == 200
    assert b"never-render-this-secret" not in edit.content
    fresh = client.get("/system/email-connections/new")
    assert fresh.status_code == 200
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        context = browser.new_context(viewport={"width": width, "height": 800})
        page = context.new_page()
        _open(page, _rendered_page(edit.content.decode()))
        assert page.get_by_role("heading", name="Edit Saved SendGrid").is_visible()
        assert "never-render-this-secret" not in page.content()
        assert page.locator('[name="api_key"]').is_visible()
        page.unroute_all()
        _open(page, _rendered_page(fresh.content.decode()))
        assert page.locator("#id_region").input_value() == "global"
        assert page.get_by_role("heading", name="Service credentials").is_visible()
        page.locator("#id_provider").focus()
        page.locator("#id_provider").select_option("amazon_ses")
        assert page.get_by_label("AWS credential source").is_visible()
        assert page.locator("#id_region").input_value() == "eu-west-1"
        assert page.get_by_role("heading", name="Service credentials").is_hidden()
        page.locator("#id_credential_source").select_option("access_keys")
        assert page.get_by_label("AWS access key ID").is_visible()
        assert page.get_by_role("heading", name="Service credentials").is_visible()
        page.locator("#id_provider").select_option("custom_https")
        assert page.get_by_role("heading", name="Service credentials").is_hidden()
        assert page.get_by_label("Payload mapping JSON").is_visible()
        assert page.get_by_label("Bearer token").is_visible()
        page.locator('#id_auth_type').select_option("basic")
        assert page.get_by_label("Basic password").is_visible()
        assert page.get_by_label("Bearer token").is_hidden()
        assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth + 1")
        browser.close()
