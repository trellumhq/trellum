"""Last-period KPIs use the retained rows, including on restored filters."""

import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest

from trellum.assets import load_js
from trellum.components import RenderContext
from trellum.metrics import Dataset, invalidate_metrics_cache
from trellum.metrics_report import _block
from trellum.project import get_project_root, set_project_root
from trellum.themes import DefaultTheme


ROWS = [
    {"day": "2026-10-03", "platform": "ios", "region": "eu", "balance": 30},
    {"day": "2026-10-01", "platform": "ios", "region": "eu", "balance": 10},
    {"day": "2026-10-03", "platform": "android", "region": "eu", "balance": -8},
    {"day": "2026-10-02", "platform": "ios", "region": "eu", "balance": 20},
    {"day": "2026-10-03", "platform": "ios", "region": "us", "balance": 7},
    {"day": None, "platform": "ios", "region": "eu", "balance": 999},
    {"day": "2026-10-03", "platform": "ios", "region": "eu", "balance": None},
]


def generated_config(tmp_path, agg="sum"):
    """Exercise the registry, generated metric block and component expansion."""
    (tmp_path / "metrics.yaml").write_text(f"""version: 1
datasets:
  balances:
    provider: fixture:balances
    time: {{column: day, grain: day}}
    dimensions: [platform, region]
metrics:
  - name: balance
    label: Balance
    description: Latest balance across retained platform and region rows on the final date.
    agg: {agg}
    column: balance
    dataset: balances
    time_agg: last
""")
    original = get_project_root()
    set_project_root(str(tmp_path))
    invalidate_metrics_cache()
    try:
        from trellum.metrics import load_metrics

        metric = load_metrics()["balance"]
        ds = Dataset(name="balances", time_column="day", grain="day")
        ctx = RenderContext(theme=DefaultTheme)
        _block(metric, pd.DataFrame(ROWS), ds).render_html(ctx)
        return next((cid, cfg) for cid, cfg in ctx.component_data.items()
                    if cfg["type"] == "kpi_row_live")
    finally:
        set_project_root(original)
        invalidate_metrics_cache()


