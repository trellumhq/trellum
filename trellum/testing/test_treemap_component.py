"""Treemap API, genuine report mechanics, label paint and native details."""

import importlib.util
import inspect
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import DataSource, FilterBar, TreemapChart
from trellum.components.base import RenderContext
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import DEFAULT_THEME
from trellum.validation import validate_report


def build_report(output):
    path = Path(__file__).parents[1] / "demo/reports/component-showcase/examples_treemaps.py"
    spec = importlib.util.spec_from_file_location("examples_treemaps", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={"name": "Treemap mechanics", "studio": "test", "category": "Test",
                                "description": "Deterministic supplied revenue at region/channel/platform grain, with hierarchy colors, exact values, shares and filtering."},
                        slug="treemaps", output_dir=str(output))
    module.add_sections(ctx)
    identity = pd.DataFrame({"parent": ["A.B", "A", "__proto__", "constructor", "<b>literal</b>"],
                             "leaf": ["C", "B.C", "Store", "Store", "Store"],
                             "value": [100, 80, 30, 20, 10]})
    ctx.add_section("Tuple identity", [TreemapChart(
        identity, ["parent", "leaf"], "value", "Tuple identity", static=True)])
    cases = [("Complete", [10, 20]), ("Zero", [0, 0]), ("Missing", [10, None]),
             ("Signed", [20, -5]), ("Blank", [10, 5])]
    frame = pd.DataFrame([
        {"case": case, "parent": "" if case == "Blank" and i else "North",
         "leaf": "Store" if i == 0 else "Web", "value": value}
        for case, values in cases for i, value in enumerate(values)
    ])
    ctx.add_section("Live eligibility", [
        DataSource("treemap_eligibility", frame),
        FilterBar("treemap_eligibility", frame, filters=[{"column": "case", "type": "toggle"}]),
        TreemapChart(frame, ["parent", "leaf"], "value", "Live eligibility",
                     dataset_id="treemap_eligibility", static=True, show_percentages=True),
    ])
    result = validate_report(ctx, details=compute_details(ctx))
    assert result.summary["warn"] == result.summary["fail"] == 0, result.to_dict()
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))
    return ctx


def test_api_preserves_positions_and_binding_precedence():
    positional = [name for name, parameter in inspect.signature(TreemapChart).parameters.items()
                  if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD]
    assert positional == ["df", "group_cols", "value", "title", "color_col", "value_format", "dataset_id", "static"]

    @dataclass
    class Subclass(TreemapChart):
        extra: str = "preserved"

        def __post_init__(self):
            self.calls = getattr(self, "calls", 0) + 1
            super().__post_init__()

    frame = pd.DataFrame({"group": ["Store"], "value": [10]})
    chart = Subclass(frame, ["group"], "value", "Title", None, "currency", "explicit", True,
                     "subclass positional", show_percentages=True)
    assert chart.extra == "subclass positional"
    ctx = RenderContext(DEFAULT_THEME)
    chart.render_html(ctx)
    assert chart.calls == 1
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["dataset_id"] == "explicit" and "rows" not in cfg and cfg["show_percentages"] is True
    chart = TreemapChart(frame, ["group"], "value", static=True)
    ctx = RenderContext(DEFAULT_THEME)
    assert "fw-treemap" in chart.render_html(ctx)
    cfg = next(iter(ctx.component_data.values()))
    assert cfg["rows"] == [{"group": "Store", "value": 10}]
    assert cfg["show_percentages"] is False
    assert ".fw-treemap-context" in chart.css()


@pytest.mark.parametrize("value", [None, 0, 1, "yes", [], {}])
def test_share_option_requires_boolean(value):
    with pytest.raises(ValueError, match="show_percentages"):
        TreemapChart(pd.DataFrame(), ["group"], "value", show_percentages=value)
    chart = TreemapChart(pd.DataFrame(), ["group"], "value")
    chart.show_percentages = value
    with pytest.raises(ValueError, match="show_percentages"):
        chart.render_html(RenderContext(DEFAULT_THEME))


