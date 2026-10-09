from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[3]


def test_settings_form_dirty_state_reverts_and_rebases():
    helper = (ROOT / "static" / "settings-forms.js").read_text(encoding="utf-8")
    html = f"""
    <form data-settings-form>
      <select name="role"><option value="viewer">Viewer</option><option value="admin">Admin</option></select>
      <input name="name" value="Original">
      <fieldset disabled><input name="disabled-field" value="off"></fieldset>
      <button type="submit">Save changes</button>
    </form>
    <script>{helper}</script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        status = page.locator("[data-settings-dirty-status]")
        assert status.text_content() == ""

        page.locator('[name="name"]').fill("Changed")
        assert status.text_content() == "Unsaved changes"
        page.locator('[name="name"]').fill("Original")
        assert status.text_content() == ""

        page.locator('[name="role"]').select_option("admin")
        assert status.text_content() == "Unsaved changes"
        page.locator("form").evaluate("form => form.dispatchEvent(new Event('settings:saved'))")
        assert status.text_content() == ""
        page.locator('[name="role"]').select_option("viewer")
        assert status.text_content() == "Unsaved changes"
        browser.close()


def test_disabled_controls_do_not_create_false_dirty_state():
    helper = (ROOT / "static" / "settings-forms.js").read_text(encoding="utf-8")
    html = f"""
    <form data-settings-form>
      <fieldset disabled><input name="disabled-field" value="off"></fieldset>
      <button type="submit">Save changes</button>
    </form>
    <script>{helper}</script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.locator('[name="disabled-field"]').evaluate("field => field.value = 'changed'")
        page.locator('[name="disabled-field"]').dispatch_event("input")
        assert page.locator("[data-settings-dirty-status]").text_content() == ""
        browser.close()


