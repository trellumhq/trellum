"""Browser contract for keeping the console document alive around reports."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest
from django.conf import settings

HOST_JS = (settings.BASE_DIR / "static" / "console-report-host.js").read_text(
    encoding="utf-8"
)
HOST_CSS = (settings.BASE_DIR / "static" / "console-report-host.css").read_text(
    encoding="utf-8"
)
SHELL_JS = (settings.BASE_DIR / "static" / "console-shell.js").read_text(
    encoding="utf-8"
)
SHELL_CSS = (settings.BASE_DIR / "static" / "console-shell.css").read_text(
    encoding="utf-8"
)
MENU_JS = (settings.BASE_DIR / "static" / "report_menu.js").read_text(
    encoding="utf-8"
)
SHARE_JS = (settings.BASE_DIR / "static" / "report_share.js").read_text(
    encoding="utf-8"
)
ASSISTANT_JS = (settings.BASE_DIR / "static" / "assistant.js").read_text(
    encoding="utf-8"
)
ASSISTANT_CSS = (settings.BASE_DIR / "static" / "assistant.css").read_text(
    encoding="utf-8"
)


def _catalog_html(*, fast_timeout: bool = False) -> str:
    timeout = (
        "var nativeTimeout=window.setTimeout;window.setTimeout=function(fn,ms){"
        "return nativeTimeout(fn,ms===10000?30:ms);};"
        if fast_timeout
        else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{SHELL_CSS}\n{HOST_CSS}\n{ASSISTANT_CSS}</style></head>
<body class="mgmt tl-console-page">
<div data-console-shell data-console-default-mode="light">
 <aside class="tl-console-sidebar"><a href="/s/demo/casino/">Reports</a></aside>
  <header class="tl-console-header"><button data-console-toggle>Menu</button>
   <span data-console-page-title>Reports</span><span style="flex:1"></span></header>
 <button data-console-backdrop>Close</button>
</div>
<div data-console-catalog data-console-inert style="height:1800px;padding-top:520px">
 <div id="content" data-catalog-ready="true"></div>
 <a id="report" href="/s/demo/casino/r/player-overview/index.html?display=console"
    data-console-report-title="Player overview">Player overview</a>
 <a id="newtab" target="_blank" href="/s/demo/casino/r/player-overview/index.html">New tab</a>
 <a id="download" download href="/s/demo/casino/r/player-overview/index.html">Download</a>
 <a id="foreign" href="/s/demo/other/r/player-overview/index.html">Other studio</a>
 <a id="broken" href="/s/demo/casino/r/broken/index.html"
    data-console-report-title="Broken">Broken</a>
</div>
<section data-console-report-host data-console-inert hidden>
 <div data-console-report-status role="status">Loading report…</div>
 <iframe data-console-report-frame title="Report" hidden></iframe>
</section>
<script>window.PORTAL_CTX={json.dumps({'prefix': '/s/demo/casino', 'studio': {'name': 'Casino'}})};
    {timeout}</script><script>{SHELL_JS}</script><script>{HOST_JS}</script>
<script>{ASSISTANT_JS}</script></body></html>"""


def _report_html() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{ASSISTANT_CSS}</style></head><body>
<a class="fw-crumb" href="/?org=demo">Demo</a>
<a class="fw-back-link" href="/s/demo/casino/">Casino</a>
<button class="fw-share-btn">Copy link</button>
<div class="fw-header"><div class="fw-header-left">Player overview</div>
 <div class="fw-header-right"><button id="reportOption">Options</button><select><option>Range</option></select></div></div>
