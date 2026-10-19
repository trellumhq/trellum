"""Phone layout of the Studio creation page.

The heading, field and button content is server-rendered and asserted in
test_management_views.py::TestStudios::test_create_page_renders_its_form."""
import pytest
from django.conf import settings

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)

pytestmark = pytest.mark.django_db


def test_studio_create_page_fits_a_phone_and_uses_a_readable_field_font(login, org_admin, org):
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
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        try:
            context = browser.new_context(viewport={"width": 390, "height": 900})
            try:
                page = context.new_page()
                page.set_content(html, wait_until="load")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for field in (page.get_by_label("Name"), page.get_by_label("Slug")):
                    assert field.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 16
            finally:
                context.close()
        finally:
            browser.close()
