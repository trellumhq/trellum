"""An ordinary report with live components still renders and reacts to filters.

``live_wrap.js`` and ``data_loader.js`` ship in every report. The Metrics
workspace added branches to both (a workspace-only draw queue, lazy lease
handling and a rewritten ``_renderComps``). These tests build a regular report
-- no ``_fwMetricsWorkspace`` -- and check the paths a normal report depends on:
live components draw at load, a filter redraws them, a chart below the fold is
drawn when it scrolls into view, and none of the workspace state appears.
"""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from trellum.components import (
    BarChart,
    DataSource,
    DataTable,
    FilterBar,
    KpiRow,
    LineChart,
    RawHTML,
)
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def _build(output):
    days = pd.date_range("2026-09-01", periods=10).strftime("%Y-%m-%d")
    frame = pd.DataFrame([{"day": day, "platform": platform, "value": 10 if platform == "ios" else 1}
                          for day in days for platform in ("ios", "android")])
    ctx = ReportContext(config={"name": "Ordinary", "studio": "test", "category": "Test",
                                "description": "A regular report with live components."},
                        slug="ordinary", output_dir=str(output))
    ctx.add_section("", [
        DataSource("events", frame),
        FilterBar("events", frame, filters=[{"column": "platform", "type": "toggle"}]),
    ])
    ctx.add_section("Top", [
        KpiRow(dataset_id="events", kpis=[{"label": "Total", "format": "number", "agg": "sum", "column": "value"}]),
        LineChart(frame, x="day", y="value", dataset_id="events", title="Trend"),
        DataTable(frame, title="Rows", dataset_id="events", columns=["day", "platform", "value"], max_rows=100),
    ])
    ctx.add_section("Spacer", [RawHTML(html='<div style="height:4000px"></div>')])
    ctx.add_section("Below the fold", [
        BarChart(frame, x="platform", y="value", dataset_id="events", title="By platform")])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


def test_ordinary_report_draws_live_components_filters_and_lazy_charts(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    _build(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1200, "height": 800})
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html", wait_until="networkidle")

            # None of the workspace machinery is present or leaks state.
            assert page.evaluate("""() => [window._fwMetricsWorkspace, window._fwWorkspaceState,
                window._fwMetricsCatalog, window._fwWorkspaceWork].map(String)""") == ["undefined"] * 4
            assert sorted({url.rsplit("/", 1)[1].split("?", 1)[0] for url in requests if ".json" in url}) == [
                "_meta.json", "data.json"]
            assert page.evaluate("typeof window._fwLiveWrap") == "function"

            # Priority components (KPI, table) draw at load: ten days of 10 + 1.
            expect(page.locator(".fw-kpi-value").first).to_have_text("110")
            expect(page.locator(".fw-table tbody tr")).to_have_count(20)
            # The chart in view is drawn; the one far below the fold waits for the scroll.
            page.wait_for_function("Object.keys(window._chartInstances || {}).length === 1")
            charts = page.evaluate("Object.keys(window._chartInstances)")
            page.locator("text=By platform").scroll_into_view_if_needed()
            page.wait_for_function("(before) => Object.keys(window._chartInstances).length === before + 1",
                                   arg=len(charts))

            # A filter reaches every live component through the subscription.
            page.get_by_role("button", name="android", exact=True).click()
            expect(page.locator(".fw-kpi-value").first).to_have_text("10")
            expect(page.locator(".fw-table tbody tr")).to_have_count(10)
            # The chart that was drawn lazily is subscribed too.
            assert page.evaluate("""() => Object.values(window._chartInstances).some(
                chart => JSON.stringify(chart.data.labels) === '["android"]')""")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
