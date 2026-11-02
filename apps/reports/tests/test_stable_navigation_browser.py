"""Mounted catalog destinations with controlled primary and secondary responses."""
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def wait_for(page, condition):
    for _ in range(500):
        if condition():
            return
        page.wait_for_timeout(10)
    assert condition()


@pytest.fixture
def navigation_pages(login, org_admin, studio):
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"r-{i}", name=f"Report {i}") for i in range(40)
    ] + [Report(studio=studio, slug=f"a-{i}", name=f"Analysis {i}", kind="analysis") for i in range(40)])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    documents = {path: client.get(base + path).content for path in ('/', '/analyses', '/metrics', '/metrics/comparison')}
    for name in ('members', 'invites'):
        path = f'/orgs/{studio.org.slug}/settings/{name}'
        documents[path] = client.get(path).content
    rows = {kind: client.get(base + f'/api/registry?kind={kind}&page=1&limit=50').json() for kind in ('report', 'analysis')}
    fragments = {path: client.get(base + path, HTTP_X_TRELLUM_NAVIGATION='1').content for path in ('/metrics', '/metrics/comparison')}
    return base, documents, rows, fragments


class Routes:
    def __init__(self, page, data):
        self.page, (self.base, self.documents, self.rows, self.fragments) = page, data
        self.registry, self.secondary, self.metrics, self.native = [], [], [], []
        self.hold_registry = self.hold_native = False
        self.errors = []
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.route('http://stable.test/**', self.serve)

    def serve(self, route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        if path.startswith('/static/'):
            target = settings.BASE_DIR / path.removeprefix('/')
            if target.is_file():
                route.fulfill(body=target.read_bytes(), content_type='text/css' if path.endswith('.css') else 'application/javascript')
            else:
                route.fulfill(status=204)
        elif '/api/registry' in path:
            if self.hold_registry:
                self.registry.append(route)
            else:
                self.answer_registry(route)
        elif request.headers.get('x-trellum-navigation') == '1':
            self.metrics.append(route)
        elif path.endswith('/api/me/favorites') or path.endswith('/api/my-subscriptions'):
            self.secondary.append(route)  # these never answer; primary must still finish
        elif request.resource_type == 'document':
            if self.hold_native:
                self.native.append(route)
            elif '/r/' in path:
                route.fulfill(body='<h1>Hosted report</h1>', content_type='text/html')
            else:
                route.fulfill(body=self.documents[path if path in self.documents else path.removeprefix(self.base)], content_type='text/html')
        else:
            route.fulfill(body='{}', content_type='application/json')

    def answer_registry(self, route):
        kind = parse_qs(urlsplit(route.request.url).query)['kind'][0]
        route.fulfill(body=json.dumps(self.rows[kind]), content_type='application/json')

    def open(self, path='/?view=list'):
        self.page.goto('http://stable.test' + self.base + path)
        expect(self.page.locator('#content[data-catalog-ready=true]')).to_be_attached()

    def click(self, name):
        self.page.locator('.tl-console-nav-link').filter(has=self.page.locator('.tl-console-label', has_text=name)).evaluate('(node) => node.click()')


def test_catalog_primary_commit_stale_cancellation_and_report_history(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        routes = Routes(page, navigation_pages)
        routes.open()
        page.evaluate('window.documentIdentity = 284; history.replaceState({...history.state, otherOwner: 17}, "")')
        routes.hold_registry = True
        page.evaluate("scrollTo(0,400)")
        old_height = page.evaluate('document.body.scrollHeight')
        routes.click('Analyses')
        wait_for(page, lambda: routes.registry)
        expect(page.locator('[data-console-catalog]')).to_have_attribute('aria-busy', 'true')
        assert page.locator('#content tr[data-slug]').first.get_attribute('data-slug').startswith('r-')
        assert page.evaluate('document.body.scrollHeight') == old_height
        assert page.evaluate('scrollY') == 400
        stale = routes.registry.pop()
        # Clicking the still-current destination cancels without replacing its rows.
        routes.click('Reports')
        expect(page.locator('[data-console-catalog]')).not_to_have_attribute('aria-busy', 'true')
        routes.click('Analyses')
        wait_for(page, lambda: routes.registry)
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]').first).to_have_attribute('data-slug', 'a-0')
        routes.answer_registry(stale)
        assert page.evaluate('window.documentIdentity') == 284
        assert page.evaluate('history.state.otherOwner') == 17
        assert len(routes.secondary) == 2
        # Filters replace only catalog-owned state; report history returns to this exact list.
        page.locator('#searchInput').fill('Analysis')
        page.wait_for_function("location.search.includes('q=Analysis')")
        page.locator('#content tr[data-slug="a-30"] a.list-title').scroll_into_view_if_needed()
        page.evaluate('scrollBy(0,80)')
        page.wait_for_function('history.state.tlConsoleCatalog?.y === scrollY')
        saved_url, saved_y = page.url, page.evaluate('scrollY')
        page.locator('#content tr[data-slug="a-30"] a.list-title').click()
        expect(page.locator('[data-console-report-host]')).to_be_visible()
        page.evaluate('history.back()')
        expect(page.locator('[data-console-report-host]')).to_be_hidden()
        assert page.url == saved_url
        page.wait_for_function('y => Math.abs(scrollY-y)<3', arg=saved_y)
        page.evaluate('history.forward()')
        expect(page.locator('[data-console-report-host]')).to_be_visible()
        # The host captured the newly committed Analyses path, rather than its mount-time Reports path.
        routes.click('Analyses')
        expect(page.locator('[data-console-report-host]')).to_be_hidden()
        assert page.url == saved_url
        page.evaluate('history.back()')
        wait_for(page, lambda: routes.registry)
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]').first).to_have_attribute('data-slug', 'r-0')
        page.evaluate('history.forward()')
        wait_for(page, lambda: routes.registry)
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#searchInput')).to_have_value('Analysis')
        page.wait_for_function('y => Math.abs(scrollY-y)<3', arg=saved_y)
        # Authorization/session failure keeps the old content and offers native entry, never an empty list.
        routes.click('Reports')
        wait_for(page, lambda: routes.registry)
        routes.registry.pop().fulfill(status=403, body='{}', content_type='application/json')
        expect(page.locator('[data-catalog-navigation-error]')).to_be_visible(timeout=20000)
        assert page.locator('#content tr[data-slug]').count() == 40
        expect(page.locator('#content')).to_have_attribute('data-catalog-ready', 'true')
        assert 'No reports' not in page.locator('#content').inner_text()
        assert not routes.errors
        browser.close()


