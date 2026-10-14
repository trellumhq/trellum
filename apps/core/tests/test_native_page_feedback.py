"""Real console documents with controlled browser response delay.

The shared analytics fixture starts owned Gunicorn/PostgreSQL and authenticates
with PBKDF2. Held navigation responses test client recovery, not server timing.
"""
import json

import pytest

from apps.reports.tests.test_analytics_bounded_browser import analytics_server, _login

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("width", [1440, 390])
def test_local_page_links_and_get_filters_show_recoverable_feedback(analytics_server, engine, width):
    from playwright.sync_api import sync_playwright, expect

    server = analytics_server
    with sync_playwright() as playwright:
        browser = getattr(playwright, engine).launch()
        context = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce",
                                      record_video_dir=str(server['artifacts'] / 'video'))
        page = context.new_page()
        page.set_default_timeout(15000)
        errors = []
        dialogs = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialogs.append(dialog.message))
        held = []
        try:
            _login(page, server, "admin")
            page.evaluate("""() => {
                window.nativeEvents = [];
                window.pendingVisibility = [];
                document.addEventListener('console:native-start', () => {
                    const started = performance.now();
                    const oldChildren = Array.from(document.querySelector('main').children)
                        .filter(node => !node.hasAttribute('data-console-native-pending'));
                    const displayDiagnostic = () => ({
                        tableRects:document.querySelector('#analyticsTable').getClientRects().length,
                        oldChildren:oldChildren.map(node => ({hidden:node.hidden,
                            display:getComputedStyle(node).display, rects:node.getClientRects().length}))
                    });
                    const immediateDisplay = displayDiagnostic();
                    setTimeout(() => pendingVisibility.push({
                        immediateDisplay, display:displayDiagnostic(),
                        milliseconds:performance.now()-started,
                        pending:!!document.querySelector('[data-console-native-pending]')
                    }), 50);
                });
                ['console:native-start','console:native-restored'].forEach(name =>
                    document.addEventListener(name, event => nativeEvents.push([name,event.detail.url])));
                document.querySelector('main').insertAdjacentHTML('beforeend',
                    '<form id="neighbor" data-settings-form><input name="draft" value="saved"></form>');
                TrellumSettingsForms.init(document);
            }""")
            page.route(server["origin"]+server["path"]+"?**", lambda route: held.append(route))
            page.locator('#neighbor input').fill('keep this draft')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.get_by_role('link', name='Next', exact=True).click()
            assert not held
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            expect(page.locator('#neighbor input')).to_have_value('keep this draft')
            page.once('dialog', lambda dialog: dialog.accept())
            state = page.evaluate("""() => {
                const started = performance.now();
                document.querySelector('.analytics-pagination a:last-child').click();
                const panel = document.querySelector('[data-console-native-pending]');
                const r = panel.querySelector('button').getBoundingClientRect();
                return {ms:performance.now()-started,text:panel.textContent,x:r.x+r.width/2,y:r.y+r.height/2,
                    oldVisibility:getComputedStyle(document.querySelector('#analyticsTable')).visibility,
                    observedAt:performance.now()};
            }""")
            assert state['ms'] < 100
            assert 'Opening Report Analytics' in state['text']
            page.wait_for_timeout(50)
            assert len(held) == 1
            # The high-level screenshot waits for frames that native provisional
            # navigation can suspend. Record actual compositor output instead.
            if engine == 'chromium':
                import base64
                session = context.new_cdp_session(page)
                capture = session.send('Page.captureScreenshot', {'format': 'png'})
                (server['artifacts'] / f'native-pending-{engine}-{width}.png').write_bytes(base64.b64decode(capture['data']))
                # Context closure releases this session. Detaching during a
                # held provisional navigation can itself block the protocol.
            page.wait_for_timeout(600)
            page.mouse.click(state['x'], state['y'])
            held.pop().abort()
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            expect(page.locator('#neighbor input')).to_have_value('keep this draft')
            assert page.evaluate('nativeEvents.map(e=>e[0])') == ['console:native-start', 'console:native-restored']
            rendered = page.evaluate('pendingVisibility')
            (server['artifacts'] / f'pending-link-{engine}-{width}.json').write_text(json.dumps(rendered, indent=2))
            assert rendered and rendered[-1]['pending'] and rendered[-1]['display']['tableRects'] == 0, rendered
            assert rendered[-1]['milliseconds'] < 100, rendered

            # A GET search also guards neighboring drafts and retains its inputs.
            # Match native browse forms registered with the shared draft guard.
            page.evaluate("""() => {
                document.querySelector('.analytics-search-form').setAttribute('data-settings-form', '');
                TrellumSettingsForms.init(document);
            }""")
            page.locator('#analyticsSearch').fill('report-0999')
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.locator('.analytics-search-form button[type=submit]').click()
            assert not held
            expect(page.locator('#analyticsSearch')).to_have_value('report-0999')
            dialog_count = len(dialogs)
            page.once('dialog', lambda dialog: dialog.accept())
            state = page.evaluate("""() => {
                const started=performance.now();
                document.querySelector('.analytics-search-form').requestSubmit();
                const panel=document.querySelector('[data-console-native-pending]');
                return {ms:performance.now()-started,text:panel.textContent,
                    oldVisibility:getComputedStyle(document.querySelector('#analyticsTable')).visibility};
            }""")
            assert state['ms'] < 100 and 'Opening Report Analytics' in state['text']
            assert len(dialogs) == dialog_count + 1
            page.wait_for_timeout(50)
            assert len(held) == 1
            page.wait_for_timeout(100)
            page.keyboard.press('Escape')
            held.pop().abort()
            expect(page.locator('#analyticsSearch')).to_have_value('report-0999')
            expect(page.locator('#neighbor input')).to_have_value('keep this draft')
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            rendered = page.evaluate('pendingVisibility')
            (server['artifacts'] / f'pending-form-{engine}-{width}.json').write_text(json.dumps(rendered, indent=2))
            assert len(rendered) == 2 and rendered[-1]['pending'] and rendered[-1]['display']['tableRects'] == 0, rendered
            assert rendered[-1]['milliseconds'] < 100, rendered
            # Resolve an actual authorized response; no synthetic result body.
            page.evaluate("document.querySelector('#neighbor').remove()")
            previous_scroll = page.evaluate("window.scrollTo(0,500); scrollY")
            page.evaluate("document.querySelector('.analytics-search-form').requestSubmit()")
            page.wait_for_timeout(50)
            route = held.pop()
            response = route.fetch()
            assert response.status == 200
            route.fulfill(response=response)
            expect(page.locator('#analyticsTable tbody tr[data-name]')).to_have_count(1)
            expect(page.locator('#analyticsSearch')).to_have_value('report-0999')
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            boundaries = page.evaluate("""() => {
                const urls = ['/login', '/api/me', '/s/org/studio/r/report/', '/s/org/studio/r/report/index.html',
                    '/orgs/org/settings/audit/export.csv', 'https://outside.example/',
                    location.pathname+location.search+'#local'];
                return urls.map(href => {
                    const link=document.createElement('a'); link.href=href; link.textContent='Boundary';
                    document.querySelector('main').append(link);
                    // Prevent the actual destination after the production window listener.
                    window.addEventListener('click', event=>event.preventDefault(), {once:true});
                    link.click();
                    const pending=!!document.querySelector('[data-console-native-pending]');
                    link.remove(); return {href,pending};
                });
            }""")
            assert not any(row['pending'] for row in boundaries), boundaries
            ordinary = page.evaluate("""() => {
                const urls=['/me/api-keys/new','/system/email-connections/new',
                    '/system/email-connections/12','/s/org/studio/r/report/access',
                    '/scopes/','/scopes/open/?kind=org&selected=org'];
                return urls.map(href => {
                    const link=document.createElement('a'); link.href=href; link.textContent='Open page';
                    document.querySelector('main').append(link);
                    window.addEventListener('click', event=>event.preventDefault(), {once:true});
                    link.click();
                    const pending=!!document.querySelector('[data-console-native-pending]');
                    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
                    link.remove(); return {href,pending};
                });
            }""")
            assert all(row['pending'] for row in ordinary), ordinary
            page.go_back()
            expect(page.locator('#analyticsTable tbody tr[data-name]')).to_have_count(25)
            expect(page).to_have_url(server['origin'] + server['path'])
            page.wait_for_function('expected => Math.abs(scrollY-expected)<3', arg=previous_scroll)
            assert not errors
        finally:
            for route in held:
                try:
                    route.abort()
                except Exception:
                    pass
            context.close()
            browser.close()
