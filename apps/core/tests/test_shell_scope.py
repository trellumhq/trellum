"""Narrow navigation facts agree with route roles without transferring report ids."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.core import roles
from apps.core.context_processors import shell
from apps.core.permissions import get_effective
from apps.core.shell_scope import (
    can_manage_any_org, organization_choices,
    studio_choices, studio_facts,
)
from apps.core.shell_scope_views import api_choices
from apps.orgs.models import Organization, OrgMembership
from apps.reports.models import Report, ReportPermissionGrant
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def request_for(user, params=None, *, org=None, studio=None):
    request = RequestFactory().get('/', params or {})
    request.user, request.session = user, {}
    if org is not None:
        request.org = org
    if studio is not None:
        request.studio = studio
    return request


COLD_FACT_SOURCES = [
    'none', 'direct-viewer', 'direct-developer', 'direct-admin',
    'group-viewer', 'group-developer', 'group-admin', 'selected',
    'default-viewer', 'default-developer', 'default-admin', 'org-admin',
    'direct-org-admin', 'mixed', 'orphan-group',
]
# A private report only changes the outcome for viewer-level access
# (EffectiveRoles.report_scope_for); developers, admins and non-members see the
# same result either way.
PRIVATE_SENSITIVE_SOURCES = ('direct-viewer', 'default-viewer', 'group-viewer', 'selected')


@pytest.mark.parametrize('source, private', [
    (source, private) for source in COLD_FACT_SOURCES for private in (False, True)
    if not private or source in PRIVATE_SENSITIVE_SOURCES
])
def test_cold_facts_match_existing_roles(source, private, member, org, studio, studio2,
        make_group, attach_group, grant_studio, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    report = Report.objects.create(studio=studio, slug='fact-report',
        audience=Report.AUDIENCE_PRIVATE if private else Report.AUDIENCE_STUDIO)
    if source.startswith('direct-') and source != 'direct-org-admin':
        grant_studio(member, studio, source.removeprefix('direct-'))
    elif source.startswith('default-'):
        attach_group(member, make_group(source, default_studio_role=source.removeprefix('default-')))
    elif source.startswith('group-'):
        group = make_group(source, grants=[(studio, source.removeprefix('group-'))])
        attach_group(member, group)
        if private and source == 'group-viewer':
            ReportPermissionGrant.objects.create(grant=group.grants.get(), report=report)
    elif source == 'selected':
        group = make_group(source, grants=[(studio, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
        grant.save()
        attach_group(member, group)
        if private:
            ReportPermissionGrant.objects.create(grant=grant, report=report)
    elif source in ('org-admin', 'orphan-group'):
        attach_group(member, make_group(source, org_role=roles.ORG_ADMIN))
        if source == 'orphan-group':
            OrgMembership.objects.filter(user=member, org=org).delete()
    elif source == 'direct-org-admin':
        OrgMembership.objects.filter(user=member, org=org).update(role=roles.ORG_ADMIN)
    elif source == 'mixed':
        grant_studio(member, studio, roles.VIEWER)
        attach_group(member, make_group(source, default_studio_role=roles.DEVELOPER))
    expected = get_effective(request_for(member), org)
    request = request_for(member)
    with patch('apps.core.permissions.get_effective', side_effect=AssertionError('uncached broad resolver')):
        rows = list(studio_choices(request, org))
        assert {row.pk for row in rows} == {
            row.pk for row in (studio, studio2) if expected.role_for(row) is not None}
        for row in rows:
            row.org = org
            assert studio_facts(request, row) == (expected.role_for(row), expected.has_full_studio_visibility(row))
        org_rows = list(organization_choices(request))
        assert bool(org_rows) == expected.is_member
        if org_rows:
            assert org_rows[0].shell_admin == expected.is_org_admin
        assert can_manage_any_org(request, org_rows, has_more=False) == expected.is_org_admin


def test_selected_empty_studio_is_visible_but_never_full(member, org, studio, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group('empty selected', grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save()
    attach_group(member, group)
    row = studio_choices(request_for(member), org).get(pk=studio.pk)
    assert row.shell_role == roles.VIEWER
    assert not row.shell_full


def test_group_report_grant_in_another_studio_does_not_change_full_boolean(member, org, studio, studio2, make_group, attach_group, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group('full viewer', grants=[(studio, roles.VIEWER)])
    attach_group(member, group)
    private = Report.objects.create(studio=studio2, slug='private-other', audience=Report.AUDIENCE_PRIVATE)
    ReportPermissionGrant.objects.bulk_create([ReportPermissionGrant(grant=group.grants.get(), report=private)])
    row = studio_choices(request_for(member), org).get(pk=studio.pk)
    assert row.shell_full


@pytest.mark.parametrize('actor', ['member', 'superuser'])
def test_bearer_scope_and_inactive_org_match_guards(actor, member, superuser, org, other_org, studio, grant_studio):
    user = member if actor == 'member' else superuser
    grant_studio(member, studio, roles.VIEWER)
    request = request_for(user)
    request.api_key = SimpleNamespace(pk=1, org_id=other_org.pk)
    assert not studio_choices(request, org).exists()
    assert not organization_choices(request).filter(pk=org.pk).exists()
    request.api_key.org_id = org.pk
    assert studio_choices(request, org).exists()
    org.is_active = False
    assert not studio_choices(request, org).exists()


def test_borrowed_actor_cache_cannot_elevate_target(org_admin, member, org, studio):
    request = request_for(org_admin, org=org, studio=studio)
    get_effective(request, org)
    assert shell(request)['shell_is_org_admin']
    # Borrowing replaces request.user; cache is scoped to the target identity.
    request.user = member
    assert not shell(request)['shell_is_org_admin']
    assert shell(request)['shell_studio'] is None


def test_cold_shell_never_populates_broad_role_cache(member, org, studio, grant_studio):
    grant_studio(member, studio, roles.VIEWER)
    request = request_for(member, org=org, studio=studio)
    with patch('apps.core.permissions.get_effective', side_effect=AssertionError('uncached resolver')):
        ctx = shell(request)
    assert ctx['shell_studio_role'] == roles.VIEWER
    assert not getattr(request, '_bi_effective_roles', {})


def test_every_choice_is_inline_up_to_the_limit(org_admin, org, studio):
    Studio.objects.bulk_create([Studio(org=org, slug=f'inline-{i:03}', name=f'Inline {i:03}') for i in range(250)])
    ctx = shell(request_for(org_admin, org=org, studio=studio))
    assert len(ctx['shell_studios']) == 251
    assert not ctx['shell_studios_more']


def test_huge_lists_get_a_short_preview_and_search_without_counting(org_admin, org, studio):
    Studio.objects.bulk_create([Studio(org=org, slug=f'bounded-{i:03}', name=f'Bounded {i:03}') for i in range(520)])
    request = request_for(org_admin, org=org, studio=studio)
    with CaptureQueriesContext(connection) as captured:
        ctx = shell(request)
    assert len(ctx['shell_studios']) <= 26
    assert studio in ctx['shell_studios']
    assert ctx['shell_studios_more']
    assert not any('COUNT(' in item['sql'].upper() for item in captured)
    assert any('LIMIT 501' in item['sql'].upper() for item in captured)
    assert not any(' FROM "reports_report"' in item['sql'] and 'EXISTS(' not in item['sql'].upper() for item in captured)


def test_offpage_remembered_org_and_studio_survive_and_revoke(org_admin, org):
    organizations = [Organization(slug=f'org-{i:03}', name=f'Org {i:03}') for i in range(510)]
    Organization.objects.bulk_create(organizations)
    OrgMembership.objects.bulk_create([OrgMembership(org=row, user=org_admin, role=roles.ORG_ADMIN) for row in organizations])
    last = organizations[-1]
    studios = [Studio(org=last, slug=f'studio-{i:03}', name=f'Studio {i:03}') for i in range(510)]
    Studio.objects.bulk_create(studios)
    request = request_for(org_admin)
    request.session = {'active_org': last.slug, 'active_studio_by_org': {last.slug: studios[-1].pk}}
    ctx = shell(request)
    assert ctx['shell_org'] == last
    assert ctx['shell_nav_studio'] == studios[-1]
    assert ctx['shell_studio'] is None
    assert len(ctx['shell_orgs']) == len(ctx['shell_studios']) == 26
    OrgMembership.objects.filter(org=last, user=org_admin).delete()
    following = request_for(org_admin)
    following.session = request.session
    assert shell(following)['shell_org'] != last
    assert last.slug not in following.session['active_studio_by_org']


@pytest.mark.parametrize('remembered', ['bad', [], {'demo': []}])
def test_malformed_remembered_map_does_not_break_shell(member, org, remembered):
    request = request_for(member, org=org)
    request.session['active_studio_by_org'] = remembered
    assert shell(request)['shell_nav_studio'] is None


@pytest.mark.parametrize('params', [
    {'kind': 'bad'}, {'kind': 'org', 'intent': 'assistant'}, {'kind': 'studio'},
    {'kind': 'org', 'q': 'x' * 201}, {'kind': 'org', 'page': '0'},
    {'kind': 'org', 'q': '\x00'}, {'kind': 'org', 'q': 'name\x00suffix'},
    {'kind': 'org', 'page': '2147483648'}, {'kind': 'org', 'page': '١'},
    {'kind': 'org', 'page': '-1'}, {'kind': 'org', 'page': '1.0'},
    {'kind': 'org', 'selected': 'x' * 65}, {'kind': 'org', 'selected': '../evil'},
    {'kind': 'org', 'next': 'https://example.test'}, {'kind': ['org', 'studio']},
])
def test_invalid_choice_inputs_return_400(org_admin, params):
    assert api_choices(request_for(org_admin, params)).status_code == 400


def test_choice_search_and_selected_revalidation(org_admin, org, studio):
    Studio.objects.bulk_create([Studio(org=org, slug=f'page-{i:03}', name=f'Page {i:03}') for i in range(30)])
    params = {'kind': 'studio', 'org': org.slug, 'page': '2', 'selected': studio.slug}
    payload = json.loads(api_choices(request_for(org_admin, params)).content)
    assert payload['pagination']['total'] == 31
    assert payload['pagination']['page'] == 2
    assert payload['selected']['value'] == studio.slug
    searched = json.loads(api_choices(request_for(org_admin, {**params, 'q': 'Page 005', 'page': '2147483647'})).content)
    assert [row['value'] for row in searched['results']] == ['page-005']
    assert searched['selected']['value'] == studio.slug
    assert searched['pagination']['page'] == 1
    unavailable = json.loads(api_choices(request_for(org_admin, {**params, 'selected': 'missing'})).content)
    assert unavailable['selected'] is None


def test_authentication_and_methods():
    request = request_for(AnonymousUser(), {'kind': 'org'})
    assert api_choices(request).status_code == 401
    request.method = 'POST'
    assert api_choices(request).status_code == 405


@pytest.mark.parametrize('source', ['direct', 'grant', 'default'])
@pytest.mark.parametrize('invalid', ['', 'invalid'])
def test_invalid_role_rows_match_legacy_without_promotion(source, invalid, member, org, studio, make_group, attach_group, grant_studio):
    from apps.orgs.models import PermissionGroup, PermissionGroupGrant
    if source == 'direct':
        grant_studio(member, studio, invalid)
    elif source == 'grant':
        group = make_group('invalid grant', grants=[(studio, roles.VIEWER)])
        attach_group(member, group)
        PermissionGroupGrant.objects.filter(group=group).update(role=invalid)
    else:
        group = make_group('invalid default')
        attach_group(member, group)
        PermissionGroup.objects.filter(pk=group.pk).update(default_studio_role=invalid)
    legacy_request = request_for(member)
    expected = get_effective(legacy_request, org)
    from apps.core.permissions import visible_studios
    expected_ids = set(visible_studios(member, org, request=legacy_request).values_list('pk', flat=True))
    cold_request = request_for(member)
    rows = list(studio_choices(cold_request, org))
    assert {row.pk for row in rows} == expected_ids
    for row in rows:
        row.org = org
        assert studio_facts(cold_request, row) == (expected.role_for(row), expected.has_full_studio_visibility(row))
        assert row.shell_role is None
        assert not row.shell_full


def test_all_endpoint_responses_are_private(org_admin, member, org, studio):
    for response in (
        api_choices(request_for(AnonymousUser(), {'kind': 'org'})),
        api_choices(request_for(org_admin, {'kind': 'bad'})),
        api_choices(request_for(member, {'kind': 'studio', 'org': org.slug})),
    ):
        assert response['Cache-Control'] == 'private, no-store'
