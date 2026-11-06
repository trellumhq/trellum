"""Production registrar, cleanup, capture admission and baseline geometry."""
from __future__ import annotations
import importlib.util
import json
import subprocess
import sys
import types

import pytest
from trellum.components import MiniKpi, KpiRow, Panel
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.testing.test_metric_views import ROOT
from trellum.testing.test_marketing_composition_browser import build, rows, SOURCE, RESULT, EVIDENCE
from trellum.testing.test_chart_header_summaries_browser import _route


def test_native_registrar_cleanup_rebinding_and_capture_admission(tmp_path):
    from playwright.sync_api import sync_playwright
    module=build(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1280,'height':900})
            _route(page,tmp_path)
            page.goto('http://metric-lifecycle.test/index.html',wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=RESULT)
            # A nonvisual registrar publishes before consumer capture, without marking unresolved as ready.
            state=page.evaluate('''async args=>{
                const e=fw.filterEngine, section=document.getElementById('sec-marketing-geography');
                e.init(args.source,[{country:'GB',cost:'unsafe',installs:1}]);
                let blocked='';try {await fw.captureElementSnapshotForAnalysis(section)} catch(error) {blocked=error.message}
                return {blocked,rows:e.getFiltered(args.result),status:_fwMetricViews.status(args.result)};
            }''',{'source':SOURCE,'result':RESULT})
            assert state['blocked'] and not state['status']['ready']
            assert all(r['cost'] is None for r in state['rows'])
            page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':SOURCE,'rows':module.facts().to_dict('records')})
            before=page.evaluate('''()=>{
                const entries=Object.entries(_reportData.components);
                const [viewId,cfg]=entries.find(([id,c])=>c.type==='metric_view');
                const [miniId,mini]=entries.find(([id,c])=>c.binding);
                const original=JSON.parse(JSON.stringify(mini));
                const before=fw.filterEngine._lifecycleListeners.size;
                _fwRenderers.mini_kpi(miniId,{...mini,binding:undefined,value:44});
                let unbound=document.getElementById(miniId).textContent;
                let removed=document.getElementById(miniId).querySelectorAll('.fw-mini-binding-reason').length;
                _fwRenderers.mini_kpi(miniId,original);_fwRenderers.mini_kpi(miniId,original);
                const rebound=fw.filterEngine._lifecycleListeners.size;
                _fwRenderers.metric_view(viewId,{...cfg,groups:[{id:'worldwide',label:'Replacement'}]});
                let replacement=fw.filterEngine.getFiltered(cfg.dataset_id);
                _fwRenderers.metric_view(viewId,cfg);
                const wrapper=document.createElement('div');wrapper.id='owned-removal';
                const node=document.createElement('span');node.id='owned-mini';wrapper.append(node);document.body.append(wrapper);
                _fwRenderers.mini_kpi('owned-mini',original);
                const added=fw.filterEngine._lifecycleListeners.size;
                wrapper.remove();
                return {before,rebound,added,unbound,removed,replacement,viewId,miniId};
            }''')
            assert before['before']==before['rebound']==33
            assert before['added']==34 and before['removed']==0 and '44' in before['unbound']
            assert len(before['replacement'])==1 and before['replacement'][0]['group_label']=='Replacement'
            page.wait_for_function('n=>fw.filterEngine._lifecycleListeners.size===n',arg=before['before'])
            page.evaluate('id=>document.getElementById(id).remove()',before['viewId'])
            page.wait_for_function('id=>!fw.filterEngine.isReady(id)',arg=RESULT)
            assert page.evaluate('fw.filterEngine._lifecycleListeners.size')==32
            assert page.evaluate('id=>document.getElementById(id).querySelector(".fw-mini-val").textContent',before['miniId'])=='—'
            # All owned lifecycle and row listeners disappear when native scope DOM is removed.
            page.locator('#sec-marketing-geography').evaluate('el=>el.remove()')
            page.wait_for_function('()=>fw.filterEngine._lifecycleListeners.size===0')
            assert page.evaluate('id=>(fw.filterEngine._subscribers[id]||[]).filter(s=>s.id.includes(":metric-view")).length',SOURCE)==0
        finally: browser.close()


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_static_mini_and_kpi_row_baseline_geometry_pixels(tmp_path,width):
    from playwright.sync_api import sync_playwright
    source=subprocess.check_output(['git','show','1c0c49710c5d073a78f83ab069fd517cd49c1632:trellum/components/kpis.py'],cwd=ROOT,text=True)
    module=types.ModuleType('_metric_browser_baseline');sys.modules[module.__name__]=module;exec(source,module.__dict__)
    outputs=[]
    for name,mini,row in [('baseline',module.MiniKpi,module.KpiRow),('current',MiniKpi,KpiRow)]:
        output=tmp_path/name;output.mkdir()
        ctx=ReportContext(config={'name':'Static parity','studio':'test','category':'Test'},slug='static-parity',output_dir=str(output))
        ctx.add_section('Existing layout',[Panel([mini('Supplied',1234.5,'currency'),mini('Stacked',12.5,'percent',stacked=True)]),
            row([{'label':'One','value':10},{'label':'Two','value':20},{'label':'Three','value':30}])])
        render_report(ctx,str(output),auto_refresh=False);copy_vendor_tree(output);outputs.append(output)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            shots=[];geometry=[]
            for name,output in zip(('baseline','current'),outputs):
                page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
                _route(page,output);page.goto('http://metric-static.test/index.html',wait_until='networkidle')
                page.wait_for_function('()=>document.querySelector(".fw-mini-val")');page.evaluate('document.fonts.ready')
                geometry.append(page.locator('.fw-mini-kpi,.fw-kpi-card').evaluate_all('nodes=>nodes.map(n=>{let r=n.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})'))
                shots.append(page.locator('#sec-existing-layout').screenshot(path=str(EVIDENCE/f'static-{width}-{name}.png')))
                page.close()
            assert geometry[0]==geometry[1]
            assert shots[0]==shots[1]
        finally: browser.close()


