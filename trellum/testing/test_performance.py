"""Performance tests: measure report rendering speed with large datasets.

Generates reports with configurable row counts (1k–100k), then uses Playwright
to measure page load, filter engine init, filter operations, chart rendering,
theme switching, and memory usage.  Produces a structured report identifying
bottlenecks and scaling characteristics.

Run with:  python3 -m pytest trellum/testing/test_performance.py -v -s

Requires:  pip install playwright && python3 -m playwright install chromium
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import HTTPServer, SimpleHTTPRequestHandler
from unittest.mock import patch

import pandas as pd
import pytest

from trellum.project import get_project_root

# Resolve the project the way the framework itself does. The old three-deep
# dirname walk assumed this package sat at <project>/trellum/, which is only
# true in a consumer checkout; run from the framework's own repo it pointed at
# the parent directory, so reports/ was never found and every test in this
# file skipped silently.
_PROJECT_ROOT = get_project_root()
_REPORTS_DIR = os.path.join(_PROJECT_ROOT, "reports")

# Row counts to test — from small to stress-test territory
ROW_COUNTS = [1_000, 10_000, 50_000, 100_000]

# Reports to benchmark, named for what they exercise rather than for
# themselves -- same reasoning as test_interactions.py, and overridable the
# same way: FW_INTERACTION_REPORTS="<filtered-report>,<custom-js-report>".
_FILTERS_REPORT = "player-overview"      # FilterBar, date range, many charts
_CUSTOM_JS_REPORT = "cart-funnel"        # RawHTML + custom canvas JS, no vendor lib

_env_reports = os.environ.get("FW_INTERACTION_REPORTS", "").strip()
if _env_reports:
    _parts = [p.strip() for p in _env_reports.split(",") if p.strip()]
    if len(_parts) == 2:
        _FILTERS_REPORT, _CUSTOM_JS_REPORT = _parts

BENCHMARK_REPORTS = [_FILTERS_REPORT, _CUSTOM_JS_REPORT]


@dataclass
class PerfMetric:
    """A single timed measurement."""
    name: str
    rows: int
    duration_ms: float
    extra: dict = field(default_factory=dict)


@dataclass
class PerfReport:
    """Collected performance metrics for one report at one row count."""
    slug: str
    rows: int
    metrics: list[PerfMetric] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, name: str, duration_ms: float, **extra):
        self.metrics.append(PerfMetric(name=name, rows=self.rows, duration_ms=duration_ms, extra=extra))

    def summary(self) -> dict[str, float]:
        return {m.name: m.duration_ms for m in self.metrics}


# ── Helpers ──────────────────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _create_large_mock_query_df(n_rows: int, seed: int = 42):
    """Create a mock query_df that returns DataFrames with n_rows rows."""
    from trellum.testing.mock_data import _generate_column, _infer_type, generate_mock_df
    call_count = 0

    _FALLBACK_COLS = [
        "event_date", "platform", "spender_tier", "dsi_bucket",
        "revenue", "net_revenue", "dau", "mau", "payers",
        "transactions", "sessions", "ftd_users", "impressions",
        "ad_revenue", "iap_usd", "unique_users", "country_cd",
    ]

    def mock_query_df(conn, sql, params=None, cache_ttl=None, **kwargs):
        nonlocal call_count
        call_count += 1
        import random as _rng
        _rng.seed(seed + call_count)

        date_range = ("2025-01-01", "2026-04-11")
        df = generate_mock_df(sql, n_rows=n_rows, seed=seed + call_count)

        if len(df.columns) <= 1:
            data = {}
            for col in _FALLBACK_COLS:
                ct = _infer_type(col)
                data[col] = _generate_column(col, ct, n_rows, date_range)
            df = pd.DataFrame(data)

        return df

    return mock_query_df


def _generate_report_with_rows(slug: str, n_rows: int, output_dir: str) -> tuple[bool, float, float]:
    """Generate a report with mock data at a specific row count.

    Returns (success, generation_time_s, data_json_size_kb).
    """
    from trellum.rendering.html_builder import render_report
    from trellum.report import BaseReport, ReportContext
    from trellum.runner import discover_report
    from trellum.testing.runner import (
        _create_mock_read_excel,
        _load_extra_columns,
        _MockConnection,
        _patch_query_df_in_report,
        _unpatch_query_df_in_report,
    )

    report_dir = os.path.join(_REPORTS_DIR, slug)
    config = BaseReport.load_config(report_dir)
    os.makedirs(output_dir, exist_ok=True)

    extra_cols = _load_extra_columns(report_dir)
    if extra_cols is None:
        return False, 0, 0

    report_cls = discover_report(report_dir)
    report = report_cls()

    from trellum.themes import resolve_theme
    theme = resolve_theme(config.get("theme"))
    ctx = ReportContext(config=config, slug=slug, output_dir=output_dir, theme=theme)

    mock_conn = _MockConnection(seed=42)
    mock_qdf = _create_large_mock_query_df(n_rows=n_rows)
    mock_excel = _create_mock_read_excel(seed=42, extra_columns=extra_cols)

    from trellum.data.query import disable_cache, enable_cache
    disable_cache()

    gen_start = time.time()
    try:
        with patch.object(ctx, "get_connection", return_value=mock_conn), \
             patch("trellum.data.query_df", mock_qdf), \
             patch("trellum.data.query.query_df", mock_qdf), \
             patch("pandas.read_excel", mock_excel):
            _patch_query_df_in_report(report_dir, mock_qdf)
            try:
                report.generate(ctx)
            finally:
                _unpatch_query_df_in_report(report_dir)
                enable_cache()
    except Exception:
        try:
            ctx.close_connections()
        except Exception:
            pass
        return False, 0, 0

    gen_time = time.time() - gen_start

    from trellum.runner import _load_events
    events = _load_events(config)
    from trellum.validation import validate_report
    validation = validate_report(ctx, events=events)
    extra_data = {"_events": events} if events else None
    html_path, json_path = render_report(ctx, output_dir, data=extra_data, auto_refresh=False, validation=validation)

    try:
        ctx.close_connections()
    except Exception:
        pass

    json_size_kb = os.path.getsize(json_path) / 1024 if os.path.exists(json_path) else 0
    return True, gen_time, json_size_kb


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # Charts do not render without the bundled vendor libraries.
        from trellum.rendering.cdn import serve_vendor_request

        if serve_vendor_request(self):
            return
        super().do_GET()


def _run_browser_perf(page, output_dir: str, slug: str, n_rows: int) -> PerfReport:
    """Open a generated report in Playwright and measure performance."""
    perf = PerfReport(slug=slug, rows=n_rows)

    port = _find_free_port()

    class Handler(_QuietHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=output_dir, **kwargs)

    server = HTTPServer(("", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    try:
        # 1. Page load time
        load_start = time.time()
        page.goto(f"http://localhost:{port}/index.html", wait_until="networkidle")
        page.wait_for_timeout(1000)
        load_ms = (time.time() - load_start) * 1000
        perf.add("page_load", load_ms)

        # 2. data.json parse time (measured in browser)
        parse_ms = page.evaluate("""
        (() => {
            var t0 = performance.now();
            JSON.parse(JSON.stringify(window._reportData || {}));
            return performance.now() - t0;
        })()
        """)
        perf.add("data_json_reparse", parse_ms)

        # 3. Filter engine init time (measures initColumnar with index building)
        fe_init_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            var t0 = performance.now();
            fe.initColumnar(dsId + '_perf_test', rows);
            var elapsed = performance.now() - t0;
            fe.removeDataset(dsId + '_perf_test');
            return elapsed;
        })()
        """)
        if fe_init_ms >= 0:
            perf.add("filter_engine_init", fe_init_ms)

        # 4. getFiltered (no filters) — full materialization
        get_all_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var t0 = performance.now();
            for (var i = 0; i < 10; i++) {
                fe.clearAllFilters(dsId);
                fe.getFiltered(dsId);
            }
            return (performance.now() - t0) / 10;
        })()
        """)
        if get_all_ms >= 0:
            perf.add("getFiltered_no_filter", get_all_ms)

        # 5. setFilter + getFiltered (equals mode)
        filter_eq_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            if (!rows.length) return -1;
            var catCol = null;
            var sample = rows[0];
            for (var k in sample) {
                if (typeof sample[k] === 'string' && k !== 'event_date') { catCol = k; break; }
            }
            if (!catCol) return -1;
            var val = String(rows[0][catCol]);
            var t0 = performance.now();
            for (var i = 0; i < 10; i++) {
                fe.setFilter(dsId, 'perf_eq', catCol, 'equals', val);
            }
            var elapsed = (performance.now() - t0) / 10;
            fe.clearFilter(dsId, 'perf_eq');
            return elapsed;
        })()
        """)
        if filter_eq_ms >= 0:
            perf.add("setFilter_equals", filter_eq_ms,
                     note="avg of 10 iterations, includes notify/subscriber callbacks")

        # 6. setFilter + getFiltered (range mode — date range)
        filter_range_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            if (!rows.length) return -1;
            var dateCol = null;
            for (var k in rows[0]) {
                if (k.indexOf('date') !== -1) { dateCol = k; break; }
            }
            if (!dateCol) return -1;
            var dates = rows.map(r => String(r[dateCol])).filter(d => /^\\d{4}/.test(d)).sort();
            if (dates.length < 2) return -1;
            var mid = Math.floor(dates.length / 2);
            var t0 = performance.now();
            for (var i = 0; i < 10; i++) {
                fe.setFilter(dsId, 'perf_rng', dateCol, 'range', {min: dates[0], max: dates[mid]});
            }
            var elapsed = (performance.now() - t0) / 10;
            fe.clearFilter(dsId, 'perf_rng');
            return elapsed;
        })()
        """)
        if filter_range_ms >= 0:
            perf.add("setFilter_range", filter_range_ms,
                     note="date range filter, avg of 10 iterations")

        # 7. Aggregation: sum
        agg_sum_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            var numCol = null;
            for (var k in rows[0]) {
                if (typeof rows[0][k] === 'number') { numCol = k; break; }
            }
            if (!numCol) return -1;
            var agg = window._fwAggregate;
            var t0 = performance.now();
            for (var i = 0; i < 100; i++) agg.sum(rows, numCol);
            return (performance.now() - t0) / 100;
        })()
        """)
        if agg_sum_ms >= 0:
            perf.add("aggregate_sum", agg_sum_ms, note="avg of 100 iterations")

        # 8. Aggregation: groupBy
        agg_group_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            var dateCol = null, numCols = [];
            for (var k in rows[0]) {
                if (k.indexOf('date') !== -1 && !dateCol) dateCol = k;
                else if (typeof rows[0][k] === 'number' && numCols.length < 3) numCols.push(k);
            }
            if (!dateCol || !numCols.length) return -1;
            var agg = window._fwAggregate;
            var t0 = performance.now();
            for (var i = 0; i < 10; i++) agg.groupBy(rows, dateCol, numCols);
            return (performance.now() - t0) / 10;
        })()
        """)
        if agg_group_ms >= 0:
            perf.add("aggregate_groupBy", agg_group_ms, note="avg of 10 iterations")

        # 9. Aggregation: pivot
        agg_pivot_ms = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return -1;
            var rows = fe.getFiltered(dsId);
            var dateCol = null, catCol = null, numCols = [];
            for (var k in rows[0]) {
                if (k.indexOf('date') !== -1 && !dateCol) dateCol = k;
                else if (typeof rows[0][k] === 'string' && !catCol) catCol = k;
                else if (typeof rows[0][k] === 'number' && numCols.length < 2) numCols.push(k);
            }
            if (!dateCol || !catCol || !numCols.length) return -1;
            var agg = window._fwAggregate;
            var t0 = performance.now();
            for (var i = 0; i < 5; i++) agg.pivot(rows, dateCol, catCol, numCols);
            return (performance.now() - t0) / 5;
        })()
        """)
        if agg_pivot_ms >= 0:
            perf.add("aggregate_pivot", agg_pivot_ms, note="avg of 5 iterations")

        # 10. Theme switch time
        theme_ms = page.evaluate("""
        (() => {
            var t0 = performance.now();
            switchTheme('dark');
            var elapsed = performance.now() - t0;
            switchTheme('light');
            return elapsed;
        })()
        """)
        perf.add("theme_switch", theme_ms)

        # 11. Chart count and memory
        chart_info = page.evaluate("""
        (() => {
            var instances = window._chartInstances || {};
            var count = Object.keys(instances).length;
            var mem = performance.memory ? {
                usedJSHeapSize: performance.memory.usedJSHeapSize,
                totalJSHeapSize: performance.memory.totalJSHeapSize,
            } : null;
            var fe = window._fwFilterEngine;
            var stats = fe.getEngineStats();
            return {
                chartCount: count,
                memory: mem,
                totalFilterRows: stats.totalRows,
                datasets: stats.datasetCount
            };
        })()
        """)
        perf.add("chart_count", 0, count=chart_info.get("chartCount", 0))
        perf.add("total_filter_rows", 0, count=chart_info.get("totalFilterRows", 0))
        perf.add("dataset_count", 0, count=chart_info.get("datasets", 0))
        if chart_info.get("memory"):
            perf.add("js_heap_used_mb", 0,
                     value_mb=round(chart_info["memory"]["usedJSHeapSize"] / 1048576, 1))
            perf.add("js_heap_total_mb", 0,
                     value_mb=round(chart_info["memory"]["totalJSHeapSize"] / 1048576, 1))

        if js_errors:
            perf.errors.extend(js_errors[:5])

    finally:
        server.shutdown()

    return perf


