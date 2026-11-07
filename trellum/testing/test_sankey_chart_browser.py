"""Generated native reports with vendored assets, pointer input and exports."""

from __future__ import annotations

import base64
import csv
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import pandas as pd
import pytest

from trellum.components import (
    BarChart,
    DataSource,
    FilterBar,
    ScopedDataSource,
    TabGroup,
    TreemapChart,
)
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_sankey_chart import chart, examples
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get("TRELLUM_SANKEY_EVIDENCE", "/tmp/trellum-sankey-evidence"))
IDENTITY_TITLE = "Typed identities " + "unbroken" * 60


def _build(output, *, standalone=False):
    ctx = ReportContext(config={
        "name":"Sankey native report", "title":"Supplied paths and adjacent transitions",
        "studio":"demo", "category":"Components", "theme":"trellum light",
        "description":"Synthetic weekly complete player paths and independently supplied adjacent transition populations, with platform and channel filters and exact supplied weights.",
        "annotations":False,
    }, slug="sankey-native", output_dir=str(output))
    if standalone:
        ctx.add_section("Only Sankey", [chart(static=True, title="Only Sankey")])
    else:
        e = examples()
        e.add_sections(ctx)
        ctx.add_section("Sankey lifecycle", [
            DataSource("sankey_scope_base", e.paths_frame()),
            ScopedDataSource("sankey_scope_child",parent="sankey_scope_base"),
            FilterBar("sankey_scope_child",e.paths_frame(),filters=[{"column":"platform","type":"toggle"}]),
            TabGroup([
                {"label":"Visible paths","content":[chart(title="Scoped visible",dataset_id="sankey_scope_child")]},
                {"label":"Reveal paths","content":[chart(title="Scoped revealed",dataset_id="sankey_scope_child")]},
            ]),
        ])
        hostile = pd.DataFrame([[None,"Unavailable",1],["Unavailable",None,2],
                                ["__proto__","constructor",3],["<img src=x onerror=bad()>",'Long "quoted", category\rline',0]],
                               columns=["a","b","players"])
        ctx.add_section("Exact identity and representative charts",[
            chart(hostile, title=IDENTITY_TITLE,static=True,stages=[{"key":"a","label":"Repeated stage"},{"key":"b","label":"Repeated stage"}]),
            BarChart(pd.DataFrame({"category":["A","B"],"value":[12,18]}),"category","value","Ordinary bar",static=True),
            TreemapChart(pd.DataFrame({"category":["A","B"],"value":[12,18]}),["category"],"value","Ordinary Treemap",static=True),
        ])
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.level,c.id,c.message) for c in result.checks if c.level in {"warn","fail"}]
    assert issues == [], issues
    render_report(ctx,str(output),auto_refresh=False)
    copy_vendor_tree(output)
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE / ("standalone-diagnostics.json" if standalone else "native-diagnostics.json")).write_text(json.dumps({"validation":result.to_dict(),"details":details},indent=2))
    return ctx


