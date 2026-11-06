"""Genuine generated Combo reports, vendored runtime, actual CSV and captures."""
from __future__ import annotations

import base64
import csv
import io
import json
import math
import os
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from trellum.assets import load_js
from trellum.components import ComboChart, DataSource, FilterBar
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.testing.test_combo_summaries import BASELINE, ROOT, context, examples, module, validate
from trellum.testing.test_traffic_composition_browser import browser as browser
from trellum.testing.test_traffic_composition_browser import capture, open_report, state


def build(output, mode='on', scoped=False):
    output.mkdir(parents=True, exist_ok=True)
    teaching = examples()
    cls = ComboChart
    if mode == 'baseline':
        source = output / 'baseline_bar.py'
        source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
        cls = module(source, 'combo_native_baseline').ComboChart
    if mode != 'on':
        def factory(*args, **kwargs):
            kwargs.pop('header_summary', None)
            return cls(*args, **kwargs)
        teaching.ComboChart = factory
    ctx = context(output)
    ctx.sections.clear()
    if scoped:
        for scope in ('First', 'Second'):
            ctx.set_scope(scope)
            frame = teaching.facts()
            ds = 'combo_scope_' + scope.lower()
            ctx.add_section(scope, [DataSource(ds, frame),
                FilterBar(ds, frame, filters=[{'column': 'platform', 'type': 'toggle'}]),
                teaching.live(frame, ds, scope + ' combo', header_summary=teaching.summary())])
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
    copy_vendor_tree(output)
    ctx.close_connections()


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_COMBO_SUMMARY_EVIDENCE', str(tmp_path_factory.mktemp('combo-summaries'))))
    for mode in ('on', 'off', 'baseline'):
        build(root / mode, mode)
    build(root / 'scoped', scoped=True)
    return root


def canvas(page, title):
    return page.locator('canvas[data-fw-title="' + title + '"]')


def box(target):
    return target.page.locator('[data-combo-summary="' + target.get_attribute('id') + '"]')


def value(target):
    state(target)
    return box(target).locator('.fw-combo-summary-value').text_content()


def region(page, index):
    return page.locator('.fw-section').filter(has=page.get_by_role('heading', name=examples().TITLES[index], exact=True)).first


