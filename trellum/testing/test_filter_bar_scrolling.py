"""Native filter bars preserve usable phone space and desktop pinning."""

from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, DataTable, FilterBar, RawHTML
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_wrapped_bars_scroll_and_compact_bars_keep_their_offset(tmp_path, width):
    from playwright.sync_api import sync_playwright

    frame = pd.DataFrame([
        {"platform": platform, "country": "Netherlands", "campaign": "Summer campaign", "value": value}
        for platform, value in [("iOS", 10), ("Android", 20)]
    ])
    filters = [
        {"type": "toggle", "column": column, "label": column, "options": options}
        for column, options in [
            ("platform", ["iOS", "Android"]),
            ("country", ["Netherlands", "United Kingdom", "United States"]),
            ("campaign", ["Summer campaign", "Winter campaign", "Spring campaign"]),
        ]
    ]
    ctx = ReportContext(config={"name": "Filter scrolling", "studio": "test", "category": "Test"},
                        slug="scrolling", output_dir=str(tmp_path))
    ctx.add_section("", [DataSource("facts", frame), FilterBar("facts", frame, filters=filters)])
    ctx.add_section("Compact local bar", [
        FilterBar("facts", frame, filters=[filters[0]]),
        DataTable(frame, "Current rows", dataset_id="facts"),
        RawHTML('<div style="height:1600px">Scroll area</div>'),
    ])
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 844})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            target = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
            if target.is_file():
                route.fulfill(path=str(target))
            else:
                route.fulfill(status=404, body="Missing asset")

        page.route("**/*", serve)
        page.goto("http://scrolling.test/index.html", wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        bars = page.locator(".fw-filter-bar")
        main, local = bars.nth(0), bars.nth(1)
        page.wait_for_function("document.querySelectorAll('.fw-filter-bar').length === 2")
        expected = "relative" if width == 390 else "sticky"
        page.wait_for_function("position => getComputedStyle(document.querySelector('.fw-filter-bar')).position === position",
                               arg=expected)
        assert local.evaluate("el => getComputedStyle(el).position") == "sticky"
        main_height = main.bounding_box()["height"]
        if width == 390:
            assert main_height > 96
        offset = page.evaluate("parseFloat(document.documentElement.style.getPropertyValue('--fw-main-fb-height'))")
        assert offset == (0 if width == 390 else pytest.approx(main_height, abs=1))
        main.get_by_role("button", name="iOS", exact=True).click()
        page.wait_for_function("document.querySelectorAll('tbody tr').length === 1")
        page.evaluate("scrollTo(0, 600)")
        page.wait_for_function("scrollY >= 600")
        if width == 390:
            assert main.bounding_box()["y"] + main_height < 0
        else:
            assert main.bounding_box()["y"] >= 0
        assert local.bounding_box()["y"] >= 0
        assert page.locator("html").evaluate("el => el.scrollWidth <= innerWidth")

        # Re-measure on size changes without losing the current selection.
        if width == 390:
            page.set_viewport_size({"width": 1280, "height": 844})
            page.wait_for_function("getComputedStyle(document.querySelector('.fw-filter-bar')).position === 'sticky'")
            assert main.get_by_role("button", name="iOS", exact=True).get_attribute("aria-pressed") == "true"
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_function("getComputedStyle(document.querySelector('.fw-filter-bar')).position === 'relative'")

        # Removal clears the reserve; the replacement is measured independently.
        main.evaluate("el => {window.savedBar = el.cloneNode(true); el.remove();}")
        page.wait_for_function("document.documentElement.style.getPropertyValue('--fw-main-fb-height') === '0px'")
        page.evaluate("""() => {
            document.querySelector('.fw-section').before(window.savedBar);
            window._fwWatchFilterBarLayout(window.savedBar);
        }""")
        page.wait_for_function("position => getComputedStyle(window.savedBar).position === position", arg=expected)
        assert errors == []
        browser.close()