def _root(page, title):
    cid = page.evaluate("""title=>Object.keys(window._reportData.components).find(id=>{
        const cfg=window._reportData.components[id];return cfg.type==='treemap' && cfg.title===title;
    })""", title)
    return page.locator("#" + cid).locator("xpath=ancestor::div[@class='fw-treemap'][1]")


def _cid(page, title):
    return _root(page, title).locator("canvas").get_attribute("id")


def _reveal(page):
    for canvas in page.locator(".fw-treemap canvas").all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function("id => !!window._chartInstances[id]", arg=canvas.get_attribute("id"))


def _geometry(page, cid):
    return page.evaluate("""id => {const c = window._chartInstances[id];return {
        height:c.height, nodes:c.data.datasets[0].data.map(r=>({x:r.x,y:r.y,w:r.w,h:r.h,v:r.v,l:r.l,g:r.g})),
        elements:c.getDatasetMeta(0).data.map(r=>({x:r.x,y:r.y,w:r.width,h:r.height}))};}""", cid)


def _details(root):
    return root.locator("tbody tr").evaluate_all("""rows => rows.map(r => ({
        path:JSON.parse(r.dataset.path), value:Number(r.dataset.value),
        share:r.cells[2].textContent, text:r.cells[1].textContent}))""")


def _colors(page, cid):
    return page.evaluate("""id => {const c=window._chartInstances[id];return c.data.datasets[0].data.map((r,i)=>({
        path:c.data.datasets[0].groups.slice(0,r.l+1).map(k=>r._data.children[0][k]),
        color:c.getDatasetMeta(0).data[i].options.backgroundColor}));}""", cid)


