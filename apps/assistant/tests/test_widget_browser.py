"""One smoke test that runs the shipped widget bundle on a report page."""

import json

import pytest

PAGE = "http://reports.test/s/demo/casino/r/player-overview/index.html"
PAGE_HTML = """<!doctype html>
<html><head><title>Player Overview</title></head><body>
  <div class="fw-header-right"><div id="fwOptionsWrap"></div></div>
  <script src="/api/assistant/widget.js"></script>
</body></html>"""
REPLY = (
    'data: {"type": "turn_start"}\n\n'
    'data: {"type": "text_delta", "text": "Hello from the fake model"}\n\n'
    'data: {"type": "done"}\n\n'
)


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed")

    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(headless=True)
        except Error as exc:  # pragma: no cover - depends on local browsers
            pytest.skip(f"chromium is unavailable: {exc}")
        yield instance
        instance.close()


def test_widget_boots_opens_and_posts_with_the_csrf_header(browser):
    from apps.assistant.views import _widget_bundle

    context = browser.new_context(viewport={"width": 1440, "height": 800})
    context.add_cookies([{"name": "csrftoken", "value": "tok123", "url": PAGE}])
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(error))
    posts = []

    def respond(route):
        request = route.request
        path = request.url.removeprefix("http://reports.test")
        if path == "/api/assistant/widget.js":
            route.fulfill(body=_widget_bundle(), content_type="application/javascript")
        elif path.startswith("/s/demo/casino/api/assistant/available"):
            route.fulfill(content_type="application/json", body=json.dumps(
                {"available": True, "suggestions": [], "deadline_s": 30, "studio_name": "Casino"}
            ))
        elif request.method == "POST" and path.endswith("/sessions"):
            posts.append((path, request.headers))
            route.fulfill(content_type="application/json", body=json.dumps(
                {"id": 7, "scope": "studio", "report": None}
            ))
        elif request.method == "POST" and path.endswith("/sessions/7/message"):
            posts.append((path, request.headers))
            route.fulfill(content_type="text/event-stream", body=REPLY)
        elif request.resource_type == "document":
            route.fulfill(content_type="text/html", body=PAGE_HTML)
        else:
            route.fulfill(status=204)

    page.route("http://reports.test/**", respond)
    try:
        page.goto(PAGE, wait_until="load")
        ask = page.locator("#assistantAsk")
        ask.wait_for(state="visible")
        ask.click()
        page.locator("#assistantPanel.assistant-open").wait_for()
        page.locator("#assistantInput").fill("How was revenue?")
        page.locator("#assistantSend").click()
        page.locator("#assistantMessages").get_by_text("Hello from the fake model").wait_for()
        assert [path for path, _ in posts] == [
            "/s/demo/casino/api/assistant/sessions",
            "/s/demo/casino/api/assistant/sessions/7/message",
        ]
        assert all(headers.get("x-csrftoken") == "tok123" for _, headers in posts)
        assert not errors
    finally:
        context.close()
