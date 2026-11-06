"""Read-only administrator discovery in the existing catalog, with layout evidence."""
import json
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.utils import timezone
from playwright.sync_api import expect

from apps.core import roles
from apps.reports.models import Report, ReportFavorite
from apps.reports.tests.test_registry_browser import open_page

pytestmark = pytest.mark.django_db(transaction=True)
ARTIFACTS = Path(django_settings.BASE_DIR) / '.data-test' / 'report-expiry-artifacts'
LABEL = 'Include hidden'


def no_horizontal_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('label,viewport', [
    ('desktop', {'width': 1440, 'height': 1000}),
    pytest.param('mobile', {'width': 390, 'height': 844}, marks=pytest.mark.heavy),
])
def test_admin_discovers_existing_states_and_ordinary_visits_default_off(
    live_server, login, org_admin, member, studio, grant_studio, label, viewport, settings,
):
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    now = timezone.now()
    for slug, state in [
        ('ordinary', {}), ('hidden', {'archived_in_scan': True, 'present_in_scan': False}),
        ('expired', {'expires_at': now}), ('disabled', {'disabled': True}), ('retained', {'data_expired_at': now}),
    ]:
        Report.objects.create(studio=studio, slug=slug, name=slug.title(), category='Discovery', **state)
    base = f'/s/{studio.org.slug}/{studio.slug}'
    client = login(org_admin)
    ReportFavorite.objects.create(user=org_admin, report=Report.objects.get(studio=studio, slug='hidden'))
    grant_studio(member, studio, roles.VIEWER)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with open_page(live_server, client) as page:
        page.set_viewport_size(viewport)
        url = live_server.url + base + '/'
        page.goto(url)
        expect(page.locator('tr[data-slug]')).to_have_count(1)
        toggle = page.get_by_role('switch', name=LABEL)
        expect(toggle).not_to_be_checked()
        expect(page.locator('#discoveryHelp')).to_be_hidden()
        expect(toggle).to_have_attribute('aria-describedby', 'discoveryAccessibleHelp')
        # Keep the existing columns and full-width list layout without depending
        # on development Git history, which is absent from release checkouts.
        # Favorites can replace the table while loading; inspect one DOM snapshot.
        page.wait_for_function("""expected => {
            const table = document.querySelector('.list-table');
            const view = document.querySelector('.list-view');
            if (!table || !view) return false;
            const headers = [...table.querySelectorAll('th')]
                .map(h => h.textContent.replace(/[▲▼]/g, '').trim());
            const width = table.getBoundingClientRect().width;
            return JSON.stringify(headers) === JSON.stringify(expected) && width > 0
                && Math.abs(width - view.getBoundingClientRect().width) <= 1;
        }""", arg=['', '', 'Report', 'Category', 'Studio', 'Status', 'Schedule', 'Last Updated'])
        no_horizontal_overflow(page)
        page.screenshot(path=str(ARTIFACTS / f'{label}-catalog-default.png'), full_page=True)
        toggle.focus()
        page.keyboard.press('Space')
        expect(toggle).to_be_checked()
        expect(page.locator('tr[data-slug]')).to_have_count(6)
        expect(page.locator('#discoveryHelp')).to_be_visible()
        expect(page.locator('#discoveryHelp')).to_contain_text('Admin view')
        expect(page.locator('#discoveryHelp')).to_contain_text('Includes hidden, expired and disabled reports.')
        for slug, status in [('hidden', 'Hidden'), ('expired', 'Expired'), ('disabled', 'Disabled'), ('retained', 'Data expired')]:
            expect(page.locator(f'tr[data-slug="{slug}"] .ui-badge', has_text=status).first).to_be_visible()
        assert page.locator('tr[data-slug="hidden"] a.list-title').count() == 0
        hidden_star = page.locator('tr[data-slug="hidden"] button[title="Remove from favorites"]').first
        expect(hidden_star).to_be_visible()
        hidden_star.click()
        expect(page.locator('tr[data-slug]')).to_have_count(5)
        expect(page.locator('tr[data-slug="hidden"] button[title="Add to favorites"]')).to_be_visible()
        page.locator('#searchInput').fill('hidden')
        expect(page.locator('tr[data-slug]')).to_have_count(1)
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        page.locator('#searchInput').fill('')
        expect(page.locator('tr[data-slug]')).to_have_count(5)
        no_horizontal_overflow(page)
        page.screenshot(path=str(ARTIFACTS / f'{label}-catalog-extra-list.png'), full_page=True)
        page.locator('[data-setview="cards"]').click()
        expect(page.locator('a.card')).to_have_count(5)
        expect(page.locator('a.card[data-slug="disabled"]')).to_contain_text('Disabled')
        expect(page.locator('a.card[data-slug="retained"]')).to_contain_text('Data expired')
        no_horizontal_overflow(page)
        page.screenshot(path=str(ARTIFACTS / f'{label}-catalog-extra-cards.png'), full_page=True)
        page.locator('.discovery-switch').click()
        expect(toggle).not_to_be_checked()
        expect(page.locator('a.card')).to_have_count(1)
        page.locator('.discovery-switch').click()
        expect(toggle).to_be_checked()
        expect(page.locator('a.card')).to_have_count(5)
        page.goto(url)
        expect(page.get_by_role('switch', name=LABEL)).not_to_be_checked()
        expect(page.locator('a.card')).to_have_count(1)
    with open_page(live_server, login(member)) as page:
        page.goto(live_server.url + base + '/')
        expect(page.locator('tr[data-slug]')).to_have_count(1)
        expect(page.get_by_label(LABEL)).to_have_count(0)


