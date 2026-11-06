"""Panel-width layouts, real chart context and filters in vendored Chromium builds."""

import importlib.util
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

from trellum.components import Grid, LineChart, Panel, StackedBar
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def _build(output):
    module_path = (Path(__file__).resolve().parents[1] / "demo" / "reports"
                   / "component-showcase" / "examples_layouts.py")
    spec = importlib.util.spec_from_file_location("examples_layouts", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(
        config={"name": "Report layouts", "studio": "test", "category": "Test",
                "description": "Panel-width grids and complete ratio panes."},
        slug="layouts", output_dir=str(output),
    )
    module.add_sections(ctx)
    # Use the example's real DataSource for omission and sibling boundaries too.
    frame = ctx.sections[0]["components"][0].df
    common = dict(df=frame, dataset_id="showcase_layouts_daily", x="day", y="revenue",
                  stack_by_options={"Platform": "platform", "Channel": "channel"})

    def line(title):
        return LineChart(title=title, height="compact", **common)

    ctx.add_section("Omission boundaries", [
        Grid(columns=1, show_titles=False, show_controls=False, children=[
            line("Parent hidden"),
            Panel(children=[Grid(columns=1, children=[
                line("Nested visible"),
                Grid(columns=1, show_titles=False, show_controls=False,
                     children=[line("Nested omitted")]),
            ])]),
            StackedBar(frame, x="day", y_cols=["revenue"], title="Stack hidden",
                       dataset_id="showcase_layouts_daily", stack_by="platform",
                       stack_by_options={"Platform": "platform", "Channel": "channel"},
                       normalize_toggle=True),
        ]),
        Grid(columns=1, show_controls=False, children=[line("Controls omitted")]),
        Grid(columns=1, children=[line("Sibling visible")]),
        line("Outside visible"),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


def _section(page, title):
    return page.locator(".fw-section").filter(
        has=page.get_by_role("heading", name=title, exact=True)).last


def _columns(grid):
    return grid.evaluate("el => getComputedStyle(el).gridTemplateColumns.split(' ').length")


def _pane_boxes(split):
    return split.locator(":scope > .fw-split-pane").evaluate_all(
        "els => els.map(el => {const r = el.getBoundingClientRect();"
        "return {x:r.x, y:r.y, width:r.width, bottom:r.bottom};})")


def _assert_ratio(split):
    left, right = _pane_boxes(split)
    assert abs(left["y"] - right["y"]) < 1
    assert left["width"] / right["width"] == pytest.approx(2, abs=0.002)


def _assert_stacked(split):
    left, right = _pane_boxes(split)
    assert right["y"] > left["bottom"]
    assert abs(left["x"] - right["x"]) < 1
    assert abs(left["width"] - right["width"]) < 1


def _chart(page, title):
    return page.get_by_text(title, exact=True).last


def _container_for_title(title):
    return title.evaluate_handle("el => {"
        "let node = (el.closest('.fw-chart-head') || el).nextElementSibling;"
        "while (node && !node.matches('.fw-chart-container')) node = node.nextElementSibling;"
        "return node;}")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_layouts_resize_context_filtering_and_plot_heights(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: message.type == "error" and errors.append(message.text))

            def serve(route):
                path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
                assert urlparse(route.request.url).hostname == "layouts.test"
                if path.is_file():
                    route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                                  or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing test asset")

            page.route("**/*", serve)
            page.goto("http://layouts.test/index.html", wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))

            grid = _section(page, "Grid — up to three columns, 280px minimum").locator(".fw-grid")
            split = _section(page, "SplitPane — 2:1 with complete chart context").locator(".fw-split")
            nested = _section(page, "Nested panel — columns fit the available panel").locator(".fw-grid")
            assert _columns(grid) == (3 if width == 1280 else 1)
            assert _columns(nested) == 1
            if width == 1280:
                _assert_ratio(split)
                # Same wide viewport, progressively narrower actual panels.
                for panel_width in (740, 400, 200):
                    split.evaluate("(el, w) => el.style.width = w + 'px'", panel_width)
                    _assert_stacked(split)
                    grid.evaluate("(el, w) => el.style.width = w + 'px'", panel_width)
                    assert _columns(grid) == (2 if panel_width == 740 else 1)
                    assert grid.evaluate("el => el.scrollWidth <= el.clientWidth")
                split.evaluate("el => el.style.width = '1000px'")
                _assert_ratio(split)
                threshold = split.evaluate("el => 720 + parseFloat(getComputedStyle(el).columnGap)")
                split.evaluate("(el, w) => el.style.width = w + 'px'", threshold)
                _assert_ratio(split)
                split.evaluate("(el, w) => el.style.width = w + 'px'", threshold - 1)
                _assert_stacked(split)
                grid.evaluate("el => el.style.width = '1000px'")
                assert _columns(grid) == 3
                grid.evaluate("el => el.style.width = ''")
                split.evaluate("el => el.style.width = ''")
                # A nested Grid restores two columns as its own panel expands.
                nested.evaluate("el => el.parentElement.style.width = '600px'")
                assert _columns(nested) == 2
                nested.evaluate("el => el.parentElement.style.width = ''")
                # Requested count stays a maximum even for tiny minimums.
                grid.evaluate("el => el.style.gridTemplateColumns = el.style.gridTemplateColumns.replace('280px', '40px')")
                assert _columns(grid) == 3
                grid.evaluate("el => el.style.gridTemplateColumns = el.style.gridTemplateColumns.replace('40px', '280px')")
                # Controls wrap inside a narrow panel at this wide viewport.
                grid.evaluate("el => el.style.width = '200px'")
                assert grid.locator("[data-stack-toggle]").evaluate(
                    "el => {const r = el.getBoundingClientRect(); return Array.from(el.children).every(b => {"
                    "const c = b.getBoundingClientRect(); return c.bottom <= r.bottom && c.right <= r.right;});}")
                grid.evaluate("el => el.style.width = ''")
            else:
                _assert_stacked(split)

            for pane in split.locator(":scope > .fw-split-pane").all():
                assert pane.locator(".fw-chart-title").count() == 1
                assert pane.locator("[data-stack-toggle]").is_visible()
                assert pane.locator("canvas").count() == 1
                assert pane.locator(".fw-chart-legend").count() == 1
                # Real breakdown changes rebuild only the chart in this pane.
                pane.get_by_role("button", name="Channel", exact=True).click()
                page.wait_for_function("el => el.textContent.includes('Organic')", arg=pane.locator(".fw-chart-legend").element_handle())
                pane.get_by_role("button", name="Platform", exact=True).click()
                page.wait_for_function("el => el.textContent.includes('iOS')", arg=pane.locator(".fw-chart-legend").element_handle())

            for title, visible in (("Parent hidden", False), ("Nested visible", True),
                                   ("Nested omitted", False), ("Sibling visible", True),
                                   ("Outside visible", True), ("Stack hidden", False)):
                label = _chart(page, title)
                assert label.is_visible() is visible, title
                container = _container_for_title(label)
                controls = container.evaluate("el => {"
                    "const root = el.parentElement; return Array.from(root.querySelectorAll('[data-stack-toggle]'))"
                    ".filter(c => c.parentElement === root).map(c => getComputedStyle(c).display !== 'none');}")
                assert controls == [visible], title
            normalize = _section(page, "Omission boundaries").locator("[data-normalize-toggle]")
            assert normalize.count() == 1 and not normalize.is_visible()
            omitted_controls = _container_for_title(_chart(page, "Controls omitted"))
            assert _chart(page, "Controls omitted").is_visible()
            assert omitted_controls.evaluate(
                "el => getComputedStyle(el.parentElement.querySelector('[data-stack-toggle]')).display") == "none"
            title_only = _section(page, "Optional omission — redundant chart title").locator(".fw-grid")
            assert not title_only.locator(".fw-chart-title").is_visible()
            assert title_only.locator("[data-stack-toggle]").is_visible()

            heights = (("Daily revenue · balanced 220px", 220),
                       ("Daily orders · compact 160px", 160),
                       ("Daily revenue · roomy 320px", 320),
                       ("Revenue by acquisition channel", 320 if width == 1280 else 180))
            for title, height in heights:
                assert _container_for_title(_chart(page, title)).evaluate(
                    "el => el.getBoundingClientRect().height") == height
            assert _chart(page, "Revenue by acquisition channel").is_visible()

            chart = _container_for_title(_chart(page, "Daily revenue · balanced 220px"))
            chart_id = chart.evaluate("el => el.querySelector('canvas').id")
            before = page.evaluate("id => window._chartInstances[id].data.datasets.length", chart_id)
            assert before == 2
            page.get_by_role("button", name="Android", exact=True).first.click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets.length === 1", arg=chart_id)
            assert page.evaluate("id => window._chartInstances[id].data.datasets[0].label", chart_id) == "Android"
            assert page.evaluate("id => {const c = document.getElementById(id);"
                                 "return c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(v => v > 0);}", chart_id)
            page.get_by_role("button", name="All", exact=True).first.click()
            page.wait_for_function("id => window._chartInstances[id].data.datasets.length === 2", arg=chart_id)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.locator(".fw-grid").evaluate_all(
                "els => els.every(el => el.scrollWidth <= el.clientWidth + 1)")

            shots = os.environ.get("TRELLUM_SCREENSHOT_DIR")
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(Path(shots) / f"report-layouts-{width}.png"), full_page=True)
                split.evaluate("el => el.scrollIntoView({block:'center'})")
                split.screenshot(path=str(Path(shots) / f"report-layouts-split-{width}.png"))
                grid.evaluate("el => el.scrollIntoView({block:'center'})")
                grid.screenshot(path=str(Path(shots) / f"report-layouts-grid-{width}.png"))
            assert errors == []
        finally:
            browser.close()