def _print_perf_table(all_results: list[PerfReport]):
    """Print a formatted performance comparison table."""
    if not all_results:
        return

    print(f"\n{'=' * 100}")
    print("  PERFORMANCE RESULTS")
    print(f"{'=' * 100}\n")

    # Group by slug
    by_slug: dict[str, list[PerfReport]] = {}
    for r in all_results:
        by_slug.setdefault(r.slug, []).append(r)

    for slug, reports in by_slug.items():
        print(f"  Report: {slug}")
        print(f"  {'─' * 90}")

        # Collect all metric names
        all_metric_names = []
        seen = set()
        for r in reports:
            for m in r.metrics:
                if m.name not in seen:
                    seen.add(m.name)
                    all_metric_names.append(m.name)

        # Header
        row_labels = [str(r.rows) for r in reports]
        header = f"  {'Metric':<30}" + "".join(f"  {rl:>12} rows" for rl in row_labels)
        print(header)
        print(f"  {'─' * 90}")

        for mname in all_metric_names:
            vals = []
            for r in reports:
                match = [m for m in r.metrics if m.name == mname]
                if match:
                    m = match[0]
                    if m.extra.get("count") is not None:
                        vals.append(f"{m.extra['count']:>10}")
                    elif m.extra.get("value_mb") is not None:
                        vals.append(f"{m.extra['value_mb']:>8.1f} MB")
                    elif m.duration_ms > 0:
                        if m.duration_ms >= 1000:
                            vals.append(f"{m.duration_ms / 1000:>9.2f} s")
                        else:
                            vals.append(f"{m.duration_ms:>8.1f} ms")
                    else:
                        vals.append(f"{'—':>12}")
                else:
                    vals.append(f"{'—':>12}")

            row = f"  {mname:<30}" + "".join(f"  {v:>15}" for v in vals)
            print(row)

        # Scaling analysis
        print(f"\n  {'─' * 90}")
        print("  Scaling analysis (ratio vs 1k baseline):")
        baseline = reports[0] if reports else None
        if baseline and len(reports) > 1:
            baseline_metrics = {m.name: m.duration_ms for m in baseline.metrics if m.duration_ms > 0}
            for r in reports[1:]:
                ratios = []
                for m in r.metrics:
                    if m.duration_ms > 0 and m.name in baseline_metrics and baseline_metrics[m.name] > 0:
                        ratio = m.duration_ms / baseline_metrics[m.name]
                        ratios.append((m.name, ratio, m.rows))
                if ratios:
                    worst = max(ratios, key=lambda x: x[1])
                    row_scale = r.rows / baseline.rows
                    print(f"    {r.rows:>6} rows ({row_scale:.0f}x data):")
                    for name, ratio, _ in sorted(ratios, key=lambda x: -x[1])[:5]:
                        status = "OK" if ratio < row_scale * 1.5 else "SLOW" if ratio < row_scale * 3 else "BOTTLENECK"
                        print(f"      {name:<28} {ratio:>6.1f}x  [{status}]")

        if any(r.errors for r in reports):
            print("\n  JS Errors:")
            for r in reports:
                for err in r.errors:
                    print(f"    [{r.rows} rows] {err[:100]}")

        print()

    # Generate improvement recommendations
    print(f"  {'=' * 90}")
    print("  RECOMMENDATIONS")
    print(f"  {'=' * 90}\n")

    for slug, reports in by_slug.items():
        if len(reports) < 2:
            continue
        baseline = reports[0]
        largest = reports[-1]
        baseline_ms = {m.name: m.duration_ms for m in baseline.metrics if m.duration_ms > 0}
        largest_ms = {m.name: m.duration_ms for m in largest.metrics if m.duration_ms > 0}
        data_scale = largest.rows / baseline.rows

        bottlenecks = []
        for name in largest_ms:
            if name in baseline_ms and baseline_ms[name] > 0:
                ratio = largest_ms[name] / baseline_ms[name]
                if ratio > data_scale * 1.5 and largest_ms[name] > 50:
                    bottlenecks.append((name, ratio, largest_ms[name]))

        if bottlenecks:
            print(f"  {slug}:")
            for name, ratio, abs_ms in sorted(bottlenecks, key=lambda x: -x[2]):
                print(f"    ⚠ {name}: {abs_ms:.0f}ms at {largest.rows} rows ({ratio:.1f}x scaling)")
                if "filter" in name.lower() or "getFiltered" in name:
                    print("      → Consider indexed lookups or pre-computed filter maps")
                elif "pivot" in name.lower():
                    print("      → Consider server-side pre-pivoting or lazy pivot on demand")
                elif "groupBy" in name.lower():
                    print("      → Consider pre-aggregated datasets or Web Worker offload")
                elif "page_load" in name.lower():
                    print("      → Consider paginating data.json or lazy-loading datasets")
                elif "theme" in name.lower():
                    print("      → Chart.update('none') may be slow with many chart instances")
            print()
        else:
            print(f"  {slug}: No significant bottlenecks detected ✓\n")


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright not installed")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    yield browser
    browser.close()
    pw.stop()


