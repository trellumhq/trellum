"""Ordinary line styles: Python contracts and real vendored Chromium lifecycle."""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

from trellum.components import AreaChart, ComboChart, DataSource, FilterBar, LineChart, RenderContext
from trellum.components.charts.line_area import line_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DefaultTheme, THEME_REGISTRY
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
    isolated = _frame(500)
    isolated["value"] = float("nan")
    isolated.loc[[67, 249, 412], "value"] = [10, 20, 0]
    gap = _frame(8)
    gap["value"] = [0, None, 20, 20, None, 0, None, 10]
    plateau = _frame(8)
    plateau["value"] = [0, 20, 20, 80, 80, 0, 100, 100]
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
        AreaChart(_frame(12), "day", "value", "Area", static=True),
        ComboChart(_frame(12), "day", ["value"], ["value"], "Combo", static=True),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


_STATE = """title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    return {id:c.canvas.id, width:c.chartArea.width,height:c.height,
      min:c.scales.x.min,max:c.scales.x.max,ymin:c.scales.y.min,ymax:c.scales.y.max,
      ds:c.data.datasets.map((d,di)=>({data:d.data.slice(),color:d.borderColor,tension:d.tension,
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
        page.get_by_role("button", name="All", exact=True).click()
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


def test_example_module_builds_with_zero_warn_fail(tmp_path):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_line_styles.py"
    spec = importlib.util.spec_from_file_location("examples_line_styles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = _context(tmp_path)
    module.add_sections(ctx)
    result = validate_report(ctx)
    assert result.summary["warn"] == result.summary["fail"] == 0, [dataclasses.asdict(c) for c in result.checks if c.level in ("warn", "fail")]
    render_report(ctx, str(tmp_path), auto_refresh=False, validation=result)
    result.write(str(tmp_path))
    assert (tmp_path / "index.html").is_file()
