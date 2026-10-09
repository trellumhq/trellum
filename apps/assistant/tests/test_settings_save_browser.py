"""Connection tests stay tied to the saved assistant configuration."""
from types import SimpleNamespace

from django.conf import settings
from django.template.loader import get_template
from django.test import RequestFactory
from playwright.sync_api import expect, sync_playwright

from apps.orgs.forms import AssistantConfigForm


def test_old_connection_test_cannot_replace_result_or_unlock_new_test_after_save():
    org = SimpleNamespace(name="Acme", slug="acme")
    request = RequestFactory().get("/orgs/acme/settings/assistant")
    request.org = org
    form = AssistantConfigForm(org=org, initial={"provider": "openai", "model": "gpt-example"})
    html = get_template("orgs/assistant.html").render({
        "request": request, "org": org, "form": form,
        "cfg": SimpleNamespace(), "has_key": True, "csrf_token": "test-csrf-token",
    })
    helper = (settings.BASE_DIR / "static/settings-forms.js").read_text(encoding="utf-8")
    html += f"<script>{helper}</script>"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # Deliberately deliver old responses even after abort to exercise generation guards.
        page.add_init_script("""
          window.connectionTests = [];
          const originalTimeout = window.setTimeout;
          window.setTimeout = function (callback, milliseconds) {
            if (milliseconds === 30000) window.connectionTimeout = callback;
            return originalTimeout(callback, milliseconds);
          };
          const originalFetch = window.fetch;
          window.fetch = function (url, options) {
            if (String(url).endsWith('/test')) {
              return new Promise(function (resolve, reject) {
                connectionTests.push({resolve: resolve, reject: reject, signal: options.signal});
              });
            }
            return originalFetch(url, options);
          };
        """)
        saves = []
        def route(route):
            if route.request.resource_type == "document":
                route.fulfill(body=html, content_type="text/html")
            elif route.request.method == "POST":
                saves.append(route)
            else:
                route.fulfill(status=204)
        page.route("http://settings.test/**", route)
        page.goto("http://settings.test/orgs/acme/settings/assistant", wait_until="domcontentloaded")
        button = page.locator("#assistant-test")
        result = page.locator("#assistant-test-result")
        message = page.locator("#assistant-test-message")
        button.click()
        expect(message).to_have_text("Testing the saved settings…")
        page.locator("#id_model").fill("gpt-updated")
        expect(result).to_be_hidden()
        assert page.evaluate("connectionTests[0].signal.aborted")
        page.locator("#orgAssistantForm").evaluate("form => form.requestSubmit()")
        expect(page.locator("#orgAssistantForm")).to_have_attribute("aria-busy", "true")
        expect(button).to_be_disabled()
        button.dispatch_event("click")
        assert page.evaluate("connectionTests.length") == 1
        page.wait_for_timeout(30)
        assert len(saves) == 1
        saves[0].fulfill(json={
            "ok": True, "message": "AI settings saved.",
            "assistant_ready": True,
            "assistant_open_pricing": True,
            "updates": {
                "assistant-readiness-title": "AI is ready based on saved settings.",
                "assistant-readiness-reason": "Use Test connection below to verify provider access.",
                "assistant-pricing-state": "Cost estimates are unavailable without pricing. Prices are optional while both budgets are blank.",
            },
        })
        expect(page.locator("#orgAssistantForm")).to_have_attribute("data-settings-state", "saved")
        expect(page.locator('[data-settings-text="assistant-readiness-title"]')).to_have_text("AI is ready based on saved settings.")
        expect(page.locator('[data-settings-text="assistant-pricing-state"]')).to_contain_text("Cost estimates are unavailable")
        expect(page.locator("#orgAssistantForm details").nth(0)).to_have_attribute("open", "")
        expect(page.locator("#assistant-readiness")).to_have_class("ui-flash success")
        expect(page.locator("#assistant-readiness-mark")).to_have_text(chr(0x2713))
        page.evaluate("document.getElementById('orgAssistantForm').dispatchEvent(new CustomEvent('settings:success', {bubbles:true, detail:{assistant_ready:false, assistant_open_pricing:false}}))")
        expect(page.locator("#assistant-readiness")).to_have_class("ui-flash error")
        expect(page.locator("#assistant-readiness-mark")).to_have_text(chr(0x2715))
        page.evaluate("document.getElementById('orgAssistantForm').dispatchEvent(new CustomEvent('settings:success', {bubbles:true, detail:{assistant_ready:true, assistant_open_pricing:false}}))")
        expect(result).to_be_hidden()
        expect(button).to_be_enabled()
        button.dispatch_event("click")
        assert page.evaluate("connectionTests.length") == 2
        page.evaluate("connectionTests[0].resolve({ok:true,json:async () => ({ok:true,message:'Old settings worked'})})")
        expect(message).to_have_text("Testing the saved settings…")
        expect(button).to_be_disabled()
        page.evaluate("connectionTests[1].resolve({ok:true,json:async () => ({ok:true,message:'Current settings worked'})})")
        expect(message).to_have_text("Current settings worked")
        expect(button).to_be_enabled()
        page.locator("#id_provider").select_option("anthropic")
        expect(result).to_be_hidden()
        button.click()
        page.evaluate("connectionTimeout(); connectionTests[2].reject(new DOMException('Aborted', 'AbortError'))")
        assert page.evaluate("connectionTests[2].signal.aborted")
        expect(message).to_have_text("The connection test timed out. Try again.")
        expect(button).to_be_enabled()
        browser.close()
