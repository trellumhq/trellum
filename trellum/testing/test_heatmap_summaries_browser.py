"""Native vendored matrix reports: filters, ownership, baseline pixels and captures."""
from __future__ import annotations

import base64
import csv
import json
import os
import subprocess
from io import BytesIO, StringIO
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

from trellum.components import BarChart, DataSource, DataTable, FilterBar, HeatmapChart
from trellum.components.charts._heatmap_summary import heatmap_option_problems
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_doughnut_summaries_browser import _csv, _day, _settle
from trellum.testing.test_heatmap_summaries import (
    BASELINE,
    ROOT,
    baseline_class,
    example_context,
    frame,
    walk,
)
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_HEATMAP_SUMMARY_EVIDENCE', '/tmp/trellum-heatmap-summaries'))
STATS = ('sum', 'mean', 'min', 'max', 'count')


def _diagnose(ctx, output):
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.id,c.level,c.section,c.message) for c in result.checks if c.level in {'warn','fail'}]
    assert issues == [], issues
    for section in ctx.sections:
        for chart in walk(section['components']):
            if isinstance(chart, HeatmapChart):
                assert heatmap_option_problems(chart) == []
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    result.write(str(output))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'diagnostics.json').write_text(json.dumps({'validation': result.to_dict(), 'details': details}, default=str, indent=2))


def _build(output):
    ctx = example_context(output)
    unsafe = pd.DataFrame([
        {'case': case, 'x': x, 'y': y, 'v': value}
        for case, entries in (
            ('Safe total', [('A', 'one', 2**53-1), ('B', 'two', 2**53-1)]),
            ('Unsafe aggregate', [('A', 'one', 2**53-1), ('A', 'one', 2**53-1)]),
            ('Unsafe raw', [('A', 'two', 2**53)]),
            ('Cancellation', [('A', 'one', 2**53-1), ('B', 'one', 1), ('C', 'two', -(2**53-1))]),
        ) for x, y, value in entries
    ], dtype=object)
    ctx.add_section('Heatmap numeric boundaries', [DataSource('heatmap_numeric_boundaries', unsafe),
        FilterBar('heatmap_numeric_boundaries', unsafe, filters=[{'column': 'case', 'type': 'toggle'}]),
        *[HeatmapChart(unsafe, 'x', 'y', 'v', 'Boundary '+stat, dataset_id='heatmap_numeric_boundaries',
            header_summary={'stat': stat, 'scope': 'current_cells'}) for stat in STATS],
        HeatmapChart(pd.DataFrame({'x': ['A','B'], 'y': ['one','two'], 'v': [None,10]}, dtype=object),
            'x', 'y', 'v', 'Static original-input guard', static=True, drop_empty=True,
            header_summary={'stat': 'sum', 'scope': 'current_cells'}),
    ])
    # Load the ordinary native chart delegate used by existing heatmap CSV.
    ctx.add_section('Native CSV delegate', [BarChart(frame(), 'x', 'v', 'Ordinary source chart', static=True)])
    _diagnose(ctx, output)


def _id(page, title):
    return page.get_by_text(title, exact=True).last.evaluate(
        "el=>{let n=el.closest('.fw-chart-head')||el;n=n.nextElementSibling;"
        "while(n&&!n.matches('.fw-chart-container'))n=n.nextElementSibling;return n.querySelector('canvas').id}")


def _box(page, cid):
    return page.locator(f'[data-chart-summary="{cid}"]')


def _values(page, ids):
    return [_box(page,cid).locator('.fw-chart-summary-value').inner_text() for cid in ids]


def _section(page, cid):
    return page.locator('#'+cid).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def _plot(page, cid):
    return page.evaluate('''id=>{const c=_chartInstances[id];return {
        cells:c.data.datasets[0].data, x:c.scales.x.getLabels(), y:c.scales.y.getLabels(),
        height:c.canvas.parentElement.getBoundingClientRect().height,
        points:c.getDatasetMeta(0).data.map(e=>({x:e.x,y:e.y,width:e.width,height:e.height,color:e.options.backgroundColor})),
        key:document.querySelector('[data-heatmap-key="'+id+'"]').innerText,
        tooltip:c.options.plugins.tooltip.callbacks.label({dataset:c.data.datasets[0],dataIndex:0})}}''', cid)


