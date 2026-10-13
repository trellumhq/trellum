import pytest
from apps.reports.recipient_choices import recipient_snapshot, page_snapshot
from apps.orgs.models import PermissionGroup, PermissionGroupMembership

pytestmark = pytest.mark.django_db


def test_full_audiences_are_independent_of_page_and_search(studio, org, make_user, grant_studio):
    people = [make_user(f'person-{i:03}@example.test', org=org) for i in range(60)]
    for person in people:
        grant_studio(person, studio, 'viewer')
    group = PermissionGroup.objects.create(org=org, name='Audience')
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(group=group, user=person) for person in people])
    snapshot = recipient_snapshot(studio)
    page = page_snapshot(snapshot, query='person-059', selected_people=[str(people[0].pk)])
    assert [row['id'] for row in page['members']] == [people[-1].pk]
    assert page['selected']['members'][0]['id'] == people[0].pk
    assert page['roles']['everyone'] == 60
    assert snapshot['groups'][0]['count'] == 60
    assert page['pagination']['members']['audience_total'] == 60
    assert len(page_snapshot(snapshot)['members']) == 25


def test_selected_foreign_and_invalid_ids_never_resolve(studio, org, make_user, grant_studio):
    user = make_user('eligible@example.test', org=org); grant_studio(user, studio, 'viewer')
    foreign = make_user('foreign@example.test')
    page = page_snapshot(recipient_snapshot(studio), selected_people=[str(foreign.pk), '-1', '9'*30, 'bad'])
    assert page['selected']['members'] == []


def test_native_delivery_browse_preserves_off_page_choices_and_draft(org_admin, studio_tree, report_row, org, make_user, grant_studio, login):
    from apps.reports.models import EmailSchedule
    people = [make_user(f'browse-{i:03}@example.test', org=org) for i in range(60)]
    for person in people:
        grant_studio(person, studio_tree, 'viewer')
    groups = []
    for i, person in enumerate(people):
        group = PermissionGroup.objects.create(org=org, name=f'Browse {i:03}'); groups.append(group)
        PermissionGroupMembership.objects.create(group=group, user=person)
    schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    schedule.recipients.add(people[-1]); schedule.recipient_groups.add(groups[-1])
    url = f'/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/schedules/{schedule.pk}'
    from html.parser import HTMLParser
    class Checked(HTMLParser):
        def __init__(self): super().__init__(); self.values = set()
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'input' and 'checked' in values: self.values.add((values.get('name'), values.get('value')))
    rendered = login(org_admin).get(f'/s/{org.slug}/{studio_tree.slug}/deliveries/{schedule.pk}/edit')
    checked = Checked(); checked.feed(rendered.content.decode())
    assert ('recipient_ids', str(people[-1].pk)) in checked.values
    assert ('recipient_group_ids', str(groups[-1].pk)) in checked.values
    body = {'action': 'browse_choices', 'people_page': '2', 'group_page': '2',
            'recipient_ids': [str(people[-1].pk)], 'recipient_group_ids': [str(groups[-1].pk)],
            'freq': 'weekly', 'send_time': '16:47', 'timezone': 'Europe/Amsterdam',
            'weekday': '3', 'month_day': '1', 'enabled': 'on'}
    response = login(org_admin).post(url, body)
    assert response.status_code == 200
    form = response.context['form']
    assert form.data['send_time'] == '16:47' and form.data['timezone'] == 'Europe/Amsterdam'
    assert len(form.fields['recipient_ids'].choices) <= 26
    assert str(people[-1].pk) in {str(value) for value, label in form.fields['recipient_ids'].choices}
    assert str(groups[-1].pk) in {str(value) for value, label in form.fields['recipient_group_ids'].choices}
    schedule.refresh_from_db(); assert schedule.freq == 'daily'
    body['action'] = 'save'  # Paging state cannot turn an explicit Save into browse.
    response = login(org_admin).post(url, body)
    assert response.status_code == 302
    schedule.refresh_from_db(); assert schedule.send_hour == 16 and schedule.send_minute == 47
    assert list(schedule.recipients.values_list('pk', flat=True)) == [people[-1].pk]


def test_inactive_choices_do_not_inflate_delivery_audience_and_superuser_group_parity(studio, org, make_user, grant_studio):
    person = make_user('inactive@example.test', org=org); grant_studio(person, studio, 'viewer')
    person.is_active = False; person.save(update_fields=['is_active'])
    operator = make_user('operator-group@example.test')
    operator.is_superuser = True; operator.save(update_fields=['is_superuser'])
    group = PermissionGroup.objects.create(org=org, name='Mixed audience')
    PermissionGroupMembership.objects.create(group=group, user=person)
    PermissionGroupMembership.objects.create(group=group, user=operator)
    snapshot = recipient_snapshot(studio, include_inactive=True)
    assert [row['id'] for row in snapshot['members']] == [person.pk]
    assert snapshot['groups'][0]['count'] == 1  # Active superuser, despite no OrgMembership.
    assert snapshot['roles']['everyone'] == 0
    page = page_snapshot(snapshot)
    assert page['pagination']['members']['choice_total'] == 1
    assert page['pagination']['members']['audience_total'] == 0
