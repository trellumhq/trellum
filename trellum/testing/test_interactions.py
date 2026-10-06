"""Playwright browser interaction tests.

Uses mock-generated report output and tests real user interactions:
filter changes, theme switching, chart rendering, URL sync, and JS errors.

Run with:  python3 -m pytest trellum/testing/test_interactions.py -v

Requires:  pip install playwright && python3 -m playwright install chromium
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from trellum.project import get_project_root

_PROJECT_ROOT = get_project_root()
_REPORTS_DIR = os.path.join(_PROJECT_ROOT, "reports")

# Two reports, chosen for what they exercise rather than for their names.
# Tests refer to these constants and never to a literal slug: this suite spent
# a long time silently skipping every test because it named two reports from a
# project that no longer exists, and nothing noticed.
#
# Override for a project with different reports:
#   FW_INTERACTION_REPORTS="my-filtered-report,my-custom-js-report"
_FILTERS_REPORT = "player-overview"      # FilterBar, date range, many charts
_CUSTOM_JS_REPORT = "cart-funnel"        # RawHTML + custom canvas JS, no vendor lib

_env_reports = os.environ.get("FW_INTERACTION_REPORTS", "").strip()
if _env_reports:
    _parts = [p.strip() for p in _env_reports.split(",") if p.strip()]
    if len(_parts) != 2:
        raise ValueError(
            "FW_INTERACTION_REPORTS must name exactly two reports "
            "(one with filters, one with custom JS), got: " + _env_reports
        )
    _FILTERS_REPORT, _CUSTOM_JS_REPORT = _parts

_TEST_REPORTS = [_FILTERS_REPORT, _CUSTOM_JS_REPORT]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _generate_report(slug: str, output_dir: str) -> bool:
    """Generate a report with mock data. Returns True if successful."""
    from trellum.testing.runner import test_report

    report_dir = os.path.join(_REPORTS_DIR, slug)
    if not os.path.isdir(report_dir):
        return False

    result = test_report(report_dir, output_dir=output_dir, screenshot=False, seed=42)
    return result.generation_ok


class _ReportServer:
    """Temporary HTTP server for a report output directory."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.port = _find_free_port()

        from trellum.rendering.cdn import serve_vendor_request

        class Handler(SimpleHTTPRequestHandler):
            def __init__(inner_self, *args, **kwargs):
                super().__init__(*args, directory=output_dir, **kwargs)
            def log_message(inner_self, *args):
                pass
            def do_GET(inner_self):
                # Charts do not render without the bundled vendor libraries.
                if serve_vendor_request(inner_self):
                    return
                super().do_GET()

        self.server = HTTPServer(("", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/index.html"

    def shutdown(self):
        self.server.shutdown()


@pytest.fixture(scope="module")
def _playwright():
    """Shared Playwright instance for all interaction tests."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


def _render_everything(page) -> None:
    """Scroll the whole page once so deferred components actually render.

    Charts outside the first viewport are handed to an IntersectionObserver
    with a 200px rootMargin (``rendering/html_builder.py``) and stay unrendered
    until they are scrolled near. A report whose opening section is tall --
    a full-height custom canvas, say -- therefore has *no* chart instances at
    load, and an assertion about ``window._chartInstances`` then describes a
    page no reader ever sees.

    In viewport-sized steps rather than one jump to the bottom: the observer
    only fires for what passes through the viewport, so a single leap skips
    everything in the middle of a long report.
    """
    height = page.evaluate("document.body.scrollHeight")
    for pos in range(0, int(height) + 700, 700):
        page.evaluate(f"window.scrollTo(0, {pos})")
        page.wait_for_timeout(120)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)


@pytest.fixture(scope="module")
def report_fixtures(_playwright):
    """Generate mock reports and serve them. Returns dict of slug -> (page, server, errors)."""
    fixtures = {}
    for slug in _TEST_REPORTS:
        report_dir = os.path.join(_REPORTS_DIR, slug)
        # Fail rather than skip. A configured report that isn't there means
        # this file's configuration has rotted, and skipping is how that goes
        # unnoticed for months -- which is exactly what happened.
        if not os.path.isdir(report_dir):
            pytest.fail(
                f"Report {slug!r} not found under {_REPORTS_DIR}. This suite "
                f"names the reports it drives; point it at yours with "
                f"FW_INTERACTION_REPORTS='<filtered-report>,<custom-js-report>'."
            )

        output_dir = tempfile.mkdtemp(prefix=f"fw_interact_{slug}_")
        if not _generate_report(slug, output_dir):
            pytest.fail(f"Could not generate report {slug!r} for interaction tests")

        server = _ReportServer(output_dir)
        page = _playwright.new_page(viewport={"width": 1400, "height": 900})
        js_errors = []
        page.on("pageerror", lambda e, errs=js_errors: errs.append(str(e)))

        page.goto(server.url, wait_until="networkidle")
        page.wait_for_timeout(2000)
        _render_everything(page)

        fixtures[slug] = {"page": page, "server": server, "errors": js_errors}

    yield fixtures

    for f in fixtures.values():
        f["page"].close()
        f["server"].shutdown()


def _get_fixture(report_fixtures, slug):
    # The fixture fails loudly on a missing report, so by the time we get here
    # every configured slug is present. A KeyError would be a bug in this file.
    return report_fixtures[slug]


# ── No JS errors on initial load ─────────────────────────────

class TestInitialLoad:
    def test_filters_report_no_js_errors(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        assert len(f["errors"]) == 0, f"JS errors on load: {f['errors']}"

    def test_custom_js_report_no_js_errors(self, report_fixtures):
        f = _get_fixture(report_fixtures, _CUSTOM_JS_REPORT)
        assert len(f["errors"]) == 0, f"JS errors on load: {f['errors']}"

    def test_filters_report_charts_rendered(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        chart_count = f["page"].evaluate("Object.keys(window._chartInstances || {}).length")
        assert chart_count > 0, "No chart instances found"

    def test_custom_js_report_charts_rendered(self, report_fixtures):
        f = _get_fixture(report_fixtures, _CUSTOM_JS_REPORT)
        chart_count = f["page"].evaluate("Object.keys(window._chartInstances || {}).length")
        assert chart_count > 0, "No chart instances found"

    def test_data_json_loaded(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        has_data = f["page"].evaluate("window._reportData !== null && window._reportData !== undefined")
        assert has_data, "_reportData not loaded"

    def test_fw_namespace_available(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        result = f["page"].evaluate("typeof window.fw")
        assert result == "object"


# ── Theme switching ──────────────────────────────────────────

class TestThemeSwitching:
    def test_theme_switch_updates_attribute(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]

        page.evaluate("switchTheme('dark')")
        page.wait_for_timeout(500)
        theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert theme == "dark"

        page.evaluate("switchTheme('light')")
        page.wait_for_timeout(500)
        theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert theme == "light"

    def test_theme_switch_no_js_errors(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        f["errors"].clear()

        for theme in ["dark", "money", "blossom", "light"]:
            page.evaluate(f"switchTheme('{theme}')")
            page.wait_for_timeout(300)

        assert len(f["errors"]) == 0, f"JS errors during theme switching: {f['errors']}"

    def test_theme_switch_redraws_charts(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]

        charts_before = page.evaluate("Object.keys(window._chartInstances || {}).length")
        page.evaluate("switchTheme('dark')")
        page.wait_for_timeout(500)
        charts_after = page.evaluate("Object.keys(window._chartInstances || {}).length")

        assert charts_after >= charts_before, "Charts disappeared after theme switch"
        page.evaluate("switchTheme('light')")

    def test_visible_chart_theme_fonts_remain_raw_options(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        f["errors"].clear()

        chart_ids = page.evaluate("Object.keys(window._chartInstances || {})")
        try:
            for chart_id in chart_ids:
                page.evaluate(
                    "id => window._chartInstances[id].canvas.scrollIntoView({block:'center'})",
                    chart_id,
                )
                page.wait_for_timeout(150)
                page.evaluate("switchTheme('dark')")
                page.wait_for_timeout(150)
                font_tag = page.evaluate(
                    """id => {
                        var chart = window._chartInstances[id];
                        var legend = chart.config.options.plugins
                            && chart.config.options.plugins.legend;
                        var font = legend && legend.labels && legend.labels.font;
                        return font ? Object.prototype.toString.call(font) : null;
                    }""",
                    chart_id,
                )
                assert font_tag in (None, "[object Object]"), (chart_id, font_tag)
                assert not f["errors"], (chart_id, f["errors"])
        finally:
            page.evaluate("window.scrollTo(0, 0); switchTheme('light')")

    def test_custom_js_theme_switch(self, report_fixtures):
        f = _get_fixture(report_fixtures, _CUSTOM_JS_REPORT)
        page = f["page"]
        f["errors"].clear()

        page.evaluate("switchTheme('dark')")
        page.wait_for_timeout(500)
        page.evaluate("switchTheme('light')")
        page.wait_for_timeout(500)

        assert len(f["errors"]) == 0, f"JS errors in custom JS theme switch: {f['errors']}"


# ── Filter interactions ──────────────────────────────────────

class TestFilters:
    def test_filter_engine_initialized(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        result = f["page"].evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            return fe.getDatasetIds().length;
        })()
        """)
        assert result > 0, "No datasets initialized in filter engine"

    def test_filter_engine_has_rows(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        result = f["page"].evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            return dsId ? fe.getDatasetRowCount(dsId) : 0;
        })()
        """)
        assert result > 0, "Dataset has no rows"

    def test_date_range_filter_reduces_rows(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]

        total_rows = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            return dsId ? fe.getDatasetRowCount(dsId) : 0;
        })()
        """)

        # Apply a narrow date range filter using the public API
        filtered_count = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return 0;
            var allRows = fe.getFiltered(dsId);
            if (!allRows.length) return 0;
            var dateCol = null;
            var sampleRow = allRows[0];
            for (var k in sampleRow) {
                if (k.indexOf('date') !== -1) { dateCol = k; break; }
            }
            if (!dateCol) return allRows.length;

            var dates = allRows.map(r => String(r[dateCol])).sort();
            var mid = Math.floor(dates.length / 2);
            var minDate = dates[mid];
            var maxDate = dates[Math.min(mid + 5, dates.length - 1)];

            fe.setFilter(dsId, 'test_date', dateCol, 'range',
                {min: minDate, max: maxDate});
            var filtered = fe.getFiltered(dsId);

            fe.clearFilter(dsId, 'test_date');

            return filtered.length;
        })()
        """)

        assert filtered_count <= total_rows, "Filtered rows should not exceed total"
        if total_rows > 6:
            assert filtered_count < total_rows, "Date range filter did not reduce row count"

    def test_filter_no_js_errors(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        f["errors"].clear()

        page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return;
            var rows = fe.getFiltered(dsId);
            if (!rows.length) return;
            var dateCol = Object.keys(rows[0]).find(k => k.indexOf('date') !== -1);
            if (!dateCol) return;
            var dates = rows.map(r => String(r[dateCol])).sort();
            fe.setFilter(dsId, 'test', dateCol, 'range',
                {min: dates[0], max: dates[Math.min(5, dates.length - 1)]});
            fe.setFilter(dsId, 'test', dateCol, 'range',
                {min: dates[0], max: dates[dates.length - 1]});
            fe.clearFilter(dsId, 'test');
        })()
        """)
        page.wait_for_timeout(500)

        assert len(f["errors"]) == 0, f"JS errors during filtering: {f['errors']}"


