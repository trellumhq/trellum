"""Native binder and consumers; intercepted local transport, no DOM renderer."""
from __future__ import annotations

import json
import base64
from io import BytesIO
import mimetypes
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import pytest

from trellum.data.live_query_guard import check_sql_safe, coerce_params, inject_limit, shape_rows
from trellum.rendering.js_runtime import host_flag_scripts
from trellum.testing.test_live_result_context import build_native

DATASET = 'live_result_rows'
COLUMNS = ['selection', 'day', 'platform', 'value']


@pytest.fixture(scope='module')
def reports(tmp_path_factory):
    root = Path(os.environ.get('TRELLUM_LIVE_CONTEXT_EVIDENCE', str(tmp_path_factory.mktemp('native-live-context'))))
    for name in ['current', 'opt-out', 'baseline']:
        build_native(root / name, opt_out=name != 'current', historical=name == 'baseline')
    return root


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def open_native(browser, output, *, width=1280, flags='hosted', dark=False):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    page.set_default_timeout(10000)
    errors, requests, pending = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('window._fwShareLink=' + json.dumps(flags == 'share'))

    def route(request):
        assert urlparse(request.request.url).hostname == 'live-context.test'
        if request.request.method == 'POST':
            requests.append(request.request.post_data_json)
            pending.append(request)
            return
        target = output / urlparse(request.request.url).path.lstrip('/')
        if target.name == 'index.html':
            stamp, _ = host_flag_scripts({'nav_html': '', 'scripts': [],
                'live_query_url': '/query' if flags in ('hosted', 'share') else ''}, 'live-result-examples')
            request.fulfill(body=target.read_text().replace('<script>window._fwHasHost=false;</script>', stamp), content_type='text/html')
        else:
            request.fulfill(status=200 if target.is_file() else 404,
                body=target.read_bytes() if target.is_file() else b'missing',
                content_type=mimetypes.guess_type(target)[0] or 'application/octet-stream')
    page.route('**/*', route)
    page.goto('https://live-context.test/index.html', wait_until='networkidle')
    page.wait_for_function("window._fwFilterEngine && window._fwFilterEngine.isReady('live_result_rows')")
    page.evaluate('document.fonts.ready')
    if dark:
        page.locator('#fwThemeSelect').select_option('trellum dark')
    page.evaluate("document.querySelectorAll('.fw-live-dot').forEach(el=>el.style.animation='none')")
    return page, requests, pending, errors


def context(page, dataset=DATASET, section='#sec-returned-observations'):
    return page.locator(section + ' .fw-live-result-context[data-fw-dataset="' + dataset + '"]')


def select(page, selection):
    page.locator('[data-fw-filter-bar][data-fw-ds-id="live_result_rows"] button').filter(has_text=selection).click()


def wait_requests(page, pending, count=2):
    for _ in range(120):
        if len(pending) >= count:
            return
        page.wait_for_timeout(10)
    assert len(pending) == count


def fulfill(pending, *, query=None, rows=None, truncated=False, status=200):
    targets = [route for route in pending if query is None or route.request.post_data_json['query_id'] == query]
    assert targets
    for route in targets:
        pending.remove(route)
        route.fulfill(status=status, json={'columns': COLUMNS, 'rows': rows or [], 'truncated': truncated}
                      if status == 200 else {'error': '<img src=x onerror="window.__injected=1"> Synthetic failure'})


def execute_native(pending, output):
    """Run the real manifest against the named temporary SQLite database."""
    manifest = json.loads((output / '_live_queries.json').read_text())
    # Artifact manifest is the same input the supported development host uses.
    queries = manifest.get('queries', manifest)
    conn = sqlite3.connect(output / 'synthetic.sqlite')
    try:
        for route in list(pending):
            request = route.request.post_data_json
            declaration = queries[request['query_id']]
            assert check_sql_safe(declaration['sql']) is None
            params = coerce_params(declaration['params'], request['params'])
            cursor = conn.execute(inject_limit(declaration['sql']), params)
            rows, truncated = shape_rows(cursor.fetchall())
            pending.remove(route)
            route.fulfill(json={'columns': [column[0] for column in cursor.description], 'rows': rows, 'truncated': truncated})
    finally:
        conn.close()


