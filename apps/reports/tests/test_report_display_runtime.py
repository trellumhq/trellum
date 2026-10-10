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
REPORT_HEADER_CSS = "\n".join(
    (settings.BASE_DIR / "trellum" / "static" / "css" / path).read_text(encoding="utf-8")
    for path in ("base.css", "components/header.css", "components/toggle.css")
)
REPORT_METADATA_JS = (
    settings.BASE_DIR / "trellum" / "static" / "js" / "components"
    / "report_metadata.js"
).read_text(encoding="utf-8")
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
<html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>{REPORT_HEADER_CSS}
html,body{{margin:0}} .fw-container{{box-sizing:border-box;padding:12px 20px}}
.fw-header{{display:flex;justify-content:space-between;gap:8px;box-sizing:border-box;max-width:100%}}
.fw-header-left{{min-width:0;white-space:nowrap}} .fw-header-right{{display:flex;align-items:center;gap:6px;min-width:0;max-width:100%}}
.fw-toggle-group{{display:flex;gap:4px}} .fw-toggle-btn{{min-height:36px;padding:4px 9px}}
.fw-header-right select{{max-width:100%;min-height:36px}} .fw-help-wrap{{white-space:nowrap}}
.fw-filter-bar{{display:block;box-sizing:border-box;
position:sticky;top:var(--fw-sticky-offset,48px);width:100vw;padding:8px 24px;
margin-left:calc(-50vw + 50%);margin-right:calc(-50vw + 50%)}}
.fw-report-metadata{{display:flex;align-items:center;gap:12px;min-height:30px;
box-sizing:border-box;padding:5px 12px;position:sticky;top:var(--fw-header-h,48px)}}
body.fw-only .fw-header,body.fw-only .fw-report-metadata{{display:none}}
.fw-freshness{{display:inline-flex;align-items:center;gap:4px}}
</style></head><body>
<div class="fw-header"><div class="fw-header-left"><h1>Test report</h1></div>
  <div class="fw-header-right"><div class="fw-toggle-group fw-scope-toggle" data-toggle-id="__scope__"><button class="fw-toggle-btn active" data-scope-key="north">North</button><button class="fw-toggle-btn" data-scope-key="south">South</button><button class="fw-toggle-btn" data-scope-key="combined">Combined</button></div><select class="fw-theme-select" id="fwThemeSelect"><option value="light" selected>Light</option><option value="high-contrast">High contrast</option></select><div class="fw-help-wrap">Help</div></div></div>
<div class="fw-report-metadata" id="fwReportMetadata">
  <span class="fw-freshness" id="fwFreshness"></span>
  <span class="fw-report-metadata-item"><span>Last event</span><span>2026-10-10</span></span>
</div>
<div class="fw-container" id="reportContent">
  <div class="fw-filter-bar">Date range</div><main id="reportBody">Report body</main>
  <div id="assistantPill">Ask about this report</div>