def csv_download(target, path):
    cid = target.get_attribute('id')
    page = target.page
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
def test_native_historical_off_pixels_plot_geometry_formats_csv_and_captures(reports, browser, width):
    pages = []
    try:
        for mode in ('baseline', 'off', 'on'):
            pages.append(open_report(browser, reports / mode, width=width))
        baseline, off, on = [entry[0] for entry in pages]
        payloads = [json.loads((reports / mode / 'data.json').read_text()) for mode in ('baseline', 'off')]
        for payload in payloads:
            payload.pop('_freshness', None)
        assert payloads[0] == payloads[1]
        for theme in ('trellum light', 'trellum dark'):
            for page, _, _ in pages:
                page.locator('#fwThemeSelect').select_option(theme)
            for index in (0, 2):
                a = capture(region(baseline, index), reports / f'baseline-{index}-{width}-{theme.split()[-1]}.png')
                b = capture(region(off, index), reports / f'off-{index}-{width}-{theme.split()[-1]}.png')
                assert a == b
                capture(region(on, index), reports / f'on-{index}-{width}-{theme.split()[-1]}.png')
                # A viewport taller than this section keeps the report's real
                # sticky bars from obscuring the first chart in an element shot.
                height = math.ceil(region(on, index).bounding_box()['height']) + 800
                on.set_viewport_size({'width': width, 'height': max(1000, height)})
                region(on, index).screenshot(path=str(reports / f'view-{index}-{width}-{theme.split()[-1]}.png'))
                on.set_viewport_size({'width': width, 'height': 1000})
            for title in ('Static on', 'Spend sum', 'Orders mean', 'Context left', 'Context right', 'Reused source'):
                targets = [canvas(page, title) for page in (baseline, off, on)]
                assert state(targets[0]) == state(targets[1]) == state(targets[2])
                # Canvas-only bitmap also proves the opt-in header leaves plot pixels intact.
                pixels = [t.evaluate('el=>el.toDataURL()') for t in targets]
                assert pixels[0] == pixels[1] == pixels[2]
            assert on.locator('html').evaluate('el=>el.scrollWidth <= window.innerWidth')
        assert [value(canvas(on, 'Spend ' + stat)) for stat in ('sum', 'mean', 'min', 'max', 'last')] == ['$90.00', '$45.00', '$30.00', '$60.00', '$60.00']
        assert value(canvas(on, 'Orders sum')) == '27' and value(canvas(on, 'Orders mean')) == '14'  # Actual compact-number rounding.
        assert value(canvas(on, 'Static on')) == '30'
        assert value(canvas(on, 'Static right actual currency')) == '$13.50'
        assert value(canvas(on, 'Fees sum')) == '$18.00' and value(canvas(on, 'Visits sum')) == '180'
        assert value(canvas(on, 'Eligibility zero')) == '$0.00'
        assert value(canvas(on, 'Eligibility signed')) == '$30.00'
        assert value(canvas(on, 'Unselected invalid')) == '27'
        assert value(canvas(on, 'Fractional orders')) == '13.50%'
        for title in ('Eligibility missing', 'Eligibility string', 'Eligibility bool', 'Eligibility unsafe', 'Same-axis duplicate', 'Missing caption'):
            assert value(canvas(on, title)) == '—'
            assert box(canvas(on, title)).locator('.fw-combo-summary-reason').is_visible()
        assert value(canvas(on, 'Reused source')) == '$180.00'
        for title in ('Context left', 'Reused source', 'Static on', 'Same-axis duplicate'):
            downloads = [csv_download(canvas(page, title), reports / f'{mode}-{width}-{title.replace(" ", "-")}.csv')
                         for mode, (page, _, _) in zip(('baseline', 'off', 'on'), pages)]
            assert downloads[0] == downloads[1] == downloads[2]
        # Native section/report capture includes all DOM, while raw canvas excludes the header.
        capture(region(on, 2), reports / f'native-section-{width}.png')
        for method, extension in (('exportPNG', 'png'), ('exportPDF', 'pdf')):
            with on.expect_download(timeout=60000) as download:
                on.evaluate('method=>fw[method]()', method)
            download.value.save_as(reports / f'native-report-{width}.{extension}')
        raw = canvas(on, 'Context left').evaluate('el=>el.toDataURL().split(",")[1]')
        (reports / f'raw-canvas-{width}.png').write_bytes(base64.b64decode(raw))
        subprocess.run(['pdftoppm', '-f', '1', '-singlefile', '-scale-to', '1600', '-png',
            str(reports / f'native-report-{width}.pdf'), str(reports / f'native-pdf-first-{width}')], check=True, capture_output=True)
        for _, errors, failed in pages:
            assert not errors + failed
    finally:
        for page, _, _ in pages:
            page.close()


