"""Shared schedule authorization and creator-independent execution."""
import json

import pytest
from django.core import mail

from apps.core import roles
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import EmailSchedule, Report, ReportPermissionGrant
from apps.reports import deliveries, notify
from apps.studios.models import StudioMembership
from apps.accounts.models import ApiKey

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('scope', [ApiKey.READ, ApiKey.READ_WRITE])
def test_api_key_write_scope_and_org_boundary(scope, client, org_admin, org, other_org,
        report_row):
    schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    prefix = f'/s/{org.slug}/{report_row.studio.slug}'
    url = f'{prefix}/api/reports/{report_row.slug}/schedules'
    _, secret = ApiKey.mint(user=org_admin, org=org, name='Delivery', scopes=scope)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {secret}'}
    result = client.get(url, **headers)
    assert result.status_code == 200
    assert result.json()['can_manage'] is (scope == ApiKey.READ_WRITE)
    assert client.post(f'{url}/{schedule.pk}', json.dumps({'enabled': False}),
        content_type='application/json', **headers).status_code == (200 if scope == ApiKey.READ_WRITE else 403)
    schedule.refresh_from_db()
    assert schedule.enabled is (scope == ApiKey.READ)
    from apps.orgs.models import OrgMembership
    OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_ADMIN)
    _, foreign = ApiKey.mint(user=org_admin, org=other_org, name='Other', scopes=scope)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {foreign}'}
    assert client.get(url, **headers).status_code == 404
    assert client.get('/me/deliveries', **headers).status_code == 403


@pytest.mark.parametrize('source', ['direct', 'group'])
@pytest.mark.parametrize('role', [roles.VIEWER, roles.DEVELOPER, roles.ADMIN])
def test_effective_role_controls_shared_schedules(source, role, login, make_user, org,
        report_row, grant_studio, make_group, attach_group, monkeypatch):
    user = make_user(f'{source}-{role}@example.test', org=org)
    if source == 'direct':
        grant_studio(user, report_row.studio, role)
    else:
        attach_group(user, make_group('Access', grants=[(report_row.studio, role)]))
    prefix = f'/s/{org.slug}/{report_row.studio.slug}'
    url = f'{prefix}/api/reports/{report_row.slug}/schedules'
    own = EmailSchedule.objects.create(report=report_row, created_by=user)
    colleague = EmailSchedule.objects.create(report=report_row, created_by=None, enabled=False)
    own.recipients.add(user)
    client = login(user)
    data = client.get(url).json()
    assert {row['id'] for row in data['schedules']} == {own.pk, colleague.pk}
    assert data['subscribed_count'] == 1
    allowed = role != roles.VIEWER
    assert data['can_manage'] is allowed
    assert all(row['editable'] is allowed for row in data['schedules'])
    assert client.get(f'{prefix}/deliveries/new').status_code == (200 if allowed else 403)
    assert client.get(f'{prefix}/deliveries/{own.pk}/edit').status_code == (200 if allowed else 404)
    body = json.dumps({'recipient_ids': [user.pk]})
    assert client.post(url, body, content_type='application/json').status_code == (200 if allowed else 404)
    target = colleague if allowed else own
    detail = f'{url}/{target.pk}'
    assert client.post(detail, json.dumps({'enabled': True}), content_type='application/json').status_code == (200 if allowed else 404)
    class InlineThread:
        def __init__(self, *, target, args, kwargs, daemon):
            self.run = lambda: target(*args, **kwargs)

        def start(self):
            self.run()
    monkeypatch.setattr('apps.reports.views.threading.Thread', InlineThread)
    monkeypatch.setattr(notify, 'send_sample_or_raise', lambda *args: None)
    assert client.post(f'{detail}/sample').status_code == (202 if allowed else 404)
    assert client.post(f'{prefix}/api/reports/{report_row.slug}/sample').status_code == (202 if allowed else 403)
    assert client.delete(detail).status_code == (200 if allowed else 404)
    if not allowed:
        own.refresh_from_db()
        assert own.enabled and own.delivery_revision is None
        assert EmailSchedule.objects.count() == 2


def test_selected_report_viewer_lists_only_authorized_report_schedules(login, make_user,
        org, report_row, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    user = make_user('selected@example.test', org=org)
    group = make_group('Selected', grants=[(report_row.studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=['viewer_scope'])
    attach_group(user, group)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    visible = EmailSchedule.objects.create(report=report_row, enabled=False)
    private = Report.objects.create(studio=report_row.studio, slug='hidden', audience=Report.AUDIENCE_PRIVATE)
    EmailSchedule.objects.create(report=private, created_by=user)
    client = login(user)
    prefix = f'/s/{org.slug}/{report_row.studio.slug}'
    for listing in (f'{prefix}/deliveries', '/me/deliveries'):
        rows = client.get(listing).context['schedule_rows']
        assert [row['schedule'].pk for row in rows] == [visible.pk]
        assert not rows[0]['editable']
    assert client.get(f'{prefix}/api/reports/{private.slug}/schedules').status_code == 404


@pytest.mark.parametrize('change', ['delete', 'deactivate', 'demote', 'revoke'])
def test_creator_change_during_render_does_not_cancel_recipient_delivery(change,
        make_user, org, report_row, grant_studio, write_meta, monkeypatch):
    creator = make_user('creator@example.test', org=org)
    recipient = make_user('recipient@example.test', org=org)
    grant_studio(creator, report_row.studio, roles.DEVELOPER)
    grant_studio(recipient, report_row.studio, roles.VIEWER)
    schedule = EmailSchedule.objects.create(report=report_row, created_by=creator)
    schedule.recipients.add(recipient)
    snapshot = deliveries.schedule_snapshot(schedule)
    write_meta(report_row.slug, last_status='success')
    def render(output):
        if change == 'delete':
            creator.delete()
        elif change == 'deactivate':
            type(creator).objects.filter(pk=creator.pk).update(is_active=False)
        elif change == 'demote':
            StudioMembership.objects.filter(user=creator, studio=report_row.studio).update(role=roles.VIEWER)
        else:
            StudioMembership.objects.filter(user=creator, studio=report_row.studio).delete()
        assert deliveries.schedule_send_current(snapshot) is not None
        return None
    monkeypatch.setattr(notify, '_try_render', render)
    notify.send_scheduled(schedule.pk)
    schedule.refresh_from_db()
    assert [message.to for message in mail.outbox] == [[recipient.email]]
    assert schedule.delivery_outcome == 'accepted' and schedule.last_accepted_at