def _chart_csv(page, cid, name):
    return _csv(page, page.locator('#'+cid).locator('..'), EVIDENCE/name)


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_heatmap_summaries_filters_ownership_exports(tmp_path, width):
    from playwright.sync_api import sync_playwright

    _build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':width,'height':900}, device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
            _route(page,tmp_path)
            page.goto('http://heatmap-summary.test/index.html',wait_until='networkidle')
            _ready(page)
            ids=[_id(page,'Filtered cells '+stat) for stat in STATS]
            other=[_id(page,name+' cell mean') for name in ('Global','Row-relative')]
            assert _values(page,ids)==['540','135','0.00','270','4']
            assert _values(page,other)==['135','135']
            assert len(_plot(page,ids[0])['cells'])==4
            off,on=[_id(page,'Heatmap summary '+state) for state in ('off','on')]
            assert _box(page,off).count()==0
            # Compare geometry in equivalent two-column panels, including key and tooltip.
            assert _plot(page,off)==_plot(page,on)
            assert _plot(page,on)['height']==200
            assert page.evaluate('ids=>ids.map(id=>document.getElementById(id).toDataURL()).every((v,i,a)=>v===a[0])',[off,on])
            static_guard=_id(page,'Static original-input guard')
            assert _values(page,[static_guard])==['—']
            assert _plot(page,static_guard)['cells']==[{'x':'B','y':'two','v':10}]
            assert 'raw values' in _box(page,static_guard).get_attribute('title')
            boundaries=[_id(page,'Boundary '+stat) for stat in STATS]
            boundary_section=_section(page,boundaries[0])
            boundary_section.get_by_role('button',name='Safe total',exact=True).click()
            boundary_values=_values(page,boundaries)
            assert boundary_values[0]=='—' and '—' not in boundary_values[1:] and boundary_values[-1]=='2'
            assert 'arithmetic' in _box(page,boundaries[0]).get_attribute('title')
            for case,count in (('Unsafe aggregate','1'),('Unsafe raw','1')):
                boundary_section.get_by_role('button',name=case,exact=True).click()
                assert _values(page,boundaries)==['—','—','—','—',count]
            boundary_section.get_by_role('button',name='Cancellation',exact=True).click()
            assert _values(page,boundaries)[:2]==['1','0.33']
            assert _box(page,on).locator('.fw-heatmap-summary-reason').count()==0
            assert _chart_csv(page,off,f'off-{width}.csv')==_chart_csv(page,on,f'on-{width}.csv')
            # This is the actual retained limitation: scale domains are not chart.data.labels.
            assert (EVIDENCE/f'on-{width}.csv').read_bytes().decode('utf-8-sig').strip()=='label,Value'
            section=_section(page,ids[0])
            section.get_by_role('button',name='iOS',exact=True).click()
            _day(section,'2026-08-01')
            assert _values(page,ids)==['60','15','0.00','30','4']
            table=section.locator('[data-csv-target]').get_attribute('data-csv-target')
            table_csv=_csv(page,section.locator(f'[data-csv-target="{table}"]').locator('..'),EVIDENCE/f'source-ios-aug1-{width}.csv')
            rows=list(csv.DictReader(StringIO(table_csv.decode('utf-8-sig'))))
            assert len(rows)==5 and sum(float(row['units']) for row in rows)==60
            assert {row['day'] for row in rows}=={'2026-08-01'} and {row['platform'] for row in rows}=={'iOS'}
            assert sorted(float(row['units']) for row in rows)==[0,10,12,18,20]
            section.get_by_role('button',name='Android',exact=True).click()
            _day(section,'2026-08-02')
            assert _values(page,ids)==['240','60','0.00','120','4']
            assert _values(page,other)==['135','135']
            restored_url=page.url
            page.reload(wait_until='networkidle')
            _ready(page)
            assert _values(page,ids)==['360','90','0.00','180','4'], restored_url
            # Native explicit date URL decoding works independently of the retained
            # multi-bar preset serialization limitation above.
            url=page.evaluate('''()=>{const u=new URL(location.href);
                u.searchParams.set('showcase_heatmap_summary_reducers.day','2026-08-02..2026-08-02');return u.href}''')
            page.goto(url,wait_until='networkidle')
            _ready(page)
            assert _values(page,ids)==['240','60','0.00','120','4']
            section=_section(page,ids[0])
            page.evaluate('id=>{const c=_chartInstances[id];c.options.scales.x.min=0;c.options.scales.x.max=0;c.update("none")}',ids[0])
            assert _values(page,ids)==['240','60','0.00','120','4']
            page.evaluate('id=>_fwRenderers.heatmap(id,_reportData.components[id])',ids[0])
            edges=[_id(page,f'Eligibility {stat} {drop}') for stat,drop in (('mean',False),('count',False),('mean',True),('count',True))]
            edge_section=_section(page,edges[0])
            for case,expected in (
                ('Absent and zero',['10','3','10','3']),
                ('Null input',['—','2','—','1']),
                ('String input',['—','2','—','2']),
                ('Boolean input',['—','2','—','2']),
                ('All zero',['0.00','2','—','0']),
                ('Signed cells',['-5','3','10','1']),
                ('Invalid dropped group',['—','2','—','1']),
                ('Fractional rates',['0.20','2','0.20','2']),
            ):
                edge_section.get_by_role('button',name=case,exact=True).click()
                assert _values(page,edges)==expected,case
                assert 'Entry count is independent of numeric completeness' in _box(page,edges[1]).get_attribute('title')
                assert 'x AND y groups have positive cells' in _box(page,edges[2]).get_attribute('title')
            edge_section.get_by_role('button',name='Null input',exact=True).click()
            assert _plot(page,edges[0])['cells'][0]['v']==0
            assert 'raw values' in _box(page,edges[0]).locator('.fw-heatmap-summary-reason').inner_text()
            guard_capture=edge_section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE/f'native-input-guard-{width}.png').write_bytes(base64.b64decode(guard_capture['image']['data_base64']))
            page.evaluate("fw.filterEngine.setFilter('showcase_heatmap_summary_eligibility','empty','case','equals','Absent case')")
            assert _values(page,edges)==['—','0','—','0']
            assert 'No retained' in _box(page,edges[0]).locator('.fw-heatmap-summary-reason').inner_text()
            page.evaluate("fw.filterEngine.clearFilter('showcase_heatmap_summary_eligibility','empty')")
            edge_section.get_by_role('button',name='Absent and zero',exact=True).click()
            # Read actual raster pixels from a genuinely absent slot and a supplied zero.
            raster=page.evaluate('''id=>{const c=_chartInstances[id],r=c.currentDevicePixelRatio;
                function pixel(x,y){return [...c.ctx.getImageData(Math.round(x*r),Math.round(y*r),1,1).data].join(',')}
                const zero=c.getDatasetMeta(0).data[0].getCenterPoint(), colors=new Set();
                const x=c.scales.x.getPixelForValue('12'),y=c.scales.y.getPixelForValue('Tue');
                for(let dx=-10;dx<10;dx++)for(let dy=-8;dy<8;dy++)colors.add(pixel(x+dx,y+dy));
                return {zero:pixel(zero.x+20,zero.y-8),missing:[...colors]}}''',edges[0])
            assert len(raster['missing'])>2 and raster['zero'] not in raster['missing']
            edge_section.get_by_role('tab',name='Rate snapshot',exact=True).click()
            revealed=_id(page,'Fractional rate mean')
            page.locator('#'+revealed).scroll_into_view_if_needed()
            page.wait_for_function('id=>!!_chartInstances[id]',arg=revealed)
            assert _values(page,[revealed])==['20.00%']
            assert _box(page,revealed).locator('.fw-chart-summary-label').inner_text()=='Rate <mean> & supplied'
            assert _box(page,revealed).locator('mean').count()==0
            for theme in ('trellum light','trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert _values(page,ids)==['240','60','0.00','120','4']
                assert _values(page,other)==['135','135']
                assert _plot(page,off)==_plot(page,on)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                for loc,label in ((_section(page,off),'pair'),(section,'filtered'),(edge_section,'eligibility'),(_box(page,revealed),'rate')):
                    loc.screenshot(path=str(EVIDENCE/f'{width}-{theme.split()[-1]}-{label}.png'))
                # Existing capture removes sticky chrome without altering the report renderer.
                pair_capture=_section(page,off).evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                (EVIDENCE/f'native-pair-{width}-{theme.split()[-1]}.png').write_bytes(base64.b64decode(pair_capture['image']['data_base64']))
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert _values(page,ids)==['240','60','0.00','120','4']
            # Canvas/config/source replacement, opt-out and re-entry own only local tokens.
            lifecycle=page.evaluate('''id=>{
                const cfg=_reportData.components[id],canvas=document.getElementById(id),summary=cfg.header_summary;
                canvas.setAttribute('aria-describedby',canvas.getAttribute('aria-describedby')+' unrelated');
                canvas.replaceWith(canvas.cloneNode(true));cfg.dataset_id='showcase_heatmap_summary_reducers';_fwRenderers.heatmap(id,cfg);
                const read=()=>document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent;
                const replaced=read();cfg.dataset_id='showcase_heatmap_summary_normalization';_fwRenderers.heatmap(id,cfg);
                const independent=read();delete cfg.header_summary;_fwRenderers.heatmap(id,cfg);
                const removed=document.querySelectorAll('[data-chart-summary="'+id+'"]').length;
                const described=document.getElementById(id).getAttribute('aria-describedby');
                cfg.header_summary=summary;_fwRenderers.heatmap(id,cfg);_fwRenderers.heatmap(id,cfg);
                const subscriptions=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                return {replaced,independent,removed,described,restored:read(),subscriptions,
                    descriptions:document.querySelectorAll('#'+id+'-heatmap-summary-scope').length};}''',other[1])
            assert lifecycle=={'replaced':'60','independent':'135','removed':0,'described':other[1]+'-header-context unrelated',
                'restored':'135','subscriptions':1,'descriptions':1}
            count=page.evaluate('''id=>{const cfg=_reportData.components[id],ds=cfg.dataset_id;
                delete cfg.dataset_id;cfg.cells=[{x:'09',y:'Mon',v:0}];cfg.x_labels=['09'];cfg.y_labels=['Mon'];cfg.summary_input_reason='';
                _fwRenderers.heatmap(id,cfg);const n=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                cfg.dataset_id=ds;_fwRenderers.heatmap(id,cfg);return n}''',other[1])
            assert count==0
            # Normal native capture admission and real completed filtered source rows.
            snap=section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE/f'native-section-{width}.json').write_text(json.dumps(snap,indent=2))
            (EVIDENCE/f'native-section-{width}.png').write_bytes(base64.b64decode(snap['image']['data_base64']))
            if width==1280:
                for ext,fn in (('png','exportPNG'),('pdf','exportPDF')):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{fn}()')
                    download.value.save_as(str(EVIDENCE/f'native-report.{ext}'))
            # Source replacement uses currently delivered rows, not stale chart data.
            page.evaluate("_fwFilterEngine.setLiveRows('showcase_heatmap_summary_normalization',['day','platform','x','y','units'],[['2026-08-01','iOS','09','Mon',8]])")
            assert _values(page,other)==['8','8']
            for detached in (ids[-1],revealed):
                page.evaluate('id=>document.getElementById(id).remove()',detached)
                page.wait_for_function('id=>!_fwHeatmapBindings[id]',arg=detached)
                assert _box(page,detached).count()==0
                assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length',detached)==0
            assert page.locator('#'+revealed+'-header-context').count()==1
            assert errors==[],errors
        finally:
            browser.close()


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_historical_baseline_off_pixels_cells_key_geometry_csv(tmp_path,width):
    from playwright.sync_api import sync_playwright

    roots=[]
    for name in ('baseline','current'):
        output=tmp_path/name
        output.mkdir()
        cls=baseline_class() if name=='baseline' else HeatmapChart
        df=frame()
        df.loc[2,'y']='two'
        ctx=ReportContext(config={'name':'Heatmap baseline','studio':'test','category':'Test',
            'description':'Supplied static and bound quantities for exact default heatmap compatibility.'},slug='baseline',output_dir=str(output))
        ctx.add_section('Default heatmaps',[DataSource('baseline_source',df),
            FilterBar('baseline_source',df,filters=[{'column':'y','type':'toggle'}]),
            cls(df,'x','y','v','Static',static=True),cls(df,'x','y','v','Bound',dataset_id='baseline_source'),
            BarChart(df,'x','v','CSV delegate',static=True),DataTable(df,'Raw source rows · units',dataset_id='baseline_source')])
        result=validate_report(ctx,details=compute_details(ctx))
        assert result.summary['warn']==result.summary['fail']==0,result.to_dict()
        render_report(ctx,str(output),auto_refresh=False)
        copy_vendor_tree(output)
        if name=='baseline':
            for asset in ('js','css'):
                relative=f'trellum/static/{asset}/components/heatmap.{asset}'
                current=(ROOT/relative).read_text()
                historical=subprocess.check_output(['git','show',f'{BASELINE}:{relative}'],cwd=ROOT,text=True)
                replaced=False
                for file in [output/'index.html',*output.rglob('*.js'),*output.rglob('*.css')]:
                    text=file.read_text()
                    if current in text:
                        file.write_text(text.replace(current,historical))
                        replaced=True
                assert replaced
        roots.append(output)
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            results={}
            for name,output in zip(('baseline','current'),roots):
                page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
                _route(page,output)
                page.goto('http://heatmap-baseline.test/index.html',wait_until='networkidle')
                _ready(page)
                for theme in ('trellum light','trellum dark'):
                    page.locator('#fwThemeSelect').select_option(theme)
                    _ready(page)
                    _settle(page)
                    page.mouse.move(0,0)
                    geometry=[_plot(page,_id(page,title)) for title in ('Static','Bound')]
                    image=page.locator('.fw-section').screenshot(path=str(EVIDENCE/f'opt-out-{name}-{width}-{theme.split()[-1]}.png'),animations='disabled')
                    data=[_chart_csv(page,_id(page,title),f'baseline-{name}-{title}-{width}-{theme.split()[-1]}.csv') for title in ('Static','Bound')]
                    results[name,theme]=(geometry,Image.open(BytesIO(image)).convert('RGBA').tobytes(),data)
                page.close()
            for theme in ('trellum light','trellum dark'):
                assert results['baseline',theme]==results['current',theme]
        finally:
            browser.close()


