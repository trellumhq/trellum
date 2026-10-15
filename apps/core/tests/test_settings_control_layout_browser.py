"""Rendered browser geometry for recovery, audit, and member controls."""

from types import SimpleNamespace as S

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.test import RequestFactory

UI_CSS = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def browser(request):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        try:
            instance = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{request.param} unavailable: {exc}")
        yield instance
        instance.close()


def _page(browser, html, width, theme):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.set_content(html, wait_until="load")
    page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
    page.add_style_tag(content=UI_CSS)
    page.add_style_tag(content=SHELL_CSS)
    return context, page


def _codes_html():
    return render_to_string(
        "accounts/mfa_recovery_codes.html",
        {"codes": ["ABCD-EFGH-1234", "JKLM-NPQR-5678"] * 5},
    )


def _setup_html():
    from apps.accounts.forms import TotpConfirmForm

    return render_to_string(
        "accounts/mfa_setup.html",
        {
            "form": TotpConfirmForm(),
            "secret": "SYNTHETICSECRET1234",
            "qr_svg": '<svg viewBox="0 0 200 200" width="200" height="200"></svg>',
        },
    )


def _audit_html():
    org = S(name="Synthetic Organization", slug="synthetic")
    request = RequestFactory().get("/orgs/synthetic/settings/audit")
    request.user = AnonymousUser()
    request.org = org
    request.studio = None
    return render_to_string(
        "orgs/audit.html",
        {
            "org": org,
            "tiles": S(events=0, denials=0, failed_logins=0, actors=0),
            "pills": [],
            "applied": {"category": "", "q": "", "actor": "", "action": "", "outcome": "", "from": "", "to": ""},
            "page": Paginator([], 50).get_page(1),
            "actor_choices": [(2, "person@example.test")],
            "action_choices": [("member.update", "Member updated")],
            "hidden_carry": [],
            "any_filter_active": False,
            "recent_denials": [],
            "request": request,
        },
        request=request,
    )


def _members_html():
    org = S(name="Synthetic Organization", slug="synthetic")
    member = S(
        m=S(user=S(display_name="Synthetic Person", email="person@example.test"), user_id=2),
        groups=[],
        effective=[],
        studio_editor=[
            S(studio=S(name="Synthetic Studio", slug="studio", pk=3), direct_role="viewer")
        ],
    )
    return render_to_string(
        "orgs/members.html",
        {
            "org": org,
            "user": S(pk=1, is_authenticated=False),
            "member_rows": [member],
            "org_roles_choices": [("member", "Member"), ("admin", "Admin")],
            "studio_role_choices": [("", "None"), ("viewer", "Viewer"), ("developer", "Developer")],
            "q": "",
            "page": S(number=1),
            "total": 1,
        },
    )


@pytest.mark.parametrize("width", (320, 390, 1024, 1440))
@pytest.mark.parametrize("theme", ("light", "dark"))
def test_recovery_codes_fit_the_available_panel_width(browser, width, theme):
    context, page = _page(browser, _codes_html(), width, theme)
    try:
        grid = page.locator(".recovery-code-grid")
        assert grid.evaluate("el => el.scrollWidth <= el.clientWidth")
        assert grid.locator("div").count() == 10
    finally:
        context.close()


@pytest.mark.parametrize("width", (320, 390, 1024, 1440))
@pytest.mark.parametrize("theme", ("light", "dark"))
def test_mfa_setup_qr_and_manual_key_stay_inside_the_panel(browser, width, theme):
    context, page = _page(browser, _setup_html(), width, theme)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        panel = page.locator("main .panel")
        qr = page.locator(".mfa-qr")
        key = page.locator(".panel > div:nth-child(2)")
        panel_right = panel.bounding_box()["x"] + panel.bounding_box()["width"]
        assert qr.bounding_box()["x"] + qr.bounding_box()["width"] <= panel_right
        assert key.bounding_box()["x"] + key.bounding_box()["width"] <= panel_right
    finally:
        context.close()


@pytest.mark.parametrize("width", (320, 390, 1024, 1440))
@pytest.mark.parametrize("theme", ("light", "dark"))
def test_audit_filters_wrap_without_clipping_and_align_date_controls(browser, width, theme):
    context, page = _page(browser, _audit_html(), width, theme)
    try:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        apply = page.locator(".audit-filters button").bounding_box()
        from_date = page.locator(".audit-date-field input").nth(0).bounding_box()
        to_date = page.locator(".audit-date-field input").nth(1).bounding_box()
        if width >= 1024:
            assert abs((apply["y"] + apply["height"] / 2) - (from_date["y"] + from_date["height"] / 2)) < 2
            assert abs((apply["y"] + apply["height"] / 2) - (to_date["y"] + to_date["height"] / 2)) < 2
    finally:
        context.close()


@pytest.mark.parametrize("width", (320, 390, 1024, 1440))
@pytest.mark.parametrize("theme", ("light", "dark"))
def test_member_manage_popup_stays_inside_viewport(browser, width, theme):
    context, page = _page(browser, _members_html(), width, theme)
    try:
        page.locator("td.ui-row-actions summary").click()
        page.wait_for_function(
            """() => {
              const menu = document.querySelector('td.ui-row-actions .shell-menu');
              return menu && menu.style.position === 'fixed';
            }"""
        )
        menu = page.locator("td.ui-row-actions .shell-menu")
        box = menu.bounding_box()
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width
        assert box["y"] >= 0
        assert box["y"] + box["height"] <= 900
        select = page.locator('.shell-menu form[style*="display:flex"] select').bounding_box()
        save = page.locator('.shell-menu form[style*="display:flex"] button').bounding_box()
        assert select["x"] + select["width"] <= width
        assert save["x"] + save["width"] <= width
        assert abs(select["y"] - save["y"]) <= 1
        assert abs(select["height"] - save["height"]) <= 1
        assert (
            select["x"] + select["width"] <= save["x"]
            or save["x"] + save["width"] <= select["x"]
        )
    finally:
        context.close()


def test_tall_member_menu_scrolls_without_closing_then_page_scroll_closes(browser):
    context = browser.new_context(viewport={"width": 320, "height": 400})
    page = context.new_page()
    page.set_content(_members_html(), wait_until="load")
    page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
    page.add_style_tag(content=UI_CSS)
    page.add_style_tag(content=SHELL_CSS)
    try:
        page.locator("td.ui-row-actions summary").click()
        page.wait_for_function(
            """() => {
              const menu = document.querySelector('td.ui-row-actions .shell-menu');
              return menu && menu.style.position === 'fixed' && menu.style.overflowY === 'auto';
            }"""
        )
        menu = page.locator("td.ui-row-actions .shell-menu")
        assert menu.evaluate("el => el.scrollHeight > el.clientHeight")
        menu.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        page.wait_for_function(
            """() => {
              const menu = document.querySelector('td.ui-row-actions .shell-menu');
              return document.querySelector('td.ui-row-actions details').open && menu.scrollTop > 0;
            }"""
        )
        page.evaluate("document.body.style.minHeight = '1600px'; window.scrollTo(0, 0)")
        page.evaluate("window.scrollBy(0, 20)")
        page.wait_for_function(
            """() => {
              const details = document.querySelector('td.ui-row-actions details');
              const menu = details.querySelector('.shell-menu');
              return !details.open && menu.style.position === '' && menu.style.maxHeight === '';
            }"""
        )
        assert menu.evaluate("el => el.style.position === '' && el.style.maxHeight === ''")
    finally:
        context.close()
