"""Combo-only axis context and legends, using genuine report artifacts."""
from __future__ import annotations

import csv
import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import ComboChart, DataSource, FilterBar, LineChart, RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.themes import DefaultTheme


def _frame():
    return pd.DataFrame([
        {"day": f"2026-10-0{i + 1}", "platform": platform,
         "spend": (100 + i * 20) * scale, "fees": (15 + i * 3) * scale,
         "orders": (10 + i * 4) * scale, "visits": (45 + i * 7) * scale}
        for i in range(4) for platform, scale in (("web", 1), ("ios", 2))
    ])


def _report(output, *, context=True):
    frame = _frame()
    ctx = ReportContext(config={"name": "Combo axis examples", "studio": "test",
        "category": "Test", "description": "Synthetic daily spend, fees, orders and visits by platform.",
        "theme": "trellum light"}, slug="combos", output_dir=str(output))
    explicit = {"left_axis_label": "Spend and fees · USD",
                "right_axis_label": "Orders and visits · count"} if context else {}
    ctx.add_section("Combo filters", [DataSource("combos", frame),
        FilterBar("combos", frame, filters=[{"column": "platform", "type": "toggle"}]),
        ComboChart(frame, "day", ["spend", "fees"], ["orders", "visits"],
            "Live currency and counts", bar_labels=["Shared", "Shared"],
            line_labels=["Shared", "Visits"], dataset_id="combos", static=True,
            y_max=1000, y1_max=300, line_colors=["#ab3478"], **explicit),
    ])
    static = frame.iloc[::2].reset_index(drop=True)
    ctx.add_section("Static Combo representations", [
        ComboChart(static, "day", ["spend"], ["orders"], "Static current formatting",
            bar_labels=["Spend"], line_labels=["Orders"], static=True,
            bar_format="currency", line_format="percent", show_labels=True,
            y_max=9999, y1_max=9999, height=220, line_colors=["#ab3478"]),
        ComboChart(static, "day", ["spend"], [], "Bars only", static=True),
        ComboChart(static, "day", [], ["orders"], "Lines only", static=True),
        ComboChart(static.iloc[:0], "day", [], [], "Empty", static=True),
        ComboChart(static, "day", ["spend"], ["orders"], "Long captions", static=True,
            bar_labels=["Spend for the internationally distributed storefront with a very long caption"],
            line_labels=["__proto__ <img src=x onerror=alert(1)> orders with another long caption"]),
        LineChart(static, "day", ["spend", "fees"], "Ordinary line", static=True,
                  y_labels=["Shared", "Fees"]),
    ])
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    return ctx


def _open(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip("/")
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0]
                          or "application/octet-stream")
        else:
            route.fulfill(status=404, body="Missing test asset")
    page.route("**/*", serve)
    page.goto("http://combos.test/index.html", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    for cid in page.locator("canvas[data-fw-kind]").evaluate_all("els=>els.map(e=>e.id)"):
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)


_STATE = """() => Object.values(window._chartInstances).map(c=>({
    id:c.canvas.id,title:c.canvas.dataset.fwTitle,height:c.height,
    axes:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,[s.min,s.max]])),
    datasets:c.data.datasets.map(d=>({label:d.label,data:d.data.slice(),axis:d.yAxisID,
      color:d.borderColor,width:d.borderWidth,radius:typeof d.pointRadius==='number'?d.pointRadius:null,
      tension:d.tension})),
    interaction:c.options.interaction.mode,
    ticks:[c.scales.y.ticks.map(t=>t.label),c.scales.y1?.ticks.map(t=>t.label)],
    labels:c.options.plugins.datalabels.display
}))"""


@pytest.mark.parametrize("name", ["left_axis_label", "right_axis_label"])
@pytest.mark.parametrize("value", [True, False, 0, 1.5, [], {}, "", " \n "])
def test_axis_description_validation(name, value):
    with pytest.raises(ValueError, match=name):
        ComboChart(_frame(), "day", ["spend"], ["orders"], **{name: value})
    chart = ComboChart(_frame(), "day", ["spend"], ["orders"], static=True)
    setattr(chart, name, value)
    with pytest.raises(ValueError, match=name):
        chart.render_html(RenderContext(DefaultTheme))


