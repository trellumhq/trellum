"""Real Django/PG permissions and generated pages; synthetic warehouse rows only."""
from pathlib import Path
from socketserver import ThreadingMixIn
import threading
from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler

import pandas as pd
import pytest
import yaml
from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.test import Client

from apps.core import roles
from apps.reports.models import Report
from apps.reports.scan import sync_studio_metrics
from apps.reports.source_summary import reconcile
from trellum.metrics import invalidate_metrics_cache
from trellum.metrics_report import MetricsReport
from trellum.report import ReportContext
from trellum.rendering.html_builder import render_report

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(params=('chromium', 'webkit'))
def metrics_browser(request, workspace_http):
    # Finish synchronous Django fixture setup before Playwright starts its loop;
    # close that loop before Django transaction teardown.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = getattr(playwright, request.param).launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def workspace_http(studio_tree, member, grant_studio, monkeypatch, settings, request):
    settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
    grant_studio(member, studio_tree, roles.VIEWER)
    project = studio_tree.project_root; project.mkdir(parents=True, exist_ok=True)
    metric = {'agg': 'sum', 'column': 'value', 'dataset': 'daily',
              'description': 'Controlled synthetic daily observations for real workspace browser flows.', 'tags': ['Revenue']}
    count = getattr(request, 'param', 56)
    metrics = [{**metric, 'name': f'metric_{i:04}', 'label': f'Metric {i:04}',
                **({'time_agg': 'last'} if count == 1000 and i == 0 else {})} for i in range(count)]
    metrics.extend({'name': f'unbound_{i:04}', 'label': f'Unbound {i:04}',
                    'owner': 'late-owner-0029' if i == 29 else 'owner'} for i in range(1000 if count == 1000 else 30))
    (project / 'metrics.yaml').write_text(yaml.safe_dump({'version': 1, 'datasets': {
        'daily': {'provider': 'fixture:daily', 'dimensions': ['platform', 'region'] if count == 1000 else ['platform'], 'time': {'column': 'date', 'grain': 'day'}}}, 'metrics': metrics}))
    monkeypatch.setattr('trellum.project._project_root_override', str(project))
    invalidate_metrics_cache(); sync_studio_metrics(studio_tree)
    output = studio_tree.output_dir / 'metrics'
    context = ReportContext({'name': 'Metrics', 'tags': ['metrics']}, 'metrics', str(output))
    rows = pd.DataFrame([{'date': day.strftime('%Y-%m-%d'), 'platform': f'Platform {i}', 'region': f'Region {i % 2}', 'value': 1 + i}
        for day in pd.date_range('2026-08-01', periods=60) for i in range(4)])
    context.metrics = lambda *args, **kwargs: rows.copy()
    MetricsReport().generate(context); render_report(context, auto_refresh=False)
    report = Report.objects.create(studio=studio_tree, slug='metrics', name='Metrics', tags=['metrics'])
    assert reconcile(report.pk)['status'] == 'ready'
    client = Client(); client.force_login(member)
    held = threading.Event(); release = threading.Event(); state = {'delay': False, 'requests': [], 'request_urls': []}
    application = StaticFilesHandler(get_wsgi_application())
    def app(environ, start_response):
        state['requests'].append(environ['PATH_INFO'])
        state['request_urls'].append(environ['PATH_INFO'] + ('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else ''))
        if state['delay'] and '_metrics-workspace-' in environ['PATH_INFO'] and environ['PATH_INFO'].endswith('.json'):
            held.set(); release.wait(8)
        response = application(environ, start_response)
        try:
            yield from response
        finally:
            if hasattr(response, 'close'):
                response.close()
            connections.close_all()
    class Server(ThreadingMixIn, WSGIServer):
        daemon_threads = True
    class Quiet(WSGIRequestHandler):
        def log_message(self, *args):
            pass
    server = make_server('127.0.0.1', 0, app, server_class=Server, handler_class=Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    yield {'base': base, 'prefix': f'/s/{studio_tree.org.slug}/{studio_tree.slug}',
        'cookie': {'name': settings.SESSION_COOKIE_NAME, 'value': client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': base},
        'held': held, 'release': release, 'state': state, 'output': output, 'last': count - 1}
    release.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)
    invalidate_metrics_cache()


def open_context(browser, server, *, native=False, mobile=False):
    context = browser.new_context(java_script_enabled=not native, viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 900})
    context.add_cookies([server['cookie']])
    page = context.new_page()
    return context, page


@pytest.mark.parametrize('mobile', [False, True])
def test_native_pages_and_enhanced_selection_persist_across_history(metrics_browser, workspace_http, mobile):
    context, page = open_context(metrics_browser, workspace_http, mobile=mobile)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison')
        page.get_by_text('Metrics ready.', exact=True).wait_for()
        frame = page.locator('#metWorkspace').element_handle().content_frame()
        assert frame.locator('.fw-metric-block').count() == 25
        assert frame.locator('#fwMetricsWorkspace input[type=checkbox]').count() == 25
        frame.locator('#fwMetricsWorkspace summary').click()
        frame.locator('input[value="metric-metric_0000"]').uncheck()
        frame.get_by_role('link', name='Page 2', exact=True).click()
        frame.wait_for_url('**-page-2.html*')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        frame.get_by_role('link', name='Page 1', exact=True).click()
        frame.wait_for_url('**-page-1.html*')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        assert not frame.locator('input[value="metric-metric_0000"]').is_checked()
        frame.locator('#fwMetricsWorkspace summary').click()
        frame.locator('#fwMetricsSearch').fill('Metric 0055')
        result = frame.locator('#fwMetricsSearchResults a')
        assert result.count() == 1
        result.click(); frame.wait_for_url('**-page-3.html*')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        assert frame.locator('#metric-metric_0055').count() == 1
        assert frame.locator('.fw-metric-block').count() == 6
        assert frame.evaluate('Object.keys(window._chartInstances || {}).length') <= 25
        assert frame.evaluate('document.querySelectorAll("*").length') <= 1100
        # A native joint-history back moves the child document, so the parent
        # does not emit the top-level load event awaited by Page.go_back().
        page.evaluate('history.back()'); frame.wait_for_url('**-page-1.html*')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        assert not frame.locator('input[value="metric-metric_0000"]').is_checked()
    finally:
        context.close()
    native, page = open_context(metrics_browser, workspace_http, native=True, mobile=mobile)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison')
        frame = page.locator('#metWorkspace').element_handle().content_frame()
        assert frame.locator('.fw-metric-block').count() == 25
        frame.get_by_role('link', name='Page 3', exact=True).click()
        frame.wait_for_url('**-page-3.html*')
        assert frame.locator('#metric-metric_0055').count() == 1
        assert frame.locator('.fw-metric-block').count() == 6
    finally:
        native.close()


def test_offpage_focused_comparison_instantiates_one_chart_per_frame(metrics_browser, workspace_http):
    context, page = open_context(metrics_browser, workspace_http)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + f'/metrics/compare?a=metric_0000&b=metric_{workspace_http["last"]:04}')
        try:
            page.wait_for_function("document.querySelectorAll('iframe[data-ready=\"1\"]').length === 2", timeout=15000)
        except Exception:
            states = [frame.evaluate("""() => ({url:location.href,state:window._fwWorkspaceState,
                disposed:window._fwWorkspaceDisposed,data:!!window._reportData,body:document.body.className,
                charts:Object.keys(window._chartInstances||{}).length,work:window._fwWorkspaceWork,
                hidden:document.hidden,readyState:document.readyState})""") for frame in page.frames[1:]]
            pytest.fail(str({'frames':states,'cards':page.locator('.mc-card').all_inner_texts()}))
        for handle in page.locator('iframe').element_handles():
            frame = handle.content_frame()
            assert frame.evaluate('Object.keys(window._chartInstances || {}).length') == 1
            assert frame.evaluate('document.querySelectorAll("*").length') <= 1100
        page.locator('#mcFrom').fill('2026-08-05'); page.locator('#mcTo').fill('2026-08-20')
        page.get_by_role('button', name='Apply dates to both').click()
        page.wait_for_function('location.search.includes("from=2026-08-05")')
        assert 'Filters: 2026-08-05 – 2026-08-20' in page.locator('.mc-range').first.inner_text()
    finally:
        context.close()


@pytest.mark.parametrize('workspace_http', [1000], indirect=True)
def test_large_offpage_focused_workspace_readiness(metrics_browser, workspace_http):
    test_offpage_focused_comparison_instantiates_one_chart_per_frame(metrics_browser, workspace_http)


def test_navigation_cancels_old_workspace_before_delayed_payload_finishes(metrics_browser, workspace_http):
    workspace_http['state']['delay'] = True
    context, page = open_context(metrics_browser, workspace_http)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison', wait_until='domcontentloaded')
        assert workspace_http['held'].wait(5)
        page.get_by_role('link', name='Overview', exact=True).click()
        page.wait_for_url('**/metrics')
        assert page.locator('#metWorkspace').count() == 0
        workspace_http['release'].set(); page.wait_for_timeout(300)
        assert page.locator('#metWorkspace').count() == 0
        assert page.get_by_role('heading', name='Metrics', exact=True).is_visible()
    finally:
        workspace_http['release'].set(); context.close()


def test_definition_search_sort_and_csv_include_offpage_metadata(metrics_browser, workspace_http):
    context, page = open_context(metrics_browser, workspace_http)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison')
        page.get_by_text('Metrics ready.', exact=True).wait_for()
        frame = page.locator('#metWorkspace').element_handle().content_frame()
        table_id = frame.evaluate('window._fwMetricsWorkspace.definitionId')
        table = frame.locator('#' + table_id)
        assert table.locator('tbody tr').count() == 25
        assert table.get_by_text('unbound_0029', exact=True).count() == 0
        next_button = table.get_by_role('button', name='Next definitions')
        next_button.focus(); next_button.press('Enter')
        assert table.locator('tbody tr').count() == 5
        assert table.get_by_role('button', name='Previous definitions').evaluate('el => el === document.activeElement')
        table.get_by_role('button', name='Previous definitions').press('Enter')
        assert table.locator('tbody tr').count() == 25
        assert table.get_by_role('button', name='Next definitions').evaluate('el => el === document.activeElement')
        frame.locator('[data-table-search="' + table_id + '"]').fill('late-owner-0029')
        table.get_by_text('unbound_0029', exact=True).wait_for()
        assert table.locator('tbody tr').count() == 1
        frame.locator('[data-table-search="' + table_id + '"]').fill('')
        frame.wait_for_function('(id) => document.getElementById(id).querySelectorAll("tbody tr").length === 25', arg=table_id)
        table.get_by_role('columnheader', name='metric').click()
        assert table.locator('tbody tr').first.inner_text().startswith('unbound_0029')
        with page.expect_download() as download:
            frame.locator('[data-csv-target="' + table_id + '"]').click()
        text = Path(download.value.path()).read_text(encoding='utf-8-sig')
        assert 'unbound_0000' in text and 'unbound_0029' in text and 'late-owner-0029' in text
        assert len(text.splitlines()) == 31
        assert table.locator('tbody tr').count() == 25
    finally:
        context.close()


def test_second_origin_workspace_is_visible_unconfirmed_retry_and_cleanup(metrics_browser):
    """Actual two-origin HTTP redirect, not a fabricated contentDocument value."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    script = (Path(settings.BASE_DIR) / 'static/metrics_workspace.js').read_text()
    state = {'redirects': 0}
    class Edge(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b'<h1>External served workspace</h1><script>parent.postMessage({type:"resize",height:99999},"*")</script>')
        def log_message(self, *args): pass
    edge = ThreadingHTTPServer(('127.0.0.1', 0), Edge)
    class Parent(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/legacy':
                self.send_response(200); self.end_headers(); self.wfile.write(b'<h1>Legacy HTML, chart readiness unknown</h1>'); return
            if self.path == '/redirect':
                state['redirects'] += 1
                self.send_response(302); self.send_header('Location', f'http://127.0.0.1:{edge.server_port}/workspace'); self.end_headers(); return
            self.send_response(200); self.end_headers()
            self.wfile.write(('''<div data-workspace-status></div><iframe id="metWorkspace" data-workspace-version="1" src="/redirect"></iframe>
                <script>window.TrellumNavigation={};window.controller=new AbortController();</script><script>'''
                + script + '''</script><script>window.cleanup=TrellumMetricsWorkspace.mount(document,controller.signal);</script>''').encode())
        def log_message(self, *args): pass
    parent = ThreadingHTTPServer(('127.0.0.1', 0), Parent)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (edge, parent)]
    for thread in threads: thread.start()
    context = metrics_browser.new_context(); page = context.new_page()
    try:
        page.goto(f'http://127.0.0.1:{parent.server_port}/')
        page.get_by_text('Metrics opened on another origin; chart readiness cannot be confirmed.', exact=False).wait_for()
        assert page.locator('#metWorkspace').is_visible()
        assert page.locator('#metWorkspace').evaluate('el => el.style.height') == '700px'
        assert page.get_by_text('Metrics ready.', exact=True).count() == 0
        page.clock.install(); page.clock.fast_forward(13000)
        assert page.locator('#metWorkspace').is_visible()
        # Nothing to retry: the page opened, only its readiness cannot be read.
        assert page.get_by_role('button', name='Retry metrics').count() == 0
        page.get_by_text('Metrics opened on another origin; chart readiness cannot be confirmed.', exact=False).wait_for()
        assert state['redirects'] == 1
        page.evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
        page.clock.fast_forward(13000)
        assert page.locator('#metWorkspace').is_visible()
        page.evaluate('controller.abort();cleanup();controller=new AbortController();cleanup=TrellumMetricsWorkspace.mount(document,controller.signal)')
        page.clock.fast_forward(13000)
        assert page.locator('#metWorkspace').is_visible()
        assert page.get_by_text('Metrics ready.', exact=True).count() == 0
        page.evaluate('controller.abort();cleanup()')
        page.locator('#metWorkspace').evaluate("el => el.src='/redirect'")
        page.wait_for_timeout(100)
        assert page.locator('#metWorkspace').evaluate('el => el.style.height') == '700px'
        assert page.get_by_text('Metrics ready.', exact=True).count() == 0
        page.evaluate('controller=new AbortController();document.getElementById("metWorkspace").dataset.workspaceVersion="0";cleanup=TrellumMetricsWorkspace.mount(document,controller.signal);document.getElementById("metWorkspace").src="/legacy"')
        page.get_by_text('Metrics opened; chart readiness cannot be confirmed.', exact=False).wait_for()
        assert page.locator('#metWorkspace').is_visible()
        assert page.get_by_text('Metrics ready.', exact=True).count() == 0
        page.evaluate('document.getElementById("metWorkspace").dataset.workspaceVersion="1";document.getElementById("metWorkspace").src="about:blank"')
        page.wait_for_function('document.getElementById("metWorkspace").contentWindow.location.href === "about:blank"')
        page.clock.fast_forward(13000)
        assert page.locator('#metWorkspace').is_hidden()
        status_text = page.locator('[data-workspace-status]').inner_text()
        assert status_text.startswith('Metrics unavailable; retrying'), status_text
        assert page.get_by_role('button', name='Retry metrics').count() == 0
    finally:
        context.close()
        for server in (parent, edge): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=2)


@pytest.mark.parametrize('failure', ['removed-selection', 'replaced-build'])
def test_obsolete_workspace_recovers_to_root_without_loading_chart_payload(metrics_browser, workspace_http, failure):
    context, page = open_context(metrics_browser, workspace_http)
    payloads = []
    try:
        if failure == 'replaced-build':
            def changed_meta(route):
                response = route.fetch()
                meta = response.json(); meta['metrics_workspace']['build'] = 'f' * 32
                route.fulfill(response=response, json=meta)
            context.route('**/_meta.json*', changed_meta)
        page.on('request', lambda request: payloads.append(request.url) if '-page-' in request.url and '.json' in request.url else None)
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison')
        frame = page.locator('#metWorkspace').element_handle().content_frame()
        if failure == 'removed-selection':
            frame.goto(frame.url + ('&' if '?' in frame.url else '?') + 'only=metric-removed')
        frame.wait_for_function('window._fwWorkspaceState === "failed"')
        recovery = frame.get_by_role('link', name='Open latest metric workspace')
        from urllib.parse import urlsplit
        assert urlsplit(recovery.get_attribute('href')).path.endswith('/index.html')
        assert 'only=' not in recovery.get_attribute('href')
        assert frame.evaluate('Object.keys(window._chartInstances || {}).length') == 0
        if failure == 'replaced-build':
            assert payloads == []
            context.unroute('**/_meta.json*')
        assert page.get_by_role('button', name='Retry metrics').count() == 0
        page.get_by_text('Metrics ready.', exact=True).wait_for(timeout=15000)
        assert page.locator('#metWorkspace').is_visible()
        assert 'only=' not in page.locator('#metWorkspace').get_attribute('src')
    finally:
        context.close()


@pytest.mark.parametrize('target', ['?only=metric-%6detric_0055&from=2026-08-05', '#%6detric-metric_0055'])
def test_offpage_route_precedes_old_document_bootstrap_and_preserves_local_links(metrics_browser, workspace_http, target):
    context, page = open_context(metrics_browser, workspace_http)
    try:
        workspace_http['state']['requests'].clear(); workspace_http['state']['request_urls'].clear()
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/r/metrics/index.html' + target)
        page.wait_for_url('**-page-3.html*')
        page.wait_for_function('window._fwWorkspaceState === "ready"')
        assert page.locator('#metric-metric_0055').count() == 1
        requests = workspace_http['state']['requests']
        destination = next(i for i, path in enumerate(requests) if path.endswith('-page-3.html'))
        before = requests[:destination]
        assert not any(path.endswith('-catalog.json') or '-page-' in path and path.endswith('.json') for path in before)
        assert not any('/vendor/' in path or '/fonts/' in path or '/static/' in path for path in before), before
        assert sum(path.endswith('-catalog.json') for path in requests) == 1
        if target.startswith('?'):
            destination_url = next(url for url in workspace_http['state']['request_urls'] if url.split('?', 1)[0].endswith('-page-3.html'))
            assert 'from=2026-08-05' in destination_url
            assert 'only=metric-' in page.url
            assert page.evaluate('Object.keys(window._chartInstances || {}).length') == 1
        else:
            assert page.url.endswith(target)
        # Direct file routing uses the same immutable sibling, independent of
        # a portal URL and before file-fetch/browser CORS limitations apply.
        context.route('https://**', lambda route: route.abort())
        page.goto(workspace_http['output'].joinpath('index.html').as_uri() + target)
        page.wait_for_url('file://**-page-3.html*')
        assert Path(page.url.split('?', 1)[0].split('#', 1)[0].removeprefix('file://')).parent == workspace_http['output']
    finally:
        context.close()


def test_lazy_chart_uses_latest_rapid_filter_without_rendering_hidden_selection(metrics_browser, workspace_http):
    context, page = open_context(metrics_browser, workspace_http)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/r/metrics/index.html')
        page.wait_for_function('window._fwWorkspaceState === "ready"')
        assert page.evaluate('Object.keys(window._chartInstances || {}).length') < 25
        assert page.evaluate('''() => Object.entries(window._reportData.components).filter(([id,c]) => {
            var el=document.getElementById(id), rect=el && el.getBoundingClientRect();
            return c.type==='chart' && el.getClientRects().length && rect.top < innerHeight+200;
        }).every(([id]) => !!window._chartInstances[id])''')
        chart_id = page.evaluate('''() => Object.entries(window._reportData.components).find(([id,c]) => c.type==='chart' && document.getElementById(id)?.closest('.fw-metric-block')?.id==='metric-metric_0024')?.[0]''')
        assert chart_id
        page.locator('#fwMetricsWorkspace summary').click()
        page.locator('input[value="metric-metric_0024"]').uncheck()
        page.evaluate('''() => {
            for (var max of ['2026-08-05','2026-08-20','2026-08-15'])
                window.fw.filterEngine.setFilter('daily','rapid-probe','date','range',{min:'2026-08-05',max:max+'\\uffff'});
        }''')
        page.locator('#metric-metric_0023').scroll_into_view_if_needed()
        page.wait_for_timeout(100)
        assert page.evaluate('id => !window._chartInstances[id]', chart_id)
        page.get_by_role('button', name='Show all', exact=True).click()
        page.locator('#metric-metric_0024').scroll_into_view_if_needed()
        page.wait_for_function('id => !!window._chartInstances[id]', arg=chart_id)
        labels = page.evaluate('id => window._chartInstances[id].data.labels', chart_id)
        assert labels and min(str(label)[:10] for label in labels) >= '2026-08-05' and max(str(label)[:10] for label in labels) <= '2026-08-15'
        assert page.evaluate('Object.keys(window._chartInstances).length') <= 25
    finally:
        context.close()


def test_offpage_definition_hash_scroll_uses_catalog_anchor_and_preserves_user_position(metrics_browser, workspace_http):
    import json
    context, page = open_context(metrics_browser, workspace_http)
    try:
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/comparison')
        page.get_by_text('Metrics ready.', exact=True).wait_for()
        frame = page.locator('#metWorkspace').element_handle().content_frame()
        frame.locator('#fwMetricsWorkspace summary').click()
        frame.locator('#fwMetricsSearch').fill('Unbound 0029')
        frame.locator('#fwMetricsSearchResults a').click()
        frame.wait_for_url('**-page-2.html*#definition-unbound_0029')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        target = frame.locator('#definition-unbound_0029')
        assert target.is_visible() and 'Unbound 0029' in target.inner_text()
        box = target.bounding_box()
        assert box and 0 <= box['y'] < page.viewport_size['height']
        # A later rerender must not repeat the initial hash scroll.
        frame.evaluate('window.scrollTo(0,0)')
        page.evaluate('window.scrollTo(0,0)')
        frame.evaluate('window.renderAll()')
        frame.wait_for_timeout(250)
        assert page.evaluate('scrollY') == 0
        page.evaluate('history.back()'); frame.wait_for_url('**/index.html*')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        page.evaluate('history.forward()'); frame.wait_for_url('**-page-2.html*#definition-unbound_0029')
        frame.wait_for_function('window._fwWorkspaceState === "ready"')
        page.wait_for_timeout(150)
        assert page.evaluate('scrollY') == 0
    finally:
        context.close()
    native, page = open_context(metrics_browser, workspace_http, native=True)
    try:
        marker = json.loads((workspace_http['output'] / '_meta.json').read_text())['metrics_workspace']
        name = f'_metrics-workspace-{marker["build"]}-page-2.html'
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/r/metrics/' + name + '#definition-unbound_0029')
        row = page.locator('#definition-unbound_0029')
        assert row.is_visible() and 'Unbound 0029' in row.inner_text()
        assert page.locator('noscript tbody tr').count() == 5
    finally:
        native.close()