@pytest.mark.heavy
def test_repeated_heatmap_ownership_and_sparse_resources(tmp_path):
    """Bounded summary population; no Cartesian entries or accumulating listeners."""
    from playwright.sync_api import sync_playwright
    _build(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900})
            _route(page,tmp_path)
            page.goto('http://heatmap-resources.test/index.html',wait_until='networkidle')
            _ready(page)
            cid=_id(page,'Filtered cells count')
            result=page.evaluate('''id=>{const cfg=_reportData.components[id];
                const oldAdd=window.addEventListener,oldRemove=window.removeEventListener;let adds=0,removes=0;
                window.addEventListener=function(t,...a){if(t==='fw-theme-change')adds++;return oldAdd.call(this,t,...a)};
                window.removeEventListener=function(t,...a){if(t==='fw-theme-change')removes++;return oldRemove.call(this,t,...a)};
                for(let i=0;i<40;i++)_fwRenderers.heatmap(id,cfg);
                const subscriptions=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                const header=document.querySelectorAll('[data-chart-summary="'+id+'"]').length;
                const tokens=document.getElementById(id).getAttribute('aria-describedby').split(/\\s+/);
                delete cfg.dataset_id;cfg.cells=Array.from({length:300},(_,i)=>({x:'x'+i,y:'y'+i,v:i}));
                cfg.x_labels=cfg.cells.map(c=>c.x);cfg.y_labels=cfg.cells.map(c=>c.y);_fwRenderers.heatmap(id,cfg);
                const c=_chartInstances[id];return {adds,removes,subscriptions,header,tokens:tokens.length,
                    cells:c.data.datasets[0].data.length,rectangles:c.getDatasetMeta(0).data.length,
                    value:document.querySelector('[data-chart-summary="'+id+'"] .fw-chart-summary-value').textContent}}''',cid)
            assert result=={'adds':40,'removes':41,'subscriptions':1,'header':1,'tokens':1,'cells':300,'rectangles':300,'value':'300'}
            page.evaluate('''id=>{const cfg=_reportData.components[id];cfg.dataset_id='showcase_heatmap_summary_reducers';
                _fwMetricsWorkspace=true;_fwRenderers.heatmap(id,cfg);_fwRenderers.heatmap(id,cfg)}''',cid)
            assert page.evaluate('id=>typeof _fwMetricDrawLeases[id]',cid)=='function'
            assert page.evaluate('id=>Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length',cid)==1
            page.evaluate("window.dispatchEvent(new Event('pagehide'))")
            assert page.evaluate('Object.keys(_fwMetricDrawLeases).length')==0
        finally:
            browser.close()
