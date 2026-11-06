"""Native generated Marketing reports, real controls, CSV and capture evidence."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
from pathlib import Path

import pytest

from trellum.components import DataSource, MiniKpi, Panel
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_chart_header_summaries_browser import _route
from trellum.testing.test_doughnut_summaries_browser import _day
from trellum.testing.test_marketing_composition import example_context
from trellum.validation import validate_report
from trellum.validation.checks.metric_views import check_metric_views

EVIDENCE=Path(os.environ.get('TRELLUM_METRIC_VIEW_EVIDENCE','/tmp/trellum-metric-views'))
SOURCE='showcase_marketing_facts'
RESULT='showcase_marketing_geography'


def build(output):
    ctx,module=example_context(output)
    # Independent source proves lifecycle publications do not touch unrelated data.
    import pandas as pd
    ctx.add_section('Static compatibility', [DataSource('independent',pd.DataFrame({'n':[7]})),
        Panel([MiniKpi('Static supplied',1234.5,'currency',stacked=True)])])
    from trellum.validation.checks.metric_views import check_column_formats
    from trellum.validation.walk import _all_components
    details=compute_details(ctx)
    validation=validate_report(ctx,details=details)
    check_metric_views(ctx,validation)
    check_column_formats(_all_components(ctx),validation)
    issues=[(c.id,c.level,c.component,c.section,c.message) for c in validation.checks if c.level in {'warn','fail'}]
    assert issues==[],issues
    render_report(ctx,str(output),auto_refresh=False)
    copy_vendor_tree(output)
    delivered=json.loads((output/'data.json').read_text())
    assert len(delivered['_ds_'+SOURCE]['_data'])==16
    assert '_ds_'+RESULT not in delivered
    assert all('rows' not in c for c in delivered['components'].values() if c['type']=='metric_view')
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    (EVIDENCE/'diagnostics.json').write_text(json.dumps({'validation':validation.to_dict(),'details':details},indent=2))
    return module


def rows(page):
    return page.evaluate('id=>fw.filterEngine.getFiltered(id)',RESULT)


def minis(page,group='worldwide'):
    return page.evaluate('''group=>{
        const out={};Object.entries(_reportData.components).forEach(([id,c])=>{
            if(c.binding&&c.binding.group===group) out[c.binding.metric]=document.getElementById(id).querySelector('.fw-mini-val')?.textContent;
        });return out;
    }''',group)


def visible_table(page):
    return page.locator('.fw-data-table:visible').first


def download_csv(page,table,path):
    with page.expect_download() as info:
        table.locator('xpath=preceding-sibling::div[1]').get_by_title('Download CSV').click()
    info.value.save_as(str(path))
    return list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))


def wait_panels(page):
    page.locator('[data-fw-kind="MiniKpi"]').first.scroll_into_view_if_needed()
    page.wait_for_function("""()=>Object.entries(_reportData.components).filter(([id,c])=>c.binding).every(([id])=>!!document.querySelector('#'+id+' .fw-mini-val'))""")


@pytest.mark.parametrize('width',[1280,pytest.param(390,marks=pytest.mark.heavy)])
def test_native_marketing_controls_parity_csv_capture_and_replacement(tmp_path,width):
    from playwright.sync_api import sync_playwright
    module=build(tmp_path)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
            page.set_default_timeout(10000)
            errors=[]
            page.on('pageerror',lambda exc:errors.append(str(exc)))
            page.on('console',lambda msg:errors.append(msg.text) if msg.type=='error' else None)
            _route(page,tmp_path)
            page.goto('http://metric-views.test/index.html',wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=RESULT)
            page.evaluate('document.fonts.ready')
            assert page.get_by_role('tab',name='Summary table',exact=True).get_attribute('aria-selected')=='true'
            assert [r['installs'] for r in rows(page)]==[2520,2160,900,720]
            summary=page.locator('[data-fw-kind="DataTable"]').first
            summary.scroll_into_view_if_needed()
            page.wait_for_function('id=>!!document.querySelector("#"+id+" tbody tr")',arg=summary.get_attribute('id'))
            if width==390:
                local=summary.evaluate('el=>({width:el.clientWidth,scroll:el.scrollWidth})')
                assert local['scroll']>local['width']
                summary.evaluate('el=>el.scrollLeft=el.scrollWidth')
                assert summary.evaluate('el=>el.scrollLeft')>0
                summary.evaluate('el=>el.scrollLeft=0')
            parsed=download_csv(page,summary,EVIDENCE/f'summary-{width}.csv')
            assert [r['group_label'] for r in parsed]==['Worldwide','Tier 1','US','UK']
            assert [float(r['installs']) for r in parsed]==[2520,2160,900,720]
            first_cells=summary.locator('tbody tr').first.locator('td').all_text_contents()
            assert first_cells[2]=='$4,320.00' and first_cells[6]=='$1.71' and first_cells[7]=='108.75%'
            assert float(parsed[0]['cpi'])==pytest.approx(12/7)
            assert float(parsed[0]['d30_recovery'])==pytest.approx(1.0875)
            summary.locator('thead th').nth(1).click()
            summary.locator('xpath=preceding-sibling::div[1]').locator('input.fw-table-search').fill('UK')
            page.wait_for_function('id=>document.querySelectorAll("#"+id+" tbody tr").length===1',arg=summary.get_attribute('id'))
            parsed=download_csv(page,summary,EVIDENCE/f'searched-{width}.csv')
            assert len(parsed)==1 and parsed[0]['group_label']=='UK' and float(parsed[0]['installs'])==720
            assert rows(page)[0]['installs']==2520
            page.get_by_role('tab',name='Geography panels',exact=True).click()
            wait_panels(page)
            assert minis(page)['installs']=='2,520' and minis(page)['d30_recovery']=='108.75%'
            assert minis(page,'uk')['installs']=='720'
            section=page.locator('.fw-section[data-fw-section-title="Marketing geography"]')
            if not section.count():
                section=page.locator('.fw-section').filter(has=page.get_by_role('tab',name='Geography panels',exact=True)).first
            section.get_by_role('button',name='iOS',exact=True).click()
            _day(section,'2026-08-26')
            assert rows(page)[0]['installs']==560 and rows(page)[0]['cost']==960 and rows(page)[0]['d30_revenue']==1044
            assert rows(page)[0]['cpi']==pytest.approx(12/7)
            assert minis(page)['installs']=='560'
            country=section.locator('select[data-filter-col="country"]').locator('xpath=..')
            country.locator('.ss-main').click()
            page.locator('.ss-content:visible .ss-option').get_by_text('US',exact=True).click()
            page.wait_for_function('id=>fw.filterEngine.getFiltered(id)[0].installs===200',arg=RESULT)
            assert [r['installs'] for r in rows(page)]==[200,200,200,None]
            assert minis(page,'uk')['installs']=='—'
            country.get_by_text('Select all',exact=True).click()
            page.wait_for_function('id=>fw.filterEngine.getFiltered(id)[0].installs===560',arg=RESULT)
            assert rows(page)[0]['installs']==560
            # Both views consume identical typed result rows; table-local search never changes panels.
            page.get_by_role('tab',name='Summary table',exact=True).click()
            summary.locator('xpath=preceding-sibling::div[1]').locator('input.fw-table-search').fill('')
            page.wait_for_function('id=>document.querySelectorAll("#"+id+" tbody tr").length===4',arg=summary.get_attribute('id'))
            filtered=download_csv(page,summary,EVIDENCE/f'filtered-{width}.csv')
            assert sorted(float(r['installs']) for r in filtered)==[160,200,480,560]
            page.get_by_role('tab',name='Geography panels',exact=True).click()
            wait_panels(page)
            # Real zeros, incomplete sibling and zero denominator, through real source replacement.
            original=module.facts().to_dict('records')
            for replacement,expected_cost,expected_cpi in [
                ([{'country':'GB','cost':0,'installs':2,'d7_revenue':0,'d30_revenue':0,'d7_retained':0,'platform':'iOS','cohort_date':'2026-08-26'}],0,0),
                ([{'country':'GB','cost':3,'installs':0,'d7_revenue':None,'d30_revenue':6,'d7_retained':0,'platform':'iOS','cohort_date':'2026-08-26'}],3,None),
            ]:
                page.evaluate('args=>fw.filterEngine.init(args.id,args.rows)',{'id':SOURCE,'rows':replacement})
                assert rows(page)[0]['cost']==expected_cost and rows(page)[0]['cpi']==expected_cpi
                assert rows(page)[2]['installs'] is None and rows(page)[2]['facts']==0
                if expected_cpi is None:
                    assert rows(page)[0]['d7_revenue'] is None and rows(page)[0]['d30_revenue']==6
                    assert minis(page)['cpi']=='—'
                    assert page.locator('.fw-mini-binding-reason:visible').filter(has_text='Zero denominator').count()>=1
            page.evaluate('args=>fw.filterEngine.init(args.id,args.rows)',{'id':SOURCE,'rows':original})
            assert rows(page)[0]['installs']==560
            # No ordinary notify is needed after replacement. Removal invalidates, re-entry reattaches.
            page.evaluate('id=>fw.filterEngine.removeDataset(id)',SOURCE)
            assert rows(page)[0]['installs'] is None and minis(page)['installs']=='—'
            page.evaluate('args=>fw.filterEngine.init(args.id,args.rows)',{'id':SOURCE,'rows':original})
            assert rows(page)[0]['installs']==2520 # remove deliberately cleared native source filters
            assert page.evaluate("fw.filterEngine.getFiltered('independent')")==[{'n':7}]
            # Restore a visible native filter state before reload/theme/capture.
            section.get_by_role('button',name='Android',exact=True).click()
            assert rows(page)[0]['installs']==1680
            _day(section,'2026-08-26')
            assert rows(page)[0]['installs']==1120
            page.evaluate('renderAll();renderAll()')
            wait_panels(page)
            # Native controls retain their date value on rerender (day 2).
            assert rows(page)[0]['installs']==1120
            for theme in ('trellum light','trellum dark'):
                page.locator('#fwThemeSelect').select_option(theme)
                wait_panels(page)
                assert minis(page)['installs']=='1,120'
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                section.screenshot(path=str(EVIDENCE/f'{width}-{theme.split()[-1]}-panels.png'))
            # A shared collapsed native table exposes the matching summary CSV in panels too.
            section.get_by_text('Summary rows and CSV',exact=True).click()
            panel_table=section.locator('.fw-table-scroll').nth(1)
            panel_table.scroll_into_view_if_needed()
            page.wait_for_function('id=>!!document.querySelector("#"+id+" tbody tr")',arg=panel_table.get_attribute('id'))
            assert len(download_csv(page,panel_table,EVIDENCE/f'panels-{width}.csv'))==4
            section.get_by_text('Selected source facts and CSV',exact=True).click()
            source_table=section.locator('.fw-table-scroll').nth(2)
            source_table.scroll_into_view_if_needed()
            page.wait_for_function('id=>!!document.querySelector("#"+id+" tbody tr")',arg=source_table.get_attribute('id'))
            source_csv=download_csv(page,source_table,EVIDENCE/f'source-facts-{width}.csv')
            assert len(source_csv)==4 and sum(float(r['installs']) for r in source_csv)==1120
            snapshot=section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            (EVIDENCE/f'native-section-panels-{width}.json').write_text(json.dumps(snapshot,indent=2))
            (EVIDENCE/f'native-section-panels-{width}.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
            if width==1280:
                for ext,fn in [('png','exportPNG'),('pdf','exportPDF')]:
                    with page.expect_download(timeout=60000) as info:
                        page.evaluate(f'fw.{fn}()')
                    info.value.save_as(str(EVIDENCE/f'native-report-panels.{ext}'))
                page.get_by_role('tab',name='Summary table',exact=True).click()
                snapshot=section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
                (EVIDENCE/'native-section-table.png').write_bytes(base64.b64decode(snapshot['image']['data_base64']))
                for ext,fn in [('png','exportPNG'),('pdf','exportPDF')]:
                    with page.expect_download(timeout=60000) as info:
                        page.evaluate(f'fw.{fn}()')
                    info.value.save_as(str(EVIDENCE/f'native-report-table.{ext}'))
            page.reload(wait_until='networkidle')
            page.wait_for_function('id=>fw.filterEngine.isReady(id)',arg=RESULT)
            assert rows(page)[0]['installs']==1120
            assert errors==[],errors
        finally:
            browser.close()
