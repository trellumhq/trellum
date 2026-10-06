from pathlib import Path

from playwright.sync_api import sync_playwright


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