# ── Tests ────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("slug", BENCHMARK_REPORTS)
def test_performance_scaling(slug, _playwright):
    """Measure performance at increasing row counts and print analysis."""
    report_dir = os.path.join(_REPORTS_DIR, slug)
    if not os.path.isdir(report_dir):
        pytest.skip(f"Report {slug} not found")

    all_results: list[PerfReport] = []

    for n_rows in ROW_COUNTS:
        output_dir = tempfile.mkdtemp(prefix=f"fw_perf_{slug}_{n_rows}_")
        print(f"\n  [{slug}] Generating with {n_rows:,} rows...", end="", flush=True)

        gen_start = time.time()
        success, gen_time, json_kb = _generate_report_with_rows(slug, n_rows, output_dir)
        if not success:
            print(" FAILED (generation error)")
            continue

        print(f" OK ({gen_time:.1f}s, {json_kb:.0f} KB)", flush=True)

        page = _playwright.new_page(viewport={"width": 1400, "height": 900})
        try:
            perf = _run_browser_perf(page, output_dir, slug, n_rows)
            perf.add("python_generation", gen_time * 1000)
            perf.add("data_json_size_kb", 0, value_mb=round(json_kb / 1024, 2))
            all_results.append(perf)
        finally:
            page.close()

    _print_perf_table(all_results)

    # Assert no catastrophic performance (page load under 30s for largest)
    if all_results:
        largest = all_results[-1]
        load_metric = [m for m in largest.metrics if m.name == "page_load"]
        if load_metric:
            assert load_metric[0].duration_ms < 30000, \
                f"Page load at {largest.rows} rows took {load_metric[0].duration_ms:.0f}ms (>30s)"

        assert len(largest.errors) == 0, \
            f"JS errors at {largest.rows} rows: {largest.errors}"


