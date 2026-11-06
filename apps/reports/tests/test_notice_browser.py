"""Notice recovery works with the operator's enforced script policy."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from apps.core import roles
from apps.runner.models import Run

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user('notice-dev@example.test', org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f'/s/{org.slug}/{studio_tree.slug}'


@pytest.fixture
def notice_client(settings, login, developer, report_row):
    settings.CSP_REPORT_ONLY = False
    report_row.data_expired_at = timezone.now()
    report_row.save(update_fields=['data_expired_at'])
    return login(developer)


def open_notice(browser, response, path, *, width=1280, post_status=200, outage=False):
    page = browser.new_page(viewport={'width': width, 'height': 850})
    page.clock.install()
    assert response.status_code in (200, 503), (response.status_code, dict(response.items()))
    requests = []
    errors = []
    assert 'Content-Security-Policy' in response
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)

    def serve(route):
        request = route.request
        url = urlsplit(request.url)
        if url.path.startswith('/static/'):
            asset = ROOT / 'static' / url.path.removeprefix('/static/')
            route.fulfill(path=str(asset), content_type='text/css' if asset.suffix == '.css' else 'application/javascript')
        elif request.method == 'POST':
            requests.append(request)
            if post_status == -1:
                return
            if post_status == 0:
                route.abort()
            else:
                route.fulfill(status=post_status, json={'ok': post_status == 200, 'status': 'queued'})
        elif url.path == path:
            if request.method == 'HEAD':
                requests.append(request)
                route.fulfill(status=503 if len(requests) == 1 else 200, body='')
            elif outage and len(requests) >= 2:
                route.fulfill(body='<h1>Recovered report</h1>')
            else:
                route.fulfill(status=response.status_code, body=response.content, headers=dict(response.items()))
        else:
            route.fulfill(status=200, body='')

    page.route('http://notice.test/**', serve)
    page.goto('http://notice.test' + path)
    return page, requests, errors


def test_rebuild_under_enforced_policy(notice_client, prefix):
    response = notice_client.get(prefix + '/r/player-overview/')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page, requests, errors = open_notice(browser, response, prefix + '/r/player-overview/')
        page.get_by_role('button', name='Rebuild now', exact=True).click()
        expect(page.locator('#reportRebuildNow')).to_contain_text('Rebuild queued')
        assert len(requests) == 1
        assert requests[0].headers.get('x-csrftoken')
        assert not [error for error in errors if 'Content Security Policy' in error or 'script-src' in error]
        browser.close()


@pytest.mark.parametrize('post_status', [403, 500, 0])
def test_rebuild_failure_is_visible_and_never_replayed(notice_client, prefix, post_status):
    response = notice_client.get(prefix + '/r/player-overview/')
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page, requests, _ = open_notice(browser, response, prefix + '/r/player-overview/', post_status=post_status)
        page.locator('#reportRebuildNow').click()
        if post_status == 403:
            expect(page.locator('[data-rebuild-status]')).to_contain_text('no longer have permission')
            expect(page.locator('#reportRebuildNow')).to_be_enabled()
        else:
            expect(page.locator('#reportRebuildNow')).to_have_text('Rebuild unconfirmed')
            expect(page.locator('#reportRebuildNow')).to_be_disabled()
            expect(page.locator('[data-rebuild-status]')).to_contain_text('may have been queued')
        assert len(requests) == 1
        browser.close()


@pytest.mark.heavy
def test_storage_recovers_automatically_under_enforced_policy(notice_client, prefix):
    path = prefix + '/r/player-overview/'
    with patch('apps.reports.views.storage.output_root', side_effect=OSError('offline')):
        response = notice_client.get(path)
    assert response.status_code == 503
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page, requests, errors = open_notice(browser, response, path, outage=True)
        assert page.get_by_role('button', name='Try again').count() == 0
        page.evaluate("""() => {
            window.noticeProbeSettled = false;
            const originalFetch = window.fetch;
            window.fetch = async (...args) => {
                const response = await originalFetch(...args);
                window.noticeProbeSettled = true;
                return response;
            };
        }""")
        with page.expect_response(lambda r: r.request.method == 'HEAD', timeout=3000):
            page.clock.fast_forward(5000)
        page.wait_for_function('window.noticeProbeSettled', timeout=3000)
        assert len(requests) == 1
        page.clock.fast_forward(10000)
        expect(page.get_by_role('heading', name='Recovered report')).to_be_visible()
        assert len(requests) == 2
        assert all(request.method == 'HEAD' for request in requests)
        assert not [error for error in errors if 'script-src' in error]
        browser.close()


@pytest.mark.parametrize('width', [1280, pytest.param(390, marks=pytest.mark.heavy)])
def test_notices_render_without_overflow(notice_client, prefix, width):
    path = prefix + '/r/player-overview/'
    expired = notice_client.get(path)
    with patch('apps.reports.views.storage.output_root', side_effect=OSError('offline')):
        outage = notice_client.get(path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, response in [('expired', expired), ('storage', outage)]:
            page, _, errors = open_notice(browser, response, path, width=width)
            expect(page.locator('.ui-notice-page h1')).to_be_visible()
            assert page.locator('body').evaluate('e => e.scrollWidth <= innerWidth + 1')
            assert page.locator('html').get_attribute('data-theme') in ('light', 'dark')
            if os.environ.get('TRELLUM_NOTICE_SCREENSHOTS'):
                output = Path(os.environ['TRELLUM_NOTICE_SCREENSHOTS'])
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'{name}-{width}.png'), full_page=True)
            assert not [error for error in errors if 'script-src' in error]
            page.close()
        browser.close()


@pytest.mark.heavy
def test_rebuild_timeout_stays_unconfirmed_without_replay(notice_client, prefix):
    path = prefix + '/r/player-overview/'
    response = notice_client.get(path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page, requests, _ = open_notice(browser, response, path, post_status=-1)
        page.locator('#reportRebuildNow').click()
        page.clock.fast_forward(15000)
        expect(page.locator('#reportRebuildNow')).to_have_text('Rebuild unconfirmed')
        page.clock.fast_forward(60000)
        assert len(requests) == 1
        browser.close()


@pytest.mark.heavy
def test_storage_checks_pause_while_hidden_and_resume(notice_client, prefix):
    path = prefix + '/r/player-overview/'
    with patch('apps.reports.views.storage.output_root', side_effect=OSError('offline')):
        response = notice_client.get(path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page, requests, _ = open_notice(browser, response, path, outage=True)
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable: true, value: true});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.clock.fast_forward(60000)
        assert requests == []
        page.evaluate("""() => {
            Object.defineProperty(document, 'hidden', {configurable: true, value: false});
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        with page.expect_response(lambda r: r.request.method == 'HEAD'):
            page.clock.fast_forward(5000)
        assert len(requests) == 1
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        page.clock.fast_forward(60000)
        assert len(requests) == 1
        browser.close()


def test_notice_rebuild_keeps_csrf_enforcement(developer, prefix, report_row):
    report_row.data_expired_at = timezone.now()
    report_row.save(update_fields=['data_expired_at'])
    client = Client(enforce_csrf_checks=True)
    client.force_login(developer)
    response = client.get(prefix + '/r/player-overview/')
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode())[1]
    url = prefix + '/api/reports/player-overview/run'
    assert client.post(url, data='{}', content_type='application/json').status_code == 403
    assert not Run.objects.filter(report=report_row).exists()
    response = client.post(url, data='{}', content_type='application/json', HTTP_X_CSRFTOKEN=token)
    assert response.json() == {'ok': True, 'status': 'queued'}
    assert Run.objects.filter(report=report_row).count() == 1


def test_notice_policy_keeps_tenant_report_assets_exempt(notice_client, prefix, studio_tree):
    notice = notice_client.get(prefix + '/r/player-overview/')
    assert 'Content-Security-Policy' in notice
    output = studio_tree.output_dir / 'player-overview'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'index.html').write_text('<html><body><script>window.tenantReport = true;</script></body></html>')
    content = notice_client.get(prefix + '/r/player-overview/index.html')
    assert content.status_code == 200
    assert 'Content-Security-Policy' not in content
    assert 'Content-Security-Policy-Report-Only' not in content
