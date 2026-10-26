"""Deterministic date preset -> local live query -> result coverage."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from trellum import project
from trellum.assets import load_js
from trellum.components import DataTable, FilterBar, LiveDataSource
from trellum.components.filters.date_range import DateRangeFilter
from trellum.data.datasource_config import invalidate_datasource_cache
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.runner.live_query_dev import (
    handle_auth_me_request,
    handle_live_query_request,
    maybe_serve_injected_html,
)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def date_server(tmp_path, monkeypatch):
    monkeypatch.setattr(project, "_project_root_override", str(tmp_path))
    invalidate_datasource_cache()
    (tmp_path / "data-sources").mkdir()
    (tmp_path / "data-sources/config.yaml").write_text(
        "sources:\n  A:\n    type: sqlite\n    path: events.sqlite\n  B:\n", encoding="utf-8")
    with sqlite3.connect(tmp_path / "events.sqlite") as conn:
        conn.execute("CREATE TABLE events (day TEXT, value INTEGER)")
        conn.executemany("INSERT INTO events VALUES (?, ?)", [
            (day, None if day == "2026-11-02" else 1)
            for day in pd.date_range("2026-10-01", "2026-11-02").strftime("%Y-%m-%d")
            if day != "2026-10-05"])
    output = tmp_path / "out"
    ctx = ReportContext(config={"name": "Date lookup", "description": "Date lookup fixture",
                                "data_sources": ["A"]}, slug="dates", output_dir=str(output))
    try:
        frame = ctx.declare_live_query(
            "dates", "SELECT day, value FROM events WHERE day >= :since AND day <= :until ORDER BY day",
            datasource="A", params=[{"name": name, "type": "date", "required": True}
                                     for name in ("since", "until")],
            snapshot_params={"since": "2026-10-01", "until": "2026-11-02"})
        ctx.add_section("", [LiveDataSource("dates", query="dates", df=frame),
                             FilterBar("dates", frame, filters=[
                                 {"type": "date_range", "column": "day", "min_param": "since",
                                  "max_param": "until", "label": "Date"}])])
        ctx.add_section("Results", [DataTable(frame, title="Events", dataset_id="dates")])
        render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
    finally:
        ctx.close_connections()

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if not (handle_auth_me_request(self) or maybe_serve_injected_html(self)):
                super().do_GET()

        def do_POST(self):
            if not handle_live_query_request(self):
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        invalidate_datasource_cache()


def _open(page, url):
    page.goto(url)
    page.wait_for_selector(".fw-live-bar[data-live-state='ready']")
    page.wait_for_selector(".fw-table")


def _preset(page, label):
    page.locator(".fw-date-trigger").click()
    page.get_by_role("button", name=label, exact=True).click()


def test_seven_days_live_bounds_results_custom_empty_null_and_error(browser, date_server):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1280, "height": 800}, timezone_id="America/Los_Angeles")
    try:
        _open(page, date_server)
        with page.expect_response("**/_fw/live-query") as response:
            _preset(page, "7D")
        assert response.value.request.post_data_json["params"] == {
            "since": "2026-10-26", "until": "2026-11-02"}
        assert response.value.json()["rows"][-1] == ["2026-11-02", None]
        expect(page.locator(".fw-table tbody tr")).to_have_count(8)
        expect(page.locator(".fw-date-trigger-text")).to_have_text("2026-10-26 – 2026-11-02")
        # SQL null stays null in the engine and displays as an empty table cell.
        assert page.evaluate("window._fwFilterEngine.getFiltered('dates').at(-1).value") is None
        expect(page.locator(".fw-table tbody tr").last.locator("td").last).to_have_text("")
        missing = page.request.post(date_server + "_fw/live-query", data={
            "query_id": "dates", "params": {"since": None, "until": "2026-11-02"}})
        assert missing.status == 400
        assert missing.json()["error"] == "missing required parameter: since"
        with page.expect_response("**/_fw/live-query") as response:
            page.get_by_role("button", name="14D", exact=True).click()
        assert response.value.request.post_data_json["params"]["since"] == "2026-10-19"
        expect(page.locator(".fw-table tbody tr")).to_have_count(15)
        # A custom range in a gap succeeds with zero rows, not an error.
        with page.expect_response("**/_fw/live-query") as response:
            page.locator(".fw-date-input[data-edge='min']").fill("2026-10-05")
            page.locator(".fw-date-input[data-edge='max']").fill("2026-10-05")
        assert response.value.status == 200
        assert response.value.json()["rows"] == []
        expect(page.locator(".fw-live-status")).to_be_hidden()
        page.wait_for_function("window._fwFilterEngine.getFiltered('dates').length === 0")
        # A request failure uses the existing error surface rather than 'no data'.
        page.route("**/_fw/live-query", lambda route: route.fulfill(
            status=400, content_type="application/json", body=json.dumps({"error": "Date lookup failed"})))
        page.get_by_role("button", name="7D", exact=True).click()
        expect(page.locator(".fw-live-status")).to_have_text("Date lookup failed")
        assert "None" not in page.locator(".fw-date-trigger-text").inner_text()
    finally:
        page.close()


@pytest.mark.parametrize("zone", ["UTC", pytest.param("Europe/Amsterdam", marks=pytest.mark.heavy),
                                 pytest.param("America/Los_Angeles", marks=pytest.mark.heavy)])
def test_date_presets_use_data_calendar_days_across_timezone_and_day_boundaries(browser, zone):
    context = browser.new_context(timezone_id=zone)
    page = context.new_page()
    try:
        page.clock.install(time=datetime(2026, 10, 21, 23, 59, tzinfo=timezone.utc))
        page.evaluate(load_js("components/date_range_filter.js"))
        for maximum, expected in [("2026-11-02", "2026-10-26"),
                                  ("2026-03-09", "2026-03-02"),
                                  ("2024-03-03", "2024-02-25"),
                                  ("2026-01-03", "2025-12-27")]:
            frame = pd.DataFrame({"day": ["2024-01-01", maximum]})
            config = DateRangeFilter().build_config("dates", "day", {"default_days": 7}, frame)
            assert config["default_min_date"] == expected
            assert page.evaluate("""fc => window._fwFilterTypes.date_range._computePresetMin(fc, 7, null)""",
                                 config) == expected
    finally:
        context.close()


@pytest.mark.parametrize("values", [[], [None, None]])
def test_absent_date_bounds_do_not_produce_a_nan_preset(browser, values):
    plugin = DateRangeFilter()
    frame = pd.DataFrame({"day": pd.Series(values, dtype="str")})
    config = plugin.build_config("dates", "day", {}, frame)
    html = plugin.render_html("dates", "day", "Date", {}, frame)
    page = browser.new_page()
    try:
        page.set_content(html)
        page.evaluate(load_js("components/date_range_filter.js"))
        assert page.evaluate("""fc => window._fwFilterTypes.date_range._computePresetMin(fc, 7, null)""",
                             config) is None
        assert "No date bounds available" in page.locator(".fw-date-trigger-text").inner_text()
        assert page.evaluate("""() => window._fwFilterTypes.date_range._readRange(
            document.querySelector('.fw-date-range'), true)""") == {"min": "", "max": ""}
        assert page.locator(".fw-date-preset:enabled").count() == 0
        # Live attachment enables controls before type-specific wiring;
        # wiring must preserve the unavailable preset state as well.
        assert page.evaluate("""fc => {
            var calls = [];
            document.querySelectorAll('button').forEach(b => b.disabled = false);
            window._fwFilterTypes.date_range.wireControls(document.body, {}, 'dates', {
                findFc: () => fc, setFilter: (...args) => calls.push(args),
                propagate: () => {}, urlWrite: () => {}
            });
            document.querySelector('.fw-date-preset').click();
            return calls;
        }""", config) == []
        assert page.locator(".fw-date-preset:enabled").count() == 0
    finally:
        page.close()


@pytest.mark.parametrize("change", [
    {"max_date": ""}, {"min_date": None}, {"max_date": "not-a-date"},
    {"max_date": "2026-02-30"}, {"min_date": "NaN-NaN-NaN"},
    {"max_date": "2026-10-21 25:00"}, {"days": "seven"}, {"days": "7oops"},
    {"days": -7}, {"days": None, "hours": "NaN"},
    {"days": None, "hours": "1e30"}, {"days": 9007199254740991},
])
def test_unavailable_seven_day_selection_retains_truthful_trigger_and_sends_no_params(browser, change):
    plugin = DateRangeFilter()
    frame = pd.DataFrame({"day": ["2026-10-01", "2026-10-21"]})
    config = plugin.build_config("dates", "day", {}, frame)
    config.update({key: value for key, value in change.items() if key not in {"days", "hours"}})
    config["presets"][0].update({key: value for key, value in change.items() if key in {"days", "hours"}})
    page = browser.new_page()
    try:
        page.set_content(plugin.render_html("dates", "day", "Date", {}, frame))
        page.evaluate(load_js("components/date_range_filter.js"))
        trigger = page.locator(".fw-date-trigger-text")
        before = trigger.inner_text()
        page.evaluate("""({fc, change}) => {
            const button = document.querySelector('.fw-date-preset[data-days="7"]');
            for (const key of ['days', 'hours']) {
                if (key in change) button.dataset[key] = change[key] === null ? '' : change[key];
            }
            window.dateParams = [];
            const h = {findFc: () => fc, urlLookup: () => '7D',
                setFilter: (...args) => window.dateParams.push(args),
                propagate: (...args) => window.dateParams.push(args), urlWrite: () => {}};
            // Default and URL initialization cannot forward malformed bounds.
            window._fwFilterTypes.date_range.init(document.body, fc, 'dates', h);
            window.dateParams = [];
            window._fwFilterTypes.date_range.wireControls(document.body, {}, 'dates', h);
        }""", {"fc": config, "change": change})
        button = page.get_by_role("button", name="7D", exact=True)
        assert button.is_disabled()
        # Also exercise a button enabled before the bounds became unavailable.
        button.evaluate("button => button.disabled = false")
        button.click()
        assert trigger.inner_text() == before
        assert "NaN" not in trigger.inner_text()
        assert page.evaluate("window.dateParams") == []
        assert page.locator('.fw-date-input[data-edge="min"]').input_value() == "2026-10-01"
        assert page.locator('.fw-date-input[data-edge="max"]').input_value() == "2026-10-21"
    finally:
        page.close()


def test_hourly_iso_bounds_keep_existing_calendar_semantics(browser):
    page = browser.new_page(timezone_id="America/Los_Angeles")
    try:
        page.evaluate(load_js("components/date_range_filter.js"))
        config = {"min_date": "2026-10-01T00:00:00+02:00",
                  "max_date": "2026-10-21T12:30:00+02:00", "hourly": True}
        assert page.evaluate("fc => window._fwFilterTypes.date_range._computePresetMin(fc, 7, null)", config) == "2026-10-14 00:00"
        assert page.evaluate("fc => window._fwFilterTypes.date_range._computePresetMin(fc, null, 6)", config) == "2026-10-21 06:30"
    finally:
        page.close()


@pytest.mark.parametrize("maximum", ["", None, "not-a-date", "2026-02-30"])
def test_unavailable_live_bounds_preserve_selected_date_trigger_and_send_no_params(browser, date_server, maximum):
    page = browser.new_page()
    writes = []
    try:
        _open(page, date_server)
        page.evaluate("""() => {
            const type = window._fwFilterTypes.date_range;
            const compute = type._computePresetMin;
            type._computePresetMin = function(fc, days, hours) {
                window.liveDateConfig = fc;
                return compute(fc, days, hours);
            };
        }""")
        with page.expect_response("**/_fw/live-query") as response:
            _preset(page, "7D")
        assert response.value.request.post_data_json["params"] == {
            "since": "2026-10-26", "until": "2026-11-02"}
        page.wait_for_function("!window._fwLiveQuery.getCaptureStatus().pending")
        before = page.locator(".fw-date-trigger-text").inner_text()
        assert before == "2026-10-26 – 2026-11-02"
        page.on("request", lambda request: writes.append(request.post_data_json)
                if request.url.endswith("/_fw/live-query") else None)
        page.evaluate("value => window.liveDateConfig.max_date = value", maximum)
        page.get_by_role("button", name="7D", exact=True).click()
        assert page.locator(".fw-date-trigger-text").inner_text() == before
        assert "NaN" not in page.locator(".fw-date-trigger-text").inner_text()
        assert writes == []
        assert not page.evaluate("window._fwLiveQuery.getCaptureStatus().pending")
    finally:
        page.close()


@pytest.mark.heavy
@pytest.mark.parametrize("selection", ["14D", "All"])
def test_older_live_date_response_cannot_replace_newer_selection(browser, date_server, selection):
    from playwright.sync_api import expect

    page = browser.new_page()
    pending = []
    try:
        _open(page, date_server)

        def hold(route):
            pending.append(route)
            page.evaluate("n => window._heldDateRequests = n", len(pending))

        page.route("**/_fw/live-query", hold)
        _preset(page, "7D")
        page.wait_for_function("window._heldDateRequests === 1")
        assert len(pending) == 1
        page.get_by_role("button", name=selection, exact=True).click()
        pending[0].fulfill(response=pending[0].fetch())
        if selection == "All":
            page.wait_for_function("!window._fwLiveQuery.getCaptureStatus().pending")
            assert len(pending) == 1  # returning to the snapshot is a no-op
            expect(page.locator(".fw-table tbody tr")).to_have_count(32)
            expect(page.locator(".fw-live-status")).to_be_hidden()
            assert page.evaluate("window._fwLiveQuery.getCaptureStatus().revision") == 0
            return
        page.wait_for_function("window._heldDateRequests === 2")
        # The 7D response has completed after 14D was selected. Keep the
        # snapshot until the current request completes, without applying 7D.
        expect(page.locator(".fw-table tbody tr")).to_have_count(32)
        assert page.evaluate("window._fwLiveQuery.getCaptureStatus().revision") == 0
        assert len(pending) == 2
        assert pending[1].request.post_data_json["params"]["since"] == "2026-10-19"
        pending[1].fulfill(response=pending[1].fetch())
        expect(page.locator(".fw-table tbody tr")).to_have_count(15)
    finally:
        page.close()
