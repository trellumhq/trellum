"""Native Grid colors: real Chart.js, filters, lifecycle, CSV and exports."""

import base64
import csv
import importlib.util
import io
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from PIL import Image, ImageChops

from trellum.components import AreaChart, BarChart, Grid, LineChart, Panel, RawHTML
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_grid_colors import BASELINE, ROOT
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def example():
    return module(ROOT / "trellum/demo/reports/component-showcase/examples_grid_colors.py", "grid_color_examples")


def build(output, *, baseline=False, control=False, export_only=False):
    output.mkdir(parents=True, exist_ok=True)
    teaching = example()
    if baseline:
        for filename, names in (("charts/line_area.py", ("LineChart", "AreaChart")),
                                ("charts/bar.py", ("BarChart",)), ("layout.py", ("Grid",))):
            old_path = output / (Path(filename).stem + "_baseline.py")
            old_path.write_bytes(subprocess.check_output(["git", "show", f"{BASELINE}:trellum/components/{filename}"], cwd=ROOT))
            old = module(old_path, "grid_baseline_" + old_path.stem)
            for name in names:
                setattr(teaching, name, getattr(old, name))
    ctx = ReportContext(config={"name": "Grid colors", "studio": "test", "category": "Components",
        "description": "Deterministic synthetic daily metric tiles, fixed and filter-bound, October 1–7, 2026.",
        "theme": "trellum light"}, slug="grid-colors", output_dir=str(output),
        theme=THEME_REGISTRY["trellum light"])
    teaching.add_sections(ctx)
    if control:
        ctx.sections[:] = [s for s in ctx.sections if "theme cycle" not in s["title"]]
    if export_only:
        ctx.sections[:] = [s for s in ctx.sections if "default colors" not in s["title"]]
    if not baseline and not control and not export_only:
        df = teaching.daily_frame()
        common = dict(df=df, x="day", y="revenue", dataset_id=teaching.DATASET)
        reuse = LineChart(title="Reused", **common)
        mapping = {"domain": "grid_platform", "column": "platform", "slots": {"Desktop": 3, "Mobile": 1}}
        ratio = [{"numerator": "revenue", "denominator": "orders", "label": "Revenue per order"}]
        ctx.add_section("Ownership and exclusions", [
            Grid([RawHTML("<p>Authored spacer</p>"), reuse], color_cycle="theme"), reuse,
            Grid([RawHTML("<p>Outer spacer</p>"), Grid([reuse]), Panel(children=[reuse]),
                  Grid([reuse, reuse], color_cycle="theme"),
                  LineChart(title="Explicit", colors=["#b45309"], **common),
                  LineChart(title="Category", stack_by="platform", category_colors=mapping, **common),
                  LineChart(title="Multi metric", y=["revenue", "orders"],
                            **{k: v for k, v in common.items() if k != "y"}),
                  LineChart(title="Ratio", ratios=ratio, **common),
                  AreaChart(title="Stacked area", stacked=True, **common),
                  BarChart(title="Outcome", net_color=True, **common),
                  reuse], color_cycle="theme"),
        ])
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    (output / "diagnostics.json").write_text(json.dumps(details, indent=2, default=str))
    result.write(str(output))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
    assert not any(check.suppressed for check in result.checks)
    render_report(ctx, str(output), auto_refresh=False, details=details, validation=result)
    if baseline:
        path = output / "index.html"
        html = path.read_text()
        for name in ("components/chart_base.js", "runtime/theme.js", "data_loader.js"):
            current = (ROOT / "trellum/static/js" / name).read_text()
            original = subprocess.check_output(["git", "show", f"{BASELINE}:trellum/static/js/{name}"], cwd=ROOT, text=True)
            assert current in html
            html = html.replace(current, original)
        path.write_text(html)
    copy_vendor_tree(output)
    ctx.close_connections()


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    root = Path(os.environ.get("TRELLUM_GRID_EVIDENCE", tmp_path_factory.mktemp("grid-colors")))
    build(root / "native")
    build(root / "off", control=True)
    build(root / "baseline", baseline=True, control=True)
    build(root / "exports", export_only=True)
    return root


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


def open_report(browser, reports, width, mode="native", start_theme=None):
    page = browser.new_page(viewport={"width": width, "height": 900})
    if start_theme is not None:
        page.add_init_script("localStorage.setItem('fw-theme', " + json.dumps(start_theme) + ")")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    def serve(route):
        url = urlparse(route.request.url)
        assert url.hostname == "grid.test", route.request.url
        path = reports / mode / unquote(url.path).lstrip("/")
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404, body="Missing native fixture asset")
    page.route("**/*", serve)
    page.goto("http://grid.test/index.html", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    for canvas in page.locator("canvas").all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=canvas.get_attribute("id"))
    return page, errors


