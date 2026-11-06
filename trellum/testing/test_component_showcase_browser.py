"""The combined demo must render each module and keep its filters independent."""

import csv
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.runner.discovery import discover_report


def test_component_showcase_sections_and_independent_filters(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    source = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase"
    report = discover_report(str(source))
    ctx = ReportContext(config=report.load_config(str(source)),
                        slug="component-showcase", output_dir=str(tmp_path))
    report().generate(ctx)
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
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
            page.get_by_role("link", name="report controls", exact=True).click()
            tabs = page.locator('[data-tab-id="showcase_controls_tabs"]')
            tabs.get_by_role("tab", name="Revenue", exact=True).click()
            tabs.get_by_role("tab", name="Orders across all supported platforms — daily supplied totals").click()
            assert page.get_by_text("Orders by platform", exact=True).is_visible()
            page.get_by_role("link", name="data tables", exact=True).click()
            table = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="DataTable — filtered population", exact=True))
            table.locator("tbody tr").first.wait_for()
            table.locator('input[data-table-search]').fill("Reactivation")
            table.get_by_role("button", name="Android", exact=True).click()
            expect(table.locator(".fw-table-note")).to_contain_text("16 matching")
            table.get_by_role("columnheader").filter(has_text="Revenue").click()
            with page.expect_download() as download_info:
                table.get_by_role("button", name="Download CSV", exact=True).click()
            download = download_info.value
            assert download.suggested_filename == "component-showcase_table.csv"
            with open(download.path(), encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            assert len(rows) == 16
            assert all(row['Platform'] == 'Android' and row['Campaign'] == 'Reactivation'
                       for row in rows)
            expected = sorted(range(49, 80, 2), key=lambda i: -(95 + i * 31 % 140 + i / 1000))
            assert [row['Event ID'] for row in rows] == [f'{i + 1:05d}' for i in expected]
            assert table.locator('tbody tr').count() == 10
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="date ranges", exact=True).click()
            dates = page.locator('[data-fw-ds-id="showcase_dates_daily"].fw-filter-bar')
            dates.locator('.fw-date-trigger').click()
            dates.get_by_role("button", name="14D", exact=True).click()
            assert page.evaluate("window._fwFilterEngine.getFiltered('showcase_dates_daily').length") == 15
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            assert table.locator('tbody tr').count() == 10
            assert "16 matching" in table.locator('.fw-table-note').inner_text()
            page.keyboard.press("Escape")
            page.get_by_role("link", name="area charts", exact=True).click()
            area = page.locator('canvas[data-fw-title="Additive source mix · live filter"]')
            area.scroll_into_view_if_needed()
            page.locator('#sec-area-example-filters').get_by_role(
                "button", name="Android", exact=True).click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets[0].data[0] === 1296",
                                   arg=area.get_attribute("id"))
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="comparison tables", exact=True).click()
            comparison = page.locator('#sec-filterable-comparison-context')
            comparison.get_by_role("button", name="Availability", exact=True).click()
            assert comparison.locator('tbody tr').count() == 3
            assert comparison.locator('tbody tr').first.locator('td').last.inner_text() == "—\nZero baseline"
            assert comparison.locator('tbody tr').first.locator('td').nth(2).inner_text() == "$480"
            assert table.locator('tbody tr').count() == 10
            page.get_by_role("link", name="doughnuts", exact=True).click()
            doughnut = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="Doughnuts — report filters and ring visibility", exact=True))
            doughnut.get_by_role("button", name="Android", exact=True).click()
            assert doughnut.locator('.fw-doughnut-center-value').inner_text() == "$40,000.00"
            doughnut.locator('.fw-doughnut-entry').first.click()
            assert doughnut.locator('.fw-doughnut-center-value').inner_text() == "$40,000.00"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="pivot tables", exact=True).click()
            pivot = page.locator('.fw-section').filter(has=page.get_by_role(
                "heading", name="Pivot tables — revenue by title and platform", exact=True))
            pivot.get_by_role("button", name="Organic", exact=True).click()
            assert pivot.locator('tbody tr').count() == 2
            assert pivot.locator('tfoot td').last.inner_text() == "$5.7k"
            assert live.locator(".fw-kpi-value").first.inner_text() == "$18.4k"
            page.get_by_role("link", name="line styles", exact=True).click()
            density = page.locator('[data-tab-id="showcase_line_density"]')
            density.get_by_role('tab', name='Sparse: 12 daily observations', exact=True).click()
            sparse = page.locator('canvas[data-fw-title="Sparse: 12 daily observations"]')
            sparse.scroll_into_view_if_needed()
            page.wait_for_function("id => window._chartInstances[id]?.getDatasetMeta(0).data.length === 12",
                                   arg=sparse.get_attribute('id'))
            assert page.evaluate("id => window._chartInstances[id].getDatasetMeta(0).data.every(p => p.options.radius === 2)",
                                 sparse.get_attribute('id'))
            # Every chart must own a real canvas and draw after scrolling into
            # view; scope-based data swaps cannot stand in for different layouts.
            for canvas in page.locator("canvas:visible").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]",
                                       arg=canvas.get_attribute("id"))
                box = canvas.bounding_box()
                assert box["width"] > 0 and box["height"] > 0
            assert not errors, errors
        finally:
            browser.close()
