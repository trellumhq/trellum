"""Chromium desktop check that Experiments looks and behaves like the unbounded page.

Set ``PORTAL_UX_SHOTS`` to a directory to also save 1440 and 412 pixel screenshots.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from apps.reports.models import Report, ReportExperimentSummary, ReportSourceSummary
from apps.reports.report_experiment_projection import declared_rows, project_results

pytestmark = pytest.mark.django_db(transaction=True)


def _experiment(studio, slug, name, entries, results=None, **fields):
    """``results`` maps a declared test_name to its primary KPI row."""
    report = Report.objects.create(studio=studio, slug=slug, name=name, config={'ab_test': entries}, **fields)
    payload = None
    if results:
        components = {f'c{i}': {'type': 'ab_compare', 'header': {'test_name': test}, 'body': {'kpi_rows': [kpi]}}
                      for i, (test, kpi) in enumerate(results.items())}
        payload = {'components': components}
        rows, digest = project_results(report.config, payload, list(components.values()))
    else:
        rows, digest = declared_rows(report.config)
    summary = ReportSourceSummary.objects.create(
        report=report, schema_version=2, declaration_digest=digest, state='ready' if payload else 'missing',
        quality='observed', experiment_state='ready' if payload else 'missing', has_output=bool(payload),
        observed_at=timezone.now() - timedelta(hours=1))
    ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=summary, **row) for row in rows])


@pytest.fixture
def seeded(studio, org_admin, login):
    today = timezone.now().date()
    iso = lambda days: (today - timedelta(days=days)).isoformat()  # noqa: E731
    revenue = {'metric': 'Revenue Normalised vs Control', 'higher_is_better': True}
    _experiment(studio, 'cart-funnel', 'Cart Funnel', {'test_name': 'checkout_button_color', 'split': '50/50', 'start_date': iso(61)},
                {'checkout_button_color': {**revenue, 'delta_pct': 30.0, 'ci_pct': [0.04, 68.4]}})
    _experiment(studio, 'experiments', 'Checkout Experiment', [
        {'name': 'Checkout Experiment', 'test_name': 'checkout_v2', 'split': '50/50', 'start_date': iso(93)},
        {'name': 'Pricing Experiment', 'test_name': 'starter_pack_price', 'split': '50/50', 'start_date': iso(98), 'end_date': iso(69)},
        {'name': 'Onboarding Experiment', 'test_name': 'onboarding_v2', 'split': '50/50', 'start_date': iso(150), 'end_date': iso(128), 'closed': True},
    ], {'checkout_v2': {**revenue, 'delta_pct': 3.5, 'ci_pct': [-3.1, 12.2]},
        'starter_pack_price': {**revenue, 'delta_pct': -3.4, 'ci_pct': [-13.2, 10.1]},
        'onboarding_v2': {'metric': 'Conversion vs Control', 'higher_is_better': True, 'delta_pct': 9.7, 'ci_pct': [6.2, 13.2]}},
        schedule_cron='0 9 * * *')
    plain = Report.objects.create(studio=studio, slug='plain', name='Plain report')
    # A report without an ab_test block is indexed as "no experiments".
    ReportSourceSummary.objects.create(report=plain, schema_version=2, declaration_digest=declared_rows(plain.config)[1], experiment_state='not-applicable')
    return login(org_admin)


def _page(browser, live_server, client, width):
    context = browser.new_context(viewport={'width': width, 'height': 900})
    context.add_cookies([{'name': name, 'value': cookie.value, 'url': live_server.url} for name, cookie in client.cookies.items()])
    return context, context.new_page()


def test_experiments_page_matches_the_unbounded_layout_and_filters_in_one_click(live_server, studio, seeded):
    from playwright.sync_api import expect, sync_playwright

    url = f'{live_server.url}/s/{studio.org.slug}/{studio.slug}/experiments'
    shots = os.environ.get('PORTAL_UX_SHOTS')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context, page = _page(browser, live_server, seeded, 1440)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url, wait_until='domcontentloaded')
            expect(page.locator('h1')).to_have_text('Experiments')
            # No search box, Search/Apply button, status pills beyond main's three, or paging rows.
            assert page.locator('main input[type=search], main form button, main select').count() == 0
            assert page.get_by_role('button', name='Search').count() == 0
            pills = page.locator('.filter-row .ui-pill')
            assert [' '.join(text.split()) for text in pills.all_inner_texts()] == ['All 4', 'Running 3', 'Concluded 1']
            for text in ('matching experiments', 'Page 1 of', 'Declaration coverage', 'not indexed yet', 'Observation unavailable'):
                assert page.get_by_text(text).count() == 0, text
            expect(page.locator('.ui-stat .num')).to_have_text(['3', '1', '100%'])
            expect(page.locator('.ui-stat .sub').last).to_contain_text('1 of 1 concluded were wins')
            assert page.locator('.tl-lane').count() == 3  # the closed one ended before the four month window
            expect(page.locator('tbody tr')).to_have_count(4)
            expect(page.locator('tbody tr').first).to_contain_text('Revenue Normalised vs Control')
            expect(page.locator('tbody tr').first).to_contain_text('+30.0%')
            if shots:
                page.wait_for_timeout(400)
                page.screenshot(path=str(Path(shots) / 'experiments-fixed-1440.png'))

            # One click on a pill applies immediately: no Apply step.
            page.get_by_role('link', name='Running 3').click()
            page.wait_for_url('**?status=running')
            expect(page.locator('tbody tr')).to_have_count(3)
            assert page.locator('.tl-lane').count() == 3  # the timeline ignores the filter, as it always did
            page.get_by_role('link', name='Concluded 1').click()
            page.wait_for_url('**?status=concluded')
            expect(page.locator('tbody tr')).to_have_count(1)
            page.get_by_role('link', name='All 4').click()
            expect(page.locator('tbody tr')).to_have_count(4)
            assert not errors
            context.close()

            if shots:
                context, page = _page(browser, live_server, seeded, 412)
                page.goto(url, wait_until='domcontentloaded')
                page.wait_for_timeout(600)
                page.screenshot(path=str(Path(shots) / 'experiments-fixed-412.png'))
                context.close()
        finally:
            browser.close()
