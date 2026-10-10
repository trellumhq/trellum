"""JS runtime unit tests via Playwright.

Generates a minimal HTML page that loads Chart.js + the framework JS runtime,
then uses page.evaluate() to test each JS function in a real browser environment.

Run with:  python3 -m pytest trellum/testing/test_js_runtime.py -v

Requires:  pip install playwright && python3 -m playwright install chromium
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from dataclasses import replace
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from trellum.assets import load_css, load_js
from trellum.rendering.cdn import get_cdn_url
from trellum.rendering.html_builder import _generate_base_css
from trellum.rendering.js_runtime import generate_js_runtime
from trellum.themes import THEME_REGISTRY

# ── Fixtures ──────────────────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _build_test_page(tmpdir: str) -> str:
    """Build a minimal HTML page with Chart.js + the JS runtime."""
    js_runtime = generate_js_runtime("light", THEME_REGISTRY, refresh_seconds=0)

    chartjs_url = get_cdn_url("chartjs")
    annotation_url = get_cdn_url("chartjs_annotation")

    html = f"""<!DOCTYPE html>
<html data-theme="light">
<head><meta charset="utf-8"><title>JS Runtime Tests</title>
<script src="{chartjs_url}"></script>
<script src="{annotation_url}"></script>
</head>
<body>
<div class="fw-header"></div>
<div id="fwReportMetadata"></div>
{js_runtime}
</body></html>"""

    path = os.path.join(tmpdir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    # Also write a stub data.json
    data = {"_events": [
        {"date": "2026-04-05", "label": "Release v2", "type": "release"},
        {"date": "2026-04-07", "end_date": "2026-04-09", "label": "Easter", "type": "event"},
    ]}
    with open(os.path.join(tmpdir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)

    return path


@pytest.fixture(scope="module")
def browser_page():
    """Launch Playwright browser, serve test page, yield page object."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    tmpdir = tempfile.mkdtemp(prefix="fw_jstest_")
    _build_test_page(tmpdir)

    port = _find_free_port()

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=tmpdir, **kwargs)
        def log_message(self, *args):
            pass

    server = HTTPServer(("", port), QuietHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    page.goto(f"http://localhost:{port}/index.html", wait_until="networkidle")
    page.wait_for_timeout(1000)

    yield page

    context.close()
    browser.close()
    pw.stop()
    server.shutdown()


@pytest.fixture
def capture_page(browser_page):
    """Capture tests get a fresh document so browser globals cannot leak."""
    page = browser_page.context.new_page()
    page.goto(browser_page.url.split("?", 1)[0].split("#", 1)[0], wait_until="networkidle")
    page.evaluate("window._reportData = {components: {}, _freshness: {generated_at: null}};")
    yield page
    page.close()


@pytest.fixture
def live_query_page(browser_page):
    """Fresh runtime with live-query host flags set before runtime startup."""
    page = browser_page.context.new_page()
    page.add_init_script("window._fwHasHost = true; window._fwLiveQueryUrl = '/live-query';")
    page.goto(browser_page.url.split("?", 1)[0].split("#", 1)[0], wait_until="networkidle")
    yield page
    page.close()


# ── Formatter tests ──────────────────────────────────────────

def test_annotation_bar_follows_metadata_strip(capture_page):
    capture_page.evaluate("""() => {
        var chrome = document.createElement('div');
        chrome.className = 'fw-report-chrome';
        var header = document.querySelector('.fw-header');
        var metadata = document.getElementById('fwReportMetadata');
        header.parentNode.insertBefore(chrome, header);
        chrome.appendChild(header);
        chrome.appendChild(metadata);
        window._reportData = {_events: [
            {date: '2026-04-05', label: 'Release', type: 'release'}
        ]};
        window._buildAnnoToggleBar();
    }""")
    assert capture_page.evaluate("""() => {
        var chrome = document.querySelector('.fw-report-chrome');
        return chrome.nextElementSibling.id === 'fwAnnoBar';
    }""")


def test_integrated_chrome_is_counted_once_in_sticky_offset(capture_page):
    capture_page.evaluate("""() => {
        var chrome = document.createElement('div');
        chrome.className = 'fw-report-chrome';
        chrome.style.height = '52px';
        var header = document.querySelector('.fw-header');
        var metadata = document.getElementById('fwReportMetadata');
        metadata.style.height = '18px';
        header.parentNode.insertBefore(chrome, header);
        chrome.appendChild(header);
        chrome.appendChild(metadata);
    }""")
    capture_page.add_script_tag(content=load_js("components/report_metadata.js"))
    assert capture_page.evaluate("""() => {
        var root = getComputedStyle(document.documentElement);
        return [root.getPropertyValue('--fw-header-h').trim(),
                root.getPropertyValue('--fw-metadata-h').trim(),
                root.getPropertyValue('--fw-sticky-offset').trim()];
    }""") == ["52px", "0px", "52px"]


class TestFormatters:
    def test_fmtCompact_millions(self, browser_page):
        result = browser_page.evaluate("fmtCompact(1234567)")
        assert result == "1.2M"

    def test_fmtCompact_thousands(self, browser_page):
        result = browser_page.evaluate("fmtCompact(4567)")
        assert result == "4.6k"

    def test_fmtCompact_small(self, browser_page):
        result = browser_page.evaluate("fmtCompact(42)")
        assert result == "42"

    def test_fmtCompact_fraction(self, browser_page):
        result = browser_page.evaluate("fmtCompact(0.753)")
        assert result == "0.75"

    def test_fmtCompact_zero(self, browser_page):
        result = browser_page.evaluate("fmtCompact(0)")
        assert result == "0.00"

    def test_fmtCompact_negative(self, browser_page):
        result = browser_page.evaluate("fmtCompact(-5000)")
        assert result == "-5.0k"

    def test_fmtCompact_null(self, browser_page):
        result = browser_page.evaluate("fmtCompact(null)")
        assert result == "-"

    def test_fmtCompact_nan(self, browser_page):
        result = browser_page.evaluate("fmtCompact(NaN)")
        assert result == "-"

    def test_fmtCompact_undefined(self, browser_page):
        result = browser_page.evaluate("fmtCompact(undefined)")
        assert result == "-"

    def test_fmtCompact_billions(self, browser_page):
        result = browser_page.evaluate("fmtCompact(2500000000)")
        assert result == "2.5B"

    def test_fmtCompact_trillions(self, browser_page):
        result = browser_page.evaluate("fmtCompact(1200000000000)")
        assert result == "1.2T"

    def test_fmtCompact_dollar(self, browser_page):
        result = browser_page.evaluate("fmtCompact$(1234567)")
        assert result == "$1.2M"

    def test_fmtCompact_dollar_zero(self, browser_page):
        result = browser_page.evaluate("fmtCompact$(0)")
        assert result == "$0.00"

    def test_fmtCompact_dollar_null(self, browser_page):
        result = browser_page.evaluate("fmtCompact$(null)")
        assert result == "-"

    def test_fmtChips(self, browser_page):
        result = browser_page.evaluate("fmtChips(1234567)")
        assert result == "1.2M chips"

    def test_fmtChips_null(self, browser_page):
        result = browser_page.evaluate("fmtChips(null)")
        assert result == "-"

    def test_fmtPercent(self, browser_page):
        result = browser_page.evaluate("fmtPercent(0.053)")
        assert result == "5.30%"

    def test_fmtPercent_zero(self, browser_page):
        result = browser_page.evaluate("fmtPercent(0)")
        assert result == "0.00%"

    def test_fmtPercent_null(self, browser_page):
        result = browser_page.evaluate("fmtPercent(null)")
        assert result == "-"

    def test_fmt_dollar_exact(self, browser_page):
        result = browser_page.evaluate("fmt$(4.5)")
        assert result == "$4.50"

    def test_fmt_dollar_null(self, browser_page):
        result = browser_page.evaluate("fmt$(null)")
        assert result == "-"

    def test_getFormatter_currency(self, browser_page):
        result = browser_page.evaluate("getFormatter('currency')(1234567)")
        assert result == "$1.2M"

    def test_getFormatter_number(self, browser_page):
        result = browser_page.evaluate("getFormatter('number')(5000)")
        assert result == "5.0k"

    def test_getFormatter_percent(self, browser_page):
        result = browser_page.evaluate("getFormatter('percent')(0.5)")
        assert result == "50.00%"

    def test_getFormatter_ratio(self, browser_page):
        result = browser_page.evaluate("getFormatter('ratio')(3.14)")
        assert result == "3.14x"

    def test_getFormatter_plain(self, browser_page):
        result = browser_page.evaluate("getFormatter('plain')('hello')")
        assert result == "hello"

    def test_getFormatter_unknown_falls_back(self, browser_page):
        result = browser_page.evaluate("getFormatter('nonexistent')(1000)")
        assert result == "1.0k"


# ── fw namespace tests ───────────────────────────────────────

class TestFwNamespace:
    def test_fw_exists(self, browser_page):
        result = browser_page.evaluate("typeof window.fw")
        assert result == "object"

    def test_fw_fmtCompact(self, browser_page):
        result = browser_page.evaluate("fw.fmtCompact(5000)")
        assert result == "5.0k"

    def test_fw_getActiveTheme(self, browser_page):
        result = browser_page.evaluate("fw.getActiveTheme()")
        assert result in THEME_REGISTRY

    def test_fw_getThemeColors(self, browser_page):
        result = browser_page.evaluate("fw.getThemeColors()")
        assert "chart_colors" in result
        assert "grid_color" in result
        assert "tick_color" in result
        assert isinstance(result["chart_colors"], list)

    def test_fw_themes_registry(self, browser_page):
        result = browser_page.evaluate("Object.keys(fw.themes)")
        for theme_name in THEME_REGISTRY:
            assert theme_name in result

    def test_fw_filterEngine_exists(self, browser_page):
        result = browser_page.evaluate("typeof fw.filterEngine")
        assert result == "object"

    def test_fw_aggregate_exists(self, browser_page):
        result = browser_page.evaluate("typeof fw.aggregate")
        assert result == "object"


# ── Filter Engine tests ──────────────────────────────────────

class TestFilterEngine:
    def test_init_and_getFiltered_returns_all(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {date: '2026-04-01', platform: 'iOS', revenue: 100},
                {date: '2026-04-02', platform: 'Android', revenue: 200},
                {date: '2026-04-03', platform: 'iOS', revenue: 150},
            ];
            window._fwFilterEngine.initColumnar('test1', rows);
            return window._fwFilterEngine.getFiltered('test1').length;
        })()
        """)
        assert result == 3

    def test_setFilter_equals(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {date: '2026-04-01', platform: 'iOS', revenue: 100},
                {date: '2026-04-02', platform: 'Android', revenue: 200},
                {date: '2026-04-03', platform: 'iOS', revenue: 150},
            ];
            window._fwFilterEngine.initColumnar('test_eq', rows);
            window._fwFilterEngine.setFilter('test_eq', 'f1', 'platform', 'equals', 'iOS');
            return window._fwFilterEngine.getFiltered('test_eq').length;
        })()
        """)
        assert result == 2

    def test_setFilter_equals_all(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {platform: 'iOS', revenue: 100},
                {platform: 'Android', revenue: 200},
            ];
            window._fwFilterEngine.initColumnar('test_all', rows);
            window._fwFilterEngine.setFilter('test_all', 'f1', 'platform', 'equals', '__all__');
            return window._fwFilterEngine.getFiltered('test_all').length;
        })()
        """)
        assert result == 2

    def test_setFilter_in_mode(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {platform: 'iOS', revenue: 100},
                {platform: 'Android', revenue: 200},
                {platform: 'Web', revenue: 50},
            ];
            window._fwFilterEngine.initColumnar('test_in', rows);
            window._fwFilterEngine.setFilter('test_in', 'f1', 'platform', 'in', ['iOS', 'Web']);
            return window._fwFilterEngine.getFiltered('test_in').length;
        })()
        """)
        assert result == 2

    def test_setFilter_range_mode(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {event_date: '2026-04-01', revenue: 100},
                {event_date: '2026-04-05', revenue: 200},
                {event_date: '2026-04-10', revenue: 150},
            ];
            window._fwFilterEngine.initColumnar('test_range', rows);
            window._fwFilterEngine.setFilter('test_range', 'f1', 'event_date', 'range',
                {min: '2026-04-01', max: '2026-04-06'});
            return window._fwFilterEngine.getFiltered('test_range').length;
        })()
        """)
        assert result == 2

    def test_setFilter_numrange_mode(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {product: 'a', discount_pct: 0.0},
                {product: 'b', discount_pct: 0.10},
                {product: 'c', discount_pct: 0.15},
                {product: 'd', discount_pct: 0.25},
            ];
            window._fwFilterEngine.initColumnar('test_numrange', rows);
            window._fwFilterEngine.setFilter('test_numrange', 'f1', 'discount_pct', 'numrange',
                {min: 0.10, max: 0.20});
            return window._fwFilterEngine.getFiltered('test_numrange').length;
        })()
        """)
        assert result == 2

    def test_setFilter_numrange_mode_compares_numerically_not_lexically(self, browser_page):
        # The whole reason 'numrange' exists: 'range' string-compares, so
        # '9' > '10' lexically. A plain numeric column must not fall into
        # that trap -- 9 has to test as less than 10.
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {qty: 5}, {qty: 9}, {qty: 10}, {qty: 20},
            ];
            window._fwFilterEngine.initColumnar('test_numrange_order', rows);
            window._fwFilterEngine.setFilter('test_numrange_order', 'f1', 'qty', 'numrange',
                {min: 9, max: 10});
            return window._fwFilterEngine.getFiltered('test_numrange_order')
                .map(r => r.qty).sort((a, b) => a - b);
        })()
        """)
        assert result == [9, 10]

    def test_setFilter_numrange_mode_excludes_null_while_bound_active(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {qty: 5}, {qty: null}, {qty: 15},
            ];
            window._fwFilterEngine.initColumnar('test_numrange_null', rows);
            window._fwFilterEngine.setFilter('test_numrange_null', 'f1', 'qty', 'numrange',
                {min: 0, max: 100});
            return window._fwFilterEngine.getFiltered('test_numrange_null').length;
        })()
        """)
        assert result == 2

    def test_setFilter_numrange_mode_on_scoped_child(self, browser_page):
        # Exercises _filterRowObjects (the row-object twin of _rowMatches),
        # used by ScopedDataSource children.
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {qty: 5}, {qty: 9}, {qty: 20},
            ];
            window._fwFilterEngine.initColumnar('test_scope_parent', rows);
            window._fwFilterEngine.addScopedChild('test_scope_child', 'test_scope_parent');
            window._fwFilterEngine.setFilter('test_scope_child', 'f1', 'qty', 'numrange',
                {min: 8, max: 100});
            return window._fwFilterEngine.getFiltered('test_scope_child').length;
        })()
        """)
        assert result == 2

    def test_subscribe_fires_on_setFilter(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {platform: 'iOS', revenue: 100},
                {platform: 'Android', revenue: 200},
            ];
            window._fwFilterEngine.initColumnar('test_sub', rows);
            let callCount = 0;
            let lastLen = 0;
            window._fwFilterEngine.subscribe('test_sub', 'counter', function(filtered) {
                callCount++;
                lastLen = filtered.length;
            });
            window._fwFilterEngine.setFilter('test_sub', 'f1', 'platform', 'equals', 'iOS');
            return {callCount, lastLen};
        })()
        """)
        assert result["callCount"] == 1
        assert result["lastLen"] == 1

    def test_independent_datasets(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            window._fwFilterEngine.initColumnar('ds_a', [
                {platform: 'iOS', v: 1}, {platform: 'Android', v: 2}
            ]);
            window._fwFilterEngine.initColumnar('ds_b', [
                {platform: 'iOS', v: 10}, {platform: 'Android', v: 20}, {platform: 'Web', v: 30}
            ]);
            window._fwFilterEngine.setFilter('ds_a', 'f1', 'platform', 'equals', 'iOS');
            return {
                a: window._fwFilterEngine.getFiltered('ds_a').length,
                b: window._fwFilterEngine.getFiltered('ds_b').length,
            };
        })()
        """)
        assert result["a"] == 1
        assert result["b"] == 3

    def test_empty_dataset(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            window._fwFilterEngine.initColumnar('empty', []);
            return window._fwFilterEngine.getFiltered('empty').length;
        })()
        """)
        assert result == 0

    def test_nonexistent_dataset(self, browser_page):
        result = browser_page.evaluate("""
        window._fwFilterEngine.getFiltered('does_not_exist').length
        """)
        assert result == 0


