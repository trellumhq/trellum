"""Native ReportContext traffic compositions with real vendored charts/exports."""
from __future__ import annotations

import base64
import csv
import io
import json
import mimetypes
import os
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

import pytest

from trellum.assets import load_js
from trellum.components import DataSource, FilterBar, Grid, StackedBar, Toggle
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.testing.test_traffic_composition import BASELINE, ROOT, examples, module
from trellum.validation import validate_report


def build(output, *, historical=False, scoped=False):
    output.mkdir(parents=True, exist_ok=True)
    ctx = ReportContext(config={'name': 'Traffic composition', 'studio': 'test', 'category': 'Components',
        'description': 'Complete synthetic event-linked traffic and independently filtered native teaching variants.'},
        slug='traffic-composition', output_dir=str(output))
    teaching = examples()
    if historical:
        source = output / 'baseline_bar.py'
        source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/bar.py'], cwd=ROOT))
        old = module(source, 'native_traffic_baseline')
        def chart(*args, **kwargs):
            kwargs.pop('show_controls', None)
            kwargs.pop('height', None)
            return old.StackedBar(*args, **kwargs)
        teaching.StackedBar = chart
        teaching.VARIANTS = teaching.VARIANTS[:1]
    if scoped:
        frame = teaching.facts()
        for scope in ['First', 'Second']:
            ctx.set_scope(scope)
            ds = 'scope_' + scope.lower()
            ctx.add_section('Scoped ' + scope, [DataSource(ds, frame),
                FilterBar(ds, frame, filters=[{'column': 'platform', 'type': 'toggle'}]),
                Toggle(ds + '_split', list(teaching.BREAKDOWNS)),
                Toggle(ds + '_mode', ['Absolute', 'Percentage']),
                Grid(children=[StackedBar(frame, 'day', [metric], scope + metric, dataset_id=ds,
                    stack_by='channel', stack_by_options=teaching.BREAKDOWNS, normalize_toggle=True,
                    show_controls=False, stack_toggle_id=ds + '_split', normalize_toggle_id=ds + '_mode')
                    for metric in ['events', 'd7_revenue']])])
    else:
        teaching.add_sections(ctx)
    details = compute_details(ctx)
    validation = validate_report(ctx, details=details)
    (output / 'diagnostics.json').write_text(json.dumps(details, indent=2, default=str))
    (output / 'validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
    assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
    assert not any(c.id == 'toggle-unused' for c in validation.checks)
    with ExitStack() as stack:
        if historical:
            old_js = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/components/chart_base.js'], cwd=ROOT, text=True)
            stack.enter_context(patch('trellum.components.charts.base.load_js', lambda name: old_js if name == 'components/chart_base.js' else load_js(name)))
        render_report(ctx, str(output), auto_refresh=False)
    copy_vendor_tree(output)
    ctx.close_connections()


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_TRAFFIC_EVIDENCE', str(tmp_path_factory.mktemp('native-traffic'))))
    build(root / 'current')
    build(root / 'baseline', historical=True)
    build(root / 'scoped', scoped=True)
    return root


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


def open_report(browser, output, *, width=1280, query=''):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    errors, failed = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    advisories = []
    def console(message):
        if message.type == 'warning' and message.text.startswith('Canvas2D: Multiple readback operations using getImageData'):
            # Chromium's existing capture-library optimization hint also occurs
            # on the exact baseline; retain it as evidence, not a report warning.
            advisories.append(message.text)
            (output / 'capture-console-advisories.json').write_text(json.dumps(advisories, indent=2))
        elif message.type in ('warning', 'error'):
            errors.append(message.type + ': ' + message.text)
    page.on('console', console)
    page.on('response', lambda response: failed.append(response.url) if response.status >= 400 else None)
    def serve(route):
        path = output / unquote(urlparse(route.request.url).path).lstrip('/')
        route.fulfill(status=200 if path.is_file() else 404,
                      body=path.read_bytes() if path.is_file() else b'Missing native report asset',
                      content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
    page.route('http://traffic.test/**', serve)
    page.goto('http://traffic.test/index.html' + query, wait_until='networkidle')
    for canvas in page.locator('canvas[data-fw-kind]').all():
        # Only canvases in the current native report scope are visible.
        if canvas.is_visible():
            canvas.scroll_into_view_if_needed()
            page.wait_for_function('id=>!!window._chartInstances[id]', arg=canvas.get_attribute('id'))
    page.evaluate('document.fonts.ready')
    page.evaluate('Object.values(window._chartInstances).forEach(c=>{c.stop();c.update("none")})')
    return page, errors, failed


def section(page, key):
    return page.locator('.fw-section').filter(has=page.get_by_role('heading', name=dict(
        (k, title) for k, title, _, _ in examples().VARIANTS)[key], exact=True)).first


def charts(page, key='shared'):
    return section(page, key).locator('canvas[data-fw-title="Event composition"], canvas[data-fw-title="D7 revenue composition"]')


def state(canvas):
    canvas.scroll_into_view_if_needed()
    canvas.page.wait_for_function('id=>!!window._chartInstances[id]', arg=canvas.get_attribute('id'))
    return canvas.evaluate('''el=>{
      const c=window._chartInstances[el.id]; c.stop();c.update('none');
      return {labels:c.data.labels, height:el.parentElement.getBoundingClientRect().height,
        datasets:c.data.datasets.map((d,i)=>({label:d.label, data:d.data, abs:d._absData || null,
            visible:c.isDatasetVisible(i),color:d.borderColor}))};
    }''')


def pick(page, key, suffix, value):
    page.locator('[data-toggle-id="showcase_traffic_' + key + '_' + suffix + '"]').get_by_role('button', name=value, exact=True).click()


def assert_oracle(canvas, frame, column, metric, percentage):
    actual = state(canvas)
    expected = frame.groupby(['day', column])[metric].sum()
    assert actual['labels'] == sorted(frame.day.unique())
    for dataset in actual['datasets']:
        absolute = [float(expected.get((day, dataset['label']), 0)) for day in actual['labels']]
        totals = frame.groupby('day')[metric].sum()
        plotted = [v / totals[day] * 100 for day, v in zip(actual['labels'], absolute)] if percentage else absolute
        assert dataset['data'] == pytest.approx(plotted)
        if percentage:
            assert dataset['abs'] == pytest.approx(absolute)
        else:
            assert dataset['abs'] is None
    if percentage:
        for i, _ in enumerate(actual['labels']):
            assert sum(d['data'][i] for d in actual['datasets']) == pytest.approx(100)


def prepare_capture(region):
    # Native collapsed details render lazily. Reveal actual tables before asking
    # the existing section-capture readiness guard to admit the section.
    for disclosure in region.locator('.fw-collapsible').all():
        if 'fw-collapsed' not in disclosure.get_attribute('class'):
            continue
        disclosure.locator('.fw-collapsible-toggle').click()
        table = disclosure.locator('[data-fw-kind="DataTable"]')
        table.scroll_into_view_if_needed()
        region.page.wait_for_function('id=>document.getElementById(id).childElementCount>0', arg=table.get_attribute('id'))
        disclosure.locator('.fw-collapsible-toggle').click()
    region.scroll_into_view_if_needed()
    region.evaluate('el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{})))')


def capture(region, path):
    prepare_capture(region)
    region.page.evaluate('Object.values(window._chartInstances).forEach(c=>{c.stop();c.update("none")})')
    captured = region.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
    png = base64.b64decode(captured['image']['data_base64'])
    path.write_bytes(png)
    return png


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_baseline_all_breakdowns_modes_filters_and_dimensions(reports, browser, width):
    page, errors, failed = open_report(browser, reports / 'current', width=width)
    before, old_errors, old_failed = open_report(browser, reports / 'baseline', width=width)
    try:
        frame = examples().facts()
        for theme in ['trellum light', 'trellum dark']:
            for target in [page, before]:
                target.locator('#fwThemeSelect').select_option(theme)
            for i in range(2):
                assert state(charts(page, 'local').nth(i)) == state(charts(before, 'local').nth(i))
            # Actual historical/current captures, including KPI geometry and doughnuts.
            current_png = capture(section(page, 'local'), reports / f'local-{width}-{theme.split()[-1]}.png')
            old_png = capture(section(before, 'local'), reports / f'baseline-{width}-{theme.split()[-1]}.png')
            assert current_png == old_png
            for key in ['shared', 'shared_220']:
                region = section(page, key)
                assert region.locator('[data-toggle-id$="_breakdown"]').count() == 1
                assert region.locator('[data-toggle-id$="_mode"]').count() == 1
                assert region.locator('[data-stack-toggle], [data-normalize-toggle]').count() == 0
                for label, column in examples().BREAKDOWNS.items():
                    pick(page, key, 'breakdown', label)
                    for mode in ['Absolute', 'Percentage']:
                        pick(page, key, 'mode', mode)
                        for i, metric in enumerate(['events', 'd7_revenue']):
                            assert_oracle(charts(page, key).nth(i), frame, column, metric, mode == 'Percentage')
                            assert state(charts(page, key).nth(i))['height'] == (220 if key == 'shared_220' else 320 if width == 1280 else 180)
                capture(region, reports / f'{key}-{width}-{theme.split()[-1]}.png')
        # Per-section filters: one iOS day; sibling pair/section remains unchanged.
        key = 'shared'
        pick(page, key, 'breakdown', 'Channel')
        section(page, key).get_by_role('button', name='iOS', exact=True).click()
        ds = 'showcase_traffic_shared'
        region = section(page, key)
        region.locator('.fw-date-trigger').click()
        region.locator('input[data-edge="max"]').fill('2026-08-01')
        region.locator('input[data-edge="max"]').press('Tab')
        page.keyboard.press('Escape')
        subset = frame[(frame.platform == 'iOS') & (frame.day == '2026-08-01')]
        for i, metric in enumerate(['events', 'd7_revenue']):
            assert_oracle(charts(page, key).nth(i), subset, 'channel', metric, True)
            assert sum(d['abs'][0] for d in state(charts(page, key).nth(i))['datasets']) == (68 if i == 0 else 80)
            assert state(charts(page, 'shared_220').nth(i))['labels'] == ['2026-08-01', '2026-08-02']
        for label, column in examples().BREAKDOWNS.items():
            pick(page, key, 'breakdown', label)
            for mode in ['Absolute', 'Percentage']:
                pick(page, key, 'mode', mode)
                for i, metric in enumerate(['events', 'd7_revenue']):
                    assert_oracle(charts(page, key).nth(i), subset, column, metric, mode == 'Percentage')
        for name, count, revenue in [('paid', 32, 62), ('organic', 36, 18)]:
            sums = page.evaluate('ds=>window._fwFilterEngine.getFiltered(ds).reduce((a,r)=>[a[0]+r.events,a[1]+r.d7_revenue],[0,0])', ds + '_' + name)
            assert sums == [count, revenue]
        assert not errors + old_errors + failed + old_failed
    finally:
        page.close()
        before.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_restoration_independent_legends_csv_and_captures(reports, browser, width):
    query = '?t.showcase_traffic_shared_breakdown=Channel&t.showcase_traffic_shared_mode=Percentage'
    page, errors, failed = open_report(browser, reports / 'current', query=query, width=width)
    try:
        frame = examples().facts()
        pair = charts(page)
        ids = [pair.nth(i).get_attribute('id') for i in range(2)]
        for i, metric in enumerate(['events', 'd7_revenue']):
            assert_oracle(pair.nth(i), frame, 'channel', metric, True)
        # Native legend action changes only one chart; all-series denominators stay fixed.
        # Legends have a native chart-owned DOM reference regardless of placement.
        page.evaluate('id=>window._chartInstances[id]._fwLegendEl.querySelector(".cl").click()', ids[0])
        assert state(pair.nth(0))['datasets'][0]['visible'] is False
        assert all(d['visible'] for d in state(pair.nth(1))['datasets'])
        pick(page, 'shared', 'mode', 'Absolute')
        pick(page, 'shared', 'mode', 'Percentage')
        page.locator('#fwThemeSelect').select_option('trellum dark')
        page.evaluate('window.renderAll()')
        assert state(pair.nth(0))['datasets'][0]['visible'] is False
        assert all(d['visible'] for d in state(pair.nth(1))['datasets'])
        for mode in ['Percentage', 'Absolute']:
            pick(page, 'shared', 'mode', mode)
            for i, metric in enumerate(['events', 'd7_revenue']):
                expected = state(pair.nth(i))
                with page.expect_download() as download:
                    page.locator('[data-csv-chart="' + ids[i] + '"]').click(force=True)
                path = reports / f'{metric}-{mode.lower()}-{width}.csv'
                download.value.save_as(path)
                rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
                assert len(rows) == 3
                assert rows[0][1:] == [d['label'] for d in expected['datasets']]
                assert [r[0] for r in rows[1:]] == expected['labels']
                for j, row in enumerate(rows[1:]):
                    assert [float(v) for v in row[1:]] == pytest.approx([d['data'][j] for d in expected['datasets']])
                assert_oracle(pair.nth(i), frame, 'channel', metric, mode == 'Percentage')
        pick(page, 'shared', 'mode', 'Percentage')
        region = section(page, 'shared')
        prepare_capture(region)
        captured = region.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        (reports / f'native-section-{width}.png').write_bytes(base64.b64decode(captured['image']['data_base64']))
        # Normal native capture excludes action chrome but includes selected controls/pair.
        for method, suffix in [('exportPNG', 'png'), ('exportPDF', 'pdf')]:
            with page.expect_download(timeout=60000) as download:
                page.evaluate('method=>fw[method]()', method)
            download.value.save_as(reports / (f'native-report-{width}.' + suffix))
        assert not errors + failed
    finally:
        page.close()


def test_native_binding_cleanup_source_canvas_config_and_fresh_scope_reentry(reports, browser):
    page, errors, failed = open_report(browser, reports / 'current')
    try:
        pair = charts(page)
        ids = [pair.nth(i).get_attribute('id') for i in range(2)]
        args = {'id': ids[0], 'ds': 'showcase_traffic_shared'}
        def subscriptions():
            return page.evaluate('a=>(window._fwFilterEngine._subscribers[a.ds]||[]).filter(s=>s.id===a.id).length', args)
        assert subscriptions() == 1
        page.evaluate('''()=>{
            window.trafficDraws={};const original=window._createChart;
            window._createChart=function(id,canvas,spec){window.trafficDraws[id]=(window.trafficDraws[id]||0)+1;return original(id,canvas,spec)};
        }''')
        page.evaluate('''a=>{
            const cfg=window._reportData.components[a.id];window.trafficCfg=cfg;
            for(let i=0;i<8;i++) window._fwRenderers.chart(a.id,cfg);
        }''', args)
        assert subscriptions() == 1
        page.evaluate('''a=>{
            const engine=window._fwFilterEngine;
            window.trafficOriginalRows=engine.getFiltered(a.ds).map(r=>({...r}));
            engine.initColumnar(a.ds, window.trafficOriginalRows.map(r=>({...r,events:r.events*3})));
            window._fwRenderers.chart(a.id, window.trafficCfg);
            const replacement=JSON.parse(JSON.stringify(window.trafficCfg));
            window._reportData.components[a.id]=replacement;
            window._fwRenderers.chart(a.id,replacement); window.trafficCfg=replacement;
        }''', args)
        assert subscriptions() == 1
        changed = examples().facts()
        changed['events'] *= 3
        assert_oracle(pair.nth(0), changed, 'channel', 'events', False)
        page.evaluate('a=>{window._fwFilterEngine.initColumnar(a.ds,window.trafficOriginalRows);window._fwRenderers.chart(a.id,window.trafficCfg)}', args)
        page.evaluate('window.trafficDraws={}')
        pick(page, 'shared', 'mode', 'Percentage')
        assert page.evaluate('id=>window.trafficDraws[id]', ids[0]) == 1
        page.evaluate('window.trafficDraws={};window.dispatchEvent(new CustomEvent("fw-theme-change"))')
        assert page.evaluate('id=>window.trafficDraws[id]', ids[0]) == 1
        assert_oracle(pair.nth(0), examples().facts(), 'channel', 'events', True)
        page.evaluate('''a=>{
            const old=document.getElementById(a.id);old.replaceWith(old.cloneNode(true));
            window._fwRenderers.chart(a.id, window.trafficCfg);
        }''', args)
        assert subscriptions() == 1
        # Replace an external native group, preserving its selected value.
        page.evaluate('''a=>{
            const group=document.querySelector('[data-toggle-id="showcase_traffic_shared_mode"]');
            group.replaceWith(group.cloneNode(true));window._fwRenderers.chart(a.id,window.trafficCfg);
        }''', args)
        # The sibling retires too; a normal render rebinds both to the new owner.
        page.evaluate('window.renderAll()')
        assert subscriptions() == 1
        pick(page, 'shared', 'mode', 'Absolute')
        assert_oracle(pair.nth(0), examples().facts(), 'channel', 'events', False)
        page.evaluate('a=>window._fwFilterEngine.setFilter(a.ds,"empty-check","platform","equals","Missing")', args)
        empty = state(pair.nth(0))
        assert not empty['labels'] and not empty['datasets']
        page.evaluate('a=>window._fwFilterEngine.clearFilter(a.ds,"empty-check")', args)
        assert_oracle(pair.nth(0), examples().facts(), 'channel', 'events', False)
        page.evaluate('id=>document.getElementById(id).remove()', ids[0])
        page.wait_for_function('a=>!(window._fwFilterEngine._subscribers[a.ds]||[]).some(s=>s.id===a.id)', arg=args)
        assert not page.evaluate('id=>window._fwCategoryChartBindings.has(id)', ids[0])
        page.evaluate('window.dispatchEvent(new CustomEvent("fw-theme-change"))')
        assert not errors + failed
    finally:
        page.close()
    query = ('?showcase_traffic_shared.platform=iOS&showcase_traffic_shared.day=2026-08-01..2026-08-01'
             '&t.showcase_traffic_shared_breakdown=Inactivity&t.showcase_traffic_shared_mode=Percentage')
    restored, restore_errors, restore_failed = open_report(browser, reports / 'current', query=query)
    try:
        frame = examples().facts()
        subset = frame[(frame.platform == 'iOS') & (frame.day == '2026-08-01')]
        for i, metric in enumerate(['events', 'd7_revenue']):
            assert_oracle(charts(restored).nth(i), subset, 'inactivity', metric, True)
        assert not restore_errors + restore_failed
    finally:
        restored.close()
    page, errors, failed = open_report(browser, reports / 'scoped')
    try:
        first = page.locator('canvas[data-fw-title="Firstevents"]')
        first_id = first.get_attribute('id')
        pick_scope = page.locator('[data-toggle-id="__scope__"]')
        page.locator('[data-toggle-id="scope_first_mode"]').get_by_role('button', name='Percentage', exact=True).click()
        pick_scope.get_by_role('button', name='Second', exact=True).click()
        page.wait_for_function('id=>!window._fwCategoryChartBindings.has(id)', arg=first_id)
        pick_scope.get_by_role('button', name='First', exact=True).click()
        first.scroll_into_view_if_needed()
        page.wait_for_function('id=>window._fwCategoryChartBindings.has(id)', arg=first_id)
        assert_oracle(first, examples().facts(), 'channel', 'events', True)
        assert page.evaluate('id=>window._fwFilterEngine._subscribers.scope_first.filter(s=>s.id===id).length', first_id) == 1
        assert not errors + failed
    finally:
        page.close()
