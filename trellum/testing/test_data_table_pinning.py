"""Optional pinned DataTable edges keep the existing table population."""

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import subprocess
import sys
import types

import pandas as pd
import pytest

from trellum.components import ComparisonTable, DataTable
from trellum.components.base import RenderContext
from trellum.themes import DEFAULT_THEME

ROOT = Path(__file__).resolve().parents[2]


def _register(table):
    ctx = RenderContext(DEFAULT_THEME)
    html = table.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


def _sum():
    return {"Value": {"agg": "sum"}}


def test_pin_options_are_keyword_only_default_off_and_independent():
    @dataclass
    class Extended(DataTable):
        caption: str = "caption"

    frame = pd.DataFrame({"Label": ["a", "b"], "Value": [1, 2]})
    args = (frame, "Rows", 2, True, None, None, True, ["Label", "Value"], {}, True, None, "positional")
    original = Extended(*args)
    base = _register(original)
    assert "pin" not in base[0] and not any(key.startswith("pin") for key in base[1])
    assert _register(Extended(*args, pin_height=7)) == base
    for kwargs, expected in [
        ({"pin_header": True}, (True, False)),
        ({"pin_footer": True, "totals": _sum()}, (False, True)),
        ({"pin_header": True, "pin_footer": True, "totals": _sum()}, (True, True)),
    ]:
        _, cfg = _register(DataTable(frame, "Accessible rows", **kwargs))
        assert (cfg["pinHeader"], cfg["pinFooter"]) == expected
        assert cfg["pinHeight"] == 320 and cfg["title"] == "Accessible rows"
    bound = DataTable(frame, "Bound", dataset_id="explicit", static=True,
                      pin_header=True, totals=_sum())
    _, cfg = _register(bound)
    assert cfg["dataset_id"] == "explicit" and "rows" not in cfg