STATE = """els=>els.map(el=>{const c=window._chartInstances[el.id]; return {
    id:el.id, labels:c.data.labels, series:c.data.datasets.map(ds=>({label:ds.label,data:ds.data,
    color:ds.borderColor,fill:ds.backgroundColor,slot:ds._fwPaletteSlot??null})),
    height:el.parentElement.getBoundingClientRect().height};})"""


def state(region):
    return region.locator("canvas").evaluate_all(STATE)


def section(page, text):
    return page.locator(".fw-section").filter(has=page.get_by_role("heading", name=text, exact=True))


def source_values(items):
    return [[item["labels"], [(s["label"], s["data"]) for s in item["series"]]] for item in items]


def csv_data(page, cid, path):
    page.locator(f"canvas#{cid}").scroll_into_view_if_needed()
    page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)
    with page.expect_download() as download:
        page.locator(f'[data-csv-chart="{cid}"]').evaluate("el=>el.click()")
    download.value.save_as(path)
    # Chromium throttles automatic downloads above ten per second. This fixture
    # intentionally exercises every tile's real native download without stubs.
    page.wait_for_timeout(150)
    return list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def assert_slots(page):
    palette = page.evaluate("getThemeColors().chart_colors")
    for mode in ("Fixed snapshot", "Filter-bound"):
        enabled = state(section(page, mode + " · theme cycle"))
        assert len(enabled) == 6
        for i, item in enumerate(enabled):
            ds = item["series"][0]
            color = palette[i % len(palette)]
            assert ds["slot"] == i and ds["color"] == color
            assert ds["fill"] == color + ("1f" if i % 3 == 1 else "cc" if i % 3 == 2 else "22")
        assert len({c["series"][0]["color"] for c in enabled}) == 6
    # Real rendered adaptive observation points follow their dataset too.
    assert page.evaluate("""()=>Array.from(document.querySelectorAll('canvas')).every(el=>{
        const c=window._chartInstances[el.id],ds=c.data.datasets[0];
        return ds._fwPaletteSlot===undefined || c.config.type!=='line' || ds._areaFill ||
          c.getDatasetMeta(0).data.every(p=>p.options.borderColor===ds.borderColor && p.options.backgroundColor===ds.borderColor);
    })""")
    # Dataset strings alone do not prove that Chart.js refreshed shared paint.
    assert page.evaluate("""()=>Array.from(document.querySelectorAll('canvas')).every(el=>{
        const c=window._chartInstances[el.id],ds=c.data.datasets[0],meta=c.getDatasetMeta(0);
        if(ds._fwPaletteSlot===undefined)return true;
        if(c.config.type==='bar')return meta.data.every(p=>p.options.backgroundColor===ds.backgroundColor && p.options.borderColor===ds.borderColor);
        return !ds._areaFill || meta.dataset.options.backgroundColor===ds.backgroundColor;
    })""")


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_filter_theme_csv_and_ownership(reports, browser, width):
    page, errors = open_report(browser, reports, width)
    try:
        assert_slots(page)
        originals = {mode: state(section(page, mode + " · theme cycle")) for mode in ("Fixed snapshot", "Filter-bound")}
        for mode in originals:
            assert source_values(originals[mode]) == source_values(state(section(page, mode + " · default colors")))
        assert source_values(originals["Fixed snapshot"]) == source_values(originals["Filter-bound"])
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=canvas.get_attribute("id"))
            assert_slots(page)
            palette = page.evaluate("getThemeColors().chart_colors")
            owned = state(section(page, "Ownership and exclusions"))
            assert [c["series"][0]["slot"] for c in owned] == [1, None, None, None, 0, 1, None, None, None, 7, None, None, 10]
            assert owned[6]["series"][0]["color"] == "#b45309"
            assert {s["label"]: s["color"] for s in owned[7]["series"]} == {"Desktop": palette[3], "Mobile": palette[1]}
            assert owned[-1]["series"][0]["color"] == palette[10 % len(palette)]
            assert page.locator(".fw-grid").evaluate_all("els=>els.every(el=>el.scrollWidth<=el.clientWidth+1)")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            grid = section(page, "Filter-bound · theme cycle").locator(".fw-grid")
            grid.screenshot(path=str(reports / f"grid-{width}-{theme.split()[1]}.png"),
                            style=".fw-report-chrome {visibility: hidden}")
        page.get_by_role("button", name="Desktop", exact=True).first.click()
        target = originals["Filter-bound"][0]["id"]
        expected = example().daily_frame().query("platform == 'Desktop'")["revenue"].tolist()
        page.wait_for_function("arg=>JSON.stringify(window._chartInstances[arg.id].data.datasets[0].data)===JSON.stringify(arg.values)",
                               arg={"id": target, "values": expected})
        assert_slots(page)
        for mode in ("Fixed snapshot", "Filter-bound"):
            off = state(section(page, mode + " · default colors"))
            on = state(section(page, mode + " · theme cycle"))
            assert source_values(off) == source_values(on)
            for i, (plain, colored) in enumerate(zip(off, on)):
                a = csv_data(page, plain["id"], reports / f"{width}-{mode}-{i}-off.csv")
                b = csv_data(page, colored["id"], reports / f"{width}-{mode}-{i}-on.csv")
                assert a == b
            if mode == "Fixed snapshot":
                assert source_values(on) == source_values(originals[mode])
        # A category declaration stays category-owned even with one remaining group.
        owned = state(section(page, "Ownership and exclusions"))
        assert owned[7]["series"] == [{"label": "Desktop", "data": expected,
            "color": page.evaluate("getThemeColors().chart_colors[3]"),
            "fill": page.evaluate("getThemeColors().chart_colors[3]+'22'"), "slot": None}]
        page.get_by_role("button", name="All", exact=True).first.click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===30", arg=target)
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_omitted_pixels_and_source_csv_match_actual_baseline(reports, browser, width):
    old, old_errors = open_report(browser, reports, width, "baseline")
    new, new_errors = open_report(browser, reports, width, "off")
    try:
        assert source_values(state(old)) == source_values(state(new))
        for i, (a, b) in enumerate(zip(old.locator("canvas").all(), new.locator("canvas").all())):
            # Canvas byte identity includes pixels, geometry, axes and data marks.
            assert a.evaluate("el=>el.toDataURL()") == b.evaluate("el=>el.toDataURL()")
            assert csv_data(old, a.get_attribute("id"), reports / f"baseline-{width}-{i}.csv") == csv_data(
                new, b.get_attribute("id"), reports / f"omitted-{width}-{i}.csv")
        a = section(old, "Fixed snapshot · default colors").screenshot()
        b = section(new, "Fixed snapshot · default colors").screenshot()
        assert ImageChops.difference(Image.open(io.BytesIO(a)).convert('RGB'),
                                     Image.open(io.BytesIO(b)).convert('RGB')).getbbox() is None
        assert not old_errors + new_errors
    finally:
        old.close()
        new.close()