def test_switching_drops_old_search_and_load_more_results(live_server, login, org_admin, studio):
    # A small preload exercises pagination without a scale or timing test.
    Report.objects.bulk_create([Report(studio=studio, slug=f'normal-{i:02}', name=f'Normal {i}') for i in range(55)])
    Report.objects.create(studio=studio, slug='extra-hidden', name='Hidden needle', archived_in_scan=True, present_in_scan=False)
    base = f'/s/{studio.org.slug}/{studio.slug}/'
    client = login(org_admin)
    with open_page(live_server, client) as page:
        page.add_init_script('window.addEventListener("DOMContentLoaded", () => { CATALOG_AUTOLOAD = 50; });')
        page.goto(live_server.url + base)
        expect(page.locator('tr[data-slug]')).to_have_count(50)
        page.locator('.discovery-switch').click()
        expect(page.locator('tr[data-slug="extra-hidden"]')).to_be_visible()
        held = []
        def hold(route):
            held.append(route)
        page.route('**/api/registry?*include_unavailable=1*', hold)
        page.get_by_role('button', name='Show more', exact=True).click()
        page.wait_for_function('() => _catalogMoreBusy')
        # Wait for an actual request before invalidating it.
        with page.expect_request('**/api/registry?*include_unavailable=1*'):
            page.locator('#searchInput').fill('needle')
        expect(page.locator('tr[data-slug="extra-hidden"]')).to_be_visible()
        page.locator('.discovery-switch').click()
        page.unroute('**/api/registry?*include_unavailable=1*', hold)
        page.locator('#searchInput').fill('')
        # Release old responses after the ordinary catalog has restarted.
        for route in held:
            try:
                route.fulfill(json={'reports': [{'id': 99999, 'slug': 'stale', 'name': 'Stale hidden'}],
                                    'pagination': {'total': 56}})
            except Exception:
                pass  # aborted by the new selection
        expect(page.locator('tr[data-slug]')).to_have_count(50)
        expect(page.locator('tr[data-slug="stale"]')).to_have_count(0)
        expect(page.locator('tr[data-slug="extra-hidden"]')).to_have_count(0)
        expect(page.locator('.catalog-more')).to_contain_text('Showing 50 of 55')
        page.get_by_role('button', name='Show more', exact=True).click()
        expect(page.locator('tr[data-slug]')).to_have_count(55)


def test_toggle_read_failure_is_not_presented_as_an_empty_catalog(live_server, login, org_admin, studio):
    Report.objects.create(studio=studio, slug='ordinary', name='Ordinary')
    Report.objects.create(studio=studio, slug='hidden', name='Hidden', archived_in_scan=True, present_in_scan=False)
    with open_page(live_server, login(org_admin)) as page:
        page.goto(f'{live_server.url}/s/{studio.org.slug}/{studio.slug}/')
        expect(page.locator('tr[data-slug="ordinary"]')).to_be_visible()
        page.evaluate('CATALOG_RETRY_MS = []')
        def unavailable(route):
            route.fulfill(status=503, json={'error': 'unavailable'})
        pattern = '**/api/registry?*'
        page.route(pattern, unavailable)
        page.locator('.discovery-switch').click()
        expect(page.locator('[data-catalog-navigation-error]')).to_contain_text('This catalog could not load.')
        expect(page.locator('tr[data-slug="ordinary"]')).to_be_visible()
        expect(page.locator('#content')).not_to_contain_text('No reports')
        expect(page.get_by_role('switch', name=LABEL)).not_to_be_checked()
        expect(page.locator('#discoveryHelp')).to_be_hidden()
        page.unroute(pattern, unavailable)
        page.locator('.discovery-switch').click()
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        expect(page.locator('[data-catalog-navigation-error]')).to_have_count(0)
        # A failed opt-out retains the prior rows and their explicit Admin view.
        page.route(pattern, unavailable)
        page.locator('.discovery-switch').click()
        expect(page.locator('[data-catalog-navigation-error]')).to_be_visible()
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        expect(page.get_by_role('switch', name=LABEL)).to_be_checked()
        expect(page.locator('#discoveryHelp')).to_be_visible()
        page.unroute(pattern, unavailable)
        page.locator('.discovery-switch').click()
        expect(page.locator('tr[data-slug]')).to_have_count(1)


