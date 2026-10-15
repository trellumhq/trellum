"""Chromium desktop check that Report Analytics filters and sorts like the unbounded page.

A studio up to the threshold filters and sorts in the browser with no request. A
larger one searches and sorts on the server after a 400 ms pause, one click per
header. Set ``PORTAL_UX_SHOTS`` to a directory to also save 1440 and 412 pixel
screenshots.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.reports.models import Report, ReportSourceSummary, ReportViewDaily, ReportViewEvent

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def seeded(studio, org_admin, login):
    now = timezone.now()
    names = ['Cart Funnel', 'Checkout Experiment', 'Daily Revenue', 'Retention Cohorts', 'Marketing Mix', 'Session Quality']
    reports = Report.objects.bulk_create([Report(studio=studio, slug=name.lower().replace(' ', '-'), name=name) for name in names])
    for index, report in enumerate(reports[:4]):
        ReportViewDaily.objects.create(report=report, date=now.date(), views=(index + 1) * 10)
        ReportViewEvent.objects.create(report=report, user=org_admin, bucket=now - timedelta(hours=index))
    # Built a day ago and never viewed: the quiet "unseen" chip.
    ReportSourceSummary.objects.create(report=reports[5], attempt_status='success', attempt_at=now - timedelta(days=1))
    return login(org_admin)


def _open(browser, live_server, client, width, path):
    context = browser.new_context(viewport={'width': width, 'height': 900})
    context.add_cookies([{'name': name, 'value': cookie.value, 'url': live_server.url} for name, cookie in client.cookies.items()])
    page = context.new_page()
    page.goto(live_server.url + path, wait_until='domcontentloaded')
    return context, page


def test_small_studio_filters_and_sorts_instantly_without_requests(live_server, studio, seeded):
    from playwright.sync_api import expect, sync_playwright

    path = f'/s/{studio.org.slug}/{studio.slug}/analytics'
    shots = os.environ.get('PORTAL_UX_SHOTS')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context, page = _open(browser, live_server, seeded, 1440, path)
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            rows = page.locator('#analyticsTable tbody tr[data-name]')
            expect(rows).to_have_count(6)
            # Main's controls only: range pills and one filter box. No selects or buttons.
            assert page.locator('.analytics-range .ui-pill').all_inner_texts() == ['7d', '30d', '90d']
            assert page.locator('main select, main form, main button').count() == 0
            assert page.get_by_role('button', name='Search').count() == 0
            assert page.get_by_text('unavailable').count() == 0
            expect(page.locator('.ui-badge', has_text='unseen')).to_have_count(1)
            expect(page.locator('tr[data-name="session quality"] .ui-badge')).to_contain_text('unseen')
            expect(page.locator('#analyticsTable tbody .ui-row-actions a', has_text='Open').first).to_be_visible()
            assert page.locator('#analyticsTable tbody tr[data-name]').first.get_attribute('data-name') == 'cart funnel'
            if shots:
                page.wait_for_timeout(300)
                page.screenshot(path=str(Path(shots) / 'analytics-fixed-1440.png'))

            # Typing filters at once: no pause, no request.
            page.locator('#analyticsSearch').fill('mark')
            expect(page.locator('#analyticsTable tbody tr[data-name]:visible')).to_have_count(1)
            page.locator('#analyticsSearch').fill('')
            expect(page.locator('#analyticsTable tbody tr[data-name]:visible')).to_have_count(6)
            # Click a header to sort; click again to reverse.
            page.locator('th[data-sort="views"]').click()
            expect(rows.first).to_have_attribute('data-name', 'marketing mix')
            page.locator('th[data-sort="views"]').click()
            expect(rows.first).to_have_attribute('data-name', 'retention cohorts')
            expect(page.locator('th[data-sort="views"]')).to_have_attribute('aria-sort', 'descending')
            assert [u for u in requests if '/analytics' in u] == []
            # A range pill is a plain link, as before.
            page.get_by_role('link', name='7d', exact=True).click()
            page.wait_for_url('**?days=7')
            assert not errors
            context.close()

            if shots:
                context, page = _open(browser, live_server, seeded, 412, path)
                page.wait_for_timeout(500)
                page.screenshot(path=str(Path(shots) / 'analytics-fixed-412.png'))
                context.close()
        finally:
            browser.close()


def test_large_studio_searches_and_sorts_on_the_server_after_a_pause(live_server, studio, seeded, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    from apps.reports import analytics

    monkeypatch.setattr(analytics, 'CLIENT_ROWS', 4)
    monkeypatch.setattr(analytics, 'PAGE_SIZE', 4)
    path = f'/s/{studio.org.slug}/{studio.slug}/analytics'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context, page = _open(browser, live_server, seeded, 1440, path)
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url) if '/analytics' in request.url else None)
            rows = page.locator('#analyticsTable tbody tr[data-name]')
            expect(rows).to_have_count(4)
            # Main's pager look, only because the studio is beyond the threshold.
            expect(page.locator('.ui-pager')).to_contain_text('1–4 of 6')
            assert page.get_by_role('button', name='Search').count() == 0 and page.locator('main select').count() == 0

            # Typing is debounced: five quick keystrokes make one request.
            page.locator('#analyticsSearch').press_sequentially('check', delay=40)
            expect(rows).to_have_count(1)
            assert len(requests) == 1 and 'q=check' in requests[0]
            assert 'q=check' in page.url
            assert page.evaluate("document.activeElement.id") == 'analyticsSearch'
            expect(page.locator('.ui-pager')).to_have_count(0)

            page.locator('#analyticsSearch').fill('')
            expect(rows).to_have_count(4)
            # A header sorts on the server in one click, keeping the box focused-state free of reloads.
            page.locator('th[data-sort="views"]').click()
            expect(rows.first).to_have_attribute('data-name', 'marketing mix')
            expect(page.locator('th[data-sort="views"]')).to_have_attribute('aria-sort', 'ascending')
            page.locator('th[data-sort="views"]').click()
            expect(rows.first).to_have_attribute('data-name', 'retention cohorts')
            assert 'sort=views' in page.url and 'direction=desc' in page.url
            assert not errors
            context.close()
        finally:
            browser.close()