# ── Chart instance integrity ────────────────────────────────

class TestChartIntegrity:
    def test_all_charts_have_canvas(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        result = f["page"].evaluate("""
        (() => {
            var instances = window._chartInstances || {};
            var bad = [];
            for (var id in instances) {
                var chart = instances[id];
                if (!chart || !chart.canvas) bad.push(id);
            }
            return bad;
        })()
        """)
        assert len(result) == 0, f"Charts without canvas: {result}"

    def test_all_charts_have_data(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        result = f["page"].evaluate("""
        (() => {
            var instances = window._chartInstances || {};
            var empty = [];
            for (var id in instances) {
                var chart = instances[id];
                if (!chart || !chart.data || !chart.data.datasets || chart.data.datasets.length === 0) {
                    empty.push(id);
                }
            }
            return empty;
        })()
        """)
        assert len(result) == 0, f"Charts with no datasets: {result}"

    def test_custom_chart_instances(self, report_fixtures):
        f = _get_fixture(report_fixtures, _CUSTOM_JS_REPORT)
        chart_count = f["page"].evaluate("Object.keys(window._chartInstances || {}).length")
        assert chart_count > 0, "Custom JS report has no chart instances"


# ── Header and UI elements ──────────────────────────────────

class TestUIElements:
    def test_header_exists(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        header = f["page"].query_selector(".fw-header")
        assert header is not None, "Missing .fw-header element"

    def test_theme_dropdown_exists(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        dropdown = f["page"].query_selector("#fwThemeSelect")
        assert dropdown is not None, "Missing theme dropdown"

    def test_filter_bar_visible(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        filterbar = f["page"].query_selector("[data-fw-filter-bar]")
        assert filterbar is not None, "Missing FilterBar element"

    def test_mobile_filter_bar_does_not_widen_page(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        page.set_viewport_size({"width": 375, "height": 812})
        try:
            metrics = page.evaluate("""
            (() => {
                var bar = document.querySelector('[data-fw-filter-bar]');
                var rect = bar.getBoundingClientRect();
                return {
                    client: document.documentElement.clientWidth,
                    scroll: document.documentElement.scrollWidth,
                    left: rect.left,
                    right: rect.right
                };
            })()
            """)
            assert metrics["left"] >= -0.5
            assert metrics["right"] <= metrics["client"] + 0.5
        finally:
            page.set_viewport_size({"width": 1400, "height": 900})


# ── Page stability after interactions ────────────────────────

class TestStability:
    def test_rapid_theme_switching(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        f["errors"].clear()

        page.evaluate("""
        (() => {
            var themes = ['light', 'dark', 'money', 'blossom', 'light', 'dark', 'light'];
            themes.forEach(t => switchTheme(t));
        })()
        """)
        page.wait_for_timeout(1000)

        charts_ok = page.evaluate("Object.keys(window._chartInstances || {}).length > 0")
        assert charts_ok, "Charts gone after rapid theme switching"
        assert len(f["errors"]) == 0, f"JS errors after rapid switching: {f['errors']}"

    def test_multiple_filter_operations(self, report_fixtures):
        f = _get_fixture(report_fixtures, _FILTERS_REPORT)
        page = f["page"]
        f["errors"].clear()

        page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return;
            var rows = fe.getFiltered(dsId);
            if (!rows.length) return;
            var dateCol = Object.keys(rows[0]).find(k => k.indexOf('date') !== -1);
            if (!dateCol) return;
            var dates = rows.map(r => String(r[dateCol])).sort();
            for (var i = 0; i < 10; i++) {
                var end = Math.min(i + 5, dates.length - 1);
                fe.setFilter(dsId, 'stress', dateCol, 'range',
                    {min: dates[0], max: dates[end]});
            }
            fe.clearFilter(dsId, 'stress');
        })()
        """)
        page.wait_for_timeout(500)

        assert len(f["errors"]) == 0, f"JS errors after filter stress: {f['errors']}"


# ── Slider filter (store-health, discount_pct) ────────────────
#
# Kept as its own fixture/report rather than folded into _TEST_REPORTS: the
# slider only exists on store-health today, and every test above iterates
# _FILTERS_REPORT / _CUSTOM_JS_REPORT -- adding a third report to that list
# would make unrelated tests pay to generate a report they never touch.

_SLIDER_REPORT = "store-health"


@pytest.fixture(scope="module")
def slider_report_fixture(_playwright):
    report_dir = os.path.join(_REPORTS_DIR, _SLIDER_REPORT)
    if not os.path.isdir(report_dir):
        pytest.fail(f"Report {_SLIDER_REPORT!r} not found under {_REPORTS_DIR}.")

    output_dir = tempfile.mkdtemp(prefix=f"fw_interact_{_SLIDER_REPORT}_")
    if not _generate_report(_SLIDER_REPORT, output_dir):
        pytest.fail(f"Could not generate report {_SLIDER_REPORT!r} for interaction tests")

    server = _ReportServer(output_dir)
    page = _playwright.new_page(viewport={"width": 1400, "height": 900})
    js_errors = []
    page.on("pageerror", lambda e, errs=js_errors: errs.append(str(e)))

    page.goto(server.url, wait_until="networkidle")
    page.wait_for_timeout(2000)
    _render_everything(page)

    yield {"page": page, "server": server, "errors": js_errors}

    page.close()
    server.shutdown()


class TestSliderFilter:
    def test_slider_present(self, slider_report_fixture):
        page = slider_report_fixture["page"]
        slider = page.query_selector(".fw-slider")
        assert slider is not None, "Missing .fw-slider element"

    def _row_count(self, page):
        return page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            return dsId ? fe.getFiltered(dsId).length : 0;
        })()
        """)

    def test_slider_keyboard_drag_reduces_rows(self, slider_report_fixture):
        # Real keyboard interaction (not a programmatic engine call) so this
        # exercises the actual noUiSlider wiring end to end: focus the upper
        # handle and step it down with the keyboard, which noUiSlider treats
        # as a genuine interaction -- start/slide/end/change all fire, and
        # slider_filter.js commits the narrower range on 'change'.
        page = slider_report_fixture["page"]

        before = self._row_count(page)
        assert before > 0, "Dataset has no rows to narrow"

        handle = page.query_selector('.fw-slider [data-handle="1"]')
        assert handle is not None, "Missing upper slider handle"
        handle.focus()
        for _ in range(40):
            page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(300)

        after = self._row_count(page)
        assert after < before, f"Slider drag did not reduce rows ({before} -> {after})"

    def test_slider_no_js_errors(self, slider_report_fixture):
        f = slider_report_fixture
        page = f["page"]
        f["errors"].clear()

        handle = page.query_selector('.fw-slider [data-handle="0"]')
        if handle is not None:
            handle.focus()
            for _ in range(10):
                page.keyboard.press("ArrowRight")
            page.keyboard.press("Home")  # snap back to the slider minimum
        page.wait_for_timeout(300)

        assert len(f["errors"]) == 0, f"JS errors during slider interaction: {f['errors']}"


# ── Ordinal slider filter (monetization, spender_tier) ─────────
#
# Kept as its own fixture/report, same reasoning as the numeric slider
# fixture above: the ordinal slider lives on one report ("The Journey"
# section of monetization) and nothing else in this suite touches it.

_ORDINAL_SLIDER_REPORT = "monetization"
_ORDINAL_SLIDER_SELECTOR = ".fw-filter-slider-item--ordinal .fw-slider"


@pytest.fixture(scope="module")
def ordinal_slider_report_fixture(_playwright):
    report_dir = os.path.join(_REPORTS_DIR, _ORDINAL_SLIDER_REPORT)
    if not os.path.isdir(report_dir):
        pytest.fail(f"Report {_ORDINAL_SLIDER_REPORT!r} not found under {_REPORTS_DIR}.")

    output_dir = tempfile.mkdtemp(prefix=f"fw_interact_{_ORDINAL_SLIDER_REPORT}_")
    if not _generate_report(_ORDINAL_SLIDER_REPORT, output_dir):
        pytest.fail(f"Could not generate report {_ORDINAL_SLIDER_REPORT!r} for interaction tests")

    server = _ReportServer(output_dir)
    page = _playwright.new_page(viewport={"width": 1400, "height": 900})
    js_errors = []
    page.on("pageerror", lambda e, errs=js_errors: errs.append(str(e)))

    page.goto(server.url, wait_until="networkidle")
    page.wait_for_timeout(2000)
    _render_everything(page)

    yield {"page": page, "server": server, "errors": js_errors}

    page.close()
    server.shutdown()


class TestOrdinalSliderFilter:
    def test_ordinal_slider_present(self, ordinal_slider_report_fixture):
        page = ordinal_slider_report_fixture["page"]
        slider = page.query_selector(_ORDINAL_SLIDER_SELECTOR)
        assert slider is not None, "Missing ordinal .fw-slider element"

    def _journey_row_count(self, page):
        return page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            return fe.isReady('journey') ? fe.getFiltered('journey').length : 0;
        })()
        """)

    def test_ordinal_slider_keyboard_drag_reduces_rows_via_in_filter(self, ordinal_slider_report_fixture):
        # Real keyboard interaction (not a programmatic engine call), same
        # pattern as the numeric slider test: focus the upper handle and
        # step it down, which noUiSlider treats as a genuine interaction --
        # start/slide/end/change all fire, and slider_filter.js commits the
        # narrower contiguous span through the *existing* 'in' engine mode,
        # exactly like a dropdown multi-select would -- no new engine mode.
        page = ordinal_slider_report_fixture["page"]

        before = self._journey_row_count(page)
        assert before > 0, "Journey dataset has no rows to narrow"

        handle = page.query_selector(_ORDINAL_SLIDER_SELECTOR + ' [data-handle="1"]')
        assert handle is not None, "Missing upper ordinal slider handle"
        handle.focus()
        # Only 4 tiers (positions 0..3) -- 3 presses walks the upper handle
        # from 'whale' (3) down to 'non_spender' (0), collapsing the span to
        # a single tier regardless of the exact per-press step size.
        for _ in range(3):
            page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(300)

        after = self._journey_row_count(page)
        assert after < before, f"Ordinal slider drag did not reduce rows ({before} -> {after})"

    def test_ordinal_slider_commits_in_mode_with_contiguous_values(self, ordinal_slider_report_fixture):
        page = ordinal_slider_report_fixture["page"]

        state = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var st = fe.getFilterState('journey');
            for (var fid in st) {
                if (st[fid].column === 'spender_tier') return st[fid];
            }
            return null;
        })()
        """)
        assert state is not None, "No spender_tier filter state found on 'journey'"
        assert state["mode"] == "in", f"Expected ordinal range to commit 'in', got {state['mode']!r}"
        assert isinstance(state["value"], list) and len(state["value"]) >= 1
        tiers = ["non_spender", "minnow", "dolphin", "whale"]
        assert all(v in tiers for v in state["value"]), state["value"]

    def test_ordinal_slider_no_js_errors(self, ordinal_slider_report_fixture):
        f = ordinal_slider_report_fixture
        page = f["page"]
        f["errors"].clear()

        handle = page.query_selector(_ORDINAL_SLIDER_SELECTOR + ' [data-handle="0"]')
        if handle is not None:
            handle.focus()
            for _ in range(6):
                page.keyboard.press("ArrowRight")
            page.keyboard.press("Home")  # snap back to the slider minimum
        page.wait_for_timeout(300)

        assert len(f["errors"]) == 0, f"JS errors during ordinal slider interaction: {f['errors']}"
