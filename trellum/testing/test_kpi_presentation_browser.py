"""Chromium runtime checks for filtered KPI presentation."""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from trellum.components import DataSource, FilterBar, KpiCard, KpiRow, MiniKpi
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def test_kpi_rendering_and_filtered_aggregation(tmp_path):
    from playwright.sync_api import sync_playwright

    frame = pd.DataFrame([
        {"platform": "iOS", "revenue": 1000, "players": 100},
        {"platform": "Android", "revenue": 500, "players": 50},
    ])
    ctx = ReportContext(config={"name": "KPI runtime"}, slug="kpi-runtime",
                        output_dir=str(tmp_path))
    ctx.add_section("Cards", [
        KpiCard(label="A & <b>B</b>", value=12345.6, delta="+2%"),
        KpiCard(label="Centered", value=2, alignment="center"),
        MiniKpi(label="Inline", value=3), MiniKpi(label="Stacked", value=4, stacked=True),
    ])
    ctx.add_section("", [
        DataSource("showcase_kpi_browser", frame),
        FilterBar("showcase_kpi_browser", frame, filters=[
            {"column": "platform", "label": "Platform", "type": "toggle"},
        ]),
        KpiRow([
            {"label": "Revenue", "agg": "sum", "column": "revenue", "format": "number"},
            {"label": "Players", "agg": "sum", "column": "players"},
            {"label": "Revenue / player", "agg": "ratio", "numerator": "revenue",
             "denominator": "players", "format": "currency"},
                {"label": "Percent input", "value": 12.5, "format": "percent"},
                {"label": "Ratio percent", "agg": "ratio", "numerator": "players",
                 "denominator": "revenue", "format": "percent"},
            {"label": "Fourth", "agg": "count"},
            {"label": "Fifth", "agg": "count"},
            {"label": "Sixth", "agg": "count"},
        ], dataset_id="showcase_kpi_browser", mobile_collapse=4),
    ])
    render_report(ctx, str(tmp_path), auto_refresh=False)
    copy_vendor_tree(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler,
                                                           directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="networkidle")
            page.wait_for_function("() => document.querySelectorAll('#' + CSS.escape(document.querySelector('.fw-kpi-grid').id) + ' .fw-kpi-value').length")
            cards = page.locator(".fw-kpi-card")
            assert cards.nth(0).locator(".fw-kpi-label").text_content() == "A & <b>B</b>"
            assert cards.nth(0).locator(".fw-kpi-value").text_content() == "12.3k"
            assert cards.nth(1).evaluate("el => getComputedStyle(el).textAlign") == "center"
            assert page.evaluate("() => [window._fwKpiFormat(12345.67, 'number', true), window._fwKpiFormat(4, 'number', true), window._fwKpiFormat(1.23456, 'ratio', true, 3), window._fwKpiFormat(0.0842, 'percent', true, 2)]") == ["12,345.67", "4", "1.235x", "8.42%"]
            assert page.locator(".fw-kpi-grid").nth(0).locator(".fw-kpi-extra").count() == 4
            grid = page.locator(".fw-kpi-grid").nth(0)
            assert grid.locator(".fw-kpi-value").nth(0).text_content() == "1.5k"
            assert grid.locator(".fw-kpi-value").nth(3).text_content() == "12.5%"
            assert grid.locator(".fw-kpi-value").nth(4).text_content() == "10.0%"
            # Filter down to iOS and verify the aggregate follows the active filter.
            toggle = page.locator(".fw-toggle-btn").filter(has_text="iOS")
            toggle.click()
            page.wait_for_timeout(100)
            assert grid.locator(".fw-kpi-value").nth(0).text_content() == "1.0k"
            assert grid.locator(".fw-kpi-extra").count() == 4
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
