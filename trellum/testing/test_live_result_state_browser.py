"""Native ReportContext/FilterBar/Line/KPI/Table contract with vendored assets.

Route interception supplies network responses to the real binder. Deterministic
transport edge cases live in test_live_result_state.py, not a report renderer.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import subprocess
from urllib.parse import urlparse
from unittest.mock import patch

import pytest

from trellum.assets import load_js
from trellum.components import DataTable, FilterBar, KpiRow, LineChart, LiveDataSource
from trellum.data.query import disable_cache, enable_cache
from trellum.rendering.cdn import copy_vendor_tree
from trellum.rendering.html_builder import render_report
from trellum.rendering.js_runtime import host_flag_scripts
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report

BASELINE = 'd19cd9e0b8e444dc1729f3548c2b95c68c08742b'
ROOT = Path(__file__).resolve().parents[2]
COLUMNS = ['id', 'day', 'value', 'label']
HOSTILE = '<img src=x onerror="window.__injected=1"> & ' + 'Long label ' * 30


def build_report(output, *, baseline=False, scoped=False):
    output.mkdir(parents=True, exist_ok=True)
    db = output / 'fixture.sqlite'
    conn = sqlite3.connect(db)
    conn.execute('drop table if exists observations')
    conn.execute('create table observations (id integer, day text, value real, label text)')
    conn.executemany('insert into observations values (?,?,?,?)',
                     [(1, f'2026-10-0{i}', value, 'Observed purchase') for i, value in enumerate([10, 25, 0], 1)])
    conn.commit()
    conn.close()
    ctx = ReportContext(config={'name': 'Live result contract', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic daily observations with independent per-entity lookups and unchanged native consumers.',
        'data_sources': [{'name': 'fixture', 'type': 'sqlite', 'path': str(db)}]},
        slug='live-result-contract', output_dir=str(output))
    disable_cache()
    try:
        for dataset in (['orders', 'secondary'] if scoped else ['orders', 'independent', 'empty']):
            frame = ctx.declare_live_query(dataset,
                'SELECT id, day, value, label FROM observations WHERE id = :id ORDER BY day',
                datasource='fixture', params=[{'name': 'id', 'type': 'int', 'required': True}],
                snapshot_params={'id': 999 if dataset == 'empty' else 1})
            source = LiveDataSource(dataset, query=dataset, df=frame,
                bindings=[{'column': 'id', 'param': 'id', 'filter_type': 'text'}])
            if scoped:
                ctx.set_scope(dataset)
            if not scoped and dataset != 'orders':
                # Independent registrars exercise the real binder without an
                # unrelated on-screen consumer or a synthetic state adapter.
                ctx.sections[0]['components'].insert(0, source)
                continue
            ctx.add_section('', [source,
                FilterBar(dataset, frame, filters=[{'type': 'text', 'column': 'id', 'param': 'id', 'label': 'Entity ID'}])])
            ctx.add_section('Orders', [
                KpiRow(dataset_id=dataset, kpis=[{'label': 'Returned value', 'column': 'value', 'agg': 'sum', 'format': 'number'},
                                               {'label': 'Observations', 'agg': 'count', 'format': 'number'}]),
                LineChart(frame, x='day', y='value', title='Daily ' + dataset, dataset_id=dataset),
                DataTable(frame, title='Returned ' + dataset, dataset_id=dataset, sortable=True),
            ])
        details = compute_details(ctx)
        validation = validate_report(ctx, details=details)
        (output / 'diagnostics.json').write_text(json.dumps(details, indent=2, default=str))
        (output / 'validation.json').write_text(json.dumps(validation.to_dict(), indent=2))
        assert validation.summary['warn'] == validation.summary['fail'] == 0, validation.to_dict()
        with ExitStack() as stack:
            if baseline:
                names = ['runtime/live_query.js', 'runtime/filter_engine.js', 'components/live_data_source.js']
                sources = {name: subprocess.check_output(['git', 'show', f'{BASELINE}:trellum/static/js/{name}'], cwd=ROOT, text=True)
                           for name in names}
                historical = lambda name: sources[name] if name in sources else load_js(name)
                stack.enter_context(patch('trellum.rendering.js_runtime.load_js', historical))
                stack.enter_context(patch('trellum.components.live_filterable.load_js', historical))
            render_report(ctx, str(output), auto_refresh=False)
        copy_vendor_tree(output)
    finally:
        ctx.close_connections()
        enable_cache()


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_LIVE_STATE_EVIDENCE', str(tmp_path_factory.mktemp('native-live-state'))))
    # A fresh directory per invocation keeps real native outputs isolated.
    root.mkdir(parents=True, exist_ok=True)
    for kind in ['current', 'baseline']:
        build_report(root / kind, baseline=kind == 'baseline')
    build_report(root / 'scoped', scoped=True)
    return root


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        instance = p.chromium.launch(headless=True)
        yield instance
        instance.close()


def open_report(browser, output, *, width=1280, flags='hosted'):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    page.add_init_script('window._fwShareLink = ' + json.dumps(flags == 'share'))
    errors, requests, pending = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def serve(route):
        assert urlparse(route.request.url).hostname == 'live-state.test', route.request.url
        if route.request.method == 'POST':
            requests.append(route.request.post_data_json)
            pending.append(route)
            return
        path = output / urlparse(route.request.url).path.lstrip('/')
        if path.name == 'index.html':
            # A serving host stamps its supported flags before runtime startup.
            extension = {'nav_html': '', 'scripts': [], 'live_query_url': '/query' if flags in ('hosted', 'share') else ''}
            if flags == 'old-host':
                extension['nav_html'] = '<nav>Host</nav>'
            stamp, _ = host_flag_scripts(extension, 'live-result-contract')
            html = path.read_text().replace('<script>window._fwHasHost=false;</script>', stamp)
            route.fulfill(body=html, content_type='text/html')
        else:
            route.fulfill(status=200 if path.is_file() else 404,
                          body=path.read_bytes() if path.is_file() else b'not found',
                          content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')


    page.route('**/*', serve)
    page.goto('https://live-state.test/index.html', wait_until='networkidle')
    page.wait_for_function("window._fwFilterEngine && window._fwFilterEngine.isReady('orders')")
    for canvas in page.locator('canvas[data-fw-kind]').all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function('id => !!window._chartInstances[id]', arg=canvas.get_attribute('id'))
    page.evaluate('document.fonts.ready')
    page.evaluate('Object.values(window._chartInstances).forEach(c=>{c.stop();c.update("none")})')
    page.evaluate("document.querySelectorAll('.fw-live-dot').forEach(el=>el.style.animation='none')")
    page.evaluate('window.scrollTo(0,0)')
    return page, requests, pending, errors


def state(page, ds='orders'):
    return page.evaluate('ds=>window._fwLiveQuery.getDatasetState(ds)', ds)


def commit(page, value, ds='orders'):
    if ds != 'orders':
        page.evaluate("([ds,value])=>window._fwFilterEngine.setFilter(ds,'fixture','id','text',String(value))", [ds, value])
        return
    control = page.locator(f'.fw-filter-bar[data-fw-ds-id="{ds}"] input[type=text]')
    control.fill(str(value))
    control.press('Enter')


def reply(pending, rows, *, status=200, truncated=False):
    route = pending.pop(0)
    route.fulfill(status=status, json={'columns': COLUMNS, 'rows': rows, 'truncated': truncated}
                  if status == 200 else {'error': 'Fixture lookup failed'})


def wait_requests(page, requests, count):
    # Pump browser events until the network fixture observes the real request.
    for _ in range(100):
        if len(requests) >= count:
            return
        page.wait_for_timeout(10)
    assert len(requests) == count


def observe(page):
    return page.evaluate('''() => {
      const section=[...document.querySelectorAll('.fw-section')].find(s=>s.textContent.includes('Daily orders'));
      const box=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};
      const c=Object.values(window._chartInstances).find(c=>c.canvas.dataset.fwTitle==='Daily orders');
      c.stop();c.update('none');
      return {rows:window._fwFilterEngine.getFiltered('orders'),kpis:[...section.querySelectorAll('.fw-kpi-card')].map(el=>({text:el.innerText,box:box(el)})),
        chart:{box:box(c.canvas),labels:c.data.labels,data:c.data.datasets.map(d=>d.data)},
        controls:[...document.querySelectorAll('.fw-filter-bar input,.fw-filter-bar select,.fw-filter-bar button')].map(el=>({tag:el.tagName,value:el.value,text:el.textContent,disabled:el.disabled})),
        table:section.querySelector('.fw-table').innerText,overflow:document.documentElement.scrollWidth>innerWidth};
    }''')


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_completed_retained_layout_and_theme_match_baseline(reports, browser, width):
    before, requests0, pending0, errors0 = open_report(browser, reports / 'baseline', width=width)
    page, requests, pending, errors = open_report(browser, reports / 'current', width=width)
    try:
        assert state(page)['origin'] == 'snapshot'
        assert state(page, 'empty')['has_result'] is True
        assert state(page, 'empty')['row_count'] == 0
        assert observe(page) == observe(before)
        before.screenshot(path=str(reports / f'baseline-{width}-snapshot.png'), full_page=True)
        page.screenshot(path=str(reports / f'current-{width}-snapshot.png'), full_page=True)
        for target, recorded, work in [(before, requests0, pending0), (page, requests, pending)]:
            commit(target, 2)
            wait_requests(target, recorded, 1)
            reply(work, [[2, f'2026-10-0{i}', v, HOSTILE] for i, v in enumerate([0, 50, 70], 1)])
            target.wait_for_function("window._fwFilterEngine.getFiltered('orders')[0].id===2")
        assert state(page)['displayed_params'] == {'id': 2}
        assert observe(page) == observe(before)
        assert page.locator('.fw-table img').count() == 0
        for target in [before, page]:
            target.evaluate('window.scrollTo(0,0)')
        before.screenshot(path=str(reports / f'baseline-{width}-completed.png'), full_page=True)
        page.screenshot(path=str(reports / f'current-{width}-completed.png'), full_page=True)
        applied = state(page)
        for target, recorded in [(before, requests0), (page, requests)]:
            commit(target, 3)
            wait_requests(target, recorded, 2)
        retained = state(page)
        assert retained['phase'] == 'pending'
        assert retained['requested_params'] == {'id': 3}
        assert retained['displayed_params'] == {'id': 2}
        assert retained['revision'] == applied['revision']
        assert observe(page) == observe(before)
        before.screenshot(path=str(reports / f'baseline-{width}-retained.png'), full_page=True)
        page.screenshot(path=str(reports / f'current-{width}-retained.png'), full_page=True)
        for work in [pending0, pending]:
            reply(work, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').phase==='error'")
        before.wait_for_function("document.querySelector('.fw-live-error')")
        assert observe(page) == observe(before)
        page.evaluate('window.renderAll()')
        assert state(page)['displayed_params'] == {'id': 2}
        assert page.evaluate("window._fwFilterEngine.getFiltered('orders')[0].id") == 2
        page.locator('#fwThemeSelect').select_option('trellum dark')
        assert state(page)['generation'] == applied['generation']
        assert state(page)['displayed_params'] == {'id': 2}
        assert not errors and not errors0
    finally:
        page.close()
        before.close()


def test_native_empty_zero_limited_failure_and_independent_context(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'current')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        reply(pending, [[2, '2026-10-01', 0, HOSTILE]], truncated=True)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        assert state(page)['truncated'] is True
        assert state(page)['row_count'] == 1
        assert observe(page)['rows'][0]['value'] == 0
        commit(page, 99, 'independent')
        wait_requests(page, requests, 2)
        reply(pending, [], status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('independent').phase==='error'")
        assert state(page)['phase'] == 'ready'
        commit(page, 3)
        assert state(page)['displayed_params'] == {'id': 2}
        wait_requests(page, requests, 3)
        reply(pending, [])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').row_count===0")
        assert state(page)['phase'] == 'ready'
        assert state(page)['has_result'] is True
        assert state(page)['displayed_params'] == {'id': 3}
        assert requests[-1] == {'query_id': 'orders', 'params': {'id': 3}}
        assert errors == []
    finally:
        page.close()


@pytest.mark.parametrize('flags', ['standalone', 'share', 'old-host'])
def test_native_unarmed_sources_stay_inert(reports, browser, flags):
    page, requests, _, errors = open_report(browser, reports / 'current', flags=flags)
    try:
        assert page.locator('.fw-filter-bar input[type=text]').first.is_disabled()
        page.evaluate("window._fwFilterEngine.setFilter('orders','script','id','text','2')")
        assert state(page)['requested_params'] == {'id': 2}
        assert state(page)['displayed_params'] == {'id': 1}
        assert state(page)['phase'] == 'ready'
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus().pending') is False
        assert requests == [] and errors == []
    finally:
        page.close()


def test_native_source_replacement_removal_and_consumer_unsubscribe(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'current')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        reply(pending, [[2, '2026-10-01', 120, 'Queried']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        old = state(page)
        page.evaluate('''() => {
          window.__states=[];
          window.__off=window._fwLiveQuery.subscribeDataset('orders',s=>window.__states.push(s));
          window.__off();
          const data=window._reportData;
          window._reportData={...data,_ds_orders:{_cols:['id','day','value','label'],_data:[[1,'2026-10-01',99,'New build']],_dict:{}}};
          window.renderAll();
        }''')
        assert state(page)['generation'] > old['generation']
        assert state(page)['displayed_params'] == {'id': 1}
        assert state(page)['origin'] == 'snapshot'
        assert state(page)['completed_at'] is None
        assert observe(page)['rows'][0]['value'] == 99
        assert state(page)['requested_params'] == {'id': 2}
        wait_requests(page, requests, 2)
        reply(pending, [[2, '2026-10-01', 120, 'New query']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        assert page.evaluate('window.__states.length') == 1
        commit(page, 3)
        wait_requests(page, requests, 3)
        page.evaluate('''() => {
          const [id]=Object.entries(window._reportData.components).find(([,c])=>c.type==='live_data_source'&&c.dataset_id==='orders');
          document.getElementById(id).remove();
        }''')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders')===null")
        assert state(page, 'independent')['has_result'] is True
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus().pending') is False
        assert page.evaluate("window._fwFilterEngine.isLiveDataset('orders')") is False
        page.evaluate('window.renderAll()')
        assert state(page) is None
        assert not errors
    finally:
        page.close()


def test_native_scope_reentry_preserves_controls_and_retires_old_owner(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'scoped')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        reply(pending, [[2, '2026-10-01', 120, 'Queried']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        generation = state(page)['generation']
        page.evaluate('window.renderAll();window.renderAll()')
        assert state(page)['generation'] == generation
        assert state(page)['displayed_params'] == {'id': 2}
        page.locator('[data-scope-key=secondary]').click()
        page.wait_for_function("window._fwLiveQuery.getDatasetState('secondary')!==null")
        assert state(page) is None
        assert state(page, 'secondary')['displayed_params'] == {'id': 1}
        page.locator('[data-scope-key=orders]').click()
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders')!==null")
        assert state(page)['generation'] > generation
        assert state(page)['requested_params'] == {'id': 2}
        assert state(page)['displayed_params'] == {'id': 1}
        wait_requests(page, requests, 2)
        assert requests[-1] == {'query_id': 'orders', 'params': {'id': 2}}
        reply(pending, [[2, '2026-10-01', 121, 'After scope return']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        assert state(page, 'secondary') is None
        assert not errors
    finally:
        page.close()


@pytest.mark.heavy
def test_native_completed_png_pdf_and_capture_admission(reports, browser):
    page, requests, pending, errors = open_report(browser, reports / 'current')
    try:
        commit(page, 2)
        wait_requests(page, requests, 1)
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus().pending') is True
        assert page.evaluate('fw.captureElementForAnalysis(document.querySelector(".fw-section"))') is False
        assert 'live data query is still running' in page.locator('.fw-analysis-error').inner_text()
        page.locator('.fw-analysis-capture-ui').evaluate('el=>el.remove()')
        reply(pending, [[2, '2026-10-01', 0, 'Measured zero']])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').origin==='query'")
        capture = page.evaluate('window._fwLiveQuery.getCaptureStatus()')
        assert capture == {'pending': False, 'error': '', 'revision': state(page)['revision']}
        for method, suffix, magic in [('exportPNG', 'png', b'\x89PNG'), ('exportPDF', 'pdf', b'%PDF')]:
            with page.expect_download(timeout=45000) as download:
                page.evaluate('method=>fw[method]()', method)
            target = reports / ('completed-export.' + suffix)
            download.value.save_as(target)
            assert target.read_bytes().startswith(magic)
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus()') == capture
        assert observe(page)['rows'][0]['value'] == 0
        assert not errors
    finally:
        page.close()


def test_native_missing_snapshot_is_unavailable_not_empty(reports, browser):
    page, requests, _, errors = open_report(browser, reports / 'current', flags='standalone')
    try:
        assert state(page, 'empty')['has_result'] is True
        page.evaluate('''() => {
          const data={...window._reportData};delete data._ds_orders;window._reportData=data;window.renderAll();
        }''')
        missing = state(page)
        assert missing['phase'] == 'unavailable'
        assert missing['has_result'] is False
        assert missing['row_count'] is None
        assert missing['origin'] is None
        assert missing['displayed_params'] is None
        assert missing['completed_at'] is None
        assert not requests and not errors
    finally:
        page.close()


@pytest.mark.heavy
def test_native_http_streamed_body_stale_selection_and_transport_deadline(reports, browser):
    """Real fetch/Response.json streaming; virtual time advances only the deadline."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading

    release = threading.Event()
    started = threading.Event()
    requests = []
    stamp, _ = host_flag_scripts({'nav_html': '', 'scripts': [], 'live_query_url': '/query'}, 'live-result-contract')
    output = reports / 'current'

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output), **kwargs)

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == '/index.html':
                html = output.joinpath('index.html').read_text().replace('<script>window._fwHasHost=false;</script>', stamp).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            else:
                super().do_GET()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            value = body['params']['id']
            payload = json.dumps({'columns': COLUMNS, 'rows': [[value, '2026-10-01', value * 10, 'Streamed']], 'truncated': False}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                if value in (2, 4):
                    self.wfile.write(payload[:12])
                    self.wfile.flush()
                    started.set()
                    if not release.wait(15):
                        return
                    self.wfile.write(payload[12:])
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A client abort need not cancel server-side work.

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.set_default_timeout(10000)
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/index.html', wait_until='networkidle')
        page.wait_for_function("window._fwFilterEngine.isReady('orders')")
        commit(page, 2)
        assert started.wait(5), 'fixture did not send response headers'
        page.wait_for_function("window._fwLiveQuery.getCaptureStatus().pending")
        assert state(page)['displayed_params'] == {'id': 1}
        commit(page, 3)
        release.set()
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').displayed_params.id===3")
        assert [r['params']['id'] for r in requests] == [2, 3]
        assert observe(page)['rows'][0]['id'] == 3
        # A streamed body stays pending after headers and is abortable on the
        # 30s deadline. Clock acceleration avoids a real 30s test wait.
        started.clear()
        release.clear()
        page.clock.install()
        commit(page, 4)
        page.clock.run_for(250)
        # The local server runs independently of virtual browser time.
        assert started.wait(5)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('orders').phase==='pending'")
        page.clock.run_for(30000)
        assert state(page)['phase'] == 'error'
        assert '30 seconds' in state(page)['error']
        assert state(page)['displayed_params'] == {'id': 3}
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus().pending') is False
        release.set()
        page.wait_for_timeout(20)
        assert state(page)['displayed_params'] == {'id': 3}
        page.clock.run_for(60000)
        assert [r['params']['id'] for r in requests] == [2, 3, 4]
    finally:
        release.set()
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