_SERIALIZE_ROW_COUNTS = [10_000, 50_000, 100_000]


def test_serialize_columnar_benchmark():
    """Benchmark _serialize_columnar at increasing row counts."""
    import numpy as np

    from trellum.components.filterable import _serialize_columnar

    results: list[tuple[int, float, int]] = []

    for n in _SERIALIZE_ROW_COUNTS:
        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "event_date": pd.date_range("2025-01-01", periods=n, freq="h"),
            "platform": rng.choice(["iOS", "Android", "Web"], size=n),
            "country": rng.choice(["US", "DE", "JP", "BR", "IN", "GB"], size=n),
            "spender_tier": rng.choice(
                ["whale", "dolphin", "minnow", "non_payer"], size=n,
            ),
            "revenue": rng.uniform(0, 500, size=n).astype(float),
            "net_revenue": rng.uniform(0, 350, size=n).astype(float),
            "dau": rng.integers(100, 50000, size=n),
            "sessions": rng.integers(1, 20, size=n),
            "payers": rng.integers(0, 1000, size=n),
            "misc_obj": [
                None if rng.random() < 0.05 else f"val_{i % 200}"
                for i in range(n)
            ],
        })

        t0 = time.time()
        out = _serialize_columnar(df)
        elapsed_ms = (time.time() - t0) * 1000

        json_bytes = len(
            json.dumps(out, default=str).encode()
        )
        results.append((n, elapsed_ms, json_bytes))

    print(f"\n{'=' * 70}")
    print("  _serialize_columnar BENCHMARK")
    print(f"{'=' * 70}")
    print(f"  {'Rows':>10}  {'Time (ms)':>12}  {'JSON size':>12}")
    print(f"  {'─' * 50}")
    for n, ms, sz in results:
        print(f"  {n:>10,}  {ms:>10.1f}ms  {sz / 1024:>9.0f} KB")

    if len(results) >= 2:
        ratio = results[-1][1] / results[0][1]
        data_ratio = results[-1][0] / results[0][0]
        print(f"\n  Scaling: {ratio:.1f}x time for {data_ratio:.0f}x data")

    assert results[-1][1] < 30_000, (
        f"Serialization of {results[-1][0]} rows took "
        f"{results[-1][1]:.0f}ms (>30s)"
    )