def result_values(page):
    section = page.locator('#sec-returned-observations')
    section.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('#sec-returned-observations .fw-table tbody')")
    return {'kpis': section.locator('.fw-kpi-card').all_inner_texts(),
            'table': section.locator('.fw-table').inner_text()}


def geometry(page):
    # Positions may shift by the context block; consumer dimensions and each
    # card's arrangement within its row must remain exactly unchanged.
    return page.evaluate('''() => {
      const box=el=>{const r=el.getBoundingClientRect();return [r.width,r.height]};
      return [...document.querySelectorAll('.fw-section')].map(section=>({
        cards:[...section.querySelectorAll('.fw-kpi-card')].map(box),
        rows:[...section.querySelectorAll('.fw-kpi-grid')].map(row=>{
          const r=row.getBoundingClientRect();return [...row.children].map(el=>{
            const c=el.getBoundingClientRect();return [c.x-r.x,c.y-r.y,c.width,c.height]})}),
        charts:[...section.querySelectorAll('canvas[data-fw-kind]')].map(box),
        tables:[...section.querySelectorAll('.fw-table')].map(box),
        controls:[...section.querySelectorAll('input,button,select')].map(el=>[el.tagName,el.value,el.textContent,el.disabled])
      }));
    }''')


def render_tables(page):
    for table in page.locator('[data-fw-kind="DataTable"]').all():
        table.scroll_into_view_if_needed()
        page.wait_for_function('id=>!!document.getElementById(id).querySelector("table")', arg=table.get_attribute('id'))
    for canvas in page.locator('canvas[data-fw-kind]').all():
        canvas.scroll_into_view_if_needed()
        page.wait_for_function('id=>!!window._chartInstances[id]', arg=canvas.get_attribute('id'))
    page.evaluate("Object.values(window._chartInstances).forEach(c=>{c.stop();c.update('none')});window.scrollTo({top:0,behavior:'instant'})")


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_optout_baseline_parity_and_context_geometry(reports, browser, width):
    pages = [open_native(browser, reports / name, width=width) for name in ['baseline', 'opt-out', 'current']]
    try:
        for page, _, _, _ in pages:
            render_tables(page)
        for theme in ['trellum light', 'trellum dark']:
            for page, _, _, _ in pages:
                page.locator('#fwThemeSelect').select_option(theme)
                render_tables(page)
            baseline, optout, current = [item[0] for item in pages]
            assert optout.locator('.fw-live-result-context').count() == 0
            assert baseline.locator('.fw-live-result-context').count() == 0
            assert geometry(current) == geometry(optout) == geometry(baseline)
            assert result_values(current) == result_values(optout) == result_values(baseline)
            evidence = {}
            for name, (page, _, _, errors) in zip(['baseline', 'opt-out', 'current'], pages):
                page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
                page.mouse.move(0, 0)
                image = page.screenshot(path=str(reports / f'{name}-{width}-{theme.replace(" ", "-")}.png'), full_page=True, animations='disabled')
                if name == 'baseline':
                    baseline_image = image
                elif name == 'opt-out':
                    from PIL import Image, ImageChops
                    difference = ImageChops.difference(Image.open(BytesIO(image)), Image.open(BytesIO(baseline_image)))
                    # Chromium can differ by one antialiasing color level
                    # between otherwise identical rasterized controls.
                    assert max(channel[1] for channel in difference.getextrema()) <= 1
                evidence[name] = page.evaluate('''() => ({
                    height:document.documentElement.scrollHeight,overflow:document.documentElement.scrollWidth>innerWidth,
                    context:[...document.querySelectorAll('.fw-live-result-context')].map(el=>({dataset:el.dataset.fwDataset,height:el.getBoundingClientRect().height,text:el.innerText}))
                })''')
                evidence[name]['geometry'] = geometry(page)
                assert not evidence[name]['overflow']
                assert errors == []
            (reports / f'geometry-{width}-{theme.replace(" ", "-")}.json').write_text(json.dumps(evidence, indent=2))
    finally:
        for page, _, _, _ in pages:
            page.close()


