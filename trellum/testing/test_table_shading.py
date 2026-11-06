"""Exact author targets share the native table population and painted cells."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from urllib.parse import unquote, urlsplit

import pandas as pd
import pytest

from trellum.components import ComparisonTable, DataSource, DataTable
from trellum.components.base import RenderContext
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME, THEME_REGISTRY
from trellum.validation import validate_report

BASELINE = "395a68b89aa1eb77616419f064f821e9bbf1f14e"
ROOT = Path(__file__).resolve().parents[2]


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    html = table.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def _examples():
    spec = importlib.util.spec_from_file_location("examples_table_shading", ROOT / "trellum/demo/reports/component-showcase/examples_table_shading.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target(value=1, direction="higher"):
    return {"n": {"target": value, "direction": direction}}


def test_options_are_keyword_only_and_default_output_and_positions_stay_identical():
    @dataclass
    class NamedTable(DataTable):
        caption: str = "caption"

    @dataclass
    class NamedComparison(ComparisonTable):
        caption: str = "caption"

    frame = pd.DataFrame({"n": [0, 1]})
    table = NamedTable(frame, "table", 2, True, 0, None, True, ["n"], {}, True, None, "positional")
    assert table.caption == "positional"
    unchanged = _register(table)
    for option in (None, {}):
        table.cell_shading = option
        assert _register(table) == unchanged
    table.cell_shading = _target()
    assert _register(table)[1]["cellShading"] == _target("1")
    comp = NamedComparison(frame, "comparison", "n", ["n"], None, True, {}, ["n"], "positional")
    assert comp.caption == "positional"
    unchanged = _register(comp)
    for option in (None, {}):
        comp.cell_shading = option
        assert _register(comp) == unchanged
    comp.cell_shading = _target()
    assert _register(comp)[1]["cellShading"] == _target("1")
    for table in (table, comp):
        table.static, table.dataset_id = True, "explicit_dataset"
        cfg = _register(table)[1]
        assert cfg["dataset_id"] == "explicit_dataset" and "rows" not in cfg


@pytest.mark.parametrize("option", [
    [], False, "", {1: {"target": 1, "direction": "higher"}},
    {"unknown": {"target": 1, "direction": "higher"}},
    {"n": None}, {"n": {}}, {"n": {"target": 1}},
    {"n": {"target": 1, "direction": "higher", "extra": True}},
    *[_target(value) for value in (True, False, None, "1", [], {}, float("nan"),
                                  float("inf"), float("-inf"), Decimal("NaN"),
                                  Decimal("sNaN"), Decimal("Infinity"))],
    *[_target(direction=value) for value in (None, True, "Higher", "neutral", [], {})],
])
@pytest.mark.parametrize("wrapper", [False, True])
def test_invalid_declarations_and_mutation_are_rejected(option, wrapper):
    frame = pd.DataFrame({"n": [1]})
    table = ComparisonTable(frame, "table", "n", []) if wrapper else DataTable(frame, "table")
    _register(table)
    table.cell_shading = option
    with pytest.raises(ValueError, match="cell_shading"):
        _register(table)


def test_projection_targets_and_exact_serialization_and_mixed_types():
    frame = pd.DataFrame({"n": [1], "hidden": [2]})
    with pytest.raises(ValueError, match="displayed"):
        _register(DataTable(frame, "Projected", columns=["hidden"], cell_shading=_target()))
    for value in (0, -2.5, 2**100, Decimal("1.80000000000000000001"), Decimal("1E+999999999999999999")):
        assert _register(DataTable(frame, "Exact", cell_shading=_target(value)))[1]["cellShading"] == _target(str(value))
    frame = pd.DataFrame({"n": pd.Series([1, "1", True, Decimal("1.0000000000000000001"), None], dtype=object)})
    plain = _register(DataTable(frame, "mixed"))[1]
    shaded = _register(DataTable(frame, "mixed", cell_shading=_target()))[1]
    assert shaded["cellShadingNumbers"] == {"n": ["1", None, None, "1.0000000000000000001", None]}
    assert {k: v for k, v in shaded.items() if not k.startswith("cellShading")} == plain


def test_genuine_module_build_and_full_grain_diagnostics(tmp_path):
    ctx = _context(tmp_path)
    _examples().add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
    render_report(ctx, auto_refresh=False)
    assert (tmp_path / "data.json").is_file()


def _context(path):
    return ReportContext({"name": "Table targets", "studio": "test", "category": "Components",
                          "description": "Synthetic campaign/platform cohort observations show CPI, D30 recovery and D7 retention targets, exact snapshot boundaries and homogeneous weekly retention comparisons."},
                         "table-shading", str(path))


def _edge_frame():
    return pd.DataFrame({
        "Label": ["negative", "zero", "equal", "above", "missing", "bool", "text", "date", "infinite", "decimal"],
        "n": pd.Series([-3, 0, -2.5, -2.499, None, True, "-3", date(2026, 11, 1), float("inf"), Decimal("-2.5000000000000000001")], dtype=object),
    })


@pytest.fixture
def shading_page(tmp_path):
    from playwright.sync_api import sync_playwright

    current = tmp_path / "current"
    ctx = _context(current)
    _examples().add_sections(ctx)
    frame = _edge_frame()
    dataset_frame = frame.copy()
    dataset_frame["n"] = dataset_frame["n"].map(lambda v: str(v) if isinstance(v, Decimal) else v)
    safe = pd.DataFrame({"__proto__": [0, 1], "constructor": [0, 1],
                         'target "<img src=x onerror=window.bad=1>': [0, 1]})
    ctx.add_section("Typed and escaped edges", [
        DataTable(frame, "Mixed static", static=True, cell_shading=_target(Decimal("-2.5"), "lower")),
        DataSource("shading_edges", dataset_frame),
        DataTable(dataset_frame, "Mixed dataset", dataset_id="shading_edges", cell_shading=_target(-2.5, "lower")),
        DataTable(safe, "Safe columns", static=True, cell_shading={c: {"target": 0, "direction": "lower"} for c in safe}),
        DataTable(pd.DataFrame({"n": [Decimal("1E+999999999999999998"), Decimal("1E+999999999999999999")]}),
                  "Extreme exponents", static=True, cell_shading=_target(Decimal("1E+999999999999999999"))),
    ])
    render_report(ctx, auto_refresh=False)
    data = json.loads((current / "data.json").read_text())
    ids = [cid for cid, cfg in data["components"].items() if cfg["type"] == "table"]
    vendor = ROOT / "trellum/static/vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fulfill(route):
            url = urlsplit(route.request.url)
            assert url.hostname == "shading.test", route.request.url
            path = unquote(url.path).lstrip("/")
            file = vendor / path[len("_vendor/"):] if path.startswith("_vendor/") else tmp_path / path
            route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")

        page.route("**/*", fulfill)
        page.goto("http://shading.test/current/index.html", wait_until="networkidle")
        for cid in ids:
            page.locator(f"#{cid}").scroll_into_view_if_needed()
            page.locator(f"#{cid} tbody tr").first.wait_for()
        page.evaluate("window.csvCalls = []; window._fwDownloadCsv = (columns, rows) => csvCalls.push({columns, rows})")
        yield page, ids, data, tmp_path
        assert errors == []
        browser.close()


def _cell(page, cid, row, column):
    return page.locator(f"#{cid} tbody tr").nth(row).locator("td").nth(column)


def _export(page, cid):
    page.locator(f'[data-csv-target="{cid}"]').click()
    return page.evaluate("csvCalls.at(-1)")


def _outcomes(page, cid, column):
    return page.locator(f"#{cid} tbody tr td:nth-child({column + 1})").evaluate_all(
        "cells => cells.map(c => c.title.split(': ')[1]?.split(' (')[0] || '')")


def test_raw_boundaries_types_safe_names_comparison_units_and_retained_live_rows(shading_page):
    page, ids, data, _ = shading_page
    plain, shaded, live, exact, comparison, mixed, delivered, safe, extreme = ids
    assert _cell(page, shaded, 0, 3).inner_text() == _cell(page, shaded, 1, 3).inner_text() == "1.80"
    assert _outcomes(page, shaded, 3) == ["Meets target", "Above target", "Meets target", "Meets target", "", "Above target"]
    assert _outcomes(page, shaded, 4) == ["Meets target", "Below target", "Meets target", "Below target", "", "Meets target"]
    assert _outcomes(page, exact, 1) == ["Below target", "Meets target", "Meets target"]
    assert _outcomes(page, exact, 2) == ["Meets target", "Meets target", "Above target"]
    page.locator(f"#{exact} th").filter(has_text="Exact integer").click()
    assert [r[1] for r in _export(page, exact)["rows"]] == ["9007199254740994", "9007199254740993", "9007199254740992"]
    assert _outcomes(page, exact, 1) == ["Meets target", "Meets target", "Below target"]
    assert _outcomes(page, extreme, 0) == ["Below target", "Meets target"]
    assert _outcomes(page, mixed, 1) == ["Meets target", "Above target", "Meets target", "Above target", "", "", "", "", "", "Meets target"]
    assert _outcomes(page, delivered, 1) == ["Meets target", "Above target", "Meets target", "Above target", "", "", "", "", "", ""]
    assert _cell(page, comparison, 0, 2).inner_text() == _cell(page, comparison, 1, 2).inner_text() == "25.0%"
    assert _outcomes(page, comparison, 2) == ["Meets target", "Below target", "Meets target"]
    assert _export(page, comparison)["rows"][0] == ["Mature search", .23, .25]
    page.evaluate("""([id, cfg]) => window._fwRenderers.table(id, {...cfg,
        conditionalFormats:{Current:[{op:'>=',value:.25,color:'var(--accent-red)'}]}})""", [comparison, data["components"][comparison]])
    assert _cell(page, comparison, 0, 2).locator("span").first.evaluate("e=>e.style.color") == "var(--accent-red)"
    assert _outcomes(page, comparison, 2) == ["Meets target", "Below target", "Meets target"]
    assert _export(page, plain) == _export(page, shaded)
    assert page.locator(f"#{plain} td[title]").count() == 0
    assert page.locator(f'[data-shading-for="{plain}"]').count() == 0
    assert page.locator(f"#{safe} img").count() == page.locator(".fw-table-shading-key img").count() == 0
    assert page.evaluate("window.bad || 0") == 0
    assert _outcomes(page, safe, 0) == ["Meets target", "Above target"]
    assert _outcomes(page, safe, 1) == ["Meets target", "Above target"]
    assert 'target "<img' in page.locator(f'[data-shading-for="{safe}"]').inner_text()
    # A live result's numeric-looking strings stay text even with stale numericColumns.
    page.evaluate("""id => {
        const cfg = {...window._reportData.components[id], numericColumns:['n']};
        window._fwLiveWrap = (id, cfg, draw) => {
            window.deliverShading = draw;
            draw([{Label:'text', n:'-3'}, {Label:'number', n:-3}, {Label:'equal', n:-2.5}, {Label:'boolean', n:true}, {Label:'nan', n:NaN}]);
        };
        window._fwRenderers.table(id, cfg);
        window._fwFilterEngine.getFiltered = () => {throw Error('export must use retained rows');};
    }""", delivered)
    assert _outcomes(page, delivered, 1) == ["", "Meets target", "Meets target", "", ""]
    assert _export(page, delivered)["rows"][:4] == [["text", "-3"], ["number", -3], ["equal", -2.5], ["boolean", True]]
    page.evaluate("deliverShading([{Label:'new zero', n:0}])")
    assert _outcomes(page, delivered, 1) == ["Above target"]
    assert page.locator(f'[data-shading-for="{delivered}"]').count() == 1


def test_filter_search_sort_csv_redraw_preserve_population_and_text_rules(shading_page):
    page, ids, data, _ = shading_page
    cid = ids[2]
    # A lower display limit still exports all matches in exact stable sort order.
    cfg = dict(data["components"][cid], maxRows=1)
    page.evaluate("([id, cfg]) => window._fwRenderers.table(id, cfg)", [cid, cfg])
    assert page.locator(f"#{cid} tbody tr").count() == 1
    assert len(_export(page, cid)["rows"]) == 6
    page.locator(f'[data-table-search="{cid}"]').fill("launch")
    page.wait_for_function("id => document.getElementById(id)._fwTableState.term === 'launch'", arg=cid)
    page.locator(f"#{cid} th").filter(has_text="CPI").click()
    assert [r[3] for r in _export(page, cid)["rows"]] == [1.801, 1.8, 1.799]
    assert _outcomes(page, cid, 3) == ["Above target"]
    assert _cell(page, cid, 0, 3).evaluate("e => e.style.color") == "var(--text-warning)"
    assert page.locator(f"#{cid} .fw-bar-fill").count() == 1
    page.get_by_role("button", name="iOS", exact=True).click()
    page.wait_for_function("id => document.querySelector('#'+id+' .fw-table-note').textContent.includes('2 matching')", arg=cid)
    assert [r[0] for r in _export(page, cid)["rows"]] == ["Partner launch", "Search launch"]
    assert _outcomes(page, cid, 3) == ["Meets target"]
    page.evaluate("([id, cfg]) => window._fwRenderers.table(id, cfg)", [cid, cfg])
    assert [r[0] for r in _export(page, cid)["rows"]] == ["Partner launch", "Search launch"]
    assert page.locator(f'[data-shading-for="{cid}"]').count() == 1
    assert page.locator(f'[data-table-search="{cid}"] + .fw-table-count').count() == 1


def _paint(cell):
    return cell.evaluate("""el => {
        const s=getComputedStyle(el), canvas=document.createElement('canvas'), c=canvas.getContext('2d');
        function rgb(v) {c.clearRect(0,0,1,1);c.fillStyle=v;c.fillRect(0,0,1,1);return Array.from(c.getImageData(0,0,1,1).data).slice(0,3);}
        const fg=rgb(s.color), bg=rgb(s.backgroundColor);
        function lum(values) {return values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);}
        const a=lum(fg), b=lum(bg);
        return {bg, fg, contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05), height:el.getBoundingClientRect().height, align:s.textAlign};
    }""")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_actual_theme_paint_hover_geometry_scroll_and_evidence(shading_page, width):
    page, ids, data, tmp = shading_page
    page.set_viewport_size({"width": width, "height": 1000})
    plain, shaded = ids[:2]
    for theme in THEME_REGISTRY:
        page.locator("#fwThemeSelect").select_option(theme)
        assert page.locator("html").get_attribute("data-theme") == theme
        page.mouse.move(0, 0)
        colors = []
        for row in (0, 1, 3):
            cell = _cell(page, shaded, row, 3)
            cell.scroll_into_view_if_needed()
            style = _paint(cell)
            assert style["contrast"] >= 4.5, (theme, row, style)
            assert style["align"] == "right"
            assert style["height"] == _paint(_cell(page, plain, row, 3))["height"]
            colors.append(style["bg"])
        assert colors[0] != colors[1]  # favorable and unfavorable
        assert colors[0] != colors[2]  # stripe still contributes on favorable cells
        _cell(page, shaded, 0, 3).hover()
        hover = _paint(_cell(page, shaded, 0, 3))
        assert hover["bg"] != colors[0] and hover["contrast"] >= 4.5
        assert hover["bg"] != _paint(_cell(page, shaded, 0, 2))["bg"]
        page.mouse.move(0, 0)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        key = page.locator(f'[data-shading-for="{shaded}"]')
        assert key.count() == 1 and "original units" in key.inner_text()
        assert key.bounding_box()["width"] <= width
        if theme in ("trellum light", "trellum dark"):
            shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", tmp / "evidence"))
            shots.mkdir(parents=True, exist_ok=True)
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(shots / f"shading-{width}-{theme.replace(' ', '-')}.png"), full_page=True)
    if width == 390:
        scroller = page.locator(f"#{shaded}")
        assert scroller.evaluate("e => e.scrollWidth > e.clientWidth")
        scroller.evaluate("e => e.scrollLeft = e.scrollWidth")
        assert scroller.evaluate("e => e.scrollLeft > 0")
        page.locator(f'[data-shading-for="{shaded}"]').scroll_into_view_if_needed()
        page.screenshot(path=str(shots / "shading-390-numbers.png"))
        page.evaluate("([id,cfg]) => window._fwRenderers.table(id,cfg)", [shaded, data["components"][shaded]])
        assert page.locator(f'[data-shading-for="{shaded}"]').count() == 1


def _baseline_module(reference=BASELINE):
    result = subprocess.run(["git", "show", f"{reference}:trellum/components/tables.py"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        return None
    module = types.ModuleType("_table_shading_baseline")
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{reference}/tables.py", "exec"), module.__dict__)
    for loader, suffix in (("load_css", "css"), ("load_js", "js")):
        real = getattr(module, loader)
        content = subprocess.check_output(["git", "show", f"{reference}:trellum/static/{suffix}/components/data_table.{suffix}"], cwd=ROOT, text=True)
        setattr(module, loader, lambda name, real=real, content=content, suffix=suffix: content if name == f"components/data_table.{suffix}" else real(name))
    return module


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
@pytest.mark.parametrize("reference", [BASELINE, "2fcbf6afcb8bf48b83d2dedc76dd15222168b9a2"])
def test_default_off_matches_actual_dependency_baseline(shading_page, width, reference):
    page, _, _, tmp = shading_page
    page.set_viewport_size({"width": width, "height": 1000})
    baseline = _baseline_module(reference)
    if baseline is None:
        pytest.skip("Dependency baseline Git object is unavailable for visual comparison")
    frame = _examples().shading_fixture()
    captures = []
    for name, cls in (("before", baseline.DataTable), ("after", DataTable)):
        ctx = _context(tmp / name)
        ctx.add_section("Default table", [cls(frame, "Default table", static=True)])
        render_report(ctx, auto_refresh=False)
        page.goto(f"http://shading.test/{name}/index.html", wait_until="networkidle")
        page.locator("#fwThemeSelect").select_option("trellum light")
        cell = page.locator(".fw-data-table td").first
        cell.wait_for()
        page.evaluate("document.fonts.ready")
        page.mouse.move(0, 0)
        page.wait_for_function("getComputedStyle(document.querySelector('.fw-csv-btn')).color === 'rgb(71, 85, 105)'")
        table = page.locator(".fw-table-scroll")
        assert page.locator('[data-table-row-limit], .fw-row-limit-note').count() == 0
        assert page.locator(".fw-table-shading-key, .fw-table-shading-meets, .fw-table-shading-misses").count() == 0
        assert cell.evaluate("e=>getComputedStyle(e).paddingTop") == "6px"
        shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", tmp / "evidence"))
        shots.mkdir(parents=True, exist_ok=True)
        # The CSV button transitions colors after switching theme. Finish that
        # finite animation so both captures compare the settled presentation.
        capture = page.locator('.fw-section').first.screenshot(
            path=str(shots / f"default-{reference[:7]}-{width}-{name}.png"), animations='disabled')
        geometry = table.evaluate("e => Array.from(e.querySelectorAll('th,td')).map(c => {const r=c.getBoundingClientRect();return [r.x,r.width,r.height,getComputedStyle(c).backgroundColor]})")
        captures.append((table.screenshot(animations='disabled'), geometry, capture))
    assert captures[0][:2] == captures[1][:2]
    # Whole-panel captures also cover the header. Chromium's SVG button edge
    # antialiasing can differ by one RGB level after a theme transition.
    from io import BytesIO
    from PIL import Image, ImageChops
    before, after = (Image.open(BytesIO(c[2])).convert('RGB') for c in captures)
    assert before.size == after.size
    assert all(high <= 1 for low, high in ImageChops.difference(before, after).getextrema())