def test_native_scope_switches_replace_owned_registrars(tmp_path):
    import pandas as pd
    from playwright.sync_api import sync_playwright
    from trellum.components import DataSource, FilterBar, ScopedDataSource, DataTable
    from trellum.components.metric_views import MetricView
    from trellum.reporting.diagnostics import compute_details
    from trellum.validation import validate_report
    from trellum.validation.checks.metric_views import check_metric_views
    ctx=ReportContext(config={'name':'Native metric scopes','studio':'test','category':'Test','description':'Compare complete synthetic additive facts across independent native report scopes and local country filters.'},slug='metric-scopes',output_dir=str(tmp_path))
    for scope,amount in [('scope_a',10),('scope_b',30)]:
        frame=pd.DataFrame({'country':['GB','US'],'cost':[amount,2*amount],'installs':[2,3]})
        ctx.set_scope(scope,label=scope)
        view=MetricView(scope+'_result',parent=scope+'_scoped',groups=[{'id':'all','label':'All'}],metrics={'cost':{'agg':'sum','column':'cost'}})
        ctx.add_section('Scope metrics', [DataSource(scope+'_facts',frame),ScopedDataSource(scope+'_scoped',scope+'_facts'),
            FilterBar(scope+'_scoped',frame,filters=[{'column':'country','type':'toggle'}]),view,
            MiniKpi('Cost',binding={'dataset_id':view.id,'group':'all','metric':'cost'}),
            DataTable(view.empty_frame(),'Scope rows',dataset_id=view.id)])
    result=validate_report(ctx,details=compute_details(ctx));check_metric_views(ctx,result)
    assert [(c.id,c.message) for c in result.checks if c.level in {'warn','fail'}]==[]
    render_report(ctx,str(tmp_path),auto_refresh=False);copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page();_route(page,tmp_path)
            page.goto('http://metric-scopes.test/index.html',wait_until='networkidle')
            page.wait_for_function('()=>document.querySelector(".fw-mini-val")?.textContent==="30"')
            for scope,expected in [('scope_b','90'),('scope_a','30'),('scope_b','90'),('scope_a','30')]:
                page.get_by_role('button',name=scope,exact=True).click()
                page.wait_for_function('value=>document.querySelector(".fw-mini-val")?.textContent===value',arg=expected)
                assert page.evaluate('fw.filterEngine._lifecycleListeners.size')==2
            page.get_by_role('button',name='GB',exact=True).click()
            assert page.locator('.fw-mini-val').inner_text()=='10'
            page.evaluate('renderAll();renderAll()')
            assert page.locator('.fw-mini-val').inner_text()=='10'
            assert page.evaluate('fw.filterEngine._lifecycleListeners.size')==2
        finally: browser.close()
