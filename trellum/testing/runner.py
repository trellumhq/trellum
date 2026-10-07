"""Test runner: generate reports with mock data and validate output.

Provides structural checks (valid HTML, valid JSON, no exceptions, validation
passes) and optional visual regression via Playwright screenshots.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pandas as pd

from trellum.testing.mock_data import generate_mock_df


@dataclass
class TestResult:
    """Result of testing a single report."""

    slug: str
    passed: bool = True
    generation_ok: bool = False
    html_valid: bool = False
    json_valid: bool = False
    validation_pass: bool = False
    validation_warns: int = 0
    validation_fails: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    details: list[str] = field(default_factory=list)

    def add_detail(self, msg: str) -> None:
        self.details.append(msg)

    def fail(self, msg: str) -> None:
        self.passed = False
        self.details.append(f"FAIL: {msg}")

    def summary_line(self) -> str:
        icon = "PASS" if self.passed else "FAIL"
        parts = [f"[{icon:>4}] {self.slug:<35} {self.elapsed:>5.1f}s"]
        if self.validation_fails:
            parts.append(f"val_fail={self.validation_fails}")
        if self.validation_warns:
            parts.append(f"val_warn={self.validation_warns}")
        if self.error:
            parts.append(f"err={self.error[:50]}")
        return "  ".join(parts)


def _load_extra_columns(report_dir: str) -> list[str] | None:
    """Load extra test columns from a report's _test_columns.txt file.

    Reports with complex CTEs or pivots can provide a simple text file
    listing additional column names (one per line) that the mock should
    always include alongside parsed SQL columns.

    Returns None if the file contains ``_skip`` (report should be skipped).
    """
    fixture_path = os.path.join(report_dir, "_test_columns.txt")
    if not os.path.isfile(fixture_path):
        return []
    with open(fixture_path) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    if "_skip" in lines:
        return None
    return lines


class _MockConnection:
    """Fake DB connection that returns mock DataFrames for any query."""

    def __init__(self, seed: int = 42):
        self._seed = seed
        self._call_count = 0

    def cursor(self):
        return MagicMock()

    def close(self):
        pass


def _create_mock_query_df(seed: int = 42, extra_columns: list[str] | None = None):
    """Create a patched query_df that returns mock data instead of querying DB.

    Parses the SQL at call time to infer columns. Falls back to a wide
    DataFrame with common column names if parsing fails.
    """
    call_count = 0
    extra_columns = extra_columns or []

    _FALLBACK_COLS = [
        "event_date", "user_id", "platform", "test_variant",
        "dau", "mau", "payers", "transactions", "revenue", "net_revenue",
        "ad_revenue", "impressions", "unique_users", "cpm",
        "ftd_users", "ftd", "hands_played", "sessions", "time_played_min",
        "installs", "cohort_users", "cohort_size", "total_users", "total_dau",
        "eligible_count", "ineligible_count", "eligible", "ineligible",
        "placement", "dsi_bucket", "spender_tier", "audience_segment",
        "currency_name", "source_direct", "source_from_purchased",
        "source_organic", "sink_amount", "is_bot", "top1perc_flag",
        "economy_transaction_feature", "revenue_last_week", "user_level_group",
        "template_name", "template_type", "iap_usd", "days_active",
        "iap_per_active_day", "hands", "unique_players", "attributed_iap_usd",
        "country_cd", "country_name", "bi_property_name", "bi_property_value",
        "ref_date", "total_invitees", "with_install", "first_installs",
        "total_revenue", "step_name", "step_num", "users",
        "elite_flag", "unique_users_window", "current_churners",
        "retained_d3", "retained_d7", "eligible_d3_users", "eligible_d7_users",
        "avg_dau_7d", "wau_7d", "month", "package_group", "package_name",
        "payment_platform", "price_point", "transaction_count",
        "avg_transaction", "from_node", "to_node", "flow", "weighted_items",
        "item_id", "total_chips", "total_consumed", "feature", "detail_name",
        "src_feature", "items_out", "events", "gross_revenue", "net_rev",
        "invites_sent", "invitee_logins", "unique_inviters", "unique_invitees",
        "quest_invites", "organic_invites", "daily_revenue",
        "cohort_date", "d1", "d7", "d14", "avg_invites_per_user",
        "invite_bucket", "sort_order", "game_event_group_name",
        "game_event_name", "game_event_id", "start_date", "end_date",
        "participants", "total_sessions", "total_minutes", "iap_revenue",
        "dtc_revenue", "purchase_revenue", "cz_bucket",
        "only_a", "only_b", "only_c", "ab", "ac", "bc", "abc",
        "last_updated", "last_etl_run", "last_event_ts",
        "total_metrics", "distinct_values", "users_updated",
        "elite_flag", "build_platform", "account_manager",
        "non_dtc_revenue", "dtc_revenue",
    ]

    def mock_query_df(conn, sql, params=None, cache_ttl=None, **kwargs):
        nonlocal call_count
        call_count += 1
        n_rows = 30

        import random as _rng
        _rng.seed(seed + call_count)
        from trellum.testing.mock_data import _generate_column, _infer_type

        date_range = ("2026-03-01", "2026-04-11")
        df = generate_mock_df(sql, n_rows=n_rows, seed=seed + call_count)

        # If SQL parsing found very few columns, use the full fallback set
        if len(df.columns) <= 1:
            data = {}
            for col in _FALLBACK_COLS:
                ct = _infer_type(col)
                data[col] = _generate_column(col, ct, n_rows, date_range)
            df = pd.DataFrame(data)

        # If report provides extra_test_columns, add any missing ones
        if extra_columns:
            missing = {}
            for col in extra_columns:
                if col not in df.columns:
                    ct = _infer_type(col)
                    missing[col] = _generate_column(col, ct, n_rows, date_range)
            if missing:
                df = pd.concat([df, pd.DataFrame(missing)], axis=1)

        return df

    return mock_query_df


def _create_mock_read_excel(seed: int = 42, extra_columns: list[str] | None = None):
    """Create a mock pd.read_excel that returns DataFrames with common columns.

    Returns a DF shaped like a typical Excel sheet with human-readable column
    names. Reports rename these in their generator code.
    """
    call_count = 0

    def mock_read_excel(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        import random as _rng
        _rng.seed(seed + call_count + 1000)
        from trellum.testing.mock_data import _generate_column

        n_rows = 20
        date_range = ("2026-03-01", "2026-04-11")

        data = {
            "User ID": [_rng.randint(100000, 999999) for _ in range(n_rows)],
            "Account Manager": [_rng.choice(["Direct", "Managed", "VIP Team"]) for _ in range(n_rows)],
            "Name": [f"Player_{i}" for i in range(n_rows)],
            "Join Date": _generate_column("event_date", "date", n_rows, date_range),
            "Android Tablets PRAS": [_rng.choice(["", "yes", "no"]) for _ in range(n_rows)],
            "Status": [_rng.choice(["Active", "Inactive"]) for _ in range(n_rows)],
        }
        return pd.DataFrame(data)

    return mock_read_excel


def test_report(
    report_dir: str,
    output_dir: Optional[str] = None,
    screenshot: bool = False,
    seed: int = 42,
    verbose: bool = False,
    save_baseline: bool = False,
    check_baseline: bool = False,
    baseline_threshold: float = 5.0,
) -> TestResult:
    """Test a single report with mock data.

    1. Patches query_df to return auto-generated mock DataFrames
    2. Patches get_connection to return a mock connection
    3. Runs the report generator
    4. Validates output HTML and JSON structure
    5. Checks validation results (fails = test failure)
    6. Optionally takes a Playwright screenshot
    7. Optionally saves/compares visual baseline

    Args:
        report_dir: Path to the report directory.
        output_dir: Where to write output (defaults to temp dir).
        screenshot: If True, take a browser screenshot of the rendered report.
        seed: Random seed for reproducible mock data.
        verbose: Print detailed progress.
        save_baseline: Save screenshot as baseline for future comparisons.
        check_baseline: Compare screenshot against saved baseline.
        baseline_threshold: Max % of changed pixels before failing (default 5%).

    Returns:
        TestResult with pass/fail status and details.
    """
    from trellum.rendering.html_builder import render_report
    from trellum.report import BaseReport, ReportContext
    from trellum.runner import discover_report

    report_dir = os.path.abspath(report_dir)
    config = BaseReport.load_config(report_dir)
    slug = config.get("slug", os.path.basename(report_dir))

    if config.get("disabled"):
        result = TestResult(slug=slug, passed=True)
        result.add_detail("Skipped (disabled)")
        return result

    result = TestResult(slug=slug)
    start = time.time()

    if output_dir is None:
        tmp = tempfile.mkdtemp(prefix=f"fw_test_{slug}_")
        output_dir = tmp
    else:
        os.makedirs(output_dir, exist_ok=True)

    try:
        from trellum.runner.execute import _prepare_report, _resolve_report_theme
        report = (_prepare_report(report_dir, config) if config.get("kind") == "analysis"
                  else discover_report(report_dir)())
        theme = None
        try:
            theme = _resolve_report_theme(config)
        except ValueError:
            pass

        ctx = ReportContext(
            config=config,
            slug=slug,
            output_dir=output_dir,
            theme=theme,
        )

        # Load report-specific test fixture overrides if they exist
        extra_cols = _load_extra_columns(report_dir)
        if extra_cols is None:
            result.add_detail("Skipped (mock not supported — see _test_columns.txt)")
            result.elapsed = time.time() - start
            return result

        mock_conn = _MockConnection(seed=seed)
        mock_qdf = _create_mock_query_df(seed=seed, extra_columns=extra_cols)

        mock_excel = _create_mock_read_excel(seed=seed, extra_columns=extra_cols)

        from trellum.data.query import disable_cache, enable_cache
        disable_cache()

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

        result.generation_ok = True
        if verbose:
            print(f"  [{slug}] Generation OK", flush=True)

        from trellum.runner import _load_events, local_annotation_events
        events = []
        if config.get("kind", "report") != "analysis":
            events = _load_events(config)
            events.extend(local_annotation_events(config))

        from trellum.reporting.diagnostics import (
            compute_details,
            enrich_details_with_disk,
            write_details,
        )
        details = compute_details(ctx, mock_data=config.get("kind", "report") != "analysis")

        from trellum.validation import validate_report
        validation = validate_report(ctx, events=events, details=details)
        val_summary = validation.summary
        result.validation_warns = val_summary.get("warn", 0)
        result.validation_fails = val_summary.get("fail", 0)
        result.validation_pass = result.validation_fails == 0

        if result.validation_fails > 0:
            result.fail(f"Validation has {result.validation_fails} FAIL(s)")
            for check in validation.checks:
                if check.level == "fail":
                    result.add_detail(f"  VAL FAIL: {check.id}: {check.message}")

        extra_data: dict = {}
        if events:
            extra_data["_events"] = events
        from trellum.runner import _weekday_highlight_config
        weekday_highlight = _weekday_highlight_config(config)
        if weekday_highlight:
            extra_data["_weekday_highlight"] = weekday_highlight
        if details.get("coverage_by_dataset"):
            extra_data["_filter_health"] = details["coverage_by_dataset"]
        if details.get("data_source") == "mock":
            extra_data["_is_mock_data"] = True
        # gc_stale_chunks=False: mock-data runs must not delete chunks
        # produced by a previous real-data run sharing the same output dir.
        # Production runs (trellum.run without --test) still GC.
        html_path, json_path = render_report(
            ctx, output_dir, data=extra_data or None, auto_refresh=False,
            validation=validation, details=details, gc_stale_chunks=False,
        )
        enrich_details_with_disk(details, output_dir)
        write_details(output_dir, details)
        from trellum.meta import write_meta
        write_meta(output_dir, {"details": details})

        _check_html(result, html_path)
        _check_json(result, json_path)

        if screenshot or save_baseline or check_baseline:
            _take_screenshot(result, output_dir, slug)

        if result.screenshot_path and (save_baseline or check_baseline):
            from trellum.testing.visual_regression import (
                compare_screenshot,
            )
            from trellum.testing.visual_regression import (
                save_baseline as _save_bl,
            )

            if save_baseline:
                bl_path = _save_bl(slug, result.screenshot_path)
                result.add_detail(f"Baseline saved → {bl_path}")

            if check_baseline:
                diff = compare_screenshot(
                    slug, result.screenshot_path,
                    threshold=baseline_threshold,
                    diff_output_dir=output_dir,
                )
                if not diff.has_baseline:
                    result.add_detail(f"Visual: {diff.message}")
                elif diff.within_threshold:
                    result.add_detail(f"Visual: {diff.message}")
                else:
                    result.fail(f"Visual regression: {diff.message}")

    except Exception as e:
        result.fail(f"Exception: {type(e).__name__}: {e}")
        result.error = str(e)[:200]
        if verbose:
            import traceback
            traceback.print_exc()
    finally:
        result.elapsed = time.time() - start
        try:
            ctx.close_connections()
        except Exception:
            pass

    return result


def _patch_query_df_in_report(report_dir: str, mock_qdf):
    """Patch query_df in any report-level modules that imported it directly."""
    report_dir = os.path.abspath(report_dir)
    slug = os.path.basename(report_dir).replace("-", "_")
    parent_name = os.path.basename(os.path.dirname(report_dir))
    pkg_name = f"{parent_name}.{slug}"

    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not mod_name.startswith(pkg_name):
            continue
        if hasattr(mod, "query_df"):
            mod._orig_query_df = mod.query_df
            mod.query_df = mock_qdf


def _unpatch_query_df_in_report(report_dir: str):
    """Restore original query_df in report modules."""
    report_dir = os.path.abspath(report_dir)
    slug = os.path.basename(report_dir).replace("-", "_")
    parent_name = os.path.basename(os.path.dirname(report_dir))
    pkg_name = f"{parent_name}.{slug}"

    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not mod_name.startswith(pkg_name):
            continue
        if hasattr(mod, "_orig_query_df"):
            mod.query_df = mod._orig_query_df
            del mod._orig_query_df


def _check_html(result: TestResult, html_path: str) -> None:
    """Validate the generated HTML file."""
    if not os.path.isfile(html_path):
        result.fail("index.html not created")
        return

    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    if len(html) < 500:
        result.fail(f"index.html suspiciously small ({len(html)} bytes)")
        return

    if "<html" not in html.lower():
        result.fail("index.html missing <html> tag")
        return

    if "</html>" not in html.lower():
        result.fail("index.html missing closing </html> tag")
        return

    if "data.json" not in html:
        result.fail("index.html does not reference data.json")
        return

    unclosed = 0
    for tag in re.findall(r"<(script|style|div|canvas)\b", html, re.IGNORECASE):
        unclosed += 1
    for tag in re.findall(r"</(script|style|div|canvas)>", html, re.IGNORECASE):
        unclosed -= 1
    if unclosed > 5:
        result.add_detail(f"WARN: {unclosed} potentially unclosed tags")

    result.html_valid = True
    result.add_detail(f"HTML OK ({len(html):,} bytes)")


def _check_json(result: TestResult, json_path: str) -> None:
    """Validate the generated data.json file."""
    if not os.path.isfile(json_path):
        result.fail("data.json not created")
        return

    with open(json_path, encoding="utf-8") as f:
        raw = f.read()

    if len(raw) < 10:
        result.fail(f"data.json suspiciously small ({len(raw)} bytes)")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        result.fail(f"data.json is not valid JSON: {e}")
        return

    if not isinstance(data, dict):
        result.fail(f"data.json root is {type(data).__name__}, expected dict")
        return

    result.json_valid = True
    n_keys = len(data)
    result.add_detail(f"JSON OK ({len(raw):,} bytes, {n_keys} top-level keys)")


def _take_screenshot(result: TestResult, output_dir: str, slug: str) -> None:
    """Take a browser screenshot of the rendered report using Playwright.

    Starts a temporary HTTP server so data.json can be fetched via XHR
    (file:// URLs block fetch requests).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result.add_detail("SKIP screenshot: playwright not installed (pip install playwright && playwright install chromium)")
        return

    screenshot_path = os.path.join(output_dir, f"{slug}_screenshot.png")

    try:
        import socket
        from http.server import HTTPServer, SimpleHTTPRequestHandler

        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]

        from trellum.rendering.cdn import serve_vendor_request

        class QuietHandler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=output_dir, **kwargs)
            def log_message(self, *args):
                pass
            def do_GET(self):
                # Without this the vendor bundles 404 and the screenshot
                # captures a page with no charts on it.
                if serve_vendor_request(self):
                    return
                super().do_GET()

        server = HTTPServer(("", port), QuietHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})

            page.goto(f"http://localhost:{port}/index.html", wait_until="networkidle")
            page.wait_for_timeout(2000)

            # Check for JS console errors
            js_errors: list[str] = []
            page.on("pageerror", lambda e: js_errors.append(str(e)))

            # Freeze motion before capturing. Resizing the viewport below makes
            # every chart re-render, and Chart.js animates that over ~1s; a
            # screenshot taken mid-animation differs run to run, which shows up
            # as phantom visual regressions.
            page.add_style_tag(content=(
                "*,*::before,*::after{animation:none!important;"
                "transition:none!important;}"
            ))
            page.evaluate(_FREEZE_CHARTS_JS)

            full_height = page.evaluate("document.documentElement.scrollHeight")
            page.set_viewport_size({"width": 1400, "height": min(full_height, 10000)})
            page.wait_for_timeout(500)

            # The resize re-rendered the charts -- settle them again.
            page.evaluate(_FREEZE_CHARTS_JS)
            page.wait_for_timeout(400)

            page.screenshot(path=screenshot_path, full_page=True)

            if js_errors:
                result.add_detail(f"JS errors in browser: {'; '.join(js_errors[:3])}")

            browser.close()

        server.shutdown()

        result.screenshot_path = screenshot_path
        size_kb = os.path.getsize(screenshot_path) / 1024
        result.add_detail(f"Screenshot OK ({size_kb:.0f} KB) → {screenshot_path}")

    except Exception as e:
        result.add_detail(f"Screenshot FAILED: {e}")


