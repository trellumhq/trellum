"""Browser-level report display mode behavior."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings

REPORT_MENU_JS = (settings.BASE_DIR / "static" / "report_menu.js").read_text(
    encoding="utf-8"
)
REPORT_SHARE_JS = (settings.BASE_DIR / "static" / "report_share.js").read_text(
    encoding="utf-8"
)
CONSOLE_SHELL_JS = (settings.BASE_DIR / "static" / "console-shell.js").read_text(
    encoding="utf-8"
)
CONSOLE_SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)
SHELL_HTML = """<div class="tl-console-shell" data-console-shell
  data-console-default-mode="dark" data-console-theme="light"
  data-console-css="/static/console-shell.css"
  data-console-script="/static/console-shell.js">
  <aside class="tl-console-sidebar" id="consoleSidebar">
    <a href="/s/demo/casino/">Reports</a>
    <button type="button" data-console-close>Close</button>
    <button type="button" data-console-collapse>Collapse</button>
  </aside>
  <header class="tl-console-header">
    <button type="button" data-console-toggle aria-controls="consoleSidebar">Menu</button>
    <span data-console-page-title>Test report</span>
    <button type="button" data-report-display="focus">Expand</button>
    <button type="button" data-report-display="monitor">Monitor</button>
  </header>
  <button type="button" data-console-backdrop>Close navigation</button>
</div>"""
REPORT_HTML = f"""<!doctype html>
<html><head><style>
html,body{{margin:0}} .fw-container{{box-sizing:border-box;padding:12px 20px}}
.fw-header{{display:flex}} .fw-filter-bar{{display:block;box-sizing:border-box;
position:sticky;top:48px;width:100vw;padding:8px 24px;
margin-left:calc(-50vw + 50%);margin-right:calc(-50vw + 50%)}}
</style></head><body>
<div class="fw-container" id="reportContent">
  <div class="fw-header"><div class="fw-header-left"><h1>Test report</h1></div>
    <div class="fw-header-right"></div></div>
  <div class="fw-filter-bar">Date range</div><main id="reportBody">Report body</main>
