"""Browser checks against complete Django-rendered console pages."""
import re

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.django_db


def _inline_shell_assets(html: str) -> str:
    css = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
        encoding="utf-8"
    )
    ui = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
    js = (settings.BASE_DIR / "static" / "console-shell.js").read_text(encoding="utf-8")
    html = re.sub(r'<link href="[^\"]*inter\.css" rel="stylesheet">', "", html)
    html = re.sub(r'<link href="[^\"]*ui\.css" rel="stylesheet">', f"<style>{ui}</style>", html)
    html = re.sub(
        r'<link href="[^\"]*console-shell\.css" rel="stylesheet">',
        f"<style>{css}</style>",
        html,
    )
    return re.sub(
        r'<script src="[^\"]*console-shell\.js"></script>',
        f"<script>{js}</script>",
        html,
    )


def _page(
    instance,
    html: str,
    *,
    width: int,
    height: int = 760,
    reduced_motion=None,
    init_script=None,
):
    context = instance.new_context(
        viewport={"width": width, "height": height}, reduced_motion=reduced_motion
    )
    if init_script:
        context.add_init_script(init_script)
    page = context.new_page()

    def serve(route):
        if route.request.resource_type == "document":
            route.fulfill(body=html, content_type="text/html")
        else:
            route.fulfill(status=204)

    page.route("http://shell.test/**", serve)
    page.goto("http://shell.test/", wait_until="domcontentloaded")
    page.locator("[data-console-shell]").wait_for(state="attached")
    return context, page


def _box(page, selector):
    box = page.locator(selector).bounding_box()
    assert box is not None
    return box


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_mobile_bar_search_drawer_and_account_menu(
    engine, login, org_admin, org, studio_tree
):
    org_admin.email = "account-with-an-extraordinarily-long-address@a-very-long-example-domain.test"
    org_admin.save(update_fields=["email"])
    html = _inline_shell_assets(
        login(org_admin)
        .get(f"/s/{org.slug}/{studio_tree.slug}/settings/members")
        .content.decode()
    )

    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        for width in (320, 390, 430):
            context, page = _page(instance, html, width=width)
            header = _box(page, ".tl-console-header")
            assert header["height"] == 56
            menu = _box(page, "[data-console-toggle]")
            title = _box(page, "[data-console-page-title]")
            search_button = _box(page, "[data-console-search-toggle]")
            account = _box(page, ".tl-console-account > summary")
            assert menu["x"] < title["x"] < search_button["x"] < account["x"]
            assert account["x"] + account["width"] <= width - 12 + 0.5

            page.locator("[data-console-search-toggle]").click()
            search = page.locator("[data-console-search]")
            assert search.is_visible()
            assert _box(page, "#searchInput")["height"] == 44
            font_size = page.locator("#searchInput").evaluate(
                "e => getComputedStyle(e).fontSize"
            )
            assert font_size == "16px"
            page.keyboard.press("Escape")
            assert not search.is_visible()

            page.locator("[data-console-toggle]").click()
            assert page.locator("[data-console-shell]").evaluate(
                "e => e.classList.contains('is-open')"
            )
            page.wait_for_function(
                "getComputedStyle(document.querySelector('.tl-console-sidebar')).transform === 'matrix(1, 0, 0, 1, 0, 0)'"
            )
            assert _box(page, ".tl-console-sidebar")["x"] == 0
            page.locator("[data-console-close]").click()
            page.locator(".tl-console-sidebar").wait_for(state="hidden")

            page.locator(".tl-console-account > summary").click()
            account_menu = _box(page, ".tl-console-account-menu")
            assert account_menu["x"] >= 12
            assert account_menu["x"] + account_menu["width"] <= width - 12 + 0.5
            assert page.locator(".tl-console-account-menu").evaluate(
                "e => e.scrollWidth <= e.clientWidth"
            )
            if engine == "chromium" and width == 390:
                artifacts = settings.BASE_DIR / ".artifacts" / "shell"
                artifacts.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=artifacts / "mobile-390-account.png", full_page=True
                )
                page.locator(".tl-console-account > summary").click()
                page.locator("[data-console-toggle]").click()
                page.wait_for_function(
                    "getComputedStyle(document.querySelector('.tl-console-sidebar')).transform === 'matrix(1, 0, 0, 1, 0, 0)'"
                )
                page.screenshot(
                    path=artifacts / "mobile-390-navigation.png", full_page=True
                )
            context.close()
        instance.close()


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_desktop_dark_prepaint_expanded_nav_and_reduced_motion(
    engine, login, org_admin, org
):
    html = _inline_shell_assets(
        login(org_admin).get(f"/orgs/{org.slug}/settings/members").content.decode()
    )
    init = (
        "localStorage.setItem('mgmt-theme','dark'); "
        "localStorage.setItem('tl-console-collapsed','0');"
    )
    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        context, page = _page(
            instance,
            html,
            width=1440,
            height=900,
            reduced_motion="reduce",
            init_script=init,
        )
        assert page.locator("html").get_attribute("data-theme") == "dark"
        assert _box(page, ".tl-console-header")["height"] == 56
        assert _box(page, ".tl-console-sidebar")["width"] == 240
        assert page.locator("[data-console-nav-group='org-security']").is_visible()
        shell = page.locator("[data-console-shell]")
        shell.evaluate("e => e.dataset.consoleTheme = 'light'")
        assert shell.evaluate(
            "e => getComputedStyle(e).getPropertyValue('--tl-console-card').trim()"
        ) == "#fff"
        page.evaluate("window.TrellumConsoleShell.setTheme('dark')")
        assert shell.get_attribute("data-console-theme") == "dark"
        assert page.locator("html").get_attribute("data-theme") == "dark"
        duration = page.locator(".tl-console-sidebar").evaluate(
            "e => getComputedStyle(e).transitionDuration"
        )
        assert "1e-05s" in duration or "0.00001s" in duration
        page.locator(".tl-console-account > summary").click()
        menu = _box(page, ".tl-console-account-menu")
        assert menu["x"] >= 12 and menu["x"] + menu["width"] <= 1428.5
        if engine == "chromium":
            artifacts = settings.BASE_DIR / ".artifacts" / "shell"
            artifacts.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=artifacts / "desktop-1440-dark-expanded.png", full_page=True)
        context.close()
        instance.close()