</div>
<script>(function(){{var only=new URLSearchParams(location.search).get('only');
if(only&&document.getElementById(only))document.body.classList.add('fw-only')}})();</script>
<script>window._reportData={{_freshness:{{generated_at:new Date(Date.now()-3*3600000).toISOString(),refresh_seconds:0}}}};
{REPORT_METADATA_JS}</script>
<script>
window.themeChanges = 0;
document.getElementById('fwThemeSelect').addEventListener('change', function(){{window.themeChanges++;}});
document.querySelectorAll('.fw-scope-toggle .fw-toggle-btn').forEach(function(button){{button.addEventListener('click',function(){{
  document.querySelectorAll('.fw-scope-toggle .fw-toggle-btn').forEach(function(other){{other.classList.toggle('active',other===button);}});
}});}});
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
        path: str, *, shell_status: int = 503, viewport=None, fail_console_css=False,
        installed=None,
    ):
        context = report_browser.new_context(
            viewport=viewport, is_mobile=bool(viewport and viewport["width"] < 768),
            has_touch=bool(viewport and viewport["width"] < 768),
        )
        contexts.append(context)
        if installed == "standalone":
            context.add_init_script("""const originalMatchMedia = window.matchMedia.bind(window);
                window.matchMedia = query => query === '(display-mode: standalone)'
                    ? {matches: true, media: query} : originalMatchMedia(query);""")
        elif installed == "ios":
            context.add_init_script("Object.defineProperty(navigator, 'standalone', {value: true});")
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
    page.wait_for_function("document.getElementById('fwFreshness').textContent.includes('Updated')")
    assert page.locator("#fwFreshness").inner_text() == "Updated 3h ago"
    assert page.locator("#fwReportMetadata").is_visible()
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
    expected_top = page.locator(".fw-header").bounding_box()["height"] + page.locator(
        "#fwReportMetadata"
    ).bounding_box()["height"] + 56
    assert abs(float(filter_bar.evaluate("el => getComputedStyle(el).top")[:-2]) - expected_top) < 1
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
        "?display=monitor&range=30d#revenue",
        viewport={"width": 390, "height": 720},
    )
    assert shell_requests == []
    assert not page.locator(".fw-header").is_visible()
    assert not page.locator(".fw-filter-bar").is_visible()
    metadata = page.locator("#fwReportMetadata")
    assert metadata.is_visible()
    page.wait_for_function("document.getElementById('fwFreshness').textContent.includes('Updated')")
    assert page.locator("#fwFreshness").inner_text() == "Updated 3h ago"
    assert "Last event" in metadata.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    exit_button = page.locator(".tl-report-monitor-exit")
    assert exit_button.count() == 1
    exit_box = exit_button.bounding_box()
    assert abs(exit_box["y"] + exit_box["height"] - (720 - 12)) < 1
    assert abs(exit_box["x"] + exit_box["width"] - (390 - 12)) < 1

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


def test_only_embed_hides_report_metadata_strip(open_report):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?only=reportBody"
    )
    assert page.locator("body").evaluate("el => el.classList.contains('fw-only')")
    assert not page.locator("#fwReportMetadata").is_visible()
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


@pytest.mark.parametrize("installed", ["standalone", "ios"])
@pytest.mark.parametrize("path", [
    "/s/demo/casino/r/player-overview/index.html",
    "/content/demo/casino/player-overview/builds/b7/index.html",
])
def test_installed_report_defaults_to_monitor_and_can_return_to_console(open_report, installed, path):
    page, shell_requests, errors = open_report(
        path + "?range=30d#revenue", installed=installed,
        viewport={"width": 390, "height": 720},
    )
    assert shell_requests == []
    assert not page.locator(".fw-header").is_visible()
    assert not page.locator(".fw-filter-bar").is_visible()
    assert not page.locator("#assistantPill").is_visible()
    assert parse_qs(urlsplit(page.url).query) == {"display": ["monitor"], "range": ["30d"]}
    assert urlsplit(page.url).fragment == "revenue"
    page.evaluate("window._fwUrlSync.write()")
    assert parse_qs(urlsplit(page.url).query) == {"display": ["monitor"], "range": ["30d"]}
    page.reload()
    assert page.locator("body.tl-report-monitor").count() == 1
    with page.expect_navigation(wait_until="load"):
        page.locator(".tl-report-monitor-exit").click()
    assert parse_qs(urlsplit(page.url).query) == {"display": ["console"], "range": ["30d"]}
    assert page.locator(".fw-header").is_visible()
    assert errors == []


@pytest.mark.parametrize("display", ["console", "focus", "unknown"])
def test_installed_report_respects_explicit_display_mode(open_report, display):
    page, _requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=" + display,
        installed="standalone",
    )
    assert page.locator(".fw-header").is_visible()
    assert page.locator(".tl-report-monitor-exit").count() == 0
    assert errors == []


@pytest.mark.parametrize("path", ["/share/public-token/", "/standalone/player-overview/index.html"])
def test_installed_context_does_not_change_unsupported_report_paths(open_report, path):
    page, requests, errors = open_report(path, installed="standalone")
    assert requests == []
    assert page.locator(".fw-header").is_visible()
    assert page.locator(".tl-report-monitor-exit").count() == 0
    assert errors == []


