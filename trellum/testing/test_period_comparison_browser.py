"""Native report controls, real pointer hovers, downloads and capture lifecycle."""
import base64
import csv
import io
import json
import os
from pathlib import Path

import pytest

from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _route
from trellum.testing.test_marketing_composition_browser import download_csv
from trellum.testing.test_period_comparison import example_context, oracle
from trellum.validation import validate_report

EVIDENCE = Path(os.environ.get('TRELLUM_PERIOD_EVIDENCE', '/tmp/trellum-period-comparison'))


def build(output):
    ctx, module = example_context(output)
    details = compute_details(ctx)
    result = validate_report(ctx, details=details)
    issues = [(c.id,c.level,c.section,c.message) for c in result.checks if c.level in {'warn','fail'}]
    assert issues == [], issues
    render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    delivered = json.loads((output/'data.json').read_text())
    assert len(delivered['_ds_'+module.SOURCE]['_data']) == 184
    assert not any('_ds_'+o.id in delivered for o in module.projection().outputs)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE/'native-diagnostics.json').write_text(json.dumps({'validation':result.to_dict(),'details':details},indent=2))
    return module


def rows(page, dataset):
    return page.evaluate('id=>fw.filterEngine.getFiltered(id)',dataset)


def ready_charts(page):
    for table in page.locator('.fw-table-scroll:visible').all():
        table.scroll_into_view_if_needed()
        page.wait_for_function('id=>document.getElementById(id).childElementCount>0',arg=table.get_attribute('id'))
    for canvas in page.locator('canvas:visible').all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function('id=>!!_chartInstances[id]',arg=canvas.get_attribute('id'))
    page.evaluate('document.fonts.ready')


def chart_state(page):
    return page.locator('canvas:visible').evaluate_all('''nodes=>nodes.map(n=>{
        const c=_chartInstances[n.id];return {height:n.getBoundingClientRect().height,
            labels:c.data.labels,values:c.data.datasets.map(d=>d.data),
            tension:c.data.datasets.map(d=>d.tension), y:c.options.scales.y.type};})''')