</div>
<script>
window._fwUrlSync = {{_custom: {{}}, registerCustom: function(key, getter) {{
  this._custom[key] = getter;
}}, write: function() {{
  var params = new URLSearchParams(); params.set('range', '30d');
  Object.keys(this._custom).forEach(function(key) {{
    params.set(key, window._fwUrlSync._custom[key]());
  }});
  history.replaceState(null, '', location.pathname + '?' + params.toString());
}}}};
</script>
<script>{REPORT_MENU_JS}</script>
<script>{REPORT_SHARE_JS}</script>
</body></html>"""


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def report_browser(request):
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


@pytest.fixture
def open_report(report_browser):
    contexts = []

    def _open(
        path: str, *, shell_status: int = 503, viewport=None, fail_console_css=False
    ):
        context = report_browser.new_context(viewport=viewport)
        contexts.append(context)
        page = context.new_page()
        shell_requests = []
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        def respond(route):
            if "/api/report-shell" in route.request.url:
                shell_requests.append(route.request.url)
                route.fulfill(
                    status=shell_status,
                    content_type="text/html",
                    body=SHELL_HTML if shell_status == 200 else "unavailable",
                )
            elif route.request.url.endswith("/static/console-shell.css"):
                if fail_console_css:
                    route.abort()
                else:
                    route.fulfill(content_type="text/css", body=CONSOLE_SHELL_CSS)
            elif route.request.url.endswith("/static/console-shell.js"):
                route.fulfill(
                    content_type="application/javascript", body=CONSOLE_SHELL_JS
                )
            elif route.request.url.endswith("/api/reports/player-overview/share_links"):
                route.fulfill(
                    content_type="application/json",
                    body=(
                        '{"share_links":[],"sharing_enabled":true,'
                        '"can_manage_policy":true,"require_password":false,'
                        '"max_expiry_days":null,"embed_links_enabled":true}'
                    ),
                )
            else:
                route.fulfill(status=200, content_type="text/html", body=REPORT_HTML)

        page.route("**/*", respond)
        page.goto(f"http://reports.test{path}", wait_until="load")
        return page, shell_requests, page_errors

    yield _open
    for context in contexts:
        context.close()


def test_focus_transition_preserves_other_query_and_fragment(open_report):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html"
        "?display=focus&range=30d#revenue"
    )
    assert page.locator("body").evaluate("el => el.classList.contains('tl-report-focus')")
    assert page.locator(".fw-header").evaluate("el => getComputedStyle(el).display") == "flex"
    filter_bar = page.locator(".fw-filter-bar")
    assert filter_bar.evaluate("el => getComputedStyle(el).marginLeft") == "0px"
    assert filter_bar.bounding_box()["x"] + filter_bar.bounding_box()["width"] <= page.evaluate(
        "window.innerWidth"
    )
    share_item = page.locator('[data-item-id="share"]')
    share_item.wait_for(state="attached")
    assert page.locator('[data-item-id="display-monitor"] svg').count() == 1
    page.locator("#fwOptionsBtn").click()
    share_item.click()
    embed = page.locator("#rswEmbed")
    embed.wait_for()
    assert embed.is_enabled()
    embed.check()
    assert page.locator("#rswEmbedOriginsField").is_visible()
    page.evaluate("window.ReportShare.close()")

    with page.expect_navigation(wait_until="load"):
        page.locator('[data-item-id="display-monitor"]').evaluate("el => el.click()")
    target = urlsplit(page.url)
    assert parse_qs(target.query) == {"display": ["monitor"], "range": ["30d"]}
    assert target.fragment == "revenue"
    assert errors == []


def test_console_shell_failure_leaves_report_usable(open_report):
    page, shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html"
        "?display=console&range=7d#chart"
    )
    page.wait_for_timeout(100)
    assert len(shell_requests) == 1
    assert page.locator("[data-console-shell]").count() == 0
    assert not page.locator("body").evaluate(
        "el => el.classList.contains('tl-report-console-mounted')"
    )
    assert page.locator("#reportBody").is_visible()
    page.locator('[data-item-id="share"]').wait_for(state="attached")
    assert page.locator('[data-item-id="display-focus"] svg').count() == 1
    with page.expect_navigation(wait_until="load"):
        page.locator('[data-item-id="display-focus"]').evaluate("el => el.click()")
    target = urlsplit(page.url)
    assert parse_qs(target.query) == {"display": ["focus"], "range": ["7d"]}
    assert target.fragment == "chart"
    assert len(shell_requests) == 1
    assert errors == []


def test_console_asset_failure_releases_reserved_space(open_report):
    page, shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=console",
        shell_status=200,
        fail_console_css=True,
        viewport={"width": 1280, "height": 720},
    )
    page.wait_for_timeout(150)
    assert len(shell_requests) == 1
    assert not page.locator("body").evaluate(
        "el => el.classList.contains('tl-report-console-pending')"
    )
    assert not page.locator("body").evaluate(
        "el => el.classList.contains('tl-report-console-mounted')"
    )
    assert page.locator("#reportBody").is_visible()
    assert page.locator("body").evaluate("el => getComputedStyle(el).paddingLeft") == "0px"
    assert errors == []


def test_console_mounts_shared_shell_and_transitions_to_focus(open_report):
    page, shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html"
        "?display=console&range=30d#revenue",
        shell_status=200,
        viewport={"width": 1280, "height": 720},
    )
    page.wait_for_selector("body.tl-report-console-mounted")
    shell = page.locator("[data-console-shell]")
    assert len(shell_requests) == 1
    page.locator('[data-console-shell][data-console-ready="true"]').wait_for(state="attached")
    assert shell.get_attribute("data-console-theme") == "dark"
    assert page.locator("body").evaluate("el => getComputedStyle(el).paddingLeft") == "240px"
    assert not page.locator("#reportContent").get_attribute("inert")
    filter_bar = page.locator(".fw-filter-bar")
    assert filter_bar.evaluate("el => getComputedStyle(el).top") == "104px"
    assert filter_bar.bounding_box()["x"] >= 240
    assert filter_bar.bounding_box()["x"] + filter_bar.bounding_box()["width"] <= 1280

    page.locator("[data-console-collapse]").click()
    assert page.locator("body").evaluate(
        "el => el.classList.contains('tl-console-collapsed')"
    )
    assert page.locator("body").evaluate("el => getComputedStyle(el).paddingLeft") == "64px"

    with page.expect_navigation(wait_until="load"):
        page.locator('[data-report-display="focus"]').click()
    target = urlsplit(page.url)
    assert parse_qs(target.query) == {"display": ["focus"], "range": ["30d"]}
    assert target.fragment == "revenue"
    assert errors == []


def test_report_filter_url_sync_preserves_console_mode(open_report):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=console",
        shell_status=200,
    )
    page.evaluate("window._fwUrlSync.write()")
    assert parse_qs(urlsplit(page.url).query) == {
        "display": ["console"],
        "range": ["30d"],
    }
    assert errors == []


def test_console_mobile_drawer_starts_closed_and_manages_report_inert(open_report):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=console",
        shell_status=200,
        viewport={"width": 390, "height": 720},
    )
    page.wait_for_selector("body.tl-report-console-mounted")
    sidebar = page.locator(".tl-console-sidebar")
    report = page.locator("#reportContent")
    assert sidebar.get_attribute("aria-hidden") == "true"
    assert sidebar.get_attribute("inert") == ""
    assert report.get_attribute("inert") is None
    assert page.evaluate("document.documentElement.scrollWidth") <= 390

    page.locator("[data-console-toggle]").click()
    assert sidebar.get_attribute("aria-hidden") == "false"
    assert report.get_attribute("inert") == ""
    page.keyboard.press("Escape")
    assert sidebar.get_attribute("aria-hidden") == "true"
    assert report.get_attribute("inert") is None
    assert errors == []


def test_monitor_hides_controls_and_has_pointer_touch_keyboard_exit(open_report):
    page, shell_requests, errors = open_report(
        "/content/demo/casino/player-overview/builds/b7/index.html"
        "?display=monitor&range=30d#revenue"
    )
    assert shell_requests == []
    assert not page.locator(".fw-header").is_visible()
    assert not page.locator(".fw-filter-bar").is_visible()
    exit_button = page.locator(".tl-report-monitor-exit")
    assert exit_button.count() == 1

    page.mouse.move(20, 20)
    assert exit_button.evaluate("el => el.classList.contains('is-visible')")
    exit_button.evaluate("el => el.classList.remove('is-visible')")
    page.evaluate("document.dispatchEvent(new Event('touchstart'))")
    assert exit_button.evaluate("el => el.classList.contains('is-visible')")
    with page.expect_navigation(wait_until="load"):
        page.keyboard.press("Escape")
    target = urlsplit(page.url)
    assert parse_qs(target.query) == {"display": ["console"], "range": ["30d"]}
    assert target.fragment == "revenue"
    assert errors == []


@pytest.mark.parametrize(
    "path",
    [
        "/share/public-token/?display=monitor",
        "/standalone/player-overview/index.html?display=console",
        "/s/demo/casino/r/player-overview/index.html?display=unknown",
        "/s/demo/casino/r/player-overview/index.html",
    ],
)
def test_unsupported_and_unauthenticated_contexts_stay_unchanged(open_report, path):
    page, shell_requests, errors = open_report(path)
    assert shell_requests == []
    assert page.locator("[class*=tl-report-]").count() == 0
    assert page.locator(".tl-report-monitor-exit").count() == 0
    assert page.locator(".fw-header").is_visible()
    assert errors == []


def test_display_mode_does_not_write_browser_storage(open_report):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=focus"
    )
    assert page.evaluate("localStorage.length") == 0
    assert page.evaluate("sessionStorage.length") == 0
    assert errors == []
