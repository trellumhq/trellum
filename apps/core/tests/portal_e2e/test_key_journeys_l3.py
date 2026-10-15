"""Key journeys: dashboard login, schedules and alerts (L3 routes)."""
import re

import pytest

from apps.core.tests.browser_login import sign_in
from .key_journeys import VARIANTS, bind, browser_session, record_metrics

pytestmark = pytest.mark.portal_e2e
S = '/s/alpha/studio-0'
HEAVY = pytest.mark.heavy
DESKTOP_ONLY = [pytest.param('chromium-desktop')]
BOTH = [pytest.param('chromium-desktop'), pytest.param('webkit-mobile', marks=HEAVY)]


@pytest.mark.parametrize('variant', BOTH)
def test_login_lands_on_dashboard(journey, variant):
    from playwright.sync_api import expect
    from .identities import ROLES
    from .seed import PASSWORD
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-login-dashboard', login_first=False) as (page, context):
        sign_in(page, origin, ROLES['org-admin'], PASSWORD)
        page.wait_for_url(origin + '/')
        journey.assert_actor(context, 'org-admin')
        expect(page.get_by_role('heading', name='Studios')).to_be_visible()
        card = page.locator('a.card', has_text='Studio 0')
        expect(card).to_be_visible()
        expect(card).to_contain_text('reports')
        journey.checkpoint('landing')
        journey.checkpoint('studio-card')
        bind(journey, 'route-244', 'GET', None, 'org-admin', spec, ())
        record_metrics(journey, page)


def schedule_for(slug):
    from apps.reports.models import EmailSchedule
    row = EmailSchedule.objects.get(report__studio__org__slug='alpha', report__slug=slug)
    return {'hour': row.send_hour, 'minute': row.send_minute, 'roles': list(row.recipient_roles), 'count': EmailSchedule.objects.filter(report__studio__org__slug='alpha').count()}


def check_picker_option(page, name, value):
    """Tick a recipient in the opened choice picker (the picker owns the checkboxes while enhanced)."""
    page.locator('[data-recipient-picker] .ui-picker-trigger').first.click()
    page.locator(f'.ui-picker-popup input[name={name}][value="{value}"]').check()
    page.keyboard.press('Escape')


@pytest.mark.parametrize('variant', DESKTOP_ONLY)
def test_schedules_list_create_edit(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-schedules') as (page, context):
        page.goto(origin + S + '/deliveries')
        rows = page.locator('tbody tr', has=page.locator('a[href*="/r/"]'))
        expect(rows).to_have_count(1)
        expect(page.get_by_role('link', name='Report 0', exact=True)).to_be_visible()
        journey.checkpoint('list')
        bind(journey, 'route-141', 'GET', None, 'org-admin', spec, ())
        # Create: choose a report, then the schedule form.
        page.get_by_role('link', name='Add delivery').click()
        # Choosing the report opens its form at once; the picker has no Continue button.
        with page.expect_navigation():
            page.locator('select[name=report]').select_option(label='Report 1 \u00b7 Report')
        expect(page.get_by_role('heading', name='Add delivery')).to_be_visible()
        page.locator('#id_send_time').fill('07:30')
        check_picker_option(page, 'recipient_roles', 'admins')
        page.get_by_role('button', name='Create delivery').click()
        page.wait_for_url(origin + S + '/deliveries*')
        expect(rows).to_have_count(2)
        created = journey.database(schedule_for, 'report-0001')
        assert (created['hour'], created['minute'], created['roles']) == (7, 30, ['admins']) and created['count'] == 2
        journey.checkpoint('db-persistence')
        page.reload()
        expect(page.get_by_role('link', name='Report 1', exact=True)).to_be_visible()
        journey.checkpoint('reload-persistence')
        journey.checkpoint('visible-feedback')
        bind(journey, 'route-142', 'POST', None, 'org-admin', spec, ())
        # Edit the new schedule.
        rows.filter(has=page.get_by_role('link', name='Report 1', exact=True)).get_by_role('link', name='Edit').click()
        expect(page.get_by_role('heading', name='Edit delivery')).to_be_visible()
        page.locator('#id_send_time').fill('09:15')
        page.get_by_role('button', name='Save delivery').click()
        expect(page.locator('#delivery-schedule-form [data-settings-dirty-status]')).to_contain_text(re.compile('saved', re.I))
        edited = journey.database(schedule_for, 'report-0001')
        assert (edited['hour'], edited['minute'], edited['count']) == (9, 15, 2)
        page.goto(origin + S + '/deliveries')
        expect(rows.filter(has=page.get_by_role('link', name='Report 1', exact=True))).to_contain_text('09:15')
        bind(journey, 'route-178', 'POST', None, 'org-admin', spec, ())
        record_metrics(journey, page)


def alert_state():
    from apps.alerts.models import AlertRule
    rule = AlertRule.objects.get(org__slug='alpha', studio__slug='studio-0')
    return {'name': rule.name, 'cooldown': rule.cooldown_hours, 'count': AlertRule.objects.filter(org__slug='alpha').count(), 'pk': rule.pk}


@pytest.mark.parametrize('variant', DESKTOP_ONLY)
def test_alerts_list_edit_save(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-alerts') as (page, context):
        page.goto(origin + S + '/alerts')
        expect(page.get_by_role('link', name='Rule 0', exact=True)).to_be_visible()
        journey.checkpoint('list')
        bind(journey, 'route-196', 'GET', None, 'org-admin', spec, ())
        page.get_by_role('link', name='Edit').click()
        expect(page.get_by_role('heading', name='Edit \u201cRule 0\u201d')).to_be_visible()
        page.locator('#alertForm [name=name]').fill('Rule 0 renamed')
        page.locator('#id_cooldown_hours').fill('12')
        page.get_by_role('button', name='Save alert').click()
        expect(page.locator('#alertForm [data-settings-dirty-status]')).to_contain_text(re.compile('saved', re.I))
        journey.checkpoint('visible-feedback')
        state = journey.database(alert_state)
        assert (state['name'], state['cooldown'], state['count']) == ('Rule 0 renamed', 12, 1)
        journey.checkpoint('db-persistence')
        page.goto(origin + S + '/alerts')
        expect(page.get_by_role('link', name='Rule 0 renamed', exact=True)).to_be_visible()
        expect(page.locator('table tbody tr')).to_contain_text('12 h')
        journey.checkpoint('reload-persistence')
        bind(journey, 'route-199', 'POST', None, 'org-admin', spec, ())
        record_metrics(journey, page)
