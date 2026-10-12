"""Rendered layout and organization picker checks for personal API-key creation."""
from pathlib import Path
import re

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
            lambda match: f"<script>{script}</script>", html,
        )
    return html


def test_create_page_picker_and_responsive_themes(login, api_key_user, org, db):
    from apps.orgs.models import Organization, OrgMembership

    for index in range(10):
        item = Organization.objects.create(slug=f"workspace-{index}", name=f"Workspace {index}")
        OrgMembership.objects.create(user=api_key_user, org=item, role="member")

    html = _rendered_page(login(api_key_user).get("/me/api-keys/new").content.decode())
    screenshots = ROOT / ".artifacts" / "api-key-create"
    screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, theme in ((1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")):
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.set_content(html, wait_until="domcontentloaded")
            page.evaluate(
                "theme => { document.documentElement.dataset.theme = theme; "
                "document.querySelector('[data-console-shell]').dataset.consoleTheme = theme; }",
                theme,
            )
            expect(page.get_by_role("heading", name="Add API key")).to_be_visible()
            assert page.locator("body").evaluate("e => e.scrollWidth <= innerWidth + 1")
            assert page.locator(".ui-form-rail").evaluate("e => e.getBoundingClientRect().width <= 760")

            picker = page.locator(".ui-choice-picker")
            trigger = picker.locator(".ui-picker-trigger")
            trigger.click()
            popup = picker.locator(".ui-picker-popup")
            expect(popup).to_be_visible()
            assert popup.evaluate(
                "e => { const r=e.getBoundingClientRect(); return r.left >= 0 && "
                "r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; }"
            )
            search = popup.locator(".ui-picker-search")
            expect(popup.locator(".ui-picker-option")).to_have_count(11)
            search.fill("Workspace 9")
            assert popup.locator(".ui-picker-option").count() == 1, page.locator("#id_org").inner_html()
            popup.get_by_role("button", name="Workspace 9").click()
            expect(trigger).to_contain_text("Workspace 9")
            expect(popup).to_be_hidden()
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("link", name="Cancel", exact=True).click()
            expect(trigger).to_contain_text("Workspace 9")
            expect(page.locator("#apiKeyCreateForm")).to_have_attribute("data-settings-state", "dirty")
            page.screenshot(path=screenshots / f"create-{width}-{theme}.png", full_page=True)
            page.close()
        browser.close()
