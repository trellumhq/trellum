"""Ordinary line styles: Python contracts and real vendored Chromium lifecycle."""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

from trellum.components import (
    AreaChart,
    ComboChart,
    DataSource,
    FilterBar,
    LineChart,
    RenderContext,
)
from trellum.components.charts.line_area import line_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import THEME_REGISTRY, DefaultTheme
from trellum.validation import validate_report


def _frame(count):
    return pd.DataFrame({"day": pd.date_range("2026-04-01", periods=count).strftime("%Y-%m-%d"),
                         "value": [10 + i % 9 for i in range(count)]})


@pytest.mark.parametrize("value", [True, False, -0.1, 1.1, float("nan"), float("inf"), "0.35", None, []])
def test_invalid_tension_at_construction_and_after_mutation(tmp_path, value):
    with pytest.raises(ValueError, match="tension=.*finite number"):
        LineChart(_frame(3), "day", "value", tension=value)
    chart = LineChart(_frame(3), "day", "value", static=True)
    chart.tension = value
    assert line_option_problems(chart)[0][0] == "line-chart-tension-invalid"
    ctx = _context(tmp_path)
    ctx.add_section("Mutation", [chart])
    assert [(c.id, c.level) for c in validate_report(ctx).checks if c.id.startswith("line-chart-")] == [
        ("line-chart-tension-invalid", "fail")]


