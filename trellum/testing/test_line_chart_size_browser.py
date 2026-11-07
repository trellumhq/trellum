"""LineChart sizes and header summary, in a real report in Chromium.

Builds an ordinary report with the real components, renders it with the
vendored Chart.js and checks what a reader sees: the plotting-area height at
desktop and phone widths, the title and summary in a tile grid, painted
canvases, and the summary following a filter.

Set ``TRELLUM_SCREENSHOT_DIR`` to also write the page screenshots there.
"""
from __future__ import annotations

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest

from trellum.components import BarChart, DataSource, FilterBar, Grid, LineChart
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext

_SUMMARY = {"stat": "mean", "series": 0, "label": "Daily avg", "format": "number"}


def build_report(output) -> None:
    days = pd.date_range("2026-09-01", periods=30).strftime("%Y-%m-%d")
    frame = pd.DataFrame([
        {"day": day, "platform": platform, "revenue": 10 if platform == "ios" else 1,
         "users": 5 if platform == "ios" else 1}
        for day in days for platform in ("ios", "android")])
    ctx = ReportContext(
        config={"name": "Sizes", "studio": "test", "category": "Test",
                "description": "LineChart sizes and header summary."},
        slug="sizes", output_dir=str(output))
    line = {"x": "day", "y": "revenue", "dataset_id": "events"}
    ctx.add_section("", [
        DataSource("events", frame),
        FilterBar("events", frame, filters=[{"column": "platform", "type": "toggle"}]),
    ])
    ctx.add_section("Page", [
        LineChart(frame, title="Default size", header_summary=_SUMMARY, **line),
        LineChart(frame, title="Compact size", height="compact",
                  header_summary={**_SUMMARY, "label": "Compact avg"}, **line),
        LineChart(frame, title="Roomy size", height="roomy", **line),
        LineChart(frame, title="Ratio", dataset_id="events", x="day",
                  ratios=[{"numerator": "revenue", "denominator": "users", "label": "ARPU"}],
                  y_format="decimal1", header_summary={"stat": "last", "label": "Latest ARPU"}),
        BarChart(frame, x="platform", y="revenue", dataset_id="events", title="Unchanged bar"),
    ])
    ctx.add_section("Tiles", [Grid(min_width=300, children=[
        LineChart(frame, title="Tile default", header_summary={**_SUMMARY, "label": "Tile avg"}, **line),
        LineChart(frame, title="Tile compact", height="compact",
                  header_summary={**_SUMMARY, "label": "Tile compact avg"}, **line),
        LineChart(frame, title="Tile plain", **line),
        BarChart(frame, x="platform", y="revenue", dataset_id="events", title="Tile bar"),
    ])])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


_PAINT = """(id) => {
    const canvas = document.getElementById(id);
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let painted = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] !== 0) painted++;
    return {painted, width: canvas.width, height: canvas.height};
}"""

_BOX = """(title) => {
    const titles = Array.from(document.querySelectorAll('.fw-chart-title'))
        .filter(el => el.textContent === title);
    const titleEl = titles[0];
    const root = titleEl.closest('.fw-chart-head') || titleEl;
    let box = root.nextElementSibling;
    while (box && !box.classList.contains('fw-chart-container')) box = box.nextElementSibling;
    const summary = (titleEl.closest('.fw-chart-head') || document.createElement('i'))
        .querySelector('.fw-chart-summary');
    const visible = el => !!el && el.getClientRects().length > 0
        && getComputedStyle(el).visibility !== 'hidden';
    return {
        height: box.getBoundingClientRect().height,
        titleVisible: visible(titleEl),
        summaryVisible: visible(summary),
        summary: summary ? summary.textContent : null,
        canvasId: box.querySelector('canvas').id,
    };
}"""


