"""Browser checks against complete Django-rendered console pages."""
import re
import time
from itertools import pairwise

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


def _inline_portal_js(html: str) -> str:
    portal = (settings.BASE_DIR / "static" / "portal.js").read_text(
        encoding="utf-8"
    )
    return re.sub(
        r'<script src="[^\"]*portal\.js"></script>',
        lambda _: f"<script>{portal}</script>",
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


def _assert_docs_in_account_menu(page):
    page.locator(".tl-console-account > summary").click()
    assert page.locator(".tl-console-account-docs").is_visible()
    page.keyboard.press("Escape")


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


def test_operator_health_warning_polls_and_clears(login, superuser, monkeypatch):
    from apps.core import health

    monkeypatch.setattr(
        health,
        "quick_health_summary",
        lambda: {"status": "ok", "message": "", "detail": "", "failure_count": 0},
    )
    html = _inline_shell_assets(login(superuser).get("/").content.decode())
    replies = [
        '{"status":"error","message":"Worker offline","detail":"Repository sync and scheduled reports are paused"}',
        '{"status":"ok","message":"","detail":""}',
    ]

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        context = instance.new_context(viewport={"width": 320, "height": 760})
        page = context.new_page()

        def serve(route):
            if route.request.resource_type == "document":
                route.fulfill(body=html, content_type="text/html")
            elif route.request.url.endswith("/api/system/health"):
                route.fulfill(body=replies.pop(0), content_type="application/json")
            else:
                route.fulfill(status=204)

        page.route("http://shell.test/**", serve)
        page.goto("http://shell.test/", wait_until="domcontentloaded")
        alert = page.locator("[data-console-health-alert]")
        assert alert.is_hidden()
        assert page.evaluate("!!window.__trellumHealthTimer")

        page.evaluate(
            "Promise.all([window.TrellumConsoleShell.refreshHealth(), "
            "window.TrellumConsoleShell.refreshHealth()])"
        )
        assert len(replies) == 1  # overlapping polls share one request
        assert alert.is_visible()
        assert alert.get_attribute("data-health-status") == "error"
        assert alert.get_attribute("aria-label") == (
            "Worker offline. Repository sync and scheduled reports are paused"
        )
        assert _box(page, ".tl-console-header")["height"] == 56
        assert _box(page, "[data-console-health-alert]")["width"] == 44
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        page.evaluate("window.TrellumConsoleShell.refreshHealth()")
        assert alert.is_hidden()
        assert alert.get_attribute("aria-label") == "System health"
        context.close()
        instance.close()


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
@pytest.mark.parametrize(
    "platform,modifier,label",
    (("Win32", "Control", "Ctrl+K"), ("MacIntel", "Meta", "⌘K")),
)
def test_search_shortcut_layout_and_platform_label(
    engine, platform, modifier, label, login, org_admin, org, studio_tree
):
    html = _inline_shell_assets(
        login(org_admin)
        .get(f"/s/{org.slug}/{studio_tree.slug}/settings/members")
        .content.decode()
    )
    init = (
        "Object.defineProperty(navigator, 'platform', "
        f"{{get: () => '{platform}'}});"
    )

    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        # These CSS viewport widths approximate a 1536px display at 100%,
        # 125%, 150%, and 200% browser zoom. Browser zoom itself is not set.
        for width in (1536, 1229, 1024, 768, 390):
            context, page = _page(instance, html, width=width, init_script=init)
            search = page.locator("[data-console-search]")
            input_box = page.locator("#searchInput")
            badge = search.locator("kbd")
            assert page.locator("#searchInput").get_attribute("aria-label")
            assert badge.inner_text() == label

            if width < 768:
                page.locator("[data-console-search-toggle]").click()
                assert search.is_visible()

            # Long placeholders and user queries remain in the input's own
            # track; the adjacent badge has its own measured width.
            input_box.evaluate(
                "el => el.setAttribute('placeholder', 'Search ' + "
                "'localized report names '.repeat(8))"
            )
            input_box.fill("evidence query " + "x" * 160)
            assert input_box.input_value().endswith("x" * 160)
            assert input_box.get_attribute("type") == "search"
            input_box.focus()
            assert search.evaluate("el => getComputedStyle(el).outlineStyle === 'solid'")
            assert search.evaluate("el => getComputedStyle(el).outlineWidth === '2px'")
            assert input_box.evaluate("el => getComputedStyle(el).boxShadow === 'none'")
            assert input_box.evaluate("el => getComputedStyle(el).outlineStyle === 'none'")

            if width >= 768:
                entry = input_box.bounding_box()
                shortcut = badge.bounding_box()
                assert entry["x"] + entry["width"] <= shortcut["x"]
                assert search.evaluate("el => el.scrollWidth <= el.clientWidth")
            page.keyboard.press(f"{modifier}+k")
            assert input_box.evaluate("el => el === document.activeElement")
            assert input_box.evaluate(
                "el => el.selectionStart === 0 && el.selectionEnd === el.value.length"
            )
            if width < 768:
                page.keyboard.press("Escape")
                assert not search.is_visible()

            if engine == "chromium" and width == 1536 and platform == "Win32":
                artifacts = (
                    settings.BASE_DIR.parents[1]
                    / ".artifacts"
                    / "portal-third-batch"
                    / "search"
                )
                artifacts.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=artifacts / "studio-search-1536.png", full_page=True
                )
            context.close()
        instance.close()