def test_constructor_subclass_and_payload_compatibility():
    @dataclasses.dataclass
    class Child(ComboChart):
        extra: str = "child"

    for name in ("left_axis_label", "right_axis_label"):
        assert inspect.signature(ComboChart).parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
    assert inspect.signature(Child).parameters["extra"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert Child(_frame(), "day", ["spend"], ["orders"]).extra == "child"
    legacy = Child(_frame(), "day", ["spend"], ["orders"], "Legacy", ["Spend"],
                   ["Orders"], "currency", "number", False, None, True,
                   ["#ab3478"], False, 220, 500, 100, "subclass positional")
    assert legacy.extra == "subclass positional" and legacy.y1_max == 100
    assert legacy.left_axis_label is None and legacy.right_axis_label is None
    with pytest.raises(ValueError, match="left_axis_label"):
        Child(_frame(), "day", [], [], left_axis_label=True)
    for binding in ({"static": True}, {"dataset_id": "bound", "static": True}):
        ctx = RenderContext(DefaultTheme)
        ComboChart(_frame(), "day", ["spend"], ["orders"],
                   left_axis_label="Spend · USD", right_axis_label="Orders · count",
                   **binding).render_html(ctx)
        cfg = next(iter(ctx.component_data.values()))
        assert cfg["combo_axes"] == {"left_label": "Spend · USD", "right_label": "Orders · count"}
        assert bool(cfg.get("dataset_id")) == bool(binding.get("dataset_id"))
        if not cfg.get("dataset_id"):
            assert cfg["datasets"][0]["data"] == _frame().spend.tolist()
            assert cfg["datasets"][1]["data"] == _frame().orders.tolist()


def test_genuine_showcase_module_validates_and_builds(tmp_path):
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report

    source = Path(__file__).resolve().parents[1] / "demo/reports/component-showcase/examples_combos.py"
    spec = importlib.util.spec_from_file_location("examples_combos", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={"name": "Combo showcase", "studio": "test", "category": "Test",
        "description": "Daily additive synthetic channel observations and independent fixed chart examples."},
        slug="combo-showcase", output_dir=str(tmp_path))
    module.add_sections(ctx)
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["fail"] == result.summary["warn"] == 0, result.to_dict()
    render_report(ctx, str(tmp_path), auto_refresh=False)
    assert (tmp_path / "index.html").is_file()


def _check_browser(tmp_path, width):
    from playwright.sync_api import expect, sync_playwright

    output = Path(os.environ.get("TRELLUM_COMBO_EVIDENCE", tmp_path)) / f"current-{width}"
    output.mkdir(parents=True, exist_ok=True)
    _report(output)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open(page, output)
            state = page.evaluate(_STATE)
            (output / "chart-state.json").write_text(json.dumps(state, indent=2))
            baseline = os.environ.get("TRELLUM_COMBO_BASELINE")
            if baseline:
                assert state == json.loads((Path(baseline) / f"state-{width}.json").read_text())
            live = next(c for c in state if c["title"] == "Live currency and counts")
            static = next(c for c in state if c["title"] == "Static current formatting")
            live_id = live["id"]
            assert live["height"] == (320 if width == 1280 else 180)
            assert static["height"] == 220
            assert live["axes"]["y"] == [0, 1000] and live["axes"]["y1"] == [0, 300]
            assert [d["data"] for d in live["datasets"]] == [
                [300, 360, 420, 480], [45, 54, 63, 72], [30, 42, 54, 66], [135, 156, 177, 198]]
            assert static["datasets"][0]["data"] == [100, 120, 140, 160]
            assert static["datasets"][1]["data"] == [10, 14, 18, 22]
            assert static["axes"]["y"][1] != 9999 and static["axes"]["y1"][1] != 9999
            assert static["labels"] is False and any("$" in t for t in static["ticks"][1])
            for chart in (live, static):
                assert chart["interaction"] == "nearest"
                assert chart["datasets"][-1]["width"] == 2.5
                assert chart["datasets"][-1]["tension"] == 0
                assert chart["datasets"][-1]["radius"] == 2

            root = page.locator(f"#{live_id}-combo-context")
            left = root.locator('[data-axis="y"] button')
            right = root.locator('[data-axis="y1"] button')
            assert left.count() == 1 and right.count() == 2
            expect(root.locator('[data-axis="y"]')).to_contain_text("Spend and fees · USD")
            expect(root.locator('[data-axis="y1"]')).to_contain_text("Orders and visits · count")
            # Author-supplied descriptions are text too; update in place.
            page.evaluate("""id=>{const c=window._chartInstances[id];
                c.config.options.plugins.fwLegend.comboAxes.left_label='<img src=x> USD';c.update();}""", live_id)
            expect(root.locator('.fw-combo-description').first).to_have_text('<img src=x> USD')
            assert root.locator('img').count() == 0
            page.evaluate("""id=>{const c=window._chartInstances[id];
                c.config.options.plugins.fwLegend.comboAxes.left_label='Spend and fees · USD';c.update();}""", live_id)
            assert page.locator(f"#{live_id}").get_attribute("aria-describedby") == live_id + "-combo-context"
            assert root.locator('.bar').count() == 1 and root.locator('.line').count() == 2
            assert root.locator('.line').first.evaluate("e=>parseFloat(getComputedStyle(e).borderTopWidth)") >= 2
            assert root.locator('.bar').evaluate("e=>e.getBoundingClientRect().height") == 9

            for title, axis in (("Bars only", "y"), ("Lines only", "y1")):
                cid = next(c["id"] for c in state if c["title"] == title)
                assert page.locator(f"#{cid}-combo-context .fw-combo-axis").count() == 1
                assert page.locator(f"#{cid}-combo-context [data-axis='{axis}']").count() == 1
            empty = next(c["id"] for c in state if c["title"] == "Empty")
            assert page.locator(f"#{empty}-combo-context").is_hidden()
            long_id = next(c["id"] for c in state if c["title"] == "Long captions")
            assert page.locator(f"#{long_id}-combo-context img").count() == 0
            expect(page.locator(f"#{long_id}-combo-context")).to_contain_text("__proto__ <img")
            ordinary = next(c["id"] for c in state if c["title"] == "Ordinary line")
            assert page.locator(f"#{ordinary}-combo-context").count() == 0
            assert page.locator(f"#{ordinary}").evaluate(
                "e=>e.parentElement.previousElementSibling.querySelectorAll('span.cl').length") == 2

            # Actual raster pixels from a bar interior verify Chart.js painted.
            assert page.evaluate("""id=>{const c=window._chartInstances[id],r=c.currentDevicePixelRatio,
              p=c.getDatasetMeta(0).data[0],rgba=c.ctx.getImageData(p.x*r,(p.y+p.base)/2*r,1,1).data;
              return rgba[3]>150 && rgba[1]>rgba[0];}""", live_id)

            page.locator(f"#{live_id}").scroll_into_view_if_needed()
            page.screenshot(path=str(output / "light.png"), full_page=True)
            page.evaluate("""id=>window._comboButton=document.querySelector('#'+id+'-combo-context [data-axis=y] button')""", live_id)
            left.focus()
            left.press("Space")
            def visibility():
                return page.evaluate("id=>window._chartInstances[id].data.datasets.map((_,i)=>window._chartInstances[id].isDatasetVisible(i))", live_id)
            assert visibility() == [False, False, True, True]
            assert page.evaluate("document.activeElement===window._comboButton && window._comboButton.isConnected")
            left.press("Enter")
            assert visibility() == [True] * 4
            right.first.focus()
            right.first.press("Space")
            assert visibility() == [True, True, False, True]
            right.first.press("Enter")
            right.first.click(modifiers=["Shift"])
            assert visibility() == [False, False, True, False]
            right.first.click(modifiers=["Shift"])
            assert visibility() == [True] * 4
            left.dblclick()
            assert visibility() == [True, True, False, False]
            left.dblclick()
            assert visibility() == [True] * 4

            # Actual nearest hit testing and the floating tooltip remain single-series.
            point = page.evaluate("""id=>{const c=window._chartInstances[id],p=c.getDatasetMeta(2).data[1],
                r=c.canvas.getBoundingClientRect();return {x:r.x+p.x,y:r.y+p.y}}""", live_id)
            page.mouse.move(point["x"], point["y"])
            page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.length===1", arg=live_id)
            assert page.evaluate("id=>window._chartInstances[id].tooltip.body[0].lines[0]", live_id) == "Shared: 42"
            page.screenshot(path=str(output / "nearest-tooltip.png"))

            # A legend-hidden line is still present in the real CSV download.
            right.first.click()
            with page.expect_download() as info:
                page.locator(f'[data-csv-chart="{live_id}"]').click(force=True)
            with open(info.value.path(), encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            assert rows[0] == ["label", "Shared", "Shared", "Shared", "Visits"]
            assert rows[1] == ["2026-10-01", "300", "45", "30", "135"]
            assert len(rows) == 5
            right.first.click()

            # The static branch keeps its real legacy currency line tooltip.
            page.locator(f"#{static['id']}").scroll_into_view_if_needed()
            point = page.evaluate("""id=>{const c=window._chartInstances[id],p=c.getDatasetMeta(1).data[1],
                r=c.canvas.getBoundingClientRect();return {x:r.x+p.x,y:r.y+p.y}}""", static["id"])
            page.mouse.move(point["x"], point["y"])
            page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.length===1", arg=static["id"])
            assert page.evaluate("id=>window._chartInstances[id].tooltip.body[0].lines[0]", static["id"]) == "Orders: $14.00"

            for theme in ("trellum dark", "trellum light"):
                page.locator("#fwThemeSelect").select_option(theme)
                for chart in (live, static):
                    cid = chart["id"]
                    page.locator(f"#{cid}").scroll_into_view_if_needed()
                    page.wait_for_function("""id=>{const c=window._chartInstances[id],root=c._fwLegendEl;
                      return c.data.datasets.find(d=>d.type==='line').borderColor==='#ab3478' &&
                        Array.from(root.querySelectorAll('button')).every(btn=>{
                          const [axis,label]=JSON.parse(btn.dataset.comboKey);
                          const ds=c.data.datasets.find(d=>d.yAxisID===axis && d.label===label);
                          return btn.style.getPropertyValue('--series-color')===ds.borderColor;});}""", arg=cid)
                assert page.locator("html").get_attribute("data-theme") == theme
                if theme == "trellum dark":
                    page.screenshot(path=str(output / "dark.png"), full_page=True)

            page.locator(f"#{live_id}").scroll_into_view_if_needed()
            left.focus()
            page.evaluate("window.renderAll()")
            page.wait_for_function("id=>document.activeElement?.closest('.fw-combo-context')?.id===id+'-combo-context'", arg=live_id)
            page.get_by_role("button", name="ios", exact=True).click()
            page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===200", arg=live_id)
            assert page.evaluate("id=>window._chartInstances[id].data.datasets[2].data.slice()", live_id) == [20, 28, 36, 44]
            assert page.evaluate("id=>window._chartInstances[id].data.datasets[0].data.slice()", static["id"]) == [100, 120, 140, 160]
            for _ in range(2):
                page.evaluate("window.renderAll()")
                page.wait_for_function("id=>!!window._chartInstances[id]", arg=live_id)
            assert page.locator(f"#{live_id}-combo-context").count() == 1
            assert page.locator(f"#{live_id}-combo-context button").count() == 3
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert page.locator('.fw-combo-context button').evaluate_all("""buttons=>buttons.every(b=>{
                const r=b.getBoundingClientRect(),p=b.closest('.fw-combo-context').getBoundingClientRect();
                return r.left>=p.left-1 && r.right<=p.right+1 && b.scrollWidth<=b.clientWidth+1;
            })""")
            if width == 390:
                assert page.locator('.fw-combo-context button').evaluate_all(
                    "buttons=>buttons.every(b=>b.getBoundingClientRect().height>=44)")
            # A narrow panel inside a wide report wraps independently of viewport.
            page.locator(f"#{long_id}").evaluate("e=>e.parentElement.parentElement.style.maxWidth='280px'")
            page.locator(f"#{long_id}").scroll_into_view_if_needed()
            narrow = page.locator(f"#{long_id}-combo-context")
            expect(narrow.locator('[data-axis="y1"]')).to_be_visible()
            assert narrow.evaluate("e=>e.scrollWidth<=e.clientWidth")
            page.screenshot(path=str(output / "narrow-panel.png"))
            page.evaluate("id=>{window._chartInstances[id].destroy();delete window._chartInstances[id]}", live_id)
            assert page.locator(f"#{live_id}-combo-context").count() == 0
            assert page.locator(f"#{live_id}").get_attribute("aria-describedby") is None
            assert errors == [], errors
        finally:
            browser.close()


def test_combo_chart_desktop(tmp_path):
    _check_browser(tmp_path, 1280)


@pytest.mark.heavy
def test_combo_chart_phone(tmp_path):
    _check_browser(tmp_path, 390)


def test_untagged_y1_payload_keeps_legacy_legend(tmp_path):
    from playwright.sync_api import sync_playwright

    _report(tmp_path)
    data_path = tmp_path / "data.json"
    data = json.loads(data_path.read_text())
    for cfg in data["components"].values():
        if isinstance(cfg, dict):
            cfg.pop("combo_axes", None)
    data_path.write_text(json.dumps(data))
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            _open(page, tmp_path)
            assert page.locator('.fw-combo-context').count() == 0
            canvas = page.locator('canvas[data-fw-title="Live currency and counts"]')
            assert canvas.evaluate("e=>e.parentElement.previousElementSibling.querySelectorAll('span.cl').length") == 2
        finally:
            browser.close()
