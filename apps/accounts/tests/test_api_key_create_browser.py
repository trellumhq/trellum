"""Rendered layout and organization picker checks for personal API-key creation."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def api_key_user(make_user, org):
    return make_user("api-key-browser@demo.example", org=org)


def _rendered_page(html):
    for name in ("themes.generated.css", "ui.css", "choice-picker.css", "console-shell.css"):
        css = (ROOT / "static" / name).read_text(encoding="utf-8")
        html = re.sub(
            rf'<link href="[^"]*{re.escape(name)}" rel="stylesheet">',
            f"<style>{css}</style>", html,
        )
    for name in ("console-shell.js", "settings-forms.js", "choice-picker.js"):
        script = (ROOT / "static" / name).read_text(encoding="utf-8")
        html = re.sub(
            rf'<script src="[^"]*{re.escape(name)}"(?: defer)?></script>',
            lambda match, script=script: f"<script>{script}</script>", html,
        )
    return html


def _picker_popup_is_inside_the_viewport(page):
    picker = page.locator("main .ui-choice-picker")
    picker.locator(".ui-picker-trigger").click()
    popup = picker.locator(".ui-picker-popup")
    expect(popup).to_be_visible()
    assert popup.evaluate(
        "e => { const r=e.getBoundingClientRect(); return r.left >= 0 && "
        "r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }"
    )
    return picker, popup


def test_create_page_picker_and_responsive_themes(login, api_key_user, org, db):
    from apps.orgs.models import Organization, OrgMembership

    for index in range(10):
        item = Organization.objects.create(slug=f"workspace-{index}", name=f"Workspace {index}")
        OrgMembership.objects.create(user=api_key_user, org=item, role="member")

    html = _rendered_page(login(api_key_user).get("/me/api-keys/new").content.decode())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        # The picker interaction does not depend on width or theme: do it once,
        # on desktop in the light theme.
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_content(html, wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Add API key")).to_be_visible()
        picker, popup = _picker_popup_is_inside_the_viewport(page)
        trigger = picker.locator(".ui-picker-trigger")
        expect(popup.locator(".ui-picker-option")).to_have_count(11)
        popup.locator(".ui-picker-search").fill("Workspace 9")
        assert popup.locator(".ui-picker-option").count() == 1, page.locator("#id_org").inner_html()
        popup.get_by_role("button", name="Workspace 9").click()
        expect(trigger).to_contain_text("Workspace 9")
        expect(popup).to_be_hidden()
        page.on("dialog", lambda dialog: dialog.dismiss())
        page.get_by_role("link", name="Cancel", exact=True).click()
        expect(trigger).to_contain_text("Workspace 9")
        expect(page.locator("#apiKeyCreateForm")).to_have_attribute("data-settings-state", "dirty")
        page.close()

        # Layout: only overflow, form-rail alignment and popup bounds vary, so
        # check them at desktop width in the dark theme and at phone width in
        # the light theme.
        for width, theme in ((1440, "dark"), (390, "light")):
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_content(html, wait_until="domcontentloaded")
            page.evaluate(
                "theme => { document.documentElement.dataset.theme = theme; "
                "document.querySelector('[data-console-shell]').dataset.consoleTheme = theme; }",
                theme,
            )
            assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth + 1")
            assert page.locator(".ui-form-rail").evaluate("""el => {
                const parent = el.parentElement, p = parent.getBoundingClientRect();
                const style = getComputedStyle(parent), r = el.getBoundingClientRect();
                const start = parseFloat(style.paddingLeft), end = parseFloat(style.paddingRight);
                return Math.abs(r.left - p.left - parent.clientLeft - start) < 2 &&
                       Math.abs(r.width - parent.clientWidth + start + end) < 2;
            }""")
            _picker_popup_is_inside_the_viewport(page)
            page.close()
        browser.close()


def test_api_key_list_does_not_overflow_on_a_phone(login, api_key_user, org, db):
    """The primary action and empty state are server-rendered; those are asserted
    in test_api_keys.py::TestMyKeysPage. Only the layout needs a browser."""
    from apps.accounts.models import ApiKey

    empty_html = _rendered_page(login(api_key_user).get("/me/api-keys").content.decode())
    ApiKey.mint(user=api_key_user, org=org, name="deployment key")
    populated_html = _rendered_page(login(api_key_user).get("/me/api-keys").content.decode())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for html in (empty_html, populated_html):
            page = browser.new_page(viewport={"width": 390, "height": 900})
            page.set_content(html, wait_until="domcontentloaded")
            expect(page.get_by_role("heading", name="API keys", exact=True)).to_be_visible()
            assert page.locator("body").evaluate("el => el.scrollWidth <= innerWidth + 1")
            page.close()
        browser.close()
