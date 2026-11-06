"""Real native reports, vendored charts, original-unit reducers and exports."""
from __future__ import annotations

import base64
from contextlib import ExitStack
import csv
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from trellum.assets import load_js
from trellum.components import DataSource, FilterBar, Grid, StackedBar, Toggle
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.testing.test_stacked_summaries import BASELINE, ROOT, examples, module, validate
from trellum.testing.test_traffic_composition_browser import browser, open_report, state, capture


def build(output, mode='on', scoped=False):
    output.mkdir(parents=True, exist_ok=True)
    ctx = ReportContext(config={'name': 'Stacked summaries', 'studio': 'test', 'category': 'Components',
        'description': 'Complete synthetic delivered additive event facts by date, platform and channel; original-unit selected bar summaries.'},
        slug='stacked-summaries', output_dir=str(output))
    teaching = examples()
    if mode != 'on':
        cls = StackedBar
        if mode == 'baseline':
            source = output / 'baseline_bar.py'
            source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
            cls = module(source, 'stacked_native_baseline').StackedBar
        def factory(*args, **kwargs):
            kwargs.pop('header_summary', None)
            return cls(*args, **kwargs)
        teaching.StackedBar = factory
    if scoped:
        for scope in ('First', 'Second'):
            ctx.set_scope(scope)
            ds = 'stacked_scope_' + scope.lower(); frame = teaching.facts()
            ctx.add_section(scope, [DataSource(ds, frame), FilterBar(ds, frame, filters=[{'column': 'platform', 'type': 'toggle'}]),
                Grid(columns=2, children=[StackedBar(frame, 'day', ['events'], scope + series, dataset_id=ds,
                    stack_by='channel', value_format='number', stack_by_options=teaching.BREAKDOWNS,
                    normalize_toggle=True, header_summary=teaching.summary(series=series)) for series in ('UA', 'RT')])])
    else:
        teaching.add_sections(ctx)
    details, validation = validate(ctx)
    (output / 'diagnostics.json').write_text(json.dumps(details, indent=2, default=str))
    (output / 'validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
    with ExitStack() as patches:
        if mode == 'baseline':
            js = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/components/chart_base.js'], cwd=ROOT, text=True)
            patches.enter_context(patch('trellum.components.charts.base.load_js', lambda name: js if name == 'components/chart_base.js' else load_js(name)))
        render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output); ctx.close_connections()


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_STACKED_SUMMARY_EVIDENCE', str(tmp_path_factory.mktemp('stacked-summaries'))))
    for mode in ('on', 'off', 'baseline'):
        build(root / mode, mode)
    build(root / 'scoped', scoped=True)
    return root


def canvas(page, title):
    return page.locator('canvas[data-fw-title="' + title + '"]')


def box(target):
    return target.page.locator('[data-chart-summary="' + target.get_attribute('id') + '"]')


def value(target):
    state(target)
    return box(target).locator('.fw-chart-summary-value').text_content()


def region(page, index):
    return page.locator('.fw-section').filter(has=page.get_by_role('heading', name=examples().TITLES[index], exact=True)).first