# ── URL Filter Sync tests (slider: numeric + ordinal) ─────────
#
# decode() is a pure function shared by every slider mode; write() is
# exercised through a fake registered bar so these do not need a full
# report. Each test resets _bars first -- it is a persistent array on a
# module-scoped page, so a prior test's registration would otherwise leak
# into the next call's query string.

class TestUrlSyncSlider:
    def test_decode_slider_single_mode_returns_raw_value(self, browser_page):
        result = browser_page.evaluate("window._fwUrlSync.decode('whale', 'slider')")
        assert result == 'whale'

    def test_decode_slider_range_mode_numeric(self, browser_page):
        result = browser_page.evaluate("window._fwUrlSync.decode('10..20', 'slider')")
        assert result == {"min": "10", "max": "20"}

    def test_decode_slider_range_mode_ordinal_categories(self, browser_page):
        # decode() has no notion of ordinal vs numeric -- it just splits on
        # '..'. slider_filter.js's init() is what maps these strings back to
        # positions via fc.values when fc.ordinal is set.
        result = browser_page.evaluate("window._fwUrlSync.decode('minnow..dolphin', 'slider')")
        assert result == {"min": "minnow", "max": "dolphin"}

    def test_write_encodes_ordinal_range_as_first_dotdot_last(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            window._fwUrlSync._bars = [];
            window._fwUrlSync.register('ds1',
                [{column: 'spender_tier', type: 'slider'}],
                function() {
                    return [{column: 'spender_tier', type: 'slider',
                             value: ['minnow', 'dolphin']}];
                });
            window._fwUrlSync.write();
            return location.search;
        })()
        """)
        assert result == '?spender_tier=minnow..dolphin'

    def test_write_encodes_ordinal_single_value_plain(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            window._fwUrlSync._bars = [];
            window._fwUrlSync.register('ds1',
                [{column: 'spender_tier', type: 'slider'}],
                function() {
                    return [{column: 'spender_tier', type: 'slider', value: 'whale'}];
                });
            window._fwUrlSync.write();
            return location.search;
        })()
        """)
        assert result == '?spender_tier=whale'

    def test_write_encodes_numeric_range_as_min_dotdot_max(self, browser_page):
        # Regression guard: the array-encoding branch added for ordinal
        # ranges must not disturb the existing {min, max} numeric case.
        result = browser_page.evaluate("""
        (() => {
            window._fwUrlSync._bars = [];
            window._fwUrlSync.register('ds1',
                [{column: 'discount_pct', type: 'slider'}],
                function() {
                    return [{column: 'discount_pct', type: 'slider',
                             value: {min: 10, max: 20}}];
                });
            window._fwUrlSync.write();
            return location.search;
        })()
        """)
        assert result == '?discount_pct=10..20'

    def test_write_skips_empty_ordinal_array(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            window._fwUrlSync._bars = [];
            window._fwUrlSync.register('ds1',
                [{column: 'spender_tier', type: 'slider'}],
                function() {
                    return [{column: 'spender_tier', type: 'slider', value: []}];
                });
            window._fwUrlSync.write();
            return location.search;
        })()
        """)
        assert result == ''


# ── Aggregation tests ────────────────────────────────────────

class TestAggregation:
    def test_sum(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [{revenue: 100}, {revenue: 200}, {revenue: 300}];
            return window._fwAggregate.sum(rows, 'revenue');
        })()
        """)
        assert result == 600

    def test_sum_missing_column(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [{revenue: 100}, {other: 200}];
            return window._fwAggregate.sum(rows, 'revenue');
        })()
        """)
        assert result == 100

    def test_sumCols(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [{a: 10, b: 20}, {a: 30, b: 40}];
            return window._fwAggregate.sumCols(rows, ['a', 'b']);
        })()
        """)
        assert result == 100

    def test_absSum(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [{v: 100}, {v: -50}, {v: 30}];
            return window._fwAggregate.absSum(rows, 'v');
        })()
        """)
        assert result == 180

    def test_groupBy(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {date: '2026-04-01', revenue: 100, users: 10},
                {date: '2026-04-01', revenue: 50, users: 5},
                {date: '2026-04-02', revenue: 200, users: 20},
            ];
            return window._fwAggregate.groupBy(rows, 'date', ['revenue', 'users']);
        })()
        """)
        assert "2026-04-01" in result["labels"]
        assert "2026-04-02" in result["labels"]
        assert result["map"]["2026-04-01"]["revenue"] == 150
        assert result["map"]["2026-04-01"]["users"] == 15
        assert result["map"]["2026-04-02"]["revenue"] == 200

    def test_pivot(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {date: '2026-04-01', platform: 'iOS', revenue: 100},
                {date: '2026-04-01', platform: 'Android', revenue: 200},
                {date: '2026-04-02', platform: 'iOS', revenue: 150},
                {date: '2026-04-02', platform: 'Android', revenue: 250},
            ];
            return window._fwAggregate.pivot(rows, 'date', 'platform', ['revenue']);
        })()
        """)
        assert "2026-04-01" in result["labels"]
        assert "2026-04-02" in result["labels"]
        assert "iOS" in result["stackNames"]
        assert "Android" in result["stackNames"]
        assert len(result["datasets"]["iOS"]["revenue"]) == 2
        assert len(result["datasets"]["Android"]["revenue"]) == 2

    def test_pivot_aggregates_duplicates(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {d: 'A', s: 'X', v: 10},
                {d: 'A', s: 'X', v: 20},
            ];
            var r = window._fwAggregate.pivot(rows, 'd', 's', ['v']);
            return r.datasets['X']['v'][0];
        })()
        """)
        assert result == 30

    def test_pivot_sorts_numeric_x_axis_numerically(self, browser_page):
        # Regression: integer-valued x columns (e.g. hour_utc 0..23) used to
        # render as "0, 1, 10, 11, 12, 2, 3, ..." because xlabels.sort() did
        # a plain string sort. pivot must now detect all-numeric labels and
        # sort numerically.
        result = browser_page.evaluate("""
        (() => {
            const rows = [];
            const hours = [0, 1, 10, 11, 12, 2, 3, 9];
            const stacks = ['A', 'B'];
            hours.forEach(function(h) {
                stacks.forEach(function(s) {
                    rows.push({h: h, s: s, v: 1});
                });
            });
            return window._fwAggregate.pivot(rows, 'h', 's', ['v']);
        })()
        """)
        assert result["labels"] == ["0", "1", "2", "3", "9", "10", "11", "12"]

    def test_groupBy_sorts_numeric_x_axis_numerically(self, browser_page):
        # Same fix in the shared _sortLabels helper. groupBy already had a
        # numeric-aware sort; assert it still holds after refactor.
        result = browser_page.evaluate("""
        (() => {
            const rows = [];
            [0, 1, 10, 11, 12, 2, 3, 9].forEach(function(h) {
                rows.push({h: h, v: 1});
            });
            return window._fwAggregate.groupBy(rows, 'h', ['v']);
        })()
        """)
        assert result["labels"] == ["0", "1", "2", "3", "9", "10", "11", "12"]

    def test_pivot_falls_back_to_string_sort_for_mixed_labels(self, browser_page):
        # If any label is non-numeric, sort lexicographically. ISO dates sort
        # correctly that way; categorical labels are alphabetical.
        result = browser_page.evaluate("""
        (() => {
            const rows = [
                {x: '2026-04-02', s: 'A', v: 1},
                {x: '2026-04-01', s: 'A', v: 1},
                {x: '2026-04-10', s: 'A', v: 1},
            ];
            return window._fwAggregate.pivot(rows, 'x', 's', ['v']).labels;
        })()
        """)
        assert result == ["2026-04-01", "2026-04-02", "2026-04-10"]


