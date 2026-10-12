"""Rendered desktop and mobile checks for the Studio creation page."""
import pytest
from django.conf import settings

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)
ARTIFACTS = settings.BASE_DIR / ".artifacts" / "studio-create"

pytestmark = pytest.mark.django_db


def test_studio_create_page_rendered_desktop_mobile_and_themes(login, org_admin, org):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    html = login(org_admin).get(f"/orgs/{org.slug}/studios/new").content.decode()
    html = html.replace(
        "</head>",
        f"<style>{UI_CSS}{SHELL_CSS}"
        "*,*::before,*::after{animation:none!important;transition:none!important;"
        "scroll-behavior:auto!important}</style></head>",
    )
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            for width, theme in ((1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")):
                context = browser.new_context(viewport={"width": width, "height": 900})
                try:
                    page = context.new_page()
                    page.set_content(html, wait_until="load")
                    page.evaluate(
                        "theme => { document.documentElement.dataset.theme = theme; "
                        "document.querySelector('[data-console-shell]').dataset.consoleTheme = theme; }",
                        theme,
                    )
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert page.get_by_role("heading", name="Add Studio").is_visible()
                    assert page.get_by_label("Name").is_visible()
                    assert page.get_by_label("Slug").is_visible()
                    assert page.get_by_role("button", name="Create Studio").is_visible()
                    if width == 390:
                        for field in (page.get_by_label("Name"), page.get_by_label("Slug")):
                                assert field.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 16
                    page.screenshot(
                        path=str(ARTIFACTS / f"studio-create-{width}-{theme}.png"),
                        full_page=True,
                    )
                finally:
                    context.close()
        finally:
            browser.close()
