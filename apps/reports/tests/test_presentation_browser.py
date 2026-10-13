"""The same built deck plays standalone, shared, and in the console iframe."""
from __future__ import annotations

import json
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings

from trellum.rendering.cdn import serve_vendor_request
from trellum.runner.execute import run_report
from apps.reports.tests.test_console_report_host_runtime import _catalog_html, MENU_JS


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def deck_browser(request):
    from playwright.sync_api import Error, sync_playwright
    with sync_playwright() as playwright:
        try:
            browser = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:
            pytest.skip(f"{request.param} unavailable: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def deck_server(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    source = tmp_path / "reports" / "checkout-presentation"
    shutil.copytree(settings.BASE_DIR / "trellum/demo/reports/checkout-presentation", source)
    output = Path(run_report(str(source)))
    markup = (output / "index.html").read_text(encoding="utf-8")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if serve_vendor_request(self):
                return
            path = urlsplit(self.path).path
            if path == "/s/demo/casino/":
                body = _catalog_html().replace('<section data-console-report-host',
                    '<section class="tl-console-report-host" data-console-report-host').replace(
                    '<iframe data-console-report-frame', '<iframe allow="fullscreen" allowfullscreen data-console-report-frame')
                content_type = "text/html"
            elif path.endswith("/index.html") or path in ("/", "/share/deck/"):
                body = markup
                if path.startswith("/s/"):
                    body = body.replace("</body>", f"<script>{MENU_JS}</script></body>")
                content_type = "text/html"
            elif "/api/" in path:
                body, content_type = json.dumps({"available": False, "configured": False}), "application/json"
            else:
                # Evidence URLs remain relative to each document's location.
                for prefix in ("/share/deck", "/s/demo/casino/r/player-overview"):
                    if path.startswith(prefix + "/"):
                        self.path = path[len(prefix):]
                return super().do_GET()
            content = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", tmp_path
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("path", ("/", "/share/deck/", "/s/demo/casino/r/player-overview/index.html?display=focus"))
def test_deck_navigation_history_fullscreen_denial_and_focus(deck_browser, deck_server, path):
    origin, output = deck_server
    context = deck_browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + path + "#slide-mobile", wait_until="networkidle")
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 2 of 4"
    page.evaluate("() => { document.querySelector('.fw-presentation').requestFullscreen = () => Promise.reject(new Error('denied')); }")
    page.get_by_role("button", name="Present", exact=True).click()
    assert page.locator(".fw-presentation-notice").inner_text().startswith("Fullscreen unavailable")
    assert page.locator(".fw-slide:visible").count() == 1
    assert page.evaluate("document.activeElement.id") == "slide-mobile-title"
    page.keyboard.press("ArrowRight")
    assert page.url.endswith("#slide-comparison")
    page.go_back()
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 2 of 4"
    page.go_forward()
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 3 of 4"
    page.keyboard.press("Home")
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 1 of 4"
    page.keyboard.press("End")
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 4 of 4"
    page.keyboard.press("ArrowLeft")
    page.screenshot(path=str(output / "presentation-desktop.png"))
    assert page.locator(".fw-slide:visible").bounding_box()["width"] <= 1440
    page.keyboard.press("Escape")
    assert page.locator(".fw-slide:visible").count() == 4
    assert page.evaluate("document.activeElement.textContent") == "Present"
    page.evaluate("history.pushState(history.state, '', location.pathname + location.search); window.dispatchEvent(new PopStateEvent('popstate'))")
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 1 of 4"
    assert not errors
    context.close()


