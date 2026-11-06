"""Daily month declaration: API compatibility, native report diagnostics and paint."""
from __future__ import annotations

import base64
import dataclasses
import importlib.util
import inspect
import json
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.assets import load_css, load_js
from trellum.components import (
    AreaChart,
    BarChart,
    ComboChart,
    DataSource,
    FilterBar,
    LineChart,
    RenderContext,
    StackedBar,
)
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.runner.events import local_annotation_events
from trellum.themes import THEME_REGISTRY
from trellum.validation import validate_report

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "42ececa30d424131b0096cecd44c74cea92d550c"
FAMILIES = [LineChart, AreaChart, BarChart, StackedBar, ComboChart]


def _df():
    return pd.DataFrame({"day": ["2026-09-30", "2026-10-01"], "value": [0, 23],
                         "users": [3, 11], "channel": ["Direct", "Partner"]})


def _chart(cls, **kwargs):
    args = (_df(), "day", ["value"], ["users"], "Chart") if issubclass(cls, ComboChart) else (
        _df(), "day", ["value"], "Chart") if issubclass(cls, StackedBar) else (_df(), "day", "value", "Chart")
    return cls(*args, **kwargs)


def _payload(chart):
    ctx = RenderContext(theme=THEME_REGISTRY["trellum light"])
    html = chart.render_html(ctx)
    return html, next(iter(ctx.component_data.values()))


@pytest.mark.parametrize("cls", FAMILIES)
@pytest.mark.parametrize("value", [None, 0, 1, "true", "", [], {}, 1.0])
def test_strict_declaration_at_construction_and_render(cls, value):
    with pytest.raises(ValueError, match="month_context"):
        _chart(cls, month_context=value)
    chart = _chart(cls)
    chart.month_context = value
    with pytest.raises(ValueError, match="month_context"):
        _payload(chart)


@pytest.mark.parametrize("cls", FAMILIES)
@pytest.mark.parametrize("options", [{"static": True}, {"dataset_id": "daily"},
                                     {"dataset_id": "daily", "static": True}])
def test_false_is_identical_and_true_preserves_binding(cls, options):
    off = _payload(_chart(cls, **options))
    assert off == _payload(_chart(cls, month_context=False, **options))
    html, cfg = _payload(_chart(cls, month_context=True, **options))
    assert html == off[0]
    assert cfg.pop("month_context") is True
    assert cfg == off[1]
    if options.get("dataset_id"):
        assert cfg["dataset_id"] == "daily" and "datasets" not in cfg


