"""Rendered provider settings use shared saves and reject obsolete probe results."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from apps.orgs.models import OrgAssistantConfig

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.django_db


@pytest.fixture
def page(login, org_admin, org):
    OrgAssistantConfig.objects.create(org=org, enabled=True, provider="openai", api_key="stored", model="gpt-4o")
    html = login(org_admin).get(f"/orgs/{org.slug}/settings/assistant").content.decode()
    html = re.sub(r'<script\b[^>]*\bsrc=[^>]*>.*?</script>', '', html, flags=re.S)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        current = browser.new_page(viewport={"width": 1280, "height": 900})
        def route(request):
            path = request.request.url.split("portal.example", 1)[-1]
            if path == "/settings":
                request.fulfill(body=html, content_type="text/html")
            elif path.startswith("/static/"):
                file = ROOT / "static" / path.removeprefix("/static/").split("?", 1)[0]
                if file.is_file():
                    request.fulfill(path=file, content_type="text/css" if file.suffix == ".css" else "application/octet-stream")
                else:
                    request.abort()
            else:
                request.abort()
        current.route("**/*", route)
        current.goto("https://portal.example/settings")
        current.add_script_tag(content=(ROOT / "static/settings-forms.js").read_text(encoding="utf-8"))
        current.add_script_tag(content=(ROOT / "static/assistant-settings.js").read_text(encoding="utf-8"))
        yield current
        browser.close()


def fake_fetch(page):
    # Deliberately ignores abort so stale response suppression is exercised.
    page.evaluate("() => {window.requests = []; window.fetch = (url, options) => new Promise(resolve => requests.push({url, options, resolve}));}")


def resolve(page, index, payload, status=200):
    page.evaluate("args => requests[args.index].resolve(new Response(JSON.stringify(args.payload), {status: args.status, headers: {'Content-Type':'application/json'}}))", {"index": index, "payload": payload, "status": status})


def connection(provider="openai", **changes):
    result = {"provider": provider, "base_url": "https://api.openai.com/v1", "auth_mode": "api_key", "cloud_config": {}, "model": "gpt-4o"}
    result.update(changes)
    return result


def test_conditional_fields_and_actual_rendered_screenshots(page, tmp_path):
    screenshots = tmp_path / "provider-settings-review"
    page.locator('#orgAssistantForm details > summary').first.click()
    page.locator('[name="base_url"]').fill('https://draft-gateway.example/v1')
    page.select_option('[name="provider"]', 'azure')
    assert page.locator('[name="base_url"]').input_value() == ''
    assert page.locator('[name="model"]').input_value() == ''
    page.select_option('[name="auth_mode"]', 'client_secret')
    expect(page.locator('[name="tenant_id"]')).to_be_visible()
    expect(page.locator('[name="client_secret"]')).to_be_visible()
    expect(page.locator('[name="secret_access_key"]')).to_be_hidden()
    expect(page.locator('#assistant-models')).to_be_disabled()
    screenshots.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshots / "azure-settings.png"), full_page=True)
    page.locator('[name="base_url"]').fill('https://draft-resource.openai.azure.com')
    page.select_option('[name="provider"]', 'vertex')
    assert page.locator('[name="base_url"]').input_value() == ''
    expect(page.locator('[name="project"]')).to_be_visible()
    expect(page.locator('[name="service_account"]')).to_be_visible()
    expect(page.locator('[data-endpoint-fields]')).to_be_hidden()
    page.screenshot(path=str(screenshots / "vertex-settings.png"), full_page=True)


def test_enhanced_save_preserves_scroll_focus_neighbor_draft_and_newer_edits(page):
    fake_fetch(page)
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<form data-settings-form id=neighbor><input name=notes value=original></form>'); TrellumSettingsForms.init(document.getElementById('neighbor'));")
    page.locator('#neighbor input').fill('neighbor draft')
    model = page.locator('[name="model"]')
    model.fill('submitted-model')
    page.evaluate("document.querySelector('[name=model]').focus({preventScroll:true}); scrollTo(0, 400);")
    position = page.evaluate('scrollY')
    page.locator('#orgAssistantForm').evaluate('form => {form.requestSubmit(); form.requestSubmit();}')
    expect(page.locator('#orgAssistantForm')).to_have_attribute('aria-busy', 'true')
    assert page.evaluate('requests.length') == 1
    model.fill('newer-model')
    resolve(page, 0, {"ok": True, "message": "Saved.", "config_revision": 2,
                     "saved_connection": connection(model="submitted-model"), "assistant_ready": False,
                     "values": {"api_key": ""}, "updates": {"assistant-readiness-reason": "Needs test"}})
    expect(page.locator('[data-settings-dirty-status]').first).to_contain_text('Newer changes are still unsaved')
    assert model.input_value() == 'newer-model'
    assert page.locator('#neighbor input').input_value() == 'neighbor draft'
    assert page.evaluate('document.activeElement.name') == 'model'
    assert page.evaluate('scrollY') == position
    assert page.url == 'https://portal.example/settings'
    page.locator('#orgAssistantForm').evaluate('form => form.requestSubmit()')
    resolve(page, 1, {"ok": False, "message": "Enter a model", "errors": {"model": ["Invalid model"]}}, 400)
    expect(model).to_have_attribute('aria-invalid', 'true')
    assert model.input_value() == 'newer-model'
    assert page.locator('#neighbor input').input_value() == 'neighbor draft'


def test_discovery_failure_allows_manual_entry_and_keeps_catalog_while_typing(page):
    fake_fetch(page)
    page.locator('#assistant-models').click()
    resolve(page, 0, {"ok": False, "message": "Discovery failed; enter manually.", "models": [], "config_revision": 1})
    expect(page.locator('#assistant-model-status')).to_contain_text('enter manually')
    page.locator('[name="model"]').fill('manual-model')
    expect(page.locator('#assistant-models')).to_be_enabled()
    page.locator('#assistant-models').click()
    resolve(page, 1, {"ok": True, "message": "Loaded", "models": [{"id": "model-one"}], "config_revision": 1})
    expect(page.locator('#assistant-model-list option')).to_have_count(1)
    page.locator('[name="model"]').fill('model-')
    expect(page.locator('#assistant-model-list option')).to_have_count(1)
    page.select_option('[name="provider"]', 'litellm')
    expect(page.locator('#assistant-models')).to_be_disabled()
    expect(page.locator('#assistant-model-list option')).to_have_count(0)


def test_late_test_and_discovery_ignored_and_current_check_updates_readiness(page):
    fake_fetch(page)
    page.evaluate("document.getElementById('assistant-readiness').classList.remove('success');")
    page.locator('#assistant-test').click()
    page.locator('[name="model"]').fill('draft-model')
    resolve(page, 0, {"ok": True, "message": "OBSOLETE", "config_revision": 1, "checks": {"chat": {"ok": True}}})
    expect(page.locator('#assistant-test-result')).to_be_hidden()
    page.locator('[name="model"]').fill('gpt-4o')
    page.locator('#assistant-test').click()
    resolve(page, 1, {"ok": True, "message": "Verified", "config_revision": 1, "assistant_available": True, "assistant_reason": "",
                     "checks": {"chat": {"ok": True, "message": "Streamed tools"}, "alerts": {"ok": False, "message": "Unsupported forced tool"}, "usage": {"ok": True, "message": "Metered"}}})
    expect(page.locator('#assistant-readiness')).to_have_class(re.compile('success'))
    expect(page.locator('[data-settings-text="assistant-alerts-check"]')).to_contain_text('Unavailable')
    page.locator('#assistant-models').click()
    page.select_option('[name="provider"]', 'azure')
    resolve(page, 2, {"ok": True, "message": "OBSOLETE", "config_revision": 1, "models": [{"id": "old-provider-model"}]})
    expect(page.locator('#assistant-model-list option')).to_have_count(0)
    expect(page.locator('#assistant-model-status')).not_to_contain_text('OBSOLETE')