@pytest.mark.parametrize(
    "platform,label", (("Win32", "Ctrl+K"), ("MacIntel", "⌘K"))
)
def test_dashboard_portal_boot_keeps_placeholder_and_search_shortcut(
    platform, label, login, org_admin, org, studio_tree
):
    html = _inline_portal_js(
        _inline_shell_assets(
            login(org_admin)
            .get(f"/s/{org.slug}/{studio_tree.slug}/")
            .content.decode()
        )
    )
    init = f"""
      Object.defineProperty(navigator, 'platform', {{get: () => '{platform}'}});
      window.__portalRegistryRequested = false;
      const originalFetch = window.fetch.bind(window);
      window.fetch = (...args) => {{
        if (String(args[0]).includes('/api/registry')) {{
          window.__portalRegistryRequested = true;
        }}
        return originalFetch(...args);
      }};
    """

    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        context, page = _page(instance, html, width=1440, init_script=init)
        input_box = page.locator("#searchInput")
        page.wait_for_function("window.__portalRegistryRequested")
        assert input_box.get_attribute("placeholder") == "Search reports…"
        assert page.locator(".tl-console-search kbd").inner_text() == label
        input_box.fill("report title")
        page.keyboard.press("Meta+k" if platform == "MacIntel" else "Control+k")
        assert input_box.evaluate("el => el === document.activeElement")
        assert input_box.evaluate(
            "el => el.selectionStart === 0 && el.selectionEnd === el.value.length"
        )
        context.close()
        instance.close()


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_org_assistant_picker_coexists_with_docs_on_narrow_screens(
    engine, login, org_admin, org, studio_tree
):
    html = _inline_shell_assets(
        login(org_admin).get(f"/?org={org.slug}").content.decode()
    )

    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        for width in (320, 390, 430):
            context, page = _page(instance, html, width=width)
            picker = page.locator(".tl-console-assistant-picker")
            docs = page.locator(".tl-console-docs-link")
            assert picker.is_visible()
            if width < 360:
                # Below 360px the header docs link folds into the account menu.
                assert not docs.is_visible()
                _assert_docs_in_account_menu(page)
            else:
                assert docs.is_visible()
                picker_box, docs_box = _box(page, ".tl-console-assistant-picker > summary"), _box(
                    page, ".tl-console-docs-link"
                )
                assert picker_box["x"] + picker_box["width"] <= docs_box["x"]
                assert docs_box["x"] + docs_box["width"] <= width
            picker.locator("summary").click()
            studio_link = picker.locator(
                f'a[href="/s/{org.slug}/{studio_tree.slug}/?assistant=open"]'
            )
            assert studio_link.is_visible()
            assert "Open or resume chat" in studio_link.inner_text()
            popover = picker.locator(".tl-console-assistant-popover")
            popover.evaluate(
                """el => {
                  for (let i = 0; i < 20; i++) {
                    const a = document.createElement('a');
                    a.textContent = 'Additional studio';
                    el.append(a);
                  }
                }"""
            )
            popover_box = popover.bounding_box()
            assert popover_box["x"] >= 0
            assert popover_box["x"] + popover_box["width"] <= width + 0.5
            assert popover.evaluate("el => el.scrollHeight > el.clientHeight")
            assert popover.evaluate("el => getComputedStyle(el).overflowY === 'auto'")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            context.close()
        instance.close()


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_unavailable_assistant_reason_popover_keeps_docs_and_account_reachable(
    engine, login, org_admin, org, studio_tree
):
    html = login(org_admin).get(f"/s/{org.slug}/{studio_tree.slug}/").content.decode()
    assistant_css = (settings.BASE_DIR / "static" / "assistant.css").read_text(
        encoding="utf-8"
    )
    html = _inline_shell_assets(html).replace(
        "</head>", f"<style>{assistant_css}</style></head>"
    )
    entry = (
        '<details class="assistant-unavailable" open>'
        '<summary aria-label="AI assistant unavailable: The AI assistant is disabled for this organization">AI assistant</summary>'
        '<div class="assistant-unavailable-popover">'
        '<p class="assistant-unavailable-reason">The AI assistant is disabled for this organization. '
        'An administrator can review the organization settings and check whether a provider is configured. '
        'This is a deliberately long reason to verify it flows above the link and stays readable on narrow screens.</p>'
        '<button class="assistant-unavailable-retry">Retry</button>'
        '<a class="assistant-setup" href="/orgs/example/settings/assistant">AI settings</a>'
        '<a class="assistant-unavailable-help" href="/docs/" target="_blank" rel="noopener">Documentation</a>'
        '</div></details>'
    )
    html = html.replace('<a class="tl-console-docs-link"', entry + '<a class="tl-console-docs-link"')

    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        artifacts = settings.BASE_DIR.parent.parent / ".artifacts" / "portal-third-batch" / "assistant-scope"
        artifacts.mkdir(parents=True, exist_ok=True)
        for width in (320, 390):
            context, page = _page(instance, html, width=width)
            summary = page.locator(".assistant-unavailable > summary")
            popover_box = _box(page, ".assistant-unavailable-popover")
            assert summary.inner_text() == "AI assistant"
            assert summary.get_attribute("aria-label").startswith("AI assistant unavailable:")
            assert popover_box["height"] > 100
            reason_box = _box(page, ".assistant-unavailable-reason")
            retry_box = _box(page, ".assistant-unavailable-retry")
            settings_box = _box(page, ".assistant-setup")
            help_link = page.locator(".assistant-unavailable-help")
            help_color = help_link.evaluate("e => getComputedStyle(e).color")
            narrow = width < 360
            docs_box = None if narrow else _box(page, ".tl-console-docs-link")
            account_box = _box(page, ".tl-console-account > summary")
            assert reason_box["y"] + reason_box["height"] <= retry_box["y"]
            assert retry_box["y"] + retry_box["height"] <= settings_box["y"]
            assert help_color != "rgb(0, 0, 238)"
            assert help_link.evaluate("e => getComputedStyle(e).textDecorationLine") == "underline"
            assert popover_box["x"] >= 0
            assert popover_box["x"] + popover_box["width"] <= width
            if narrow:
                assert not page.locator(".tl-console-docs-link").is_visible()
            else:
                assert popover_box["y"] >= docs_box["y"] + docs_box["height"]
                assert docs_box["x"] + docs_box["width"] <= account_box["x"]
            assert popover_box["y"] >= account_box["y"] + account_box["height"]
            assert account_box["x"] + account_box["width"] <= width
            page.screenshot(path=str(artifacts / f"assistant-unavailable-{engine}-{width}.png"))
            context.close()
        instance.close()


