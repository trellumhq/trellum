import pytest
from apps.reports.recipient_choices import recipient_snapshot, recipient_page
from apps.orgs.models import PermissionGroup, PermissionGroupMembership

pytestmark = pytest.mark.django_db


def test_full_audiences_are_independent_of_page_and_search(studio, org, make_user, grant_studio):
    people = [make_user(f'person-{i:03}@example.test', org=org) for i in range(60)]
    for person in people:
        grant_studio(person, studio, 'viewer')
    group = PermissionGroup.objects.create(org=org, name='Audience')
    PermissionGroupMembership.objects.bulk_create([PermissionGroupMembership(group=group, user=person) for person in people])
    snapshot = recipient_snapshot(studio)
    page = recipient_page(studio, query='person-059', selected_people=[str(people[0].pk)])
    assert [row['id'] for row in page['members']] == [people[-1].pk]
    assert page['selected']['members'][0]['id'] == people[0].pk
    assert page['roles']['everyone'] == 60
    assert snapshot['groups'][0]['count'] == 60
    assert page['pagination']['members']['audience_total'] == 60
    assert len(recipient_page(studio)['members']) == 25


def test_selected_foreign_and_invalid_ids_never_resolve(studio, org, make_user, grant_studio):
    user = make_user('eligible@example.test', org=org); grant_studio(user, studio, 'viewer')
    foreign = make_user('foreign@example.test')
    page = recipient_page(studio, selected_people=[str(foreign.pk), '-1', '9'*30, 'bad'])
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
    page = recipient_page(studio, include_inactive=True)
    assert page['pagination']['members']['choice_total'] == 1
    assert page['pagination']['members']['audience_total'] == 0


# ── The SQL audience must equal the Python resolvers it replaces ───────────

def _python_audience(studio, report, include_inactive=False):
    """The pre-SQL answer: the full snapshot, then slicing in memory."""
    snapshot = recipient_snapshot(studio, report, include_inactive=include_inactive)
    return ([row['id'] for row in snapshot['members']],
            [(row['id'], row['count']) for row in snapshot['groups']], snapshot['roles'])


def _sql_audience(studio, report, include_inactive=False):
    full = recipient_page(studio, report, include_inactive=include_inactive, size=1000)
    return ([row['id'] for row in full['members']],
            [(row['id'], row['count']) for row in full['groups']], full['roles'])


@pytest.fixture
def audience_matrix(org, other_org, studio, studio2, make_user, make_group, attach_group, grant_studio, settings):
    """Every way a person can come to have (or not have) a role on ``studio``."""
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True  # selected-report grants may be created
    from apps.orgs.models import PermissionGroupGrant
    from apps.reports.models import Report, ReportPermissionGrant

    shared = Report.objects.create(studio=studio, slug='shared')
    secret = Report.objects.create(studio=studio, slug='secret', audience=Report.AUDIENCE_PRIVATE)
    elsewhere = Report.objects.create(studio=studio2, slug='elsewhere', audience=Report.AUDIENCE_PRIVATE)
    people = {}

    def person(name, **kwargs):
        people[name] = make_user(f'{name}@matrix.test', org=org, **kwargs)
        return people[name]

    person('org-admin', org_role='admin')
    for role in ('viewer', 'developer', 'admin'):
        grant_studio(person(f'direct-{role}'), studio, role)
    person('nothing')
    grant_studio(person('other-studio'), studio2, 'viewer')
    grant_studio(person('inactive'), studio, 'viewer')
    people['inactive'].is_active = False
    people['inactive'].save(update_fields=['is_active'])
    for role in ('viewer', 'developer'):
        attach_group(person(f'default-{role}'), make_group(f'Default {role}', default_studio_role=role))
    attach_group(person('group-admin'), make_group('Org admins', org_role='admin'))
    grants = {
        'grant-viewer-all': ('viewer', 'all', None), 'grant-developer': ('developer', 'all', None),
        'grant-viewer-secret': ('viewer', 'selected', secret), 'grant-viewer-shared': ('viewer', 'selected', shared),
        'grant-viewer-private-all': ('viewer', 'all', secret),
    }
    for name, (role, scope, assigned) in grants.items():
        group = make_group(name)
        grant = PermissionGroupGrant.objects.create(group=group, studio=studio, role=role, viewer_scope=scope)
        if assigned is not None:
            ReportPermissionGrant.objects.create(grant=grant, report=assigned)
        attach_group(person(name), group)
    # A grant on another studio, a member of two groups, and people outside the organization.
    attach_group(person('grant-elsewhere'), make_group('Elsewhere only', grants=[(studio2, 'developer')]))
    attach_group(person('two-groups'), make_group('Two A', default_studio_role='viewer'))
    attach_group(people['two-groups'], make_group('Two B', grants=[(studio, 'developer')]))
    stranger = make_user('stranger@matrix.test')
    attach_group(stranger, make_group('Stranger group', default_studio_role='admin'))
    people['stranger'] = stranger
    operator = make_user('operator@matrix.test')
    operator.is_superuser = True
    operator.save(update_fields=['is_superuser'])
    attach_group(operator, make_group('Operator group'))
    people['operator'] = operator
    people['foreign'] = make_user('foreign@matrix.test', org=other_org)
    return {'people': people, 'shared': shared, 'secret': secret, 'elsewhere': elsewhere}


