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
def workspace_http(studio_tree, member, grant_studio, monkeypatch, settings):
    settings.ALLOWED_HOSTS = ['127.0.0.1', 'testserver']
    grant_studio(member, studio_tree, roles.VIEWER)
    project = studio_tree.project_root; project.mkdir(parents=True, exist_ok=True)
    metric = {'agg': 'sum', 'column': 'value', 'dataset': 'daily',
              'description': 'Controlled synthetic daily observations for real workspace browser flows.', 'tags': ['Revenue']}
    metrics = [{**metric, 'name': f'metric_{i:04}', 'label': f'Metric {i:04}'} for i in range(56)]
    metrics.extend({'name': f'unbound_{i:04}', 'label': f'Unbound {i:04}',
                    'owner': 'late-owner-0029' if i == 29 else 'owner'} for i in range(30))
    (project / 'metrics.yaml').write_text(yaml.safe_dump({'version': 1, 'datasets': {
        'daily': {'provider': 'fixture:daily', 'dimensions': ['platform'], 'time': {'column': 'date', 'grain': 'day'}}}, 'metrics': metrics}))
    monkeypatch.setattr('trellum.project._project_root_override', str(project))
    invalidate_metrics_cache(); sync_studio_metrics(studio_tree)
    output = studio_tree.output_dir / 'metrics'
    context = ReportContext({'name': 'Metrics', 'tags': ['metrics']}, 'metrics', str(output))
    rows = pd.DataFrame([{'date': day.strftime('%Y-%m-%d'), 'platform': f'Platform {i}', 'value': 1 + i}
        for day in pd.date_range('2026-08-01', periods=60) for i in range(4)])
    context.metrics = lambda *args, **kwargs: rows.copy()
    MetricsReport().generate(context); render_report(context, auto_refresh=False)
    report = Report.objects.create(studio=studio_tree, slug='metrics', name='Metrics', tags=['metrics'])
    assert reconcile(report.pk)['status'] == 'ready'
    client = Client(); client.force_login(member)
    held = threading.Event(); release = threading.Event(); state = {'delay': False}
    application = StaticFilesHandler(get_wsgi_application())
    def app(environ, start_response):
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
        'held': held, 'release': release, 'state': state}
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
        page.goto(workspace_http['base'] + workspace_http['prefix'] + '/metrics/compare?a=metric_0000&b=metric_0055')
        page.wait_for_function("document.querySelectorAll('iframe[data-ready=\"1\"]').length === 2")
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
        page.get_by_role('button', name='Retry metrics').click()
        page.get_by_text('Metrics opened on another origin; chart readiness cannot be confirmed.', exact=False).wait_for()
        assert state['redirects'] == 2
        page.evaluate('controller.abort();cleanup()')
        page.locator('#metWorkspace').evaluate("el => el.src='/redirect'")
        page.wait_for_timeout(100)
        assert page.locator('#metWorkspace').evaluate('el => el.style.height') == '700px'
        assert page.get_by_text('Metrics ready.', exact=True).count() == 0
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
        page.get_by_role('button', name='Retry metrics').click()
        page.get_by_text('Metrics ready.', exact=True).wait_for()
        assert page.locator('#metWorkspace').is_visible()
        assert 'only=' not in page.locator('#metWorkspace').get_attribute('src')
    finally:
        context.close()
