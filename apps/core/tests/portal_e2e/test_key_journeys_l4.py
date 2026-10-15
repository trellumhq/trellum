"""Key journeys: invite and member access, datasource connection test (L4 routes)."""
import contextlib
import re
import socket
import threading

import pytest

from .key_journeys import DESKTOP, VARIANTS, bind, browser_session, record_metrics

pytestmark = pytest.mark.portal_e2e
S = '/s/alpha/studio-0'
INVITEE = 'new.hire@example.invalid'


def invitation_state():
    from apps.accounts.models import Invitation
    row = Invitation.objects.filter(org__slug='alpha', email=INVITEE).first()
    if row is None:
        return None
    return {'token': row.token, 'grants': row.studio_grants, 'role': row.org_role, 'accepted': row.accepted_at is not None,
            'count': Invitation.objects.filter(org__slug='alpha').count()}


def member_state():
    from django.contrib.auth import get_user_model
    from apps.studios.models import StudioMembership
    user = get_user_model().objects.filter(email=INVITEE).first()
    if user is None:
        return None
    roles = list(StudioMembership.objects.filter(user=user, studio__slug='studio-0', studio__org__slug='alpha').values_list('role', flat=True))
    return {'id': user.pk, 'roles': roles}


@pytest.mark.parametrize('variant', [pytest.param('chromium-desktop')])
def test_invite_member_and_change_access(journey, variant):
    from playwright.sync_api import expect
    from .seed import PASSWORD
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with browser_session(journey, 'org-admin', spec, condition='key-invite-access') as (page, context):
        page.goto(origin + '/orgs/alpha/settings/invites')
        # No mail provider is configured in the disposable runtime: the invitation is a copyable link only.
        expect(page.get_by_text('Email delivery is not configured')).to_be_visible()
        journey.checkpoint('no-mail')
        page.get_by_label('Email', exact=True).fill(INVITEE)
        page.locator('select[name=studio_role_1]').select_option('viewer')
        send = page.locator('input[name=send_email]')
        if send.count() and send.is_checked():
            send.uncheck()
        page.get_by_role('button', name='Create invitation').click()
        expect(page.locator('main, body').first).to_contain_text(INVITEE)
        state = journey.database(invitation_state)
        assert state and state['role'] == 'member' and not state['accepted']
        assert [grant['role'] for grant in state['grants']] == ['viewer']
        journey.checkpoint('db-persistence')
        page.reload()
        expect(page.locator('main, body').first).to_contain_text(INVITEE)
        journey.checkpoint('reload-persistence')
        bind(journey, 'route-306', 'POST', None, 'org-admin', spec, ())
        # The invitee accepts from a separate anonymous browser context using the copyable link.
        guest = context.browser.new_context(viewport={'width': spec['width'], 'height': spec['height']})
        try:
            guest_page = guest.new_page()
            guest_page.goto(origin + f"/invite/{state['token']}")
            guest_page.locator('input[name=name]').fill('New Hire')
            guest_page.locator('input[name=password1]').fill(PASSWORD)
            guest_page.locator('input[name=password2]').fill(PASSWORD)
            guest_page.get_by_role('button', name=re.compile('Create account')).click()
            guest_page.wait_for_url(lambda url: '/invite/' not in url)
        finally:
            guest.close()
        member = journey.database(member_state)
        assert member and member['roles'] == ['viewer']
        # Change the new member's studio access and save; the result survives a reload.
        page.goto(origin + f"/orgs/alpha/settings/members/{member['id']}/studios")
        form = page.locator('form[id^=studio-role-]').first
        form.locator('select[name=role]').select_option('developer')
        form.get_by_role('button', name='Save changes').click()
        expect(form.locator('[data-settings-dirty-status]')).to_contain_text(f'{INVITEE} is now developer in Studio 0.')
        journey.checkpoint('visible-feedback')
        assert journey.database(member_state)['roles'] == ['developer']
        page.reload()
        expect(page.locator('form[id^=studio-role-]').first.locator('select[name=role]')).to_have_value('developer')
        bind(journey, 'route-292', 'POST', None, 'org-admin', spec, ())
        record_metrics(journey, page)


@contextlib.contextmanager
def silent_server():
    """A local endpoint that accepts connections and never answers (a real probe timeout)."""
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    held, stop = [], threading.Event()

    def accept():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(listener.accept()[0])
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(2)
        for connection in held:
            connection.close()
        listener.close()


def add_silent_warehouse(database, silent_port):
    """A network source whose endpoint accepts and never answers; uses a driver installed with the framework."""
    from apps.datasources.models import DataSource
    from apps.orgs.models import Organization
    DataSource.objects.create(org=Organization.objects.get(slug='alpha'), name='warehouse-hang', type='vertica',
                              config={'host': '127.0.0.1', 'port': silent_port, 'database': database},
                              credentials={'user': 'dbadmin', 'password': 'disposable'})


@pytest.mark.parametrize('variant', [pytest.param('chromium-desktop')])
def test_datasource_connection_test(journey, variant):
    from playwright.sync_api import expect
    spec = VARIANTS[variant]
    origin = journey.unit.origin
    with silent_server() as silent_port:
        journey.database(add_silent_warehouse, journey.unit.database, silent_port)
        with browser_session(journey, 'org-admin', spec, condition='key-datasource-test') as (page, context):
            page.goto(origin + '/orgs/alpha/settings/datasources')
            # Success: the seeded organization file source is found by the real test action.
            ok_row = page.locator('tbody tr', has=page.get_by_role('link', name='sample', exact=True))
            ok_row.get_by_role('button', name='Test').click()
            expect(ok_row.locator('[data-ds-result]')).to_contain_text('File found', timeout=30000)
            journey.checkpoint('test-success')
            # Timeout: the probe is bounded and reports a failure instead of hanging the request.
            hang_row = page.locator('tbody tr', has=page.get_by_role('link', name='warehouse-hang', exact=True))
            hang_row.get_by_role('button', name='Test').click()
            expect(hang_row.locator('[data-ds-result]')).to_contain_text(re.compile('timed out', re.I), timeout=45000)
            expect(hang_row.locator('[data-ds-result]')).not_to_contain_text('disposable')
            journey.checkpoint('test-timeout')
            bind(journey, 'route-263', 'POST', None, 'org-admin', spec, (), every=True)
            record_metrics(journey, page)
