"""The sidebar studio and organization switchers and the assistant picker."""
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.studios.models import Studio

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _route_handlers_use_the_orm(monkeypatch):
    # Playwright route handlers run in its event loop, which gets its own
    # database connection: committed rows and the async-unsafe override are
    # both required for the handlers to render authenticated pages.
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def serve_browser(browser, client, *, width=1440, failure=None):
    context = browser.new_context(viewport={'width': width, 'height': 900})
    def handle(route):
        url = urlsplit(route.request.url)
        if url.path.startswith('/static/'):
            asset = settings.BASE_DIR / 'static' / url.path.removeprefix('/static/')
            if asset.is_file():
                route.fulfill(path=str(asset), content_type='text/css' if asset.suffix == '.css' else 'application/javascript')
            else:
                route.fulfill(status=404, body='')
        elif url.path.startswith('/_vendor/'):
            route.fulfill(status=200, body='')
        elif url.path == '/api/shell/scopes/' and failure and failure(route):
            return
        else:
            response = client.get(url.path + ('?' + url.query if url.query else ''))
            body = response.content
            route.fulfill(status=response.status_code, body=body,
                          headers={key: value for key, value in response.items() if key.lower() in ('content-type', 'location', 'cache-control')})
    context.route('http://shell.test/**', handle)
    return context


def _open_sidebar(page, width):
    if width < 700:
        page.get_by_role('button', name='Open navigation', exact=True).click()


@pytest.mark.parametrize('width', [1440, 390])
def test_studio_switcher_lists_every_studio_and_goes_there_directly(login, org_admin, org, width):
    Studio.objects.bulk_create([Studio(org=org, slug=f'enhanced-{i:03}', name=f'Enhanced {i:03}') for i in range(30)])
    client = login(org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, width=width)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        _open_sidebar(page, width)
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('[data-scope-menu]'))
        menu = switcher.locator('[data-scope-menu]')
        # Same links as always: no form, no Go button, no pager, no browse page.
        assert page.locator('form.tl-console-scope-form, a:text("Browse all")').count() == 0
        switcher.locator('summary').click()
        assert menu.locator('a').count() == 30
        assert menu.locator('a[href="/s/%s/enhanced-029/"]' % org.slug).count() == 1
        menu.get_by_role('searchbox').fill('Enhanced 029')
        expect(menu.locator('a:visible')).to_have_count(1)
        menu.locator('a:visible').click()
        page.wait_for_url(f'**/s/{org.slug}/enhanced-029/**')
        context.close()
        browser.close()


def test_assistant_picker_lists_studios_as_links(login, org_admin, org, studio):
    client = login(org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        page.locator('.tl-console-assistant-picker summary').click()
        link = page.locator(f'.tl-console-assistant-popover a[href="/s/{org.slug}/{studio.slug}/?assistant=open"]')
        expect(link).to_contain_text('Open or resume chat')
        assert page.locator('.tl-console-assistant-popover form, .tl-console-assistant-popover button').count() == 0
        context.close()
        browser.close()


def _huge_org(org, count=520):
    Studio.objects.bulk_create([Studio(org=org, slug=f'huge-{i:03}', name=f'Huge {i:03}') for i in range(count)])


def test_huge_studio_list_searches_as_you_type_without_pages(login, org_admin, org):
    _huge_org(org)
    client = login(org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('[data-scope-source]'))
        menu = switcher.locator('[data-scope-menu]')
        switcher.locator('summary').click()
        expect(menu.locator('[data-scope-hint]')).to_contain_text('Keep typing')
        assert menu.locator('a').count() <= 26
        menu.get_by_role('searchbox').fill('Huge 519')
        result = menu.locator('a[data-scope-result]')
        expect(result).to_have_count(1)
        expect(result).to_have_attribute('href', f'/s/{org.slug}/huge-519/')
        assert page.get_by_role('button', name='Next').count() == 0
        assert page.get_by_role('button', name='Previous').count() == 0
        result.click()
        page.wait_for_url(f'**/s/{org.slug}/huge-519/**')
        context.close()
        browser.close()


def test_huge_list_retries_quietly_and_ignores_obsolete_answers(login, org_admin, org):
    from playwright.sync_api import Error
    _huge_org(org)
    Studio.objects.bulk_create([
        Studio(org=org, slug='zz-slow-result', name='Slow result'),
        Studio(org=org, slug='zz-fresh-result', name='Fresh result'),
    ])
    client = login(org_admin)
    held, state = [], {'failed': False}
    def intercept(route):
        url = route.request.url
        if 'q=Slow' in url:
            held.append(route)
            return True
        if 'q=Fresh' in url and not state['failed']:
            state['failed'] = True
            route.fulfill(status=503, body='unavailable')
            return True
        return False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, failure=intercept)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('[data-scope-source]'))
        menu = switcher.locator('[data-scope-menu]')
        switcher.locator('summary').click()
        with page.expect_request(lambda request: 'q=Slow' in request.url):
            menu.get_by_role('searchbox').fill('Slow')
        menu.get_by_role('searchbox').fill('Fresh')
        # The first Fresh answer fails; the picker asks again without a button.
        expect(menu.locator('a[data-scope-result]')).to_have_count(1, timeout=8000)
        assert state['failed']
        for route in held:
            try:
                route.fulfill(status=200, content_type='application/json', body='{"results":[{"value":"zz-slow-result","label":"Slow result"}],"selected":null,"pagination":{"page":1,"total":1,"has_previous":false,"has_next":false}}')
            except Error:
                pass
        expect(menu).not_to_contain_text('Slow result')
        expect(menu.locator('a[data-scope-result]')).to_have_text('Fresh result')
        assert page.get_by_role('button', name='Retry').count() == 0
        context.close()
        browser.close()