@pytest.fixture
def settings_page():
    helper = (ROOT / "static" / "settings-forms.js").read_text(encoding="utf-8")
    html = """<div style="height:900px"></div>
    <form id="prefs" action="/save" method="post" data-settings-form data-settings-save>
      <input name="name" value="Original" aria-describedby="hint"><span id="hint">Name</span>
      <input type="hidden" name="id" value="">
      <fieldset disabled><input name="disabled-field" value="off"></fieldset>
      <button type="submit" name="action" value="save">Save</button>
      <button type="submit" disabled>Unavailable</button>
    </form><form id="other" data-settings-form><input name="draft" value="Keep"><button type="submit">Other</button></form>
    <div data-settings-refresh="summary">Old</div><div style="height:900px"></div>"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("https://example.test/settings", lambda route: route.fulfill(body=html, content_type="text/html"))
        page.goto("https://example.test/settings")
        page.add_script_tag(content=helper)
        yield page
        browser.close()


def test_ajax_save_preserves_document_scroll_focus_and_submitter(settings_page):
    page = settings_page
    requests = []
    def save(route):
        requests.append(route.request)
        route.fulfill(json={"ok": True, "message": "Saved.", "values": {"id": 42}})
    page.route("**/save", save)
    page.locator('[name="name"]').fill("Changed")
    page.locator('[name="name"]').focus()
    page.evaluate("window.documentMarker = document; window.scrollTo(0,850)")
    original_scroll = page.evaluate("scrollY")
    page.locator("#prefs").evaluate("form => form.requestSubmit(form.querySelector('button'))")
    expect(page.locator("#prefs [data-settings-dirty-status]")).to_have_text("Saved.")
    assert page.evaluate("document === window.documentMarker")
    assert page.evaluate("scrollY") == original_scroll
    assert page.evaluate("document.activeElement.name") == "name"
    assert page.locator('[name="id"]').input_value() == "42"
    assert 'name="action"' in requests[0].post_data
    assert 'name="disabled-field"' not in requests[0].post_data
    assert page.locator('button:has-text("Unavailable")').is_disabled()
    assert not page.locator("#prefs").evaluate("form => form.classList.contains('is-dirty')")


@pytest.mark.parametrize("failure", ["validation", "network", "server", "invalid", "null-json"])
def test_failed_save_keeps_draft_and_reports_error(settings_page, failure):
    page = settings_page
    def save(route):
        if failure == "validation":
            route.fulfill(status=400, json={"ok": False, "message": "Please correct name.", "errors": {"name": ["Name already exists."]}})
        elif failure == "network":
            route.abort()
        elif failure == "server":
            route.fulfill(status=500, json={"ok": True, "message": "Saved."})
        elif failure == "null-json":
            route.fulfill(body="null", content_type="application/json")
        else:
            route.fulfill(body="not-json", content_type="text/html")
    page.route("**/save", save)
    page.evaluate("document.addEventListener('settings:error', event => { window.saveError = {message:event.detail.message, busy:event.target.hasAttribute('aria-busy')}; })")
    page.locator('[name="name"]').fill("Draft")
    page.locator("#prefs").evaluate("form => form.requestSubmit()")
    expect(page.locator("#prefs")).to_have_attribute("data-settings-state", "error")
    assert page.locator('[name="name"]').input_value() == "Draft"
    assert page.evaluate("window.saveError.busy") is False
    assert page.evaluate("window.saveError.message") == page.locator("#prefs [data-settings-dirty-status]").text_content()
    assert page.locator("#prefs").evaluate("form => form.classList.contains('is-dirty')")
    assert page.locator("#prefs [data-settings-dirty-status]").text_content() != "Saved."
    assert not page.locator('#prefs button').first.is_disabled()
    if failure == "validation":
        assert page.locator('[name="name"]').get_attribute("aria-invalid") == "true"
        assert "hint prefs-error-0" == page.locator('[name="name"]').get_attribute("aria-describedby")
        assert page.locator('[data-settings-error]').text_content() == "Name already exists."
    if failure == "network":
        assert "outcome is unknown" in page.locator("#prefs [data-settings-dirty-status]").text_content()


def test_inflight_guard_and_newer_edits_keep_their_own_baseline(settings_page):
    page = settings_page
    pending = []
    page.route("**/save", lambda route: pending.append(route))
    page.locator('[name="name"]').fill(" Submitted ")
    page.locator('[name="draft"]').fill("Other draft")
    page.locator("#prefs").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    expect(page.locator("#prefs [data-settings-dirty-status]")).to_have_text("Saving…")
    page.wait_for_function("document.querySelector('#prefs').getAttribute('aria-busy') === 'true'")
    page.wait_for_timeout(30)
    assert len(pending) == 1
    page.locator('[name="name"]').fill("Newer draft")
    pending[0].fulfill(json={"ok": True, "message": "Saved.", "values": {"name": "Submitted"}})
    expect(page.locator("#prefs")).to_have_attribute("data-settings-state", "saved")
    assert page.locator('[name="name"]').input_value() == "Newer draft"
    assert "Newer changes are still unsaved" in page.locator("#prefs [data-settings-dirty-status]").text_content()
    assert page.locator("#prefs").evaluate("form => form.classList.contains('is-dirty')")
    assert page.locator("#other [data-settings-dirty-status]").text_content() == "Unsaved changes"
    page.locator('[name="name"]').fill("Submitted")
    assert not page.locator("#prefs").evaluate("form => form.classList.contains('is-dirty')")


def test_confirmation_cancellation_and_idempotent_initialization(settings_page):
    page = settings_page
    pending = []
    page.route("**/save", lambda route: pending.append(route))
    page.locator("#prefs").evaluate("form => { window.TrellumSettingsForms.init(document); form.addEventListener('submit', event => event.preventDefault(), {once:true}); form.requestSubmit(); }")
    assert pending == []
    assert page.locator("#prefs [data-settings-dirty-status]").count() == 1
    page.locator("#prefs").evaluate("form => form.requestSubmit()")
    expect(page.locator("#prefs")).to_have_attribute("data-settings-state", "saving")
    page.wait_for_function("document.querySelector('#prefs').getAttribute('aria-busy') === 'true'")
    page.wait_for_timeout(30)
    assert len(pending) == 1
    pending[0].fulfill(json={"ok": True})
    expect(page.locator("#prefs")).to_have_attribute("data-settings-state", "saved")


def test_targeted_read_only_refresh_preserves_other_drafts(settings_page):
    page = settings_page
    page.route("**/save", lambda route: route.fulfill(json={"ok": True, "refresh": ["summary"]}))
    page.unroute("https://example.test/settings")
    page.route("https://example.test/settings", lambda route: route.fulfill(content_type="text/html", body='<div data-settings-refresh="summary">Updated<script>window.unwanted=true</script></div>'))
    page.locator('[name="draft"]').fill("Other draft")
    page.locator("#prefs").evaluate("form => form.requestSubmit()")
    expect(page.locator('[data-settings-refresh="summary"]')).to_have_text("Updated")
    assert page.locator('[name="draft"]').input_value() == "Other draft"
    assert not page.evaluate("Boolean(window.unwanted)")



def test_submit_button_focus_restored_without_taking_focus_from_later_edits(settings_page):
    page = settings_page
    page.route("**/save", lambda route: route.fulfill(json={"ok": True}))
    page.locator('#prefs button').first.click()
    expect(page.locator("#prefs")).to_have_attribute("data-settings-state", "saved")
    assert page.locator('#prefs button').first.evaluate("button => document.activeElement === button")


def test_missing_summary_reports_saved_but_refresh_failed(settings_page):
    page = settings_page
    page.route("**/save", lambda route: route.fulfill(json={"ok": True, "refresh": ["summary"]}))
    page.unroute("https://example.test/settings")
    page.route("https://example.test/settings", lambda route: route.fulfill(content_type="text/html", body="<p>Missing region</p>"))
    page.locator("#prefs").evaluate("form => form.requestSubmit()")
    expect(page.locator("#prefs [data-settings-dirty-status]")).to_contain_text("summary could not refresh")
    assert page.locator('[data-settings-refresh="summary"]').text_content() == "Old"


def test_dirty_fragment_waits_for_reset_and_read_only_refresh_preserves_scroll(settings_page):
    page = settings_page
    page.locator("#other").evaluate("form => {form.setAttribute('data-settings-refresh','editor');}")
    page.locator('[name="draft"]').fill("Keep this draft")
    page.unroute("https://example.test/settings")
    page.route("https://example.test/settings", lambda route: route.fulfill(content_type="text/html", body='<form id="other" data-settings-form data-settings-refresh="editor"><input name="draft" value="Updated"></form><div data-settings-refresh="summary" style="height:250px">Updated summary</div>'))
    page.evaluate("window.scrollTo(0,850)")
    position = page.evaluate("scrollY")
    pending = page.evaluate("async () => await TrellumSettingsForms.refreshRegions(['editor','summary'])")
    assert pending == ["editor"]
    assert page.locator('[name="draft"]').input_value() == "Keep this draft"
    assert page.evaluate("scrollY") == position
    page.locator('[name="name"]').focus()
    page.locator("#other").evaluate("form => form.reset()")
    expect(page.locator('[name="draft"]')).to_have_value("Updated")
    assert page.locator('#other [data-settings-dirty-status]').count() == 1
