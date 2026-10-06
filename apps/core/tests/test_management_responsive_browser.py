"""Chromium/WebKit geometry checks against rendered management templates."""

from types import SimpleNamespace as S

import pytest
from django.conf import settings
from django.template.loader import render_to_string

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
ARTIFACTS = settings.BASE_DIR / ".artifacts" / "management"


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def mobile_browser(request):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        try:
            browser = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{request.param} unavailable: {exc}")
        yield browser
        browser.close()


def _page(browser, html, width):
    context = browser.new_context(viewport={"width": width, "height": 820})
    page = context.new_page()
    page.set_content(
        '<!doctype html><html data-theme="light"><head><meta name="viewport" '
        'content="width=device-width, initial-scale=1"><style>'
        + UI_CSS
        + "</style></head><body class=\"mgmt\">"
        + html
        + "</body></html>",
        wait_until="load",
    )
    return context, page


def _delivery_html(rows):
    return render_to_string("reports/my_deliveries.html", {"schedule_rows": rows})


def _delivery_row():
    schedule = S(enabled=True, attach_pdf=False)
    return S(
        report=S(name="A report with a deliberately long title", slug="report"),
        org=S(name="A very long organization name", slug="acme"),
        studio=S(name="A studio with a long name", slug="studio"),
        cadence="Every Monday at 09:00",
        recipient_summary="person@example.com, another@example.com",
        schedule=schedule,
        schedule_json='{"enabled": true}',
    )


@pytest.mark.parametrize("width", (320, 390, 430))
def test_delivery_rendered_template_fits_and_keeps_editor_hidden(mobile_browser, width):
    context, page = _page(mobile_browser, _delivery_html([_delivery_row()]), width)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("#deliveryTable[data-mobile-cards]").count() == 1
        assert page.locator("[data-delivery-row]").count() == 1
        assert page.locator(".delivery-row-detail").evaluate(
            "el => getComputedStyle(el).display"
        ) == "none"
        assert page.locator("[data-toggle-edit]").first.evaluate(
            "el => getComputedStyle(el).minHeight"
        ) == "44px"
        assert page.locator("[data-toggle-edit]").first.evaluate(
            "el => getComputedStyle(el).fontSize"
        ) == "14px"
        if width == 390:
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ARTIFACTS / "deliveries-390.png"), full_page=True)
    finally:
        context.close()


def test_delivery_empty_rendered_template_has_no_table(mobile_browser):
    context, page = _page(mobile_browser, _delivery_html([]), 390)
    try:
        assert page.locator("#deliveryTable").count() == 0
        assert "No scheduled deliveries yet" in page.locator("body").inner_text()
    finally:
        context.close()


def test_account_sessions_rendered_template_has_wrapping_rows(mobile_browser):
    html = render_to_string(
        "accounts/account.html",
        {
            "user": S(email="person@example.com", is_superuser=False, name="Person"),
            "sessions": [
                S(user_agent="A very long browser user agent " * 5, ip="192.0.2.44", last_seen=None, session_key="other"),
            ],
            "current_session_key": "current",
            "has_mfa": False,
            "recovery_codes_remaining": 0,
        },
    )
    context, page = _page(mobile_browser, html, 390)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("table[data-mobile-cards]").count() == 1
        assert page.locator("table[data-mobile-cards] td[data-label='Device']").count() == 1
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / "account-sessions-390.png"), full_page=True)
    finally:
        context.close()


def test_management_templates_render_at_desktop_width(mobile_browser):
    html = render_to_string(
        "orgs/sso.html", {"claimed_domains": [], "unclaimed_domains": []}
    )
    context, page = _page(mobile_browser, html, 1440)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / "sso-1440.png"), full_page=True)
    finally:
        context.close()


def test_org_appearance_rendered_template_fits_on_phone(mobile_browser):
    html = render_to_string(
        "orgs/appearance.html",
        {"org": S(name="A long organization name"), "themes": [], "selected_theme": ""},
    )
    context, page = _page(mobile_browser, html, 390)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_non_card_wide_table_keeps_internal_scroll(mobile_browser):
    html = (
        '<main><div class="panel"><div class="ui-table-scroll">'
        '<table class="ui-table" style="min-width:720px"><tr><td>wide data</td></tr>'
        '</table></div></div></main>'
    )
    context, page = _page(mobile_browser, html, 390)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        wrapper = page.locator(".ui-table-scroll")
        assert wrapper.evaluate("el => el.scrollWidth > el.clientWidth")
        assert wrapper.evaluate("el => getComputedStyle(el).overflowX") == "auto"
    finally:
        context.close()


def test_delivery_widget_editable_fields_keep_phone_font_size(mobile_browser):
    html = '<main><input class="rdw-input" type="text"><select class="rdw-select"><option>UTC</option></select></main>'
    context, page = _page(mobile_browser, html, 390)
    try:
        assert page.locator(".rdw-input").evaluate("el => getComputedStyle(el).fontSize") == "16px"
        assert page.locator(".rdw-select").evaluate("el => getComputedStyle(el).fontSize") == "16px"
    finally:
        context.close()


def test_main_management_actions_keep_phone_hit_target(mobile_browser):
    html = '<main><button class="ui-btn primary">Save</button><a class="ui-btn ghost" href="#">Add</a><select><option>viewer</option></select></main>'
    context, page = _page(mobile_browser, html, 390)
    try:
        for selector in ("button", "a.ui-btn", "select"):
            assert page.locator(selector).evaluate("el => getComputedStyle(el).minHeight") == "44px"
    finally:
        context.close()


def test_sso_claim_form_fits_and_uses_phone_font_size(mobile_browser):
    html = render_to_string(
        "orgs/sso.html", {"claimed_domains": [], "unclaimed_domains": []}
    )
    context, page = _page(mobile_browser, html, 320)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        domain = page.locator("input[name='domain']")
        assert domain.evaluate("el => getComputedStyle(el).fontSize") == "16px"
        assert domain.evaluate("el => getComputedStyle(el).minWidth") == "0px"
    finally:
        context.close()
