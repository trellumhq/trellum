"""Key journeys: catalog, metrics, analytics/experiments, operations and scoped viewer (L2 routes)."""
import pytest

from .key_journeys import VARIANTS, bind, browser_session, record_metrics

pytestmark = pytest.mark.portal_e2e
S = '/s/alpha/studio-0'
HEAVY = pytest.mark.heavy
DESKTOP_ONLY = [pytest.param('chromium-desktop')]
BOTH = [pytest.param('chromium-desktop'), pytest.param('webkit-mobile', marks=HEAVY)]


def add_catalog_reports():
    """40 extra reports so the catalog needs real scrolling."""
    from apps.reports.models import Report
    from apps.studios.models import Studio
    studio = Studio.objects.get(org__slug='alpha', slug='studio-0')
    Report.objects.bulk_create([Report(studio=studio, slug=f'journey-{n:02}', name=f'Journey report {n:02}') for n in range(40)])


@pytest.mark.parametrize('variant', BOTH)
def test_catalog_search_open_back(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    journey.database(add_catalog_reports)
    with browser_session(journey, 'org-admin', spec, condition='key-catalog-back') as (page, context):
        page.goto(origin + S + '/')
        rows = page.locator('#content tr[data-slug]')
        expect(rows.first).to_be_visible()
        total = rows.count()
        assert total > 40  # the whole catalog is one scrolling page
        search = page.locator('#searchInput')
        expect(search).to_be_visible()  # the catalog's own field, on phones too
        search.fill('report')
        expect(page.locator('#content tr[data-slug]').first).to_be_visible()
        assert page.locator('#content tr[data-slug]').count() <= total
        journey.checkpoint('catalog-search')
        built = page.get_by_role('link', name='Report 0', exact=True)
        expect(built).to_be_visible()
        journey.checkpoint('catalog-paging')
        built.scroll_into_view_if_needed()
        page.evaluate('window.scrollBy(0, 160)')
        page.wait_for_timeout(300)  # let the host save the catalog scroll
        saved_y = page.evaluate('window.scrollY')
        assert saved_y > 100
        listing = page.url
        built.click()
        host = page.locator('[data-console-report-host]')
        expect(host).to_be_visible()
        frame = page.frame_locator('[data-console-report-frame]')
        expect(frame.get_by_role('heading', name='Baseline report')).to_be_visible()
        assert '/r/report-0000' in page.url
        journey.checkpoint('report-open')
        page.go_back()
        page.wait_for_url(listing)
        expect(page.locator('#searchInput')).to_have_value('report')
        expect(page.get_by_role('link', name='Report 0', exact=True)).to_be_visible()
        journey.checkpoint('back-restored')
        page.wait_for_function('y => Math.abs(window.scrollY - y) <= 24', arg=saved_y)
        journey.checkpoint('scroll-restored')
        bind(journey, 'route-139', 'GET', None, 'org-admin', spec, ())
        record_metrics(journey, page)


@pytest.mark.parametrize('variant', DESKTOP_ONLY)
def test_metrics_detail_comparison(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-metrics') as (page, context):
        page.goto(origin + S + '/metrics')
        cards = page.locator('.met-card')
        expect(cards).to_have_count(20)
        # Definitions are usable immediately; report usage fills in by itself.
        expect(page.locator('[data-metric-usage]').first).to_contain_text('report', timeout=20000)
        page.locator('#metSearch').fill('Metric 1')
        expect(page.locator('#metCount')).to_have_text('11 of 20')
        expect(page.locator('.met-card:not([hidden])')).to_have_count(11)
        assert page.get_by_role('button', name='Search metrics').count() == 0
        journey.checkpoint('definitions')
        journey.checkpoint('search')
        bind(journey, 'route-146', 'GET', None, 'org-admin', spec, ())
        card = page.locator('.met-card', has=page.get_by_role('button', name='Metric 10', exact=True))
        card.get_by_role('button', name='Metric 10', exact=True).click()
        modal = page.locator('#metModal')
        expect(modal).to_be_visible()
        expect(page.locator('#metModalTitle')).to_have_text('Metric 10')
        page.locator('#metModalClose').click()
        expect(modal).to_be_hidden()
        page.goto(origin + card.get_attribute('data-details-url'))
        page.wait_for_url(lambda url: '/claims' in url)
        expect(page.get_by_role('heading', name='Metric 10 usage')).to_be_visible()
        journey.checkpoint('metric-detail')
        bind(journey, 'route-324', 'GET', None, 'org-admin', spec, ())
        page.get_by_role('link', name='Back to Metrics').click()
        page.get_by_role('link', name='Comparison').click()
        page.wait_for_url(lambda url: url.endswith('/metrics/comparison'))
        expect(page.get_by_role('link', name='Comparison')).to_have_attribute('aria-current', 'page')
        # The seeded Metrics report is not built, so the page states that instead of a workspace.
        expect(page.locator('main, .settings-content, body').first).to_contain_text("workspace")
        journey.checkpoint('comparison')
        bind(journey, 'route-147', 'GET', None, 'org-admin', spec, ())
        record_metrics(journey, page)


@pytest.mark.parametrize('variant', DESKTOP_ONLY)
def test_analytics_experiments_progressive(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-analytics-experiments') as (page, context):
        page.goto(origin + S + '/analytics')
        rows = page.locator('#analyticsTable tbody tr')
        expect(rows).to_have_count(20)
        # Secondary enhancement: absolute timestamps become relative once the script runs.
        expect(page.locator('.analytics-last-viewed', has_text='Not viewed yet')).to_have_count(20)
        # Up to a few hundred reports: header sort and the filter box work in the browser.
        page.locator('#analyticsTable th[data-sort=views]').click()
        expect(page.locator('#analyticsTable th[data-sort=views]')).to_have_attribute('aria-sort', 'ascending')
        page.locator('#analyticsSearch').fill('Report 1')
        expect(page.locator('#analyticsTable tbody tr:not([hidden])')).to_have_count(11)
        journey.checkpoint('primary-rows')
        journey.checkpoint('secondary-settled')
        bind(journey, 'route-145', 'GET', None, 'org-admin', spec, ())
        # Experiments reuse the same pattern: tiles and rows first, then the status pills.
        page.goto(origin + S + '/experiments')
        expect(page.locator('.ui-stat', has_text='Running').locator('.num')).to_have_text('6')
        table_rows = page.locator('table.ui-table tbody tr')
        expect(table_rows).to_have_count(6)
        page.get_by_role('link', name='Running').click()
        page.wait_for_url(lambda url: 'status=running' in url)
        expect(table_rows).to_have_count(6)
        expect(page.locator('[role=status]', has_text='loading')).to_have_count(0)
        journey.checkpoint('primary-rows')
        bind(journey, 'route-149', 'GET', None, 'org-admin', spec, ())
        record_metrics(journey, page)


@pytest.mark.parametrize('variant', BOTH)
def test_operations_filter(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-operations-filter') as (page, context):
        page.goto(origin + S + '/operations')
        # Filters hide rows in place; only the visible ones count.
        rows = page.locator('[data-ops-row]:visible')
        expect(rows).to_have_count(20, timeout=20000)
        failed = page.locator('[data-ops-filter=error]')
        failed.scroll_into_view_if_needed()
        failed.click()
        expect(rows).to_have_count(2)
        expect(rows.first.locator('.ops-status')).to_contain_text('Failed')
        journey.checkpoint('filter')
        page.locator('[data-ops-filter=all]').click()
        expect(rows).to_have_count(20)
        search = page.locator('#searchInput')
        expect(search).to_be_visible()
        search.fill('report-0003')
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('Controlled fixture failure')
        journey.checkpoint('search')
        journey.checkpoint('status-contract')
        bind(journey, 'route-144', 'GET', None, 'org-admin', spec, ())
        bind(journey, 'route-346', 'GET', None, 'org-admin', spec, ())
        record_metrics(journey, page)


def selected_report():
    from apps.orgs.models import PermissionGroupGrant
    grant = PermissionGroupGrant.objects.get(group__org__slug='alpha', group__name='selected', studio__slug='studio-0')
    return grant.report_grants.select_related('report').get().report


@pytest.mark.parametrize('variant', DESKTOP_ONLY)
def test_selected_scope_viewer(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    allowed = journey.database(lambda: (lambda r: (r.pk, r.slug, r.name))(selected_report()))
    with browser_session(journey, 'group-selected', spec, condition='key-selected-scope') as (page, context):
        page.goto(origin + S + '/')
        rows = page.locator('#content tr[data-slug]')
        expect(rows).to_have_count(1)
        expect(rows.first).to_have_attribute('data-slug', allowed[1])
        other = 'report-0003' if allowed[1] != 'report-0003' else 'report-0004'
        assert other not in page.content() and 'Controlled fixture failure' not in page.content()
        journey.checkpoint('scope-visible')
        # Search cannot widen the scope.
        page.locator('#searchInput').fill('report')
        page.wait_for_url(lambda url: 'q=report' in url)
        expect(page.locator('#content tr[data-slug]')).to_have_count(1)
        journey.checkpoint('scope-hidden')
        # Direct URLs, the JSON catalog and details for unauthorised reports are denied, and nothing leaks.
        other_id = journey.database(lambda: __import__('apps.reports.models', fromlist=['Report']).Report.objects.get(slug=other, studio__slug='studio-0', studio__org__slug='alpha').pk)
        for path in (f'{S}/r/{other}/', f'{S}/r/{other}/index.html', f'{S}/api/catalog/details/{other_id}'):
            response = context.request.get(origin + path)
            assert response.status in (403, 404), (path, response.status)
            assert 'Report 3' not in response.text() and 'Baseline report' not in response.text()
        catalog = context.request.get(origin + S + '/api/registry?kind=report&page=1&q=report').json()
        assert [row['slug'] for row in catalog['reports']] == [allowed[1]], catalog
        journey.checkpoint('direct-url-denied')
        bind(journey, 'route-139', 'GET', None, 'group-selected', spec, ())
        for path in (S + '/metrics', S + '/operations'):
            assert context.request.get(origin + path).status in (403, 404), path
        journey.checkpoint('scope-denied')
        bind(journey, 'route-146', 'GET', None, 'group-selected', spec, ())