@pytest.mark.parametrize("engine", ("chromium", "webkit"))
def test_mobile_org_header_keeps_search_health_assistant_docs_and_account_reachable(
    engine, login, org_admin, org, studio_tree, report_row, monkeypatch
):
    from apps.core import health

    org_admin.is_operator_flag = True
    org_admin.save(update_fields=["is_operator_flag"])
    monkeypatch.setattr(health, "quick_health_summary", lambda: {
        "status": "error", "message": "Worker offline", "detail": "Background jobs are paused",
    })
    client = login(org_admin)
    client.get(f"/s/{org.slug}/{studio_tree.slug}/")
    html = _inline_shell_assets(
        client.get(f"/orgs/{org.slug}/settings/members").content.decode()
    )

    with sync_playwright() as playwright:
        instance = getattr(playwright, engine).launch(headless=True)
        for width in (320, 390):
            context, page = _page(instance, html, width=width)
            header = page.locator(".tl-console-header")
            assert page.locator("[data-console-search-toggle]").is_visible()
            assert page.locator("[data-console-health-alert]").is_visible()
            assert page.locator(".tl-console-assistant-picker > summary").is_visible()
            assert page.locator(".tl-console-account > summary").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            selectors = [
                "[data-console-toggle]", ".tl-console-assistant-picker > summary",
                "[data-console-health-alert]", "[data-console-search-toggle]",
                ".tl-console-docs-link", ".tl-console-account > summary",
            ]
            if width < 360:
                # The header docs link folds into the account menu at this width.
                assert not page.locator(".tl-console-docs-link").is_visible()
                selectors.remove(".tl-console-docs-link")
            else:
                assert page.locator(".tl-console-docs-link").is_visible()
            boxes = [_box(page, selector) for selector in selectors]
            assert all(box["x"] >= 0 and box["x"] + box["width"] <= width for box in boxes), boxes
            assert all(
                left["x"] + left["width"] <= right["x"] + 0.5
                for left, right in pairwise(boxes)
            )
            assert header.evaluate("e => e.scrollWidth <= e.clientWidth")
            context.close()
        instance.close()