@pytest.mark.parametrize('blocked', [False, True])
@pytest.mark.parametrize('include_inactive', [False, True])
@pytest.mark.parametrize('target', [None, 'shared', 'secret'])
def test_sql_audience_matches_the_python_resolvers(
        target, include_inactive, blocked, audience_matrix, studio, monkeypatch):
    monkeypatch.setattr('apps.core.report_access.selected_report_access_block_reason',
                        lambda: 'blocked' if blocked else None)
    report = audience_matrix[target] if target else None
    members, groups, counts = _python_audience(studio, report, include_inactive)
    sql_members, sql_groups, sql_counts = _sql_audience(studio, report, include_inactive)
    assert sql_members == members
    assert sql_groups == groups
    assert sql_counts == counts


def test_sql_audience_matrix_leaves_both_eligible_and_ineligible_people(audience_matrix, studio):
    members = {row['email'] for row in recipient_snapshot(studio)['members']}
    assert 'direct-viewer@matrix.test' in members and 'nothing@matrix.test' not in members
    assert 'stranger@matrix.test' not in members and 'operator@matrix.test' not in members


def test_sql_audience_matches_for_an_inactive_organization(audience_matrix, studio, org):
    org.is_active = False
    org.save(update_fields=['is_active'])
    studio.refresh_from_db()
    assert _sql_audience(studio, None) == _python_audience(studio, None)
    assert _sql_audience(studio, audience_matrix['shared']) == _python_audience(studio, audience_matrix['shared'])


@pytest.mark.parametrize('blocked', [False, True])
def test_sql_audience_matches_in_a_studio_without_private_reports(audience_matrix, studio, monkeypatch, blocked):
    from apps.reports.models import Report

    Report.objects.filter(studio=studio, audience=Report.AUDIENCE_PRIVATE).delete()
    monkeypatch.setattr('apps.core.report_access.selected_report_access_block_reason',
                        lambda: 'blocked' if blocked else None)
    assert _sql_audience(studio, audience_matrix['shared']) == _python_audience(studio, audience_matrix['shared'])


def test_search_and_paging_match_the_in_memory_slice(audience_matrix, studio):
    everyone = recipient_snapshot(studio)
    emails = [row['email'] for row in everyone['members']]
    page = recipient_page(studio, query='GRANT-', size=2)
    expected = [email for email in emails if 'grant-' in email]
    assert len(expected) > 4
    assert [row['email'] for row in page['members']] == expected[:2]
    assert page['pagination']['members']['total'] == len(expected)
    assert page['pagination']['members']['has_next'] is True
    second = recipient_page(studio, query='grant-', page=2, size=2)
    assert [row['email'] for row in second['members']] == expected[2:4]
    assert recipient_page(studio, query='%')['members'] == []  # wildcards are literal
    groups = recipient_page(studio, query='default')
    assert [row['name'] for row in groups['groups']] == ['Default developer', 'Default viewer']
    assert recipient_page(studio, query='nosuchperson')['pagination']['members']['total'] == 0


def test_search_is_a_fixed_number_of_queries_whatever_the_org_size(
        studio, org, make_user, grant_studio, CaptureQueriesContext=None):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    for i in range(30):
        grant_studio(make_user(f'many-{i:03}@example.test', org=org), studio, 'viewer')
    with CaptureQueriesContext(connection) as small:
        recipient_page(studio, query='many')
    for i in range(30, 120):
        grant_studio(make_user(f'many-{i:03}@example.test', org=org), studio, 'viewer')
    with CaptureQueriesContext(connection) as large:
        page = recipient_page(studio, query='many', page=2)
    assert len(small) == len(large) <= 16
    assert page['pagination']['members']['total'] == 120 and len(page['members']) == 25