# ── Theme tests ──────────────────────────────────────────────

class TestTheme:
    def test_font_tokens_resolve_css_units_in_browser(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            document.documentElement.style.fontSize = '20px';
            document.body.style.fontSize = '18px';
            var root = document.documentElement.style;
            root.setProperty('--test-px', '13px');
            root.setProperty('--test-rem', '0.75rem');
            root.setProperty('--test-em', '0.75em');
            root.setProperty('--test-alias', 'var(--test-rem)');
            root.setProperty('--test-calc', 'calc(1rem + 2px)');
            var values = {
                px: window._fwFontPx('--test-px', 9),
                rem: window._fwFontPx('--test-rem', 9),
                em: window._fwFontPx('--test-em', 9),
                alias: window._fwFontPx('--test-alias', 9),
                calc: window._fwFontPx('--test-calc', 9),
                fallback: window._fwFontPx('--test-missing', 9)
            };
            document.documentElement.removeAttribute('style');
            document.body.removeAttribute('style');
            return values;
        })()
        """)
        assert result == {
            "px": 13, "rem": 15, "em": 13.5,
            "alias": 15, "calc": 22, "fallback": 9,
        }

    def test_theme_switch_repaints_framework_fonts_but_keeps_custom_chart_options(self, browser_page):
        result = browser_page.evaluate("""
        async () => {
            var style = document.createElement('style');
            style.textContent = '[data-theme="dark"] {' +
                '--font-size-axis:18px;--font-size-label:19px}';
            document.head.appendChild(style);
            var canvas = document.createElement('canvas');
            canvas.id = 'managed-font-chart';
            document.body.appendChild(canvas);
            function fake(managed) {
                var options = {
                    scales: {x: {grid: {color: 'custom-grid'}, ticks: {
                        color: 'custom-tick', font: {size: 7}}}},
                    plugins: {legend: {labels: {color: 'custom-legend', font: {size: 8}}}}
                };
                return {
                    canvas: canvas, ctx: {}, config: {type: 'line', options: options},
                    data: {datasets: [{_themeManaged: false}]},
                    options: options,
                    _fwThemeManagedAxes: managed,
                    _fwThemeManagedFonts: managed,
                    update: function() {}
                };
            }
            var managed = fake(true), custom = fake(false);
            window._chartInstances = {managed: managed, custom: custom};
            switchTheme('dark');
            await new Promise(function(resolve) { requestAnimationFrame(function() {
                requestAnimationFrame(resolve);
            }); });
            var values = {
                managedAxis: managed.options.scales.x.ticks.font.size,
                managedLabel: managed.options.plugins.legend.labels.font.size,
                customAxis: custom.options.scales.x.ticks.font.size,
                customLabel: custom.options.plugins.legend.labels.font.size,
                customGrid: custom.options.scales.x.grid.color
            };
            delete window._chartInstances.managed;
            delete window._chartInstances.custom;
            canvas.remove(); style.remove();
            return values;
        }
        """)
        assert result == {
            "managedAxis": 18, "managedLabel": 19,
            "customAxis": 7, "customLabel": 8, "customGrid": "custom-grid",
        }

    def test_default_theme(self, browser_page):
        result = browser_page.evaluate("getActiveTheme()")
        assert result in THEME_REGISTRY

    def test_switchTheme_changes_attribute(self, browser_page):
        browser_page.evaluate("switchTheme('dark')")
        result = browser_page.evaluate("document.documentElement.getAttribute('data-theme')")
        assert result == "dark"

    def test_switchTheme_updates_getActiveTheme(self, browser_page):
        browser_page.evaluate("switchTheme('dark')")
        result = browser_page.evaluate("getActiveTheme()")
        assert result == "dark"

    def test_switchTheme_applies_generated_toggle_colors(self, browser_page):
        themes = dict(THEME_REGISTRY)
        themes["ocean"] = replace(themes["ocean"], primary_fill="#123456")
        css = _generate_base_css("light", themes) + "\n" + load_css("components/toggle.css")

        def rgb(color):
            channels = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
            return f"rgb({channels[0]}, {channels[1]}, {channels[2]})"

        expected = {
            name: {
                "background": rgb(theme.bg_header if theme.primary_fill == "var(--bg-header)"
                                   else theme.primary_fill),
                "color": rgb(theme.on_accent),
            }
            for name, theme in themes.items()
        }
        browser_page.evaluate("""css => {
            const style = document.createElement('style');
            style.id = 'theme-toggle-regression';
            style.textContent = css;
            document.head.appendChild(style);
            document.body.insertAdjacentHTML('beforeend',
                `<button id="theme-toggle" class="fw-toggle-btn active" style="transition:none">Selected</button>`);
        }""", css)

        actual = browser_page.evaluate("""expected => {
            const button = document.getElementById('theme-toggle');
            const result = {};
            Object.keys(expected).forEach(name => {
                switchTheme(name);
                const style = getComputedStyle(button);
                result[name] = {background: style.backgroundColor, color: style.color};
            });
            document.getElementById('theme-toggle-regression').remove();
            button.remove();
            return result;
        }""", expected)
        assert actual == expected

    def test_getThemeColors_changes_with_theme(self, browser_page):
        browser_page.evaluate("switchTheme('light')")
        light_colors = browser_page.evaluate("getThemeColors()")
        browser_page.evaluate("switchTheme('dark')")
        dark_colors = browser_page.evaluate("getThemeColors()")
        assert light_colors["grid_color"] != dark_colors["grid_color"] or \
               light_colors["tick_color"] != dark_colors["tick_color"]

    def test_switchTheme_fires_event(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            let received = null;
            window.addEventListener('fw-theme-change', function handler(e) {
                received = e.detail.theme;
                window.removeEventListener('fw-theme-change', handler);
            });
            switchTheme('money');
            return received;
        })()
        """)
        assert result == "money"

    def test_switchTheme_invalid_name_noop(self, browser_page):
        browser_page.evaluate("switchTheme('light')")
        browser_page.evaluate("switchTheme('nonexistent_theme_xyz')")
        result = browser_page.evaluate("getActiveTheme()")
        assert result == "light"

    def test_switchTheme_default_pair_with_space_in_name(self, browser_page):
        # The shipped defaults are the only registry names containing a
        # space. Every sink (data-theme attribute, localStorage value,
        # dropdown value) must round-trip them intact -- a space-unaware
        # consumer would pass the whole suite if only the old one-word
        # names were ever exercised.
        for name in ("trellum dark", "trellum light"):
            browser_page.evaluate(f"switchTheme('{name}')")
            attr = browser_page.evaluate(
                "document.documentElement.getAttribute('data-theme')"
            )
            assert attr == name
            assert browser_page.evaluate("getActiveTheme()") == name
            saved = browser_page.evaluate("localStorage.getItem('fw-theme')")
            assert saved == name

    def test_getThemeColors_default_pair(self, browser_page):
        browser_page.evaluate("switchTheme('trellum light')")
        light_colors = browser_page.evaluate("getThemeColors()")
        browser_page.evaluate("switchTheme('trellum dark')")
        dark_colors = browser_page.evaluate("getThemeColors()")
        assert light_colors["chart_colors"] != dark_colors["chart_colors"]

    def test_getThemeColors_structure(self, browser_page):
        browser_page.evaluate("switchTheme('light')")
        result = browser_page.evaluate("getThemeColors()")
        assert isinstance(result["chart_colors"], list)
        assert len(result["chart_colors"]) > 0
        assert isinstance(result["grid_color"], str)
        assert isinstance(result["tick_color"], str)