def test_tension_keyword_only_subclass_and_every_config_path():
    @dataclasses.dataclass
    class Child(LineChart):
        extra: str = "child"

    assert inspect.signature(LineChart).parameters["tension"].kind == inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters["extra"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert Child(_frame(2), "day", "value").extra == "child"
    with pytest.raises(ValueError, match="tension"):
        Child(_frame(2), "day", "value", tension=True)
    for tension in (0, .35, 1.0):
        for binding in ({"static": True}, {"dataset_id": "d", "static": True},
                        {"dataset_id": "d", "stack_by": "day"},
                        {"dataset_id": "d", "ratios": [{"numerator": "value", "denominator": "value", "label": "R"}]},
                        {"dataset_id": "d", "ratios": [{"numerator": "value", "denominator": "value", "label": "R"}], "stack_by": "day"}):
            ctx = RenderContext(theme=DefaultTheme)
            LineChart(_frame(3), "day", "value", tension=tension, **binding).render_html(ctx)
            cfg = next(iter(ctx.component_data.values()))
            assert cfg["line_style"] == "adaptive" and cfg["tension"] == tension
            assert bool(cfg.get("dataset_id")) == bool(binding.get("dataset_id"))
            if "datasets" in cfg:
                assert cfg["datasets"][0]["tension"] == tension
                assert cfg["datasets"][0]["borderWidth"] == 2.5


def _context(output):
    return ReportContext(config={"name": "Line styles", "studio": "test", "category": "Test",
        "description": "Fixed daily synthetic observations by platform, with density, gaps and interpolation checks.",
        "theme": "trellum light"}, slug="line-styles", output_dir=str(output),
        theme=THEME_REGISTRY["trellum light"])


def _report(output):
    ctx = _context(output)
    frame = pd.concat([_frame(181).assign(platform=platform, users=users)
                      for platform, users in (("ios", 2), ("android", 4))], ignore_index=True)
    ctx.add_section("Live", [DataSource("lines", frame),
        FilterBar("lines", frame, filters=[{"column": "platform", "type": "toggle"}]),
        LineChart(frame, "day", "value", "Live totals", dataset_id="lines"),
        LineChart(frame, "day", "value", "Live ratio", dataset_id="lines", tension=.35,
                  ratios=[{"numerator": "value", "denominator": "users", "label": "Ratio"}]),
        LineChart(frame, "day", "value", "Live breakdown", dataset_id="lines", stack_by="platform"),
        LineChart(frame, "day", "value", "Live ratio breakdown", dataset_id="lines", stack_by="platform",
                  tension=.35, ratios=[{"numerator": "value", "denominator": "users", "label": "Ratio"}]),
    ])
    comparison = pd.DataFrame([
        {"day": f"2026-10-{day:02d}", "platform": platform,
         "actual": 40 + day + offset, "prior": 39 + day + offset, "users": 10}
        for platform, offset in (("Desktop", 0), ("Mobile", 8))
        for day in range(1, 8)
    ])
    ctx.add_section("Bound forecast", [DataSource("forecast_live", comparison),
        FilterBar("forecast_live", comparison, filters=[
            {"column": "platform", "type": "toggle", "label": "Forecast platform"},
        ]),
        LineChart(comparison, "day", ["actual", "prior"], "Bound forecast comparison",
                  dataset_id="forecast_live", forecast_series=[1], colors=["#2563eb", "#c2410c"],
                  y_labels=["Realized", "Earlier forecast"]),
        LineChart(comparison, "day", title="Bound forecast ratios", dataset_id="forecast_live",
                  ratios=[{"numerator": "actual", "denominator": "users", "label": "Actual per user"},
                          {"numerator": "prior", "denominator": "users", "label": "Forecast per user"}],
                  forecast_series=["Forecast per user"]),
    ])
    isolated = _frame(500)
    isolated["value"] = float("nan")
    isolated.loc[[67, 249, 412], "value"] = [10, 20, 0]
    gap = _frame(8)
    gap["value"] = [0, None, 20, 20, None, 0, None, 10]
    plateau = _frame(8)
    plateau["value"] = [0, 20, 20, 80, 80, 0, 100, 100]
    forecast_frame = pd.DataFrame({"day": pd.date_range("2026-10-01", periods=10).strftime("%Y-%m-%d"),
        "actual": [42, 45, 43, 48, 51, 49, 54, None, None, None],
        "forecast": [None, None, None, None, None, None, None, 52, 55, 57]})
    ctx.add_section("Static fixtures", [
        LineChart(_frame(181), "day", "value", "Dense", static=True),
        LineChart(_frame(401), "day", "value", "Beyond cutoff", static=True),
        LineChart(_frame(12), "day", "value", "Sparse", static=True),
        LineChart(_frame(35), "day", "value", "Responsive", static=True),
        LineChart(isolated, "day", "value", "Isolated zero", static=True),
        LineChart(gap, "day", "value", "Gaps", static=True, tension=.35),
        LineChart(plateau, "day", "value", "Straight", static=True),
        LineChart(plateau, "day", "value", "Curved", static=True, tension=.35),
        LineChart(_frame(1), "day", "value", "One", static=True),
        LineChart(_frame(12), "day", "value", "Explicit", static=True, colors=["#ab3478"]),
        LineChart(_frame(500).assign(value=lambda df: [10 + i % 9 for i in range(len(df))]),
                  "day", "value", "Dense forecast", static=True, forecast_series=[0]),
        LineChart(isolated, "day", "value", "Forecast isolated points", static=True,
                  forecast_series=[0]),
        LineChart(forecast_frame, "day", ["actual", "forecast"], "Forecast opt-out baseline",
            static=True, y_labels=["Observed", "Forecast"], colors=["#2563eb", "#c2410c"]),
        LineChart(forecast_frame,
            "day", ["actual", "forecast"], "Forecast horizon", static=True,
            y_labels=["Observed", "Forecast"], colors=["#2563eb", "#c2410c"],
            forecast_series=["Forecast"]),
        AreaChart(_frame(12), "day", "value", "Area", static=True),
        ComboChart(_frame(12), "day", ["value"], ["value"], "Combo", static=True),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


_STATE = """title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    return {id:c.canvas.id, width:c.chartArea.width,height:c.height,
      min:c.scales.x.min,max:c.scales.x.max,ymin:c.scales.y.min,ymax:c.scales.y.max,
      visible:c.data.datasets.map((_,i)=>c.isDatasetVisible(i)),
      ds:c.data.datasets.map((d,di)=>({data:d.data.slice(),color:d.borderColor,tension:d.tension,
        dash:d.borderDash,forecast:d._fwForecast,
        border:d.borderWidth,fill:d.fill,activeRadius:c.getDatasetMeta(di).controller.getStyle(0,true).radius,
        segments:c.getDatasetMeta(di).dataset?.segments?.map(s=>[s.start,s.end]),
        points:c.getDatasetMeta(di).data.map(p=>({radius:p.options.radius,skip:p.skip,
          border:p.options.borderColor,bg:p.options.backgroundColor,
          hover:p.options.hoverRadius,hit:p.options.hitRadius,x:p.x,y:p.y}))}))};
}"""


def _dots(state):
    return sum(p["radius"] > 0 for p in state["ds"][0]["points"][int(state["min"]):int(state["max"]) + 1] if not p["skip"])


def _open(page, output, *, baseline=False):
    def route(request):
        path = output / urlparse(request.request.url).path.lstrip("/")
        if not path.is_file():
            request.fulfill(status=404, body="missing fixture")
            return
        body = path.read_bytes()
        if baseline and path.name == "data.json":
            data = json.loads(body)
            for cfg in data["components"].values():
                if isinstance(cfg, dict) and cfg.pop("line_style", None):
                    cfg.pop("tension", None)
                    for ds in cfg.get("datasets", []):
                        ds["tension"] = 0
            body = json.dumps(data).encode()
        request.fulfill(body=body, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
    page.route("**/*", route)
    page.goto("http://line-fixture.test/index.html", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    for cid in page.locator("canvas[data-fw-kind]").evaluate_all("els=>els.map(e=>e.id)"):
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)


def _browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _report(tmp_path)
    shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", str(tmp_path / "evidence")))
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _open(page, tmp_path)
        def state(title):
            canvas = page.locator(f"canvas[data-fw-title='{title}']")
            cid = canvas.get_attribute("id")
            if not page.evaluate("id=>!!window._chartInstances[id]", cid):
                canvas.scroll_into_view_if_needed()
                page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)
            return page.evaluate(_STATE, title)
        initial = {title: state(title) for title in ("Dense", "Sparse", "Responsive", "Isolated zero", "Gaps", "Straight", "Curved", "One", "Explicit")}
        assert _dots(initial["Dense"]) == 0
        assert _dots(initial["Sparse"]) == 12
        assert _dots(initial["Responsive"]) == (35 if width == 1280 else 0)
        assert _dots(initial["Isolated zero"]) == 3
        assert _dots(initial["One"]) == 1
        assert initial["Gaps"]["ds"][0]["data"] == [0, None, 20, 20, None, 0, None, 10]
        assert initial["Gaps"]["ds"][0]["segments"] == [[0, 0], [2, 3], [5, 5], [7, 7]]
        assert initial["Straight"]["ds"][0]["data"] == initial["Curved"]["ds"][0]["data"]
        assert (initial["Straight"]["ymin"], initial["Straight"]["ymax"]) == (initial["Curved"]["ymin"], initial["Curved"]["ymax"])
        curved_range = page.evaluate("""id=>{const c=window._chartInstances[id],p=c.getDatasetMeta(0).data;
            return Array.from({length:101},(_,i)=>{const t=i/100,u=1-t;
                return c.scales.y.getValueForPixel(u*u*u*p[1].y+3*u*u*t*p[1].cp2y+
                    3*u*t*t*p[2].cp1y+t*t*t*p[2].y);});}""", initial["Curved"]["id"])
        assert min(curved_range) < 19 and max(curved_range) > 20
        assert initial["Straight"]["ds"][0]["tension"] == 0
        assert initial["Curved"]["ds"][0]["tension"] == .35
        assert all(s["height"] == 220 and s["ds"][0]["border"] == 2.5 and not s["ds"][0].get("fill") for s in initial.values())
        assert state("Area")["ds"][0]["tension"] == 0
        assert all(p["radius"] == 0 for p in state("Area")["ds"][0]["points"])
        assert state("Combo")["ds"][1]["tension"] == 0
        assert all(p["radius"] == 2 for p in state("Combo")["ds"][1]["points"])

        # Real raster around each isolated observation, including zero at the baseline.
        isolated = initial["Isolated zero"]
        paint = page.evaluate("""id=>{const c=window._chartInstances[id],ratio=c.currentDevicePixelRatio;
          const rgb=c.data.datasets[0].borderColor.slice(1).match(/../g).map(s=>parseInt(s,16));
          return [67,249,412].map(i=>{const p=c.getDatasetMeta(0).data[i];
            const pixels=c.ctx.getImageData(Math.floor(p.x*ratio)-6,Math.floor(p.y*ratio)-6,12,12).data;
            let count=0;for(let j=0;j<pixels.length;j+=4)if(pixels[j+3]>100&&rgb.every((v,k)=>Math.abs(pixels[j+k]-v)<10))count++;
            return count;});}""", isolated["id"])
        assert all(n > 5 for n in paint), paint

        # Actual vendored plugin zoom and reset expose points, even beyond 400 labels.
        for title in ("Dense", "Beyond cutoff", "Isolated zero", "Live totals", "Live ratio", "Live breakdown", "Live ratio breakdown"):
            cid = state(title)["id"]
            page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:60,max:70},'none')", cid)
            assert _dots(state(title)) == (1 if title == "Isolated zero" else 11)
            page.evaluate("id=>window._chartInstances[id].resetZoom('none')", cid)
            assert _dots(state(title)) == (3 if title == "Isolated zero" else 0)
        # Current plot width, without replacing data or changing viewport.
        cid = state("Responsive")["id"]
        original = state("Responsive")["ds"][0]["data"]
        for panel_width, expected in ((300, 0), (850, 35), (300, 0)):
            page.evaluate("""({id,w})=>{const c=window._chartInstances[id];
                c.canvas.parentElement.style.maxWidth='none';
                c.canvas.parentElement.style.width=w+'px';}""", {"id": cid, "w": panel_width})
            page.wait_for_function("""({id,w})=>{const c=window._chartInstances[id];return (
                Math.abs(c.width-w)<2 && c.chartArea.width>w-100 && c.chartArea.width<w);}""",
                arg={"id": cid, "w": panel_width})
            # Let the real ResizeObserver finish the layout it measured.
            page.evaluate("()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
            assert _dots(state("Responsive")) == expected
            assert state("Responsive")["ds"][0]["data"] == original
        page.evaluate("id=>{const c=window._chartInstances[id];c.canvas.parentElement.style.width='';c.canvas.parentElement.style.maxWidth='';}", cid)

        # Nonfinite values behave as gaps in the actual controller as well.
        cid = state("Gaps")["id"]
        page.evaluate("""id=>{const c=window._chartInstances[id];c.data.datasets[0].data=
            [0,NaN,20,20,Infinity,0,-Infinity,10];c.update('none');}""", cid)
        assert [p["radius"] for p in state("Gaps")["ds"][0]["points"]] == [2,0,2,2,0,2,0,2]
        page.evaluate("""id=>{const c=window._chartInstances[id];c.data.datasets[0].data=
            [0,null,20,20,null,0,null,10];c.update('none');}""", cid)

        # Nearest-point tooltip still inspects a suppressed dense point.
        dense = state("Dense")
        page.locator(f"#{dense['id']}").scroll_into_view_if_needed()
        box = page.locator(f"#{dense['id']}").bounding_box()
        p = state("Dense")["ds"][0]["points"][90]
        page.mouse.move(box["x"] + p["x"], box["y"] + p["y"])
        page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.[0]?.dataIndex===90", arg=dense["id"])
        assert page.evaluate("id=>window._chartInstances[id].tooltip.dataPoints[0].parsed.y", dense["id"]) == 10
        page.mouse.move(0, 0)

        # Native selector, real deferred reveal, then a normal report redraw.
        old_color = state("Sparse")["ds"][0]["color"]
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            for title in ("Sparse", "Isolated zero", "Explicit", "Live ratio breakdown"):
                cid = state(title)["id"]
                page.locator(f"#{cid}").scroll_into_view_if_needed()
                page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=cid)
                s = state(title)
                assert all(p["border"] == d["color"] and p["bg"] == d["color"] for d in s["ds"] for p in d["points"])
                if title == "Explicit":
                    assert s["ds"][0]["color"] == "#ab3478"
            if theme == "trellum dark":
                assert state("Sparse")["ds"][0]["color"] != old_color
        page.evaluate("window.renderAll()")
        page.get_by_role("button", name="android", exact=True).click()
        page.wait_for_function("""()=>Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle==='Live totals').data.datasets[0].data[0]===10""")
        assert state("Live totals")["ds"][0]["data"] == _frame(181)["value"].tolist()
        assert state("Live ratio")["ds"][0]["data"] == [v / 4 for v in _frame(181)["value"]]
        assert len(state("Live ratio breakdown")["ds"]) == 1
        assert _dots(state("Live totals")) == 0
        for title in ("Live totals", "Live ratio", "Live breakdown", "Live ratio breakdown"):
            assert all(p["border"] == d["color"] for d in state(title)["ds"] for p in d["points"])
        for title in ("Sparse", "Isolated zero", "Explicit"):
            s = state(title)
            assert all(p["border"] == d["color"] and p["bg"] == d["color"] for d in s["ds"] for p in d["points"])
            assert s["ds"][0]["data"] == initial[title]["ds"][0]["data"]
        # CSV handler sees the exact same values, static nulls and live ratios.
        page.evaluate("window._fwDownloadCsv=(cols,rows)=>window.csvCaptured={cols,rows}")
        for title in ("Gaps", "Live totals", "Live ratio"):
            s = state(title)
            page.locator(f"[data-csv-chart='{s['id']}']").click()
            assert page.evaluate("window.csvCaptured.rows.map(r=>r[1])") == s["ds"][0]["data"]
        page.get_by_role("button", name="All", exact=True).first.click()
        page.wait_for_function("""()=>Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle==='Live totals').data.datasets[0].data[0]===20""")

        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.locator(f"#{isolated['id']}").scroll_into_view_if_needed()
        page.screenshot(path=str(shots / f"line-styles-{width}-changed.png"))
        page.screenshot(path=str(shots / f"line-styles-{width}-full.png"), full_page=True)
        baseline = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        _open(baseline, tmp_path, baseline=True)
        baseline.locator(f"#{isolated['id']}").scroll_into_view_if_needed()
        assert _dots(baseline.evaluate(_STATE, "Isolated zero")) == 0
        # Untagged payloads retain the previous policy, values and hover/hit radii.
        for title in ("Dense", "Sparse", "Responsive", "Isolated zero", "Gaps", "One", "Explicit"):
            before = baseline.evaluate(_STATE, title)
            after = state(title)
            assert before["ds"][0]["data"] == after["ds"][0]["data"]
            assert before["ds"][0]["activeRadius"] == after["ds"][0]["activeRadius"]
            assert (before["ymin"], before["ymax"], before["height"]) == (after["ymin"], after["ymax"], after["height"])
            assert [(p.get("hover"),p["hit"]) for p in before["ds"][0]["points"]] == [(p.get("hover"),p["hit"]) for p in after["ds"][0]["points"]]
        assert _dots(baseline.evaluate(_STATE, "Dense")) == 181
        baseline.screenshot(path=str(shots / f"line-styles-{width}-baseline.png"))
        baseline.screenshot(path=str(shots / f"line-styles-{width}-baseline-full.png"), full_page=True)
        assert errors == []
        browser.close()


def test_line_styles_chromium_desktop(tmp_path):
    _browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_line_styles_chromium_phone(tmp_path):
    _browser_check(tmp_path, 390)


def _build_forecast_reference(output):
    """Build old Python/JS/CSS in a separate process; label shallow fallback."""
    script = '''
import importlib, json, pathlib, runpy, subprocess, sys
import trellum.assets as assets
root = pathlib.Path(sys.argv[1])
baseline = '2a9594ede767e9eb57dec761afa79a010bb1edf8'
fixture = runpy.run_path(str(root / 'trellum/testing/test_line_chart_style.py'))
try:
    historical = subprocess.run(['git', 'cat-file', '-e', baseline], cwd=root,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
except FileNotFoundError:
    historical = False
if historical:
    paths = ['js/components/chart_base.js', 'js/runtime/chart_defaults.js',
             'css/components/chart_base.css']
    sources = {assets.STATIC_ROOT / path: subprocess.check_output(
        ['git', 'show', baseline + ':trellum/static/' + path], cwd=root, text=True) for path in paths}
    original = assets._read
    assets._read = lambda path: sources[path] if path in sources else original(path)
    module = importlib.import_module('trellum.components.charts.line_area')
    source = subprocess.check_output(['git', 'show', baseline + ':trellum/components/charts/line_area.py'],
                                     cwd=root, text=True)
    exec(compile(source, baseline + '/line_area.py', 'exec'), module.__dict__)
    kind = module.LineChart
else:
    kind = fixture['LineChart']
def without_forecast(*args, **kwargs):
    kwargs.pop('forecast_series', None)
    return kind(*args, **kwargs)
fixture['_report'].__globals__['LineChart'] = without_forecast
output = pathlib.Path(sys.argv[2])
fixture['_report'](output)
(output / 'source.json').write_text(json.dumps({'baseline': baseline if historical else None,
    'description': 'historical Python/JS/CSS' if historical else 'current-runtime opt-out control'}))
'''
    subprocess.run([sys.executable, "-c", script, str(Path(__file__).resolve().parents[2]), str(output)],
                   check=True, timeout=90)
    return json.loads((output / "source.json").read_text())


def _capture_forecast_chart(page, title, output):
    canvas = page.locator(f"canvas[data-fw-title='{title}']")
    canvas.scroll_into_view_if_needed()
    canvas.evaluate("""canvas => {
        scrollTo({top:scrollY + canvas.getBoundingClientRect().top - 240, behavior:'instant'});
        return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }""")
    clip = canvas.evaluate("""canvas => {
        const plot = canvas.parentElement, box = plot.getBoundingClientRect();
        let top = box.top, sibling = plot.previousElementSibling;
        while (sibling && sibling.matches('.fw-chart-legend,.fw-chart-title,.fw-chart-head')) {
            top = Math.min(top, sibling.getBoundingClientRect().top);
            sibling = sibling.previousElementSibling;
        }
        return {x:box.left, y:top,
                width:box.width, height:box.bottom - top};
    }""")
    page.screenshot(path=str(output), clip=clip)


def _forecast_browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _report(tmp_path)
    reference_path = tmp_path / "reference"
    reference_source = _build_forecast_reference(reference_path)
    shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", str(tmp_path / "evidence")))
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        _open(page, tmp_path)
        reference_page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        _open(reference_page, reference_path)
        historical = reference_page.evaluate(_STATE, "Forecast horizon")
        reference_canvas = reference_page.locator("canvas[data-fw-title='Forecast horizon']")
        reference_canvas.scroll_into_view_if_needed()
        source_name = "historical" if reference_source["baseline"] else "opt-out-control"
        _capture_forecast_chart(reference_page, "Forecast horizon",
            shots / f"line-forecasts-{width}-{source_name}.png")
        (shots / f"line-forecasts-{width}-source.json").write_text(json.dumps(reference_source))
        chart = page.locator("canvas[data-fw-title='Forecast horizon']")
        chart.scroll_into_view_if_needed()
        cid = chart.get_attribute("id")
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)
        state = page.evaluate(_STATE, "Forecast horizon")
        assert historical["ds"][0] == state["ds"][0]
        for key in ("width", "height", "min", "max", "ymin", "ymax", "visible"):
            assert historical[key] == state[key]
        for before, after in zip(historical["ds"], state["ds"]):
            assert before["data"] == after["data"]
            assert [(p["x"], p["y"], p["radius"]) for p in before["points"]] == [
                (p["x"], p["y"], p["radius"]) for p in after["points"]]
        reference_page.close()
        assert state["visible"] == [True, True]
        baseline = page.evaluate(_STATE, "Forecast opt-out baseline")
        assert baseline["ds"][0]["data"] == state["ds"][0]["data"]
        assert baseline["ds"][1]["data"] == state["ds"][1]["data"]
        assert (baseline["height"], baseline["ymin"], baseline["ymax"]) == (
            state["height"], state["ymin"], state["ymax"])
        assert baseline["width"] == state["width"]
        for before, after in zip(baseline["ds"], state["ds"]):
            assert before["data"] == after["data"]
            assert (before["border"], before["tension"]) == (after["border"], after["tension"])
            assert [(p["x"], p["y"], p["radius"]) for p in before["points"]] == [
                (p["x"], p["y"], p["radius"]) for p in after["points"]]
        page.locator("canvas[data-fw-title='Forecast opt-out baseline']").screenshot(
            path=str(shots / f"line-forecasts-{width}-baseline-opt-out.png"))
        assert state["ds"][0]["data"] == [42, 45, 43, 48, 51, 49, 54, None, None, None]
        assert state["ds"][1]["data"] == [None, None, None, None, None, None, None, 52, 55, 57]
        assert state["ds"][0]["dash"] is None and not state["ds"][0]["forecast"]
        assert state["ds"][1]["dash"] == [7, 4] and state["ds"][1]["forecast"]
        assert state["ds"][1]["color"] == "#c2410c"
        assert [p["radius"] for p in state["ds"][1]["points"]] == [0] * 7 + [2, 2, 2]
        dense = page.evaluate(_STATE, "Dense forecast")
        assert dense["ds"][0]["forecast"] and sum(p["radius"] > 0 for p in dense["ds"][0]["points"]) == 0
        isolated = page.evaluate(_STATE, "Forecast isolated points")
        assert isolated["ds"][0]["data"][249] == 20
        assert [i for i, p in enumerate(isolated["ds"][0]["points"]) if p["radius"] > 0] == [67, 249, 412]
        border_bg = page.evaluate("""id=>{const c=window._chartInstances[id];return c.getDatasetMeta(1).data
            .map(p=>({fill:p.options.backgroundColor,border:p.options.borderColor}));}""", cid)
        assert all(p["fill"] == "#ffffff" and p["border"] == "#c2410c" for p in border_bg[7:]), border_bg
        # Verify an actual hollow marker raster, not only its resolved options.
        raster = page.evaluate("""id=>{const c=window._chartInstances[id],r=c.currentDevicePixelRatio;
            const p=c.getDatasetMeta(1).data[7],x=Math.round(p.x*r),y=Math.round(p.y*r);
            return Array.from(c.ctx.getImageData(x-1,y-1,3,3).data);}""", cid)
        assert any(raster[i:i+4] == [255, 255, 255, 255] for i in range(0, len(raster), 4)), raster
        legend = page.locator(f"#{cid}").locator("xpath=../preceding-sibling::div[1]")
        key = legend.locator(".fw-forecast-key")
        assert key.count() == 1
        forecast_chip = legend.locator(".cl").filter(has_text="Forecast")
        assert "forecast series" in forecast_chip.get_attribute("aria-label")
        assert forecast_chip.inner_text().strip() == "Forecast"
        # Existing chip toggling and double-click isolate continue to operate.
        forecast_chip.click()
        assert page.evaluate("id=>window._chartInstances[id].isDatasetVisible(1)", cid) is False
        forecast_chip.dblclick()
        assert page.evaluate("id=>window._chartInstances[id].data.datasets.map((_,i)=>window._chartInstances[id].isDatasetVisible(i))", cid) == [False, True]
        forecast_chip.dblclick()
        assert page.evaluate("id=>window._chartInstances[id].data.datasets.map((_,i)=>window._chartInstances[id].isDatasetVisible(i))", cid) == [True, True]
        point = page.evaluate("id=>{const c=window._chartInstances[id],p=c.getDatasetMeta(1).data[7];return {x:p.x,y:p.y,box:c.canvas.getBoundingClientRect().toJSON()};}", cid)
        page.mouse.move(point["box"]["x"] + point["x"], point["box"]["y"] + point["y"])
        page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.[0]?.dataset.label==='Forecast'", arg=cid)
        assert page.evaluate("id=>window._chartInstances[id].tooltip.dataPoints[0].parsed.y", cid) == 52
        page.mouse.move(0, 0)
        page.wait_for_function("id=>window._chartInstances[id].tooltip.opacity===0", arg=cid)
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:7,max:9},'none')", cid)
        assert page.evaluate("id=>window._chartInstances[id].data.datasets[1].borderDash", cid) == [7, 4]
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", cid)
        width_before_resize = page.evaluate("id=>window._chartInstances[id].width", cid)
        page.set_viewport_size({"width": width - 100, "height": 900})
        page.wait_for_function("({id,w})=>window._chartInstances[id].width < w", arg={"id": cid, "w": width_before_resize})
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_function("({id,w})=>window._chartInstances[id].width >= w-1", arg={"id": cid, "w": width_before_resize})
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[1].borderDash.join(',')==='7,4'", arg=cid)
        bound_canvas = page.locator("canvas[data-fw-title='Bound forecast comparison']")
        bound_canvas.scroll_into_view_if_needed()
        bound_id = bound_canvas.get_attribute("id")
        assert page.evaluate("id=>window._chartInstances[id].data.datasets[1].data", bound_id) == [88 + 2 * i for i in range(7)]
        page.get_by_role("button", name="Mobile", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===49", arg=bound_id)
        assert page.evaluate("id=>window._chartInstances[id].data.datasets[1].data", bound_id) == [47 + day for day in range(1, 8)]
        ratio = page.evaluate(_STATE, "Bound forecast ratios")
        assert ratio["ds"][1]["data"] == [(47 + day) / 10 for day in range(1, 8)]
        assert ratio["ds"][1]["dash"] == [7, 4] and ratio["ds"][1]["forecast"]
        # Theme switch then lazy reveal updates the hollow center to the card surface.
        for theme, expected in (("trellum dark", "#181b1f"), ("trellum light", "#ffffff")):
            page.locator("#fwThemeSelect").select_option(theme)
            chart.scroll_into_view_if_needed()
            page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=cid)
            fills = page.evaluate("id=>window._chartInstances[id].getDatasetMeta(1).data.slice(7).map(p=>p.options.backgroundColor)", cid)
            assert fills == [expected] * 3
            ratio_canvas = page.locator("canvas[data-fw-title='Bound forecast ratios']")
            ratio_canvas.scroll_into_view_if_needed()
            ratio_id = ratio_canvas.get_attribute("id")
            page.wait_for_function("id=>!document.getElementById(id)._fwPendingTheme", arg=ratio_id)
            assert page.evaluate("""id=>{const c=window._chartInstances[id],d=c.data.datasets[1];
                return c.getDatasetMeta(1).data.every(p=>p.options.borderColor===d.borderColor);
            }""", ratio_id)
            chart.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / f"line-forecasts-{width}-{theme.split()[1]}.png"))
        page.evaluate("window.renderAll()")
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        _capture_forecast_chart(page, "Forecast horizon", shots / f"line-forecasts-{width}-changed.png")
        page.evaluate("window._fwDownloadCsv=(headers,rows,name)=>window.forecastCsv={headers,rows,name}")
        page.locator(f"[data-csv-chart='{cid}']").click()
        assert page.evaluate("window.forecastCsv.rows.map(r=>r.slice(1))") == [
            [actual, predicted] for actual, predicted in zip(state["ds"][0]["data"], state["ds"][1]["data"])]
        assert errors == []
        browser.close()