def test_default_off_matches_actual_dependency_output_when_git_object_exists():
    dependency = "dceec9e7a86a6b87104fffc0c226d9bdfa2201d6"
    result = subprocess.run(["git", "show", f"{dependency}:trellum/components/tables.py"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        pytest.skip("Required dependency object dceec9e7 unavailable; functional checks still run")
    module = types.ModuleType("_datatable_pinning_dependency")
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{dependency}/tables.py", "exec"), module.__dict__)
    for loader, suffix in (("load_css", "css"), ("load_js", "js")):
        asset = subprocess.run(["git", "show", f"{dependency}:trellum/static/{suffix}/components/data_table.{suffix}"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout
        real = getattr(module, loader)
        setattr(module, loader, lambda name, real=real, asset=asset, suffix=suffix:
                asset if name == f"components/data_table.{suffix}" else real(name))
    frame = pd.DataFrame({"Label": ["a", "b"], "Value": [1, 2]})
    assert _register(module.DataTable(frame, "Baseline", static=True)) == _register(
        DataTable(frame, "Baseline", static=True))


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_default_off_visual_matches_actual_dependency(tmp_path, width):
    from urllib.parse import unquote, urlsplit

    from playwright.sync_api import sync_playwright
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext

    dependency = "dceec9e7a86a6b87104fffc0c226d9bdfa2201d6"
    result = subprocess.run(["git", "show", f"{dependency}:trellum/components/tables.py"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        pytest.skip("Required dependency object dceec9e7 unavailable; no historical visual baseline")
    module = types.ModuleType("_datatable_pinning_visual_dependency")
    sys.modules[module.__name__] = module
    exec(compile(result.stdout, f"{dependency}/tables.py", "exec"), module.__dict__)
    for loader, suffix in (("load_css", "css"), ("load_js", "js")):
        asset = subprocess.run(["git", "show", f"{dependency}:trellum/static/{suffix}/components/data_table.{suffix}"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout
        real = getattr(module, loader)
        setattr(module, loader, lambda name, real=real, asset=asset, suffix=suffix:
                asset if name == f"components/data_table.{suffix}" else real(name))

    frame = pd.DataFrame({"Label": ["A", "B", "C"], "Value": [3, 2, 1]})
    for folder, cls in (("before", module.DataTable), ("after", DataTable)):
        ctx = ReportContext({"name": "Default table", "studio": "test", "category": "Components",
                             "description": "Compare default DataTable rendering at the supplied row grain."},
                            "pin-baseline", str(tmp_path / folder))
        ctx.add_section("Default table", [cls(frame, "Default table", static=True, sortable=True)])
        render_report(ctx, auto_refresh=False)

    vendor = ROOT / "trellum/static/vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})

        def fulfill(route):
            path = unquote(urlsplit(route.request.url).path).lstrip("/")
            if path.startswith("_vendor/"):
                file = vendor / path[len("_vendor/"):]
            else:
                folder, _, relative = path.partition("/")
                file = tmp_path / folder / relative
            route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")

        page.route("**/*", fulfill)
        images = []
        geometry = []
        for theme in ("trellum light", "trellum dark"):
            for folder in ("before", "after"):
                page.goto(f"http://pin.test/{folder}/index.html", wait_until="networkidle")
                page.locator(".fw-data-table td").first.wait_for()
                page.locator("#fwThemeSelect").select_option(theme)
                table = page.locator(".fw-table-scroll")
                images.append(table.screenshot(animations="disabled"))
                geometry.append(table.evaluate("e=>Array.from(e.querySelectorAll('th,td')).map(c=>{const r=c.getBoundingClientRect();return [r.x,r.width,r.height,getComputedStyle(c).backgroundColor]})"))
        assert images[0] == images[1] and images[2] == images[3]
        assert geometry[0] == geometry[1] and geometry[2] == geometry[3]
        browser.close()


@pytest.mark.parametrize("name,value", [
    ("pin_header", 1), ("pin_header", "yes"), ("pin_footer", 0),
    ("pin_height", True), ("pin_height", 1.0), ("pin_height", "320"),
    ("pin_height", 0), ("pin_height", -1),
])
def test_pin_options_reject_wrong_types_at_construction_and_render(name, value):
    frame = pd.DataFrame({"Label": ["a"], "Value": [1]})
    with pytest.raises(ValueError, match="pin_"):
        DataTable(frame, "Invalid", **{name: value})
    table = DataTable(frame, "Mutable")
    setattr(table, name, value)
    with pytest.raises(ValueError, match="pin_"):
        _register(table)


def test_footer_requires_declaration_and_mutated_totals_are_rechecked():
    frame = pd.DataFrame({"Label": ["a"], "Value": [1]})
    with pytest.raises(ValueError, match="pin_footer"):
        DataTable(frame, "Missing", pin_footer=True)
    table = DataTable(frame, "Mutable", pin_footer=True, totals=_sum())
    table.totals = {}
    with pytest.raises(ValueError, match="pin_footer"):
        _register(table)


def test_report_validation_surfaces_mutated_pin_declarations_before_render():
    from trellum.report import ReportContext
    from trellum.validation import validate_report

    frame = pd.DataFrame({"Label": ["a"], "Value": [1]})
    table = DataTable(frame, "Mutable", static=True, pin_header=True)
    table.pin_height = False
    ctx = ReportContext({"name": "Pinning", "studio": "test", "category": "Components",
                         "description": "A table with a mutated invalid pin declaration for validation."}, "pin-invalid", "unused")
    ctx.add_section("Mutated pinning", [table])
    result = validate_report(ctx)
    assert any(check.id == "datatable-pinning-invalid" for check in result.checks)


def test_comparison_table_keeps_its_api_and_ignores_injected_pinning():
    frame = pd.DataFrame({"Label": ["a"], "Value": [1]})
    table = ComparisonTable(frame, "Comparison", "Value", ["Value"])
    assert not hasattr(table, "pin_header")
    with pytest.raises(TypeError):
        ComparisonTable(frame, "Comparison", "Value", ["Value"], pin_header=True)


def test_native_example_has_full_diagnostics():
    from trellum.report import ReportContext
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report

    spec = importlib.util.spec_from_file_location(
        "examples_table_pinning", ROOT / "trellum/demo/reports/component-showcase/examples_table_pinning.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext({"name": "Pinning", "studio": "test", "category": "Components",
                         "description": "Inspect DataTable sticky headers, total footers, additive visit metrics, and platform source filtering at visit grain."}, "table-pinning", "unused")
    module.add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_pinned_edges_keyboard_search_and_capture(tmp_path, width):
    import base64
    import json
    from urllib.parse import unquote, urlsplit

    from playwright.sync_api import sync_playwright
    from trellum.components import DataSource, DataTable, FilterBar, KpiRow
    from trellum.rendering.html_builder import render_report
    from trellum.report import ReportContext

    long_column = "Long dimension " + "Wide " * 30
    frame = pd.DataFrame([
        {"Observation": f"Visit {i + 1:02d}", "Platform": "iOS" if i % 2 == 0 else "Android",
         long_column: f"Source {i % 3}", "Sessions": i + 1, "Revenue": (i + 1) * 2}
        for i in range(42)
    ])
    out = tmp_path / "report"
    ctx = ReportContext({"name": "Pinning", "studio": "test", "category": "Components",
                         "description": "Inspect DataTable sticky headers, additive totals, and filtered visit revenue across platform source grain."}, "pin-browser", str(out))
    ctx.add_section("Pinned visits", [
        DataSource("pin_visits", frame),
        FilterBar("pin_visits", frame, filters=[{"column": "Platform", "type": "toggle"}]),
        KpiRow([{"column": "Revenue", "label": "All filtered revenue", "agg": "sum"}], dataset_id="pin_visits"),
        DataTable(frame, "Pinned visits title", dataset_id="pin_visits", sortable=True,
                  searchable=True, row_limit=10,
                  columns=["Observation", "Platform", long_column, "Sessions", "Revenue"],
                  totals={"Sessions": {"agg": "sum"}, "Revenue": {"agg": "sum"}},
                  pin_header=True, pin_footer=True, pin_height=220),
    ])
    render_report(ctx, auto_refresh=False)
    data = json.loads((out / "data.json").read_text())
    cid = next(k for k, cfg in data["components"].items() if cfg["type"] == "table")
    vendor = ROOT / "trellum/static/vendor"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def fulfill(route):
            url = urlsplit(route.request.url)
            path = unquote(url.path).lstrip("/")
            if path.startswith("_vendor/"):
                file = vendor / path[len("_vendor/"):]
            else:
                file = out / path.removeprefix("report/").lstrip("/")
            route.fulfill(path=str(file)) if file.is_file() else route.fulfill(status=404, body="missing")

        page.route("**/*", fulfill)
        page.goto("http://pin.test/report/index.html", wait_until="networkidle")
        scroller = page.locator(f"#{cid}")
        scroller.locator("tbody tr").first.wait_for()
        assert scroller.get_attribute("role") == "region"
        assert scroller.get_attribute("aria-label") == "Pinned visits title"
        assert scroller.get_attribute("tabindex") == "0"
        assert scroller.evaluate("e=>getComputedStyle(e).maxHeight") == "220px"
        heights = scroller.locator("tbody tr").evaluate_all("rows=>rows.slice(0,3).map(r=>r.getBoundingClientRect().height)")
        scroller.evaluate("e=>e.scrollTop=120")
        box = scroller.bounding_box()
        head = scroller.locator("thead th").first.bounding_box()
        foot = scroller.locator("tfoot tr").bounding_box()
        assert abs(head["y"] - box["y"]) <= 1
        assert abs(foot["y"] + foot["height"] - (box["y"] + box["height"])) <= 1
        assert scroller.locator("tbody tr").evaluate_all("rows=>rows.slice(0,3).map(r=>r.getBoundingClientRect().height)") == heights
        scroller.focus()
        page.keyboard.press("PageDown")
        assert scroller.evaluate("e=>e.scrollTop>0")
        scroller.evaluate("e=>e.scrollLeft=e.scrollWidth")
        assert scroller.evaluate("e=>e.scrollLeft>0")
        assert scroller.locator("thead th").first.bounding_box()["x"] < scroller.bounding_box()["x"]
        assert scroller.locator("tbody tr").nth(9).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")

        # Native section capture with the actual vendored renderer, at both scroll offsets.
        section = scroller.locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
        for theme in ("trellum light", "trellum dark"):
            page.locator("#fwThemeSelect").select_option(theme)
            capture = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
            assert capture["format"] == "trellum-analysis-capture"
            evidence = tmp_path / f"pinning-{width}-{theme.replace(' ', '-')}.png"
            evidence.write_bytes(base64.b64decode(capture["image"]["data_base64"]))
            assert evidence.stat().st_size > 1000
        # Exercise the ordinary, real vendored report PNG and PDF export paths too.
        for fmt in ("PNG", "PDF"):
            with page.expect_download() as download:
                page.evaluate("fmt=>fw['export'+fmt]()", fmt)
            path = tmp_path / f"ordinary-{width}.{fmt.lower()}"
            download.value.save_as(path)
            assert path.stat().st_size > 1000
            if fmt == "PDF":
                assert path.read_bytes().startswith(b"%PDF-")
        assert "42 matching rows" in page.locator(f"#{cid}-totals-note").inner_text()

        # Renderer re-entry retains the scroller, keyboard focus, native offsets and reader state.
        page.evaluate("id=>window.pinScrollerNode=document.getElementById(id)", cid)
        page.locator(f'[data-table-row-limit="{cid}"]').select_option("20")
        page.locator(f'[data-table-search="{cid}"]').fill("Visit ")
        page.wait_for_timeout(300)
        scroller.locator('th[data-col-idx="4"]').click()
        scroller.focus()
        scroller.evaluate("e=>{e.scrollTop=80;e.scrollLeft=60}")
        generation = scroller.evaluate("e=>e._fwTableGeneration")
        page.evaluate("window.renderAll()")
        page.wait_for_function("([id,g])=>document.getElementById(id)._fwTableGeneration>g", arg=[cid, generation])
        assert scroller.evaluate("e=>e===window.pinScrollerNode && e===document.activeElement && e.scrollTop===80 && e.scrollLeft===60")
        assert page.locator(f'[data-table-search="{cid}"]').input_value() == "Visit "
        assert page.locator(f'[data-table-row-limit="{cid}"]').input_value() == "20"
        assert page.evaluate("id=>document.getElementById(id)._fwTableState.sortCol", cid) == 4
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,{...cfg,comparison:{context:null,compareColumns:[],valueColumn:'Revenue',showDivider:false,autoColors:false}})", [cid, data["components"][cid]])
        assert scroller.get_attribute("role") is None, page.evaluate("id=>({owned:document.getElementById(id)._fwDataTablePinning,renderer:document.getElementById(id).className})", cid)
        assert "fw-data-table-pinned" not in (scroller.get_attribute("class") or ""), page.evaluate("id=>({owned:document.getElementById(id)._fwDataTablePinning,classes:document.getElementById(id).className})", cid)
        page.evaluate("([id,cfg])=>{window._fwMetricsWorkspace={definitionId:id};window._fwMetricsCatalog=[];_fwRenderers.table(id,{...cfg,workspacePageSize:25});}", [cid, data["components"][cid]])
        assert scroller.get_attribute("role") is None, page.evaluate("id=>({owned:document.getElementById(id)._fwDataTablePinning,renderer:document.getElementById(id).className,workspace:window._fwMetricsWorkspace})", cid)
        assert "fw-data-table-pinned" not in (scroller.get_attribute("class") or "")
        page.evaluate("delete window._fwMetricsWorkspace")
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, data["components"][cid]])
        assert scroller.get_attribute("role") == "region"
        page.locator(f'[data-table-search="{cid}"]').fill("")
        page.wait_for_timeout(300)
        page.locator(f'[data-table-row-limit="{cid}"]').select_option("10")
        page.evaluate("window.pinCsv=[];window._fwDownloadCsv=(columns,rows)=>pinCsv.push({columns,rows})")
        page.locator(f'[data-csv-target="{cid}"]').click()
        assert len(page.evaluate("pinCsv.at(-1).rows")) == 42

        # Local search reaches records beyond the current visible ten and narrows table totals only.
        page.locator(f'[data-table-search="{cid}"]').fill("Visit 42")
        page.wait_for_timeout(300)
        assert scroller.locator("tbody tr").count() == 1
        assert scroller.locator("tfoot td").last.inner_text() == "84.00"
        assert scroller.evaluate("e=>e.clientHeight < 220")
        assert page.locator(".fw-kpi-value").first.inner_text() == "1.8k"
        page.locator(f'[data-csv-target="{cid}"]').click()
        assert page.evaluate("pinCsv.at(-1).rows.length") == 1
        assert page.evaluate("pinCsv.at(-1).rows[0][0]") == "Visit 42"
        page.locator(f'[data-table-search="{cid}"]').fill("")
        page.wait_for_timeout(300)
        page.locator(f'[data-table-row-limit="{cid}"]').select_option("all")
        assert scroller.locator("tbody tr").count() == 42
        assert scroller.locator("tfoot td").last.inner_text() == "1,806.00"
        assert page.locator(".fw-kpi-value").first.inner_text() == "1.8k"
        scroller.locator('th[data-col-idx="4"]').click()
        assert scroller.locator("tfoot td").last.inner_text() == "1,806.00"
        scroller.evaluate("e=>e.scrollTop=e.scrollHeight")
        last_row = scroller.locator("tbody tr").last.bounding_box()
        footer_row = scroller.locator("tfoot tr").bounding_box()
        assert last_row["y"] >= scroller.bounding_box()["y"]
        assert last_row["y"] + last_row["height"] <= footer_row["y"] + 1
        toggle = page.locator(".fw-toggle-group").first
        toggle.get_by_role("button", name="iOS").click()
        page.wait_for_function("id=>document.getElementById(id+'-totals-note').textContent.includes('21 matching rows')", arg=cid)
        assert scroller.locator("tbody tr").count() == 21
        assert scroller.locator("tfoot td").last.inner_text() == "882.00"
        assert page.locator(".fw-kpi-value").first.inner_text() == "882"
        page.locator(f'[data-csv-target="{cid}"]').click()
        assert page.evaluate("pinCsv.at(-1).rows.length") == 21
        toggle.get_by_role("button", name="All").click()
        page.wait_for_function("id=>document.getElementById(id+'-totals-note').textContent.includes('42 matching rows')", arg=cid)
        assert scroller.locator("tfoot td").last.inner_text() == "1,806.00"
        assert page.locator(".fw-kpi-value").first.inner_text() == "1.8k"
        page.locator(f'[data-table-search="{cid}"]').fill("no such visit")
        page.wait_for_timeout(300)
        assert scroller.locator("tfoot").count() == 0
        assert scroller.evaluate("e=>e.clientHeight < 220")
        assert scroller.evaluate("e=>e.scrollTop <= e.scrollHeight - e.clientHeight")

        # Replacing the node releases its owned state. New authored attributes and style survive opt-out.
        page.evaluate("""id=>{const old=document.getElementById(id), replacement=document.createElement('div');
          replacement.id=id;replacement.className='fw-table-scroll';replacement.setAttribute('role','group');
          replacement.setAttribute('tabindex','3');replacement.setAttribute('aria-label','Authored region');
          replacement.style.setProperty('--fw-data-table-pin-height','111px');old.replaceWith(replacement)}""", cid)
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,cfg)", [cid, data["components"][cid]])
        assert scroller.get_attribute("role") == "group"
        assert scroller.get_attribute("tabindex") == "3"
        assert scroller.get_attribute("aria-label") == "Authored region"
        assert scroller.evaluate("e=>e.style.getPropertyValue('--fw-data-table-pin-height')==='111px'")
        page.evaluate("([id,cfg])=>_fwRenderers.table(id,{...cfg,pinHeader:false,pinFooter:false})", [cid, data["components"][cid]])
        assert scroller.get_attribute("role") == "group"
        assert scroller.get_attribute("tabindex") == "3"
        assert scroller.get_attribute("aria-label") == "Authored region"
        assert "fw-data-table-pinned" not in (scroller.get_attribute("class") or "")
        assert scroller.evaluate("e=>e.style.getPropertyValue('--fw-data-table-pin-height')==='111px'")
        assert errors == []
        browser.close()