<div id="reportBody">Report body</div>
<section class="fw-section" id="revenue"><h2>Revenue trend</h2></section>
<script>window._fwUrlSync={{_custom:{{}},registerCustom:function(k,g){{this._custom[k]=g;}},
write:function(){{var p=new URLSearchParams();p.set('range','30d');
Object.keys(this._custom).forEach(function(k){{p.set(k,window._fwUrlSync._custom[k]());}});
history.replaceState(null,'',location.pathname+'?'+p.toString()+'#chart');}}}};
history.replaceState(null,'',location.pathname);</script>
<script>{MENU_JS}</script><script>{SHARE_JS}</script><script>{ASSISTANT_JS}</script></body></html>"""


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


def _open(
    report_browser, *, fast_timeout=False, viewport=None, assistant_available=True,
    assistant_configured=False, assistant_reason="", availability_state=None,
):
    context = report_browser.new_context(viewport=viewport or {"width": 390, "height": 720})
    page = context.new_page()
    errors = []
    top_navigations = []
    shell_requests = []
    assistant_messages = []
    availability_state = availability_state if availability_state is not None else {"available": None}
    page.on("pageerror", lambda error: errors.append(str(error)))

    def route_request(route):
        request = route.request
        if request.is_navigation_request() and request.frame == page.main_frame:
            top_navigations.append(request.url)
        if "/api/report-shell" in request.url:
            shell_requests.append(request.url)
        if "/api/assistant/available" in request.url:
            suggestions = (
                ["Summarise hosted report"]
                if "player-overview" in request.url
                else ["Explore studio"]
            )
            override = availability_state["available"]
            available = assistant_available if override is None else override
            configured = assistant_configured if override is None else True
            reason = assistant_reason if override is None else (
                "Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank."
                if not available else ""
            )
            route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "available": available,
                    "can_configure": not available,
                    "configured": configured,
                    "reason": reason,
                    "settings_url": "/orgs/demo/settings/assistant",
                    "suggestions": suggestions,
                }),
            )
        elif request.url.endswith("/api/assistant/sessions"):
            route.fulfill(content_type="application/json", body=json.dumps({"id": "session-1"}))
        elif request.url.endswith("/api/reports/player-overview/share_links"):
            if request.method == "POST":
                route.fulfill(
                    status=201,
                    content_type="application/json",
                    body=json.dumps({
                        "ok": True,
                        "share_link": {
                            "id": 1,
                            "url": "http://console.test/share/embed-token/",
                            "created_by": "Admin",
                            "created_at": "2026-09-08T12:00:00Z",
                            "expires_at": None,
                            "allow_export": False,
                            "has_password": False,
                            "embed": True,
                            "embed_origins": ["https://app.example.com"],
                            "embed_theme": "",
                            "embed_hide_filters": False,
                            "revoked_at": None,
                            "view_count": 0,
                            "last_viewed_at": None,
                            "blocked_by_policy": False,
                        },
                    }),
                )
                return
            route.fulfill(
                content_type="application/json",
                body=json.dumps({
                    "share_links": [],
                    "sharing_enabled": True,
                    "can_manage_policy": True,
                    "require_password": False,
                    "max_expiry_days": None,
                    "embed_links_enabled": True,
                }),
            )
        elif "/api/assistant/sessions/session-1/message" in request.url:
            assistant_messages.append(json.loads(request.post_data or "{}"))
            route.fulfill(
                content_type="text/event-stream",
                body='data: {"type":"text_delta","text":"Report answer"}\n\n'
                     'data: {"type":"done"}\n\n',
            )
        elif "/r/broken/" in request.url:
            route.fulfill(content_type="text/html", body="<p>Never sends ready</p>")
        elif "/r/" in request.url:
            route.fulfill(content_type="text/html", body=_report_html())
        else:
            route.fulfill(content_type="text/html", body=_catalog_html(fast_timeout=fast_timeout))

    page.route("**/*", route_request)
    page.goto("http://console.test/s/demo/casino/?view=list", wait_until="load")
    top_navigations.clear()
    return context, page, errors, top_navigations, shell_requests, assistant_messages


def test_back_restores_catalog_scroll_after_delayed_catalog_render(report_browser):
    context = report_browser.new_context(viewport={"width": 390, "height": 720})
    page = context.new_page()
    catalog_loads = []
    delayed = _catalog_html().replace(
        'style="height:1800px;padding-top:520px"',
        'style="height:150px;padding-top:20px"',
    ).replace('id="content" data-catalog-ready="true"', 'id="content" data-catalog-ready="false"')
    delayed = delayed.replace(
        "</body>",
        "<script>window.addEventListener('pageshow',function(){setTimeout(function(){"
        "document.querySelector('[data-console-catalog]').style.height='1800px';"
        "var content=document.getElementById('content');content.dataset.catalogReady='true';"
        "window.__catalogRenderComplete=true;window.dispatchEvent(new Event('trellum:catalog-ready'));"
        "},150);});</script></body>",
    )

    def route_request(route):
        path = urlsplit(route.request.url).path
        if path == "/ordinary":
            route.fulfill(content_type="text/html", body="<html><body>Ordinary page</body></html>")
        elif path == "/s/demo/casino/":
            catalog_loads.append(1)
            route.fulfill(content_type="text/html", body=_catalog_html() if len(catalog_loads) == 1 else delayed)
        elif "/api/registry" in path:
            route.fulfill(content_type="application/json", body='{"reports":[]}')
        else:
            route.fulfill(content_type="text/html", body="<html><body>Other</body></html>")

    page.route("**/*", route_request)
    try:
        page.goto("http://console.test/s/demo/casino/", wait_until="load")
        page.evaluate("scrollTo(0, 600)")
        page.wait_for_function("history.state && history.state.tlConsoleCatalog && history.state.tlConsoleCatalog.y >= 590")
        page.goto("http://console.test/ordinary", wait_until="load")
        page.go_back(wait_until="load")
        page.locator("#content[data-catalog-ready='false']").wait_for(state="attached")
        assert page.evaluate("window.__catalogRenderComplete !== true")
        assert page.evaluate("window.scrollY") < 100
        page.wait_for_function("window.__catalogRenderComplete === true")
        page.wait_for_function("window.scrollY >= 590")
        assert len(catalog_loads) == 2
    finally:
        context.close()


def test_report_history_filter_bridge_and_shell_identity(report_browser):
    context, page, errors, top_navigations, shell_requests, _assistant_messages = _open(report_browser)
    try:
        page.evaluate("""window.__shell=document.querySelector('[data-console-shell]');
            window.__header=document.querySelector('.tl-console-header');
            window.__sidebar=document.querySelector('.tl-console-sidebar');scrollTo(0,500);""")
        page.locator("#report").click()
        frame = page.frame_locator("iframe[data-console-report-frame]")
        frame.locator("#reportBody").wait_for(state="visible")
        assert page.url.startswith(
            "http://console.test/s/demo/casino/r/player-overview/index.html"
        )
        assert "display=console" in page.url
        assert "_console_host" not in page.url
        assert page.evaluate("""__shell===document.querySelector('[data-console-shell]')
            && __header===document.querySelector('.tl-console-header')
            && __sidebar===document.querySelector('.tl-console-sidebar')""")
        assert top_navigations == []
        assert shell_requests == []
        assert frame.locator(".fw-header-left").evaluate(
            "el => getComputedStyle(el).display"
        ) == "none"
        assert frame.locator("#reportOption").evaluate(
            "el => getComputedStyle(el).minHeight"
        ) == "44px"
        frame.locator('[data-item-id="share"]').wait_for(state="attached")
        frame.locator("#fwOptionsBtn").click()
        frame.locator('[data-item-id="share"]').click()
        frame.locator("#rswEmbed").check()
        frame.locator("#rswEmbedOrigins").fill("https://app.example.com")
        frame.locator("#rswNewForm button[type=submit]").click()
        frame.locator(".rsw-chip.embed").wait_for()
        assert frame.locator(".rsw-chip.embed").text_content() == "Embed"
        frame.locator("#rswClose").click()

        frame.locator("#reportBody").evaluate("window._fwUrlSync.write()")
        page.wait_for_function("location.search.includes('range=30d')")
        assert "display=console" in page.url
        assert "_console_host" not in page.url

        old_url = page.url
        page.evaluate("window.postMessage({type:'trellum:report-state',hostToken:'bad',"
                      "report:'player-overview',search:'?owned=1'},location.origin)")
        assert page.url == old_url

        for _ in range(5):
            page.go_back(wait_until="commit")
            page.locator("#report").wait_for(state="visible")
            page.wait_for_function("scrollY === 500")
            assert not page.evaluate("window.TrellumConsoleReportHost.isActive()")
            assert page.locator("iframe[data-console-report-frame]").count() == 0

            page.go_forward(wait_until="commit")
            frame.locator("#reportBody").wait_for(state="visible")
            assert page.evaluate("window.TrellumConsoleReportHost.isActive()")
            assert page.locator("iframe[data-console-report-frame]").count() == 1
        page.locator(".tl-console-sidebar a").evaluate("el => el.click()")
        page.locator("#report").wait_for(state="visible")
        assert not page.evaluate("window.TrellumConsoleReportHost.isActive()")
        assert top_navigations == []
        assert errors == []
    finally:
        context.close()


def test_native_links_drawer_inert_and_bounded_failure(report_browser):
    context, page, errors, top_navigations, _shell_requests, _assistant_messages = _open(
        report_browser, fast_timeout=True
    )
    try:
        assert page.locator("#newtab").evaluate("""el => {
            var ev=new MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:true});
            el.addEventListener('click',e=>e.preventDefault(),{once:true});return el.dispatchEvent(ev);}""") is False
        assert not page.evaluate("window.TrellumConsoleReportHost.isActive()")
        page.locator("#report").click()
        page.frame_locator("iframe[data-console-report-frame]").locator("#reportBody").wait_for()
        page.locator("[data-console-toggle]").click()
        assert page.locator("[data-console-report-host]").get_attribute("inert") == ""
        page.locator("[data-console-toggle]").click()
        page.go_back(wait_until="commit")
        page.locator("#broken").click()
        fallback = page.locator("[data-console-report-status] a[data-console-report-native]")
        fallback.wait_for(state="visible")
        assert "_console_host" not in fallback.get_attribute("href")
        assert top_navigations == []
        assert errors == []
    finally:
        context.close()


def test_assistant_opens_once_with_hosted_report_context_and_stable_mobile_history(
    report_browser,
):
    context, page, errors, _top_navigations, _shell_requests, messages = _open(
        report_browser
    )
    try:
        pill = page.locator("#assistantPill")
        pill.wait_for(state="visible")
        assert pill.text_content().strip() == "Ask about this studio"
        page.locator("#report").click()
        frame = page.frame_locator("iframe[data-console-report-frame]")
        frame.locator("#reportBody").wait_for(state="visible")
        frame.locator(".assistant-section-ask").wait_for(state="visible")
        assert pill.text_content().strip() == "Ask about this report"
        assert page.locator("#assistantPanel").count() == 1
        assert frame.locator("#assistantPanel").count() == 0

        page.evaluate("window.__assistantFrame=document.querySelector('iframe').contentWindow")
        frame.locator(".assistant-section-ask").click()
        panel = page.locator("#assistantPanel")
        panel.wait_for(state="visible")
        page.get_by_role("button", name="Summarise hosted report").wait_for()
        assert "Revenue trend" in page.locator("#assistantInput").input_value()
        assert page.evaluate("history.state.assistantSheet && !!history.state.tlConsoleReport")

        page.locator("#assistantInput").fill("What changed?")
        page.locator("#assistantSend").click()
        page.get_by_text("Report answer", exact=True).wait_for()
        assert messages[-1]["page"] == {
            "path": "/s/demo/casino/r/player-overview/index.html",
            "title": "Player overview - Casino",
            "chart": {"section_id": "revenue", "title": "Revenue trend"},
        }

        page.locator("#assistantClose").click()
        panel.wait_for(state="hidden")
        assert page.evaluate(
            "window.__assistantFrame===document.querySelector('iframe').contentWindow"
            " && window.TrellumConsoleReportHost.isActive()"
        )
        pill.click()
        panel.wait_for(state="visible")
        page.go_back(wait_until="commit")
        panel.wait_for(state="hidden")
        assert page.evaluate(
            "window.__assistantFrame===document.querySelector('iframe').contentWindow"
            " && window.TrellumConsoleReportHost.isActive()"
        )
        page.go_back(wait_until="commit")
        page.locator("#report").wait_for(state="visible")
        assert not page.evaluate("window.TrellumConsoleReportHost.isActive()")
        assert errors == []
    finally:
        context.close()


def test_assistant_header_and_setup_entry_fit_console(report_browser):
    desktop = _open(
        report_browser, viewport={"width": 1280, "height": 800}
    )
    context, page, errors, *_ = desktop
    try:
        ask = page.locator("#assistantAsk")
        ask.wait_for(state="visible")
        assert ask.text_content().strip() == "Ask AI"
        page.locator("#report").click()
        page.frame_locator("iframe").locator("#reportBody").wait_for()
        ask.click()
        page.locator("#assistantPanel").wait_for(state="visible")
        host_box = page.locator("[data-console-report-host]").bounding_box()
        assert host_box["x"] + host_box["width"] <= 860
        assert errors == []
    finally:
        context.close()

    unavailable = _open(report_browser, assistant_available=False)
    context, page, errors, *_ = unavailable
    try:
        setup = page.locator(".assistant-setup")
        setup.wait_for(state="visible")
        assert setup.text_content().strip() == "Set up AI →"
        assert setup.get_attribute("aria-label") == "Set up AI"
        setup_box = setup.bounding_box()
        assert setup_box["x"] + setup_box["width"] <= 390
        page.locator("#report").click()
        page.frame_locator("iframe").locator("#reportBody").wait_for()
        setup.wait_for(state="visible")
        assert page.locator("#assistantPanel").count() == 1
        assert page.locator("#assistantPill").is_hidden()
        assert errors == []
    finally:
        context.close()


def test_assistant_header_entry_tracks_availability_changes(report_browser):
    availability_state = {"available": None}
    context, page, errors, *_ = _open(
        report_browser, assistant_available=True, assistant_configured=True,
        availability_state=availability_state,
        viewport={"width": 1280, "height": 800},
    )
    try:
        ask = page.locator("#assistantAsk")
        ask.wait_for(state="visible")
        assert page.locator(".assistant-setup").count() == 0

        availability_state["available"] = False
        page.evaluate(
            "window.dispatchEvent(new CustomEvent('trellum:assistant-context',"
            "{detail:{report:'blocked'}}))"
        )
        settings = page.locator(".assistant-setup")
        settings.wait_for(state="visible")
        assert settings.text_content().strip() == "AI settings →"
        assert "claude-future-9" in settings.get_attribute("aria-label")

        availability_state["available"] = True
        page.evaluate(
            "window.dispatchEvent(new CustomEvent('trellum:assistant-context',"
            "{detail:{report:'ready'}}))"
        )
        settings.wait_for(state="detached")
        ask.wait_for(state="visible")

        availability_state["available"] = False
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
        page.locator(".assistant-setup").wait_for(state="visible")
        assert errors == []
    finally:
        context.close()

    configured_unavailable = _open(
        report_browser, assistant_available=False, assistant_configured=True,
        assistant_reason="Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank.",
    )
    context, page, errors, *_ = configured_unavailable
    try:
        settings = page.locator(".assistant-setup")
        settings.wait_for(state="visible")
        assert settings.text_content().strip() == "AI settings →"
        assert settings.get_attribute("aria-label") == (
            "AI settings: Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank."
        )
        assert settings.get_attribute("title") == "Set input and output prices for model 'claude-future-9' in AI settings to enforce budgets, or leave both budgets blank."
        assert errors == []
    finally:
        context.close()