def test_native_fullscreen_lifecycle_and_mobile_click_swipe(deck_browser, deck_server):
    origin, output = deck_server
    context = deck_browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True)
    page = context.new_page()
    page.goto(origin, wait_until="networkidle")
    page.get_by_role("button", name="Present", exact=True).click()
    page.wait_for_function("document.fullscreenElement || document.querySelector('.fw-presentation-notice').textContent")
    if deck_browser.browser_type.name == "chromium":
        assert page.evaluate("!!document.fullscreenElement")
    if page.evaluate("!!document.fullscreenElement"):
        page.evaluate("document.exitFullscreen()")
        page.wait_for_function("!document.body.classList.contains('fw-presenting')")
        page.get_by_role("button", name="Present", exact=True).click()
    page.get_by_role("button", name="Next", exact=True).click()
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 2 of 4"
    assert page.locator(".fw-slide:visible").bounding_box()["width"] <= 390
    page.screenshot(path=str(output / "presentation-mobile.png"), full_page=True)
    page.evaluate("""() => {
        var el = document.querySelector('.fw-slide:not([hidden])');
        function touch(type, x) {
            var event = new Event(type, {bubbles:true});
            Object.defineProperty(event, type === 'touchstart' ? 'touches' : 'changedTouches',
                {value:[{clientX:x, clientY:200}]});
            el.dispatchEvent(event);
        }
        touch('touchstart', 300); touch('touchend', 100);
    }""")
    assert page.locator(".fw-presentation-position").inner_text() == "Slide 3 of 4"
    page.get_by_role("button", name="Next", exact=True).click()
    page.evaluate("""() => {
        var text = document.querySelector('#slide-next-step .fw-slide-text');
        for (var i=0;i<40;i++) { var p=document.createElement('p'); p.textContent='Long discussion paragraph '+i; text.appendChild(p); }
    }""")
    page.get_by_text("Long discussion paragraph 39", exact=True).scroll_into_view_if_needed()
    assert page.get_by_text("Long discussion paragraph 39", exact=True).is_visible()
    page.get_by_role("button", name="Exit presentation", exact=True).click()
    assert page.locator(".fw-slide:visible").count() == 4
    context.close()


def test_host_fallback_hides_chrome_and_guards_messages(deck_browser, deck_server):
    origin, _output = deck_server
    context = deck_browser.new_context(viewport={"width": 1360, "height": 900})
    page = context.new_page()
    page.goto(origin + "/s/demo/casino/", wait_until="networkidle")
    page.locator("#report").click()
    frame = page.frame_locator("[data-console-report-frame]")
    frame.get_by_role("button", name="Present", exact=True).wait_for()
    child = page.frames[1]
    child.evaluate("() => { document.querySelector('.fw-presentation').requestFullscreen = () => Promise.reject(new Error('denied')); }")
    page.evaluate("window.postMessage({type:'trellum:presentation',report:'player-overview',hostToken:'wrong',presenting:true},location.origin)")
    assert not page.locator("body").evaluate("el => el.classList.contains('tl-console-presenting')")
    frame.get_by_role("link", name="Investigate before choosing an experiment", exact=True).click()
    assert len(page.frames) == 2
    frame.get_by_role("button", name="Present", exact=True).click()
    page.wait_for_function("document.body.classList.contains('tl-console-presenting')")
    assert not page.locator(".tl-console-header").is_visible()
    assert page.locator("[data-console-report-host]").bounding_box()["x"] == 0
    assert frame.locator(".fw-presentation-position").inner_text() == "Slide 4 of 4"
    child.locator(".fw-slide:visible h2").focus()
    page.keyboard.press("Escape")
    page.wait_for_function("!document.body.classList.contains('tl-console-presenting')")
    assert page.locator(".tl-console-header").is_visible()
    assert frame.get_by_role("button", name="Present", exact=True).evaluate("el => el === document.activeElement")
    child.evaluate("delete document.querySelector('.fw-presentation').requestFullscreen")
    frame.get_by_role("button", name="Present", exact=True).click()
    child.wait_for_function("document.fullscreenElement || document.querySelector('.fw-presentation-notice').textContent")
    if deck_browser.browser_type.name == "chromium":
        assert child.evaluate("!!document.fullscreenElement")
    frame.get_by_role("button", name="Exit presentation", exact=True).click()
    page.wait_for_function("!document.body.classList.contains('tl-console-presenting')")
    context.close()


def test_no_javascript_keeps_every_slide_readable(deck_browser, deck_server):
    origin, _output = deck_server
    context = deck_browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(origin + "/share/deck/", wait_until="load")
    assert page.locator(".fw-slide:visible").count() == 4
    assert page.locator(".fw-presentation-controls").count() == 0
    assert page.locator("body").bounding_box()["width"] <= 390
    context.close()
