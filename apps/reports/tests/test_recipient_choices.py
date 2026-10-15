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


def _people_and_groups(org, studio_tree, make_user, grant_studio, count):
    people = [make_user(f'browse-{i:03}@example.test', org=org) for i in range(count)]
    for person in people:
        grant_studio(person, studio_tree, 'viewer')
    groups = []
    for i, person in enumerate(people):
        group = PermissionGroup.objects.create(org=org, name=f'Browse {i:03}'); groups.append(group)
        PermissionGroupMembership.objects.create(group=group, user=person)
    return people, groups


def test_recipients_endpoint_lists_the_whole_audience_unless_asked_to_search(
        org_admin, studio_tree, report_row, org, make_user, grant_studio, login):
    _people_and_groups(org, studio_tree, make_user, grant_studio, 60)
    url = f'/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/recipients'
    whole = login(org_admin).get(url).json()
    assert len(whole['members']) >= 60 and len(whole['groups']) == 60
    assert whole['truncated'] is False and 'pagination' not in whole
    assert whole['roles']['everyone'] >= 60 and whole['studio_name'] == studio_tree.name
    searched = login(org_admin).get(url, {'q': 'browse-059', 'page': 1}).json()
    assert [row['email'] for row in searched['members']] == ['browse-059@example.test']
    assert searched['pagination']['members']['total'] == 1


def test_edit_form_lists_everyone_and_only_a_huge_audience_searches_remotely(
        org_admin, studio_tree, report_row, org, make_user, grant_studio, login, monkeypatch):
    from apps.reports.models import EmailSchedule
    people, groups = _people_and_groups(org, studio_tree, make_user, grant_studio, 12)
    schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    schedule.recipients.add(people[-1]); schedule.recipient_groups.add(groups[-1])
    client = login(org_admin)
    edit = f'/s/{org.slug}/{studio_tree.slug}/deliveries/{schedule.pk}/edit'
    html = client.get(edit).content.decode().split('<main', 1)[1]
    assert html.count('type="checkbox" name="recipient_ids"') >= 12 and html.count('type="checkbox" name="recipient_group_ids"') == 12
    for marker in ('data-recipient-source', 'data-picker-fallback', 'Browse choices', 'Search recipients', 'name="action"'):
        assert marker not in html
    monkeypatch.setattr('apps.reports.recipient_choices.CHOICE_LIMIT', 5)
    html = client.get(edit).content.decode().split('<main', 1)[1]
    source = f'/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/recipients'
    assert f'data-recipient-source="{source}"' in html
    assert html.count('type="checkbox" name="recipient_ids"') == 6 and html.count('type="checkbox" name="recipient_group_ids"') == 6
    assert 'checked' in html.split(f'value="{people[-1].pk}"', 1)[1][:80]  # the saved selection stays rendered
    # Validation still knows the whole audience, so a person found by remote search can be saved.
    url = f'/s/{org.slug}/{studio_tree.slug}/api/reports/{report_row.slug}/schedules/{schedule.pk}'
    body = {'freq': 'weekly', 'send_time': '16:47', 'timezone': 'Europe/Amsterdam', 'weekday': '3', 'month_day': '1',
            'enabled': 'on', 'recipient_ids': [str(people[0].pk)]}
    response = client.post(url, body)
    assert response.status_code == 302
    assert list(schedule.recipients.values_list('pk', flat=True)) == [people[0].pk]


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