def test_native_independent_filters_url_nearest_legend_zoom_and_scope(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        left, right = canvas(page, 'Context left'), canvas(page, 'Context right')
        assert state(left)['height'] == state(right)['height'] == 220
        cid = left.get_attribute('id')
        assert page.evaluate('id=>window._chartInstances[id].options.interaction.mode', cid) == 'nearest'
        legend = page.locator('#' + cid + '-combo-context')
        legend.locator('[data-axis="y"]').get_by_role('button', name='Daily total', exact=True).click()
        left.evaluate('el=>{const c=window._chartInstances[el.id];c.zoom(1.2);c.update()}')
        assert value(left) == '$90.00' and value(right) == '27'
        assert state(left)['datasets'][0]['visible'] is False and state(left)['datasets'][2]['visible'] is True
        assert all(d['visible'] for d in state(right)['datasets'])
        # Actual nearest-series tooltip callback still inspects the selected line.
        left.evaluate('el=>window._chartInstances[el.id].resetZoom()')
        point = left.evaluate('el=>{const p=window._chartInstances[el.id].getDatasetMeta(2).data[0];return {x:p.x,y:p.y}}')
        left.hover(position=point)
        actual = left.evaluate('el=>window._chartInstances[el.id].tooltip.body.map(b=>b.lines).flat()')
        assert actual == ['Daily total: 9']
        controls = region(page, 2)
        controls.get_by_role('button', name='iOS', exact=True).click()
        controls.locator('.fw-date-trigger').click()
        controls.locator('input[data-edge="max"]').fill('2026-08-01')
        controls.locator('input[data-edge="max"]').press('Tab')
        page.keyboard.press('Escape')
        assert value(left) == '$10.00' and value(right) == '3'
        assert value(canvas(page, 'Spend sum')) == '$90.00'
        # Incoming scoped URLs are supported. Existing global date-preset
        # serialization can rewrite custom ranges to another bar's All preset.
        url = '?combo_summary_context.platform=iOS&combo_summary_context.day=2026-08-01..2026-08-01'
        for title in ('Eligibility missing', 'Eligibility string'):
            assert value(canvas(page, title)) == '—'
        page.evaluate('''()=>{for(const suffix of ['missing','string','bool','unsafe'])
            fw.filterEngine.setFilter('combo_summary_eligibility_'+suffix,'valid-day','day','equals','2026-08-02')}''')
        for suffix in ('missing', 'string', 'bool', 'unsafe'):
            assert value(canvas(page, 'Eligibility ' + suffix)) == '$60.00'
        assert not errors + failed
    finally:
        page.close()
    serialized = {}
    for mode in ('baseline', 'off', 'on'):
        page, errors, failed = open_report(browser, reports / mode, query=url)
        try:
            assert state(canvas(page, 'Context left'))['datasets'][0]['data'] == [10]
            assert state(canvas(page, 'Context right'))['datasets'][2]['data'] == [3]
            if mode == 'on':
                assert value(canvas(page, 'Context left')) == '$10.00'
                assert value(canvas(page, 'Context right')) == '3'
                assert value(canvas(page, 'Spend sum')) == '$90.00'
            serialized[mode] = page.evaluate('window._fwUrlSync.parse()')
            assert not errors + failed
        finally:
            page.close()
    assert serialized['baseline'] == serialized['off'] == serialized['on']
    assert serialized['on']['combo_summary_context.day'] == 'All'
    (reports / 'retained-url-limit.json').write_text(json.dumps(serialized, indent=2))
    page, errors, failed = open_report(browser, reports / 'scoped')
    try:
        assert value(canvas(page, 'First combo')) == '$90.00'
        for label in ('Second', 'First', 'Second', 'First'):
            page.locator('[data-toggle-id="__scope__"]').get_by_role('button', name=label, exact=True).click()
            page.wait_for_function('scope=>!!window._scopeCache[scope]', arg=label)
            assert value(canvas(page, 'First combo')) == '$90.00'
        cid = canvas(page, 'First combo').get_attribute('id')
        assert page.evaluate('id=>(fw.filterEngine._subscribers.combo_scope_first||[]).filter(s=>s.id===id).length', cid) == 1
        assert not errors + failed
    finally:
        page.close()


def test_actual_runtime_reducers_cancellation_eligibility_and_identity(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        result = page.evaluate('''()=>{
            const s={stat:'sum',scope:'axis_series',axis:'left',series:'Daily total'};
            const reduce=(values,stat='sum',extra=[])=>_fwComboSummaryResult({...s,stat},[{label:s.series,yAxisID:'y',data:values},...extra],'');
            const cfg={combo_summary:s,value_cols:['spend','fees'],y_labels:['Daily total','Fees'],line_cols:['orders'],line_labels:['Daily total']};
            return {
                stats:['sum','mean','min','max','last'].map(stat=>reduce([30,60],stat).value),
                rightMean:_fwComboSummaryResult({...s,axis:'right',stat:'mean'},[{yAxisID:'y1',label:s.series,data:[9,18]}],'').value,
                zero:reduce([0,0]).value,signed:reduce([-5,2]).value,
                cancellation:reduce([1e15,1,-1e15]).value,cancellationMean:reduce([1e15,1,-1e15],'mean').value,
                tiny:reduce([1e15,.001,-1e15]).value,
                invalid:[null,true,'3',NaN,Infinity,9007199254740992].map(v=>reduce([1,v]).reason),
                unsafeResult:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER]).reason,
                mean:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER],'mean').value,
                min:reduce([Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER],'min').value,
                empty:reduce([]).reason,last:reduce([60,null],'last').reason,
                right:reduce([30,60],'sum',[{yAxisID:'y1',label:s.series,data:[Infinity]}]).value,
                duplicate:reduce([30,60],'sum',[{yAxisID:'y',label:s.series,data:[1]}]).reason,
                missing:_fwComboSummaryResult({...s,series:'Absent'},[{yAxisID:'y',label:s.series,data:[90]}],'').reason,
                selected:_fwComboSummaryInputReason(cfg,[{spend:null,fees:1,orders:2}]),
                unselected:_fwComboSummaryInputReason(cfg,[{spend:3,fees:null,orders:'bad'}]),
                fallback:_fwComboSummaryInputReason({...cfg,y_labels:[''] ,combo_summary:{...s,series:'spend'}},[{spend:null}]),
                zerosMean:reduce([0,30,60],'mean').value
            };
        }''')
        assert result['stats'] == [90, 45, 30, 60, 60] and result['rightMean'] == 13.5
        assert result['zero'] == 0 and result['signed'] == -3
        assert result['cancellation'] == 1 and result['cancellationMean'] == pytest.approx(1 / 3)
        assert result['tiny'] == .001 and result['zerosMean'] == 30
        assert all(result['invalid']) and all(result[k] for k in ('unsafeResult', 'empty', 'last', 'duplicate', 'missing', 'selected', 'fallback'))
        assert result['mean'] == result['min'] == 2 ** 53 - 1 and result['right'] == 90
        assert result['unselected'] == ''
        target = canvas(page, 'Spend sum')
        cid = target.get_attribute('id')
        for fmt, expected in [('number', '90'), ('currency', '$90.00'), ('percent', '9000.00%'),
                              ('chips', '90 chips'), ('ratio', '90.00x'), ('plain', '90')]:
            # Format affects only the header; construction tests reject a sum
            # declaration with percent/ratio. Use mean for those formats here.
            stat = 'mean' if fmt in ('percent', 'ratio') else 'sum'
            page.evaluate('''a=>{const cfg=window._reportData.components[a.id];
                cfg.combo_summary={...cfg.combo_summary,format:a.fmt,stat:a.stat};window._fwRenderers.chart(a.id,cfg)}''',
                {'id': cid, 'fmt': fmt, 'stat': stat})
            assert value(target) == ({'percent': '4500.00%', 'ratio': '45.00x'}.get(fmt, expected))
        assert not errors + failed
    finally:
        page.close()


def test_native_source_config_canvas_option_adoption_removal_and_owned_handlers(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        target = canvas(page, 'Context left')
        cid = target.get_attribute('id')
        ds = 'combo_summary_context'
        args = {'id': cid, 'ds': ds}
        original_tokens = target.get_attribute('aria-describedby').split()
        assert {cid + '-header-context', cid + '-combo-context', cid + '-month-context', cid + '-combo-summary-scope'} == set(original_tokens)
        page.evaluate('''a=>{
            window.summaryCfg=window._reportData.components[a.id];window.summaryRows=fw.filterEngine.getFiltered(a.ds).map(r=>({...r}));
            for(let i=0;i<5;i++)window._fwRenderers.chart(a.id,summaryCfg);
            fw.filterEngine.init(a.ds,summaryRows.map(r=>({...r,spend:r.spend*3})));
        }''', args)
        assert value(target) == '$270.00'
        page.evaluate('a=>fw.filterEngine.removeDataset(a.ds)', args)
        assert value(target) == '—'
        page.evaluate('a=>fw.filterEngine.init(a.ds,summaryRows)', args)
        assert value(target) == '$90.00'
        # Follow the engine's existing scoped-parent lifecycle without a new hook.
        page.evaluate('''a=>{fw.filterEngine.addScopedChild('combo_summary_child',a.ds);
            summaryCfg={...summaryCfg,dataset_id:'combo_summary_child'};window._fwRenderers.chart(a.id,summaryCfg);
            fw.filterEngine.init(a.ds,summaryRows.map(r=>({...r,spend:r.spend*2})));}''', args)
        assert value(target) == '$180.00'
        page.evaluate('''a=>{summaryCfg={...summaryCfg,dataset_id:a.ds};window._fwRenderers.chart(a.id,summaryCfg);
            fw.filterEngine.removeDataset('combo_summary_child');fw.filterEngine.init(a.ds,summaryRows);}''', args)
        assert value(target) == '$90.00'
        page.evaluate('''a=>{summaryCfg={...summaryCfg,combo_summary:{...summaryCfg.combo_summary,stat:'mean'}};
            window._reportData.components[a.id]=summaryCfg;window._fwRenderers.chart(a.id,summaryCfg)}''', args)
        assert value(target) == '$45.00'
        page.evaluate('a=>{delete summaryCfg.combo_summary;window._fwRenderers.chart(a.id,summaryCfg)}', args)
        assert box(target).count() == 0 and set(target.get_attribute('aria-describedby').split()) == set(original_tokens) - {cid + '-combo-summary-scope'}
        assert target.evaluate('el=>el.parentElement.parentElement.querySelectorAll(".fw-chart-title").length') == 1
        page.evaluate('''a=>{summaryCfg.combo_summary={stat:'last',scope:'axis_series',axis:'right',series:'Daily total',label:'<img src=x onerror=alert(1)>',format:'plain'};
            window._fwRenderers.chart(a.id,summaryCfg)}''', args)
        assert value(target) == '18' and box(target).locator('img').count() == 0
        assert box(target).locator('.fw-combo-summary-label').text_content().startswith('<img')
        page.evaluate('''a=>{const el=document.getElementById(a.id);el.replaceWith(el.cloneNode(true));window._fwRenderers.chart(a.id,summaryCfg)}''', args)
        target = page.locator('#' + cid)
        assert value(target) == '18' and page.locator('#' + cid + '-combo-summary-scope').count() == 1
        assert page.evaluate('a=>(fw.filterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length', args) == 1
        page.evaluate('''id=>{window.summaryDrawCount=0;const create=window._createChart;
            window._createChart=function(...args){if(args[0]===id)window.summaryDrawCount++;return create(...args)};
            window.dispatchEvent(new CustomEvent('fw-theme-change'));}''', cid)
        assert page.evaluate('window.summaryDrawCount') == 1
        page.evaluate('''id=>{window.summaryZoomResetCount=0;window._chartInstances[id].resetZoom=()=>window.summaryZoomResetCount++}''', cid)
        target.dispatch_event('dblclick')
        assert page.evaluate('window.summaryZoomResetCount') == 1
        # Canvas removal retires only its own subscription, token and header.
        page.evaluate('a=>document.getElementById(a.id).remove()', args)
        page.wait_for_function('a=>(fw.filterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length===0', arg=args)
        assert page.locator('#' + cid + '-combo-summary-scope').count() == 0
        assert value(canvas(page, 'Context right')) == '27'
        assert not errors + failed
    finally:
        page.close()


def test_native_static_adoption_and_owned_cleanup(reports, browser):
    page, errors, failed = open_report(browser, reports / 'off')
    try:
        target = canvas(page, 'Static on')
        cid = target.get_attribute('id')
        page.evaluate('''id=>{const cfg=window._reportData.components[id];cfg.combo_summary={stat:'last',scope:'axis_series',axis:'left',series:'Daily total',label:'Last shown',format:'plain'};
            window._fwRenderers.chart(id,cfg)}''', cid)
        assert value(target) == '30'
        assert target.evaluate('el=>el.parentElement.parentElement.querySelectorAll(".fw-chart-title").length') == 1
        page.evaluate('''id=>{const el=document.getElementById(id);el.replaceWith(el.cloneNode(true));window._fwRenderers.chart(id,window._reportData.components[id])}''', cid)
        target = page.locator('#' + cid)
        assert value(target) == '30' and page.locator('#' + cid + '-combo-summary-scope').count() == 1
        page.evaluate('''id=>{const cfg=window._reportData.components[id];delete cfg.combo_summary;window._fwRenderers.chart(id,cfg)}''', cid)
        assert box(target).count() == 0 and 'combo-summary' not in (target.get_attribute('aria-describedby') or '')
        page.evaluate('''id=>{const cfg=window._reportData.components[id];cfg.combo_summary={stat:'sum',scope:'axis_series',axis:'left',series:'Daily total',label:'x'.repeat(60),format:'number'};
            cfg.datasets[0].label='x'.repeat(120);cfg.combo_summary.series=cfg.datasets[0].label;window._fwRenderers.chart(id,cfg)}''', cid)
        assert value(target) == '90'
        page.evaluate('id=>document.getElementById(id).remove()', cid)
        page.wait_for_function('id=>!document.getElementById(id+"-combo-summary-scope")', arg=cid)
        assert not errors + failed
    finally:
        page.close()


@pytest.mark.heavy
def test_owned_hidden_tab_and_reentry_resources(reports, browser):
    page, errors, failed = open_report(browser, reports / 'on')
    try:
        target = canvas(page, 'Spend sum')
        cid = target.get_attribute('id')
        before = page.evaluate('fw.filterEngine._lifecycleListeners.size')
        page.evaluate('''id=>{Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));
            for(let i=0;i<25;i++)window._fwRenderers.chart(id,window._reportData.components[id]);
            Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'));}''', cid)
        assert value(target) == '$90.00'
        assert page.evaluate('fw.filterEngine._lifecycleListeners.size') == before
        assert page.evaluate('id=>(fw.filterEngine._subscribers.combo_summary_reducers||[]).filter(s=>s.id===id).length', cid) == 1
        page.set_viewport_size({'width': 390, 'height': 1800})
        page.evaluate('''id=>{const cfg=window._reportData.components[id];cfg.y_labels[0]='W'.repeat(120);
            cfg.combo_summary={...cfg.combo_summary,series:cfg.y_labels[0],label:'W'.repeat(60)};
            window._fwRenderers.chart(id,cfg)}''', cid)
        assert value(target) == '$90.00'
        assert page.locator('html').evaluate('el=>el.scrollWidth <= window.innerWidth')
        target.locator('..').locator('..').screenshot(path=str(reports / 'phone-long-summary.png'))
        assert not errors + failed
    finally:
        page.close()
