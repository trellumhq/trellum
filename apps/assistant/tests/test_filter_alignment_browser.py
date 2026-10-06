"""Browser geometry checks for report filter bars in assistant layouts."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parents[3]
ASSETS = {
    "/static/base.css": (ROOT / "trellum/static/css/base.css").read_text(),
    "/static/filter_bar.css": (
        ROOT / "trellum/static/css/components/filter_bar.css"
    ).read_text(),
    "/static/assistant.css": (ROOT / "static/assistant.css").read_text(),
    "/static/report_menu.js": (ROOT / "static/report_menu.js").read_text(),
}
REPORT_HTML = """<!doctype html>
<html><head>
  <link rel="stylesheet" href="/static/base.css">
  <link rel="stylesheet" href="/static/filter_bar.css">
  <link rel="stylesheet" href="/static/assistant.css">
</head><body>
  <div class="fw-container">
    <div class="fw-filter-bar">
      <div class="fw-filter-item"><label class="fw-filter-label">Date</label>
        <select><option>30 days</option></select></div>
      <div class="fw-filter-item"><label class="fw-filter-label">Platform</label>
        <select><option>All</option></select></div>
    </div>
    <div class="fw-section"><h2>Player Overview</h2><div style="height:900px"></div></div>
  </div>
  <script src="/static/report_menu.js"></script>
</body></html>"""


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def browser(request):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed")

    with sync_playwright() as playwright:
        try:
            instance = getattr(playwright, request.param).launch(headless=True)
        except Error as exc:  # pragma: no cover - depends on local browsers
            pytest.skip(f"{request.param} is unavailable: {exc}")
        yield instance
        instance.close()


def _serve_report(page):
    def respond(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/api/report-shell"):
            route.fulfill(status=503, content_type="text/plain", body="unavailable")
        elif path in ASSETS:
            content_type = (
                "application/javascript" if path.endswith(".js") else "text/css"
            )
            route.fulfill(content_type=content_type, body=ASSETS[path])
        else:
            route.fulfill(content_type="text/html", body=REPORT_HTML)

    page.route("**/*", respond)


def _measure(page):
    bar = page.locator(".fw-filter-bar").bounding_box()
    item = page.locator(".fw-filter-item").first.bounding_box()
    heading = page.locator(".fw-section h2").bounding_box()
    return {
        "bar_left": bar["x"],
        "bar_right": bar["x"] + bar["width"],
        "item_left": item["x"],
        "heading_left": heading["x"],
        "overflow": page.evaluate(
            "document.documentElement.scrollWidth-document.documentElement.clientWidth"
        ),
    }


def test_report_filter_bar_alignment_across_assistant_layouts(browser):
    page = browser.new_page()
    _serve_report(page)
    try:
        for width in (390, 768, 1727, 2391):
            page.set_viewport_size({"width": width, "height": 820})
            for mode in ("console", "focus"):
                page.goto(
                    "http://reports.test/s/demo/casino/r/player-overview/"
                    f"index.html?display={mode}",
                    wait_until="load",
                )
                if mode == "console":
                    page.locator("body:not(.tl-report-console-pending)").wait_for()
                assert page.locator("#tlReportDisplayStyle").count() == 1
                assert page.locator("body").evaluate(
                    "(body, expected) => body.classList.contains(expected)",
                    f"tl-report-{mode}",
                )
                measured = _measure(page)
                assert abs(measured["item_left"] - measured["heading_left"]) <= 1
                assert measured["bar_left"] >= 0
                assert measured["bar_right"] <= width
                assert measured["overflow"] == 0

        page.set_viewport_size({"width": 1280, "height": 820})
        page.goto(
            "http://reports.test/s/demo/casino/r/player-overview/index.html",
            wait_until="load",
        )
        assert page.locator("#tlReportDisplayStyle").count() == 0
        for assistant_width in (0, 420):
            page.locator("body").evaluate(
                "(body, width) => { body.style.paddingRight = width + 'px'; "
                "body.style.setProperty('--assistant-w', width + 'px'); }",
                assistant_width,
            )
            measured = _measure(page)
            assert abs(measured["bar_left"]) <= 1
            assert abs(measured["bar_right"] - (1280 - assistant_width)) <= 1
            assert measured["overflow"] == 0
    finally:
        page.close()
