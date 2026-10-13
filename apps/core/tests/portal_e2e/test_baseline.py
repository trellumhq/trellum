"""Live browser tests collected only by the disposable runner."""
import json
import os
from pathlib import Path
import pytest
import django
if os.environ.get("PORTAL_E2E_ORIGIN"):
    django.setup()

pytestmark = [pytest.mark.portal_e2e, pytest.mark.skipif(not os.environ.get('PORTAL_E2E_ORIGIN'), reason='run scripts/portal_e2e.py')]

@pytest.mark.parametrize('viewport', [{'width': 1440, 'height': 900}, {'width': 390, 'height': 844}], ids=['desktop', 'mobile'])
def test_login_save_reload(viewport):
    from playwright.sync_api import sync_playwright, expect
    from apps.core.tests.portal_e2e.seed import PASSWORD
    from django.contrib.auth import get_user_model
    from django.db import connection
    origin = os.environ['PORTAL_E2E_ORIGIN']
    artifacts = Path(os.environ['PORTAL_E2E_ARTIFACTS'])
    browser_name = os.environ.get('PORTAL_E2E_BROWSER', 'chromium')
    with sync_playwright() as playwright:
        browser = getattr(playwright, browser_name).launch()
        context = browser.new_context(viewport=viewport)
        page = context.new_page()
        context.tracing.start(screenshots=True, snapshots=True)
        try:
            page.goto(origin + '/login')
            page.locator('input[type=email]').fill('alpha-admin@example.invalid')
            page.locator('input[type=password]').fill(PASSWORD)
            page.locator('button[type=submit]').click()
            page.wait_for_url(lambda url: '/login' not in url)
            page.goto(origin + '/account')
            field = page.locator('input[name=name]')
            saved_name = f'Persisted {browser_name} {viewport["width"]}'
            field.fill(saved_name)
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/account')) as saved:
                field.locator('xpath=ancestor::form').locator('button[type=submit]').click()
            assert saved.value.status in (200, 302)
            expect(field).to_have_value(saved_name)
            page.reload()
            expect(page.locator('input[name=name]')).to_have_value(saved_name)
            assert connection.vendor == 'postgresql'
            for endpoint in ('registry', 'system/status', 'system/run-stats'):
                response = context.request.get(origin + '/s/alpha/studio-0/api/' + endpoint)
                assert response.status == 200, (endpoint, response.status, response.text()[:200])
                payload = response.json()
                (artifacts / (endpoint.replace('/', '-') + '.json')).write_text(json.dumps(payload, indent=2))
            page.screenshot(path=str(artifacts / f'{browser_name}-{viewport["width"]}.png'), full_page=True)
        finally:
            context.tracing.stop(path=str(artifacts / f'{browser_name}-{viewport["width"]}-trace.zip'))
            browser.close()
    assert get_user_model().objects.get(email='alpha-admin@example.invalid').name == saved_name

@pytest.mark.parametrize('role', ['admin', 'developer', 'viewer', 'group-member', 'group-admin', 'group-selected', 'revoked'])
def test_role_request_baseline(role):
    """Actual login plus permission-separated full HTTP request samples."""
    import requests
    from apps.core.tests.portal_e2e.seed import PASSWORD
    session = requests.Session()
    origin = os.environ['PORTAL_E2E_ORIGIN']
    session.get(origin + '/login', timeout=10)
    response = session.post(origin + '/login', data={'email': f'alpha-{role}@example.invalid', 'password': PASSWORD, 'csrfmiddlewaretoken': session.cookies['csrftoken']}, headers={'Referer': origin+'/login'}, timeout=10)
    assert response.status_code == 200
    for endpoint in ('registry', 'system/status', 'system/run-stats'):
        url = '/s/alpha/studio-0/api/' + endpoint
        response = session.get(origin + url, timeout=30, allow_redirects=False)
        if role == 'revoked':
            assert response.status_code in (302, 403, 404)
        elif endpoint == 'registry':
            assert response.status_code == 200
        else:
            assert response.status_code == (200 if role in ('admin', 'developer', 'group-admin') else 403), (role, endpoint, response.status_code)
        # Retain numeric full-request samples separately from access assertions.
        row = {'role': role, 'endpoint': endpoint, 'status': response.status_code, 'http_ms': response.elapsed.total_seconds()*1000, 'response_bytes': len(response.content)}
        with (Path(os.environ['PORTAL_E2E_ARTIFACTS']) / 'roles.jsonl').open('a') as out:
            out.write(json.dumps(row)+'\n')
        denied = session.get(origin + '/s/beta/studio-0/api/' + endpoint, timeout=30, allow_redirects=False)
        assert denied.status_code in (302, 403, 404), (role, endpoint, denied.status_code)