@pytest.mark.heavy
def test_repeated_disposal_reentry(reports, browser):
    page, errors = open_report(browser, reports, 390, start_theme="trellum dark")
    try:
        assert_slots(page)
        canvas = section(page, "Filter-bound · theme cycle").locator("canvas").nth(1)
        cid = canvas.get_attribute("id")
        count = page.evaluate("window._fwCategoryChartBindings.size")
        for _ in range(8):
            canvas.evaluate("""el=>{window.gridRemoved=el; window.gridParent=el.parentElement; el.remove();}""")
            page.wait_for_function("id=>!window._fwCategoryChartBindings.has(id)&&!window._chartInstances[id]", arg=cid)
            page.evaluate("""()=>{const el=window.gridRemoved.cloneNode();window.gridParent.append(el);
                window._fwRenderers.chart(el.id,window._reportData.components[el.id]);}""")
            page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)
            assert page.evaluate("window._fwCategoryChartBindings.size") == count
            assert page.evaluate("id=>window._fwFilterEngine._subscribers.showcase_grid_colors_daily.filter(s=>s.id===id).length", cid) == 1
        page.evaluate("window.renderAll()")
        for canvas in page.locator("canvas").all():
            canvas.scroll_into_view_if_needed()
            page.wait_for_function("id=>!!window._chartInstances[id]", arg=canvas.get_attribute("id"))
        assert_slots(page)
        assert not errors
    finally:
        page.close()


@pytest.mark.heavy
@pytest.mark.parametrize("width", [1280, 390])
def test_active_theme_native_png_pdf_and_section_capture(reports, browser, width):
    page, errors = open_report(browser, reports, width, "exports")
    try:
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            region = section(page, "Filter-bound · theme cycle")
            for canvas in page.locator("canvas").all():
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=canvas.get_attribute("id"))
            region.scroll_into_view_if_needed()
            assert_slots(page)
            before = state(region)
            payload = region.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
            mode = theme.split()[1]
            (reports / f"native-section-{width}-{mode}.png").write_bytes(base64.b64decode(payload["image"]["data_base64"]))
            assert state(region) == before
            # Native report exports include both static and bound six-tile Grids.
            before_report = state(page)
            for method, suffix, magic in (("exportPNG", "png", b"\x89PNG"), ("exportPDF", "pdf", b"%PDF")):
                with page.expect_download(timeout=60000) as download:
                    page.evaluate("method=>fw[method]()", method)
                path = reports / f"native-report-{width}-{mode}.{suffix}"
                download.value.save_as(path)
                assert path.read_bytes().startswith(magic)
                assert state(page) == before_report
            subprocess.run(["pdftoppm", "-f", "1", "-singlefile", "-scale-to", "1600", "-png",
                str(reports / f"native-report-{width}-{mode}.pdf"), str(reports / f"native-pdf-{width}-{mode}")],
                check=True, timeout=30, capture_output=True)
            assert not page.locator("iframe.html2canvas-container,#fwExportBtn.running").count()
            assert_slots(page)
        assert not errors
    finally:
        page.close()