@pytest.mark.parametrize("width", [1280, 390])
def test_line_chart_sizes_title_and_summary_in_page_and_tiles(tmp_path, width):
    from playwright.sync_api import sync_playwright

    build_report(tmp_path)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    shots = os.environ.get("TRELLUM_SCREENSHOT_DIR")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            # Charts below the fold draw when scrolled to; visit each in turn.
            for canvas_id in page.evaluate(
                    "Array.from(document.querySelectorAll('.fw-chart-container canvas')).map(c => c.id)"):
                page.evaluate("(id) => document.getElementById(id).scrollIntoView()", canvas_id)
                page.wait_for_function("(id) => !!window._chartInstances[id]", arg=canvas_id)
            assert page.evaluate("Object.keys(window._chartInstances).length") == 9

            def box(title):
                return page.evaluate(_BOX, title)

            default, compact, roomy = box("Default size"), box("Compact size"), box("Roomy size")
            ratio = box("Ratio")
            assert (default["height"], compact["height"], roomy["height"]) == (220, 160, 320)
            # The bar chart keeps the width-keyed size it always had.
            assert box("Unchanged bar")["height"] == (320 if width == 1280 else 180)

            for chart in (default, compact, roomy, ratio):
                assert chart["titleVisible"]
                paint = page.evaluate(_PAINT, chart["canvasId"])
                assert paint["painted"] > 500, paint
            assert default["summaryVisible"] and default["summary"] == "Daily avg11"
            assert compact["summary"] == "Compact avg11"
            # 11 revenue / 6 users per day, in the chart's own one-decimal format.
            assert ratio["summary"] == "Latest ARPU1.8"

            # Grids retain context and every chart family's own plotting height.
            tile, tile_compact = box("Tile default"), box("Tile compact")
            plain, bar_title = box("Tile plain"), page.evaluate(
                "Array.from(document.querySelectorAll('.fw-grid-auto .fw-chart-title'))"
                ".filter(el => el.textContent === 'Tile bar').map(el => el.getClientRects().length)")
            assert (tile["height"], tile_compact["height"], plain["height"]) == (220, 160, 220)
            for chart in (tile, tile_compact, plain):
                assert chart["titleVisible"]
                assert page.evaluate(_PAINT, chart["canvasId"])["painted"] > 500
            assert tile["summaryVisible"] and tile["summary"] == "Tile avg11"
            assert tile_compact["summary"] == "Tile compact avg11"
            assert bar_title == [1]
            assert page.evaluate("""() => {
                const bar = Array.from(document.querySelectorAll('.fw-grid-auto .fw-chart-container'))
                    .find(el => !el.classList.contains('fw-line-chart'));
                return bar.getBoundingClientRect().height;
            }""") == (320 if width == 1280 else 180)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(shots) / f"line-chart-{width}.png"), full_page=True)

            # A filter changes the plotted series and the summary together.
            page.get_by_role("button", name="android", exact=True).click()
            page.wait_for_function(
                "() => document.querySelector('[data-chart-summary]').textContent.endsWith('1')"
                " && !document.querySelector('[data-chart-summary]').textContent.endsWith('11')")
            assert box("Default size")["summary"] == "Daily avg1"
            assert box("Tile compact")["summary"] == "Tile compact avg1"
            assert page.evaluate(
                "window._chartInstances[%r].data.datasets[0].data.every(v => v === 1)" % default["canvasId"])
            assert page.evaluate(_PAINT, default["canvasId"])["painted"] > 500
            assert (box("Default size")["height"], box("Compact size")["height"]) == (220, 160)
            # Removing the filter restores the totals.
            page.get_by_role("button", name="All", exact=True).click()
            page.wait_for_function(
                "() => document.querySelector('[data-chart-summary]').textContent.endsWith('11')")
            # A valid long label must wrap inside a narrow card, not widen the page.
            page.evaluate("""() => {
                const label = document.querySelector('.fw-chart-summary-label');
                label.textContent = 'LongSummaryLabel'.repeat(4).slice(0, 60);
            }""")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.evaluate("document.querySelector('.fw-chart-summary-label').textContent = 'Daily avg'")
            page.evaluate("window.scrollTo(0, 0)")
            if shots:
                page.screenshot(path=str(Path(shots) / f"line-chart-{width}-viewport.png"))
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
