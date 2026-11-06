"""Native generated reports, exact exports, source ownership and historical pixels."""
from __future__ import annotations

import base64
import csv
from io import StringIO
import json
import os
from pathlib import Path
import subprocess

import pytest

from trellum.components import DataSource, FilterBar, ScopedDataSource, TabGroup
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report
from trellum.testing.test_doughnut_summaries_browser import _settle
from trellum.testing.test_sankey_chart_browser import _route, _ready, _graph
from trellum.testing.test_sankey_summaries import ROOT, BASELINE, example_context, examples

EVIDENCE = Path(os.environ.get('TRELLUM_SANKEY_SUMMARY_EVIDENCE','/tmp/trellum-sankey-summaries'))


def _build(output):
    ctx = example_context(output); e = examples(); df = e.facts()
    ctx.add_section('Summary ownership', [DataSource('summary_scope_base',df),
        ScopedDataSource('summary_scope_child',parent='summary_scope_base'),
        FilterBar('summary_scope_child',df,filters=[{'column':'platform','type':'toggle'}]),
        TabGroup([{'label': name,'content':[e.paths(df,title,dataset_id='summary_scope_child',
            header_summary=e.declaration('count'))]} for name,title in [('Visible','Scoped count'),('Reveal','Revealed count')]])])
    details = compute_details(ctx); result = validate_report(ctx,details=details)
    issues = [(c.id,c.level,c.section,c.message) for c in result.checks if c.level in {'warn','fail'}]
    assert issues == [], issues
    render_report(ctx,str(output),auto_refresh=False); copy_vendor_tree(output)
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE/'diagnostics.json').write_text(json.dumps({'validation':result.to_dict(),'details':details},indent=2))
    # Historical renderer dependency in a genuinely generated report. Only the
    # opt-out chart is compared; the old renderer does not implement summaries.
    current = (ROOT/'trellum/static/js/components/sankey.js').read_text()
    old = subprocess.check_output(['git','show',f'{BASELINE}:trellum/static/js/components/sankey.js'],cwd=ROOT,text=True)
    html = (output/'index.html').read_text()
    assert current in html
    (output/'historical.html').write_text(html.replace(current,old))


def _root(page, title):
    return page.get_by_text(title,exact=True).locator('xpath=ancestor::div[contains(@class,"fw-sankey") and @data-sankey][1]')


def _value(root):
    return root.locator('.fw-chart-summary-value').inner_text()


def _csv(page, root, path=None):
    with page.expect_download() as pending:
        root.get_by_role('button',name='Download CSV',exact=True).click()
    raw = Path(pending.value.path()).read_bytes()
    if path: path.write_bytes(raw)
    return raw,list(csv.DictReader(StringIO(raw.decode('utf-8-sig'),newline='')))


def _section(root):
    return root.locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def _render(page, cid, mutation):
    page.evaluate('''({id,mutation})=>{const cfg=structuredClone(_reportData.components[id]);
        Function('cfg',mutation)(cfg);_fwRenderers.sankey(id,cfg);}''',{'id':cid,'mutation':mutation})


def _plot(page, root):
    return page.evaluate('''id=>{const c=_chartInstances[id],g=c._fwSankey;return {graph:g,
        dimensions:[c.width,c.height], pixels:c.canvas.toDataURL(),
        links:c.getDatasetMeta(0).data.map(e=>({x:e.x,x2:e.x2,y:e.y,height:e.height,colorFrom:e.options.colorFrom,colorTo:e.options.colorTo})),
        labels:document.getElementById(id).closest('.fw-sankey').querySelector('.fw-sankey-stages').innerText};}''',root.locator('canvas').get_attribute('id'))