@pytest.mark.parametrize("width", [320, 390, 430])
def test_mobile_toolbar_uses_native_controls_and_fits(open_report, width):
    page, _shell_requests, errors = open_report(
        "/s/demo/casino/r/player-overview/index.html?display=console&range=30d#revenue",
        viewport={"width": width, "height": 720},
        shell_status=200,
    )
    page.wait_for_selector("body.tl-report-console-mounted")
    page.locator('[data-item-id="share"]').wait_for(state="attached")
    options = page.locator("select.rmw-mobile-options")
    assert options.is_visible()
    labels = options.locator("option").all_text_contents()
    assert labels[0] == "Options"
    assert "Expand" in labels and "Monitor" in labels and "Share" in labels
    page.evaluate("window.__reportMenu.register({id:'later',label:'Later',order:100,onSelect:function(){window.laterCalls=(window.laterCalls||0)+1;}})")
    assert "Later" in options.locator("option").all_text_contents()
    options.select_option("later")
    assert options.input_value() == ""
    assert page.evaluate("window.laterCalls") == 1
    page.evaluate("window.__reportMenu.register({id:'later',label:'Updated'})")
    assert options.locator('option[value="later"]').count() == 1
    options.select_option("later")
    assert page.evaluate("window.laterCalls") == 2
    assert page.locator("#fwOptionsBtn").evaluate("el => getComputedStyle(el).display") == "none"
    assert page.locator(".rmw-mobile-scope").is_visible()
    assert options.locator('optgroup[label="Report theme"] option').all_text_contents() == ["Light", "High contrast"]
    page.locator(".rmw-mobile-scope").select_option("south")
    assert page.locator('.fw-scope-toggle [data-scope-key="south"]').evaluate(
        "el => el.classList.contains('active')"
    )
    page.locator('.fw-scope-toggle [data-scope-key="combined"]').evaluate("el => el.click()")
    assert page.locator(".rmw-mobile-scope").input_value() == "combined"
    options.select_option("theme:high-contrast")
    assert page.locator("#fwThemeSelect").input_value() == "high-contrast"
    assert page.evaluate("window.themeChanges") == 1
    assert options.input_value() == ""
    assert not page.locator("#fwThemeSelect").is_visible()
    assert page.locator(".fw-header").bounding_box()["height"] <= 60
    for control in (options, page.locator(".rmw-mobile-scope"), page.locator(".rmw-monitor-link")):
        box = control.bounding_box()
        assert box["height"] >= 44 and box["width"] >= 60
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.locator(".rmw-monitor-link").is_visible()
    monitor_href = page.locator(".rmw-monitor-link").get_attribute("href")
    assert monitor_href == "/s/demo/casino/r/player-overview/index.html?display=monitor&range=30d#revenue"
    options.select_option("share")
    page.locator("#rswClose").click()
    assert options.evaluate("el => el === document.activeElement")
    assert errors == []


@pytest.mark.parametrize("path", [
    "/s/demo/casino/r/player-overview/index.html",
    "/content/demo/casino/player-overview/builds/b7/index.html",
])
def test_mobile_monitor_link_tracks_filters_and_opens_directly(open_report, path):
    page, _requests, errors = open_report(
        path + "?display=focus&range=30d#revenue",
        viewport={"width": 390, "height": 720},
    )
    page.evaluate("history.replaceState(null, '', location.pathname + '?display=focus&range=7d&_console_host=1&_console_host_token=internal#chart')")
    link = page.locator(".rmw-monitor-link")
    assert link.get_attribute("href") == (
        "/s/demo/casino/r/player-overview/index.html?display=monitor&range=7d#chart"
    )
    with page.expect_navigation(wait_until="load"):
        link.click()
    page.reload()
    assert not page.locator(".fw-header").is_visible()
    assert not page.locator(".fw-filter-bar").is_visible()
    with page.expect_navigation(wait_until="load"):
        page.locator(".tl-report-monitor-exit").click()
    assert parse_qs(urlsplit(page.url).query) == {"display": ["console"], "range": ["7d"]}
    assert urlsplit(page.url).fragment == "chart"
    assert errors == []
