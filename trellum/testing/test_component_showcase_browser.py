"""The combined demo must render each module and keep its filters independent."""

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.runner.discovery import discover_report


def test_component_showcase_sections_and_independent_filters(tmp_path):
    from playwright.sync_api import sync_playwright

    source = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase"
    report = discover_report(str(source))
    ctx = ReportContext(config=report.load_config(str(source)),
                        slug="component-showcase", output_dir=str(tmp_path))
    report().generate(ctx)
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                                  or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing test asset")

            page.route("http://showcase.test/**", serve)
            page.goto("http://showcase.test/index.html", wait_until="networkidle")
            page.get_by_role("link", name="KPI metrics", exact=True).click()
            cards = page.locator("#sec-kpi-cards-and-compact-metrics .fw-kpi-grid")
            live = page.locator("#sec-filterable-kpi-row")
            assert cards.locator(".fw-kpi-value").first.inner_text() == "$184.2k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$42.5k"
            live.get_by_role("button", name="iOS", exact=True).click()
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"

            page.get_by_role("link", name="line charts", exact=True).click()
            page.locator("#sec-line-example-filters").get_by_role(
                "button", name="Android", exact=True).click()
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            # Every chart must own a real canvas and draw after scrolling into
            # view; scope-based data swaps cannot stand in for different layouts.
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]",
                                       arg=canvas.get_attribute("id"))
                box = canvas.bounding_box()
                assert box["width"] > 0 and box["height"] > 0
            assert not errors, errors
        finally:
            browser.close()
