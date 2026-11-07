"""Grouped LineChart events: contracts, native geometry, paint and lifecycle."""
from __future__ import annotations

import base64
import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.assets import load_css, load_js
from trellum.components import AreaChart, BarChart, ComboChart, LineChart, RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.events import _weekday_highlight_config, local_annotation_events
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = "54703deee7d4ad41c84c32a5151fcedf00660453"


def _df():
    return pd.DataFrame({"day": ["2026-08-01", "2026-08-02"], "value": [12, 31],
                         "users": [2, 3], "channel": ["Direct", "Partner"]})


def test_event_labels_is_keyword_only_and_preserves_subclasses():
    @dataclasses.dataclass
    class Child(LineChart):
        extra: str = "child"

    assert inspect.signature(Child).parameters["extra"].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert inspect.signature(Child).parameters["event_labels"].kind == inspect.Parameter.KEYWORD_ONLY
    assert Child(_df(), "day", "value", "Title").event_labels == "adaptive"
    ctx = RenderContext(theme=THEME_REGISTRY["trellum light"])
    Child(_df(), "day", "value").render_html(ctx)
    assert next(iter(ctx.component_data.values()))["event_labels"] == "adaptive"
    assert [f.name for f in dataclasses.fields(LineChart) if f.init and not f.kw_only][-2:] == ["height", "header_summary"]


@pytest.mark.parametrize("mode", ["adaptive", "grouped", "full"])
@pytest.mark.parametrize("options", [
    {"static": True}, {"dataset_id": "d"}, {"dataset_id": "d", "static": True},
    {"dataset_id": "d", "stack_by": "channel"},
    {"dataset_id": "d", "stack_by_options": {"Channel": "channel"}},
    {"dataset_id": "d", "ratios": [{"numerator": "value", "denominator": "users", "label": "Rate"}]},
    {"dataset_id": "d", "stack_by": "channel", "ratios": [{"numerator": "value", "denominator": "users", "label": "Rate"}]},
])
def test_mode_is_carried_through_every_config_path(mode, options):
    ctx = RenderContext(theme=THEME_REGISTRY["trellum light"])
    LineChart(_df(), "day", "value", event_labels=mode, **options).render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["event_labels"] == mode
    if options.get("dataset_id"):
        assert cfg["dataset_id"] == "d" and "datasets" not in cfg


@pytest.mark.parametrize("value", [None, True, False, 1, "", "GROUPED", "automatic", [], {}])
def test_invalid_and_mutated_event_labels_fail_at_render(value):
    with pytest.raises(ValueError, match="event_labels"):
        LineChart(_df(), "day", "value", event_labels=value)
    chart = LineChart(_df(), "day", "value")
    chart.event_labels = value
    with pytest.raises(ValueError, match="event_labels"):
        chart.render_html(RenderContext(theme=THEME_REGISTRY["trellum light"]))


def test_other_families_do_not_emit_event_labels():
    for chart in [AreaChart(_df(), "day", "value"), BarChart(_df(), "day", "value", "Bar"),
                  ComboChart(_df(), "day", ["value"], ["users"], "Combo")]:
        ctx = RenderContext(theme=THEME_REGISTRY["trellum light"])
        chart.render_html(ctx)
        assert "event_labels" not in next(iter(ctx.component_data.values()))


