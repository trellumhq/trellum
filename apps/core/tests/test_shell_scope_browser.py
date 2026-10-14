"""Native and enhanced scope controls on complete rendered documents."""
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from playwright.sync_api import expect, sync_playwright

from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def serve_browser(browser, client, *, width=1440, javascript=True, failure=None, native_theme=None):
    context = browser.new_context(viewport={'width': width, 'height': 900}, java_script_enabled=javascript)
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
            if native_theme and response.get('Content-Type', '').startswith('text/html'):
                body = body.replace(b'<html lang="en">', f'<html lang="en" data-theme="{native_theme}">'.encode(), 1)
            route.fulfill(status=response.status_code, body=body,
                          headers={key: value for key, value in response.items() if key.lower() in ('content-type', 'location', 'cache-control')})
    context.route('http://shell.test/**', handle)
    return context


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_native_browse_all_and_assistant_intent(login, org_admin, org, width, theme, tmp_path):
    Studio.objects.bulk_create([Studio(org=org, slug=f'browse-{i:03}', name=f'Browse {i:03}') for i in range(30)])
    client = login(org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, width=width, javascript=False, native_theme=theme)
        page = context.new_page()
        page.goto(f'http://shell.test/scopes/?kind=studio&org={org.slug}&intent=assistant')
        # No script required for paging/search or the authorized link destination.
        assert page.locator('ul.ui-list a').count() == 25
        page.get_by_role('link', name='Next', exact=True).click()
        expect(page.get_by_role('link', name='Browse 029', exact=True)).to_have_attribute('href', f'/s/{org.slug}/browse-029/?assistant=open')
        page.get_by_role('searchbox', name='Search studios').fill('Browse 029')
        page.get_by_role('button', name='Search', exact=True).click()
        assert page.locator('ul.ui-list a').count() == 1
        page.screenshot(path=str(tmp_path / f'native-{width}-{theme}.png'), full_page=True)
        context.close()
        browser.close()


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_enhanced_picker_remote_page_search_and_native_submission(login, org_admin, org, width, theme, tmp_path):
    Studio.objects.bulk_create([Studio(org=org, slug=f'enhanced-{i:03}', name=f'Enhanced {i:03}') for i in range(30)])
    client = login(org_admin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, width=width)
        context.add_init_script(f"localStorage.setItem('mgmt-theme', '{theme}')")
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        if width < 700:
            page.get_by_role('button', name='Open navigation', exact=True).click()
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('#shellStudioChoice'))
        switcher.locator('summary').click()
        form = switcher.locator('form')
        form.locator('.ui-picker-trigger').click()
        dialog = form.get_by_role('dialog')
        expect(dialog.locator('.ui-picker-pager')).to_contain_text('30 choices')
        dialog.get_by_role('button', name='Next', exact=True).click()
        expect(dialog).to_contain_text('Enhanced 029')
        dialog.get_by_role('searchbox').fill('Enhanced 029')
        expect(dialog.locator('.ui-picker-pager')).to_contain_text('1 choices')
        dialog.get_by_role('button', name='Enhanced 029', exact=False).focus()
        page.keyboard.press('Enter')
        expect(form.locator('select')).to_have_value('enhanced-029')
        form.get_by_role('button', name='Go', exact=True).focus()
        page.screenshot(path=str(tmp_path / f'enhanced-{width}-{theme}.png'), full_page=True)
        # Native form remains the action boundary after enhancement.
        expect(form).to_have_attribute('action', '/scopes/open/')
        expect(form.locator('input[name="org"]')).to_have_value(org.slug)
        expect(form.locator('input[name="intent"]')).to_have_value('navigate')
        context.close()
        browser.close()


def test_failed_remote_load_retains_selection_and_browse_all(login, org_admin, org, studio):
    client = login(org_admin)
    failed = {'once': False}
    def fail_once(route):
        if not failed['once']:
            failed['once'] = True
            route.fulfill(status=503, body='unavailable')
            return True
        return False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, failure=fail_once)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('#shellStudioChoice'))
        switcher.locator('summary').click()
        form = switcher.locator('form')
        # The explicit choice remains in the underlying form during failures.
        form.locator('select').evaluate('(select, slug) => {select.value=slug;select.dispatchEvent(new Event("change",{bubbles:true}));}', studio.slug)
        form.locator('.ui-picker-trigger').click()
        expect(form.get_by_role('status')).to_contain_text('Your selection is retained')
        expect(form.locator('select')).to_have_value(studio.slug)
        expect(form.get_by_role('link', name='Browse all studios')).to_have_attribute('href', f'/scopes/?kind=studio&org={org.slug}')
        form.get_by_role('button', name='Retry', exact=True).click()
        expect(form.get_by_role('dialog')).to_contain_text(studio.name)
        page.keyboard.press('Escape')
        expect(form.locator('.ui-picker-trigger')).to_be_focused()
        context.close()
        browser.close()


def test_obsolete_search_response_cannot_replace_latest_choices(login, org_admin, org):
    from playwright.sync_api import Error
    Studio.objects.bulk_create([
        Studio(org=org, slug='slow-result', name='Slow result'),
        Studio(org=org, slug='fresh-result', name='Fresh result'),
    ])
    client = login(org_admin)
    held = []
    def hold_old(route):
        if 'q=Slow' in route.request.url:
            held.append(route)
            return True
        return False
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = serve_browser(browser, client, failure=hold_old)
        page = context.new_page()
        page.goto(f'http://shell.test/orgs/{org.slug}/settings/members')
        switcher = page.locator('.tl-console-switcher').filter(has=page.locator('#shellStudioChoice'))
        switcher.locator('summary').click()
        form = switcher.locator('form')
        form.locator('.ui-picker-trigger').click()
        dialog = form.get_by_role('dialog')
        expect(dialog).to_contain_text('Fresh result')
        with page.expect_request(lambda request: '/api/shell/scopes/' in request.url and 'q=Slow' in request.url):
            dialog.get_by_role('searchbox').fill('Slow')
        dialog.get_by_role('searchbox').fill('Fresh')
        expect(dialog.locator('.ui-picker-options')).to_contain_text('Fresh result')
        assert held
        for route in held:
            try:
                route.fulfill(status=200, content_type='application/json', body='{"results":[{"value":"slow-result","label":"Slow result"}],"selected":null,"pagination":{"page":1,"total":1,"has_previous":false,"has_next":false}}')
            except Error:
                # Abort is also an acceptable outcome for an obsolete request.
                pass
        expect(dialog.locator('.ui-picker-options')).not_to_contain_text('Slow result')
        dialog.get_by_role('button', name='Fresh result', exact=False).click()
        expect(form.locator('select')).to_have_value('fresh-result')
        context.close()
        browser.close()
