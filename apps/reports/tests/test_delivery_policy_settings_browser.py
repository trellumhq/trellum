"""Policy saves preserve the administrator's page and drafts."""
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("scope", ["instance", "org"])
def test_policy_save_failure_and_success_preserve_page(engine, scope, login, superuser, org_admin, org):
    path = "/instance/settings" if scope == "instance" else f"/orgs/{org.slug}/settings/deliveries"
    actor = superuser if scope == "instance" else org_admin
    html = login(actor).get(path).content.decode()
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        pending = []

        def respond(route):
            if route.request.method == "POST":
                pending.append(route)
            elif route.request.url == "https://example.test" + path:
                route.fulfill(body=html, content_type="text/html")
            else:
                route.abort()

        page.route("**/*", respond)
        page.goto("https://example.test" + path)
        page.add_style_tag(content=(ROOT / "static/ui.css").read_text(encoding="utf-8"))
        page.add_style_tag(content="#delivery-policy-form {margin-top: 400px} body {min-height: 3500px}")
        page.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        page.evaluate("TrellumSettingsForms.init(document); TrellumSettingsForms.init(document)")
        if scope == "instance":
            page.locator("[name=instance_name]").fill("Neighbor draft")
        form = page.locator("#delivery-policy-form")
        timezone = form.locator("[name=delivery_default_timezone]")
        timezone.fill("Wrong/Zone")
        timezone.focus()
        page.evaluate("window.originalDocument = document")
        position = page.evaluate("scrollY")
        form.evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
        page.wait_for_function("document.querySelector('#delivery-policy-form').dataset.settingsState === 'saving'")
        assert len(pending) == 1
        pending.pop().fulfill(status=400, json={"ok": False, "message": "Invalid timezone.", "errors": {"delivery_default_timezone": ["Enter a valid IANA timezone."]}})
        expect(timezone).to_have_value("Wrong/Zone")
        expect(timezone).to_have_attribute("aria-invalid", "true")
        assert page.evaluate("document === originalDocument && document.activeElement.name === 'delivery_default_timezone'")
        assert page.evaluate("scrollY") == position
        if scope == "instance":
            expect(page.locator("[name=instance_name]")).to_have_value("Neighbor draft")
        timezone.fill("UTC")
        form.evaluate("form => form.requestSubmit()")
        page.wait_for_function("document.querySelector('#delivery-policy-form').dataset.settingsState === 'saving'")
        timezone.fill("Europe/Amsterdam")
        pending.pop().fulfill(json={"ok": True, "message": "Delivery policy saved.", "values": {"delivery_default_timezone": "UTC", "delivery_default_enabled": ""}})
        page.wait_for_function("document.querySelector('#delivery-policy-form').dataset.settingsState === 'saved'")
        expect(page).to_have_url("https://example.test" + path)
        assert page.evaluate("document === originalDocument && document.activeElement.name === 'delivery_default_timezone'")
        assert page.evaluate("scrollY") == position
        expect(timezone).to_have_value("Europe/Amsterdam")
        expect(form).to_have_class(re.compile(r".*\bis-dirty\b.*"))
        if scope == "instance":
            expect(page.locator("[name=instance_name]")).to_have_value("Neighbor draft")
        browser.close()