# ── Annotation tests ────────────────────────────────────────

class TestAnnotations:
    def test_buildAnnotations_returns_object(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            var events = [
                {date: '2026-04-05', label: 'Release v2', type: 'release'}
            ];
            var labels = ['2026-04-01', '2026-04-05', '2026-04-10'];
            var result = window._buildAnnotations(events, labels);
            return typeof result;
        })()
        """)
        assert result == "object"

    def test_buildAnnotations_creates_line_annotation(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            var events = [
                {date: '2026-04-05', label: 'Release', type: 'release'}
            ];
            var labels = ['2026-04-01', '2026-04-05', '2026-04-10'];
            var annos = window._buildAnnotations(events, labels);
            var keys = Object.keys(annos);
            if (keys.length === 0) return {found: false};
            var first = annos[keys[0]];
            return {found: true, type: first.type};
        })()
        """)
        assert result["found"] is True
        assert result["type"] == "line"

    def test_buildAnnotations_creates_box_for_range(self, browser_page):
        """A range spanning several chart labels renders as a shaded box.

        The labels must be dense enough for the range to actually span more
        than one of them. Annotations are snapped to real chart labels, so a
        range that falls between two sparse labels collapses to zero width --
        see test_buildAnnotations_collapses_subgranular_range below.
        """
        result = browser_page.evaluate("""
        (() => {
            var events = [
                {date: '2026-04-05', end_date: '2026-04-08', label: 'Event', type: 'event'}
            ];
            var labels = ['2026-04-04', '2026-04-05', '2026-04-06',
                          '2026-04-07', '2026-04-08', '2026-04-09'];
            var annos = window._buildAnnotations(events, labels);
            var keys = Object.keys(annos);
            if (keys.length === 0) return {found: false};
            var first = annos[keys[0]];
            return {found: true, type: first.type,
                    xMin: first.xMin, xMax: first.xMax};
        })()
        """)
        assert result["found"] is True
        assert result["type"] == "box"
        assert result["xMin"] == "2026-04-05"
        assert result["xMax"] == "2026-04-08"

    def test_buildAnnotations_collapses_subgranular_range(self, browser_page):
        """A range finer than the chart's own granularity degrades to a line.

        Annotations snap to real chart labels (_snapToLabel picks the nearest
        label at or before the date). On a weekly or monthly chart, a
        three-day event can snap to a single label at both ends -- and a box
        with xMin == xMax is a zero-width rectangle nobody can see. Falling
        back to a line keeps the event visible, which is the whole point of
        drawing it. This locks in that degradation as deliberate.
        """
        result = browser_page.evaluate("""
        (() => {
            var events = [
                {date: '2026-04-05', end_date: '2026-04-08', label: 'Event', type: 'event'}
            ];
            // Both 04-05 and 04-08 snap back to the 04-05 label.
            var labels = ['2026-04-01', '2026-04-05', '2026-04-10'];
            var annos = window._buildAnnotations(events, labels);
            var keys = Object.keys(annos);
            if (keys.length === 0) return {found: false};
            var first = annos[keys[0]];
            return {found: true, type: first.type,
                    xMin: first.xMin, xMax: first.xMax};
        })()
        """)
        assert result["found"] is True
        assert result["type"] == "line", (
            "a range that snaps to a single label must degrade to a line, "
            "not a zero-width invisible box"
        )
        assert result["xMin"] == result["xMax"] == "2026-04-05"

    def test_buildAnnotations_empty_events(self, browser_page):
        result = browser_page.evaluate("""
        Object.keys(window._buildAnnotations([], ['2026-04-01'])).length
        """)
        assert result == 0

    def test_buildAnnotations_non_date_labels(self, browser_page):
        result = browser_page.evaluate("""
        Object.keys(window._buildAnnotations(
            [{date: '2026-04-05', label: 'X', type: 'release'}],
            ['Platform A', 'Platform B']
        )).length
        """)
        assert result == 0

    def test_buildAnnotations_filters_out_of_range(self, browser_page):
        result = browser_page.evaluate("""
        (() => {
            var events = [
                {date: '2026-01-01', label: 'Old Event', type: 'release'}
            ];
            var labels = ['2026-04-01', '2026-04-05'];
            return Object.keys(window._buildAnnotations(events, labels)).length;
        })()
        """)
        assert result == 0