def test_native_grouping_lazy_nested_scope_and_actual_query(reports, browser):
    page, requests, pending, errors = open_native(browser, reports / 'current')
    try:
        assert context(page).count() == 1
        assert 'Build snapshot · displayed: selection=Current' in context(page).inner_text()
        assert '3 snapshot rows · completeness unknown' in context(page).inner_text()
        assert page.locator('#sec-compiled-rows .fw-live-result-context').count() == 0
        assert page.locator('#sec-nested-owners > .fw-live-result-context').count() == 1
        assert page.locator('#sec-nested-owners .fw-section > .fw-live-result-context').count() == 1
        lazy = page.locator('#sec-lazy-table-owner')
        assert lazy.locator('.fw-table tbody').count() == 0
        assert lazy.locator('.fw-live-result-context').count() == 1
        assert lazy.locator('.fw-live-result-context').evaluate('el=>el.nextElementSibling.classList.contains("fw-data-table-header")')
        local = context(page, 'live_result_platform', '#sec-local-platform-subset')
        assert 'platform=iOS' in local.inner_text()
        assert '2 of 3 displayed live-parent rows' in local.inner_text()
        select(page, 'Alternative')
        wait_requests(page, pending)
        assert {request['query_id'] for request in requests} == {'live_result_example', 'independent_result'}
        execute_native(pending, reports / 'current')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').displayed_params.selection==='Alternative'")
        assert 'Query result · displayed: selection=Alternative' in context(page).inner_text()
        assert 'response received ' in context(page).inner_text()
        assert '3 returned rows' in context(page).inner_text()
        assert result_values(page)['kpis'] == ['RETURNED VALUE (USD)\n$120.00', 'OBSERVATIONS\n3']
        assert 'selection=Alternative' in context(page, 'independent_result', '#sec-independent-observations').inner_text()
        select(page, 'Empty')
        wait_requests(page, pending)
        execute_native(pending, reports / 'current')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').row_count===0")
        assert 'Valid empty returned result (0 rows)' in context(page).inner_text()
        assert 'No displayed result' not in context(page).inner_text()
        assert errors == []
    finally:
        page.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_native_pending_failure_zero_limited_scope_csv_and_retained_values(reports, browser, width):
    page, requests, pending, errors = open_native(browser, reports / 'current', width=width, dark=width == 390)
    try:
        costs = {}
        def measure(phase):
            costs[phase] = context(page).evaluate('''el=>({height:el.getBoundingClientRect().height,
                marginBottom:parseFloat(getComputedStyle(el).marginBottom),text:el.innerText})''')
        initial = result_values(page)
        measure('snapshot')
        select(page, 'Alternative')
        wait_requests(page, pending)
        assert result_values(page) == initial
        assert 'Loading requested: selection=Alternative · retaining displayed result.' in context(page).inner_text()
        assert 'displayed: selection=Current' in context(page).inner_text()
        measure('pending')
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(reports / f'pending-context-{width}.png'), full_page=True)
        assert page.evaluate('window._fwLiveQuery.getCaptureStatus().pending')
        fulfill(pending, status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').phase==='error'")
        assert result_values(page) == initial
        assert 'Request failed for selection=Alternative' in context(page).inner_text()
        assert 'displayed: selection=Current' in context(page).inner_text()
        assert context(page).locator('img').count() == 0
        measure('failure')
        # Return to the displayed selection clears the failure without a request.
        select(page, 'Current')
        assert 'Request failed' not in context(page).inner_text()
        select(page, 'Alternative')
        wait_requests(page, pending)
        fulfill(pending, query='live_result_example', rows=[['Alternative', '2026-10-01', 'iOS', 0], ['Alternative', '2026-10-02', 'Web', 100]], truncated=True)
        fulfill(pending, query='independent_result', rows=[['Alternative', '2026-10-01', 'iOS', 77]])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').truncated===true")
        assert 'Limited to 2 returned rows; associated aggregates cover only these rows.' in context(page).inner_text()
        local = context(page, 'live_result_platform', '#sec-local-platform-subset')
        assert 'Limited to 2 returned rows' in local.inner_text()
        assert '1 of 2 displayed live-parent rows' in local.inner_text()
        assert '$0.00' in page.locator('#sec-local-platform-subset .fw-kpi-grid').inner_text()
        page.locator('#sec-local-platform-subset').get_by_role('button', name='Web', exact=True).click()
        assert 'platform=Web' in local.inner_text()
        assert '$100.00' in page.locator('#sec-local-platform-subset .fw-kpi-grid').inner_text()
        assert 'Limited' not in context(page, 'independent_result', '#sec-independent-observations').inner_text()
        render_tables(page)
        table = page.locator('#sec-returned-observations .fw-table')
        table.locator('th').last.click()  # first numeric sort is descending
        with page.expect_download() as download:
            page.locator('#sec-returned-observations .fw-csv-btn').click()
        target = reports / f'matching-returned-rows-{width}.csv'
        download.value.save_as(target)
        csv = target.read_text(encoding='utf-8-sig')
        assert csv.splitlines() == ['day,platform,value', '2026-10-02,Web,100', '2026-10-01,iOS,0']
        assert 'Limited' not in csv
        with page.expect_download() as download:
            page.locator('#sec-local-platform-subset .fw-csv-btn').click()
        local_csv = reports / f'matching-local-subset-{width}.csv'
        download.value.save_as(local_csv)
        assert local_csv.read_text(encoding='utf-8-sig').splitlines() == ['day,platform,value', '2026-10-02,Web,100']
        measure('limited')
        page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        page.screenshot(path=str(reports / f'limited-context-{width}.png'), full_page=True)
        (reports / f'context-costs-{width}.json').write_text(json.dumps(costs, indent=2))
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize('flags', ['standalone', 'share', 'old-host'])
def test_native_snapshot_unknown_missing_and_unarmed(reports, browser, flags):
    page, requests, _, errors = open_native(browser, reports / 'current', flags=flags)
    try:
        assert page.locator('[data-fw-ds-id="live_result_rows"] button').first.is_disabled()
        assert 'Build snapshot' in context(page).inner_text()
        page.evaluate('''() => {
            const cfg=Object.values(window._reportData.components).find(c=>c.type==='live_data_source'&&c.dataset_id==='live_result_rows');
            cfg.live.defaults={};window.renderAll();
        }''')
        assert 'displayed: unknown' in context(page).inner_text()
        assert 'selection=Current' not in context(page).inner_text().split('\n')[0]
        page.evaluate('''() => {
            window._reportData={...window._reportData,_ds_live_result_rows:{_cols:['selection','day','platform','value'],_data:[],_dict:{}}};window.renderAll();
        }''')
        assert 'Valid empty snapshot (0 rows)' in context(page).inner_text()
        page.evaluate('''() => {
            const data={...window._reportData};delete data._ds_live_result_rows;
            window._reportData=data;window.renderAll();
        }''')
        assert 'No displayed result' in context(page).inner_text()
        assert '0 rows' not in context(page).inner_text()
        assert requests == [] and errors == []
    finally:
        page.close()


def test_native_context_lifecycle_reentry_replace_remove_and_unsubscribe(reports, browser):
    page, requests, pending, errors = open_native(browser, reports / 'current')
    try:
        # Isolate this helper's subscription ownership. Other cases keep the
        # Line presenter enabled to check both presenters together.
        page.evaluate('''() => {
          Object.values(window._reportData.components).forEach(c=>delete c.line_result_presentation);
          window.renderAll();
          const binder=window._fwLiveQuery,original=binder.subscribeDataset;
          window.__contextActive=0;
          binder.subscribeDataset=function(id,callback) {
            window.__contextActive++;let active=true;
            const off=original(id,callback);
            return function(){if(active){active=false;window.__contextActive--;off()}};
          };
          window.__configs=Object.values(window._reportData.components).filter(c=>c.live_context===true);
          window.__configs.forEach(c=>delete c.live_context);window.renderAll();
          window.__configs.forEach(c=>c.live_context=true);window.renderAll();
        }''')
        count = page.locator('.fw-live-result-context').count()
        assert page.evaluate('window.__contextActive') == count
        page.evaluate('''() => {
          window.__announcements=[];
          new MutationObserver(records=>window.__announcements.push(...records.map(r=>r.target.textContent)))
            .observe(document.querySelector('#sec-returned-observations .fw-live-result-context [role=status]'),{childList:true});
        }''')
        for _ in range(2):
            page.evaluate('window.renderAll()')
            page.locator('#fwThemeSelect').select_option('trellum dark')
            assert page.locator('.fw-live-result-context').count() == count
            assert page.evaluate('window.__contextActive') == count
        assert page.evaluate('window.__announcements.length') == 0
        # Moving one consumer to an existing local child scope splits this
        # Section's ownership; restoring it joins the original group again.
        page.evaluate('''() => {
          const table=document.querySelector('#sec-returned-observations [data-fw-kind=DataTable]');
          window.__tableConfig=window._reportData.components[table.id];
          window.__tableConfig.dataset_id='live_result_platform';window.renderAll();
        }''')
        assert page.locator('#sec-returned-observations .fw-live-result-context').count() == 2
        page.evaluate("window.__tableConfig.dataset_id='live_result_rows';window.renderAll()")
        assert page.locator('#sec-returned-observations .fw-live-result-context').count() == 1
        assert page.evaluate('window.__contextActive') == count
        guarded = page.evaluate('''() => {
          const engine=window._fwFilterEngine;
          engine.addScopedChild('live_result_platform','live_result_platform');
          window._fwLiveResultContext.attach();
          const count=document.querySelectorAll('#sec-local-platform-subset .fw-live-result-context').length;
          engine.addScopedChild('live_result_platform','live_result_rows');
          window._fwLiveResultContext.attach();return count;
        }''')
        assert guarded == 0
        # Re-registering a scoped engine dataset restores only its own listener.
        page.evaluate('''() => {
          window._fwFilterEngine.removeDataset('live_result_platform');window.renderAll();
        }''')
        page.locator('#sec-local-platform-subset').get_by_role('button', name='Web', exact=True).click()
        assert 'platform=Web' in context(page, 'live_result_platform', '#sec-local-platform-subset').inner_text()
        page.locator('#sec-returned-observations').evaluate('el=>el.hidden=true')
        page.locator('#sec-returned-observations').evaluate('el=>el.hidden=false')
        select(page, 'Alternative')
        wait_requests(page, pending)
        execute_native(pending, reports / 'current')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').origin==='query'")
        generation = page.evaluate("window._fwLiveQuery.getDatasetState('live_result_rows').generation")
        page.evaluate('''() => {
          window._reportData={...window._reportData,_ds_live_result_rows:{_cols:['selection','day','platform','value'],
              _data:[['Current','2026-10-01','iOS',99]],_dict:{}}};window.renderAll();
        }''')
        assert page.evaluate("window._fwLiveQuery.getDatasetState('live_result_rows').generation") > generation
        assert 'Build snapshot · displayed: selection=Current' in context(page).inner_text()
        assert 'Loading requested: selection=Alternative' in context(page).inner_text()
        wait_requests(page, pending)
        fulfill(pending, rows=[['Alternative', '2026-10-01', 'iOS', 120]])
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').origin==='query'")
        # Consumer deletion must release exactly the vanished group's lease.
        page.locator('#sec-returned-observations').evaluate('el=>el.remove()')
        page.wait_for_function('window.__contextActive===' + str(count - 1))
        # Source removal retires only its contexts, then the same native root
        # can register again without leaving a dead binder subscription.
        page.evaluate('''() => {
          const [id,cfg]=Object.entries(window._reportData.components).find(([,c])=>c.type==='live_data_source'&&c.dataset_id==='live_result_rows');
          window.__source={id,cfg,node:document.getElementById(id),parent:document.getElementById(id).parentNode};
          window.__source.node.remove();
        }''')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows')===null")
        page.wait_for_function("document.querySelectorAll('.fw-live-result-context[data-fw-dataset=live_result_rows]').length===0")
        assert context(page, 'independent_result', '#sec-independent-observations').count() == 1
        page.evaluate('''() => {
          const source=window.__source;source.parent.appendChild(source.node);
          window._fwRenderers.live_data_source(source.id,source.cfg);
        }''')
        page.wait_for_function("document.querySelector('#sec-nested-owners > .fw-live-result-context')")
        assert 'Build snapshot' in page.locator('#sec-nested-owners > .fw-live-result-context').inner_text()
        # Replacing an individual consumer's marker removes its membership, not
        # another table's context; the block dies with its last member.
        page.evaluate('''() => {
          Object.values(window._reportData.components).forEach(c=>delete c.live_context);
          window.renderAll();
        }''')
        page.wait_for_function('window.__contextActive===0')
        assert page.locator('.fw-live-result-context').count() == 0
        assert page.evaluate("Object.values(window._fwFilterEngine._subscribers).flat().filter(s=>s.id.startsWith('live-result-context-')).length") == 0
        assert not errors
    finally:
        page.close()


@pytest.mark.heavy
def test_native_completed_section_png_pdf_and_pending_error_capture_gate(reports, browser):
    page, requests, pending, errors = open_native(browser, reports / 'current')
    try:
        render_tables(page)
        select(page, 'Alternative')
        wait_requests(page, pending)
        assert page.evaluate('fw.captureElementForAnalysis(document.querySelector("#sec-returned-observations"))') is False
        assert 'still running' in page.locator('.fw-analysis-error').inner_text()
        page.locator('.fw-analysis-capture-ui').evaluate('el=>el.remove()')
        fulfill(pending, status=400)
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').phase==='error'")
        assert page.evaluate('fw.captureElementForAnalysis(document.querySelector("#sec-returned-observations"))') is False
        assert 'query failed' in page.locator('.fw-analysis-error').inner_text()
        page.locator('.fw-analysis-capture-ui').evaluate('el=>el.remove()')
        select(page, 'Current')
        select(page, 'Alternative')
        wait_requests(page, pending)
        execute_native(pending, reports / 'current')
        page.wait_for_function("window._fwLiveQuery.getDatasetState('live_result_rows').phase==='ready'")
        render_tables(page)
        assert 'Query result' in context(page).inner_text()
        payload = page.locator('#sec-returned-observations').evaluate('el=>fw.captureElementSnapshotForAnalysis(el)')
        (reports / 'completed-section.png').write_bytes(base64.b64decode(payload['image']['data_base64']))
        (reports / 'completed-section-source.json').write_text(json.dumps(payload['source'], indent=2))
        for method, suffix, args in [('exportPNG', 'png', ''), ('exportPDF', 'pdf', '')]:
            with page.expect_download(timeout=45000) as download:
                page.evaluate('fw.' + method + '(' + args + ')')
            target = reports / (method + '.' + suffix)
            download.value.save_as(target)
            assert target.read_bytes().startswith(b'\x89PNG' if suffix == 'png' else b'%PDF')
        assert not errors
    finally:
        page.close()
