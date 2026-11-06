"""Optional totals: strict declarations, exact decimal inputs and native paint."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
import base64
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import types
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import ComparisonTable, DataTable
from trellum.components._table_totals import typed_inputs
from trellum.components.base import RenderContext
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "092f6c1db124c0d58b47923f6e957040595b0046"


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    html = table.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def _sum(column="n", **kwargs):
    return {column: {"agg": "sum", **kwargs}}


def _frame(values):
    return pd.DataFrame({"Label": [f"Row {i}" for i in range(len(values))],
                         "n": pd.Series(values, dtype=object)})


def _examples():
    spec = importlib.util.spec_from_file_location("examples_table_totals", ROOT / "trellum/demo/reports/component-showcase/examples_table_totals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_copies_nested_declarations_and_preserves_constructor_positions_and_binding():
    @dataclass
    class Extended(DataTable):
        caption: str = "caption"

    frame = _frame([0, Decimal("1.00000000000001"), 2**53 + 1])
    args = (frame, "Table", 1, True, None, None, True, ["Label", "n"], {}, True, None, "positional")
    original = Extended(*args)
    assert original.caption == "positional"
    for value in (None, {}):
        assert _register(Extended(*args, totals=value)) == _register(original)
    spec = _sum()
    table = Extended(*args, totals=spec)
    spec["n"]["agg"] = "callback"
    assert _register(table)[1]["totals"]["n"] == {"agg": "sum", "format": "number", "precision": 2}
    assert _register(table)[1]["totalsInputs"] == {"n": ["0", "1.00000000000001", "9007199254740993"]}
    cfg = _register(table)[1]
    cfg["totals"]["n"]["precision"] = 6
    assert table.totals["n"]["precision"] == 2
    table.totals["n"]["precision"] = True
    with pytest.raises(ValueError, match="totals"):
        _register(table)
    table.totals = _sum()
    table.dataset_id = "explicit"
    cfg = _register(table)[1]
    assert cfg["dataset_id"] == "explicit" and "totalsInputs" not in cfg and "rows" not in cfg
    for columns in (None, []):
        assert _register(DataTable(frame, "Fallback", columns=columns, totals=_sum()))[1]["columns"] == ["Label", "n"]
    with pytest.raises(TypeError, match="totals"):
        ComparisonTable(frame, "Compare", "n", [], totals=_sum())
    assert "totals" not in _register(ComparisonTable(frame, "Compare", "n", []))[1]


@pytest.mark.parametrize("option", [
    False, [], "", lambda: {}, {"n": None}, {"n": {}},
    {"Label": {"agg": "sum"}}, {"unknown": {"agg": "sum"}}, {1: {"agg": "sum"}},
    *[{"n": {"agg": value}} for value in (None, True, [], {}, "mean", lambda: 1)],
    *[_sum(precision=value) for value in (True, False, -1, 7, 1.0, "2", None, [])],
    *[_sum(format=value) for value in ("percent", "ratio", "unknown", None, [], lambda: 1)],
    _sum(extra=1),
    {"n": {"agg": "ratio"}}, {"n": {"agg": "ratio", "numerator": "n"}},
    *[{"n": {"agg": "ratio", "numerator": value, "denominator": "n"}}
      for value in ("", "unknown", None, 2, [], lambda: 1)],
    *[{"n": {"agg": "ratio", "numerator": "n", "denominator": value}}
      for value in ("", "unknown", None, False)],
])
def test_invalid_options_reject_at_construction_and_render(option):
    frame = _frame([1])
    with pytest.raises(ValueError, match="totals"):
        DataTable(frame, "Invalid", totals=option)
    table = DataTable(frame, "Invalid")
    table.totals = option
    with pytest.raises(ValueError, match="totals"):
        _register(table)


def test_hidden_components_and_duplicate_indexes_only_add_typed_payload():
    frame = pd.DataFrame({"Label": ["a", "b"], "Rate": [3, 1],
                          "Revenue": [Decimal("30.03"), Decimal("90.09")], "Spend": [Decimal("10.01"), Decimal("90.09")]}, index=[4, 4])
    options = {"columns": ["Label", "Rate"], "static": True}
    plain = _register(DataTable(frame, "Projection", **options))[1]
    on = _register(DataTable(frame, "Projection", totals={"Rate": {
        "agg": "ratio", "numerator": "Revenue", "denominator": "Spend"}}, **options))[1]
    assert {k: v for k, v in on.items() if k not in ("totals", "totalsInputs")} == plain
    assert on["totalsInputs"] == {"Revenue": ["30.03", "90.09"], "Spend": ["10.01", "90.09"]}
    with pytest.raises(ValueError, match="displayed"):
        DataTable(frame, "Hidden sum", totals=_sum("Revenue"), **options)


@pytest.fixture(scope="module")
def native_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def arithmetic_page(native_browser):
    page = native_browser.new_page()
    page.add_script_tag(path=str(ROOT / "trellum/static/js/components/data_table_totals.js"))
    yield page
    page.close()


def _calculate(page, frame, spec):
    # Use the production typed-input encoder, independently of display/CSV.
    cfg = {"totals": spec, "totalsInputs": typed_inputs(frame, spec)}
    return page.evaluate("cfg => window._fwTableTotals(cfg, Array.from({length:Object.values(cfg.totalsInputs)[0].length}, (_,index)=>({index})))", cfg)


def _oracle(values, precision, fmt="number", denominator=None):
    with localcontext() as ctx:
        ctx.prec = 4096
        value = sum((Decimal(str(v)) for v in values), Decimal(0))
        if denominator is not None:
            value /= sum((Decimal(str(v)) for v in denominator), Decimal(0))
        if fmt == "percent":
            value *= 100
        value = value.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
        if not value:
            value = abs(value)
        sign = "-" if value < 0 else ""
        text = format(abs(value), f"{',' if fmt != 'plain' else ''}.{precision}f")
        return sign + ("$" if fmt == "currency" else "") + text + {"percent": "%", "ratio": "x"}.get(fmt, "")


@pytest.mark.parametrize("values", [
    [0, 0], [Decimal("0.1"), Decimal("0.2")], [2**53 + 1, 2**53 + 3],
    [Decimal("1.005")], [Decimal("-1.005")], [Decimal("-0.0005")],
    [Decimal("999999999999999.999999"), Decimal("-999999999999999.999998")],
    [Decimal("1E-1000"), Decimal("-1E-1000")], [-4, 2, 0],
])
@pytest.mark.parametrize("fmt", ["number", "currency", "plain"])
def test_additive_decimal_oracle(arithmetic_page, values, fmt):
    for precision in (0, 2, 6):
        result = _calculate(arithmetic_page, _frame(values), {"n": {"agg": "sum", "format": fmt, "precision": precision}})
        assert result["n"] == {"text": _oracle(values, precision, fmt)}


@pytest.mark.parametrize("fmt", ["number", "currency", "plain", "percent", "ratio"])
def test_weighted_ratios_use_exact_components_and_scaling(arithmetic_page, fmt):
    for numerator, denominator in [
        ([Decimal("30.03"), Decimal("90.09"), 0], [Decimal("10.01"), Decimal("90.09"), 0]),
        ([Decimal("-1.005")], [1]), ([1, 0], [3, 3]),
        ([2**53 + 1, 2**53 + 3], [2**53 + 7, 0]),
        ([Decimal("-0.00000001")], [100]),
    ]:
        frame = pd.DataFrame({"n": numerator, "d": denominator})
        for precision in (0, 1, 2, 6):
            result = _calculate(arithmetic_page, frame, {"rate": {"agg": "ratio", "numerator": "n", "denominator": "d", "format": fmt, "precision": precision}})
            assert result["rate"] == {"text": _oracle(numerator, precision, fmt, denominator)}


def test_random_cancellation_and_rounding_against_independent_decimal(arithmetic_page):
    rng = random.Random(292)
    for _ in range(80):
        values = [Decimal(rng.randint(-10**20, 10**20)).scaleb(-rng.randint(0, 12)) for _ in range(rng.randint(1, 30))]
        precision = rng.randint(0, 6)
        result = _calculate(arithmetic_page, _frame(values), {"n": {"agg": "sum", "precision": precision, "format": "plain"}})
        assert result["n"] == {"text": _oracle(values, precision, "plain")}


@pytest.mark.parametrize("invalid", [None, pd.NA, float("nan"), float("inf"), float("-inf"),
                                     Decimal("NaN"), Decimal("Infinity"), True, False, "2", "1.25", [1], {"v": 1}])
def test_incomplete_inputs_withhold_only_their_own_metric(arithmetic_page, invalid):
    frame = _frame([0, invalid])
    frame["valid"] = [0, 0]
    specs = {column: {"agg": "sum", "format": "number", "precision": 2} for column in ("n", "valid")}
    result = _calculate(arithmetic_page, frame, specs)
    assert result["n"]["text"] == "—" and "Complete finite" in result["n"]["reason"]
    assert result["valid"] == {"text": "0.00"}


@pytest.mark.parametrize("denominator", [[0, 0], [-2, 1], [0, None]])
def test_ratio_denominator_requires_complete_positive_sum(arithmetic_page, denominator):
    frame = pd.DataFrame({"n": [1, 2], "d": denominator})
    result = _calculate(arithmetic_page, frame, {"rate": {"agg": "ratio", "numerator": "n", "denominator": "d", "format": "ratio", "precision": 2}})
    assert result["rate"]["text"] == "—"
    assert ("positive" if None not in denominator else "Complete finite") in result["rate"]["reason"]


def test_bounded_inputs_and_intermediate_overflow(arithmetic_page):
    spec = {"n": {"agg": "sum", "format": "plain", "precision": 0}}
    for values in ([Decimal("1E+1001")], [Decimal("1E-1001")], [10**1000],
                   [Decimal("1E+999999999999999999")],
                   [Decimal("9" * 1000 + "E+1000"), Decimal("1E-1000")]):
        assert _calculate(arithmetic_page, _frame(values), spec)["n"] == {"text": "—", "reason": "Numeric range is unsupported."}
    for values in ([Decimal("1E+1000")], [Decimal("1E-1000")], [10**999], [Decimal("1E+1000"), Decimal("1E-1000")]):
        assert _calculate(arithmetic_page, _frame(values), spec)["n"] == {"text": _oracle(values, 0, "plain")}
    # Division scaling is bounded too, without allocating an excessive power.
    frame = pd.DataFrame({"n": [Decimal("9" * 1000 + "E+1000")], "d": [Decimal("1E-1000")]})
    result = _calculate(arithmetic_page, frame, {"rate": {"agg": "ratio", "numerator": "n", "denominator": "d", "format": "plain", "precision": 6}})
    assert result["rate"]["reason"] == "Numeric range is unsupported."


def test_bound_provenance_scope_ancestor_walk_and_no_bigint(arithmetic_page):
    page = arithmetic_page
    results = page.evaluate("""() => {
      const cfg={dataset_id:'child',totals:{n:{agg:'sum',format:'number',precision:2}}};
      const row=v=>[{index:0,totalsRecord:{n:v}}];
      window._fwFilterEngine={_scopes:{child:{parent:'root'}},isLiveDataset:()=>false,isReady:()=>true};
      const result=[0,-2.5,9007199254740992,'2',true,null,Infinity,NaN].map(v=>_fwTableTotals(cfg,row(v)).n);
      _fwFilterEngine.isLiveDataset=id=>id==='root';
      result.push(_fwTableTotals(cfg,row(2)).n);
      _fwFilterEngine.isLiveDataset=()=>false; _fwFilterEngine.isReady=()=>false;
      result.push(_fwTableTotals(cfg,row(2)).n);
      _fwFilterEngine._scopes.root={parent:'child'};
      result.push(_fwTableTotals(cfg,row(2)).n);
      delete _fwFilterEngine._scopes.root; _fwFilterEngine.isReady=()=>true;
      const native=window.BigInt;window.BigInt=undefined;
      result.push(_fwTableTotals(cfg,row(2)).n);window.BigInt=native;
      return result;
    }""")
    assert results[:2] == [{"text": "0.00"}, {"text": "-2.50"}]
    assert "unsafe" in results[2]["reason"]
    assert all("Complete finite" in r["reason"] for r in results[3:8])
    assert results[8]["reason"] == "Live-query total scope is not declared."
    assert "until the source is ready" in results[9]["reason"]
    assert "scope is unsupported" in results[10]["reason"]
    assert "unavailable in this browser" in results[11]["reason"]


def _context(path):
    return ReportContext({"name": "Table totals", "studio": "test", "category": "Components",
                          "description": "Synthetic distinct campaign and event contributions show exact additive spend, installs, revenue and weighted ROAS over matching platform observations."},
                         "table-totals", str(path))


def test_native_module_build_full_grain_validation(tmp_path):
    ctx = _context(tmp_path)
    _examples().add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
    render_report(ctx, auto_refresh=False)
    assert (tmp_path / "data.json").is_file()


@pytest.mark.parametrize("scoped", [False, True])
def test_validation_checks_hidden_ratio_inputs_on_actual_source(scoped):
    from trellum.components import DataSource, ScopedDataSource
    from trellum.validation.checks.columns import _check_columns
    from trellum.validation.result import ValidationResult

    frame = pd.DataFrame({"Label": ["a"], "Rate": [2], "Revenue": [20], "Spend": [10]})
    sources = {"root": DataSource("root", frame.drop(columns="Spend"))}
    if scoped:
        sources["child"] = ScopedDataSource("child", parent="root")
    table = DataTable(frame, "Declared inputs", columns=["Label", "Rate"],
                      dataset_id="child" if scoped else "root", totals={"Rate": {
                          "agg": "ratio", "numerator": "Revenue", "denominator": "Spend"}})
    result = ValidationResult(slug="table-totals")
    _check_columns([(table, "Totals")], sources, result)
    assert result.summary["fail"] == 1
    assert "datatable-totals-column-missing" in str(result.to_dict())
    assert "Spend" in str(result.to_dict())
    sources["root"] = DataSource("root", frame)
    result = ValidationResult(slug="table-totals")
    _check_columns([(table, "Totals")], sources, result)
    assert result.summary["fail"] == result.summary["warn"] == 0


def test_validation_reports_mutated_total_declaration():
    from trellum.validation.checks.columns import _check_columns
    from trellum.validation.result import ValidationResult

    table = DataTable(_frame([1]), "Mutation", static=True, totals=_sum())
    table.totals["n"]["precision"] = True
    result = ValidationResult(slug="table-totals")
    _check_columns([(table, "Totals")], {}, result)
    assert result.summary["fail"] == 1
    assert "datatable-totals-invalid" in str(result.to_dict())


@pytest.fixture
def totals_page(tmp_path, native_browser):
    ctx = _context(tmp_path / "current")
    _examples().add_sections(ctx)
    frame = pd.DataFrame({"Label": ["a", "b"], "Rate": [3, 1], "Hidden numerator": [30, 90], "Hidden denominator": [10, 90]})
    ctx.add_section("Hidden ratio inputs", [DataTable(frame, "Hidden components", static=True,
        columns=["Label", "Rate"], searchable=True, sortable=True, totals={"Rate": {
            "agg": "ratio", "numerator": "Hidden numerator", "denominator": "Hidden denominator", "format": "ratio"}})])
    ctx.add_section("Bounded totals capture", [DataTable(_examples().campaign_frame(),
        "Three matching, three displayed", static=True, max_rows=3, totals=_examples().declarations())])
    render_report(ctx, auto_refresh=False)
    data = json.loads((tmp_path / "current/data.json").read_text())
    ids = [cid for cid, cfg in data["components"].items() if cfg["type"] == "table"]
    page = native_browser.new_page(viewport={"width": 1280, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    def fulfill(route):
        url = urlsplit(route.request.url)
        assert url.hostname == "totals.test", route.request.url
        path = unquote(url.path).lstrip("/")
        file = ROOT / "trellum/static/vendor" / path[len("_vendor/"):] if path.startswith("_vendor/") else tmp_path / path
        route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")
    page.route("**/*", fulfill)
    page.goto("http://totals.test/current/index.html", wait_until="networkidle")
    for cid in ids:
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        page.locator(f"#{cid} tbody tr").first.wait_for()
    page.evaluate("window.csvCalls=[];window._fwDownloadCsv=(columns,rows)=>csvCalls.push({columns,rows})")
    yield page, ids, data, tmp_path
    assert not errors
    page.close()


def _footer(page, cid):
    return page.locator(f"#{cid} tfoot tr").inner_text().split("\t")


def _export(page, cid):
    page.locator(f'[data-csv-target="{cid}"]').click()
    return page.evaluate("csvCalls.at(-1)")


def test_native_controls_full_population_sort_search_csv_and_independent_kpi(totals_page):
    from playwright.sync_api import expect
    page, ids, data, _ = totals_page
    plain, exact, bound, incomplete, hidden, _ = ids
    assert page.locator(f"#{plain} tfoot").count() == 0
    assert _footer(page, exact) == ["Total", "$100.10", "30", "$120.12", "1.20x", "120.0%"]
    assert _export(page, plain) == _export(page, exact)
    assert _footer(page, incomplete) == ["Total", "$0.00", "4", "—", "—", "—"]
    assert "Revenue: Complete finite" in page.locator(f"#{incomplete}-totals-note").inner_text()
    assert "Recovery: The summed denominator must be positive." in page.locator(f"#{incomplete}-totals-note").inner_text()
    initial = _footer(page, bound)
    assert initial == ["Total", "", "$666.00", "105", "$876.00", "1.32x"]
    kpi = next(cid for cid, cfg in data["components"].items() if cfg["type"] == "kpi_row_live")
    before = page.locator(f"#{kpi}").inner_text()
    writes = page.evaluate("""() => {const e=window._fwFilterEngine;window.totalFilterWrites=0;
      const set=e.setFilter; e.setFilter=function(...args){window.totalFilterWrites++;return set.apply(this,args)};return totalFilterWrites;}""")
    assert writes == 0
    page.locator(f'#{bound} th[data-col-idx="2"]').click()
    assert "Contributor 36" in page.locator(f"#{bound} tbody tr").first.inner_text()
    assert _footer(page, bound) == initial
    assert len(_export(page, bound)["rows"]) == 36
    selector = page.locator(f'[data-table-row-limit="{bound}"]')
    selector.select_option("all")
    assert selector.evaluate("e=>e===document.activeElement")
    assert page.locator(f"#{bound} tbody tr").count() == 36
    assert _footer(page, bound) == initial
    selector.select_option("10")
    search = page.locator(f'[data-table-search="{bound}"]')
    search.fill("Contributor 35")
    expect(page.locator(f"#{bound} tbody tr")).to_have_count(1)
    assert _footer(page, bound) == ["Total", "", "$35.00", "6", "$35.00", "1.00x"]
    assert search.evaluate("e=>e===document.activeElement")
    exported = _export(page, bound)
    assert exported["rows"] == [["Contributor 35", "iOS", 35, 6, 35, 1]]
    assert "Total" not in str(exported)
    assert page.locator(f"#{kpi}").inner_text() == before
    assert page.evaluate("totalFilterWrites") == 0
    search.fill("no such contributor")
    expect(page.locator(f"#{bound} tbody tr")).to_have_count(0)
    assert page.locator(f"#{bound} tfoot, #{bound}-totals-note").count() == 0
    assert not page.locator(f"#{bound} table").get_attribute("aria-describedby")
    search.fill("")
    expect(page.locator(f"#{bound} tbody tr")).to_have_count(10)
    toggle = page.locator('[data-fw-ds-id="table_totals_events"] .fw-toggle-btn').filter(has_text="iOS")
    toggle.click()
    expect(page.locator(f"#{bound}-totals-note")).to_contain_text("18 matching rows")
    assert _footer(page, bound) != initial
    assert page.locator(f"#{kpi}").inner_text() != before
    assert _footer(page, hidden) == ["Total", "1.20x"]
    assert _export(page, hidden) == {"columns": ["Label", "Rate"], "rows": [["a", 3], ["b", 1]]}
    hidden_search = page.locator(f'[data-table-search="{hidden}"]')
    hidden_search.fill("90")
    expect(page.locator(f"#{hidden} tbody tr")).to_have_count(0)
    assert _export(page, hidden)["rows"] == []


def test_redraw_cleanup_replacement_focus_workspace_exclusion_and_retained_delivery(totals_page):
    from playwright.sync_api import expect
    page, ids, data, _ = totals_page
    _, exact, bound, _, hidden, _ = ids
    selector = page.locator(f'[data-table-row-limit="{bound}"]')
    selector.select_option("20")
    search = page.locator(f'[data-table-search="{bound}"]')
    search.fill("Contributor 3")
    expect(page.locator(f"#{bound} tbody tr")).to_have_count(7)
    search.focus()
    page.evaluate("renderAll()")
    assert search.evaluate("e=>document.activeElement===e")
    assert selector.input_value() == "20"
    assert _footer(page, bound)[2] == "$231.00"
    for theme in ("trellum dark", "trellum light"):
        page.locator("#fwThemeSelect").select_option(theme)
        assert page.locator(f"#{exact} tfoot").count() == 1
        assert page.locator(f"#{exact}-totals-note").count() == 1
    page.evaluate("""id => { const el=document.getElementById(id);el.replaceWith(el.cloneNode(false));
        _fwRenderers.table(id,_reportData.components[id]); }""", hidden)
    assert page.locator(f"#{hidden}-totals-note").count() == 1
    page.evaluate("""id=>{const cfg={..._reportData.components[id], totals:{}};_fwRenderers.table(id,cfg)}""", hidden)
    assert page.locator(f"#{hidden} tfoot, #{hidden}-totals-note").count() == 0
    assert page.locator(f"#{hidden} table").get_attribute("aria-describedby") is None
    page.evaluate("""id=>{window._fwMetricsWorkspace={definitionId:id};window._fwMetricsCatalog=[];
      _fwRenderers.table(id,{..._reportData.components[id],workspacePageSize:25});}""", exact)
    assert page.locator(f"#{exact} tfoot, #{exact}-totals-note").count() == 0
    page.evaluate("delete window._fwMetricsWorkspace")
    search.fill("")
    expect(page.locator(f"#{bound} tbody tr")).to_have_count(20)
    # A delayed delivery keeps body/CSV from the previous completed draw;
    # totals use that same draw and cannot accidentally read newer engine data.
    page.evaluate("""id=>{window._fwLiveWrap=(id,cfg,draw)=>{window.deliverTotals=draw;draw([
      {Event:'Old',Platform:'iOS',Spend:10,Installs:0,Revenue:30,ROAS:3}])};
      _fwRenderers.table(id,_reportData.components[id]);}""", bound)
    assert _footer(page, bound)[2] == "$10.00"
    assert _export(page, bound)["rows"][0][0] == "Old"
    page.evaluate("deliverTotals([{Event:'New',Platform:'iOS',Spend:0,Installs:0,Revenue:0,ROAS:null}])")
    assert _footer(page, bound)[2:5] == ["$0.00", "0", "$0.00"]
    assert _export(page, bound)["rows"][0][0] == "New"
    # Generation guard rejects an older renderer's eventual result.
    page.evaluate("() => { window.staleTotals=deliverTotals; }")
    page.evaluate("id=>_fwRenderers.table(id,_reportData.components[id])", bound)
    page.evaluate("staleTotals([{Event:'STALE',Spend:999}])")
    assert _export(page, bound)["rows"][0][0] == "Old"
    page.evaluate("""id=>{_fwLiveWrap=()=>{};_fwRenderers.table(id,{..._reportData.components[id],totals:null});}""", bound)
    assert page.locator(f"#{bound} tfoot, #{bound}-totals-note").count() == 0
    assert page.locator(f"#{bound} tbody tr").count() == 1


def _evidence(tmp):
    path = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", tmp / "evidence"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paint(cell):
    return cell.evaluate("""el=>{const s=getComputedStyle(el), c=document.createElement('canvas').getContext('2d');
      function rgb(color){c.clearRect(0,0,1,1);c.fillStyle=color;c.fillRect(0,0,1,1);return Array.from(c.getImageData(0,0,1,1).data).slice(0,3)}
      function lum(color){return rgb(color).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0)}
      const a=lum(s.color), b=lum(s.getPropertyValue('--bg-card'));
      return {height:el.getBoundingClientRect().height,padding:s.padding,background:s.backgroundColor,
        color:s.color,align:s.textAlign,contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};}""")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_paint_phone_horizontal_scroll_and_section_capture(totals_page, width):
    from PIL import Image
    page, ids, _, tmp = totals_page
    plain, exact, _, incomplete, _, bounded = ids
    page.set_viewport_size({"width": width, "height": 1000})
    evidence = _evidence(tmp)
    for theme in ("trellum light", "trellum dark"):
        page.locator("#fwThemeSelect").select_option(theme)
        page.evaluate("document.fonts.ready")
        page.mouse.move(0, 0)
        for row in range(3):
            off = _paint(page.locator(f"#{plain} tbody tr").nth(row).locator("td").nth(1))
            on = _paint(page.locator(f"#{exact} tbody tr").nth(row).locator("td").nth(1))
            assert {k: v for k, v in on.items() if k != "contrast"} == {k: v for k, v in off.items() if k != "contrast"}
        footer = page.locator(f"#{exact} tfoot td").first
        footer.scroll_into_view_if_needed()
        style = _paint(footer)
        assert style["align"] == "right" and style["contrast"] >= 4.5
        assert page.locator(f"#{exact} tfoot th").get_attribute("scope") == "row"
        assert page.locator(f"#{exact} table").get_attribute("aria-describedby") == f"{exact}-totals-note"
        section = page.locator(f"#{exact}").locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
        section.screenshot(path=str(evidence / f"totals-{width}-{theme.replace(' ', '-')}.png"), animations="disabled")
        reason = page.locator(f"#{incomplete}-totals-note")
        reason.scroll_into_view_if_needed()
        assert reason.evaluate("e=>e.scrollWidth<=e.clientWidth")
        assert reason.bounding_box()["width"] <= width
        reason.locator('xpath=ancestor::*[contains(@class,"fw-section")][1]').screenshot(
            path=str(evidence / f"totals-reasons-{width}-{theme.replace(' ', '-')}.png"), animations="disabled")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        if width == 390:
            scroller = page.locator(f"#{exact}")
            scroller.evaluate("e=>e.scrollLeft=e.scrollWidth")
            assert scroller.evaluate("e=>e.scrollLeft>0")
            section.screenshot(path=str(evidence / f"totals-phone-scroll-{theme.replace(' ', '-')}.png"), animations="disabled")
            # Reasons are outside the horizontal scroller and stay readable.
            assert reason.bounding_box()["x"] >= 0
            scroller.evaluate("e=>e.scrollLeft=0")
    section.scroll_into_view_if_needed()
    capture = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
    (evidence / f"totals-{width}-striped-native-section.png").write_bytes(
        base64.b64decode(capture["image"]["data_base64"]))
    section = page.locator(f"#{bounded}").locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
    section.scroll_into_view_if_needed()
    assert section.locator("tbody tr").count() == 3
    assert "3 matching rows" in section.locator(".fw-table-totals-note").inner_text()
    assert _footer(page, bounded)[1] == "$100.10"
    assert section.locator("tfoot").count() == section.locator(".fw-table-totals-note").count() == 1
    capture = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
    assert capture["format"] == "trellum-analysis-capture"
    path = evidence / f"totals-{width}-native-section.png"
    path.write_bytes(base64.b64decode(capture["image"]["data_base64"]))
    # Native capture hides the CSV button, collapsing its otherwise empty header.
    header_height = section.locator(".fw-data-table-header").bounding_box()["height"]
    assert Image.open(path).height >= int((section.bounding_box()["height"] - header_height) * 2) - 2
    (evidence / f"totals-{width}-capture-source.json").write_text(json.dumps(capture["source"], indent=2))


def _baseline_module():
    result = subprocess.run(["git", "show", f"{BASELINE}:trellum/components/tables.py"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        return None
    module = types.ModuleType("_table_totals_baseline")
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{BASELINE}/tables.py", "exec"), module.__dict__)
    for loader, suffix in (("load_css", "css"), ("load_js", "js")):
        real = getattr(module, loader)
        content = subprocess.check_output(["git", "show", f"{BASELINE}:trellum/static/{suffix}/components/data_table.{suffix}"], cwd=ROOT, text=True)
        setattr(module, loader, lambda name, real=real, content=content, suffix=suffix: content if name == f"components/data_table.{suffix}" else real(name))
    return module


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_default_off_paint_matches_actual_required_dependency(totals_page, width):
    page, _, _, tmp = totals_page
    page.set_viewport_size({"width": width, "height": 1000})
    baseline = _baseline_module()
    if baseline is None:
        pytest.skip("Required dependency object unavailable; current-runtime opt-out is not a historical baseline")
    frame = _examples().campaign_frame()
    captures = []
    evidence = _evidence(tmp)
    for name, cls in (("before", baseline.DataTable), ("after", DataTable)):
        ctx = _context(tmp / name)
        ctx.add_section("Default table", [cls(frame, "Campaigns · default", static=True)])
        render_report(ctx, auto_refresh=False)
        page.goto(f"http://totals.test/{name}/index.html", wait_until="networkidle")
        page.locator("#fwThemeSelect").select_option("trellum light")
        page.locator(".fw-data-table td").first.wait_for()
        page.evaluate("document.fonts.ready")
        page.mouse.move(0, 0)
        page.wait_for_function("getComputedStyle(document.querySelector('.fw-csv-btn')).color==='rgb(71, 85, 105)'")
        table = page.locator(".fw-table-scroll")
        assert page.locator("tfoot, .fw-table-totals-note").count() == 0
        geometry = table.evaluate("e=>Array.from(e.querySelectorAll('th,td')).map(c=>{const r=c.getBoundingClientRect();return [r.x,r.width,r.height,getComputedStyle(c).backgroundColor]})")
        page.locator(".fw-section").first.screenshot(path=str(evidence / f"default-{BASELINE[:7]}-{width}-{name}.png"), animations="disabled")
        captures.append((table.screenshot(animations="disabled"), geometry))
    assert captures[0] == captures[1]
    capture = page.locator(".fw-section").first.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
    assert capture["format"] == "trellum-analysis-capture"



def test_rendered_scope_withholding_precise_static_inputs_and_escaped_reasons(totals_page):
    page, ids, _, _ = totals_page
    _, _, bound, _, hidden, _ = ids
    # Snapshot provenance survives numeric-looking display strings and exact
    # static numbers; output reason text cannot introduce authored markup.
    name = '<img src=x onerror="window.badTotals=1">'
    frame = pd.DataFrame({"Label": ["one", "two"], "Exact": [2**53 + 1, 2**53 + 3],
                          "Decimal": [Decimal("0.10000000000000001"), Decimal("0.20000000000000002")],
                          name: ["2", True], "Zero": [0, 0]})
    cfg = _register(DataTable(frame, "Provenance", static=True, totals={
        "Exact": {"agg": "sum", "precision": 0}, "Decimal": {"agg": "sum", "precision": 6},
        name: {"agg": "sum"}, "Zero": {"agg": "sum"}}))[1]
    page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [hidden, cfg])
    assert _footer(page, hidden) == ["Total", "18,014,398,509,481,988", "0.300000", "—", "0.00"]
    assert name in page.locator(f"#{hidden}-totals-note").inner_text()
    assert page.locator(".fw-table-totals-note img").count() == 0
    assert page.evaluate("window.badTotals || 0") == 0
    for mode, reason in (("live", "Live-query total scope is not declared."),
                         ("not-ready", "until the source is ready"),
                         ("cycle", "scope is unsupported"),
                         ("unsafe", "integer precision is unsafe")):
        page.evaluate("""([id,mode])=>{
          const cfg={..._reportData.components[id],dataset_id:'totals-child'};
          window._fwFilterEngine={_scopes:{'totals-child':{parent:'totals-root'}},
            isLiveDataset:id=>mode==='live'&&id==='totals-root',isReady:()=>mode!=='not-ready'};
          if(mode==='cycle')_fwFilterEngine._scopes['totals-root']={parent:'totals-child'};
          _fwLiveWrap=(id,cfg,draw)=>draw([{Event:'Visible',Platform:'iOS',Spend:mode==='unsafe'?9007199254740992:10,
            Installs:0,Revenue:30,ROAS:3}]);_fwRenderers.table(id,cfg);
        }""", [bound, mode])
        assert "Visible" in page.locator(f"#{bound} tbody").inner_text()
        assert reason in page.locator(f"#{bound}-totals-note").inner_text()
        assert _export(page, bound)["rows"][0][0] == "Visible"
        if mode == "unsafe":
            assert _footer(page, bound)[2:5] == ["—", "0", "$30.00"]
        else:
            assert _footer(page, bound)[2:] == ["—"] * 4


def test_totals_snapshot_inputs_do_not_follow_later_record_mutation_or_sort_order(totals_page, arithmetic_page):
    page, ids, _, _ = totals_page
    bound = ids[2]
    page.evaluate("""id=>{
      window.deliveredRecord={Event:'Original',Platform:'iOS',Spend:10,Installs:0,Revenue:30,ROAS:3};
      _fwLiveWrap=(id,cfg,draw)=>draw([deliveredRecord]);_fwRenderers.table(id,_reportData.components[id]);
      deliveredRecord.Spend=999;deliveredRecord.Revenue=999;
    }""", bound)
    page.locator(f'[data-table-row-limit="{bound}"]').select_option("all")
    assert _footer(page, bound)[2:] == ["$10.00", "0", "$30.00", "3.00x"]
    assert _export(page, bound)["rows"] == [["Original", "iOS", 10, 0, 30, 3]]
    values = [Decimal("9" * 1000 + "E+1000"), Decimal("-" + "9" * 1000 + "E+1000"), Decimal("1E-1000")]
    spec = {"n": {"agg": "sum", "format": "plain", "precision": 2}}
    for ordering in (values, values[::-1], [values[0], values[2], values[1]]):
        assert _calculate(arithmetic_page, _frame(ordering), spec)["n"] == {
            "text": "—", "reason": "Numeric range is unsupported."}