def _module():
    path = _ROOT / "trellum/demo/reports/component-showcase/examples_annotations.py"
    spec = importlib.util.spec_from_file_location("examples_annotations", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(output, annotations):
    return ReportContext(config={"name": "Synthetic event presentation", "studio": "shared", "category": "Test",
        "description": "Fixed synthetic observations and events, with original dates and native chart marks.",
        "theme": "trellum light", "annotations": annotations}, slug="annotation-events", output_dir=str(output),
        theme=THEME_REGISTRY["trellum light"])


def _extras(config):
    return {"_events": local_annotation_events(config), "_weekday_highlight": _weekday_highlight_config(config)}


def test_native_example_module_has_zero_warn_fail_and_builds(tmp_path):
    module = _module()
    ctx = _context(tmp_path, module.ANNOTATIONS)
    module.add_sections(ctx)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert result.summary["warn"] == result.summary["fail"] == 0, [dataclasses.asdict(c) for c in result.checks if c.level in ("warn", "fail")]
    extra_data = _extras(ctx.config)
    render_report(ctx, str(tmp_path), data=extra_data, auto_refresh=False, validation=result, details=details)
    result.write(str(tmp_path))
    assert (tmp_path / "index.html").is_file()
    assert len(json.loads((tmp_path / "data.json").read_text())["_events"]) == 13


def _historical(path):
    try:
        return subprocess.check_output(["git", "show", f"{_BASELINE}:{path}"], cwd=_ROOT, stderr=subprocess.DEVNULL).decode()
    except subprocess.CalledProcessError:
        return None


def _build(output, *, baseline=False):
    output.mkdir(parents=True, exist_ok=True)
    module = _module()
    def line(*args, **kwargs):
        kwargs.setdefault("event_labels", "grouped")
        return LineChart(*args, **kwargs)
    historical = _historical("trellum/components/charts/line_area.py") if baseline else None
    if historical:
        # Execute the actual required baseline Python, without another checkout
        # or changes to any tracked file. Assets below also come from that ref.
        path = output / "baseline_line_area.py"
        path.write_text(historical)
        spec = importlib.util.spec_from_file_location("_annotation_baseline_line_area", path)
        old = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = old
        spec.loader.exec_module(old)
        def line(*args, **kwargs):
            kwargs.pop("event_labels", None)
            return old.LineChart(*args, **kwargs)
    elif baseline:
        # Source archives/shallow clones may not carry the baseline object.
        # This is explicitly a CURRENT FULL-MODE CONTROL, never historical.
        def line(*args, **kwargs):
            kwargs["event_labels"] = "full"
            return LineChart(*args, **kwargs)
    module.LineChart = line
    annotations = {**module.ANNOTATIONS, "events": module.ANNOTATIONS["events"] + [
        {"date": "2026-08-06", "type": "release", "label": '<img src=x onerror="window.eventXss=true"> & literal'},
        {"date": "2026-08-07", "type": "release", "studio": "elsewhere", "label": "Synthetic other studio"},
        {"date": "2026-08-14", "type": "power_bet", "default_visible": False, "label": "Synthetic hidden default"},
    ]}
    ctx = _context(output, annotations)
    module.add_sections(ctx)
    daily = ctx.sections[0]["components"][1].df
    bound = ctx.sections[1]["components"][0].df
    ratios = [{"numerator": "observations", "denominator": "observations", "label": "Supplied ratio"}]
    ctx.add_section("Native regression controls", [
        line(bound, "day", "observations", "Bound stacked events", dataset_id="showcase_annotations_daily", stack_by="channel"),
        line(bound, "day", "observations", "Bound ratio events", dataset_id="showcase_annotations_daily", ratios=ratios),
        line(bound, "day", "observations", "Bound stacked ratio events", dataset_id="showcase_annotations_daily", ratios=ratios, stack_by="channel"),
        line(daily.iloc[:0], "day", "observations", "Empty events", static=True),
        line(daily.iloc[0:2], "day", "observations", "Sparse events", static=True),
        AreaChart(daily, "day", "observations", "Area unchanged", static=True),
        BarChart(daily, "day", "observations", "Bar unchanged", static=True),
        ComboChart(daily, "day", ["observations"], ["observations"], "Combo unchanged", static=True),
    ])
    extra_data = _extras(ctx.config)
    render_report(ctx, str(output), data=extra_data, auto_refresh=False)
    copy_vendor_tree(output)
    if historical:
        html = (output / "index.html").read_text()
        for path, current in [
            ("trellum/static/js/runtime/annotations.js", load_js("runtime/annotations.js")),
            ("trellum/static/js/components/chart_base.js", load_js("components/chart_base.js")),
            ("trellum/static/css/components/chart_base.css", load_css("components/chart_base.css")),
        ]:
            assert current in html
            html = html.replace(current, _historical(path))
        (output / "index.html").write_text(html)
    return "required historical baseline " + _BASELINE if historical else "current full-mode control"


def _open(page, output, *, query="", storage=None, legacy=False):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip("/")
        if path.is_file():
            body = path.read_bytes()
            if legacy and path.name == "data.json":
                data = json.loads(body)
                for cfg in data["components"].values():
                    cfg.pop("event_labels", None)
                body = json.dumps(data).encode()
            route.fulfill(body=body, content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404, body="Missing fixture asset")
    page.route("**/*", serve)
    if storage:
        page.add_init_script("localStorage.setItem('fw-anno-visible-annotation-events'," + json.dumps(json.dumps(storage)) + ")")
    page.goto("http://events.test/index.html" + query, wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    for cid in page.locator("canvas[data-fw-kind]").evaluate_all("els=>els.map(e=>e.id)"):
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)


_STATE = """title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    const annotations=Chart.registry.plugins.get('annotation').getAnnotations(c);
    return {id:c.canvas.id, height:c.height, area:{left:c.chartArea.left,right:c.chartArea.right,top:c.chartArea.top,bottom:c.chartArea.bottom},
      scales:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,{min:s.min,max:s.max,ticks:s.ticks.map(t=>t.label)}])),
      datasets:c.data.datasets.map((d,i)=>({values:d.data, color:d.borderColor,fill:d.backgroundColor,width:d.borderWidth,dash:d.borderDash||[],
        points:c.getDatasetMeta(i).data.map(p=>({x:p.x,y:p.y,radius:p.options.radius}))})),
      annotations:annotations.map(a=>({id:a.options.id,type:a.options.type,x:a.x,x2:a.x2,y:a.y,y2:a.y2,
        width:a.width,height:a.height,border:a.options.borderColor,borderWidth:a.options.borderWidth,
        dash:a.options.borderDash,background:a.options.backgroundColor,labelDisplay:a.options.label?.display})),
      markers:(c._fwEventMarkers||[]).map(g=>({left:g.left,top:g.top,width:g.width,height:g.height,text:g.text,anchor:g.anchor,
        events:g.events.map(d=>({index:d.index,key:d.key,event:d.event}))}))};
}"""


def _geometry(state):
    return {**{k: v for k, v in state.items() if k not in ("id", "markers", "annotations")},
            "annotations": [{k: v for k, v in a.items() if k != "labelDisplay"} for a in state["annotations"]]}


def _canvas(page, title):
    return page.locator(f"canvas[data-fw-title='{title}']")


def _details(page, title):
    return page.locator("#" + _canvas(page, title).get_attribute("id") + "-event-details")


def _assert_groups(page, title):
    state = page.evaluate(_STATE, title)
    marks = state["markers"]
    indexes = [ev["index"] for g in marks for ev in g["events"]]
    assert len(indexes) == len(set(indexes))
    # Check population against real native line/band geometry, independently of
    # the marker settings (including clipped and collapsed weekly ranges).
    expected = [int(a["id"].rsplit("_", 1)[1]) for a in state["annotations"]
                if a["id"].startswith("ev_") and max(a["x"], a["x2"]) >= state["area"]["left"]
                and min(a["x"], a["x2"]) <= state["area"]["right"]]
    assert sorted(indexes) == sorted(expected)
    assert all(state["area"]["left"] <= m["left"] and m["left"] + m["width"] <= state["area"]["right"] + .01 for m in marks)
    assert all(a["left"] + a["width"] + 3.99 <= b["left"] for a, b in zip(marks, marks[1:]))
    details = _details(page, title)
    assert details.locator("[data-event-item]").count() == len(indexes)
    assert sorted(map(int, details.locator("[data-event-item]").evaluate_all("els=>els.map(e=>e.dataset.eventItem)"))) == sorted(indexes)
    assert not page.evaluate("id => !!window._chartInstances[id]._fwAnnotationDetails", state["id"])
    return state


def _browser_check(tmp_path, width):
    from playwright.sync_api import sync_playwright

    current, baseline = tmp_path / "current", tmp_path / "baseline"
    _build(current)
    baseline_label = _build(baseline, baseline=True)
    shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", str(tmp_path / "evidence")))
    shots.mkdir(parents=True, exist_ok=True)
    title = "Synthetic daily · grouped events"
    compared = [title, "Synthetic weekly · snapped dates", "Synthetic selected window", "Bound stacked events", "Bound ratio events", "Bound stacked ratio events", "Sparse events", "Area unchanged", "Bar unchanged", "Combo unchanged"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        old = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        _open(old, baseline)
        before = {t: old.evaluate(_STATE, t) for t in compared}
        _canvas(old, title).scroll_into_view_if_needed()
        old.screenshot(path=str(shots / f"events-{width}-baseline.png"))
        (shots / f"events-{width}-baseline.json").write_text(json.dumps({"reference": baseline_label, "charts": before}, indent=2))
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1, has_touch=width < 600)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        _open(page, current)
        assert page.evaluate("""() => {
            const e=window._reportData._events,l=Object.values(window._chartInstances)[0].data.labels, collected=[];
            const full=window._buildAnnotations(e,l),grouped=window._buildAnnotations(e,l,collected);
            const originals=collected.every(d=>d.event===e[d.index]&&grouped[d.key]===d.annotation);
            for(const d of collected) {if(d.annotation.label.display!==false)return false; d.annotation.label.display=true;}
            return originals&&JSON.stringify(full)===JSON.stringify(grouped);
        }""")
        for t in compared:
            assert _geometry(page.evaluate(_STATE, t)) == _geometry(before[t]), t
        state = _assert_groups(page, title)
        assert state["height"] == 220 and len(state["markers"]) > 1
        assert sum(len(m["events"]) for m in state["markers"]) == 15
        assert all(not a["labelDisplay"] for a in state["annotations"] if a["id"].startswith("ev_"))
        # Weekdays have no labels, and retain every native property exactly.
        assert [a for a in state["annotations"] if a["id"].startswith("wd_")] == [a for a in before[title]["annotations"] if a["id"].startswith("wd_")]
        full = page.evaluate(_STATE, "Synthetic daily · full labels")
        assert all(a["labelDisplay"] for a in full["annotations"] if a["id"].startswith("ev_"))
        assert _details(page, "Synthetic daily · full labels").count() == 0
        for t in ("Empty events", "Synthetic quiet dates", "Synthetic category axis"):
            assert _details(page, t).count() == 0
        weekly = _assert_groups(page, "Synthetic weekly · snapped dates")
        weekly_events = [e["event"] for m in weekly["markers"] for e in m["events"]]
        assert any(e["date"] == "2026-07-28" and e["end_date"] == "2026-08-05" for e in weekly_events)
        assert not any(e["date"] >= "2026-08-26" for e in weekly_events)
        weekly_anno = {a["id"]: a for a in weekly["annotations"]}
        assert weekly_anno["ev_line_4"]["x"] == weekly_anno["ev_line_7"]["x"]  # both snap to 3 August
        # Grouped disclosure is native, keyboard reachable and additive to header context.
        canvas = _canvas(page, title)
        cid = canvas.get_attribute("id")
        canvas.scroll_into_view_if_needed()
        details = _details(page, title)
        assert not details.evaluate("el=>el.open")
        described = canvas.get_attribute("aria-describedby").split()
        assert described.count(cid + "-header-context") == described.count(cid + "-event-details") == 1
        details.locator("summary").focus()
        page.keyboard.press("Enter")
        assert details.evaluate("el=>el.open")
        assert "2026-07-28 – 2026-08-05" in details.inner_text()
        assert '<img src=x onerror="window.eventXss=true"> & literal' in details.inner_text()
        assert details.locator("img").count() == 0 and not page.evaluate("window.eventXss===true")
        assert details.locator(".fw-event-list").evaluate("el=>el.clientHeight<=280")
        # Snapshot fixture uses the real vendored capture library and canvas.
        capture = canvas.evaluate("el=>fw.captureElementSnapshotForAnalysis(el.parentElement)")
        image = base64.b64decode(capture["image"]["data_base64"])
        assert len(image) > 2000
        (shots / f"events-{width}-native-capture.png").write_bytes(image)
        assert details.evaluate("el=>el.open")
        section_capture = canvas.evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        expanded_image = base64.b64decode(section_capture["image"]["data_base64"])
        (shots / f"events-{width}-section-expanded.png").write_bytes(expanded_image)
        assert details.evaluate("el=>el.open")
        details.locator("summary").click()
        assert not details.evaluate("el=>el.open")
        assert details.locator(".fw-event-note, .fw-event-list").evaluate_all("els=>els.every(el=>getComputedStyle(el).display==='none')")
        section_capture = canvas.evaluate("el=>fw.captureElementSnapshotForAnalysis(el.closest('.fw-section'))")
        collapsed_image = base64.b64decode(section_capture["image"]["data_base64"])
        (shots / f"events-{width}-section-collapsed.png").write_bytes(collapsed_image)
        assert struct.unpack(">II", expanded_image[16:24])[1] > struct.unpack(">II", collapsed_image[16:24])[1] + 200
        assert not details.evaluate("el=>el.open")
        canvas.scroll_into_view_if_needed()
        page.screenshot(path=str(shots / f"events-{width}-grouped.png"))
        # Real canvas pixels differ from legacy labels, including the marker box.
        paint = canvas.evaluate("""el=>{const c=window._chartInstances[el.id],g=c._fwEventMarkers[0],ctx=el.getContext('2d'),r=c.currentDevicePixelRatio;
            const d=ctx.getImageData(Math.ceil(g.left*r),Math.ceil(g.top*r),Math.floor(g.width*r),Math.floor(g.height*r)).data;
            return {alpha:Array.from(d).filter((_,i)=>i%4===3&&d[i]>0).length, colors:new Set(Array.from(d).filter((_,i)=>i%4!==3)).size};}""")
        assert paint["alpha"] > 150 and paint["colors"] > 5
        # Native click targets open exactly this chart's group, never cross-filter or zoom.
        page.evaluate("()=>{window.eventCrossFilters=[]; window._fwCrossFilter=(...args)=>window.eventCrossFilters.push(args);}")
        for group_index in range(2):
            canvas.scroll_into_view_if_needed()
            marker = page.evaluate(_STATE, title)["markers"][group_index]
            box = canvas.bounding_box()
            page.mouse.click(box["x"] + marker["left"] + marker["width"] / 2, box["y"] + marker["top"] + marker["height"] / 2)
            assert details.evaluate("el=>el.open")
            expected_index = marker["events"][0]["index"]
            assert page.evaluate("document.activeElement.dataset.eventFocus") == f"group:{expected_index}"
        assert page.evaluate("window.eventCrossFilters") == []
        assert page.evaluate("id=>window._chartInstances[id].scales.x.min", cid) == 0
        if width < 600:
            details.locator("summary").click()
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            marker = page.evaluate(_STATE, title)["markers"][-1]
            page.touchscreen.tap(box["x"] + marker["left"] + marker["width"] / 2, box["y"] + marker["top"] + marker["height"] / 2)
            assert details.evaluate("el=>el.open")
            assert page.evaluate("document.activeElement.dataset.eventFocus") == f"group:{marker['events'][0]['index']}"
        canvas.scroll_into_view_if_needed()
        page.screenshot(path=str(shots / f"events-{width}-expanded.png"))
        # Surviving event retains focus across resize, filter re-creation and renderAll.
        item = details.locator('[data-event-item="4"]')
        item.focus()
        page.set_viewport_size({"width": width - 80, "height": 900})
        page.wait_for_function("({id,w})=>window._chartInstances[id].width<w", arg={"id": cid, "w": state["area"]["right"] + 50})
        _assert_groups(page, title)
        assert page.evaluate("document.activeElement.dataset.eventFocus") == "event:4"
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("window.renderAll();window.renderAll()")
        assert details.evaluate("el=>el.open")
        assert page.evaluate("document.activeElement.dataset.eventFocus") == "event:4"
        assert page.locator(f"#{cid}-event-details").count() == 1
        # Type toggles preserve full geometry; source order and visibility stay authoritative.
        page.locator('[data-anno-type="release"]').click()
        assert not any(e["event"]["type"] == "release" for m in _assert_groups(page, title)["markers"] for e in m["events"])
        page.locator('[data-anno-type="release"]').click()
        assert sum(len(m["events"]) for m in _assert_groups(page, title)["markers"]) == 15
        page.locator('[data-anno-type="power_bet"]').click()
        assert "Synthetic hidden default" in details.text_content()
        page.locator('[data-anno-type="power_bet"]').click()
        page.evaluate("window._currentScope='active-studio';window._refreshAllAnnotations()")
        assert "Synthetic other studio" not in details.text_content()
        page.evaluate("window._currentScope=null;window._refreshAllAnnotations()")
        assert "Synthetic other studio" in details.text_content()
        # Zoom membership comes from EXISTING positioned ranges, keeping full dates.
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:6,max:10},'none')", cid)
        zoomed = _assert_groups(page, title)
        assert len(zoomed["markers"]) >= 1
        old_id = before[title]["id"]
        old.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:6,max:10},'none')", old_id)
        assert _geometry(zoomed) == _geometry(old.evaluate(_STATE, title))
        assert "2026-08-04 – 2026-08-12" in details.text_content()
        assert "2026-07-28" not in details.text_content()
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", cid)
        old.evaluate("id=>window._chartInstances[id].resetZoom('none')", old_id)
        assert sum(len(m["events"]) for m in _assert_groups(page, title)["markers"]) == 15
        # Filter engine: membership narrows and resets independently of static examples.
        live_title = "Synthetic selected window"
        live_id = _canvas(page, live_title).get_attribute("id")
        date_control = page.locator(".fw-date-range").first
        date_control.locator(".fw-date-trigger").click()
        date_control.locator('input[data-edge="min"]').fill("2026-08-18")
        date_control.locator('input[data-edge="min"]').dispatch_event("change")
        date_control.locator('input[data-edge="max"]').fill("2026-08-20")
        date_control.locator('input[data-edge="max"]').dispatch_event("change")
        page.wait_for_function("id=>window._chartInstances[id].data.labels.length===3", arg=live_id)
        live_state = _assert_groups(page, live_title)
        old.evaluate("fw.filterEngine.setFilter('showcase_annotations_daily','event-window','day','range',{min:'2026-08-18',max:'2026-08-20'})")
        for t in (live_title, "Bound stacked events", "Bound ratio events", "Bound stacked ratio events"):
            assert _geometry(page.evaluate(_STATE, t)) == _geometry(old.evaluate(_STATE, t)), t
        assert sum(len(m["events"]) for m in live_state["markers"]) == 1
        assert sum(len(m["events"]) for m in _assert_groups(page, title)["markers"]) == 15
        date_control.locator(".fw-date-preset").filter(has_text="All").click()
        date_control.locator(".fw-date-trigger").click()
        page.wait_for_function("id=>window._chartInstances[id].data.labels.length===31", arg=live_id)
        page.get_by_role("button", name="Partner", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]===41", arg=live_id)
        assert sum(len(m["events"]) for m in _assert_groups(page, live_title)["markers"]) == 15
        assert page.evaluate(_STATE, title)["datasets"] == state["datasets"]
        page.get_by_role("button", name="All", exact=True).click()
        # Exercise the actual bound factory cross-filter and drag-zoom handlers.
        interaction_title = "Bound stacked events"
        interaction_canvas = _canvas(page, interaction_title)
        interaction_id = interaction_canvas.get_attribute("id")
        page.evaluate("""id=>{const cfg=window._reportData.components[id];cfg.crossFilter='channel';cfg.crossFilterMode='dataset';
            window._fwRenderers.chart(id,cfg);window.eventCrossFilters=[];}""", interaction_id)
        interaction_canvas.scroll_into_view_if_needed()
        interaction_state = _assert_groups(page, interaction_title)
        marker = interaction_state["markers"][0]
        box = interaction_canvas.bounding_box()
        x, y = box["x"] + marker["left"] + marker["width"] / 2, box["y"] + marker["top"] + marker["height"] / 2
        filter_state = page.evaluate("JSON.stringify(fw.filterEngine._state)")
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 35, y + 50, steps=5)
        page.mouse.up()
        assert page.evaluate("id=>window._chartInstances[id].scales.x.min", interaction_id) == 0
        interaction_canvas.scroll_into_view_if_needed()
        box = interaction_canvas.bounding_box()
        page.mouse.click(box["x"] + marker["left"] + marker["width"] / 2, box["y"] + marker["top"] + marker["height"] / 2)
        assert page.evaluate("window.eventCrossFilters") == []
        assert page.evaluate("JSON.stringify(fw.filterEngine._state)") == filter_state
        # Outside markers, native nearest tooltips, cross-filter and zoom work.
        point = page.evaluate("id=>{const p=window._chartInstances[id].getDatasetMeta(0).data[15];return {x:p.x,y:p.y}}", interaction_id)
        page.mouse.move(box["x"] + point["x"], box["y"] + point["y"])
        page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.length===1", arg=interaction_id)
        page.mouse.click(box["x"] + point["x"], box["y"] + point["y"])
        page.wait_for_function("window.eventCrossFilters.length===1")
        assert page.evaluate("window.eventCrossFilters[0][0]") == "showcase_annotations_daily"
        page.mouse.move(box["x"] + 70, box["y"] + 100)
        page.mouse.down()
        page.mouse.move(box["x"] + 170, box["y"] + 100, steps=5)
        page.mouse.up()
        page.wait_for_function("id=>window._chartInstances[id].scales.x.min>0", arg=interaction_id)
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", interaction_id)
        # A plot drag that ends over a marker still belongs to drag zoom.
        page.mouse.move(box["x"] + 170, box["y"] + 100)
        page.mouse.down()
        page.mouse.move(box["x"] + marker["left"] + marker["width"] / 2,
                        box["y"] + marker["top"] + marker["height"] / 2, steps=5)
        page.mouse.up()
        page.wait_for_function("id=>window._chartInstances[id].scales.x.max<30", arg=interaction_id)
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", interaction_id)
        # Disappearing focused event returns to this summary; unrelated focus is left alone.
        details.locator('[data-event-item="4"]').focus()
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:17,max:20},'none')", cid)
        assert page.evaluate("document.activeElement.dataset.eventFocus") == "summary"
        page.locator("#fwThemeSelect").focus()
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", cid)
        assert page.evaluate("document.activeElement.id") == "fwThemeSelect"
        # A valid empty date selection removes the visible row; reset restores it.
        live_details = _details(page, live_title)
        live_details.evaluate("el=>el.open=true")
        live_details.locator('[data-event-item]').first.focus()
        page.evaluate("fw.filterEngine.setFilter('showcase_annotations_daily','event-window','day','range',{min:'2026-09-10',max:'2026-09-11'})")
        assert not _details(page, live_title).is_visible()
        assert page.evaluate("id=>window._chartInstances[id].data.labels.length", live_id) == 0
        assert page.evaluate("document.activeElement.id") == live_id
        assert _canvas(page, live_title).get_attribute("aria-label") == live_title
        page.evaluate("fw.filterEngine.clearFilter('showcase_annotations_daily','event-window')")
        assert _details(page, live_title).is_visible()
        assert page.evaluate("document.activeElement.id") == live_id
        # No dated population leaves no row, while recurring weekday geometry survives.
        visible_before = page.evaluate("({...window._annoVisible})")
        details.evaluate("el=>el.open=true")
        details.locator('[data-event-item]').first.focus()
        page.evaluate("Object.keys(window._annoVisible).filter(k=>k!=='weekday'&&k!=='_storageKey').forEach(k=>window._annoVisible[k]=false);window._refreshAllAnnotations()")
        assert not details.is_visible()
        assert page.evaluate("document.activeElement.id") == cid
        assert cid + '-event-details' not in (canvas.get_attribute('aria-describedby') or '').split()
        assert cid + '-header-context' in canvas.get_attribute('aria-describedby').split()
        assert all(a["id"].startswith("wd_") for a in page.evaluate(_STATE, title)["annotations"])
        page.evaluate("v=>{Object.assign(window._annoVisible,v);window._refreshAllAnnotations();}", visible_before)
        assert details.is_visible()
        # renderAll removes owned DOM before constructing the empty chart.
        details.locator('[data-event-item]').first.focus()
        events_before = page.evaluate("window._reportData._events")
        page.evaluate("window._reportData._events=[];window.renderAll()")
        page.wait_for_function("""id=>window._chartInstances[id]?._fwEventMarkers?.length===0
            && document.activeElement.id===id""", arg=cid)
        assert not details.is_visible()
        assert page.evaluate("document.activeElement.id") == cid
        page.evaluate("events=>{window._reportData._events=events;window.renderAll();}", events_before)
        details.wait_for(state="visible")
        # Hide/reveal resize uses current width and keeps the disclosure state.
        canvas.evaluate("el=>el.parentElement.style.display='none'")
        canvas.evaluate("el=>el.parentElement.style.display=''")
        page.wait_for_function("id=>window._chartInstances[id].width>200", arg=cid)
        _assert_groups(page, title)
        # Lazy chart themes: switch the actual native names then reveal each chart.
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            for t in (title, live_title, "Synthetic weekly · snapped dates"):
                _canvas(page, t).scroll_into_view_if_needed()
                i = _canvas(page, t).get_attribute("id")
                page.wait_for_function("id=>!!window._chartInstances[id] && !document.getElementById(id)._fwPendingTheme", arg=i)
                _assert_groups(page, t)
            assert page.locator("html").get_attribute("data-theme") == theme
            canvas.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / f"events-{width}-{theme.split()[1]}.png"))
        # Actual tiny plot: collision merging retains all events without overlap.
        canvas.evaluate("el=>el.parentElement.style.width='135px'")
        page.wait_for_function("id=>window._chartInstances[id].width<=135", arg=cid)
        narrow = _assert_groups(page, title)
        assert sum(len(m["events"]) for m in narrow["markers"]) == 15
        canvas.evaluate("el=>el.parentElement.style.width=''")
        page.wait_for_function("id=>window._chartInstances[id].width>200", arg=cid)
        # Replaced canvas destroys owned listeners/DOM, then renders a single row.
        canvas.evaluate("""el=>{const id=el.id,c=window._chartInstances[id],next=el.cloneNode();
            c.destroy();delete window._chartInstances[id];el.replaceWith(next);window._fwRenderers.chart(id,window._reportData.components[id]);}""")
        assert page.locator(f"#{cid}-event-details").count() == 1
        _assert_groups(page, title)
        page.evaluate("window._fwDownloadCsv=(headers,rows,name)=>window.eventCsv={headers,rows,name}")
        page.locator(f"[data-csv-chart='{cid}']").click()
        assert page.evaluate("window.eventCsv.rows.map(r=>r[1])") == state["datasets"][0]["values"]
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.evaluate("id=>window._chartInstances[id].destroy()", cid)
        assert page.locator(f"#{cid}-event-details").count() == 0
        assert errors == [], errors
        # Saved state and URL overrides; missing flag retains legacy labels.
        saved = browser.new_page(viewport={"width": width, "height": 900})
        _open(saved, current, storage={"release": False})
        assert not any(e["event"]["type"] == "release" for m in saved.evaluate(_STATE, title)["markers"] for e in m["events"])
        url = browser.new_page(viewport={"width": width, "height": 900})
        _open(url, current, storage={"release": False}, query="?anno=release", legacy=True)
        legacy = url.evaluate(_STATE, title)
        assert not legacy["markers"] and all(a["labelDisplay"] for a in legacy["annotations"] if a["id"].startswith("ev_"))
        assert all(a["border"] == "#00c48c" for a in legacy["annotations"])
        browser.close()


def test_grouped_events_chromium_desktop(tmp_path):
    _browser_check(tmp_path, 1280)


@pytest.mark.heavy
def test_grouped_events_chromium_phone(tmp_path):
    _browser_check(tmp_path, 390)