@pytest.mark.parametrize("cls", FAMILIES)
def test_keyword_only_preserves_inherited_and_subclass_positions(cls):
    assert inspect.signature(cls).parameters["month_context"].kind is inspect.Parameter.KEYWORD_ONLY
    @dataclasses.dataclass
    class Extended(cls):
        extra_caption: str = "extra"
    assert inspect.signature(Extended).parameters["extra_caption"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    original = _chart(Extended)
    assert original.month_context is False
    positional = [getattr(original, f.name) for f in dataclasses.fields(original) if f.init and not f.kw_only]
    assert Extended(*positional).extra_caption == "extra"


@pytest.mark.parametrize("options", [{"horizontal": True}, {"sort": "asc"}, {"sort": "desc"},
                                     {"sort": ""}, {"top_n": 0}, {"top_n": 2}, {"top_n": -1}])
def test_only_opted_in_bars_reject_nonchronological_options(options):
    _payload(_chart(BarChart, **options))
    with pytest.raises(ValueError, match="month_context"):
        _chart(BarChart, month_context=True, **options)
    chart = _chart(BarChart, month_context=True)
    for key, value in options.items():
        setattr(chart, key, value)
    with pytest.raises(ValueError, match="month_context"):
        _payload(chart)


@pytest.mark.parametrize("cls,options", [
    (LineChart, {"ratios": [{"numerator": "value", "denominator": "users", "label": "Rate"}]}),
    (BarChart, {"ratios": [{"numerator": "value", "denominator": "users", "label": "Rate"}]}),
    (LineChart, {"stack_by": "channel", "stack_sort": "label_asc", "max_stacks": 1}),
    (LineChart, {"stack_by": "channel", "ratios": [{"numerator": "value", "denominator": "users", "label": "Rate"}]}),
    (LineChart, {"stack_by_options": {"Channel": "channel"}}),
    (StackedBar, {"stack_by": "channel", "stack_sort": "label_asc", "max_stacks": 1}),
    (StackedBar, {"stack_by_options": {"Channel": "channel"}, "normalize_toggle": True}),
])
def test_ratio_and_pivot_paths(cls, options):
    _, cfg = _payload(_chart(cls, dataset_id="daily", static=True, month_context=True, **options))
    assert cfg["month_context"] is True and cfg["dataset_id"] == "daily"


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _context(output):
    return ReportContext(config={"name": "Synthetic daily month context", "studio": "shared", "category": "Test",
        "description": "Daily synthetic reporting observations, calendar month labels and independent platform/channel selection.",
        "theme": "trellum light", "annotations": {"events": [
            {"date": "2026-10-02", "type": "release", "label": "Synthetic October release"}]}},
        slug="month-context", output_dir=str(output), theme=THEME_REGISTRY["trellum light"])


def _historical(path):
    try:
        return subprocess.check_output(["git", "show", f"{BASELINE}:{path}"], cwd=ROOT, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None


def _build(output, baseline=False, regressions=False):
    output.mkdir(parents=True, exist_ok=True)
    module = _module(ROOT / "trellum/demo/reports/component-showcase/examples_month_context.py", "month_examples")
    historical = baseline and _historical("trellum/components/charts/line_area.py") is not None
    if baseline:
        (output / "baseline-reference.txt").write_text(
            "Actual required dependency baseline " + BASELINE if historical else "Current opt-out control (history unavailable)")
        # Actual dependency-baseline Python for all five families, plus its JS/CSS.
        # No alternate checkout and no substitute renderer.
        for file, classes in [("line_area", ["LineChart", "AreaChart"]), ("bar", ["BarChart", "StackedBar", "ComboChart"])]:
            if historical:
                source = _historical(f"trellum/components/charts/{file}.py")
                path = output / f"baseline_{file}.py"
                path.write_bytes(source)
                old = _module(path, f"month_baseline_{file}")
            else:
                old = _module(ROOT / f"trellum/components/charts/{file}.py", f"month_current_{file}")
            for name in classes:
                def factory(*args, _cls=getattr(old, name), **kwargs):
                    kwargs.pop("month_context", None)
                    return _cls(*args, **kwargs)
                setattr(module, name, factory)
    ctx = _context(output)
    module.add_sections(ctx)
    if regressions:
        rows = pd.DataFrame([
            {"day": day, "variant": variant, "band": "Band A" if j == 0 else "Band B", "value": 17 + i * 3 + j * 29, "users": 5 + i + j * 7}
            for i, day in enumerate(pd.date_range("2026-09-28", "2026-11-02").strftime("%Y-%m-%d"))
            for j, variant in enumerate(("North", "South"))])
        source = "month_runtime_variants"
        ratio = [{"numerator": "value", "denominator": "users", "label": "Supplied rate"}]
        opts = {"dataset_id": source, "month_context": True}
        ctx.add_section("Native month runtime variants", [
            DataSource(source, rows),
            FilterBar(source, rows, filters=[{"column": "day", "type": "date_range"},
                {"column": "variant", "type": "dropdown"}]),
            module.LineChart(rows, "day", title="Bound ratio months", ratios=ratio,
                header_context="Synthetic supplied daily rate", header_summary={"stat": "last"}, **opts),
            module.LineChart(rows, "day", title="Bound split ratio months", ratios=ratio, stack_by="variant", **opts),
            module.LineChart(rows, "day", "value", "Bound alternate breakdown months",
                stack_by_options={"Variant": "variant", "Band": "band"}, **opts),
            module.BarChart(rows, "day", "value", "Bound ratio bars months", ratios=ratio, **opts),
            module.AreaChart(rows, "day", ["value", "users"], "Bound stacked area months", stacked=True, **opts),
            module.StackedBar(rows, "day", ["value"], "Bound normalized stack months",
                stack_by="variant", stack_by_options={"Variant": "variant", "Band": "band"}, normalize_toggle=True, cross_filter=True, **opts),
            module.ComboChart(rows, "day", ["value"], ["users"], "Bound combo months", **opts),
            module.BarChart(rows, "variant", "value", "Horizontal legacy default", dataset_id=source, horizontal=True),
        ])
    if not baseline:
        details = compute_details(ctx)
        validation = validate_report(ctx, details=details)
        assert validation.summary["warn"] == validation.summary["fail"] == 0, [dataclasses.asdict(c) for c in validation.checks if c.level in ("warn", "fail")]
        validation.write(str(output))
    render_report(ctx, str(output), data={"_events": local_annotation_events(ctx.config)}, auto_refresh=False)
    copy_vendor_tree(output)
    if historical:
        html = (output / "index.html").read_text()
        for path, current in [
            ("trellum/static/js/runtime/annotations.js", load_js("runtime/annotations.js")),
            ("trellum/static/js/components/chart_base.js", load_js("components/chart_base.js")),
            ("trellum/static/css/components/chart_base.css", load_css("components/chart_base.css")),
        ]:
            old = _historical(path).decode()
            assert current in html
            html = html.replace(current, old)
        html = html.replace(load_js("runtime/month_context.js"), "")
        (output / "index.html").write_text(html)
    return ctx


def test_native_module_diagnostics_and_build(tmp_path):
    ctx = _build(tmp_path)
    assert len(ctx.sections) == 4
    assert (tmp_path / "index.html").is_file()


def _open(page, output):
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip("/")
        if path.is_file():
            route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404, body="Missing fixture")
    page.route("**/*", serve)
    page.goto("http://months.test/index.html", wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    _reveal(page)


def _reveal(page):
    for cid in page.locator("canvas[data-fw-kind]").evaluate_all("els=>els.map(e=>e.id)"):
        page.locator(f"#{cid}").scroll_into_view_if_needed()
        page.wait_for_function("id=>!!window._chartInstances[id]", arg=cid)


STATE = """title => {
    const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle===title);
    return {height:c.height, width:c.width, area:{left:c.chartArea.left,right:c.chartArea.right,top:c.chartArea.top,bottom:c.chartArea.bottom},
      scales:Object.fromEntries(Object.entries(c.scales).map(([k,s])=>[k,{min:s.min,max:s.max,offset:s.options.offset,ticks:s.ticks.map(t=>t.label)}])),
      labels:c.data.labels,datasets:c.data.datasets.map((d,i)=>({label:d.label,data:d.data,color:d.borderColor,fill:d.backgroundColor,width:d.borderWidth,tension:d.tension,
        elements:c.getDatasetMeta(i).data.map(p=>({x:p.x,y:p.y,width:p.width,height:p.height,base:p.base,radius:p.options.radius}))})),
      legend:c._fwLegendEl?.textContent||'',
      context:c._fwMonthContext||null};
}"""


def _canvas(page, title):
    return page.locator(f"canvas[data-fw-title='{title}']")


def _assert_row(page, title):
    canvas = _canvas(page, title)
    cid = canvas.get_attribute("id")
    canvas.scroll_into_view_if_needed()
    result = page.evaluate("""id=>{
        const c=window._chartInstances[id], row=document.getElementById(id+'-month-context');
        const box=row.getBoundingClientRect(), plot=c.canvas.parentElement.getBoundingClientRect();
        return {height:box.height,top:box.top,bottom:plot.bottom,
          immediate:c.canvas.parentElement.nextElementSibling===row,status:c._fwMonthContext,
          spans:[...row.children].map(el=>{const b=el.getBoundingClientRect(), t=el.lastChild.getBoundingClientRect();
            return {left:b.left,right:b.right,text:el.lastChild.textContent,full:el.firstChild.textContent,title:el.title,
              textLeft:t.left,textRight:t.right};})};
    }""", cid)
    assert result["height"] == 24 and abs(result["top"] - result["bottom"]) < 0.1 and result["immediate"]
    assert result["status"]["status"] == "eligible"
    spans = result["spans"]
    state = page.evaluate(STATE, title)
    # Independent expected boundaries use real scale centers, never elapsed time.
    expected = page.evaluate("""id=>{const c=window._chartInstances[id],x=c.scales.x,a=c.chartArea;
        return c.data.labels.flatMap((v,i,all)=>i&&v.slice(0,7)!==all[i-1].slice(0,7)
            ? [(x.getPixelForValue(i-1)+x.getPixelForValue(i))/2]:[]).filter(p=>p>=a.left&&p<=a.right);}""", cid)
    assert state["context"]["boundaries"] == expected
    if len(spans) == 1:
        assert abs(state["context"]["groups"][0]["left"] - state["area"]["left"]) < .1
        assert abs(state["context"]["groups"][0]["right"] - state["area"]["right"]) < .1
    for index, span in enumerate(spans):
        assert span["full"] == span["title"] and len(span["full"].split()[-1]) == 4
        if span["text"]:
            assert span["textLeft"] >= span["left"] - .1 and span["textRight"] <= span["right"] + .1
        if index:
            assert spans[index - 1]["right"] <= span["left"] + .1
    return result


def _geometry(state):
    return {k: v for k, v in state.items() if k != "context"}


def _browser(tmp_path, width):
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright
    current, baseline = tmp_path / "current", tmp_path / "baseline"
    _build(current, regressions=True)
    _build(baseline, baseline=True, regressions=True)
    shots = Path(os.environ.get("TRELLUM_SCREENSHOT_DIR", tmp_path / "evidence"))
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        old = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        _open(old, baseline)
        _open(page, current)
        titles = page.locator("canvas[data-fw-kind]").evaluate_all("els=>els.map(e=>e.dataset.fwTitle)")
        assert len(titles) == 19
        for title in titles:
            _canvas(page, title).scroll_into_view_if_needed()
            _canvas(old, title).scroll_into_view_if_needed()
            state, before = page.evaluate(STATE, title), old.evaluate(STATE, title)
            assert _geometry(state) == _geometry(before), title
            if state["context"]:
                _assert_row(page, title)
            else:
                assert title.endswith("default")
                cid = _canvas(page, title).get_attribute("id")
                assert page.locator(f"#{cid}-month-context").count() == 0
                a, b = shots / f"months-{width}-off-{cid}.png", shots / f"months-{width}-baseline-{cid}.png"
                _canvas(page, title).screenshot(path=str(a))
                _canvas(old, title).screenshot(path=str(b))
                # Canvas bitmaps avoid fractional page-position rounding around
                # the screenshot of the extra horizontal regression control.
                assert _canvas(page, title).evaluate("el=>el.toDataURL()") == _canvas(old, title).evaluate("el=>el.toDataURL()")
                if title != "Horizontal legacy default":
                    assert ImageChops.difference(Image.open(a), Image.open(b)).getbbox() is None
                assert not page.evaluate("id=>window._chartInstances[id].config.plugins.some(p=>p.id==='fwMonthContext')", cid)
        for view, name in [(old, "baseline"), (page, "enabled")]:
            view.evaluate("window.scrollTo(0,0)")
            path = shots / f"months-{width}-{name}.png"
            view.screenshot(path=str(path), full_page=True)
            # Crop the actual full-page screenshot without a sticky header
            # getting moved over content by locator screenshot auto-scrolling.
            box = _canvas(view, "Daily line · default").locator("xpath=ancestor::*[contains(@class,'fw-section')][1]").bounding_box()
            Image.open(path).crop((box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"])).save(
                shots / f"months-{width}-{name}-comparison.png")
        # The optional header summary and authored context remain alongside months/events.
        rate_id = _canvas(page, "Bound ratio months").get_attribute("id")
        assert page.locator(f'[data-chart-summary="{rate_id}"]').text_content().startswith("Latest")
        assert rate_id + "-header-context" in _canvas(page, "Bound ratio months").get_attribute("aria-describedby")
        # Alternate category/series order and normalization never reorder the daily axis.
        variant_state = page.evaluate(STATE, "Bound normalized stack months")
        for chart_title in ("Bound alternate breakdown months", "Bound normalized stack months"):
            canvas_id = _canvas(page, chart_title).get_attribute("id")
            control = page.locator(f'[data-stack-toggle="{canvas_id}"]')
            control.get_by_role("button", name="Band", exact=True).click()
            _assert_row(page, chart_title)
            control.get_by_role("button", name="Variant", exact=True).click()
            _assert_row(page, chart_title)
        stack_id = _canvas(page, "Bound normalized stack months").get_attribute("id")
        norm = page.locator(f'[data-normalize-toggle="{stack_id}"]')
        norm.get_by_role("button", name="Percentage", exact=True).click()
        assert page.evaluate(STATE, "Bound normalized stack months")["labels"] == variant_state["labels"]
        _assert_row(page, "Bound normalized stack months")
        norm.get_by_role("button", name="Absolute", exact=True).click()
        # Actual existing legend interaction leaves month membership intact.
        combo_id = _canvas(page, "Bound combo months").get_attribute("id")
        page.locator(f'#{combo_id}-combo-context button').first.click()
        assert page.evaluate("id=>!window._chartInstances[id].isDatasetVisible(0)", combo_id)
        _assert_row(page, "Bound combo months")
        page.locator(f'#{combo_id}-combo-context button').first.click()
        # Native bar cross-filter selection changes only its own source.
        bar_title = "Bound normalized stack months"
        bar_id = _canvas(page, bar_title).get_attribute("id")
        _canvas(page, bar_title).scroll_into_view_if_needed()
        bar_box = _canvas(page, bar_title).bounding_box()
        point = page.evaluate(STATE, bar_title)["datasets"][0]["elements"][8]
        page.mouse.click(bar_box["x"] + point["x"], bar_box["y"] + (point["y"] + point["base"]) / 2)
        page.wait_for_function("id=>window._chartInstances[id].data.datasets.length===1", arg=bar_id)
        _assert_row(page, bar_title)
        page.evaluate("window._fwCrossFilter('month_runtime_variants','variant',null)")
        page.wait_for_function("id=>window._chartInstances[id].data.datasets.length===2", arg=bar_id)
        _assert_row(page, bar_title)
        # Native CSV download helper gets the exact same category/value order.
        for view in (page, old):
            view.evaluate("window._fwDownloadCsv=(cols,rows)=>window.monthCsv={cols,rows}")
            cid = _canvas(view, "Daily line · month context").get_attribute("id")
            view.locator(f'[data-csv-chart="{cid}"]').evaluate("el=>el.click()")
        assert page.evaluate("window.monthCsv") == old.evaluate("window.monthCsv")
        title = "Selected daily line"
        cid = _canvas(page, title).get_attribute("id")
        _canvas(page, title).scroll_into_view_if_needed()
        assert len(_assert_row(page, title)["spans"]) == 3
        # Existing nearest-point tooltip inspection still uses the supplied date.
        line_box = _canvas(page, title).bounding_box()
        point = page.evaluate(STATE, title)["datasets"][0]["elements"][5]
        page.mouse.move(line_box["x"] + point["x"], line_box["y"] + point["y"])
        page.wait_for_function("id=>window._chartInstances[id].tooltip.dataPoints?.[0]?.dataIndex===5", arg=cid)
        assert page.evaluate("id=>window._chartInstances[id].tooltip.title[0]", cid) == "2026-09-20"
        page.mouse.move(0, 0)
        # Existing header summary/context and event descriptions are not overwritten.
        page.evaluate("""id=>{
          const c=window._chartInstances[id];
          c.canvas.setAttribute('aria-describedby',c.canvas.getAttribute('aria-describedby')+' authored-header authored-summary');
          const d=document.getElementById(id+'-event-details');d.open=true;d.querySelector('summary').focus();
          window.monthDetails=d;
          c.update('none');c.update('none');
        }""", cid)
        assert page.evaluate("document.activeElement===window.monthDetails.querySelector('summary')")
        assert page.evaluate("id=>document.getElementById(id+'-month-context').nextElementSibling===window.monthDetails", cid)
        tokens = _canvas(page, title).get_attribute("aria-describedby").split()
        assert {cid + "-month-context", cid + "-event-details", "authored-header", "authored-summary"} <= set(tokens)
        # Actual drag zoom and double-click reset retain the chart data.
        box = _canvas(page, title).bounding_box()
        state = page.evaluate(STATE, title)
        a = state["area"]
        page.mouse.move(box["x"] + a["left"] + 35, box["y"] + a["bottom"] - 30)
        page.mouse.down()
        page.mouse.move(box["x"] + a["right"] - 40, box["y"] + a["bottom"] - 30, steps=10)
        page.mouse.up()
        page.wait_for_function("id=>window._chartInstances[id].scales.x.min>0", arg=cid)
        zoomed = _assert_row(page, title)
        assert page.evaluate(STATE, title)["datasets"] != []
        assert page.evaluate(STATE, title)["labels"] == state["labels"]
        _canvas(page, title).dblclick(position={"x": a["left"] + 5, "y": a["bottom"] - 20})
        page.wait_for_function("id=>window._chartInstances[id].scales.x.min===0", arg=cid)
        # A short zoom centers the visible month's label inside its clipped span.
        page.evaluate("id=>window._chartInstances[id].zoomScale('x',{min:18,max:22},'none')", cid)
        assert len(_assert_row(page, title)["spans"]) == 1
        page.evaluate("id=>window._chartInstances[id].resetZoom('none')", cid)
        # Real independent platform/channel controls reaggregate daily observations.
        original = page.evaluate(STATE, "Daily line · month context")
        filtered = page.evaluate(STATE, title)
        other_source = page.evaluate(STATE, "Bound ratio months")
        page.get_by_role("button", name="Web", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].data[0]<100", arg=cid)
        assert page.evaluate(STATE, title)["datasets"][0]["data"] != filtered["datasets"][0]["data"]
        assert page.evaluate(STATE, "Daily line · month context") == original
        assert len(_assert_row(page, title)["spans"]) == 3
        assert page.evaluate(STATE, "Bound ratio months") == other_source
        selected = page.evaluate(STATE, title)
        page.get_by_role("button", name="Partner", exact=True).click()
        assert page.evaluate(STATE, title)["datasets"][0]["data"] != selected["datasets"][0]["data"]
        # Use the actual filter engine's public selection API for exact windows.
        page.evaluate("fw.filterEngine.setFilter('showcase_month_context_daily','month-test-window','day','range',{min:'2026-10-01',max:'2026-10-03'})")
        page.wait_for_function("id=>window._chartInstances[id].data.labels.length===3", arg=cid)
        assert len(_assert_row(page, title)["spans"]) == 1
        page.evaluate("fw.filterEngine.clearFilter('showcase_month_context_daily','month-test-window')")
        page.wait_for_function("id=>window._chartInstances[id].data.labels.length===62", arg=cid)
        for column in ("platform", "channel"):
            group = page.get_by_role("button", name="Web" if column == "platform" else "Partner", exact=True).locator("..")
            group.get_by_role("button", name="All", exact=True).click()
        assert page.evaluate(STATE, title)["datasets"][0]["data"] == filtered["datasets"][0]["data"]
        # Empty bound filter result has no month or event row; restoring filters restores both.
        page.evaluate("fw.filterEngine.setFilter('showcase_month_context_daily','month-test-window','day','range',{min:'2029-01-01',max:'2029-01-03'})")
        assert page.locator(f"#{cid}-month-context").count() == 0
        assert not page.locator(f"#{cid}-event-details").is_visible()
        page.evaluate("fw.filterEngine.clearFilter('showcase_month_context_daily','month-test-window')")
        _assert_row(page, title)
        assert page.locator(f"#{cid}-event-details").evaluate("el=>el.open")
        # Invalid/empty current results remove old DOM, descriptions and dividers.
        static_id = _canvas(page, "Daily line · month context").get_attribute("id")
        page.evaluate("id=>window.monthOriginal=JSON.parse(JSON.stringify(window._reportData.components[id]))", static_id)
        cases = [([], "empty"), (["2026-10-01", "2026-10-01"], "duplicate-date"),
            (["2026-10-02", "2026-10-01"], "unsorted-dates"), (["2027-02-29"], "invalid-date"),
            (["1900-02-29"], "invalid-date"), (["2026-04-31"], "invalid-date"),
            (["2026-00-01"], "invalid-date"), (["0000-01-01"], "invalid-date"),
            (["2026-10-01T00:00:00"], "invalid-date"), (["2026-10"], "invalid-date"),
            (["Mon", "Tue"], "invalid-date"), ([None], "invalid-date")]
        for labels, reason in cases:
            page.evaluate("""({id,labels})=>{const cfg=window._reportData.components[id];cfg.labels=labels;
                cfg.datasets[0].data=labels.map((_,i)=>i?null:0);window._fwRenderers.chart(id,cfg);}""", {"id": static_id, "labels": labels})
            assert page.evaluate("id=>window._chartInstances[id]._fwMonthContext.reason", static_id) == reason
            assert page.locator(f"#{static_id}-month-context").count() == 0
            assert static_id + "-month-context" not in (_canvas(page, "Daily line · month context").get_attribute("aria-describedby") or "")
        for labels in [["2028-02-28", "2028-02-29", "2028-03-01"], ["2000-02-29"],
                       ["2026-12-31", "2027-01-01"], ["2026-09-01", "2026-11-30"],
                       ["2026-10-01", "2026-10-31"], ["2027-02-28", "2027-03-01"]]:
            page.evaluate("""({id,labels})=>{const cfg=window._reportData.components[id];cfg.labels=labels;
                cfg.datasets[0].data=labels.map((_,i)=>i?null:0);window._fwRenderers.chart(id,cfg);}""", {"id": static_id, "labels": labels})
            row = _assert_row(page, "Daily line · month context")
            assert len(row["spans"]) == len(set(label[:7] for label in labels))
            assert page.evaluate("id=>window._chartInstances[id].data.datasets[0].data[0]", static_id) == 0
        # Config removal/re-add, canvas replacement, native renderAll disposal.
        page.evaluate("""id=>{const cfg=window._reportData.components[id];delete cfg.month_context;window._fwRenderers.chart(id,cfg);}""", static_id)
        assert page.locator(f"#{static_id}-month-context").count() == 0
        page.evaluate("""id=>{const cfg=window._reportData.components[id];cfg.month_context=true;window._fwRenderers.chart(id,cfg);
            const old=document.getElementById(id);old.replaceWith(old.cloneNode());window._fwRenderers.chart(id,cfg);}""", static_id)
        _assert_row(page, "Daily line · month context")
        assert page.locator(f"#{static_id}-month-context").count() == 1
        # A replacement canvas with a copied old aria token must also clean up
        # when its replacement config no longer opts in.
        page.evaluate("""id=>{const cfg=window._reportData.components[id],old=document.getElementById(id);
            old.replaceWith(old.cloneNode());delete cfg.month_context;window._fwRenderers.chart(id,cfg);}""", static_id)
        assert page.locator(f"#{static_id}-month-context").count() == 0
        assert static_id + "-month-context" not in (_canvas(page, "Daily line · month context").get_attribute("aria-describedby") or "")
        page.evaluate("id=>Object.assign(window._reportData.components[id],window.monthOriginal)", static_id)
        page.locator(f"#{cid}-event-details summary").focus()
        page.evaluate("window.renderAll()")
        _reveal(page)
        _assert_row(page, title)
        assert page.locator(f"#{cid}-event-details").evaluate("el=>el.open")
        assert page.locator(f"#{cid}-event-details summary").evaluate("el=>el===document.activeElement")
        # Theme switch uses actual native values and visits lazy charts before inspection.
        for theme in ("trellum dark", "trellum light"):
            page.locator("#fwThemeSelect").select_option(theme)
            _reveal(page)
            assert page.locator("html").get_attribute("data-theme") == theme
            for chart_title in titles:
                if not chart_title.endswith("default"):
                    _assert_row(page, chart_title)
            page.screenshot(path=str(shots / f"months-{width}-{theme.split()[1]}.png"), full_page=True)
        # Resize into a narrow panel, hide/reveal and retain unchanged plot sizing.
        page.evaluate("""id=>{const plot=document.getElementById(id).parentElement;window.monthParent=plot.parentElement;
            window.monthParent.style.width='170px';window._chartInstances[id].resize();}""", static_id)
        narrow = _assert_row(page, "Daily line · month context")
        assert any(not span["text"] for span in narrow["spans"])
        page.evaluate("window.monthParent.style.display='none'")
        page.evaluate("window.monthParent.style.display='';window.monthParent.style.width='';window._chartInstances[" + json.dumps(static_id) + "].resize()")
        _assert_row(page, "Daily line · month context")
        # Exercise the actual section-capture pipeline, not a canvas surrogate.
        section = _canvas(page, "Daily line · month context").locator("xpath=ancestor::*[contains(@class,'fw-section')][1]")
        section.scroll_into_view_if_needed()
        capture = section.evaluate("el=>fw.captureElementSnapshotForAnalysis(el)")
        image = shots / f"months-{width}-section-capture.png"
        image.write_bytes(base64.b64decode(capture["image"]["data_base64"]))
        assert Image.open(image).height > 800
        page.screenshot(path=str(shots / f"months-{width}-final.png"), full_page=True)
        assert errors == []
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        browser.close()


def test_month_context_chromium_desktop(tmp_path):
    _browser(tmp_path, 1280)


@pytest.mark.heavy
def test_month_context_chromium_phone(tmp_path):
    _browser(tmp_path, 390)