def check_oracle(page, m, preset, platform):
    actual=rows(page,m.RESULT)
    expected={s:oracle(*m.PRESETS[preset][s],platform) for s in ('A','B')}
    for row in actual:
        for side in ('A','B'):
            assert row[side] == pytest.approx(expected[side][row['metric']])
        a,b=expected['A'][row['metric']],expected['B'][row['metric']]
        assert row['change'] == pytest.approx((a-b)*100 if row['metric']=='click_conversion' else (a-b)/b)


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_native_periods_controls_csv_hover_theme_capture(tmp_path,width):
    from playwright.sync_api import sync_playwright
    m=build(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
            page.set_default_timeout(12000)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
            _route(page,tmp_path)
            page.goto('http://period-comparison.test/index.html',wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=m.RESULT)
            section=page.locator('#sec-period-comparison')
            assert page.get_by_role('tab',name='Flat comparison',exact=True).get_attribute('aria-selected')=='true'
            check_oracle(page,m,'MoM',None)
            for preset in ('WoW','YoY','MoM'):
                page.locator('[data-toggle-id="'+m.PERIOD+'"]').get_by_role('button',name=preset,exact=True).click()
                page.wait_for_function('args=>fw.filterEngine.getFiltered(args[0])[0].preset===args[1]',arg=[m.RESULT,preset])
                for platform in ('iOS','Android'):
                    section.get_by_role('button',name=platform,exact=True).click()
                    check_oracle(page,m,preset,platform)
                section.get_by_role('button',name='All',exact=True).click()
                check_oracle(page,m,preset,None)
            table=page.locator('.fw-table-scroll:visible').first
            table.scroll_into_view_if_needed()
            exported=download_csv(page,table,EVIDENCE/f'all-nine-{width}.csv')
            assert len(exported)==9 and float(exported[0]['A'])==14384
            ready_charts(page)
            before=chart_state(page)
            assert len(before)==4 and all(c['height']==220 for c in before)
            assert before[0]['values'][1][-1] is None and len(before[0]['labels'])==31
            page.mouse.move(0,0)
            off_pixels=[n.screenshot() for n in page.locator('canvas:visible').all()]
            page.get_by_role('tab',name='Original dates on',exact=True).click()
            ready_charts(page)
            assert chart_state(page)==before
            page.mouse.move(0,0)
            assert [n.screenshot() for n in page.locator('canvas:visible').all()]==off_pixels
            assert '2026-09-30' in section.get_by_text('B: 2026-09-01',exact=False).inner_text()
            # Real pointer hit on the original date for each series, same aligned x.
            canvas=page.locator('canvas:visible').first
            for series,expected in [(0,'2026-10-11'),(1,'2026-09-11')]:
                canvas.scroll_into_view_if_needed()
                pos=canvas.evaluate('''(n,series)=>{const c=_chartInstances[n.id],p=c.getDatasetMeta(series).data[10],r=n.getBoundingClientRect();
                    return {x:r.x+p.x,y:r.y+p.y};}''',series)
                page.mouse.move(pos['x'],pos['y'])
                page.wait_for_function('args=>_chartInstances[args[0]].tooltip.title?.[0]?.includes(args[1])',arg=[canvas.get_attribute('id'),expected])
                assert page.evaluate('id=>_chartInstances[id].tooltip.getActiveElements()[0].datasetIndex',canvas.get_attribute('id'))==series
                # Assert the actual framework HTML tooltip, not only Chart.js's internal model.
                painted=page.locator('.fw-chart-tooltip')
                assert painted.locator('.fw-tt-title').inner_text()==expected
                assert painted.evaluate('n=>{const r=n.getBoundingClientRect();return r.x>=0 && r.right<=innerWidth;}')
                canvas.locator('xpath=..').screenshot(path=str(EVIDENCE/f'hover-{width}-{series}.png'))
            page.mouse.move(0,0)
            # Authored group partitions; each table's horizontal scroll stays local on phone.
            page.get_by_role('tab',name='Grouped comparison',exact=True).click()
            group_tables=page.locator('.fw-table-scroll:visible').all()
            assert len(group_tables)==4
            for i,(table,count) in enumerate(zip(group_tables,[3,2,2,2])):
                table.scroll_into_view_if_needed()
                exported=download_csv(page,table,EVIDENCE/f'group-{i}-{width}.csv')
                assert len(exported)==count
                if width==390:
                    assert table.evaluate('n=>n.scrollWidth>n.clientWidth')
                    table.evaluate('n=>n.scrollLeft=n.scrollWidth')
                    assert table.evaluate('n=>n.scrollLeft')>0
                    table.evaluate('n=>n.scrollLeft=0')
            page.get_by_text('All metrics CSV',exact=True).click()
            all_table=page.locator('.fw-table-scroll:visible').last
            all_table.scroll_into_view_if_needed()
            assert len(download_csv(page,all_table,EVIDENCE/f'all-nine-grouped-{width}.csv'))==9
            # Chart CSV keeps existing label + plotted series schema and blank outside-period value.
            canvas=page.locator('canvas:visible').first
            canvas.scroll_into_view_if_needed()
            with page.expect_download() as info:
                page.locator('[data-csv-chart="'+canvas.get_attribute('id')+'"]').click()
            csv_path=EVIDENCE/f'daily-cost-{width}.csv'
            info.value.save_as(str(csv_path))
            plotted=list(csv.DictReader(io.StringIO(csv_path.read_text(encoding='utf-8-sig'))))
            assert len(plotted)==31 and plotted[-1]['B']=='' and float(plotted[10]['A'])==444 and float(plotted[10]['B'])==364
            # Existing native keyboard navigation keeps selection and state.
            page.get_by_role('tab',name='Grouped comparison',exact=True).focus()
            page.keyboard.press('ArrowLeft')
            assert page.get_by_role('tab',name='Flat comparison',exact=True).get_attribute('aria-selected')=='true'
            section.get_by_role('button',name='iOS',exact=True).click()
            page.locator('[data-toggle-id="'+m.PERIOD+'"]').get_by_role('button',name='WoW',exact=True).click()
            page.wait_for_function('id=>fw.filterEngine.getFiltered(id)[0].preset==="WoW"',arg=m.RESULT)
            assert rows(page,m.RESULT+'_daily')[0]['A_date']=='2026-10-22'
            assert rows(page,m.RESULT+'_daily')[0]['B_date']=='2026-10-15'
            page.reload(wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=m.RESULT)
            check_oracle(page,m,'WoW','iOS')
            assert 't.'+m.PERIOD+'=WoW' in page.url
            # Existing capture admission also checks collapsed table shells; reveal their native disclosures.
            section.get_by_text('All metrics CSV',exact=True).click()
            section.get_by_text('Selected calendar source facts and CSV',exact=True).click()
            ready_charts(page)
            source_table=page.locator('.fw-table-scroll:visible').last
            source_table.scroll_into_view_if_needed()
            search=source_table.locator('xpath=preceding-sibling::div[1]').locator('input.fw-table-search')
            search.fill('2026-10-22')
            page.wait_for_function('id=>document.querySelectorAll("#"+id+" tbody tr").length===1',arg=source_table.get_attribute('id'))
            check_oracle(page,m,'WoW','iOS')
            search.fill('')
            for theme in ('trellum light','trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                ready_charts(page)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                section.screenshot(path=str(EVIDENCE/f'{width}-{theme.split()[-1]}.png'))
                snapshot=section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                (EVIDENCE/f'native-section-{width}-{theme.split()[-1]}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
                (EVIDENCE/f'native-section-{width}-{theme.split()[-1]}.json').write_text(json.dumps(snapshot,indent=2))
            if width==1280:
                for ext,fn in [('png','exportPNG'),('pdf','exportPDF')]:
                    with page.expect_download(timeout=60000) as info:
                        page.evaluate(f'fw.{fn}()')
                    info.value.save_as(str(EVIDENCE/f'native-report.{ext}'))
            assert errors==[],errors
        finally:
            browser.close()


def test_period_lifecycle_replacements_and_capture_readiness(tmp_path):
    from playwright.sync_api import sync_playwright
    m=build(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            _route(page,tmp_path)
            page.goto('http://period-lifecycle.test/index.html',wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=m.RESULT)
            ready_charts(page)
            before=page.evaluate('fw.filterEngine._lifecycleListeners.size')
            for _ in range(3):
                page.evaluate('renderAll()')
            assert page.evaluate('fw.filterEngine._lifecycleListeners.size')==before
            state=page.evaluate('''async args=>{
                const e=fw.filterEngine;e.removeDataset(args.source);
                let blocked='';try{await fw.captureElementSnapshotForAnalysis(document.getElementById('sec-period-comparison'))}catch(error){blocked=error.message}
                return {blocked,rows:e.getFiltered(args.result),status:_fwPeriodComparison.status(args.result)};
            }''',{'source':m.SOURCE,'result':m.RESULT})
            assert state['blocked'] and not state['status']['ready'] and all(r['A'] is None for r in state['rows'])
            page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':m.SOURCE,'rows':m.facts().to_dict('records')})
            assert page.evaluate('id=>_fwPeriodComparison.status(id).ready',m.RESULT)
            complete=m.facts().to_dict('records')
            missing=[r for r in complete if r['date']!='2026-10-31']
            page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':m.SOURCE,'rows':missing})
            assert all(r['A'] is None and r['B'] is not None for r in rows(page,m.RESULT))
            assert rows(page,m.RESULT+'_daily')[-1]['cost_A_state']=='unavailable'
            assert page.evaluate('id=>_fwPeriodComparison.status(id).ready',m.RESULT)
            for baseline in (0,-1):
                changed=[{**r,'cost':r['cost']*baseline if r['date'].startswith('2026-09') else r['cost']} for r in complete]
                page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':m.SOURCE,'rows':changed})
                cost=rows(page,m.RESULT)[0]
                assert cost['A']==14384 and cost['change'] is None
                assert ('Zero baseline' if baseline==0 else 'Negative baseline') in cost['change_reason']
                assert rows(page,m.RESULT)[3]['A']==10788
            changed=[{**r,'revenue':'unsafe' if r['date']=='2026-10-11' else r['revenue']} for r in complete]
            page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':m.SOURCE,'rows':changed})
            assert rows(page,m.RESULT)[5]['A'] is None and rows(page,m.RESULT)[0]['A']==14384
            page.evaluate('args=>fw.filterEngine.init(args.source,args.rows)',{'source':m.SOURCE,'rows':complete})
            page.evaluate('id=>fw.filterEngine.init(id,[{A:999}])',m.RESULT)
            assert rows(page,m.RESULT)[0]['A']==14384
            # A completed empty population is available for capture, with explicit incomplete reasons.
            page.evaluate('id=>fw.filterEngine.setFilter(id,"empty","platform","in",["missing"])',m.SOURCE)
            assert page.evaluate('id=>_fwPeriodComparison.status(id).ready',m.RESULT)
            assert all(r['A'] is None for r in rows(page,m.RESULT))
            # Removed output refuses capture; explicit registrar replacement restores all outputs.
            page.evaluate('id=>fw.filterEngine.removeDataset(id)',m.RESULT+'_daily')
            assert not page.evaluate('id=>_fwPeriodComparison.status(id).ready',m.RESULT)
            page.evaluate('''()=>{const [id,c]=Object.entries(_reportData.components).find(([id,c])=>c.type==='period_comparison');_fwRenderers.period_comparison(id,c);}''')
            assert page.evaluate('id=>_fwPeriodComparison.status(id).ready',m.RESULT)
            page.locator('[data-toggle-id="'+m.PERIOD+'"]').evaluate('n=>n.remove()')
            page.wait_for_function('id=>!_fwPeriodComparison.status(id).ready',arg=m.RESULT)
            assert all(r['A'] is None for r in rows(page,m.RESULT))
            page.locator('#sec-period-comparison').evaluate('n=>n.remove()')
            page.wait_for_function('()=>fw.filterEngine._lifecycleListeners.size===0')
            assert not page.evaluate('id=>fw.filterEngine.isReady(id)',m.RESULT)
        finally:
            browser.close()


def test_native_scope_reentry_has_one_owned_projection(tmp_path):
    from playwright.sync_api import sync_playwright

    from trellum.report import ReportContext
    _, m = example_context(tmp_path)
    original=m.facts()
    ctx=ReportContext(config={'name':'Period scopes','studio':'test','category':'Test',
        'description':'Independent complete daily snapshot comparisons in native report scopes.'},slug='period-scopes',output_dir=str(tmp_path))
    scopes={}
    for scope,factor in [('period_a',1),('period_b',3)]:
        ctx.set_scope(scope,label=scope)
        m.SOURCE=scope+'_facts'
        m.RESULT=scope+'_comparison'
        # Native scope reports share one structural HTML/control shell.
        m.PERIOD='scope_period'
        m.CONTEXT='scope_context'
        frame=original.copy()
        frame['cost']*=factor
        m.facts=lambda frame=frame:frame
        m.add_sections(ctx)
        from trellum.components import ScopedDataSource
        components=ctx.scopes[scope][-1]['components']
        components.insert(1,ScopedDataSource(scope+'_subset',m.SOURCE))
        next(c for c in components if c.__class__.__name__=='PeriodComparison').parent=scope+'_subset'
        scopes[scope]=(m.RESULT,14384*factor)
    details=compute_details(ctx)
    result=validate_report(ctx,details=details)
    assert [(c.id,c.message) for c in result.checks if c.level in {'warn','fail'}]==[]
    render_report(ctx,str(tmp_path),auto_refresh=False)
    copy_vendor_tree(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            _route(page,tmp_path)
            page.goto('http://period-scopes.test/index.html',wait_until='networkidle')
            counts=[]
            for scope in ('period_b','period_a','period_b','period_a'):
                page.get_by_role('button',name=scope,exact=True).click()
                dataset,expected=scopes[scope]
                page.wait_for_function('args=>fw.filterEngine.getFiltered(args[0])[0]?.A===args[1]',arg=[dataset,expected])
                ready_charts(page)
                assert page.evaluate('id=>_fwPeriodComparison.status(id).ready',dataset)
                counts.append(page.evaluate('fw.filterEngine._lifecycleListeners.size'))
            assert len(set(counts))==1
        finally:
            browser.close()