@pytest.mark.heavy
@pytest.mark.parametrize('width', [390, 1024, 1280, 1440, 1700])
def test_discovery_keeps_controls_in_place_with_category_overflow(
    live_server, login, org_admin, studio, width,
):
    ordinary = [Report.objects.create(
        studio=studio, slug=f'ordinary-{i}', name=f'Ordinary {i}', category=f'Category {i:02}',
    ) for i in range(4)]
    Report.objects.bulk_create([Report(
        studio=studio, slug=f'hidden-{i}', name=f'Hidden {i}', category=f'Hidden category {i:02}',
        archived_in_scan=True, present_in_scan=False,
    ) for i in range(20)])
    ReportFavorite.objects.create(user=org_admin, report=ordinary[0])
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with open_page(live_server, login(org_admin)) as page:
        page.set_viewport_size({'width': width, 'height': 844})
        page.add_init_script("localStorage.setItem('trellum_portal_recent', JSON.stringify(['ordinary-1']));")
        page.goto(f'{live_server.url}/s/{studio.org.slug}/{studio.slug}/')
        evidence = {}
        for theme, background in [('light', 'rgb(246, 247, 248)'), ('dark', 'rgb(17, 19, 21)')]:
            page.evaluate('(theme) => localStorage.setItem("mgmt-theme", theme)', theme)
            page.reload()
            expect(page.locator('tr[data-slug]')).to_have_count(6)  # Includes favorite and recent sections.
            page.wait_for_function('() => _favoritesReady')
            def geometry():
                return page.evaluate('''() => ({
                    boxes: Object.fromEntries(['#searchInput', '.catalog-discovery', '#catalogToolbar', '#folderTabs', '#listSortSelect']
                        .map(s => [s, document.querySelector(s).getBoundingClientRect().toJSON()])),
                    theme: document.documentElement.dataset.theme,
                    colors: Object.fromEntries(['body', '.folder-bar', '#searchInput', '.list-table', '#listSortSelect']
                        .map(s => { const c = getComputedStyle(document.querySelector(s));
                            return [s, [c.backgroundColor, c.color, c.colorScheme]]; }))
                })''')
            before = geometry()
            assert before['theme'] == theme
            assert before['colors']['body'][0] == background
            page.screenshot(path=str(ARTIFACTS / f'geometry-{width}-{theme}-off.png'))
            toggle = page.get_by_role('switch', name=LABEL)
            toggle.focus()
            page.keyboard.press('Space')
            expect(page.locator('tr[data-slug]')).to_have_count(26)
            expect(toggle).to_be_focused()
            assert page.locator('.discovery-track').evaluate("el => getComputedStyle(el).outlineStyle") != 'none'
            # The checked thumb must remain visible in light mode too; accent
            # ink there has the same color as the enabled track.
            page.wait_for_function('''() => getComputedStyle(document.querySelector('.discovery-track > span'))
                .backgroundColor === 'rgb(255, 255, 255)' ''')
            assert page.locator('.discovery-track').evaluate("el => getComputedStyle(el).backgroundColor") != 'rgb(255, 255, 255)'
            after = geometry()
            assert after['theme'] == before['theme']
            assert after['colors'] == before['colors']
            for selector in before['boxes']:
                for dimension in ('x', 'y', 'width', 'height'):
                    assert abs(before['boxes'][selector][dimension] - after['boxes'][selector][dimension]) <= 1, (
                        width, theme, selector, dimension, before, after,
                    )
            no_horizontal_overflow(page)
            boxes = [box for selector, box in after['boxes'].items() if selector != '#listSortSelect']
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    assert a['right'] <= b['left'] + 1 or b['right'] <= a['left'] + 1 or a['bottom'] <= b['top'] + 1 or b['bottom'] <= a['top'] + 1
            if width >= 768:
                more = page.locator('.folder-more-summary')
                expect(more).to_be_visible()
                more.click()
                items = page.locator('.folder-menu-item')
                assert items.count() > 0
                assert page.locator('.folder-tab[data-cat]').count() + items.count() == 24
                page.locator('.folder-more-filter').fill('Hidden category 19')
                expect(items.filter(has_text='Hidden category 19')).to_be_visible()
                items.filter(has_text='Hidden category 19').click()
                expect(page.locator('.folder-tab.active')).to_contain_text('Hidden category 19')
                # The promoted active category and pinned tabs must also fit the track.
                track = page.locator('#folderTabs').bounding_box()
                for tab in page.locator('#folderTabs > .folder-tab, .folder-more-summary').all():
                    box = tab.bounding_box()
                    assert box['x'] + box['width'] <= track['x'] + track['width'] + 1
                page.locator('[data-folder="all"]').click()
            else:
                expect(page.get_by_role('button', name='Report category')).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / f'geometry-{width}-{theme}-on.png'))
            if width >= 1024:
                for action, padding in [('Collapse sidebar', '64px'), ('Expand sidebar', '240px')]:
                    page.get_by_role('button', name=action, exact=True).click()
                    page.wait_for_function('(padding) => getComputedStyle(document.body).paddingLeft === padding', arg=padding)
                    page.wait_for_function('''() => {
                        const track = document.querySelector('#folderTabs').getBoundingClientRect();
                        return [...document.querySelectorAll('#folderTabs > .folder-tab, .folder-more-summary')]
                            .every(el => el.getBoundingClientRect().right <= track.right + 1);
                    }''')
                    no_horizontal_overflow(page)
            evidence[theme] = {'off': before, 'on': after}
            toggle.focus()
            page.keyboard.press('Space')
            expect(page.locator('tr[data-slug]')).to_have_count(6)
            restored = geometry()
            assert restored == before
        (ARTIFACTS / f'geometry-{width}.json').write_text(json.dumps(evidence, indent=2))