def csv_download(target, path):
    cid = target.get_attribute('id'); page = target.page
    with page.expect_download() as download:
        page.locator('[data-csv-chart="' + cid + '"]').click(force=True)
    download.value.save_as(path)
    rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
    actual = state(target)
    assert rows[0] == ['label'] + [d['label'] for d in actual['datasets']]
    assert [row[0] for row in rows[1:]] == actual['labels']
    for i, row in enumerate(rows[1:]):
        assert list(map(float, row[1:])) == pytest.approx([d['data'][i] for d in actual['datasets']])
    return rows


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_baseline_pixels_geometry_payload_modes_csv_and_captures(reports, browser, width):
    pages = []
    try:
        for mode in ('baseline', 'off', 'on'):
            page, errors, failed = open_report(browser, reports / mode, width=width)
            pages.append((page, errors, failed))
        baseline, off, on = [entry[0] for entry in pages]
        # Actual checkpoint Python and JS, not a same-runtime opt-out surrogate.
        payloads = [json.loads((reports / mode / 'data.json').read_text()) for mode in ('baseline', 'off')]
        for payload in payloads: payload.pop('_freshness', None)
        assert payloads[0] == payloads[1]
        for theme in ('trellum light', 'trellum dark'):
            for page, _, _ in pages:
                page.locator('#fwThemeSelect').select_option(theme)
            for index in (0, 2):
                a = capture(region(baseline, index), reports / f'baseline-{index}-{width}-{theme.split()[-1]}.png')
                b = capture(region(off, index), reports / f'off-{index}-{width}-{theme.split()[-1]}.png')
                assert a == b
                capture(region(on, index), reports / f'on-{index}-{width}-{theme.split()[-1]}.png')
                region(on, index).screenshot(path=str(reports / f'desktop-phone-{index}-{width}-{theme.split()[-1]}.png'))
            for title in ('Local UA', 'Shared UA', 'Shared RT', 'UA sum'):
                assert state(canvas(baseline, title)) == state(canvas(off, title)) == state(canvas(on, title))
            assert on.locator('html').evaluate('el=>el.scrollWidth <= window.innerWidth')
        assert [value(canvas(on, 'UA ' + stat)) for stat in ('sum', 'mean', 'min', 'max', 'last')] == ['90', '45', '30', '60', '60']
        assert value(canvas(on, 'UA grouped away')) == '—'
        assert value(canvas(on, 'Exact Other')) == '90'
        for title in ('Eligibility null', 'Eligibility string', 'Ambiguous UA'):
            assert value(canvas(on, title)) == '—'
            assert box(canvas(on, title)).locator('.fw-stacked-summary-reason').is_visible()
        assert float(value(canvas(on, 'Eligibility zero'))) == 0
        assert value(canvas(on, 'Eligibility signed')) == '90'
        assert value(canvas(on, 'Fractional rate')) == '4.50%'
        for mode in ('Percentage', 'Absolute'):
            downloads = []
            for name, (page, _, _) in zip(('baseline', 'off', 'on'), pages):
                region(page, 2).locator('[data-normalize-toggle]').get_by_role('button', name=mode, exact=True).click()
                downloads.append(csv_download(canvas(page, 'Local UA'), reports / f'{name}-{width}-{mode}.csv'))
            assert downloads[0] == downloads[1] == downloads[2]
            assert value(canvas(on, 'Local UA')) == '90'
            assert 'Original units' in box(canvas(on, 'Local UA')).inner_text()
        # Native full-section and full-report export paths include the header.
        capture(region(on, 2), reports / f'native-section-{width}.png')
        for method, extension in (('exportPNG', 'png'), ('exportPDF', 'pdf')):
            with on.expect_download(timeout=60000) as download:
                on.evaluate('method=>fw[method]()', method)
            download.value.save_as(reports / f'native-report-{width}.{extension}')
        raw = canvas(on, 'Local UA').evaluate('el=>el.toDataURL().split(",")[1]')
        (reports / f'raw-canvas-{width}.png').write_bytes(base64.b64decode(raw))
        for _, errors, failed in pages:
            assert not errors + failed
    finally:
        for page, _, _ in pages: page.close()


def test_native_filters_url_local_shared_hidden_series_and_scope(reports, browser):
    query = '?t.stacked_summary_controls_mode=Percentage'
    page, errors, failed = open_report(browser, reports / 'on', query=query)
    try:
        ua, rt = canvas(page, 'Shared UA'), canvas(page, 'Shared RT')
        assert value(ua) == '90' and value(rt) == '180'
        assert state(ua)['datasets'][0]['abs'] is not None
        page.evaluate('id=>{const c=window._chartInstances[id];c.setDatasetVisibility(c.data.datasets.findIndex(d=>d.label==="UA"),false);c.update();c.zoom(1.2)}', ua.get_attribute('id'))
        assert value(ua) == '90'
        assert all(d['visible'] for d in state(rt)['datasets'])
        controls = region(page, 2)
        controls.locator('[data-toggle-id="stacked_summary_controls_split"]').get_by_role('button', name='Paid/Owned', exact=True).click()
        assert value(ua) == value(rt) == '—'
        assert value(canvas(page, 'Local UA')) == '90'
        controls.locator('[data-toggle-id="stacked_summary_controls_split"]').get_by_role('button', name='Channel', exact=True).click()
        assert value(ua) == '90'
        controls.get_by_role('button', name='iOS', exact=True).click()
        controls.locator('.fw-date-trigger').click()
        controls.locator('input[data-edge="max"]').fill('2026-08-01')
        controls.locator('input[data-edge="max"]').press('Tab'); page.keyboard.press('Escape')
        assert value(ua) == '10'
        assert value(canvas(page, 'UA sum')) == '90'  # Independent section.
        for title in ('Eligibility null', 'Eligibility string'):
            assert value(canvas(page, title)) == '—'
        page.evaluate('''()=>{
            for (const ds of ['stacked_summary_eligibility_null','stacked_summary_eligibility_string'])
                fw.filterEngine.setFilter(ds,'valid-day','day','equals','2026-08-02');
        }''')
        assert value(canvas(page, 'Eligibility null')) == value(canvas(page, 'Eligibility string')) == '60'
        assert box(canvas(page, 'Eligibility null')).locator('.fw-stacked-summary-reason').count() == 0
        assert not errors + failed
    finally: page.close()
    page, errors, failed = open_report(browser, reports / 'scoped')
    try:
        assert value(canvas(page, 'FirstUA')) == '90'
        for label in ('Second', 'First', 'Second', 'First'):
            page.locator('[data-toggle-id="__scope__"]').get_by_role('button', name=label, exact=True).click()
            # Native scopes deliberately reuse the default scope's DOM slots/title metadata.
            page.wait_for_function('scope=>!!window._scopeCache[scope]', arg=label)
            assert value(canvas(page, 'FirstUA')) == '90'
        cid = canvas(page, 'FirstUA').get_attribute('id')
        assert page.evaluate('id=>(fw.filterEngine._subscribers.stacked_scope_first||[]).filter(s=>s.id===id).length', cid) == 1
        assert not errors + failed
    finally: page.close()