def run_runtime(cid, cfg, script):
    runtime = "\n".join(load_js(path) for path in (
        "runtime/aggregate.js", "runtime/filter_engine.js", "runtime/live_wrap.js",
        "components/kpi_row.js"))
    result = subprocess.run(["node", "-e", """
const assert = require('node:assert/strict');
global.window = global;
window._fwRenderers = {};
window.addEventListener = function() {};
window.getFormatter = function() { return v => String(v); };
const elements = {};
global.document = {getElementById: id => elements[id] ||= {innerHTML: ''}};
""" + runtime + "\nconst cid = " + json.dumps(cid) + ";\nconst cfg = "
        + json.dumps(cfg) + ";\nconst rows = " + json.dumps(ROWS) + ";\n" + script],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_generated_last_kpi_changes_and_restores_filters(tmp_path):
    cid, cfg = generated_config(tmp_path)
    run_runtime(cid, cfg, """
const engine = window._fwFilterEngine;
engine.init('balances', rows);
const draw = () => window._fwRenderers.kpi_row_live(cid, cfg);
const value = () => /fw-kpi-value">([^<]+)/.exec(elements[cid + '_k0'].innerHTML)[1];
draw();
assert.equal(value(), '29');
engine.setFilter('balances', 'dates', 'day', 'range', {min: '2026-10-01', max: '2026-10-02'});
assert.equal(value(), '20');
engine.setFilter('balances', 'platform', 'platform', 'in', ['android']);
assert.equal(value(), '0');
engine.clearFilter('balances', 'dates');
assert.equal(value(), '-8');
engine.setFilter('balances', 'platform', 'platform', 'in', ['ios']);
engine.setFilter('balances', 'region', 'region', 'in', ['eu']);
assert.equal(value(), '30');
// Restored predicates exist before the first render, as at report startup.
engine.removeDataset('balances');
engine.init('balances', rows);
engine.setFilter('balances', 'dates', 'day', 'range', {min: '2026-10-01', max: '2026-10-02'});
engine.setFilter('balances', 'platform', 'platform', 'in', ['ios']);
draw();
assert.equal(value(), '20');
engine.clearAllFilters('balances');
assert.equal(value(), '29');
""")


@pytest.mark.parametrize("agg, expected", [("sum", 29), ("abssum", 45)])
def test_last_null_empty_and_missing_axis(tmp_path, agg, expected):
    cid, cfg = generated_config(tmp_path, agg)
    assert cfg["kpis"][0]["agg"] == "last_by_date"
    assert cfg["kpis"][0]["date_col"] == "day"
    assert "value" not in cfg["kpis"][0]
    run_runtime(cid, cfg, """
const engine = window._fwFilterEngine;
const value = () => /fw-kpi-value">([^<]+)/.exec(elements[cid + '_k0'].innerHTML)[1];
const cases = [
    [rows, EXPECTED],
    [[], 0],
    [[{day: null, balance: 123}, {day: '', balance: 456}], 0],
    [[{day: '2026-10-01', balance: 99}, {day: '2026-10-02', balance: null}], 0],
    [[{event_date: '2026-10-01', balance: 123}], 0],
    [[{day: '2026-10-03 12:00', balance: 3}, {day: '2026-10-03 09:00', balance: 8},
      {day: '2026-10-03 12:00', balance: 4}], 7]
];
for (const [data, expected] of cases) {
    engine.removeDataset('balances');
    engine.init('balances', data);
    window._fwRenderers.kpi_row_live(cid, cfg);
    assert.equal(value(), String(expected));
}
""".replace("EXPECTED", str(expected)))


def test_unrelated_inline_aggregations_still_use_the_window():
    cfg = {"dataset_id": "balances", "kpis": [
        {"agg": "sum", "column": "balance"},
        {"agg": "avg_by_date", "column": "balance", "date_col": "day"},
        {"agg": "ratio", "numerator": "balance", "denominator": "users"},
        {"value": 42},
    ]}
    run_runtime("row", cfg, """
const engine = window._fwFilterEngine;
engine.init('balances', [{day:'2026-10-01', balance:10, users:2},
                         {day:'2026-10-02', balance:20, users:3}]);
window._fwRenderers.kpi_row_live(cid, cfg);
const values = () => cfg.kpis.map((k, i) => /fw-kpi-value">([^<]+)/.exec(elements[cid + '_k' + i].innerHTML)[1]);
assert.deepEqual(values(), ['30', '15', '6', '42']);
engine.setFilter('balances', 'dates', 'day', 'range', {max:'2026-10-01'});
assert.deepEqual(values(), ['10', '10', '5', '42']);
""")


def test_generated_metrics_page_date_changes_and_url_restore(tmp_path):
    """The shipped generator and browser initialization share the live contract."""
    from playwright.sync_api import expect, sync_playwright
    from trellum.components import FilterBar
    from trellum.rendering.html_builder import render_report
    from trellum.rendering.cdn import copy_vendor_tree
    from trellum.report import ReportContext
    from trellum.metrics_report import MetricsReport
    from trellum.validation import validate_report

    generated_config(tmp_path)
    original = get_project_root()
    set_project_root(str(tmp_path))
    invalidate_metrics_cache()
    output = tmp_path / "output"
    frame = pd.DataFrame([r for r in ROWS if r["day"]])
    try:
        ctx = ReportContext(config={"name": "Balances", "studio": "test", "category": "Metrics", "description":
                            "Latest balances by platform and region, with shared dates and retained dimension rows."},
                            slug="balances", output_dir=str(output))
        ctx.metrics = lambda ids, by=None: frame
        MetricsReport().generate(ctx)
        # A regular report can add supported dimensions to its own FilterBar.
        for section in ctx.sections:
            for component in section["components"]:
                if isinstance(component, FilterBar):
                    component.filters += [{"column": "platform", "type": "toggle"},
                                          {"column": "region", "type": "toggle"}]
        validation = validate_report(ctx)
        assert not [c for c in validation.checks if c.level in ("fail", "warn")], [
            (c.id, c.message) for c in validation.checks]
        render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
        data = json.loads((output / "data.json").read_text())
        live = next(c for c in data["components"].values() if c.get("type") == "kpi_row_live")
        assert live["kpis"][0]["agg"] == "last_by_date"
        assert "value" not in live["kpis"][0]
    finally:
        set_project_root(original)
        invalidate_metrics_cache()

    handler = partial(SimpleHTTPRequestHandler, directory=str(output))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            url = f"http://127.0.0.1:{server.server_port}/index.html"
            page.goto(url, wait_until="networkidle")
            value = page.locator("#metric-balance .fw-kpi-value")
            expect(value).to_be_visible()
            expect(value).to_have_text("29")
            page.locator(".fw-date-trigger").click()
            end = page.locator(".fw-date-input").nth(1)
            end.fill("2026-10-02")
            end.dispatch_event("change")
            expect(value).to_have_text("20")
            restored_url = page.url
            page.reload()
            expect(value).to_have_text("20")
            assert page.url == restored_url
            # Shareable dimension predicates are restored by real FilterBar init.
            page.goto(url + "?day=2026-10-01..2026-10-03&platform=ios&region=eu")
            expect(value).to_have_text("30")
            page.get_by_role("button", name="us", exact=True).click()
            expect(value).to_have_text("7")
            page.get_by_role("button", name="eu", exact=True).click()
            page.get_by_role("button", name="android", exact=True).click()
            expect(value).to_have_text("-8")
            page.goto(url + "?day=2026-10-01..2026-10-02&platform=android")
            expect(value).to_have_text("0.00")
            page.goto(url + "?only=metric-balance&day=2026-10-01..2026-10-02&platform=ios")
            expect(value).to_be_visible()
            expect(value).to_have_text("20")
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