@pytest.mark.slow
def test_filter_engine_stress(_playwright):
    """Stress-test the filter engine with rapid sequential filter operations."""
    slug = _FILTERS_REPORT
    report_dir = os.path.join(_REPORTS_DIR, slug)
    if not os.path.isdir(report_dir):
        pytest.skip(f"Report {slug} not found")

    n_rows = 50_000
    output_dir = tempfile.mkdtemp(prefix="fw_perf_stress_")
    print(f"\n  [{slug}] Stress test with {n_rows:,} rows...", flush=True)

    success, _, _ = _generate_report_with_rows(slug, n_rows, output_dir)
    if not success:
        pytest.skip("Generation failed")

    port = _find_free_port()

    class Handler(_QuietHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=output_dir, **kwargs)

    server = HTTPServer(("", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    page = _playwright.new_page(viewport={"width": 1400, "height": 900})
    js_errors = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    try:
        page.goto(f"http://localhost:{port}/index.html", wait_until="networkidle")
        page.wait_for_timeout(2000)

        result = page.evaluate("""
        (() => {
            var fe = window._fwFilterEngine;
            var dsId = fe.getDatasetIds()[0];
            if (!dsId) return {error: 'no datasets'};
            var rows = fe.getFiltered(dsId);
            if (!rows.length) return {error: 'no rows'};

            var dateCol = null, catCol = null;
            for (var k in rows[0]) {
                if (k.indexOf('date') !== -1 && !dateCol) dateCol = k;
                if (typeof rows[0][k] === 'string' && k !== 'event_date' && !catCol) catCol = k;
            }

            var results = {};

            // Rapid sequential filters (100 iterations)
            if (dateCol) {
                var dates = rows.map(r => String(r[dateCol])).sort();
                var t0 = performance.now();
                for (var i = 0; i < 100; i++) {
                    var end = Math.floor(dates.length * (i + 1) / 100);
                    fe.setFilter(dsId, 'stress_rng', dateCol, 'range',
                        {min: dates[0], max: dates[Math.max(0, end - 1)]});
                }
                results.rapid_range_100 = performance.now() - t0;
                fe.clearFilter(dsId, 'stress_rng');
            }

            // Combined filters (multiple active at once)
            if (dateCol && catCol) {
                var mid = Math.floor(rows.length / 2);
                var dates2 = rows.map(r => String(r[dateCol])).sort();
                var catVal = String(rows[0][catCol]);
                var t1 = performance.now();
                fe.setFilter(dsId, 'stress_d', dateCol, 'range',
                    {min: dates2[0], max: dates2[mid]});
                fe.setFilter(dsId, 'stress_c', catCol, 'equals', catVal);
                var filtered = fe.getFiltered(dsId);
                results.combined_filter_ms = performance.now() - t1;
                results.combined_result_count = filtered.length;
                fe.clearFilter(dsId, 'stress_d');
                fe.clearFilter(dsId, 'stress_c');
            }

            fe.notify(dsId);
            results.total_rows = rows.length;
            return results;
        })()
        """)

        print(f"  Results ({n_rows:,} rows):")
        if result.get("rapid_range_100"):
            avg_ms = result["rapid_range_100"] / 100
            print(f"    Rapid range filter (100 iters):  {result['rapid_range_100']:.0f}ms total, {avg_ms:.1f}ms avg")
        if result.get("combined_filter_ms"):
            print(f"    Combined filters:                {result['combined_filter_ms']:.1f}ms")
            print(f"    Combined result count:           {result.get('combined_result_count', '?')}")

        assert len(js_errors) == 0, f"JS errors during stress test: {js_errors}"

        if result.get("rapid_range_100"):
            assert result["rapid_range_100"] < 10000, \
                f"100 rapid filters took {result['rapid_range_100']:.0f}ms (>10s)"

    finally:
        page.close()
        server.shutdown()