@pytest.mark.parametrize("width", [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_real_hierarchy_paint_details_themes_filters_and_reinit(tmp_path, width):
    from playwright.sync_api import sync_playwright

    build_report(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
        # Observe actual paint and native listener ownership without replacing them.
        page.add_init_script("""window._treemapPaint=[];window._treemapAdds=0;window._treemapRemoves=0;
            const fill=CanvasRenderingContext2D.prototype.fillText;
            CanvasRenderingContext2D.prototype.fillText=function(text,x,y,...rest){
                if(this.canvas.closest('.fw-treemap')) window._treemapPaint.push({id:this.canvas.id,text:String(text),x,y,
                    font:this.font,width:this.measureText(text).width});
                return fill.call(this,text,x,y,...rest);
            };
            const add=window.addEventListener,remove=window.removeEventListener;
            window.addEventListener=function(t,f,o){if(t==='fw-theme-change')window._treemapAdds++;return add.call(this,t,f,o)};
            window.removeEventListener=function(t,f,o){if(t==='fw-theme-change')window._treemapRemoves++;return remove.call(this,t,f,o)};""")

        def serve(route):
            assert urlparse(route.request.url).hostname == "treemap.test"
            path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")

        page.route("**/*", serve)
        page.goto("http://treemap.test/index.html", wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        _reveal(page)
        default = _root(page, "Default hierarchy")
        cid = _cid(page, "Default hierarchy")
        initial = _geometry(page, cid)
        assert initial["height"] == (320 if width == 1280 else 180)
        assert initial == _geometry(page, _cid(page, "Hierarchy with shares"))
        expected = {("North", "Store"): (36000, "36.0%"), ("North", "Web"): (24000, "24.0%"),
                    ("South", "Store"): (22000, "22.0%"), ("South", "Web"): (12000, "12.0%"),
                    ("West", "Store"): (5990, "6.0%"), ("West", "Web"): (10, "0.0%")}
        assert {tuple(row["path"]): (row["value"], row["share"]) for row in _details(default)} == expected
        assert sum(row["value"] for row in _details(default)) == 100000
        assert default.locator("tbody tr").last.locator("td").last.get_attribute("title") == "0.01%"
        assert default.locator("details").get_attribute("open") is None
        default.locator("summary").focus()
        page.keyboard.press("Enter")
        assert default.locator("details").evaluate("el=>el.open")
        group_colors = {}
        for node in _colors(page, cid):
            group = node["path"][0]
            assert group not in group_colors or group_colors[group] == node["color"]
            group_colors[group] = node["color"]
        assert len(set(group_colors.values())) == 3

        # Optional verification against the actual report built before editing.
        baseline_dir = os.environ.get("TRELLUM_TREEMAP_BASELINE")
        if baseline_dir:
            baseline = json.loads((Path(baseline_dir) / f"geometry-{width}.json").read_text())
            for before in baseline:
                actual = _geometry(page, before["id"])
                expected_geometry = {"height": before["height"], "nodes": before["geometry"], "elements": before["elements"]}
                # Unsupported all-zero/signed fixtures retain their NaN geometry.
                assert json.dumps(actual, sort_keys=True) == json.dumps(expected_geometry, sort_keys=True)
                if "supplied_colors" in before:
                    actual_colors = page.evaluate("id=>window._chartInstances[id].getDatasetMeta(0).data.map(e=>e.options.backgroundColor)", before["id"])
                    assert actual_colors == before["supplied_colors"]
                assert page.evaluate("id=>JSON.stringify(window._chartInstances[id].data.datasets[0].tree)", before["id"]) == before["tree_json"]

        identity = _root(page, "Tuple identity")
        assert [row["path"] for row in _details(identity)][:2] == [["A.B", "C"], ["A", "B.C"]]
        assert len({tuple(row["path"]) for row in _details(identity)}) == 5
        assert identity.locator("b").count() == 0
        assert "<b>literal</b>" in identity.text_content()

        # Actual paint, not just label callbacks: whole labels hide when too small.
        page.evaluate("window._treemapPaint=[];Object.values(window._chartInstances).forEach(c=>c.draw())")
        paints = page.evaluate("window._treemapPaint")
        assert any(p["text"] == "Store" for p in paints if p["id"] == cid)
        assert not any("%" in p["text"] for p in paints if p["id"] == cid)
        assert any("%" in p["text"] for p in paints if p["id"] == _cid(page, "Hierarchy with shares"))
        assert not any(p["text"] == "$10" for p in paints if p["id"] == cid)
        long_id = _cid(page, "Long hierarchy names")
        if width == 390:
            assert not any("regional retail" in p["text"] for p in paints if p["id"] == long_id)
        for paint in paints:
            # Every painted chart label stays at normal readable size.
            assert "12px" in paint["font"], paint
        fits = page.evaluate("""() => Object.values(window._chartInstances).every(c=>{
            const paints=window._treemapPaint.filter(p=>p.id===c.canvas.id);
            return paints.every(p=>c.getDatasetMeta(0).data.some(e=>
                Math.abs(p.x-(e.x+5))<0.01 && p.width<=e.width-10+0.01 &&
                p.y>=e.y+2 && p.y+6<=e.y+e.height-2));
        })""")
        assert fits
        dense_id = _cid(page, "Dense default")
        dense_share_id = _cid(page, "Dense shares")
        assert _geometry(page, dense_id) == _geometry(page, dense_share_id)
        assert len(_details(_root(page, "Dense shares"))) == 24
        off_labels = [p for p in paints if p["id"] == dense_id and p["text"].startswith("Item ")]
        on_labels = [p for p in paints if p["id"] == dense_share_id and p["text"].startswith("Item ")]
        assert len(on_labels) <= len(off_labels)
        if width == 1280:
            for title in ("Dense default", "Dense shares"):
                _root(page, title).evaluate("el=>el.style.width='220px'")
            page.wait_for_function("ids=>ids.every(id=>window._chartInstances[id].width===220)", arg=[dense_id, dense_share_id])
            page.evaluate("window._treemapPaint=[];Object.values(window._chartInstances).forEach(c=>c.draw())")
            narrow_counts = page.evaluate("""ids=>ids.map(id=>window._treemapPaint.filter(p=>p.id===id&&p.text.startsWith('Item ')).length)""", [dense_id, dense_share_id])
            assert narrow_counts[1] < narrow_counts[0]
            assert _geometry(page, dense_id) == _geometry(page, dense_share_id)
            for title in ("Dense default", "Dense shares"):
                _root(page, title).evaluate("el=>el.style.width=''")
            page.wait_for_function("ids=>ids.every(id=>window._chartInstances[id].width>220)", arg=[dense_id, dense_share_id])

        # Numeric supplied colors keep their exact source-child behavior.
        numeric = _cid(page, "Numeric supplied color")
        reversed_id = _cid(page, "Numeric reversed rows")
        assert _geometry(page, numeric)["elements"] == _geometry(page, reversed_id)["elements"]
        assert sorted(node["v"] for node in _geometry(page, numeric)["nodes"]) == sorted(
            node["v"] for node in _geometry(page, reversed_id)["nodes"])
        assert _colors(page, numeric)[0]["color"] != _colors(page, reversed_id)[0]["color"]
        assert "not an aggregate" in _root(page, "Numeric supplied color").text_content()
        assert _root(page, "Numeric supplied color").locator(".fw-treemap-key").count() == 0

        live = _root(page, "Filtered hierarchy")
        live_id = _cid(page, "Filtered hierarchy")
        live.locator("summary").click()
        before_colors = _colors(page, live_id)
        page.get_by_role("button", name="Phone", exact=True).click()
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].tree.length===6", arg=live_id)
        assert live.locator("details").evaluate("el=>el.open")

        values = _details(live)
        assert sum(r["value"] for r in values) == 35015
        assert {tuple(r["path"]): r["color"] for r in before_colors} == {
            tuple(r["path"]): r["color"] for r in _colors(page, live_id)}
        assert _geometry(page, cid) == initial  # independent static chart

        region = page.locator(".fw-section").filter(has=page.get_by_role(
            "heading", name="Treemaps — filter the complete population", exact=True)).last.locator("select.fw-dropdown")
        region.evaluate("el=>el._fwSlimSelect.setSelected(['South'])")
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].tree.length===2", arg=live_id)
        assert {tuple(r["path"]): r["color"] for r in _colors(page, live_id)} == {
            tuple(r["path"]): r["color"] for r in before_colors if r["path"][0] == "South"}
        assert sum(r["value"] for r in _details(live)) == 11905
        assert live.locator(".fw-treemap-key li").all_text_contents() == ["South"]
        region.evaluate("el=>el._fwSlimSelect.setSelected([])")
        page.wait_for_function("id=>window._chartInstances[id].data.datasets[0].tree.length===6", arg=live_id)
        assert _details(live) == values

        themes = page.locator("#fwThemeSelect option").evaluate_all("els=>els.map(e=>e.value)")
        for theme in ["trellum light"] + [t for t in themes if t not in {"trellum light", "trellum dark"}] + ["trellum dark"]:
            page.locator("#fwThemeSelect").select_option(theme)
            _reveal(page)
            assert page.evaluate("getActiveTheme()") == theme
            assert _geometry(page, cid) == initial
            assert _details(live) == values
            assert live.locator("details").evaluate("el=>el.open")
            assert default.locator("details").evaluate("el=>el.open")
            swatch = default.locator(".fw-treemap-swatch").first.evaluate("el=>el.style.backgroundColor")
            assert swatch == page.evaluate("""id=>{const e=document.createElement('span');
                e.style.backgroundColor=window._chartInstances[id].getDatasetMeta(0).data[0].options.backgroundColor;
                return e.style.backgroundColor;}""", cid)

        # Same component initialized repeatedly leaves one subscription/listener.
        balance = page.evaluate("window._treemapAdds-window._treemapRemoves")
        page.evaluate("""id=>{for(let i=0;i<3;i++)window._fwRenderers.treemap(id,window._reportData.components[id]);}""", live_id)
        assert page.evaluate("window._treemapAdds-window._treemapRemoves") == balance
        assert page.evaluate("id=>window._fwFilterEngine._subscribers.showcase_treemaps.filter(s=>s.id===id).length", live_id) == 1
        assert live.locator("details").evaluate("el=>el.open")

        page.evaluate("window.renderAll()")
        _reveal(page)
        assert page.evaluate("window._treemapAdds-window._treemapRemoves") == balance
        assert _details(live) == values
        assert live.locator("details").evaluate("el=>el.open")

        # A genuine hover follows the existing interaction selection.
        default.locator("canvas").scroll_into_view_if_needed()
        point = page.evaluate("""id=>{const c=window._chartInstances[id];
            const i=c.data.datasets[0].data.findIndex(r=>r.l===1 && r.g==='Store' && r._data.children[0].region==='North');
            return c.getDatasetMeta(0).data[i].getCenterPoint();}""", cid)
        box = default.locator("canvas").bounding_box()
        page.mouse.move(box["x"] + point["x"], box["y"] + point["y"])
        page.wait_for_function("id=>window._chartInstances[id].tooltip.title[0]==='North › Store'", arg=cid)
        page.mouse.move(0, 0)

        # A narrow parent panel must not force a plot-height or page-width change.
        if width == 1280:
            default.evaluate("el=>el.style.width='220px'")
            page.wait_for_function("id=>window._chartInstances[id].width===220", arg=cid)
            assert _geometry(page, cid)["height"] == 320
            assert default.evaluate("el=>el.scrollWidth<=el.clientWidth")
            default.evaluate("el=>el.style.width=''")
            page.wait_for_function("id=>window._chartInstances[id].width>220", arg=cid)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        directory = os.environ.get("TRELLUM_SCREENSHOT_DIR")
        if directory:
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            default.locator("summary").click()
            long = _root(page, "Long hierarchy names")
            long.locator("summary").click()
            # Capture document-coordinate regions with chrome at the page top,
            # avoiding a sticky header covering a tall section's heading.
            page.evaluate("window.scrollTo(0,0)")
            for index, name in ((0, f"changed-{width}.png"), (1, f"changed-long-{width}.png")):
                clip = page.locator(".fw-section").nth(index).evaluate("""el=>{
                    const r=el.getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};}""")
                page.screenshot(path=str(folder / name), clip=clip, full_page=True)
            (folder / f"geometry-{width}.json").write_text(json.dumps(initial, indent=2))
        assert errors == []
        browser.close()


def test_supplied_population_eligibility_and_legacy_wrapper(tmp_path):
    from playwright.sync_api import sync_playwright

    build_report(tmp_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        def serve(route):
            path = tmp_path / unquote(urlparse(route.request.url).path).lstrip("/")
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")

        page.route("**/*", serve)
        page.goto("http://treemap.test/index.html", wait_until="networkidle")
        _reveal(page)
        live = _root(page, "Live eligibility")
        live_id = _cid(page, "Live eligibility")
        for case, reason in (("Complete", None), ("Zero", "undefined"), ("Missing", "unavailable"),
                             ("Signed", "unavailable"), ("Blank", "blank hierarchy")):
            page.get_by_role("button", name=case, exact=True).click()
            if reason:
                assert reason in live.locator(".fw-treemap-context > p").all_text_contents()[1].lower()
                assert all(row["share"] == "—" for row in _details(live))
            else:
                assert sum(row["value"] for row in _details(live)) == 30
                assert {row["share"] for row in _details(live)} == {"33.3%", "66.7%"}
        page.get_by_role("button", name="Missing", exact=True).click()
        assert any("Supplied: null" in row["text"] for row in _details(live))
        # Direct payloads cover values JSON cannot encode plus older metadata.
        cid = _cid(page, "Default hierarchy")
        root = _root(page, "Default hierarchy")
        for case in ("empty", "nonfinite", "overflow", "boolean", "numeric-string", "null"):
            page.evaluate("""([id,which])=>{const cfg={...window._reportData.components[id]};
                delete cfg.supplied_values; delete cfg.show_percentages;
                const values={empty:[],nonfinite:[Infinity,10],overflow:[Number.MAX_VALUE,Number.MAX_VALUE],
                    boolean:[true,10],'numeric-string':['10',20],null:[null,20]}[which];
                cfg.rows=values.map((v,i)=>({region:'North',channel:String(i),revenue:v}));
                window._fwRenderers.treemap(id,cfg);}""", [cid, case])
            assert "unavailable" in root.locator(".fw-treemap-context > p").all_text_contents()[1].lower()
            assert all(row["share"] == "—" for row in _details(root))
        page.evaluate("""id=>{const cfg={...window._reportData.components[id]};
            cfg.rows=[{region:'North',channel:'Store',revenue:100},{region:'North',channel:'Zero',revenue:0}];
            window._fwRenderers.treemap(id,cfg);}""", cid)
        assert {tuple(r["path"]): (r["value"], r["share"]) for r in _details(root)} == {
            ("North", "Store"): (100, "100.0%"), ("North", "Zero"): (0, "0.0%")}
        page.evaluate("""id=>{const cfg={...window._reportData.components[id],group_cols:['region','channel','product']};
            cfg.rows=[{region:'North',channel:'Store',product:'Widget',revenue:10},
                {region:'North',channel:'Store',product:'Gadget',revenue:5},
                {region:'North',channel:'Market',product:'Widget',revenue:3},
                {region:'South',channel:'Store',product:'Widget',revenue:2}];
            window._fwRenderers.treemap(id,cfg);}""", cid)
        assert {tuple(r["path"]): (r["value"], r["share"]) for r in _details(root)} == {
            ("North", "Store", "Widget"): (10, "50.0%"), ("North", "Store", "Gadget"): (5, "25.0%"),
            ("North", "Market", "Widget"): (3, "15.0%"), ("South", "Store", "Widget"): (2, "10.0%")}
        page.evaluate("""id=>{const cfg={...window._reportData.components[id],group_cols:['region'],value_format:'percent'};
            cfg.rows=[{region:'North',revenue:0.01},{region:'North',revenue:0.02},{region:'South',revenue:0.03}];
            window._fwRenderers.treemap(id,cfg);}""", cid)
        assert {tuple(r["path"]): (r["value"], r["share"]) for r in _details(root)} == {
            ("North",): (0.03, "50.0%"), ("South",): (0.03, "50.0%")}
        assert all("3.00%" in row["text"] for row in _details(root))
        # Preserve the actual bundled point selection; add full tuple context.
        page.get_by_role("button", name="Complete", exact=True).click()
        tooltip = page.evaluate("""id=>{const c=window._chartInstances[id];
            const i=c.data.datasets[0].data.findIndex(r=>r.l===1),raw=c.data.datasets[0].data[i];
            const cb=c.options.plugins.tooltip.callbacks;
            return [cb.title([{raw}]),cb.label({raw}),c.options.interaction.mode];}""", live_id)
        assert tooltip[0] in {"North › Store", "North › Web"}
        assert "filtered total" in tooltip[1]
        assert tooltip[2] == "point"
        # An old ungrouped payload has no hierarchy leaves. Once coverage is
        # known, the final painted frame must agree with its unavailable note.
        painted = page.evaluate("""id=>{
            const drawn={}, original=CanvasRenderingContext2D.prototype.fillText;
            const observer={id:'treemapLastPaint',beforeDraw(c){drawn[c.canvas.id]=[];}};
            Chart.register(observer);
            CanvasRenderingContext2D.prototype.fillText=function(text,...args){
                (drawn[this.canvas.id] ||= []).push(String(text));
                return original.call(this,text,...args);
            };
            try {
                const cfg={...window._reportData.components[id],group_cols:[],
                    rows:[{revenue:10},{revenue:20}],show_percentages:true};
                window._fwRenderers.treemap(id,cfg);
                return drawn[id];
            } finally {
                CanvasRenderingContext2D.prototype.fillText=original;
                Chart.unregister(observer);
            }
        }""", cid)
        assert "legacy leaves do not cover" in root.locator('.fw-treemap-context').inner_text()
        assert any("20" in text for text in painted)
        assert not any("%" in text for text in painted)
        # Rendering into original markup without the new context remains safe.
        page.evaluate("""id=>{const canvas=document.getElementById(id);const host=canvas.closest('.fw-treemap');
            host.replaceWith(...host.childNodes);window._fwRenderers.treemap(id,window._reportData.components[id]);}""", cid)
        assert page.evaluate("id=>!!window._chartInstances[id]", cid)
        browser.close()