def _shot(root, path):
    # Keep the native sticky report header outside the component crop.
    root.evaluate('e=>window.scrollTo(0,e.getBoundingClientRect().top+scrollY-180)')
    return root.screenshot(path=str(path),animations='disabled')


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_native_sankey_summaries_filters_lifecycle_and_capture(tmp_path,width):
    from playwright.sync_api import expect, sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
            errors = []; advisories = []
            page.on('pageerror',lambda e: errors.append(str(e)))
            page.on('console',lambda m: errors.append(m.text) if m.type=='error' else advisories.append(m.text) if m.type=='warning' else None)
            _route(page,tmp_path); page.goto('http://sankey-summaries.test/index.html',wait_until='networkidle'); _settle(page)
            off,on = [_root(page,'Sankey summary '+s) for s in ('off','on')]
            _ready(off); _ready(on)
            assert off.locator('.fw-chart-summary').count() == 0
            assert _value(on) == '1.0k'
            assert _plot(page,off) == _plot(page,on)
            off_raw,off_csv = _csv(page,off,EVIDENCE/f'off-{width}.csv')
            on_raw,on_csv = _csv(page,on,EVIDENCE/f'on-{width}.csv')
            assert off_raw == on_raw and off_csv == on_csv
            assert len(on_csv) == 8 and sum(int(r['weight']) for r in on_csv) == 1000 and len(on_csv[0]) == 21
            for theme in ('trellum light','trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme); _settle(page)
                _ready(off); _ready(on)
                before = _plot(page,off)
                assert before == _plot(page,on)
                off_pixels = _shot(off,EVIDENCE/f'off-{width}-{theme.replace(" ","-")}.png')
                _shot(on,EVIDENCE/f'on-{width}-{theme.replace(" ","-")}.png')
                history = browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
                _route(history,tmp_path); history.goto('http://sankey-summaries.test/historical.html',wait_until='networkidle')
                history.locator('#fwThemeSelect').select_option(theme); _settle(history)
                old = _root(history,'Sankey summary off'); _ready(old)
                assert _plot(history,old) == before
                assert _shot(old,EVIDENCE/f'historical-off-{width}-{theme.replace(" ","-")}.png') == off_pixels
                historical_raw,_ = _csv(history,old)
                assert historical_raw == off_raw
                history.close()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#fwThemeSelect').select_option('trellum light'); _settle(page)
            roots = [_root(page,'Filtered paths '+s) for s in ('sum','max','count')]
            for r in roots: _ready(r)
            assert [_value(r) for r in roots] == ['1.0k','260','8']
            main = roots[0]; cid = main.locator('canvas').get_attribute('id'); section = _section(main)
            main.locator('details summary').click(); main.locator('details summary').focus()
            for platform,values in [('iOS',['500','260','4']),('Android',['500','220','4']),('All',['1.0k','260','8'])]:
                section.get_by_role('button',name=platform,exact=True).click()
                for root,value in zip(roots,values):
                    expect(root.locator('.fw-chart-summary-value')).to_have_text(value)
                assert [_value(r) for r in roots] == values
                assert len(_graph(page,main)['paths']) == int(values[2])
                if platform != 'All': page.wait_for_function('location.search.length>1')
                _,rows = _csv(page,main)
                assert len(rows) == int(values[2]) and sum(int(r['weight']) for r in rows) == (1000 if platform == 'All' else 500)
            assert 'sankey' in page.url
            page.evaluate('renderAll()'); _ready(main)
            assert main.locator('details').get_attribute('open') is not None
            main.locator('details summary').focus()
            for _ in range(3): page.evaluate('renderAll()'); _ready(main)
            expect(main.locator('details summary')).to_be_focused()
            assert page.evaluate("_fwFilterEngine._subscribers.showcase_sankey_summary_paths.filter(s=>s.id=== '"+cid+"').length") == 1
            # Summary adopts and removes options without owning neighbouring descriptions.
            page.evaluate("id=>document.getElementById(id).setAttribute('aria-describedby',document.getElementById(id).getAttribute('aria-describedby')+' foreign-token')",cid)
            for fmt,value in [('number','1.0k'),('plain','1000'),('currency','$1.0k'),('chips','1.0k chips')]:
                _render(page,cid,"cfg.header_summary.format="+json.dumps(fmt)); _ready(main)
                assert _value(main) == value
            _render(page,cid,'delete cfg.header_summary'); _ready(main)
            assert main.locator('.fw-chart-head').count() == 0 and main.locator('.fw-chart-summary').count() == 0
            assert main.locator('canvas').get_attribute('aria-describedby').endswith('foreign-token')
            _render(page,cid,''); _ready(main)
            assert main.locator('.fw-chart-summary').count() == 1
            # Canvas replacement releases old callbacks and removes only its own DOM.
            page.evaluate("id=>{const c=document.getElementById(id);c.replaceWith(c.cloneNode());}",cid)
            page.wait_for_function("id=>!document.getElementById(id).closest('.fw-sankey')._fwSankeyOwner",arg=cid)
            assert cid+'-sankey-summary-scope' not in main.locator('canvas').get_attribute('aria-describedby')
            _render(page,cid,''); _ready(main)
            # Source removal/re-entry uses native lifecycle, with no manual notify.
            page.evaluate("window.savedSummarySource=structuredClone(_fwFilterEngine._columnar.showcase_sankey_summary_paths);_fwFilterEngine.removeDataset('showcase_sankey_summary_paths')")
            _ready(main,'invalid'); assert main.locator('.fw-chart-summary').count() == 0
            page.evaluate("_fwFilterEngine.initColumnar('showcase_sankey_summary_paths',savedSummarySource)")
            _ready(main); assert _value(main) == '1.0k'
            page.evaluate("""() => {
                _fwFilterEngine.setFilter('showcase_sankey_summary_paths','hidden-invalid','platform','equals','iOS');
                const bad=structuredClone(savedSummarySource);
                const dictionary=(bad._dict||{}).platform;
                const row=bad._data.find(r=>(dictionary ? dictionary[r[bad._cols.indexOf('platform')]] : r[bad._cols.indexOf('platform')])==='Android');
                row[bad._cols.indexOf('players')]=null;
                _fwFilterEngine.initColumnar('showcase_sankey_summary_paths',bad);
            }""")
            _ready(main,'invalid'); assert main.locator('.fw-chart-summary').count() == 0
            expect(main.get_by_role('button',name='Download CSV',exact=True)).to_be_disabled()
            with pytest.raises(Exception,match='Sankey unavailable:'):
                main.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            page.evaluate("_fwFilterEngine.initColumnar('showcase_sankey_summary_paths',savedSummarySource)")
            _ready(main); assert _value(main) == '500'
            page.evaluate("_fwFilterEngine.clearFilter('showcase_sankey_summary_paths','hidden-invalid')")
            _ready(main)
            page.evaluate("_fwFilterEngine.setFilter('showcase_sankey_summary_paths','absent','platform','equals','Web')")
            _ready(main,'empty'); _ready(roots[2],'empty')
            assert _value(main) == '—' and _value(roots[2]) == '0'
            expect(main.locator('.fw-sankey-summary-reason')).to_have_text('No measured observations.')
            page.evaluate("_fwFilterEngine.clearFilter('showcase_sankey_summary_paths','absent')"); _ready(main)

            boundary = _root(page,'Boundary 1 sum'); _ready(boundary); bs = _section(boundary)
            for selection, expected in [('All',[['100','80','2'],['50','45','2'],['15','15','2']]),
                                        ('Web',[['—','—','0'],['5','5','1'],['0.00','0.00','1']])]:
                if selection != 'All': bs.get_by_role('button',name=selection,exact=True).click()
                for i,values in enumerate(expected,1):
                    bs.get_by_role('tab',name=f'Boundary {i}',exact=True).click()
                    current = [_root(page,f'Boundary {i} '+s) for s in ('sum','max','count')]
                    for r in current: _ready(r)
                    for root,value in zip(current,values):
                        expect(root.locator('.fw-chart-summary-value')).to_have_text(value)
                    assert [_value(r) for r in current] == values
                    expect(current[0].locator('.fw-sankey-summary-scope')).to_contain_text(f'[w{i}]')
                    expect(current[0].locator('.fw-sankey-summary-scope')).to_contain_text(f'[w{i+1}]')
                    assert _graph(page,current[0])['total'] is None
                    _,csv_rows = _csv(page,current[0]); assert len(csv_rows) == (2 if selection == 'Web' else 6)
                    current[0].screenshot(path=str(EVIDENCE/f'boundary-{selection}-{i}-{width}.png'))
            edge = current[0]; edge_id = edge.locator('canvas').get_attribute('id')
            for mutation in ["delete cfg.header_summary.from_stage", "cfg.header_summary.from_stage=[]",
                "cfg.header_summary.to_stage=true", "cfg.header_summary.from_stage='w4'",
                "cfg.header_summary.from_stage='w1'", "cfg.header_summary.from_stage='19 October'",
                "cfg.header_summary.extra=1", "cfg.header_summary.scope='complete_paths'"]:
                _render(page,edge_id,mutation); _ready(edge,'invalid')
                assert edge.locator('.fw-chart-summary').count() == 0
                with pytest.raises(Exception,match='Sankey unavailable:'):
                    edge.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                _render(page,edge_id,''); _ready(edge)
                assert _value(edge) == '0.00'
            quantity = _root(page,'USD quantity sum'); _ready(quantity)
            assert _value(quantity) == '$12.38'
            assert quantity.locator('.fw-chart-summary-label').text_content() == '<safe> "USD"\nsummary'
            assert quantity.locator('img,script').count() == 0
            quantity.locator('details summary').click()
            _,qcsv = _csv(page,quantity,EVIDENCE/f'quantity-{width}.csv')
            assert len(qcsv) == 4 and {r['unit'] for r in qcsv} == {'USD'}
            assert {r['weight'] for r in qcsv} == {'12.25','0.125','1e-9','0'}
            assert qcsv[0]['stage_1_availability'] == 'unavailable'
            assert qcsv[0]['stage_2_category'] == 'Unavailable' and qcsv[0]['stage_2_availability'] == 'observed'
            assert qcsv[2]['stage_1_category'] == '<safe> "quoted",\nline'
            quantity.screenshot(path=str(EVIDENCE/f'quantity-details-{width}.png'))
            qcapture = quantity.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE/f'native-quantity-{width}.png').write_bytes(base64.b64decode(qcapture['image']['data_base64']))
            qs = _section(quantity)
            quantities = [_root(page,'USD quantity '+s) for s in ('sum','max','count')]
            for r in quantities: _ready(r)
            assert [_value(r) for r in quantities] == ['$12.38','$12.25','4']
            for selection,state,values in [('Zero','zero',['$0.00','$0.00','1']),
                                           ('Measured','ready',['$12.38','$12.25','3']),
                                           ('All','ready',['$12.38','$12.25','4'])]:
                qs.get_by_role('button',name=selection,exact=True).click()
                for r in quantities: _ready(r,state)
                for root,value in zip(quantities,values):
                    expect(root.locator('.fw-chart-summary-value')).to_have_text(value)
                assert [_value(r) for r in quantities] == values
            for name,state,expected in [('Supplied zero','zero',['0.00','0.00','1']),('Empty paths','empty',['—','—','0'])]:
                qs.get_by_role('tab',name=name,exact=True).click()
                prefix = 'Zero' if state == 'zero' else 'Empty'
                current = [_root(page,prefix+' paths '+s) for s in ('sum','max','count')]
                for r in current: _ready(r,state)
                assert [_value(r) for r in current] == expected
                snap = current[0].evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                (EVIDENCE/f'native-{state}-{width}.png').write_bytes(base64.b64decode(snap['image']['data_base64']))
            scoped = _root(page,'Scoped count'); _ready(scoped); ss = _section(scoped)
            ss.get_by_role('button',name='iOS',exact=True).click()
            ss.get_by_role('tab',name='Reveal',exact=True).click()
            revealed = _root(page,'Revealed count'); _ready(revealed); assert _value(revealed) == '4'
            assert _value(main) == '1.0k'
            page.evaluate("window.savedScopeSource=structuredClone(_fwFilterEngine._columnar.summary_scope_base);_fwFilterEngine.removeDataset('summary_scope_base')")
            _ready(revealed,'invalid')
            page.evaluate("_fwFilterEngine.initColumnar('summary_scope_base',savedScopeSource)")
            _ready(revealed); assert _value(revealed) == '4'

            if width == 1280:
                # Real section/report export admission preserves DOM header and disclosure.
                main.scroll_into_view_if_needed()
                before = main.evaluate('e=>({open:e.querySelector("details").open,scroll:e.querySelector(".fw-sankey-scroll").scrollLeft})')
                snap = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                (EVIDENCE/'native-section.png').write_bytes(base64.b64decode(snap['image']['data_base64']))
                (EVIDENCE/'native-section.json').write_text(json.dumps(snap,indent=2))
                for ext,fn in [('png','exportPNG'),('pdf','exportPDF')]:
                    with page.expect_download(timeout=60000) as pending: page.evaluate(f'fw.{fn}()')
                    pending.value.save_as(str(EVIDENCE/f'native-report.{ext}'))
                assert main.evaluate('e=>({open:e.querySelector("details").open,scroll:e.querySelector(".fw-sankey-scroll").scrollLeft})') == before
            # Runtime-injected malformed options/data never leave an old summary or permit capture.
            for mutation in ["cfg.header_summary=[]", "cfg.header_summary.stat=true", "cfg.header_summary.scope={}",
                "cfg.header_summary.from_stage='w1'", "cfg.header_summary.label=''", "cfg.header_summary.format='percent'",
                "cfg.rows=[{w1:'Low',w2:'Low',w3:'Low',w4:'Low',players:null}];delete cfg.dataset_id;cfg.header_summary.stat='count'"]:
                _render(page,cid,mutation); _ready(main,'invalid')
                assert main.locator('.fw-chart-summary').count() == 0
                with pytest.raises(Exception,match='Sankey unavailable:'):
                    main.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                _render(page,cid,''); _ready(main)
            main.evaluate('e=>e.remove()')
            page.wait_for_function("id=>!(_fwFilterEngine._subscribers.showcase_sankey_summary_paths||[]).some(s=>s.id===id)",arg=cid)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            (EVIDENCE/f'browser-advisories-{width}.json').write_text(json.dumps(advisories,indent=2))
            assert all(message.startswith('Canvas2D: Multiple readback operations') for message in advisories), advisories
            assert errors == [], errors
        finally:
            browser.close()


