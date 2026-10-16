"""Rendered navigation contract against an isolated PostgreSQL test database."""
import json
from pathlib import Path

import pytest
from django.conf import settings
from playwright.sync_api import sync_playwright

from apps.core import roles

pytestmark = pytest.mark.django_db


def test_metrics_fragment_native_parity_and_access(studio_tree, member, grant_studio, login, client):
    grant_studio(member, studio_tree, roles.VIEWER)
    url = f'/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics'
    authenticated = login(member)
    native = authenticated.get(url)
    enhanced = authenticated.get(url, HTTP_X_TRELLUM_NAVIGATION='1')
    data = enhanced.json()
    assert data['version'] == 1 and data['controller'] == 'metrics'
    assert data['url'] == url
    assert '<h1>Metrics</h1>' in data['html'] and '<h1>Metrics</h1>' in native.content.decode()
    assert '<html' not in data['html'] and 'data-console-shell' not in data['html']
    assert 'X-Trellum-Navigation' in enhanced['Vary']
    assert enhanced['Cache-Control'] == 'private, no-store'
    authenticated.logout()
    denied = authenticated.get(url, HTTP_X_TRELLUM_NAVIGATION='1')
    assert denied.status_code == 302 and '/login' in denied.url


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('width', [1440, 390])
def test_rendered_navigation_delay_guard_history_and_native_feedback(
    studio_tree, member, grant_studio, login, engine, width, tmp_path
):
    grant_studio(member, studio_tree, roles.VIEWER)
    client = login(member)
    overview = f'/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics'
    comparison = overview + '/comparison'
    home = overview.removesuffix('metrics')
    native_responses = {url: client.get(url) for url in [overview, comparison, home]}
    fragment_responses = {url: client.get(url, HTTP_X_TRELLUM_NAVIGATION='1') for url in [overview, comparison]}
    pending = []
    native_pending = []
    saves = []
    delay_documents = False
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        context = browser.new_context(viewport={'width': width, 'height': 900 if width > 400 else 844})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def serve(route):
            request = route.request
            if request.url.startswith('http://navigation.test/static/'):
                name = request.url.split('/static/', 1)[1]
                path = settings.BASE_DIR / 'static' / name
                if path.is_file():
                    route.fulfill(body=path.read_bytes(), content_type='text/css' if name.endswith('.css') else 'application/javascript')
                else:
                    route.fulfill(status=204)
            elif request.resource_type == 'document':
                if delay_documents:
                    native_pending.append(route)
                    return
                response = native_responses[request.url.split('navigation.test', 1)[1]]
                route.fulfill(status=response.status_code, body=response.content, content_type='text/html')
            elif request.method == 'POST':
                saves.append(route)
            elif request.headers.get('x-trellum-navigation') == '1':
                pending.append(route)
            else:
                route.fulfill(status=204)
        page.route('http://navigation.test/**', serve)
        page.goto('http://navigation.test' + overview)
        assert page.locator('main').get_attribute('data-console-controller') == 'metrics'
        assert not errors
        page.evaluate("""document.addEventListener('click', function () {
            var start = performance.now(); requestAnimationFrame(function () { window.navigationPaint = performance.now() - start; });
        }, {once:true, capture:true})""")
        page.get_by_role('link', name='Comparison', exact=True).click()
        page.wait_for_function("document.querySelector('main').getAttribute('aria-busy') === 'true'")
        page.wait_for_function("window.navigationPaint !== undefined")
        sample = page.evaluate("window.navigationPaint")
        assert sample <= 100, sample
        assert page.url.endswith(comparison)
        assert page.locator('main h1').inner_text() == 'Metrics'
        assert page.get_by_role('status').filter(has_text='Loading Metrics').count() == 1
        assert pending
        route = pending.pop()
        response = fragment_responses[comparison]
        payload = response.json()
        payload['html'] += '<script>window.untrustedExecuted = true</script><button id=inline-handler onclick="window.untrustedExecuted=true">Unsafe handler</button>'
        route.fulfill(body=json.dumps(payload), content_type='application/json')
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        assert not page.evaluate('!!window.untrustedExecuted')
        assert page.locator('#inline-handler').get_attribute('onclick') is None
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<form data-settings-form><input name=example value=initial></form>'); TrellumSettingsForms.init(document)")
        page.locator('input[name=example]').fill('draft')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_role('link', name='Overview', exact=True).click()
        assert page.url.endswith(comparison)
        assert page.locator('input[name=example]').input_value() == 'draft'
        page.once('dialog', lambda dialog: dialog.accept())
        page.get_by_role('link', name='Overview', exact=True).click()
        page.wait_for_function("document.querySelector('main').getAttribute('aria-busy') === 'true'")
        route = pending.pop()
        response = fragment_responses[overview]
        route.fulfill(body=response.content, content_type='application/json')
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<form data-settings-form><input name=backdraft value=initial></form>'); TrellumSettingsForms.init(document)")
        page.locator('input[name=backdraft]').fill('keep me')
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.evaluate('history.back()')
        page.wait_for_timeout(150)
        assert page.url.endswith(overview)
        assert page.locator('input[name=backdraft]').input_value() == 'keep me'
        page.once('dialog', lambda dialog: dialog.accept())
        page.evaluate('history.back()')
        page.wait_for_function("document.querySelector('main').getAttribute('aria-busy') === 'true'")
        route = pending.pop()
        response = fragment_responses[comparison]
        route.fulfill(body=response.content, content_type='application/json')
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        assert page.url.endswith(comparison)
        page.evaluate('history.forward()')
        page.wait_for_function("document.querySelector('main').getAttribute('aria-busy') === 'true'")
        route = pending.pop()
        route.fulfill(body=fragment_responses[overview].content, content_type='application/json')
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        assert page.url.endswith(overview)
        assert not errors
        page.screenshot(path=str(tmp_path / f'c02-{engine}-{width}.png'), full_page=True)
        # A leave lifecycle invalidates a pending save without erasing its draft.
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<form data-settings-form data-settings-save method=post action=/save><input name=savedraft value=initial><button type=submit>Save draft</button></form>'); TrellumSettingsForms.init(document)")
        page.locator('input[name=savedraft]').fill('pending draft')
        page.get_by_role('button', name='Save draft').click()
        page.wait_for_function("document.querySelector('form[data-settings-save]').getAttribute('aria-busy') === 'true'")
        assert saves
        page.evaluate('TrellumSettingsForms.unmount()')
        assert page.locator('input[name=savedraft]').input_value() == 'pending draft'
        assert page.get_by_role('button', name='Save draft').is_enabled()
        assert 'outcome is unknown' in page.locator('[data-settings-dirty-status]').inner_text()
        saves.pop().fulfill(body=json.dumps({'ok': True, 'message': 'Saved.'}), content_type='application/json')
        page.wait_for_timeout(100)
        assert 'outcome is unknown' in page.locator('[data-settings-dirty-status]').inner_text()
        page.evaluate("document.querySelector('form[data-settings-save]').addEventListener('settings:success', ()=>TrellumSettingsForms.unmount(), {once:true})")
        page.get_by_role('button', name='Save draft').click()
        page.wait_for_timeout(50)
        saves.pop().fulfill(body=json.dumps({'ok': True, 'message': 'Saved.'}), content_type='application/json')
        page.wait_for_function("document.querySelector('[data-settings-dirty-status]').textContent.includes('Saved.')")
        assert 'outcome is unknown' not in page.locator('[data-settings-dirty-status]').inner_text()
        assert page.get_by_role('button', name='Save draft').is_enabled()
        assert page.locator('form[data-settings-save]').get_attribute('aria-busy') is None
        page.evaluate("document.querySelector('form[data-settings-save]').remove()")
        # An obsolete reply must never replace a newer destination.
        page.get_by_role('link', name='Comparison', exact=True).click()
        page.wait_for_function("document.querySelector('main').getAttribute('aria-busy') === 'true'")
        old_route = pending.pop()
        page.evaluate("TrellumNavigation.navigate(location.pathname.replace('/comparison', ''))")
        page.wait_for_timeout(50)
        pending.pop().fulfill(body=fragment_responses[overview].content, content_type='application/json')
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        old_payload = fragment_responses[comparison].json()
        old_payload['html'] += '<p id=obsolete>Obsolete result</p>'
        old_route.fulfill(body=json.dumps(old_payload), content_type='application/json')
        page.wait_for_timeout(100)
        assert page.locator('#obsolete').count() == 0
        assert page.get_by_role('link', name='Overview', exact=True).get_attribute('aria-current') == 'page'
        delay_documents = True
        page.evaluate("document.querySelector('main').insertAdjacentHTML('beforeend', '<input id=native-draft value=kept><div style=height:1400px></div>'); document.querySelector('#native-draft').focus(); scrollTo(0,400)")
        old_position = page.evaluate('scrollY')
        def native_reports():
            target = page.evaluate("Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Reports')).click(); var r=document.querySelector('[data-console-native-pending] button').getBoundingClientRect(); ({x:r.x+r.width/2,y:r.y+r.height/2})")
            page.wait_for_timeout(50)
            assert native_pending
            return target
        target = native_reports()
        page.mouse.click(target['x'], target['y'])
        native_pending.pop().abort()
        assert page.locator('#native-draft').input_value() == 'kept'
        assert page.evaluate("document.activeElement.id === 'native-draft'")
        assert page.evaluate('scrollY') == old_position
        assert not page.evaluate("document.querySelector('main').classList.contains('tl-console-native-pending')")
        native_reports()
        page.keyboard.press('Escape')
        native_pending.pop().abort()
        assert not page.evaluate("document.querySelector('[data-console-native-pending]') !== null")
        # The 15 s notice, shortened to 100 ms. The page is not queried while a navigation is held, so a
        # binding reports what the page showed when the notice appeared.
        notices = []
        page.expose_function('noticeSeen', lambda seen: notices.append(seen))
        page.evaluate("""var setTimeoutNow = window.setTimeout;
            window.setTimeout = function (fn, ms) { return setTimeoutNow.apply(window, [fn, ms === 15000 ? 100 : ms].concat([].slice.call(arguments, 2))); };
            new MutationObserver(function () {
                var panel = document.querySelector('[data-console-native-pending] [role=status]');
                if (panel && panel.textContent.indexOf('Still loading') === 0) window.noticeSeen([panel.textContent,
                    document.querySelector('[data-console-navigation-native-status]').textContent, !!document.querySelector('[data-console-native-pending] button')]);
            }).observe(document.body, {subtree: true, childList: true, characterData: true})""")
        # A slow page is not a failed page: after 15 s the text says so and the request keeps going.
        native_reports()
        page.wait_for_timeout(400)
        assert notices == [['Still loading Reports…', 'Still loading Reports…', True]]
        # Escape and Cancel loading remain the explicit abort, even after the notice.
        page.keyboard.press('Escape')
        native_pending.pop().abort()
        assert not page.evaluate("document.querySelector('[data-console-native-pending]') !== null")
        assert page.locator('[data-console-navigation-native-status]').inner_text() == ''
        assert page.locator('#native-draft').input_value() == 'kept'
        assert page.evaluate('scrollY') == old_position
        delay_documents = False
        page.evaluate("document.querySelector('#native-draft').remove()")
        page.get_by_role('link', name='Comparison', exact=True).click()
        page.wait_for_timeout(50)
        pending.pop().fulfill(status=401, content_type='text/html', body='<h1>Sign in again</h1>')
        page.wait_for_function("document.querySelector('main [role=alert]') !== null")
        assert page.locator('.tl-console-navigation-status').count() == 0
        assert page.get_by_role('link', name='Open page directly').get_attribute('href').endswith(comparison)
        delay_documents = True
        # An unadapted destination retains native navigation and pending feedback.
        result = page.evaluate("Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Reports')).click(); ({title:document.querySelector('[data-console-native-pending] h1').textContent, path:location.pathname})")
        assert result['title'] == 'Reports'
        page.wait_for_timeout(50)
        assert native_pending
        # Still waiting after the notice: the page is kept pending, not stopped, and then opens by itself.
        page.wait_for_timeout(400)
        assert len(notices) == 2
        route = native_pending.pop()
        route.fulfill(body=native_responses[home].content, content_type='text/html')
        page.wait_for_url(lambda url: url.split('?')[0] == 'http://navigation.test' + home)
        result = page.evaluate("Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Metrics')).click(); ({title:document.querySelector('[data-console-native-pending] h1').textContent, path:location.pathname})")
        assert result['title'] == 'Metrics'
        assert result['path'] == home
        page.wait_for_timeout(50)
        assert page.url.startswith('http://navigation.test' + home)  # native URL commits with the document
        assert native_pending
        route = native_pending.pop()
        route.fulfill(body=native_responses[overview].content, content_type='text/html')
        page.wait_for_url('http://navigation.test' + overview)
        context.close()
        browser.close()