# Records the header children's boxes on every animation frame in which any of
# them moved, tagged with the page path (sessionStorage survives navigations).
_HEADER_FRAME_SAMPLER = """
(() => {
  let last = '';
  const frame = () => {
    const header = document.querySelector('.tl-console-header');
    if (header) {
      const items = [...header.children].map((el) => {
        const r = el.getBoundingClientRect();
        return [el.className.split(' ')[0], +r.left.toFixed(1), +r.top.toFixed(1),
                +r.width.toFixed(1), +r.height.toFixed(1)];
      }).filter((item) => item[3] || item[4]);
      const signature = JSON.stringify(items);
      if (signature !== last) {
        last = signature;
        const frames = JSON.parse(sessionStorage.getItem('headerFrames') || '[]');
        frames.push([location.pathname, items]);
        sessionStorage.setItem('headerFrames', JSON.stringify(frames));
      }
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
})();
"""


def test_header_items_hold_one_layout_across_pages_and_navigation(
    login, org_admin, org, studio_tree, monkeypatch
):
    """Studio, org and operator pages render the same bar, and the AI assistant
    widget settling in after first paint moves nothing."""
    from apps.assistant.views import _widget_bundle
    from apps.core import health

    org_admin.is_operator_flag = True
    org_admin.save(update_fields=["is_operator_flag"])
    monkeypatch.setattr(health, "quick_health_summary", lambda: {
        "status": "error", "message": "Worker offline", "detail": "Background jobs are paused",
    })
    studio = f"/s/{org.slug}/{studio_tree.slug}"
    paths = [
        f"{studio}/", f"{studio}/settings/members", f"{studio}/settings/datasources",
        f"/orgs/{org.slug}/settings/invites", "/system",
    ]
    client = login(org_admin)
    client.get(f"{studio}/")  # remembers the studio, so org pages carry the search box too
    pages = {}
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        pages[path] = _inline_shell_assets(response.content.decode())
    unavailable = (
        '{"available": false, "reason": "The AI assistant is disabled for this organization."}'
    )

    def serve(route):
        path = route.request.url.removeprefix("http://shell.test")
        if route.request.resource_type == "document" and path in pages:
            route.fulfill(body=pages[path], content_type="text/html")
        elif path == "/api/assistant/widget.js":
            time.sleep(0.15)  # the widget always lands after the first paint
            route.fulfill(body=_widget_bundle(), content_type="application/javascript")
        elif "/api/assistant/available" in path:
            time.sleep(0.1)
            route.fulfill(body=unavailable, content_type="application/json")
        else:
            route.fulfill(status=204)

    layouts = {}
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        context = instance.new_context(viewport={"width": 1440, "height": 760})
        context.add_init_script(_HEADER_FRAME_SAMPLER)
        page = context.new_page()
        page.route("http://shell.test/**", serve)
        page.goto("http://shell.test" + paths[0], wait_until="domcontentloaded")
        for path in paths:
            if page.url != "http://shell.test" + path:
                page.evaluate("sessionStorage.removeItem('headerFrames')")
                page.locator(f'a.tl-console-nav-link[href="{path}"]').click()
                page.wait_for_url("**" + path)
            if path.startswith("/s/"):
                page.locator(".assistant-unavailable").wait_for()
            page.wait_for_timeout(250)  # room for any late shift to be sampled
            frames = [
                items
                for frame_path, items in page.evaluate(
                    "JSON.parse(sessionStorage.getItem('headerFrames') || '[]')"
                )
                if frame_path == path
            ]
            assert frames, path
            # One layout from the first painted frame to the settled page.
            assert len(frames) == 1, (path, frames)
            layouts[path] = [
                item for item in frames[0]
                if item[0] not in {"tl-console-breadcrumbs", "tl-console-header-spacer"}
            ]
        context.close()
        instance.close()

    reference = layouts[paths[0]]
    names = [item[0] for item in reference]
    assert names == [
        "tl-console-assistant-slot", "tl-console-health-alert", "tl-console-search",
        "tl-console-docs-link", "tl-console-account",
    ]
    for path, layout in layouts.items():
        assert layout == reference, "\n".join(map(str, [path, layout, reference]))
