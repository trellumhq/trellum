"""Native generated reports: real SQLite snapshots and binder transport."""
from __future__ import annotations

from contextlib import ExitStack
import base64
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

from trellum.assets import load_js
from trellum.components import FilterBar, LineChart, LiveDataSource, ScopedDataSource
from trellum.data.query import disable_cache, enable_cache
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report
from trellum.testing.test_live_result_state_browser import (
    browser, build_report as build_scope_report, commit, open_report, reply, state, wait_requests,
)

BASELINE = '98d96192564f87568252c6779481c6234b5b848a'
ROOT = Path(__file__).resolve().parents[2]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


def build(output, *, historical=False, with_textual=False):
    output.mkdir(parents=True, exist_ok=True)
    chart_class = LineChart
    if historical:
        source = output / 'baseline_line.py'
        source.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/components/charts/line_area.py'], cwd=ROOT))
        chart_class = module(source, 'line_result_native_baseline').LineChart

    def chart(*args, **kwargs):
        if historical:
            kwargs.pop('result_presentation', None)
        return chart_class(*args, **kwargs)

    db = output / 'fixture.sqlite'
    conn = sqlite3.connect(db)
    conn.execute('drop table if exists observations')
    conn.execute('create table observations (id integer, day text, value real, label text)')
    conn.executemany('insert into observations values (?,?,?,?)',
                     [(1, f'2026-10-0{i}', v, 'Supplied') for i, v in enumerate([10, 0, 25], 1)])
    conn.commit()
    conn.close()
    ctx = ReportContext(config={'name': 'Ordinary line results', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic supplied observations and independent native live lookups.',
        'data_sources': [{'name': 'fixture', 'type': 'sqlite', 'path': str(db)}]},
        slug='live-result-contract', output_dir=str(output))
    disable_cache()
    try:
        for dataset in (['orders', 'independent'] if with_textual else ['orders', 'empty', 'independent']):
            frame = ctx.declare_live_query(dataset,
                'SELECT id, day, value, label FROM observations WHERE id = :id ORDER BY day',
                datasource='fixture', params=[{'name': 'id', 'type': 'int', 'required': True}],
                snapshot_params={'id': 999 if dataset == 'empty' else 1})
            source = LiveDataSource(dataset, query=dataset, df=frame,
                bindings=[{'column': 'id', 'param': 'id', 'filter_type': 'text'}])
            if dataset == 'independent':
                ctx.sections[0]['components'].insert(0, source)
                continue
            ctx.add_section('', [source,
                FilterBar(dataset, frame, filters=[{'type': 'text', 'column': 'id', 'param': 'id', 'label': 'Entity ID'}])])
            ctx.add_section('Live ' + dataset, [
                chart(frame, 'day', ['value', 'id'], 'Daily ' + dataset, dataset_id=dataset,
                      y_labels=['Value', 'Entity'], header_summary={'stat': 'sum', 'series': 0, 'label': 'Displayed total'}),
                chart(frame, 'day', 'value', 'Opt-out ' + dataset, dataset_id=dataset, result_presentation=False),
            ])
            if dataset == 'orders':
                ctx.add_section('Local live subset', [
                    ScopedDataSource('child', parent='orders'),
                    FilterBar('child', frame, filters=[{'type': 'text', 'column': 'label', 'label': 'Local label contains'}]),
                    chart(frame, 'day', 'value', 'Child orders', dataset_id='child', height='compact'),
                ])
        examples = module(ROOT / 'trellum/demo/reports/component-showcase/examples_line_results.py',
                          'native_line_results_examples_' + str(historical))
        examples.LineChart = chart
        examples.add_sections(ctx)
        if with_textual:
            words = ctx.declare_live_query('textual',
                'SELECT id, day, value, label FROM observations WHERE label = :label ORDER BY day',
                datasource='fixture', params=[{'name': 'label', 'type': 'str', 'required': True, 'max_length': 200}],
                snapshot_params={'label': 'Supplied'})
            ctx.add_section('', [LiveDataSource('textual', query='textual', df=words),
                FilterBar('textual', words, filters=[{'type': 'text', 'column': 'label', 'param': 'label', 'label': 'Lookup label'}])])
            ctx.add_section('Text parameter lookup', [chart(words, 'day', 'value', 'Textual lookup', dataset_id='textual')])
        details = compute_details(ctx)
        validation = validate_report(ctx, details=details)
        (output / 'diagnostics.json').write_text(json.dumps(details, indent=2, default=str))
        (output / 'validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
        assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
        with ExitStack() as stack:
            if historical:
                old = subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/components/chart_base.js'], cwd=ROOT, text=True)
                stack.enter_context(patch('trellum.components.charts.base.load_js', lambda name: old if name == 'components/chart_base.js' else load_js(name)))
            render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
    finally:
        ctx.close_connections()
        enable_cache()


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_LINE_RESULTS_EVIDENCE', str(tmp_path_factory.mktemp('native-line-results'))))
    for name in ['current', 'baseline']:
        build(root / name, historical=name == 'baseline')
    build_scope_report(root / 'scoped', scoped=True)
    build(root / 'textual', with_textual=True)
    return root


def canvas(page, title='Daily orders'):
    return page.locator(f'canvas[data-fw-title="{title}"]')


def message(page, title='Daily orders', *, context=False):
    chart = canvas(page, title)
    return page.locator('#' + chart.get_attribute('id') + ('-result-context' if context else '-result'))


def plotted(page, title='Daily orders'):
    return canvas(page, title).evaluate('''el=>{
      const c=window._chartInstances[el.id];c.stop();c.update('none');
      return {labels:c.data.labels,data:c.data.datasets.map(d=>d.data),
        height:el.parentElement.getBoundingClientRect().height,
        visible:c.data.datasets.map((_,i)=>c.isDatasetVisible(i)),xmin:c.options.scales.x.min,xmax:c.options.scales.x.max};
    }''')


def shoot(page, path, title='Daily orders'):
    canvas(page, title).locator('xpath=ancestor::*[contains(@class,"fw-section")][1]').screenshot(path=str(path))


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_line_results_baseline_and_pending_failure_empty_theme(reports, browser, width):
    page, requests, pending, errors = open_report(browser, reports / 'current', width=width)
    before, _, _, baseline_errors = open_report(browser, reports / 'baseline', width=width)
    try:
        for theme in ['trellum light', 'trellum dark']:
            for target in [before, page]:
                target.locator('#fwThemeSelect').select_option(theme)
            for title in ['Daily orders', 'Opt-out orders', 'Selected supplied observations']:
                assert plotted(page, title) == plotted(before, title)
                assert canvas(page, title).screenshot() == canvas(before, title).screenshot()
            shoot(page, reports / f'completed-{width}-{theme.split()[-1]}.png')
        for target in [before, page]:
            target.locator('#fwThemeSelect').select_option('trellum light')
        assert message(page).is_hidden()
        for target in [before, page]:
            target.locator('.fw-filter-bar[data-fw-ds-id="line_results_observations"] button', has_text='Measured zero').click()
        assert plotted(page, 'Selected supplied observations')['data'] == [[0, 0, 0]]
        for theme in ['trellum light', 'trellum dark']:
            for target in [before, page]:
                target.locator('#fwThemeSelect').select_option(theme)
            assert plotted(page, 'Selected supplied observations') == plotted(before, 'Selected supplied observations')
            assert canvas(page, 'Selected supplied observations').screenshot() == canvas(before, 'Selected supplied observations').screenshot()
            shoot(page, reports / f'zero-{width}-{theme.split()[-1]}.png', 'Selected supplied observations')
        for target in [before, page]:
            target.locator('#fwThemeSelect').select_option('trellum light')
        assert message(page, 'Daily empty').inner_text() == 'No matching data'
        assert message(page, 'Empty supplied snapshot').inner_text() == 'No data supplied'
        assert message(page, 'Local selected observations').inner_text() == 'No matching data'
        assert 'The source has not supplied' in message(page, 'Declared unavailable observations').inner_text()
        assert plotted(page, 'Declared unavailable observations')['data'] == [[None, None]]
        shoot(page, reports / f'unavailable-{width}-light.png', 'Declared unavailable observations')
        assert plotted(page)['height'] == 220
        assert plotted(page, 'Child orders')['height'] == 160
        initial = plotted(page)
        # Real legend and zoom choices survive status-only transitions.
        canvas(page).evaluate('''el=>{const c=window._chartInstances[el.id];window.__retainedChart=c;
            c.setDatasetVisibility(1,false);c.zoomScale('x',{min:1,max:2});c.update('none');}''')
        selected = plotted(page)
        commit(page, 2)
        wait_requests(page, requests, 1)
        assert 'Requested: id=2' in message(page, context=True).inner_text()
        assert 'Displayed snapshot: id=1' in message(page, context=True).inner_text()
        assert 'Local subset of the displayed live parent (orders)' in message(page, 'Child orders', context=True).inner_text()
        assert plotted(page) == selected
        assert canvas(page).evaluate('el=>window._chartInstances[el.id]===window.__retainedChart')
        shoot(page, reports / f'retained-pending-{width}.png')
        reply(pending, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').phase==='error'")
        assert 'Could not load requested data' in message(page, context=True).inner_text()
        assert plotted(page) == selected
        shoot(page, reports / f'retained-failed-{width}.png')
        commit(page, 1)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').phase==='ready'")
        assert message(page, context=True).is_hidden()
        assert len(requests) == 1
        for _ in range(2):
            page.evaluate('window.renderAll()')
        page.locator('#fwThemeSelect').select_option('trellum dark')
        assert plotted(page) == selected
        assert page.locator('.fw-line-result-context').count() == 7
        # Native controls request an empty result, then fail from valid empty.
        commit(page, 3)
        wait_requests(page, requests, 2)
        reply(pending, [])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').displayed_params.id===3")
        assert message(page).inner_text() == 'No matching data'
        assert plotted(page)['data'] == [[], []]
        assert message(page, context=True).is_hidden()
        shoot(page, reports / f'empty-{width}-dark.png')
        commit(page, 4)
        wait_requests(page, requests, 3)
        assert message(page).inner_text() == 'Loading requested data\nRequested: id=4'
        reply(pending, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').phase==='error'")
        assert message(page).inner_text() == 'Could not load requested data\nRequested: id=4'
        assert plotted(page)['height'] == initial['height']
        shoot(page, reports / f'empty-failed-{width}-dark.png')
        # Restore valid empty, then real zero. Neither creates a fake point.
        commit(page, 3)
        commit(page, 5)
        wait_requests(page, requests, 4)
        reply(pending, [[5, '2026-10-01', 0, 'Zero']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').displayed_params.id===5")
        assert message(page).is_hidden()
        assert plotted(page)['data'] == [[0], [5]]
        assert '0' in page.locator('[data-chart-summary="' + canvas(page).get_attribute('id') + '"]').inner_text()
        # A sibling source failure does not affect this chart's result/context.
        commit(page, 2, 'independent')
        wait_requests(page, requests, 5)
        reply(pending, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('independent').phase==='error'")
        assert message(page).is_hidden() and message(page, context=True).is_hidden()
        assert plotted(page)['data'] == [[0], [5]]
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors and not baseline_errors
    finally:
        page.close()
        before.close()


def test_native_subset_unknown_missing_source_replacement_and_cleanup(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'current')
    try:
        local = page.locator('.fw-filter-bar[data-fw-ds-id="child"] input[type=text]')
        local.fill('No such label')
        local.press('Enter')
        assert message(page, 'Child orders').inner_text() == 'No matching data'
        assert requests == []
        commit(page, 2)
        wait_requests(page, requests, 1)
        assert 'Loading requested data' in message(page, 'Child orders').inner_text()
        assert plotted(page, 'Child orders')['data'] == [[]]
        # A genuine replacement snapshot with unknown public defaults.
        page.evaluate('''() => {
            const data=window._reportData;
            const [id,cfg]=Object.entries(data.components).find(([,c])=>c.type==='live_data_source'&&c.dataset_id==='orders');
            const live=JSON.parse(JSON.stringify(cfg));live.live.defaults={};
            window._reportData={...data,components:{...data.components,[id]:live},
                _ds_orders:{_cols:['id','day','value','label'],_data:[[1,'2026-10-01',99,'Replacement']],_dict:{}}};
            window.renderAll();
        }''')
        assert state(page)['generation'] > 1
        assert plotted(page)['data'][0] == [99]
        # Old transport is obsolete and may ignore client abort.
        reply(pending, [[2, '2026-10-01', 999, 'Obsolete']])
        wait_requests(page, requests, 2)
        assert state(page)['displayed_params'] is None
        assert 'Displayed snapshot: unknown' in message(page, context=True).inner_text()
        assert plotted(page)['data'][0] == [99]
        reply(pending, [[2, '2026-10-02', 7, 'Replacement query']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        assert plotted(page)['data'][0] == [7]
        local.fill('Replacement query')
        local.press('Enter')
        assert plotted(page, 'Child orders')['data'] == [[7]]
        # Hidden/revealed native content and reentry keep one subscription.
        counts = page.evaluate("window._fwFilterEngine._subscribers.orders.length")
        canvas(page).evaluate("el=>el.parentElement.hidden=true")
        page.evaluate('window.renderAll()')
        canvas(page).evaluate("el=>el.parentElement.hidden=false")
        page.evaluate('window.renderAll()')
        canvas(page).scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id]', arg=canvas(page).get_attribute('id'))
        assert page.evaluate("window._fwFilterEngine._subscribers.orders.length") == counts
        assert plotted(page)['height'] == 220
        # Missing snapshots are not valid empty successes.
        page.evaluate('''() => {const d={...window._reportData};delete d._ds_orders;window._reportData=d;window.renderAll();}''')
        assert state(page)['has_result'] is False
        assert 'No matching data' != message(page).inner_text()
        chart_id = canvas(page).get_attribute('id')
        page.evaluate('''id=>{
            window.__removedCanvas=document.getElementById(id);window.__removedCanvas.remove();
        }''', chart_id)
        page.wait_for_function('!window.__removedCanvas._fwLineResult')
        assert page.locator('#' + chart_id + '-result').count() == 0
        assert not errors
    finally:
        page.close()


@pytest.mark.heavy
def test_native_long_hostile_params_and_scope_reentry(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'textual', width=390)
    try:
        hostile = '<img src=x onerror="window.__injected=1"> & ' + 'Long value ' * 12
        control = page.locator('.fw-filter-bar[data-fw-ds-id="textual"] input[type=text]')
        control.fill(hostile)
        control.press('Enter')
        wait_requests(page, requests, 1)
        note = message(page, 'Textual lookup', context=True)
        assert json.dumps(requests[-1]['params']['label']) in note.inner_text()
        assert note.locator('img').count() == 0
        assert page.evaluate('window.__injected') is None
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        note.evaluate('''el=>{window.__textChanges=0;window.__textObserver=new MutationObserver(records=>window.__textChanges+=records.length);
            window.__textObserver.observe(el,{childList:true,characterData:true,subtree:true});}''')
        page.evaluate('window.renderAll();window.renderAll()')
        assert page.evaluate('window.__textChanges') == 0
        reply(pending, [])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('textual').phase==='ready'")
        assert message(page, 'Textual lookup').inner_text() == 'No matching data'
        control.fill('another ' + hostile)
        control.press('Enter')
        wait_requests(page, requests, 2)
        assert 'Loading requested data' in message(page, 'Textual lookup').inner_text()
        shoot(page, reports / 'long-params-390.png', 'Textual lookup')
        reply(pending, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('textual').phase==='error'")
        assert not errors
    finally:
        page.close()
    page, requests, pending, errors = open_report(browser, reports / 'scoped')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        page.evaluate("window.__oldLine=document.querySelector('canvas[data-fw-title=\"Daily orders\"]');window.__oldResult=window.__oldLine._fwLineResult")
        page.locator('[data-scope-key=secondary]').click()
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders')===null")
        # Scope rendering may reuse this canvas for the new source; its old
        # presenter must be retired, with at most one replacement owner.
        page.wait_for_function('window.__oldLine._fwLineResult!==window.__oldResult')
        page.locator('[data-scope-key=orders]').click()
        wait_requests(page, requests, 2)
        assert 'Displayed snapshot: id=1' in message(page, context=True).inner_text()
        reply(pending, [[2, '2026-10-01', 900, 'Obsolete scope']])
        reply(pending, [[2, '2026-10-01', 42, 'Current scope']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        assert plotted(page)['data'] == [[42]]
        assert message(page, context=True).is_hidden()
        assert not errors
    finally:
        page.close()


@pytest.mark.heavy
def test_native_rate_limit_retry_csv_and_completed_empty_exports(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'current')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        assert page.evaluate('fw.captureElementForAnalysis(document.querySelector(".fw-section"))') is False
        page.locator('.fw-analysis-capture-ui').evaluate('el=>el.remove()')
        pending.pop(0).fulfill(status=429, headers={'Retry-After': '0'}, json={'error': 'Busy'})
        wait_requests(page, requests, 2)
        assert message(page, context=True).is_visible()
        reply(pending, [[2, '2026-10-01', 0, 'Zero']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        with page.expect_download() as download:
            page.locator('[data-csv-chart="' + canvas(page).get_attribute('id') + '"]').click(force=True)
        target = reports / 'displayed-zero.csv'
        download.value.save_as(target)
        assert '2026-10-01,0,2' in target.read_text()
        for method, suffix, magic in [('exportPNG', 'png', b'\x89PNG'), ('exportPDF', 'pdf', b'%PDF')]:
            with page.expect_download(timeout=45000) as download:
                page.evaluate('method=>fw[method]()', method)
            target = reports / ('completed.' + suffix)
            download.value.save_as(target)
            assert target.read_bytes().startswith(magic)
        for title, name in [('Daily orders', 'completed'), ('Daily empty', 'empty')]:
            section = canvas(page, title).locator('xpath=ancestor::*[contains(@class,"fw-section")][1]')
            # Native section capture returns its PNG snapshot without bypassing
            # the same live admission guard used by report export.
            captured = section.evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
            assert captured['image']['mime_type'] == 'image/png'
            (reports / (name + '-section.png')).write_bytes(base64.b64decode(captured['image']['data_base64']))
        assert not errors
    finally:
        page.close()