_FREEZE_CHARTS_JS = """
() => {
  if (!window.Chart) return 0;
  Chart.defaults.animation = false;
  Chart.defaults.animations = false;
  if (Chart.defaults.transitions) {
    Chart.defaults.transitions.active = { animation: { duration: 0 } };
    Chart.defaults.transitions.resize = { animation: { duration: 0 } };
  }
  var n = 0;
  document.querySelectorAll('canvas').forEach(function (c) {
    var inst = Chart.getChart ? Chart.getChart(c) : null;
    if (!inst) return;
    inst.options.animation = false;
    inst.options.animations = false;
    inst.update('none');   // redraw synchronously, no tweening
    n++;
  });
  return n;
}
"""


def test_all_reports(
    reports_dir: Optional[str] = None,
    output_base: Optional[str] = None,
    screenshot: bool = False,
    seed: int = 42,
    verbose: bool = False,
    fail_fast: bool = False,
    save_baseline: bool = False,
    check_baseline: bool = False,
    baseline_threshold: float = 5.0,
) -> list[TestResult]:
    """Test every report in the reports/ directory.

    Args:
        reports_dir: Path to the reports directory (auto-detected if None).
        output_base: Base directory for test output (temp dir if None).
        screenshot: Take browser screenshots of each report.
        seed: Random seed for mock data.
        verbose: Print detailed progress.
        fail_fast: Stop on first failure.

    Returns:
        List of TestResult objects.
    """
    from trellum.report import BaseReport
    if reports_dir is None:
        from trellum.project import get_project_root
        reports_dir = os.path.join(get_project_root(), "reports")

    if output_base is None:
        output_base = tempfile.mkdtemp(prefix="fw_test_all_")

    entries = sorted(os.listdir(reports_dir))
    results: list[TestResult] = []

    print(f"\n{'=' * 64}", flush=True)
    print("  REPORT TEST SUITE", flush=True)
    print(f"  Reports dir: {reports_dir}", flush=True)
    print(f"  Output: {output_base}", flush=True)
    print(f"  Screenshots: {'yes' if screenshot else 'no'}", flush=True)
    print(f"{'=' * 64}\n", flush=True)

    for entry in entries:
        if entry.startswith("_"):
            continue
        report_dir = os.path.join(reports_dir, entry)
        yaml_path = os.path.join(report_dir, "report.yaml")
        if not os.path.isfile(yaml_path):
            continue
        config = BaseReport.load_config(report_dir)
        source = "content.md" if config.get("kind", "report") == "analysis" else "generator.py"
        if not os.path.isfile(os.path.join(report_dir, source)):
            continue

        slug = entry
        out_dir = os.path.join(output_base, slug)

        print(f"  Testing {slug}...", end="", flush=True)
        r = test_report(
            report_dir,
            output_dir=out_dir,
            screenshot=screenshot,
            seed=seed,
            verbose=verbose,
            save_baseline=save_baseline,
            check_baseline=check_baseline,
            baseline_threshold=baseline_threshold,
        )
        results.append(r)

        status = "PASS" if r.passed else "FAIL"
        extra = ""
        if r.error:
            extra = f" ({r.error[:60]})"
        elif r.validation_fails:
            extra = f" (val_fail={r.validation_fails})"
        print(f" {status} ({r.elapsed:.1f}s){extra}", flush=True)

        if fail_fast and not r.passed:
            print("\n  Stopping (--fail-fast)", flush=True)
            break

    _print_summary(results, output_base)
    return results


def _print_summary(results: list[TestResult], output_base: str) -> None:
    """Print a summary table of all test results."""
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    print(f"\n{'=' * 64}", flush=True)
    print("  TEST SUMMARY", flush=True)
    print(f"{'=' * 64}", flush=True)

    for r in results:
        print(f"  {r.summary_line()}", flush=True)

    print(f"{'─' * 64}", flush=True)
    print(f"  {passed} passed, {failed} failed, {total} total", flush=True)
    print(f"  Output: {output_base}", flush=True)
    print(f"{'=' * 64}\n", flush=True)
