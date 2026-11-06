"""PivotTable presentation preserves its values while adding optional freezing."""
from dataclasses import dataclass
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import PivotTable
from trellum.components.base import RenderContext
from trellum.report import ReportContext
from trellum.rendering.html_builder import render_report
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    html = table.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def test_keyword_only_freeze_preserves_positional_api_and_data():
    @dataclass
    class ExtendedPivot(PivotTable):
        note: str = ""

    frame = pd.DataFrame({"Group": ["A", "A", "B"], "Kind": ["x", "y", "x"],
                          "Amount": [100, 900, 300]})
    table = ExtendedPivot(frame, ["Group"], "Kind", "Amount", "sum", "Title", "number", None, False,
                          note="ok", freeze=True, freeze_height=240)
    html, cfg = _register(table)
    assert table.note == "ok" and cfg["data"][0]["Amount"] == 100
    assert 'data-freeze="true"' in html and 'data-freeze-height="240"' in html
    legacy_html, _ = _register(PivotTable(frame, ["Group"], "Kind", "Amount"))
    assert 'data-freeze="false"' in legacy_html
    with pytest.raises(ValueError, match="freeze_height"):
        _register(PivotTable(frame, ["Group"], freeze_height=0))


def test_showcase_validates_without_warnings(tmp_path):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_pivots.py"
    spec = importlib.util.spec_from_file_location("examples_pivots", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext({"name": "Pivots", "studio": "test", "category": "Components",
                         "description": "Explore synthetic revenue and sales by title, platform, region and month."},
                        "pivots", str(tmp_path))
    module.add_sections(ctx)
    render_report(ctx, auto_refresh=False)
    result = validate_report(ctx)
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()


def _build_browser_report(tmp_path, *, legacy=False):
    from trellum.components import DataSource, FilterBar
    from trellum.rendering.cdn import copy_vendor_tree

    frame = pd.DataFrame([
        {"Region": f"Region {i % 4}", "Product": f"Product {i % 3}",
         "Month": f"2026-{i % 8 + 1:02d}", "Sales": i * 10,
         "Channel": "Organic" if i % 2 else "Paid"}
        for i in range(64)
    ])
    ctx = ReportContext({"name": "Pivot tables", "studio": "test"}, "pivot-browser", str(tmp_path))
    ctx.add_section("Default pivot", [
        DataSource("pivot_review", frame),
        FilterBar("pivot_review", frame, filters=[{"column": "Channel", "type": "toggle"}]),
        PivotTable(frame, ["Region", "Product"], "Month", "Sales", dataset_id="pivot_review"),
    ])
    ctx.add_section("Optional frozen pivot", [
        PivotTable(frame, ["Region", "Product"], "Month", "Sales", static=True,
                   **({} if legacy else {"freeze": True})),
    ])
    uneven = pd.DataFrame({"Team": ["North", "North", "South"], "Sales": [100, 900, 300]})
    ctx.add_section("Average of observations", [
        PivotTable(uneven, ["Team"], values="Sales", agg="avg", static=True),
    ])
    render_report(ctx, auto_refresh=False)
    copy_vendor_tree(tmp_path)


def _serve(page, root):
    import mimetypes
    from urllib.parse import unquote, urlsplit

    def serve(route):
        target = root / unquote(urlsplit(route.request.url).path).lstrip("/")
        if target.is_file():
            route.fulfill(path=str(target), content_type=mimetypes.guess_type(target)[0]
                          or "application/octet-stream")
        else:
            route.fulfill(status=404)
    page.route("http://pivot.test/**", serve)
    page.goto("http://pivot.test/index.html", wait_until="networkidle")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_pivot_real_chromium(tmp_path, width):
    import csv
    import os
    from playwright.sync_api import sync_playwright

    _build_browser_report(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            _serve(page, tmp_path)
            default = page.locator('#sec-default-pivot .fw-pivot-scroll')
            frozen = page.locator('#sec-optional-frozen-pivot .fw-pivot-scroll')
            average = page.locator('#sec-average-of-observations .fw-pivot-scroll')
            for table in (default, frozen, average):
                table.scroll_into_view_if_needed()
                table.locator('tbody tr').first.wait_for()
            assert default.locator('tbody tr').count() == 12
            assert default.evaluate("el => getComputedStyle(el).maxHeight") == 'none'
            assert default.locator('thead th').first.evaluate("el => getComputedStyle(el).position") != 'sticky'
            assert default.locator('[colspan="8"]').inner_text() == 'MONTH'
            assert default.locator('[rowspan="2"]').count() == 3
            assert average.locator('thead tr').count() == 1
            assert average.locator('tfoot td').last.inner_text() == '433'
            assert average.locator('tbody tr').first.locator('td').last.inner_text() == '500'

            def choose(table, control, value):
                pivot_id = table.get_attribute('id')
                page.locator(f"#{pivot_id}_{control}Sel + .ss-main").click()
                page.locator('.ss-option:visible').filter(has_text=value).click()

            choose(default, 'agg', 'count')
            page.wait_for_function("el => el.querySelector('tfoot td:last-child').textContent === '64'",
                                   arg=default.element_handle())
            choose(default, 'agg', 'sum')
            page.wait_for_function("el => el.querySelector('tfoot td:last-child').textContent === '20.2k'",
                                   arg=default.element_handle())
            page.locator('#sec-default-pivot').get_by_role('button', name='Organic', exact=True).click()
            page.wait_for_function("el => el.querySelectorAll('tbody tr').length === 6",
                                   arg=default.element_handle())
            assert default.locator('[colspan="4"]').inner_text() == 'MONTH'
            with page.expect_download() as download_info:
                page.locator('#sec-default-pivot').get_by_role('button', name='Download CSV').click()
            with open(download_info.value.path(), encoding='utf-8-sig', newline='') as file:
                rows = list(csv.reader(file))
            assert rows[0] == ['Region', 'Product', '2026-02', '2026-04', '2026-06', '2026-08', 'Total']
            assert len(rows) == 7  # Six body rows, never the footer.
            assert sum(float(row[-1]) for row in rows[1:]) == 10240
            assert frozen.locator('tbody tr').count() == 12
            assert frozen.evaluate("el => getComputedStyle(el).maxHeight") == '320px'

            # Force a narrow panel even at desktop viewport width. Only the first
            # dimension stays pinned; inspect actual header cells after scrolling.
            frozen.evaluate("el => el.style.width = '240px'")
            frozen.scroll_into_view_if_needed()
            start = frozen.locator('tbody tr td').nth(1).bounding_box()['x']
            frozen.evaluate("el => {el.scrollTop=220; el.scrollLeft=160}")
            page.wait_for_function("el => el.scrollTop > 100 && el.scrollLeft > 100",
                                   arg=frozen.element_handle())
            bounds = frozen.bounding_box()
            first = frozen.locator('thead tr').first.locator('th').first.bounding_box()
            second = frozen.locator('.fw-pivot-category-row th').first.bounding_box()
            group = frozen.locator('.fw-pivot-group-header').bounding_box()
            assert abs(first['y'] - bounds['y']) < 3
            assert abs(second['y'] - group['y'] - group['height']) < 3
            assert abs(frozen.locator('tbody tr td').first.bounding_box()['x'] - bounds['x']) < 3
            assert frozen.locator('tbody tr td').nth(1).bounding_box()['x'] < start - 100
            assert frozen.locator('tbody tr td').last.evaluate("el => getComputedStyle(el).borderLeftWidth") == '1px'
            # Headers resize and theme switching cannot expose rows beneath them.
            page.locator('#fwThemeSelect').select_option('trellum light')
            assert frozen.locator('thead th').first.evaluate("el => getComputedStyle(el).backgroundColor") != 'rgba(0, 0, 0, 0)'
            frozen.evaluate("el => {el.scrollTop=0; el.scrollLeft=0; el.style.width=''}")
            choose(frozen, 'col', 'None')
            page.wait_for_function("el => el.querySelectorAll('thead tr').length === 1",
                                   arg=frozen.element_handle())
            assert frozen.evaluate("el => el._fwPivotHeaderObserver === null")
            choose(frozen, 'col', 'Month')
            page.wait_for_function("el => !!el._fwPivotHeaderObserver", arg=frozen.element_handle())
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            if os.environ.get('TRELLUM_SCREENSHOT_DIR'):
                output = Path(os.environ['TRELLUM_SCREENSHOT_DIR'])
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'pivots-{width}.png'), full_page=True)
            assert errors == []
        finally:
            browser.close()
