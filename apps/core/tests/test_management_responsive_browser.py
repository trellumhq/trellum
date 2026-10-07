"""Chromium/WebKit geometry checks against rendered management templates."""

from types import SimpleNamespace as S

import pytest
from django.conf import settings
from django.template.loader import render_to_string

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
CONSOLE_SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)
SETTINGS_FORMS_JS = (settings.BASE_DIR / "static" / "settings-forms.js").read_text(
    encoding="utf-8"
)
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


def _studio_members_html():
    org = S(name="Example Organization", slug="example-org")
    studio = S(name="Example Studio", slug="example-studio")
    return render_to_string(
        "studios/members.html",
        {
            "org": org,
            "studio": studio,
            "rows": [
                S(
                    user=S(email="avery.long.synthetic.member.address@example.invalid"),
                    user_id=1,
                    role="viewer",
                )
            ],
            "org_users": [
                S(user=S(email="another.synthetic.member@example.invalid"), user_id=2)
            ],
            "studio_role_choices": (
                ("viewer", "Viewer"),
                ("developer", "Developer"),
                ("admin", "Admin"),
            ),
            "request": S(studio=studio, org=org),
            "user": S(is_authenticated=False),
        },
    )


def _org_members_html():
    org = S(name="Example Organization", slug="example-org")
    member = S(
        user=S(
            display_name="Synthetic Member",
            email="avery.long.synthetic.member.address@example.invalid",
            has_mfa=False,
        ),
        user_id=1,
        role="member",
    )
    page = S(
        number=1,
        has_previous=False,
        has_next=False,
        paginator=S(num_pages=1),
    )
    return render_to_string(
        "orgs/members.html",
        {
            "org": org,
            "member_rows": [
                S(m=member, groups=(), effective=(), studio_editor=())
            ],
            "org_roles_choices": (("member", "Member"), ("admin", "Admin")),
            "studio_role_choices": (
                ("viewer", "Viewer"),
                ("developer", "Developer"),
                ("admin", "Admin"),
            ),
            "q": "",
            "total": 1,
            "page": page,
            "request": S(studio=None, org=org),
            "user": S(is_authenticated=False, pk=2),
        },
    )


def _rendered_management_page(browser, html, width, theme="light"):
    context = browser.new_context(viewport={"width": width, "height": 820})
    page = context.new_page()
    html = html.replace(
        "</head>", f"<style>{UI_CSS}{CONSOLE_SHELL_CSS}</style></head>"
    ).replace("</body>", f"<script>{SETTINGS_FORMS_JS}</script></body>")
    page.set_content(html, wait_until="load")
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    return context, page


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


@pytest.mark.parametrize("theme", ("light", "dark"))
def test_studio_member_role_controls_align_at_desktop(mobile_browser, theme):
    context, page = _rendered_management_page(
        mobile_browser, _studio_members_html(), 1000, theme
    )
    try:
        form = page.locator("tbody form[data-settings-form]")
        select = form.locator("select")
        button = form.locator("button")
        select_box = select.bounding_box()
        button_box = button.bounding_box()

        assert abs(select_box["height"] - button_box["height"]) <= 1
        assert abs(select_box["y"] - button_box["y"]) <= 1
        assert button.evaluate("el => getComputedStyle(el).whiteSpace") == "nowrap"
        assert select.evaluate("el => getComputedStyle(el).appearance") == "none"
        assert select.evaluate("el => getComputedStyle(el).paddingRight") == "28px"
        assert select.evaluate("el => getComputedStyle(el).backgroundImage") != "none"

        status = form.locator("[data-settings-dirty-status]")
        assert status.text_content() == ""
        select.select_option("admin")
        assert status.text_content() == "Unsaved changes"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_org_member_role_controls_align_at_desktop(mobile_browser):
    context, page = _rendered_management_page(
        mobile_browser, _org_members_html(), 1440
    )
    try:
        form = page.locator("tbody form[data-settings-form]").first
        select_box = form.locator("select").bounding_box()
        button_box = form.locator("button").bounding_box()
        assert abs(select_box["height"] - button_box["height"]) <= 1
        assert abs(select_box["y"] - button_box["y"]) <= 1
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_org_member_role_controls_wrap_whole_when_table_is_crowded(mobile_browser):
    context, page = _rendered_management_page(
        mobile_browser, _org_members_html(), 900
    )
    try:
        form = page.locator("tbody form[data-settings-form]").first
        select_box = form.locator("select").bounding_box()
        button_box = form.locator("button").bounding_box()
        assert abs(select_box["height"] - button_box["height"]) <= 1
        assert abs(select_box["x"] - button_box["x"]) <= 1
        assert button_box["y"] >= select_box["y"] + select_box["height"]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.parametrize("width", (320, 390))
def test_studio_member_controls_fit_and_wrap_as_whole_controls(mobile_browser, width):
    context, page = _rendered_management_page(
        mobile_browser, _studio_members_html(), width
    )
    try:
        form = page.locator("tbody form[data-settings-form]")
        select = form.locator("select")
        button = form.locator("button")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert select.evaluate("el => getComputedStyle(el).minHeight") == "44px"
        assert button.evaluate("el => getComputedStyle(el).minHeight") == "44px"
        assert button.evaluate("el => getComputedStyle(el).whiteSpace") == "normal"

        select.select_option("admin")
        assert form.locator("[data-settings-dirty-status]").text_content() == "Unsaved changes"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_listbox_keeps_native_appearance(mobile_browser):
    html = '<main><select multiple><option selected>Viewer</option></select></main>'
    context, page = _page(mobile_browser, html, 1000)
    try:
        select = page.locator("select")
        assert select.evaluate("el => getComputedStyle(el).appearance") == "auto"
        assert select.evaluate("el => getComputedStyle(el).backgroundImage") == "none"
    finally:
        context.close()


def test_single_select_restores_native_affordance_in_forced_colors(mobile_browser):
    context = mobile_browser.new_context(
        viewport={"width": 1000, "height": 820}, forced_colors="active"
    )
    page = context.new_page()
    page.set_content(
        '<!doctype html><style>'
        + UI_CSS
        + '</style><body class="mgmt"><main><select><option>Viewer</option></select></main>'
    )
    try:
        select = page.locator("select")
        assert select.evaluate("el => getComputedStyle(el).appearance") == "auto"
        assert select.evaluate("el => getComputedStyle(el).backgroundImage") == "none"
    finally:
        context.close()


def test_long_inline_action_can_wrap_on_phone(mobile_browser):
    html = (
        '<main><form class="inline" style="width:180px">'
        '<button class="ui-btn ghost">Save this unusually long action label</button>'
        "</form></main>"
    )
    context, page = _page(mobile_browser, html, 320)
    try:
        form = page.locator("form")
        button = page.locator("button")
        assert button.evaluate("el => getComputedStyle(el).whiteSpace") == "normal"
        assert button.bounding_box()["width"] <= form.bounding_box()["width"]
        assert button.bounding_box()["height"] > 44
        assert button.evaluate("el => el.scrollWidth <= el.clientWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()