# ── localStorage guard tests (no browser needed) ──────────────

class TestLocalStorageGuards:
    """localStorage access must never be able to kill the runtime.

    Sandboxed iframes (and some privacy modes) throw on any localStorage
    touch, and one uncaught throw takes the whole script block with it.
    Every call must go through the guarded _lsGet/_lsSet helpers.
    """

    def test_runtime_localstorage_is_guarded(self):
        out = generate_js_runtime("light", THEME_REGISTRY, refresh_seconds=0)
        assert "function _lsGet(k)" in out
        assert "function _lsSet(k, v)" in out
        # The only raw localStorage touches allowed are inside the helpers.
        assert out.count("localStorage.") == 2

    def test_flash_script_survives_blocked_storage(self):
        import inspect

        from trellum.rendering import html_builder

        src = inspect.getsource(html_builder)
        assert "try{{t=localStorage.getItem('fw-theme');}}catch(e){{}}" in src

    def test_flash_script_never_stamps_a_default_theme(self):
        # With nothing valid saved the <html> tag's own data-theme must stand:
        # a host can override it at serve time (the portal does, per viewer
        # and studio), and a fallback stamp here used to clobber that.
        from trellum.rendering.html_builder import _flash_script

        script = _flash_script()
        assert "?t:'" not in script
        assert "if(v.indexOf(t)>=0)document.documentElement.setAttribute('data-theme',t);" in script

    def test_flash_script_keeps_a_served_data_theme(self, browser_page):
        from trellum.rendering.html_builder import _flash_script
        from trellum.themes import DEFAULT_THEME_NAME

        served = next(n for n in THEME_REGISTRY if n != DEFAULT_THEME_NAME)
        html = (
            f'<!DOCTYPE html><html data-theme="{served}"><head>{_flash_script()}</head>'
            "<body></body></html>"
        )
        context = browser_page.context.browser.new_context()  # empty localStorage
        page = context.new_page()
        page.route("http://flash.test/**", lambda r: r.fulfill(body=html, content_type="text/html"))
        try:
            page.goto("http://flash.test/", wait_until="domcontentloaded")
            assert page.evaluate("localStorage.getItem('fw-theme')") is None
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == served
            # A known saved name still wins, as before.
            page.evaluate("localStorage.setItem('fw-theme', 'nord')")
            page.reload(wait_until="domcontentloaded")
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == "nord"
        finally:
            context.close()


class TestKpiRowRatio:
    """A ratio KPI aggregates to numerator/denominator; the x100 is the
    PERCENT reading of it, not part of the number. Baked into the agg it made
    every currency or plain ratio wrong by 100x, which is why such metrics
    could not be claimed live at all."""

    ROWS = "[{n: 30, d: 1000}, {n: 20, d: 1000}]"   # 50 / 2000 = 0.025

    @pytest.fixture(autouse=True)
    def _kpi_renderer(self, browser_page):
        """The shared page carries the runtime only; component renderers are
        emitted per component by the builder. Load this one in."""
        from trellum.assets import load_js

        browser_page.evaluate("window._fwRenderers = window._fwRenderers || {}")
        browser_page.add_script_tag(content=load_js("components/kpi_row.js"))

    def _render(self, page, kpis: list[dict]) -> list[str]:
        """Drive the real kpi_row_live renderer over fixed rows, with
        _fwLiveWrap stubbed to hand it those rows instead of a DataSource."""
        return page.evaluate("""
        ((kpis, rows) => {
            var host = document.createElement('div');
            host.id = 'kpitest';
            kpis.forEach(function(_, i) {
                var c = document.createElement('div');
                c.id = 'kpitest_k' + i;
                host.appendChild(c);
            });
            document.body.appendChild(host);
            var prev = window._fwLiveWrap;
            window._fwLiveWrap = function(id, cfg, draw) { draw(rows); };
            try {
                window._fwRenderers['kpi_row_live']('kpitest', {kpis: kpis});
            } finally {
                window._fwLiveWrap = prev;
            }
            var out = kpis.map(function(_, i) {
                return document.getElementById('kpitest_k' + i)
                       .querySelector('.fw-kpi-value').textContent;
            });
            host.remove();
            return out;
        })(%s, %s)
        """ % (json.dumps(kpis), self.ROWS))

    def test_percent_ratio_is_scaled_to_a_percentage(self, browser_page):
        (val,) = self._render(browser_page, [
            {"label": "Share", "agg": "ratio", "numerator": "n",
             "denominator": "d", "format": "percent"},
        ])
        assert val == "2.5%"

    def test_currency_and_plain_ratios_are_not_scaled(self, browser_page):
        """0.025 is $0.03 and 0.03x -- NOT $2.50 and 2.50x."""
        cur, plain = self._render(browser_page, [
            {"label": "ARPDAU", "agg": "ratio", "numerator": "n",
             "denominator": "d", "format": "currency"},
            {"label": "Per user", "agg": "ratio", "numerator": "n",
             "denominator": "d", "format": "ratio"},
        ])
        assert cur == "$0.03" and plain == "0.03x"

    def test_a_zero_denominator_still_reads_as_zero(self, browser_page):
        (val,) = browser_page.evaluate("""
        ((kpi) => {
            var host = document.createElement('div');
            host.id = 'kpizero';
            host.innerHTML = '<div id="kpizero_k0"></div>';
            document.body.appendChild(host);
            var prev = window._fwLiveWrap;
            window._fwLiveWrap = function(id, cfg, draw) { draw([{n: 5, d: 0}]); };
            try { window._fwRenderers['kpi_row_live']('kpizero', {kpis: [kpi]}); }
            finally { window._fwLiveWrap = prev; }
            var out = [document.querySelector('#kpizero_k0 .fw-kpi-value').textContent];
            host.remove();
            return out;
        })(%s)
        """ % json.dumps({"label": "ARPDAU", "agg": "ratio", "numerator": "n",
                          "denominator": "d", "format": "currency"}))
        assert val == "$0.00"