def _route(page, root):
    def serve(route):
        path = root / unquote(urlparse(route.request.url).path).lstrip("/")
        if path.is_file():
            route.fulfill(path=str(path), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.fulfill(status=404,body="Missing native test asset")
    page.route("**/*",serve)


def _root(page,title):
    canvas = page.locator(f'canvas[data-fw-title="{title}"]')
    return canvas.locator('xpath=ancestor::div[contains(@class,"fw-sankey") and @data-sankey][1]')


def _ready(root, state="ready"):
    root.scroll_into_view_if_needed()
    from playwright.sync_api import expect
    expect(root).to_have_attribute("data-sankey-state",state)


def _graph(page,root):
    return page.evaluate("id => window._chartInstances[id]._fwSankey",root.locator('canvas').get_attribute('id'))


def _csv(page, root):
    downloads = []
    def got(download): downloads.append(download)
    page.on("download",got)
    try:
        with page.expect_download() as pending:
            root.get_by_role("button",name="Download CSV",exact=True).click()
        with open(pending.value.path(), encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        # A second download would prove that generic chart delegation also ran.
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert len(downloads) == 1
        return rows
    finally:
        page.remove_listener("download",got)


@pytest.mark.parametrize("width",[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_native_sankey_paths_edges_filters_geometry_lifecycle_and_exports(tmp_path,width):
    from playwright.sync_api import expect, sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width":width,"height":900},device_scale_factor=1)
            errors = []
            advisories = []
            page.on('pageerror',lambda error:errors.append(str(error)))
            def console(msg):
                if msg.type == 'warning' and msg.text.startswith('Canvas2D: Multiple readback operations'):
                    advisories.append(msg.text)
                elif msg.type in ('error','warning'):
                    errors.append(msg.text)
            page.on('console',console)
            _route(page,tmp_path)
            page.goto('http://sankey-native.test/index.html',wait_until='networkidle')
            page.evaluate('document.fonts.ready')
            main = _root(page,'Weekly supplied player paths')
            _ready(main)
            cid = main.locator('canvas').get_attribute('id')
            graph = _graph(page,main)
            assert graph['total'] == 1000 and graph['boundaries'] == [1000]*3
            assert len(graph['nodes']) == 16 and len(graph['edges']) == 20 and len(graph['paths']) == 8
            geometry = page.evaluate("""id => {
                const c=window._chartInstances[id],m=c.getDatasetMeta(0);
                return {nodes:Array.from(m.controller._nodes.values()).map(n=>({x:n.x,key:n.key})),
                    links:m.data.map((e,i)=>({height:e.height,weight:c.data.datasets[0].data[i].flow,x:e.x,x2:e.x2}))};
            }""",cid)
            assert set(n['x'] for n in geometry['nodes']) == {0,1,2,3}
            assert all(e['x2'] > e['x'] and e['height'] > 0 for e in geometry['links'])
            ratios = [e['height']/e['weight'] for e in geometry['links']]
            assert max(ratios) - min(ratios) < 1e-8
            main.locator('summary').click()
            assert main.locator('tbody tr').count() == 8
            expect(main.locator('.fw-sankey-population')).to_contain_text('1000 players')
            initial_colors = page.evaluate("""id=>{const c=window._chartInstances[id];return c.data.datasets[0].data.map((e,i)=>[e.source,c.getDatasetMeta(0).data[i].options.colorFrom]);}""",cid)
            section = main.locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            section.get_by_role('button',name='iOS',exact=True).click()
            expect(main.locator('.fw-sankey-population')).to_contain_text('500 players')
            assert _graph(page,main)['boundaries'] == [500]*3
            assert main.locator('details').get_attribute('open') is not None
            filtered_colors = page.evaluate("""id=>{const c=window._chartInstances[id];return c.data.datasets[0].data.map((e,i)=>[e.source,c.getDatasetMeta(0).data[i].options.colorFrom]);}""",cid)
            assert all(dict(initial_colors)[k] == color for k,color in filtered_colors)
            rows = _csv(page,main)
            assert len(rows) == 4 and sum(int(r['weight']) for r in rows) == 500
            assert {r['record_type'] for r in rows} == {'complete_path'}
            assert rows[0]['stage_1_key'] == 'w1' and rows[0]['unit'] == 'players'
            section.get_by_role('button',name='All',exact=True).click()
            expect(main.locator('.fw-sankey-population')).to_contain_text('1000 players')
            page.evaluate("fw.filterEngine.setFilter('showcase_sankey_paths','no-match','platform','equals','Web')")
            _ready(main,'empty')
            expect(main.locator('.fw-sankey-state')).to_have_text('No matching paths.')
            assert main.locator('tbody tr').count() == 0
            assert page.evaluate('id=>!window._chartInstances[id]',cid)
            page.evaluate("fw.filterEngine.clearFilter('showcase_sankey_paths','no-match')")
            _ready(main)

            # Pointer hit testing on the actual native canvas, rather than setting tooltip API state.
            main.locator('canvas').scroll_into_view_if_needed()
            point = page.evaluate("""id=>{const c=window._chartInstances[id],e=c.getDatasetMeta(0).data[0],p=e.getCenterPoint(),r=c.canvas.getBoundingClientRect();return {x:r.left+p.x,y:r.top+p.y};}""",cid)
            page.mouse.move(point['x'],point['y'])
            tooltip = page.locator('.fw-chart-tooltip')
            expect(tooltip).to_have_css('opacity','1')
            expect(tooltip).to_contain_text('players')
            expect(tooltip).to_contain_text('[w1]')
            page.screenshot(path=str(EVIDENCE / f'pointer-tooltip-{width}.png'))
            page.mouse.move(0,0)

            special = _root(page,'Unavailable, no purchase and one player')
            _ready(special)
            sg = _graph(page,special)
            assert sg['total'] == 1000
            assert any(n['category'] is None for n in sg['nodes'])
            assert any(n['category'] == 'Unavailable' for n in sg['nodes'])
            assert any(n['category'] == 'No purchase' for n in sg['nodes'])
            tiny = page.evaluate("""id=>{const c=window._chartInstances[id],m=c.getDatasetMeta(0);return c.data.datasets[0].data.map((d,i)=>({flow:d.flow,height:m.data[i].height}));}""",special.locator('canvas').get_attribute('id'))
            one = next(p for p in tiny if p['flow'] == 1)
            large = max(tiny,key=lambda p:p['flow'])
            assert one['height'] > 0 and one['height']/large['height'] == pytest.approx(1/large['flow'])
            special.locator('summary').click()
            expect(special.locator('.fw-sankey-records')).to_contain_text('Unavailable observation')
            special_rows = _csv(page,special)
            assert len(special_rows) == 12 and any(r['weight'] == '0' for r in special_rows)
            assert any(r['stage_2_availability'] == 'unavailable' for r in special_rows)
            assert any(r['stage_2_category'] == 'Unavailable' and r['stage_2_availability'] == 'observed' for r in special_rows)

            edge = _root(page,'Independent adjacent populations')
            _ready(edge)
            assert _graph(page,edge)['boundaries'] == [100,50,15] and _graph(page,edge)['total'] is None
            expect(edge.locator('.fw-sankey-population')).to_contain_text('complete paths are not supplied')
            edge.locator('summary').click()
            edge_rows = _csv(page,edge)
            assert len(edge_rows) == 6 and {r['record_type'] for r in edge_rows} == {'adjacent_edge'}
            assert any(r['weight'] == '0' for r in edge_rows)
            zero = _root(page,'Supplied zero-weight path')
            _ready(zero,'zero')
            assert zero.locator('tbody tr').count() == 1 and len(_csv(page,zero)) == 1
            assert zero.locator('.fw-sankey-records').evaluate('e=>getComputedStyle(e).display') == 'none'
            _ready(_root(page,'No supplied paths'),'empty')

            visible = _root(page,'Scoped visible')
            _ready(visible)
            life_section = visible.locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')
            life_section.get_by_role('tab',name='Reveal paths',exact=True).click()
            revealed = _root(page,'Scoped revealed')
            _ready(revealed)
            assert revealed.locator('canvas').bounding_box()['height'] > 200
            life_section.get_by_role('button',name='iOS',exact=True).click()
            expect(revealed.locator('.fw-sankey-population')).to_contain_text('500 players')
            assert _graph(page,main)['total'] == 1000
            life_section.get_by_role('tab',name='Visible paths',exact=True).click()
            _ready(visible)
            expect(visible.locator('.fw-sankey-population')).to_contain_text('500 players')
            # Re-entry and renderAll preserve summary focus, disclosure and subscription cardinality.
            main.scroll_into_view_if_needed()
            main.locator('summary').focus()
            for _ in range(3):
                page.evaluate('renderAll()')
                _ready(main)
            assert main.locator('details').get_attribute('open') is not None
            expect(main.locator('summary')).to_be_focused()
            assert page.evaluate("_fwFilterEngine._subscribers.showcase_sankey_paths.filter(s=>s.id===document.querySelector('canvas[data-fw-title=\"Weekly supplied player paths\"]').id).length") == 1
            # Invalid effective data must replace current geometry, even if filters hide the bad row.
            page.evaluate("""() => {
                window.savedSankey = structuredClone(_fwFilterEngine._columnar.showcase_sankey_paths);
                const col=structuredClone(window.savedSankey);col._data[0][col._cols.indexOf('players')]=null;
                _fwFilterEngine.initColumnar('showcase_sankey_paths',col);_fwFilterEngine.notify('showcase_sankey_paths');
            }""")
            _ready(main,'invalid')
            assert page.evaluate('id=>!window._chartInstances[id]',cid)
            expect(main.get_by_role('button',name='Download CSV')).to_be_disabled()
            page.evaluate("_fwFilterEngine.initColumnar('showcase_sankey_paths',window.savedSankey);_fwFilterEngine.notify('showcase_sankey_paths')")
            _ready(main)
            page.evaluate("""id=>{
                delete _fwFilterEngine._columnar.showcase_sankey_paths;
                window._fwRenderers.sankey(id,window._reportData.components[id]);
            }""",cid)
            _ready(main,'invalid')
            assert page.evaluate('id=>!window._chartInstances[id]',cid)
            page.evaluate("""id=>{
                _fwFilterEngine.initColumnar('showcase_sankey_paths',window.savedSankey);
                window._fwRenderers.sankey(id,window._reportData.components[id]);
            }""",cid)
            _ready(main)

            identity = _root(page,IDENTITY_TITLE)
            _ready(identity)
            identity.locator('summary').click()
            expect(identity.locator('.fw-sankey-records')).to_contain_text('__proto__')
            assert identity.locator('img,script').count() == 0
            id_rows = _csv(page,identity)
            assert id_rows[-1]['stage_2_category'] == 'Long "quoted", category\rline'
            for title,kind in (('Ordinary bar','bar'),('Ordinary Treemap','treemap')):
                baseline = page.locator(f'canvas[data-fw-title="{title}"]')
                baseline.scroll_into_view_if_needed()
                baseline_id = baseline.get_attribute('id')
                page.wait_for_function('id=>!!window._chartInstances[id]',arg=baseline_id)
                assert page.evaluate('id=>window._chartInstances[id].config.type',baseline_id) == kind
                assert baseline.bounding_box()['height'] == (180 if width == 390 else 320)
            for theme in ('trellum light','trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                for root,title in ((main,'paths'),(special,'observations'),(edge,'edges'),(identity,'identity')):
                    _ready(root)
                    page.set_viewport_size({'width':width,'height':max(900,int(root.bounding_box()['height'])+250)})
                    root.evaluate('e=>window.scrollTo(0,e.getBoundingClientRect().top+scrollY-200)')
                    root.screenshot(path=str(EVIDENCE / f'{title}-{width}-{theme.replace(" ","-")}.png'))
                    page.set_viewport_size({'width':width,'height':900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            if width == 390:
                scroll = main.locator('.fw-sankey-scroll')
                scroll.scroll_into_view_if_needed()
                scroll.focus()
                assert scroll.evaluate('e=>e.scrollWidth > e.clientWidth')
                page.keyboard.press('ArrowRight')
                page.wait_for_function("id=>document.getElementById(id).closest('.fw-sankey-scroll').scrollLeft>0",arg=cid)
            else:
                # Capture through the real shared readiness and snapshot path.
                main.scroll_into_view_if_needed()
                snapshot = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                assert snapshot['format'] == 'trellum-analysis-capture'
                (EVIDENCE/'native-section.json').write_text(json.dumps(snapshot,indent=2))
                (EVIDENCE/'native-section.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
                for extension,fn in (('png','exportPNG'),('pdf','exportPDF')):
                    with page.expect_download(timeout=60000) as pending:
                        page.evaluate(f'fw.{fn}()')
                    pending.value.save_as(str(EVIDENCE/f'native-report.{extension}'))
            # Removed instances release their filter subscription and chart.
            removal_id = identity.locator('canvas').get_attribute('id')
            identity.evaluate('e=>e.remove()')
            page.wait_for_function('id=>!window._chartInstances[id]',arg=removal_id)
            main.evaluate('e=>e.remove()')
            page.wait_for_function("_fwFilterEngine._subscribers.showcase_sankey_paths.length===0")
            (EVIDENCE/f'capture-browser-advisories-{width}.json').write_text(json.dumps(advisories,indent=2))
            assert errors == [], errors
        finally:
            browser.close()


def test_standalone_sankey_only_csv_has_no_generic_chart_handler(tmp_path):
    from playwright.sync_api import sync_playwright

    _build(tmp_path,standalone=True)
    html = (tmp_path/'index.html').read_text()
    assert "var btn = e.target.closest('[data-csv-chart]')" not in html
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width":1280,"height":900})
            _route(page,tmp_path)
            page.goto('http://sankey-only.test/index.html',wait_until='networkidle')
            root = _root(page,'Only Sankey')
            _ready(root)
            rows = _csv(page,root)
            assert len(rows) == 8 and sum(int(r['weight']) for r in rows) == 1000
            assert len(rows[0]) == 21
        finally:
            browser.close()


def test_runtime_invalid_schema_values_and_unready_injection_clear_geometry(tmp_path):
    from playwright.sync_api import sync_playwright

    _build(tmp_path,standalone=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            _route(page,tmp_path)
            page.goto('http://sankey-injection.test/index.html',wait_until='networkidle')
            root = _root(page,'Only Sankey')
            _ready(root)
            cid = root.locator('canvas').get_attribute('id')
            # Completed empty/zero states capture without a Chart.js instance.
            for zero in (False, True):
                page.evaluate('''([id,zero])=>{
                    const cfg=structuredClone(_reportData.components[id]);
                    cfg.rows=zero ? cfg.rows.map(r=>({...r,players:0})) : [];
                    _fwRenderers.sankey(id,cfg);
                }''', [cid,zero])
                _ready(root, 'zero' if zero else 'empty')
                snapshot = root.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                assert snapshot['format'] == 'trellum-analysis-capture'
            page.evaluate('id=>_fwRenderers.sankey(id,_reportData.components[id])',cid)
            _ready(root)
            for mutation in (
                "cfg.rows[0].players=true", "cfg.rows[0].players='260'", "cfg.rows[0].players=null",
                "cfg.rows[0].players=-1", "cfg.rows[0].players=Infinity", "cfg.rows[0].w1={label:'Low'}",
                "delete cfg.rows[0].w1", "cfg.rows[0].w1='undeclared injected category'",
                "cfg.stages[1].key=cfg.stages[0].key", "cfg.stages=null", "cfg.height=false",
                "cfg.rows=undefined",
            ):
                page.evaluate("""({id,mutation})=>{
                    const cfg=structuredClone(window._reportData.components[id]);
                    Function('cfg',mutation)(cfg);window._fwRenderers.sankey(id,cfg);
                }""",{'id':cid,'mutation':mutation})
                _ready(root,'invalid')
                assert page.evaluate('id=>!window._chartInstances[id]',cid)
                with pytest.raises(Exception, match='Sankey unavailable:'):
                    root.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                page.evaluate('id=>window._fwRenderers.sankey(id,window._reportData.components[id])',cid)
                _ready(root)
            # External instance destruction releases ownership and can re-enter cleanly.
            page.evaluate('id=>window._chartInstances[id].destroy()',cid)
            assert root.evaluate('e=>!e._fwSankeyOwner')
            page.evaluate('id=>window._fwRenderers.sankey(id,window._reportData.components[id])',cid)
            _ready(root)
        finally:
            browser.close()


@pytest.mark.heavy
def test_runtime_source_bound_before_graph_creation(tmp_path):
    from playwright.sync_api import sync_playwright

    _build(tmp_path,standalone=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            _route(page,tmp_path)
            page.goto('http://sankey-bound.test/index.html',wait_until='networkidle')
            root = _root(page,'Only Sankey')
            _ready(root)
            cid = root.locator('canvas').get_attribute('id')
            page.evaluate("""id=>{
                const cfg=structuredClone(window._reportData.components[id]);
                cfg.rows=Array.from({length:10001},()=>cfg.rows[0]);
                window._fwRenderers.sankey(id,cfg);
            }""",cid)
            _ready(root,'invalid')
            assert page.evaluate('id=>!window._chartInstances[id]',cid)
        finally:
            browser.close()


def test_bound_sankey_without_summary_tracks_source_replacement(tmp_path):
    from playwright.sync_api import sync_playwright
    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            _route(page, tmp_path)
            page.goto('http://sankey-lifecycle.test/index.html', wait_until='networkidle')
            root = _root(page, 'Weekly supplied player paths')
            _ready(root)
            cid = root.locator('canvas').get_attribute('id')
            assert page.evaluate('id=>!_reportData.components[id].header_summary', cid)
            page.evaluate('''id=>{
                const cfg=_reportData.components[id];
                window.savedSankeyRows=_fwFilterEngine.getFiltered(cfg.dataset_id);
                _fwFilterEngine.init(cfg.dataset_id,savedSankeyRows.map(r=>({...r,players:0})));
            }''', cid)
            _ready(root, 'zero')
            page.evaluate('id=>_fwFilterEngine.removeDataset(_reportData.components[id].dataset_id)', cid)
            _ready(root, 'invalid')
            page.evaluate('id=>_fwFilterEngine.init(_reportData.components[id].dataset_id,savedSankeyRows)', cid)
            _ready(root)
            assert page.evaluate('id=>_fwFilterEngine._subscribers[_reportData.components[id].dataset_id].filter(s=>s.id===id).length', cid) == 1
        finally:
            browser.close()