@pytest.mark.heavy
@pytest.mark.parametrize('width', [1440, 390])
def test_catalog_and_metrics_share_delay_and_minimum_visibility(navigation_pages, width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 900})
        routes = Routes(page, navigation_pages)
        routes.open()
        # Browser clock controls exactly the delay/minimum, independent of CI speed.
        page.clock.install(time=datetime(2030, 1, 1, tzinfo=timezone.utc))
        page.clock.pause_at(datetime(2030, 1, 1, tzinfo=timezone.utc))
        routes.hold_registry = True
        routes.click('Analyses')
        wait_for(page, lambda: routes.registry)
        page.clock.run_for(100)
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]').first).to_have_attribute('data-slug', 'a-0')
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        routes.click('Reports')
        wait_for(page, lambda: routes.registry)
        page.clock.run_for(149)
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        page.clock.run_for(1)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        routes.answer_registry(routes.registry.pop())
        page.clock.run_for(499)
        assert page.locator('#content tr[data-slug]').first.get_attribute('data-slug') == 'a-0'
        page.clock.run_for(1)
        expect(page.locator('#content tr[data-slug]').first).to_have_attribute('data-slug', 'r-0')
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        # Once the minimum has elapsed, readiness still owns completion.
        routes.click('Analyses')
        wait_for(page, lambda: routes.registry)
        page.clock.run_for(1200)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        assert page.locator('#content tr[data-slug]').first.get_attribute('data-slug') == 'r-0'
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]').first).to_have_attribute('data-slug', 'a-0')
        page.goto('http://stable.test' + routes.base + '/metrics')
        page.clock.install(time=datetime(2030, 1, 1, tzinfo=timezone.utc))
        page.clock.pause_at(datetime(2030, 1, 1, tzinfo=timezone.utc))
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<div id=old style=height:1800px>Retained primary</div>'); scrollTo(0,400)")
        page.get_by_role('link', name='Comparison', exact=True).evaluate('(node) => node.click()')
        wait_for(page, lambda: routes.metrics)
        expect(page.locator('#old')).to_be_visible()
        assert page.evaluate('scrollY') == 400
        page.clock.run_for(150)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        routes.metrics.pop().fulfill(body=routes.fragments['/metrics/comparison'], content_type='application/json')
        page.clock.run_for(499)
        expect(page.locator('#old')).to_be_attached()
        page.clock.run_for(1)
        expect(page.locator('#old')).to_have_count(0)
        expect(page.locator('main')).not_to_have_attribute('aria-busy', 'true')
        page.clock.run_for(20)  # settle primary focus/scroll before the next intent
        page.get_by_role('link', name='Overview', exact=True).evaluate('(node) => node.click()')
        wait_for(page, lambda: routes.metrics)
        page.clock.run_for(100)
        routes.metrics.pop().fulfill(body=routes.fragments['/metrics'], content_type='application/json')
        expect(page.locator('main')).not_to_have_attribute('aria-busy', 'true')
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        # A fast fragment can still mount primary content whose readiness arrives later.
        page.clock.run_for(20)
        page.evaluate("""TrellumMetricsWorkspace.mount = function(root, signal) {
            var dispose=function(){}; dispose.ready=new Promise(resolve=>{
                window.completePrimary=resolve; signal.addEventListener('abort',resolve,{once:true});
            }); return dispose;
        }; void 0""")
        page.get_by_role('link', name='Comparison', exact=True).evaluate('(node) => node.click()')
        wait_for(page, lambda: routes.metrics)
        page.clock.run_for(100)
        routes.metrics.pop().fulfill(body=routes.fragments['/metrics/comparison'], content_type='application/json')
        page.wait_for_function('!!window.completePrimary')
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        page.clock.run_for(50)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        page.evaluate('completePrimary()')
        page.clock.run_for(499)
        expect(page.locator('main')).to_have_attribute('aria-busy', 'true')
        page.clock.run_for(1)
        expect(page.locator('main')).not_to_have_attribute('aria-busy', 'true')
        # Hidden surfaces own no delay/minimum timers; disposal releases a waiting completion.
        page.evaluate("""window.feedbackHidden = true;
            Object.defineProperty(document, 'hidden', {configurable:true, get:()=>window.feedbackHidden});
            window.hiddenFeedback = TrellumNavigationFeedback.start({root:document.querySelector('main'), label:'Hidden test'});""")
        page.clock.run_for(1000)
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        page.evaluate("feedbackHidden=false; document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('[data-console-loading]')).to_be_visible()
        page.evaluate("hiddenFeedback.finish().then(()=>{window.feedbackFinished=true; hiddenFeedback.dispose()}); feedbackHidden=true; document.dispatchEvent(new Event('visibilitychange'))")
        page.clock.run_for(1000)
        assert not page.evaluate('!!window.feedbackFinished')
        page.evaluate("feedbackHidden=false; document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        assert not routes.errors
        browser.close()


@pytest.mark.heavy
def test_native_landing_waits_for_catalog_primary_and_rejects_mismatched_metadata(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        routes = Routes(page, navigation_pages)
        page.clock.install()
        page.goto('http://stable.test' + routes.base + '/metrics')
        routes.hold_native = True
        routes.hold_registry = True
        samples = []
        page.expose_function('pendingSample', lambda sample: samples.append(sample))
        page.evaluate("""document.querySelector('main').insertAdjacentHTML('beforeend','<div id=retained style=height:1800px>Keep primary</div>');
            scrollTo(0,400);
            var height=document.body.scrollHeight;
            Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Reports')).click();
            setTimeout(function () { window.pendingSample({height:document.body.scrollHeight, expectedHeight:height,
                y:scrollY, retained:document.querySelector('#retained').getClientRects().length,
                shownAt:JSON.parse(sessionStorage.getItem('trellum-navigation-feedback')).shownAt}); }, 200);""")
        wait_for(page, lambda: routes.native and samples)
        sample = samples.pop()
        assert sample['height'] == sample['expectedHeight'] and sample['y'] == 400 and sample['retained']
        assert sample['shownAt']
        routes.native.pop().fulfill(body=routes.documents['/'], content_type='text/html')
        page.wait_for_url('http://stable.test' + routes.base + '/')
        wait_for(page, lambda: routes.registry)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        # Pause at current wall time, then complete primary before the inherited minimum expires.
        now = page.evaluate('Date.now()')
        page.clock.pause_at(datetime.fromtimestamp(now / 1000, timezone.utc))
        remaining = max(0, sample['shownAt'] + 500 - now)
        assert remaining > 0
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]')).to_have_count(40)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        page.clock.run_for(remaining)
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        assert len(routes.secondary) == 2  # still held, never readiness owners
        # No stale helper survives an unmount, and timing data for another destination is ignored.
        routes.hold_native = False
        routes.hold_registry = False
        page.evaluate("""sessionStorage.setItem('trellum-navigation-feedback', JSON.stringify({
            destination:location.origin+'/s/other/studio/', startedAt:Date.now(), shownAt:Date.now(), label:'Other studio'}))""")
        page.goto('http://stable.test' + routes.base + '/metrics')
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        assert page.evaluate("sessionStorage.getItem('trellum-navigation-feedback')") is None
        assert not routes.errors
        browser.close()


def test_metrics_retains_primary_and_rejects_superseded_or_denied_responses(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        routes = Routes(page, navigation_pages)
        page.goto('http://stable.test' + routes.base + '/metrics')
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<div id=retained style=height:1800px>Primary content</div>'); scrollTo(0,400)")
        height = page.evaluate('document.body.scrollHeight')
        page.get_by_role('link', name='Comparison', exact=True).evaluate('(node) => node.click()')
        wait_for(page, lambda: routes.metrics)
        assert page.evaluate('document.body.scrollHeight') == height
        assert page.evaluate('scrollY') == 400
        expect(page.locator('#retained')).to_be_attached()
        stale = routes.metrics.pop()
        page.evaluate("TrellumNavigation.navigate(location.pathname.replace('/comparison', ''))")
        wait_for(page, lambda: routes.metrics)
        routes.metrics.pop().fulfill(body=routes.fragments['/metrics'], content_type='application/json')
        expect(page.locator('main')).not_to_have_attribute('aria-busy', 'true')
        old = json.loads(routes.fragments['/metrics/comparison'])
        old['html'] += '<p id=obsolete>Superseded page</p>'
        stale.fulfill(body=json.dumps(old), content_type='application/json')
        expect(page.locator('#obsolete')).to_have_count(0)
        assert page.get_by_role('link', name='Overview', exact=True).get_attribute('aria-current') == 'page'
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend','<p id=retained-after-error>Retained on error</p>')")
        page.get_by_role('link', name='Comparison', exact=True).evaluate('(node) => node.click()')
        wait_for(page, lambda: routes.metrics)
        routes.metrics.pop().fulfill(status=401, body='<h1>Login</h1>', content_type='text/html')
        expect(page.locator('main [role=alert]')).to_be_visible()
        expect(page.locator('#retained-after-error')).to_be_attached()
        assert page.get_by_role('link', name='Open page directly').get_attribute('href').endswith('/metrics/comparison')
        assert not routes.errors
        browser.close()


@pytest.mark.heavy
def test_saved_search_beyond_inline_bound_is_primary_destination_content(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        routes = Routes(page, navigation_pages)
        routes.open()
        routes.hold_registry = True
        routes.rows['analysis']['pagination']['total'] = 560
        page.evaluate("""var link=Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Analyses'));
            link.href += '?q=Hidden+analysis&view=list'; link.click();""")
        wait_for(page, lambda: routes.registry)
        routes.answer_registry(routes.registry.pop())
        # Nine further bounded slices, independent of the total beyond the inline ceiling.
        wait_for(page, lambda: len(routes.registry) == 9)
        for route in routes.registry[:]:
            routes.answer_registry(route)
        routes.registry.clear()
        wait_for(page, lambda: routes.registry)
        query = routes.registry.pop()
        assert parse_qs(urlsplit(query.request.url).query)['q'] == ['Hidden analysis']
        assert page.locator('#content tr[data-slug]').count() == 40
        assert page.locator('#content tr[data-slug]').first.get_attribute('data-slug') == 'r-0'
        match = {**routes.rows['analysis']['reports'][0], 'id': 999999, 'slug': 'hidden', 'name': 'Hidden analysis'}
        query.fulfill(body=json.dumps({'reports': [match], 'pagination': {'total': 1}}), content_type='application/json')
        expect(page.locator('#content tr[data-slug]')).to_have_count(1)
        expect(page.locator('#content tr[data-slug]')).to_have_attribute('data-slug', 'hidden')
        assert page.evaluate('window.IS_ANALYSES_PAGE')
        assert not routes.errors
        browser.close()


def test_cancel_navigation_before_first_registry_restores_a_loading_catalog(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        routes = Routes(page, navigation_pages)
        routes.hold_registry = True
        page.goto('http://stable.test' + routes.base + '/?q=Report&view=list')
        wait_for(page, lambda: routes.registry)
        first = routes.registry.pop()
        routes.click('Analyses')
        wait_for(page, lambda: routes.registry)
        superseded = routes.registry.pop()
        routes.click('Reports')
        wait_for(page, lambda: routes.registry)
        routes.answer_registry(routes.registry.pop())
        expect(page.locator('#content tr[data-slug]')).to_have_count(40)
        expect(page.locator('#searchInput')).to_have_value('Report')
        routes.answer_registry(first)
        routes.answer_registry(superseded)
        assert page.locator('#content tr[data-slug]').first.get_attribute('data-slug') == 'r-0'
        assert not page.locator('[data-catalog-navigation-error]').count()
        assert not routes.errors
        browser.close()


def test_selected_favorites_waits_for_its_rows_before_history_readiness(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        routes = Routes(page, navigation_pages)
        page.goto('http://stable.test' + routes.base + '/?tab=_favorites&view=list')
        expect(page.locator('#content')).to_contain_text('Loading favorites')
        expect(page.locator('#content')).not_to_have_attribute('data-catalog-ready', 'true')
        wait_for(page, lambda: len(routes.secondary) == 2)
        favorite = next(route for route in routes.secondary if '/api/me/favorites' in route.request.url)
        org, studio = routes.base.split('/')[2:4]
        favorite.fulfill(body=json.dumps({'favorites': [{'org_slug': org, 'studio_slug': studio,
            'slug': 'r-0', 'report_id': routes.rows['report']['reports'][0]['id']}]}), content_type='application/json')
        expect(page.locator('#content tr[data-slug]')).to_have_count(1)
        expect(page.locator('#content')).to_have_attribute('data-catalog-ready', 'true')
        assert not routes.errors
        browser.close()


@pytest.mark.heavy
def test_organization_navigation_carries_visible_feedback_to_destination(navigation_pages):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        routes = Routes(page, navigation_pages)
        org = routes.base.split('/')[2]
        source = f'/orgs/{org}/settings/members'
        destination = f'/orgs/{org}/settings/invites'
        page.goto('http://stable.test' + source)
        routes.hold_native = True
        records = []
        page.expose_function('saveFeedbackRecord', lambda record: records.append(record))
        page.evaluate("""destination => {
            document.querySelector('a[href="' + destination + '"]').click();
            setTimeout(() => saveFeedbackRecord(JSON.parse(sessionStorage.getItem('trellum-navigation-feedback'))), 200);
        }""", destination)
        wait_for(page, lambda: routes.native and records)
        record = records[0]
        assert record['destination'] == 'http://stable.test' + destination
        assert record['shownAt'] > 0
        routes.native.pop().fulfill(body=routes.documents[destination], content_type='text/html')
        page.wait_for_url('http://stable.test' + destination)
        expect(page.locator('[data-console-loading]')).to_be_visible()
        expect(page.locator('[data-console-loading]')).to_have_count(0)
        assert not routes.errors
        browser.close()