# ── Contract / Snapshot Tests ──────────────────────────────────

class TestFwApiSnapshot:
    """Freeze the window.fw API surface so additions/removals are explicit."""

    EXPECTED_KEYS = sorted([
        "aggregate",
        "annoVisible",
        "buildAnnotations",
        "captureElementForAnalysis",
        "captureForAnalysis",
        "chartInstances",
        "currentScope",
        "data",
        "downloadCsv",
        "events",
        "exportPDF",
        "exportPNG",
        "filterEngine",
        "fmt$",
        "fmtChips",
        "fmtCompact",
        "fmtCompact$",
        "fmtPercent",
        "getActiveTheme",
        "getFormatter",
        "getThemeColors",
        "liveWrap",
        "registerChart",
        "switchTheme",
        "themes",
        "urlSync",
    ])

    def test_fw_api_keys_match_snapshot(self, browser_page):
        """Snapshot: any key added/removed from window.fw must update this test."""
        actual = browser_page.evaluate("Object.keys(window.fw).sort()")
        assert actual == self.EXPECTED_KEYS, (
            f"window.fw keys changed.\n"
            f"  Added:   {sorted(set(actual) - set(self.EXPECTED_KEYS))}\n"
            f"  Removed: {sorted(set(self.EXPECTED_KEYS) - set(actual))}\n"
            f"Update EXPECTED_KEYS in test_js_runtime.py if intentional."
        )