@pytest.mark.heavy
def test_summary_counts_cannot_bypass_runtime_resource_admission(tmp_path):
    from playwright.sync_api import sync_playwright
    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(); _route(page,tmp_path)
            page.goto('http://sankey-summaries.test/index.html',wait_until='networkidle')
            root = _root(page,'Sankey summary on'); _ready(root); cid = root.locator('canvas').get_attribute('id')
            _render(page,cid,"cfg.header_summary.stat='count';cfg.rows=Array.from({length:10001},()=>cfg.rows[0])")
            _ready(root,'invalid')
            assert root.locator('.fw-chart-summary').count() == 0
            with pytest.raises(Exception,match='Sankey unavailable:'):
                root.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        finally:
            browser.close()


def test_native_report_scope_reentry_retires_summary_subscriptions(tmp_path):
    from playwright.sync_api import sync_playwright
    from trellum.report import ReportContext

    ctx = ReportContext(config={'name':'Sankey summary scopes','studio':'demo','category':'Components',
        'description':'Independent native report scopes with synthetic complete path facts and explicitly declared path totals.',
        'annotations':False},slug='summary-scopes',output_dir=str(tmp_path))
    e = examples()
    for name,factor in [('first',1),('second',2)]:
        df = e.facts(); df['players'] *= factor
        ctx.set_scope(name,label=name)
        ctx.add_section('Scoped supplied paths',[DataSource(name+'_paths',df),
            FilterBar(name+'_paths',df,filters=[{'column':'platform','type':'toggle'}]),
            e.paths(df,'Report scoped sum',dataset_id=name+'_paths',header_summary=e.declaration('sum'))])
    result = validate_report(ctx,details=compute_details(ctx))
    assert [(c.id,c.message) for c in result.checks if c.level in {'warn','fail'}] == []
    render_report(ctx,str(tmp_path),auto_refresh=False); copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(); _route(page,tmp_path)
            errors = []; page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://sankey-scopes.test/index.html',wait_until='networkidle')
            root = _root(page,'Report scoped sum'); _ready(root)
            for name,value in [('second','2.0k'),('first','1.0k'),('second','2.0k'),('first','1.0k')]:
                page.get_by_role('button',name=name,exact=True).click()
                _ready(root)
                page.wait_for_function("value=>document.querySelector('.fw-chart-summary-value')?.textContent===value",arg=value)
                assert page.evaluate('_fwFilterEngine._lifecycleListeners.size') == 1
            _section(root).get_by_role('button',name='iOS',exact=True).click()
            page.wait_for_function("document.querySelector('.fw-chart-summary-value').textContent==='500'")
            for _ in range(3): page.evaluate('renderAll()'); _ready(root)
            assert _value(root) == '500' and page.evaluate('_fwFilterEngine._lifecycleListeners.size') == 1
            root.screenshot(path=str(EVIDENCE/'native-report-scope.png'))
            assert errors == []
        finally:
            browser.close()