def test_real_js_reducer_precision_eligibility_and_exact_identity(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        result = page.evaluate('''()=>{
            const s={stat:'sum',scope:'absolute_series',series:'UA'};
            const reduce=(values,stat='sum',extra=[])=>_fwStackSummaryResult({...s,stat},[{label:'UA',data:values},...extra],'');
            return {
                stats:['sum','mean','min','max','last'].map(stat=>reduce([30,60],stat).value),
                zero:reduce([0,0]).value, signed:reduce([-5,2]).value,
                cancellation:reduce([1e15,1,-1e15]).value,
                cancellationMean:reduce([1e15,1,-1e15],'mean').value,
                tiny:reduce([1e15,.001,-1e15]).value,
                invalid:[null,true,'3',NaN,Infinity,9007199254740992].map(v=>reduce([1,v]).reason),
                overflow:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER]).reason,
                mean:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER],'mean').value,
                max:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER],'max').value,
                empty:reduce([]).reason, last:reduce([60,null],'last').reason,
                normalized:_fwStackSummaryResult(s,[{label:'UA',data:[10,10],_absData:[30,60]}],'').value,
                line:reduce([30,60],'sum',[{type:'line',label:'UA',data:[Infinity]}]).value,
                right:reduce([30,60],'sum',[{yAxisID:'y1',label:'UA',data:[Infinity]}]).value,
                duplicate:reduce([30,60],'sum',[{label:'UA',data:[1]}]).reason,
                missing:_fwStackSummaryResult({...s,series:'Other (2)'},[{label:'Other (1)',data:[90]}],'').reason,
                zerosMean:reduce([0,30,60],'mean').value,
                raw:_fwStackSummaryInputReason({value_cols:['events'],line_cols:['line']},[{events:3,line:null}]),
                rawOther:_fwStackSummaryInputReason({value_cols:['events','other']},[{events:3,other:'2'}])
            };
        }''')
        assert result['stats'] == [90, 45, 30, 60, 60]
        assert result['zero'] == 0 and result['signed'] == -3
        assert result['cancellation'] == 1
        assert result['cancellationMean'] == pytest.approx(1 / 3)
        assert result['tiny'] == .001
        assert all(result['invalid']) and result['overflow'] and result['empty'] and result['last']
        assert result['mean'] == result['max'] == 2 ** 53 - 1
        assert result['normalized'] == result['line'] == result['right'] == 90
        assert result['duplicate'] and result['missing'] and result['rawOther']
        assert result['zerosMean'] == 30 and result['raw'] == ''
        assert not errors + failed
    finally: page.close()


def test_native_source_options_canvas_control_replacement_and_disposal(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        target = canvas(page, 'Local UA'); cid = target.get_attribute('id'); ds = 'stacked_summary_controls'
        args = {'id': cid, 'ds': ds}
        page.evaluate('''a=>{
            window.summaryCfg=window._reportData.components[a.id];
            window.summaryRows=fw.filterEngine.getFiltered(a.ds).map(r=>({...r}));
            for(let i=0;i<5;i++) window._fwRenderers.chart(a.id,summaryCfg);
            fw.filterEngine.init(a.ds,summaryRows.map(r=>({...r,events:r.events*3})));
        }''', args)
        assert value(target) == '270'
        page.evaluate('a=>fw.filterEngine.removeDataset(a.ds)', args)
        assert value(target) == '—'
        page.evaluate('a=>fw.filterEngine.init(a.ds,summaryRows)', args)
        assert value(target) == '90'
        page.evaluate('''a=>{
            summaryCfg.stacked_summary.stat='mean';
            fw.filterEngine.init(a.ds,summaryRows.filter(r=>!(r.channel==='UA' && r.day==='2026-08-02')));
        }''', args)
        assert value(target) == '15'  # Existing plotted missing-category zero-fill counts.
        page.evaluate('a=>{summaryCfg.stacked_summary.stat="sum";fw.filterEngine.init(a.ds,summaryRows)}', args)
        assert page.evaluate('a=>(fw.filterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length', args) == 1
        page.evaluate('''a=>{
            const old=document.querySelector('[data-normalize-toggle="'+a.id+'"]');
            old.replaceWith(old.cloneNode(true));window._fwRenderers.chart(a.id,summaryCfg);
        }''', args)
        region(page, 2).locator('[data-normalize-toggle]').get_by_role('button', name='Percentage', exact=True).click()
        assert value(target) == '90' and state(target)['datasets'][0]['abs'] is not None
        page.evaluate('''a=>{
            delete summaryCfg.stacked_summary;window._fwRenderers.chart(a.id,summaryCfg);
        }''', args)
        assert box(target).count() == 0
        assert 'stacked-summary' not in (target.get_attribute('aria-describedby') or '')
        page.evaluate('''a=>{
            summaryCfg.stacked_summary={stat:'last',scope:'absolute_series',series:'UA',label:'<img src=x onerror=alert(1)>',format:'plain'};
            window._fwRenderers.chart(a.id,summaryCfg);
        }''', args)
        assert value(target) == '60'
        assert box(target).locator('img').count() == 0
        assert box(target).locator('.fw-chart-summary-label').text_content().startswith('<img')
        page.evaluate('''a=>{
            const el=document.getElementById(a.id);el.replaceWith(el.cloneNode(true));
            window._fwRenderers.chart(a.id,summaryCfg);
        }''', args)
        target = page.locator('#' + cid)
        assert value(target) == '60'
        assert page.locator('#' + cid + '-stacked-summary-scope').count() == 1
        assert page.evaluate('a=>(fw.filterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length', args) == 1
        page.evaluate('a=>document.getElementById(a.id).remove()', args)
        page.wait_for_function('a=>(fw.filterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length===0', arg=args)
        assert page.locator('#' + cid + '-stacked-summary-scope').count() == 0
        assert not errors + failed
    finally: page.close()


def test_native_static_adoption_accessibility_and_owned_cleanup(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        target = canvas(page, 'Static on'); cid = target.get_attribute('id')
        assert value(target) == '90'
        # A static chart keeps its legacy omitted title in either mode.
        assert region(page, 0).locator('.fw-chart-title').all_text_contents() == ['Static section live companion']
        args = {'id': cid}
        page.evaluate('''a=>{
            const el=document.getElementById(a.id); el.replaceWith(el.cloneNode(true));
            window._fwRenderers.chart(a.id,window._reportData.components[a.id]);
        }''', args)
        target = page.locator('#' + cid)
        assert value(target) == '90'
        assert page.locator('#' + cid + '-stacked-summary-scope').count() == 1
        page.evaluate('a=>document.getElementById(a.id).remove()', args)
        page.wait_for_function('id=>!document.getElementById(id+"-stacked-summary-scope")', arg=cid)
        # Owned context tokens remain independent of the summary token.
        shared = canvas(page, 'Shared UA'); sid = shared.get_attribute('id')
        assert shared.get_attribute('aria-describedby').split() == [sid + '-header-context', sid + '-stacked-summary-scope']
        page.evaluate('''id=>{
            const cfg=window._reportData.components[id];delete cfg.stacked_summary;window._fwRenderers.chart(id,cfg);
        }''', sid)
        assert shared.get_attribute('aria-describedby') == sid + '-header-context'
        # Adoption on a chart born opt-out uses only its existing owned handlers.
        local = canvas(page, 'Local UA'); lid = local.get_attribute('id')
        page.evaluate('''id=>{
            window.summaryDrawCount=0;const create=window._createChart;
            window._createChart=function(...args){if(args[0]===id)window.summaryDrawCount++;return create(...args)};
            const cfg=window._reportData.components[id];const saved=cfg.stacked_summary;
            delete cfg.stacked_summary;window._fwRenderers.chart(id,cfg);
            cfg.stacked_summary=saved;window._fwRenderers.chart(id,cfg);
            window.summaryDrawCount=0;window.dispatchEvent(new CustomEvent('fw-theme-change'));
        }''', lid)
        assert page.evaluate('window.summaryDrawCount') == 1
        page.evaluate('window.summaryDrawCount=0')
        region(page, 2).locator('[data-normalize-toggle]').get_by_role('button', name='Percentage', exact=True).click()
        assert page.evaluate('window.summaryDrawCount') == 1
        assert value(local) == '90'
        page.evaluate('''id=>{
            window.summaryZoomResetCount=0;window._chartInstances[id].resetZoom=()=>window.summaryZoomResetCount++;
        }''', lid)
        local.dispatch_event('dblclick')
        assert page.evaluate('window.summaryZoomResetCount') == 1
        # A changing Other tail is unavailable rather than a fallback to an index.
        other = canvas(page, 'Exact Other'); oid = other.get_attribute('id')
        page.evaluate('''id=>{
            const cfg=window._reportData.components[id];cfg.max_stacks=1;window._fwRenderers.chart(id,cfg);
        }''', oid)
        assert value(other) == '—'
        assert not errors + failed
    finally: page.close()
