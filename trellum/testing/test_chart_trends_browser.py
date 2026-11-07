"""Native report fits: filters, source parity, ownership and bounded captures."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from trellum.components import Grid
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _ready, _route
from trellum.testing.test_chart_trends import example_context
from trellum.testing.test_doughnut_summaries_browser import _csv, _settle
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_TREND_EVIDENCE', '/tmp/trellum-native-trends'))


def build(output):
    ctx = example_context(output)
    for report_section in ctx.sections:
        for component in report_section['components']:
            if isinstance(component, Grid):
                component.color_cycle = 'theme'
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    assert [(c.id, c.message) for c in result.checks if c.level in ('warn', 'fail')] == []
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'diagnostics.json').write_text(json.dumps({'validation': result.to_dict(), 'details': details}, default=str, indent=2))
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)


def ids(page):
    return page.evaluate('''()=>Object.fromEntries(Object.entries(_reportData.components)
        .filter(([id,c])=>c.trend_line).map(([id,c])=>[c.title||document.getElementById(id).dataset.fwTitle,id]))''')


def trend(page, cid):
    return page.evaluate('id=>JSON.parse(JSON.stringify(_chartInstances[id]._fwTrends))', cid)


def parent(page, cid):
    return page.locator('#' + cid).locator('..')


def section(page, cid):
    return page.locator('#' + cid).locator('xpath=ancestor::div[contains(@class,"fw-section")][1]')


def state(page, cid):
    return page.evaluate('''id=>{const c=_chartInstances[id];return {data:JSON.stringify(c.data),
        summary:document.querySelector('[data-chart-summary="'+id+'"]')?.textContent,
        legend:c._fwLegendEl?.textContent,scales:['x','y'].map(a=>[c.scales[a].min,c.scales[a].max]),
        height:c.canvas.parentElement.getBoundingClientRect().height}}''', cid)


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_trends_filters_source_lifecycle_and_exports(tmp_path, width):
    from playwright.sync_api import sync_playwright

    build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            _route(page, tmp_path)
            page.goto('http://native-trends.test/index.html', wait_until='networkidle')
            _ready(page)
            _settle(page)
            charts = ids(page)
            line, ratio, split, scatter, static = [charts[t] for t in (
                'Filtered daily trend', 'Filtered daily ratio', 'Separate platform trends',
                'Numeric scatter trends', 'Static observed trend')]
            assert trend(page, line)['reason'] == ''
            full = trend(page, line)['series'][0]['fit']
            assert full['n'] == 13 and full['excluded'] == 1
            assert trend(page, ratio)['series'][0]['fit']['n'] == 12
            assert [s['fit']['n'] for s in trend(page, scatter)['series']] == [14, 14]
            assert [s['fit']['slope'] for s in trend(page, scatter)['series']] == pytest.approx([3, 3])
            assert trend(page, static)['series'][0]['fit']['n'] == 7
            assert trend(page, static)['series'][0]['fit']['excluded'] == 1
            assert page.evaluate('id=>_chartInstances[id].data.datasets[0].data[2]', static) is None
            assert page.evaluate('id=>_chartInstances[id].data.datasets[1]._fwForecast', static)
            assert page.evaluate('id=>_chartInstances[id].data.datasets.length', static) == 2
            # All source arrays, summaries, CSV and ordinary legends are owned by
            # the existing chart. Visibility changes draw only the derived path.
            before = state(page, line)
            csv_before = _csv(page, parent(page, line), EVIDENCE / f'line-before-{width}.csv')
            controls = page.locator('#' + line + '-trends')
            controls.get_by_role('button', name='Hide trend', exact=True).click()
            assert state(page, line) == before
            hidden_png = page.locator('#' + line).evaluate('el=>el.toDataURL()')
            controls.get_by_role('button', name='Show trend', exact=True).click()
            shown_png = page.locator('#' + line).evaluate('el=>el.toDataURL()')
            assert shown_png != hidden_png
            assert _csv(page, parent(page, line), EVIDENCE / f'line-after-{width}.csv') == csv_before
            assert csv_before.decode('utf-8-sig').splitlines()[0] == 'label,observed'
            split_parent = parent(page, split).locator('..')
            split_parent.locator('.fw-chart-legend .cl').first.click()
            assert page.evaluate('id=>!_chartInstances[id].isDatasetVisible(0)', split)
            split_fit = trend(page, split)['series'][0]['fit']
            split_parent.locator('.fw-chart-legend .cl').first.click()
            assert trend(page, split)['series'][0]['fit'] == split_fit
            # Actual native filters recompute post-aggregation. All-App removes
            # the incomplete Web contributor and changes the slope/population.
            report_section = section(page, line)
            report_section.get_by_role('button', name='App', exact=True).click()
            selected = trend(page, line)['series'][0]['fit']
            assert selected['n'] == 14 and selected['excluded'] == 0
            assert selected['slope'] != full['slope']
            assert trend(page, ratio)['series'][0]['fit']['n'] == 13
            assert trend(page, scatter)['series'][0]['identity'] == {'column': 'return_value', 'group': 'App'}
            assert trend(page, static)['series'][0]['fit']['n'] == 7
            report_section.locator('.fw-date-trigger').click()
            for edge, value in (('min', '2026-10-04'), ('max', '2026-10-10')):
                field = report_section.locator(f'input[data-edge="{edge}"]')
                field.fill(value)
                field.dispatch_event('change')
            report_section.locator('.fw-date-trigger').click()
            fit = trend(page, line)['series'][0]['fit']
            assert fit['n'] == 7 and fit['slope'] == pytest.approx(2)
            # Zoom/legend only crop/hide the existing fit; they never pool data.
            page.evaluate('''id=>{const c=_chartInstances[id];c.zoomScale('x',{min:2,max:4},'none');c.update('none')}''', line)
            assert trend(page, line)['series'][0]['fit'] == fit
            # Details expose the readable origin, units, counts and descriptive R².
            controls.locator('summary').click()
            assert 'n=7' in controls.inner_text() and '/day' in controls.inner_text()
            assert '2026-10-04' in controls.inner_text() and 'R²' in controls.inner_text()
            controls.get_by_role('button', name='Hide trend', exact=True).click()
            page.evaluate('renderAll();renderAll()')
            _ready(page)
            assert controls.get_by_role('button', name='Show trend', exact=True).count() == 1
            assert controls.locator('details').get_attribute('open') is not None
            controls.get_by_role('button', name='Show trend', exact=True).click()
            # Native theme update must resolve overlay from the actual source.
            for theme in ('trellum light', 'trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                _ready(page)
                _settle(page)
                assert trend(page, line)['series'][0]['fit'] == fit
                paint = page.evaluate('''id=>{const c=_chartInstances[id], context=c.ctx,
                    strokes=[], original=context.stroke;
                    context.stroke=function(...args){
                        if(this.getLineDash().join(',')==='6,3,1,3') strokes.push(this.strokeStyle);
                        return original.apply(this,args)};
                    try {c.draw()} finally {context.stroke=original}
                    return {strokes,source:c.data.datasets[0].borderColor,
                        expected:getThemeColors().chart_colors[1]}}''', ratio)
                assert paint['source'] == paint['expected']
                assert paint['strokes'] == [paint['source']]
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                report_section.screenshot(path=str(EVIDENCE / f'{width}-{theme.split()[-1]}-filtered.png'))
                section(page, static).screenshot(path=str(EVIDENCE / f'{width}-{theme.split()[-1]}-static.png'))
            # Export uses native capture, including the visible overlay and DOM
            # disclosure, and restores all source values and fit state afterwards.
            capture_before = [state(page, c) for c in (line, ratio, scatter)]
            snapshot = report_section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE / f'native-section-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            assert [state(page, c) for c in (line, ratio, scatter)] == capture_before
            if width == 1280:
                for extension, function in (('png', 'exportPNG'), ('pdf', 'exportPDF')):
                    with page.expect_download(timeout=60000) as download:
                        page.evaluate(f'fw.{function}()')
                    download.value.save_as(str(EVIDENCE / f'native-report.{extension}'))
                assert [state(page, c) for c in (line, ratio, scatter)] == capture_before
            replacement = page.evaluate("""({line,scatter})=>{
                const e=_fwFilterEngine, original=e._columnar.showcase_native_trends;
                const source={_cols:original._cols,_dict:original._dict,_data:original._data.map(r=>r.slice())};
                const observed=source._cols.indexOf('observed'), response=source._cols.indexOf('return_value');
                const changed={...source,_data:source._data.map(r=>r.map((v,i)=>
                    (i===observed||i===response)&&typeof v==='number'?v*2:v))};
                e.initColumnar('showcase_native_trends',changed);
                const slopes=[_chartInstances[line]._fwTrends.series[0].fit.slope,
                    _chartInstances[scatter]._fwTrends.series[0].fit.slope];
                e.initColumnar('showcase_native_trends',source);return slopes;
            }""", {'line': line, 'scatter': scatter})
            assert replacement == pytest.approx([4, 6])
            assert trend(page, line)['series'][0]['fit'] == fit
            page.evaluate("id=>{const c=_chartInstances[id];c.options.scales.y.type='logarithmic';c.update('none')}", scatter)
            assert 'linear value axes' in page.locator('#' + scatter + '-trends .fw-trend-status').inner_text()
            page.evaluate("id=>{const c=_chartInstances[id];c.options.scales.y.type='linear';c.update('none')}", scatter)
            # A replaced canvas is a new owner; old UI/subscriptions disappear.
            lifecycle = page.evaluate('''async id=>{
                const cfg=_reportData.components[id], old=document.getElementById(id);
                old.replaceWith(old.cloneNode(true));_fwRenderers[cfg.type](id,cfg);
                await new Promise(resolve=>setTimeout(resolve,0));
                const count=()=>document.querySelectorAll('#'+id+'-trends').length;
                const adopted=count(), subs=Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length;
                const declaration=cfg.trend_line;delete cfg.trend_line;_fwRenderers[cfg.type](id,cfg);
                const removed=count();cfg.trend_line=declaration;_fwRenderers[cfg.type](id,cfg);
                _fwRenderers[cfg.type](id,cfg);const restored=count();
                document.getElementById(id).remove();await new Promise(resolve=>setTimeout(resolve,0));
                return {adopted,subs,removed,restored,detached:count(),chart:!!_chartInstances[id],
                    remaining:Object.values(_fwFilterEngine._subscribers).flat().filter(s=>s.id===id).length};}''', scatter)
            assert lifecycle == {'adopted': 1, 'subs': 1, 'removed': 0, 'restored': 1, 'detached': 0, 'chart': False, 'remaining': 0}
            assert errors == []
        finally:
            browser.close()


@pytest.mark.heavy
def test_native_trend_resource_budget_and_no_sampling(tmp_path):
    from playwright.sync_api import sync_playwright

    build(tmp_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            _route(page, tmp_path)
            page.goto('http://trend-resource.test/index.html', wait_until='networkidle')
            _ready(page)
            scatter = ids(page)['Numeric scatter trends']
            result = page.evaluate('''id=>{
                const cfg={..._reportData.components[id]};delete cfg.dataset_id;
                cfg.trend_line={method:'linear',series:'each',details:true,columns:['y'],group_column:'group'};
                cfg.datasets=Array.from({length:8},(_,j)=>({label:'group '+j,borderColor:'#4488aa',
                    data:Array.from({length:6250},(_,i)=>({x:1700000000001+i,y:2*i+j}))}));
                const started=performance.now();const fit=_fwTrends.result(cfg,null,{datasets:cfg.datasets},true);
                const fitMs=performance.now()-started;
                _fwRenderers.scatter(id,cfg);
                const native=_chartInstances[id]._fwTrends.series.map(s=>s.fit.n);
                const details=document.getElementById(id+'-trends').textContent;
                cfg.datasets[0].data.push({x:1700000007000,y:14000});_fwRenderers.scatter(id,cfg);
                const over=_chartInstances[id]._fwTrends.reason;
                cfg.datasets=cfg.datasets.map(ds=>({...ds,data:ds.data.slice(0,3)}));cfg.datasets.push({...cfg.datasets[0],label:'ninth'});
                _fwRenderers.scatter(id,cfg);const series=_chartInstances[id]._fwTrends.reason;
                return {fitMs,native,slopes:fit.series.map(s=>s.fit.slope),over,series,details};}''', scatter)
            line = ids(page)['Filtered daily trend']
            bound = page.evaluate("""id=>{
                const cfg={..._reportData.components[id],dataset_id:'trend-resource-line'};
                const columns=Array.from({length:8},(_,i)=>'v'+i), labels=Array.from({length:6250},(_,i)=>
                    new Date(Date.UTC(2000,0,1)+i*86400000).toISOString().slice(0,10));
                const rows=labels.flatMap((day,i)=>Array.from({length:8},()=>Object.assign({day},
                    Object.fromEntries(columns.map((col,j)=>[col,(2*i+j)/8])))));
                cfg.value_cols=columns;cfg.y_labels=columns;cfg.trend_line={method:'linear',series:'each',details:true,columns};
                const spec={labels,datasets:columns.map((col,j)=>({label:col,data:labels.map((day,i)=>2*i+j)}))};
                _fwFilterEngine.init('trend-resource-line',rows);
                const start=performance.now(),fit=_fwTrends.line(cfg,rows,spec).trends,fitMs=performance.now()-start;
                _fwRenderers.chart(id,cfg);
                const native=_chartInstances[id]._fwTrends.series.map(s=>s.fit.n);
                return {fitMs,native,slopes:fit.series.map(s=>s.fit.slope),contributors:rows.length};
            }""", line)
            assert bound['contributors'] == 50000 and bound['native'] == [6250] * 8
            assert bound['slopes'] == pytest.approx([2] * 8)
            assert bound['fitMs'] < 500, bound
            result['bound_line'] = bound
            assert '1700000000001 to 1700000006250' in result['details']
            assert result['native'] == [6250] * 8
            assert result['slopes'] == pytest.approx([2] * 8)
            assert result['fitMs'] < 500, result
            assert 'budget' in result['over'] and 'budget' in result['series']
            (EVIDENCE / 'resource.json').write_text(json.dumps(result, indent=2))
            page.locator('#' + scatter + '-trends').screenshot(path=str(EVIDENCE / 'phone-budget.png'))
        finally:
            browser.close()
