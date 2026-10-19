"""Real console documents with controlled browser response delay.

The shared analytics fixture starts owned Gunicorn/PostgreSQL and authenticates
with PBKDF2. Held navigation responses test client recovery, not server timing.
The 1,000-report analytics page supplies a real shared pager link.
"""
import pytest

from apps.reports.tests.test_analytics_bounded_browser import analytics_server, _login

pytestmark = pytest.mark.django_db


# The behaviour (held navigation, Still-loading notice, cancel, recovery) does not
# depend on the width, so run each engine at one width: desktop chromium, phone webkit.
@pytest.mark.parametrize("engine, width", [("chromium", 1440), ("webkit", 390)])
def test_local_page_links_show_recoverable_feedback(analytics_server, engine, width):
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
            page.get_by_role('link', name='Next →', exact=True).click()
            assert not held
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            expect(page.locator('#neighbor input')).to_have_value('keep this draft')
            page.once('dialog', lambda dialog: dialog.accept())
            state = page.evaluate("""() => {
                const started = performance.now();
                document.querySelector('.ui-pager a:last-child').click();
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
            # The old table is gone the moment navigation starts, and still gone
            # a beat later while the request is held. (How late that beat fires
            # is the runner's timer latency, not page behaviour, so not asserted.)
            assert rendered and rendered[-1]['immediateDisplay']['tableRects'] == 0, rendered
            assert rendered[-1]['pending'] and rendered[-1]['display']['tableRects'] == 0, rendered

            # Resolve an actual authorized response; no synthetic result body.
            page.evaluate("document.querySelector('#neighbor').remove()")
            previous_scroll = page.evaluate("window.scrollTo(0,500); scrollY")
            # A held navigation never commits, so click from the page instead of waiting on it.
            page.evaluate("document.querySelector('.ui-pager a:last-child').click()")
            page.wait_for_timeout(50)
            route = held.pop()
            response = route.fetch()
            assert response.status == 200
            route.fulfill(response=response)
            expect(page.locator('.ui-pager')).to_contain_text('201–400 of 1000')
            expect(page.locator('[data-console-native-pending]')).to_have_count(0)
            # The new document's text is visible while its deferred scripts are
            # still loading, and the shell's link handlers do not exist until
            # those scripts have run, so wait for the load before using them.
            page.wait_for_load_state('load')
            # Loading feedback is opt-in: only links (and GET forms) marked data-console-nav, or inside a
            # marked region, take it. Everything else keeps the browser's own navigation.
            probe = """(cases) => cases.map(([href, attrs, wrap]) => {
                const link = document.createElement('a'); link.href = href; link.textContent = 'Probe';
                Object.entries(attrs || {}).forEach(([name, value]) => link.setAttribute(name, value));
                let host = link;
                if (wrap) { host = document.createElement('div'); host.setAttribute('data-console-nav', ''); host.append(link); }
                document.querySelector('main').append(host);
                // Prevent the actual destination after the production window listener.
                window.addEventListener('click', event => event.preventDefault(), {once: true});
                link.click();
                const pending = !!document.querySelector('[data-console-native-pending]');
                document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                host.remove(); return {href, pending};
            })"""
            marked = {'data-console-nav': ''}
            unmarked = page.evaluate(probe, [[href, None, False] for href in (
                '/me/api-keys/new', '/system/email-connections/12', '/s/org/studio/r/report/access', '/login', '/api/me',
                '/s/org/studio/r/report/', '/orgs/org/settings/audit/export.csv')])
            assert not any(row['pending'] for row in unmarked), unmarked
            excluded = page.evaluate(probe, [
                ['https://outside.example/', marked, False],
                [page.evaluate('location.pathname+location.search') + '#local', marked, False],
                ['/me/api-keys/new', {**marked, 'data-console-no-feedback': ''}, False],
                ['/me/api-keys/new', {**marked, 'download': ''}, False],
                ['/me/api-keys/new', {**marked, 'target': '_blank'}, False]])
            assert not any(row['pending'] for row in excluded), excluded
            ordinary = page.evaluate(probe, [[href, marked, False] for href in (
                '/me/api-keys/new', '/system/email-connections/new', '/system/email-connections/12',
                '/s/org/studio/r/report/access', '/brand-new/route')] + [['/me/api-keys', None, True]])
            assert all(row['pending'] for row in ordinary), ordinary
            page.go_back()
            expect(page.locator('#analyticsTable tbody tr[data-name]')).to_have_count(200)
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