class TestAnalysisCapture:
    def test_section_capture_ignores_hidden_charts_and_their_filters(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<section class="fw-section" id="visible-section">' +
                '<h2>Visible</h2><canvas id="visible-chart" data-fw-kind="LineChart"></canvas>' +
                '<div style="display:none"><canvas id="hidden-chart" data-fw-kind="LineChart"></canvas></div>' +
                '</section>';
            var components = {
                'visible-chart': {type: 'chart', dataset_id: 'visible_ds'},
                'hidden-chart': {type: 'chart', dataset_id: 'hidden_ds'}
            };
            window._reportData = {defaultScope: 'main', components: components,
                _freshness: {generated_at: '2026-10-07T09:00:00Z'}};
            window._currentScope = 'main'; window._scopeCache = {main: components};
            fw.filterEngine.initColumnar('visible_ds', [{country: 'NL'}, {country: 'US'}]);
            fw.filterEngine.setFilter('visible_ds', 'country-filter', 'country', 'equals', 'NL');
            fw.filterEngine.initColumnar('hidden_ds', [{channel: 'web'}, {channel: 'store'}]);
            fw.filterEngine.setFilter('hidden_ds', 'channel-filter', 'channel', 'equals', 'web');
            var visibleUpdates = 0, hiddenUpdates = 0;
            window._chartInstances = {
                'visible-chart': {stop() {}, update() {visibleUpdates++;}},
                'hidden-chart': {stop() {}, update() {hiddenUpdates++;}}
            };
            window.html2canvas = () => Promise.resolve({toDataURL: () => 'data:image/png;base64,cGl4ZWw='});
            URL.createObjectURL = blob => { window.__download = blob; return 'blob:test'; };
            URL.revokeObjectURL = () => {};
            HTMLAnchorElement.prototype.click = function() {};
            var ok = await fw.captureElementForAnalysis(document.getElementById('visible-section'));
            return {ok, visibleUpdates, hiddenUpdates,
                payload: window.__download ? JSON.parse(await window.__download.text()) : null,
                message: document.querySelector('.fw-analysis-error span')?.textContent || ''};
        }""")
        assert result["ok"] is True, result["message"]
        assert result["visibleUpdates"] == 1
        assert result["hiddenUpdates"] == 0
        filters = result["payload"]["source"]["filters"]
        assert filters["visible_ds"]["country-filter"]["value"] == "NL"
        assert "hidden_ds" not in filters

    def test_capture_payload_filters_and_sanitized_share_url(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.setAttribute('data-report-slug', 'sales-report');
            document.body.innerHTML = '<div class="fw-header"><h1>Sales</h1></div>' +
                '<div class="fw-section" id="sec-revenue" data-fw-section-title="Revenue">' +
                '<h2>Revenue</h2>' +
                '<div class="fw-chart-title">Revenue by country</div>' +
                '<div class="fw-chart-container"><canvas id="sales-chart"></canvas>' +
                '<button class="fw-chart-dl">CSV</button></div>' +
                '<div id="table-1" data-fw-kind="DataTable"></div></div>';
            window._reportData = {components: {"sales-chart": {dataset_id: 'sales_ds'},
                "table-1": {dataset_id: 'summary_ds'}},
                _freshness: {generated_at: '2026-10-07T09:00:00Z'}};
            fw.filterEngine.initColumnar('sales_ds', [{country: 'NL'}, {country: 'US'}]);
            fw.filterEngine.setFilter('sales_ds', 'country-filter', 'country', 'equals', 'NL');
            fw.filterEngine.initColumnar('summary_ds', [{channel: 'web'}]);
            fw.filterEngine.setFilter('summary_ds', 'channel-filter', 'channel', 'equals', 'web');
            history.replaceState({}, '', '/share/secret-token?private=1#top');
            window.__download = null;
            URL.createObjectURL = blob => { window.__download = blob; return 'blob:test'; };
            URL.revokeObjectURL = () => {};
            HTMLAnchorElement.prototype.click = function() { window.__filename = this.download; };
            window.html2canvas = target => Promise.resolve({toDataURL: () =>
                'data:image/png;base64,iVBORw0KGgo='});
            var ok = await fw.captureElementForAnalysis(document.querySelector('.fw-section'));
            var payload = window.__download ? JSON.parse(await window.__download.text()) : null;
            return {ok, payload, message: document.querySelector('.fw-analysis-error span')?.textContent || '',
                filename: window.__filename,
                targetTitle: document.querySelector('.fw-chart-title').textContent,
                dlDisplay: document.querySelector('.fw-chart-dl').style.display};
        }""")
        assert result["ok"] is True, result["message"]
        payload = result["payload"]
        assert payload["format"] == "trellum-analysis-capture"
        assert payload["version"] == 1
        assert set(payload) == {"format", "version", "image", "source"}
        assert set(payload["source"]) == {"report_slug", "report_name", "url", "component_id",
            "component_title", "captured_at", "source_built_at", "filters"}
        assert payload["image"] == {"mime_type": "image/png", "data_base64": "iVBORw0KGgo="}
        assert payload["source"]["report_slug"] == "sales-report"
        assert payload["source"]["report_name"] == "Sales"
        assert payload["source"]["url"] == ""
        assert "/share/" not in payload["source"]["url"]
        assert payload["source"]["component_id"] == "sec-revenue"
        assert payload["source"]["component_title"] == "Revenue"
        assert payload["source"]["source_built_at"] == "2026-10-07T09:00:00Z"
        assert payload["source"]["filters"]["sales_ds"]["country-filter"]["value"] == "NL"
        assert payload["source"]["filters"]["summary_ds"]["channel-filter"]["value"] == "web"
        assert result["filename"] == "sales-report-sec-revenue.trellum-capture.json"
        assert result["dlDisplay"] == ""

    def test_multiscope_capture_uses_active_components_and_scoped_parent_filters(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="monthly">' +
                '<canvas id="monthly-chart"></canvas></div>';
            var defaultComponents = {"daily-chart": {dataset_id: 'daily_ds'}};
            var activeComponents = {
                "monthly-chart": {dataset_id: 'child_ds'},
                "scoped-source": {type: 'scoped_data_source', dataset_id: 'child_ds', parent_id: 'parent_ds'}
            };
            window._reportData = {defaultScope: 'daily', components: defaultComponents,
                _freshness: {generated_at: '2026-10-07T09:00:00Z'}};
            window._currentScope = 'monthly';
            window._scopeCache = {daily: defaultComponents, monthly: activeComponents};
            fw.filterEngine.initColumnar('parent_ds', [{country: 'NL'}, {country: 'US'}]);
            fw.filterEngine.setFilter('parent_ds', 'parent-country', 'country', 'equals', 'NL');
            fw.filterEngine.addScopedChild('child_ds', 'parent_ds');
            fw.filterEngine.setFilter('child_ds', 'local-channel', 'channel', 'equals', 'web');
            URL.createObjectURL = blob => { window.__download = blob; return 'blob:test'; };
            URL.revokeObjectURL = () => {};
            HTMLAnchorElement.prototype.click = function() {};
            window.html2canvas = () => Promise.resolve({toDataURL: () =>
                'data:image/png;base64,cGl4ZWw='});
            var ok = await fw.captureElementForAnalysis(document.getElementById('monthly'));
            return {ok, payload: window.__download ? JSON.parse(await window.__download.text()) : null,
                message: document.querySelector('.fw-analysis-error span')?.textContent || ''};
        }""")
        assert result["ok"] is True, result["message"]
        filters = result["payload"]["source"]["filters"]
        assert filters["parent_ds"]["parent-country"]["value"] == "NL"
        assert filters["child_ds"]["local-channel"]["value"] == "web"
        assert "daily_ds" not in filters

    def test_filter_change_during_async_capture_discards_image(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="section">' +
                '<canvas id="chart"></canvas></div>';
            window._reportData = {defaultScope: 'main', components: {chart: {dataset_id: 'sales_ds'}},
                _freshness: {generated_at: '2026-10-07T09:00:00Z'}};
            window._currentScope = 'main';
            window._scopeCache = {main: window._reportData.components};
            fw.filterEngine.initColumnar('sales_ds', [{country: 'NL'}, {country: 'US'}]);
            fw.filterEngine.setFilter('sales_ds', 'country-filter', 'country', 'equals', 'NL');
            var started;
            var start = new Promise(resolve => { started = resolve; });
            var finish;
            window.html2canvas = () => {
                started();
                return new Promise(resolve => { finish = resolve; });
            };
            window.__downloads = 0;
            URL.createObjectURL = () => { window.__downloads++; return 'blob:test'; };
            URL.revokeObjectURL = () => {};
            HTMLAnchorElement.prototype.click = function() {};
            var capture = fw.captureElementForAnalysis(document.getElementById('section'));
            await start;
            fw.filterEngine.setFilter('sales_ds', 'country-filter', 'country', 'equals', 'US');
            finish({toDataURL: () => 'data:image/png;base64,cGl4ZWw='});
            var ok = await capture;
            return {ok, downloads: window.__downloads,
                message: document.querySelector('.fw-analysis-error span')?.textContent || ''};
        }""")
        assert result["ok"] is False
        assert result["downloads"] == 0
        assert "changed while capture was rendering" in result["message"]

    def test_pending_live_query_debounce_and_failure_state(self, live_query_page):
        result = live_query_page.evaluate("""async () => {
            window.fetch = () => Promise.reject(new Error('offline'));
            window._fwLiveQuery.registerDataset('live_ds', {
                query_id: 'query', params: [{name: 'country', type: 'str'}],
                bindings: [{column: 'country', filter_type: 'dropdown', param: 'country'}],
                defaults: {country: 'all'}
            });
            fw.filterEngine.setFilter('live_ds', 'country-filter', 'country', 'equals', 'NL');
            var duringDebounce = window._fwLiveQuery.getCaptureStatus();
            document.body.innerHTML = '<div class="fw-section" id="live-section">' +
                '<canvas id="live-chart"></canvas></div>';
            window.html2canvas = () => { throw new Error('must not render while query is pending'); };
            var blockedCapture = await fw.captureElementForAnalysis(document.getElementById('live-section'));
            var captureMessage = document.querySelector('.fw-analysis-error span')?.textContent || '';
            await new Promise(resolve => setTimeout(resolve, 350));
            return {duringDebounce, blockedCapture, captureMessage,
                afterFailure: window._fwLiveQuery.getCaptureStatus()};
        }""")
        assert result["duringDebounce"]["pending"] is True
        assert result["blockedCapture"] is False
        assert "live data query is still running" in result["captureMessage"]
        assert result["afterFailure"]["pending"] is False
        assert result["afterFailure"]["error"] == "offline"

    def test_lazy_visual_component_fails_with_actionable_readiness_message(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="lazy-section">' +
                '<canvas id="lazy-chart" data-fw-kind="LineChart"></canvas></div>';
            window._reportData = {defaultScope: 'main', components: {
                'lazy-chart': {type: 'chart', dataset_id: 'sales_ds'}
            }, _freshness: {generated_at: '2026-10-07T09:00:00Z'}};
            window._currentScope = 'main';
            window._scopeCache = {main: window._reportData.components};
            fw.filterEngine.initColumnar('sales_ds', [{country: 'NL'}]);
            window.html2canvas = () => { throw new Error('blank placeholder captured'); };
            var ok = await fw.captureElementForAnalysis(document.getElementById('lazy-section'));
            return {ok, message: document.querySelector('.fw-analysis-error span')?.textContent || ''};
        }""")
        assert result["ok"] is False
        assert "has not finished rendering" in result["message"]

    def test_cancel_restores_selection_and_pending_query_blocks_capture(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="select-me" tabindex="3">' +
            '<h2>Section</h2><canvas id="chart"></canvas></div>' +
                '<div class="fw-live-status" data-live-state="loading">updating</div>';
            var loading = document.createElement('div');
            loading.id = 'fwLoading'; loading.style.cssText = 'position:fixed;display:block';
            document.body.appendChild(loading);
            var loadResult = await fw.captureForAnalysis();
            var loadMessage = document.querySelector('.fw-analysis-error span')?.textContent || '';
            loading.remove();
            var stateSeen = !!document.querySelector('[data-live-state="loading"]');
            var pending = await fw.captureForAnalysis();
            var selectionStarted = !!document.querySelector('.fw-analysis-selectable');
            var error = document.querySelector('.fw-analysis-error span')?.textContent || '';
            document.querySelector('.fw-live-status').remove();
            var done = fw.captureForAnalysis();
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
            var cancelled = await done;
            return {loadResult, loadMessage, pending, error, cancelled, stateSeen, selectionStarted,
                tabIndex: document.getElementById('select-me').getAttribute('tabindex'),
                selectable: document.querySelector('.fw-analysis-selectable'),
                notice: document.querySelector('[role="status"]')?.textContent || '',
                ui: document.querySelector('.fw-analysis-capture-ui')};
        }""")
        assert result["loadResult"] is False
        assert result["loadMessage"] == "The report is still loading."
        assert result["pending"] is False
        assert result["stateSeen"] is True
        assert result["selectionStarted"] is False
        assert "live data query is still running" in (result["error"] or result["notice"])
        assert result["cancelled"] is False
        assert result["tabIndex"] == "3"
        assert result["selectable"] is None
        assert result["ui"] is None


    def test_ordinary_table_and_pivot_data_render_as_text(self, capture_page):
        """Data cells, headers and pivot dimensions must stay inert in Chromium."""
        from pathlib import Path

        from trellum.assets import load_js

        marker = '<svg onload="window.__xss=1"></svg>'
        result = capture_page.evaluate("""async ({tableJs, pivotJs, dropdownJs, slimJs, marker}) => {
            window.__xss = 0;
            window._fwRenderers = {};
            document.body.innerHTML = '<div id="static"></div><div id="live"></div><div id="pivot"></div>' +
                '<div id="pivot_ctrl"></div><div id="filters"><select class="fw-dropdown" data-filter-id="f"></select></div>';
            function load(source) { var s = document.createElement('script'); s.textContent = source; document.head.appendChild(s); }
            load(slimJs); load(tableJs); load(pivotJs); load(dropdownJs);
            window._fwRenderers.table('static', {columns: [marker], rows: [[marker]], totalRows: 1,
                maxRows: 10, sortable: true});
            window._fwLiveWrap = function(id, cfg, draw) { draw([{[marker]: '4' + marker}]); };
            window._fwRenderers.table('live', {dataset_id: 'ds', columns: [marker], maxRows: 10,
                rows: [], totalRows: 1, barColumn: 0});
            window._fwRenderers.pivot('pivot', {default_rows: [marker], default_col: marker,
                default_value: 'amount', default_agg: 'sum', value_format: 'number', dim_cols: [marker], num_cols: ['amount'],
                data: [{[marker]: marker, amount: 2}]});
            var fc = {id: 'f', column: 'country', options: [marker], multi: true};
            var handlers = {urlLookup: function() {}, findFc: function() { return fc; }, setFilter: function() {},
                propagate: function() {}, urlWrite: function() {}};
            window._fwFilterTypes = {};
            load(dropdownJs);
            window._fwFilterTypes.dropdown.wireControls(document.getElementById('filters'), {}, 'ds', handlers);
            var select = document.querySelector('#filters select');
            var ss = select._fwSlimSelect;
            ss.setSelected([marker]);
            ss.open();
            var search = document.querySelector('.ss-search input');
            search.value = 'svg'; search.dispatchEvent(new Event('input', {bubbles: true}));
            await new Promise(resolve => setTimeout(resolve, 30));
            return {staticText: document.querySelector('#static th').textContent + '|' + document.querySelector('#static td').textContent,
                liveText: document.querySelector('#live th').textContent + '|' + document.querySelector('#live td').textContent,
                hasBar: !!document.querySelector('#live .fw-bar-cell'),
                pivotText: Array.from(document.querySelectorAll('#pivot th,#pivot td')).map(e => e.textContent),
                controlsText: document.querySelector('#pivot_ctrl').textContent,
                dropdownText: document.querySelector('.ss-option').textContent,
                dropdownValue: Array.from(select.options).find(o => o.textContent === marker).value,
                selected: ss.getSelected(),
                active: !!document.querySelector('#static svg[onload],#live svg[onload],#pivot svg[onload],#pivot_ctrl svg[onload],#filters svg[onload],.ss-content svg[onload],#static script,#live script,#pivot script,#pivot_ctrl script,#filters script,.ss-content script'),
                executed: window.__xss};
        }""", {"tableJs": load_js("components/data_table.js"), "pivotJs": load_js("components/pivot_table.js"),
            "dropdownJs": load_js("components/dropdown_filter.js"),
            "slimJs": (Path(__file__).parents[1] / "static/vendor/slimselect.min.js").read_text(encoding="utf-8"),
            "marker": marker})
        assert result["staticText"].startswith(marker)
        assert "|" in result["staticText"]
        assert result["staticText"].endswith("|" + marker)
        assert result["liveText"] == marker + "|4" + marker
        assert result["hasBar"] is True
        assert marker in result["pivotText"]
        assert marker in result["controlsText"]
        assert result["dropdownText"] == marker
        assert result["dropdownValue"] == marker
        assert result["selected"] == [marker]
        assert result["active"] is False
        assert result["executed"] == 0

    def test_keyboard_selection_captures_the_focused_target(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="keyboard-section" tabindex="4">' +
                '<h2>Keyboard target</h2></div>';
            window.__download = null;
            URL.createObjectURL = blob => { window.__download = blob; return 'blob:test'; };
            URL.revokeObjectURL = () => {};
            HTMLAnchorElement.prototype.click = function() {};
            window.html2canvas = target => {
                window.__capturedTarget = target.id;
                return Promise.resolve({toDataURL: () => 'data:image/png;base64,cGl4ZWw='});
            };
            var capture = fw.captureForAnalysis();
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
            var ok = await capture;
            return {ok, target: window.__capturedTarget,
                tabIndex: document.getElementById('keyboard-section').getAttribute('tabindex'),
                controls: document.querySelector('.fw-analysis-selectable,.fw-analysis-capture-ui button'),
                payload: JSON.parse(await window.__download.text())};
        }""")
        assert result["ok"] is True
        assert result["target"] == "keyboard-section"
        assert result["tabIndex"] == "4"
        assert result["controls"] is None
        assert result["payload"]["image"]["data_base64"] == "cGl4ZWw="

    def test_capture_failure_is_visible_and_report_remains_interactive(self, capture_page):
        result = capture_page.evaluate("""async () => {
            document.body.innerHTML = '<div class="fw-section" id="section"><h2>Revenue</h2></div>';
            window.html2canvas = () => Promise.reject(new Error('render broke'));
            var capture = fw.captureForAnalysis();
            document.getElementById('section').dispatchEvent(new MouseEvent('click', {bubbles: true}));
            var ok = await capture;
            return {ok, message: document.querySelector('.fw-analysis-error span')?.textContent || '',
                target: document.getElementById('section').isConnected,
                selectable: document.querySelector('.fw-analysis-selectable'),
                controls: document.querySelector('.fw-analysis-capture-ui:not(.fw-analysis-error)'),
                tabIndex: document.getElementById('section').getAttribute('tabindex')};
        }""")
        assert result == {"ok": False, "message": "render broke", "target": True,
                          "selectable": None, "controls": None, "tabIndex": None}

    def test_header_exposes_capture_action_only_for_reports(self, capture_page):
        capture_page.evaluate("""() => {
            document.body.setAttribute('data-content-kind', 'analysis');
            document.body.innerHTML = '<div class="fw-section"></div>' +
                '<button id="fwExportBtn"></button><div id="fwExportMenu">' +
                '<button data-export="analysis">Capture</button></div>';
            window.__captureCalls = 0;
            window.fw.captureForAnalysis = () => { window.__captureCalls++; };
        }""")
        capture_page.add_script_tag(content=load_js("components/header.js"))
        result = capture_page.evaluate("""() => {
            document.dispatchEvent(new Event('DOMContentLoaded'));
            var analysisItem = document.querySelector('[data-export="analysis"]');
            var hiddenForAnalysis = analysisItem.hidden;
            document.body.setAttribute('data-content-kind', 'report');
            analysisItem.hidden = false;
            analysisItem.click();
            return {hiddenForAnalysis, calls: window.__captureCalls};
        }""")
        assert result == {"hiddenForAnalysis": True, "calls": 1}


class TestBackwardCompatAliases:
    """Ensure legacy bare globals still work and point to the same objects."""

    LEGACY_ALIASES = [
        ("window.fmtCompact", "window.fw.fmtCompact"),
        ("window.fmtCompact$", "window.fw['fmtCompact$']"),
        ("window.getActiveTheme", "window.fw.getActiveTheme"),
        ("window.getThemeColors", "window.fw.getThemeColors"),
        ("window.switchTheme", "window.fw.switchTheme"),
        ("window.getFormatter", "window.fw.getFormatter"),
        ("window._fwFilterEngine", "window.fw.filterEngine"),
        ("window._fwAggregate", "window.fw.aggregate"),
        ("window._fwLiveWrap", "window.fw.liveWrap"),
        ("window._themes", "window.fw.themes"),
    ]

    @pytest.mark.parametrize("legacy,fw_path", LEGACY_ALIASES)
    def test_alias_exists_and_matches(self, browser_page, legacy, fw_path):
        result = browser_page.evaluate(f"""
        (() => {{
            var legacy = {legacy};
            var fw = {fw_path};
            if (legacy === undefined) return 'legacy_undefined';
            if (fw === undefined) return 'fw_undefined';
            return legacy === fw ? 'match' : 'mismatch';
        }})()
        """)
        assert result == "match", f"{legacy} does not match {fw_path}: {result}"