def test_hidden_selection_survives_cancel_and_resets_on_mounted_navigation(
    live_server, login, org_admin, studio,
):
    Report.objects.create(studio=studio, slug='ordinary', name='Ordinary')
    Report.objects.create(studio=studio, slug='hidden', name='Hidden', archived_in_scan=True, present_in_scan=False)
    Report.objects.create(studio=studio, slug='analysis', name='Analysis', kind=Report.KIND_ANALYSIS)
    base = f'/s/{studio.org.slug}/{studio.slug}'
    with open_page(live_server, login(org_admin)) as page:
        # Enter through Analyses: its mounted template must later offer the
        # administrator switch when Reports becomes the destination.
        page.goto(live_server.url + base + '/analyses')
        expect(page.locator('tr[data-slug="analysis"]')).to_be_visible()
        expect(page.get_by_role('switch', name=LABEL)).to_have_count(0)
        page.locator(f'.tl-console-nav-link[href="{base}/"]').click()
        expect(page.locator('tr[data-slug="ordinary"]')).to_be_visible()
        toggle = page.get_by_role('switch', name=LABEL)
        expect(toggle).not_to_be_checked()
        held = []
        def hold(route):
            held.append(route)
        page.route('**/api/registry?*include_unavailable=1*', hold)
        toggle.focus()
        with page.expect_request('**/api/registry?*include_unavailable=1*'):
            page.keyboard.press('Space')
        page.wait_for_function('() => document.getElementById("content").dataset.catalogReady === "false"')
        # A slow selection retains the ordinary rows until its primary read.
        expect(page.locator('tr[data-slug="ordinary"]')).to_be_visible()
        expect(page.locator('tr[data-slug="hidden"]')).to_have_count(0)
        page.unroute('**/api/registry?*include_unavailable=1*', hold)
        for route in held:
            route.continue_()
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        held.clear()
        page.route('**/api/registry?kind=analysis*', hold)
        page.locator(f'.tl-console-nav-link[href="{base}/analyses"]').click()
        expect(page.get_by_role('button', name='Cancel loading', exact=True)).to_be_visible()
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        page.get_by_role('button', name='Cancel loading', exact=True).click()
        expect(toggle).to_be_checked()
        expect(page.locator('tr[data-slug="hidden"]')).to_be_visible()
        page.unroute('**/api/registry?kind=analysis*', hold)
        for route in held:
            try:
                route.continue_()
            except Exception:
                pass  # cancellation aborted the read
        page.locator(f'.tl-console-nav-link[href="{base}/analyses"]').click()
        expect(page.locator('tr[data-slug="analysis"]')).to_be_visible()
        expect(page.get_by_role('switch', name=LABEL)).to_have_count(0)
        page.go_back()
        expect(page.locator('tr[data-slug="ordinary"]')).to_be_visible()
        expect(page.get_by_role('switch', name=LABEL)).not_to_be_checked()
        expect(page.locator('tr[data-slug="hidden"]')).to_have_count(0)