def test_sidebar_scroll_is_restored_for_console_navigation_and_back(login, org_admin, org):
    paths = (
        f"/orgs/{org.slug}/settings/members",
        f"/orgs/{org.slug}/settings/retention",
    )
    pages = {
        path: _inline_shell_assets(login(org_admin).get(path).content.decode())
        for path in paths
    }

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        context = instance.new_context(viewport={"width": 1440, "height": 760})
        page = context.new_page()

        def serve(route):
            path = route.request.url.removeprefix("http://shell.test")
            if route.request.resource_type == "document" and path in pages:
                route.fulfill(body=pages[path], content_type="text/html")
            else:
                route.fulfill(status=204)

        page.route("http://shell.test/**", serve)
        page.goto("http://shell.test" + paths[0], wait_until="domcontentloaded")
        nav = page.locator(".tl-console-nav")
        assert nav.evaluate("e => e.scrollHeight > e.clientHeight")
        nav.evaluate("e => e.scrollTop = 180")
        assert nav.evaluate("e => e.scrollTop") > 0
        target = page.locator(f'a[href="{paths[1]}"]')
        target.scroll_into_view_if_needed()
        expected_scroll = nav.evaluate("e => e.scrollTop")
        assert expected_scroll > 0
        target.click()
        page.wait_for_url("**" + paths[1])
        assert nav.evaluate("e => e.scrollTop") == expected_scroll
        assert page.evaluate("window.scrollY") == 0
        active = page.locator(f'a[href="{paths[1]}"]')
        assert active.get_attribute("aria-current") == "page"

        page.go_back(wait_until="domcontentloaded")
        page.wait_for_url("**" + paths[0])
        assert nav.evaluate("e => e.scrollTop") == expected_scroll
        context.close()
        instance.close()