def test_line_forecasts_chromium_desktop(tmp_path):
    _forecast_browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_line_forecasts_chromium_phone(tmp_path):
    _forecast_browser_check(tmp_path, 390)


def test_example_module_builds_with_zero_warn_fail(tmp_path):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_line_styles.py"
    spec = importlib.util.spec_from_file_location("examples_line_styles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = _context(tmp_path)
    module.add_sections(ctx)
    from trellum.reporting.diagnostics import compute_details
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, [dataclasses.asdict(c) for c in result.checks if c.level in ("warn", "fail")]
    render_report(ctx, str(tmp_path), auto_refresh=False, validation=result)
    result.write(str(tmp_path))
    assert (tmp_path / "index.html").is_file()


def test_forecast_example_module_has_full_diagnostics_and_builds(tmp_path):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_line_forecasts.py"
    spec = importlib.util.spec_from_file_location("examples_line_forecasts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = _context(tmp_path)
    module.add_sections(ctx)
    from trellum.reporting.diagnostics import compute_details
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, [
        dataclasses.asdict(c) for c in result.checks if c.level in ("warn", "fail")]
    render_report(ctx, str(tmp_path), auto_refresh=False, validation=result)
    result.write(str(tmp_path))
    assert (tmp_path / "index.html").is_file()