@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
def test_real_delayed_django_metrics_destination(live_server, studio_tree, member, grant_studio, login, monkeypatch, width, engine, tmp_path):
    """Real HTTP/PG/static assets: primary response deliberately delayed 1.5s."""
    import time
    import apps.reports.metrics_catalog as catalog
    grant_studio(member, studio_tree, roles.VIEWER)
    client = login(member)
    overview = f'/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics'
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        context = browser.new_context(viewport={'width': width, 'height': 844})
        context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': live_server.url}])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(live_server.url + overview, wait_until='domcontentloaded')
        page.wait_for_function('window.TrellumNavigation !== undefined')
        original = catalog.studio_metrics_overview
        def delayed(studio):
            time.sleep(1.5)
            return original(studio)
        monkeypatch.setattr(catalog, 'studio_metrics_overview', delayed)
        page.evaluate("""document.addEventListener('click', function () {
            var start = performance.now(); requestAnimationFrame(function () {
                window.pendingSample = {ms:performance.now()-start, title:document.querySelector('main h1').textContent,
                    busy:document.querySelector('main').getAttribute('aria-busy'), url:location.pathname};
            });
        }, {once:true, capture:true})""")
        page.get_by_role('link', name='Comparison', exact=True).focus()
        page.keyboard.press('Enter')
        page.wait_for_function('window.pendingSample !== undefined')
        sample = page.evaluate('window.pendingSample')
        assert sample['ms'] <= 100, sample
        assert sample['title'] == 'Metrics' and sample['busy'] == 'true'
        assert sample['url'] == overview + '/comparison'
        assert page.evaluate("document.activeElement.matches('h1,[data-console-page-title]')")
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path / f'real-delayed-metrics-{engine}-{width}.png'))
        page.wait_for_function("!document.querySelector('main').hasAttribute('aria-busy')")
        assert page.get_by_role('link', name='Comparison', exact=True).get_attribute('aria-current') == 'page'
        page.wait_for_function("document.activeElement.matches('h1,[data-console-page-title]') && document.activeElement.textContent === 'Metrics' && document.activeElement.getClientRects().length > 0")
        (tmp_path / f'pending-{engine}-{width}.json').write_text(json.dumps(sample))
        assert not errors
        print(f'real HTTP pending {engine} {width}px: {sample["ms"]:.1f}ms')
        home = overview.removesuffix('metrics')
        page.goto(live_server.url + home, wait_until='domcontentloaded')
        page.wait_for_function('window.TrellumConsoleShell !== undefined')
        # The catalog's existing secondary requests belong to its later adapter.
        page.wait_for_load_state('networkidle')
        native = page.evaluate("""var start=performance.now();
            Array.from(document.querySelectorAll('.tl-console-nav-link')).find(n=>n.textContent.includes('Metrics')).click();
            ({ms:performance.now()-start, title:document.querySelector('[data-console-native-pending] h1').textContent,
              url:location.pathname, documentTitle:document.title})""")
        assert native['ms'] <= 100 and native['title'] == 'Metrics'
        assert native['url'] == home
        page.wait_for_url(live_server.url + overview)
        assert page.locator('main h1').inner_text() == 'Metrics'
        (tmp_path / f'native-pending-{engine}-{width}.json').write_text(json.dumps(native))
        assert not errors
        page.goto(live_server.url + '/account', wait_until='domcontentloaded')
        page.wait_for_function('window.TrellumSettingsForms !== undefined')
        assert not page.evaluate('!!window.TrellumNavigation')
        page.evaluate("history.replaceState({testHistoryOwner:1}, ''); document.querySelector('main').style.minHeight='3000px'; scrollTo(0,500)")
        page.wait_for_timeout(100)
        assert page.evaluate('history.state') == {'testHistoryOwner': 1}
        browser.close()
