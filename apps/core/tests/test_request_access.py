"""Request reuse, identity boundaries, and bulk direct-admin permissions."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.core import roles
from apps.core.context_processors import shell
from apps.core.permissions import (
    bulk_effective_roles, effective_roles, get_effective, require_studio_role,
    visible_studios,
)
from apps.core.report_access import bulk_can_view_report
from apps.core.themes import explicit_studio_theme, viewer_theme_override
from apps.orgs.models import OrgMembership
from apps.reports.models import Report, ReportPermissionGrant
from apps.studios.models import StudioPreference

pytestmark = pytest.mark.django_db


def request_for(user, org=None, studio=None):
    request = RequestFactory().get('/')
    request.user, request.session = user, {}
    if org is not None:
        request.org = org
    if studio is not None:
        request.studio = studio
    return request


def test_direct_admin_needs_only_membership_query(org_admin, org, studio, django_assert_num_queries):
    Report.objects.bulk_create([Report(studio=studio, slug=f'item-{i}') for i in range(100)])
    with django_assert_num_queries(1):
        result = effective_roles(org_admin, org)
    assert result.is_org_admin
    assert result.role_for(studio) == roles.ADMIN
    assert result.report_scope_for(studio) is None


def test_mixed_batch_keeps_selected_private_and_group_admin(
    org_admin, member, org, studio, make_user, make_group, attach_group, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    private = Report.objects.create(studio=studio, slug='private', audience=Report.AUDIENCE_PRIVATE)
    public = Report.objects.create(studio=studio, slug='public')
    group = make_group('Selected', grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save()
    attach_group(member, group)
    ReportPermissionGrant.objects.create(grant=grant, report=private)
    group_admin = make_user('group-admin@example.test')
    OrgMembership.objects.create(user=group_admin, org=org, role=roles.ORG_MEMBER)
    attach_group(group_admin, make_group('Admin', org_role=roles.ORG_ADMIN))
    outsider = make_user('outside@example.test')
    users = [org_admin, member, group_admin, outsider, AnonymousUser()]
    resolved = bulk_effective_roles(users, org)
    for user in users:
        assert resolved[user.pk].role_for(studio) == effective_roles(user, org).role_for(studio)
        assert resolved[user.pk].report_scope_for(studio) == effective_roles(user, org).report_scope_for(studio)
    assert bulk_can_view_report(users, private) == {
        org_admin.pk: True, member.pk: True, group_admin.pk: True, outsider.pk: False, None: False,
    }
    assert bulk_can_view_report(users, public)[member.pk] is False
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert not any(bulk_can_view_report(users, private).values())


def test_inactive_batch_denies_superuser(org, studio, org_admin, superuser):
    org.is_active = False
    studio.org = org
    assert all(not er.is_member for er in bulk_effective_roles([org_admin, superuser], org).values())


def test_request_cache_tracks_actor_key_scope_and_org_activity(org_admin, member, org, other_org):
    request = request_for(org_admin)
    admin = get_effective(request, org)
    assert get_effective(request, org) is admin
    request.user = member
    assert not get_effective(request, org).is_org_admin
    request.user = org_admin
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not get_effective(request, org).is_member
    request.api_key.org_id = org.pk
    assert get_effective(request, org).is_org_admin
    org.is_active = False
    assert not get_effective(request, org).is_member


def test_request_studio_visibility_rejects_other_actor(org_admin, member, org):
    with pytest.raises(ValueError):
        visible_studios(member, org, request=request_for(org_admin))


def test_shell_reuses_guard_roles_and_theme_preference(
    org_admin, org, studio, django_assert_num_queries,
):
    studio.org = org
    StudioPreference.objects.create(user=org_admin, studio=studio, theme='dracula')
    request = request_for(org_admin, org, studio)
    get_effective(request, org)
    with patch('apps.core.permissions.get_effective', side_effect=AssertionError('shell called get_effective')):
        ctx = shell(request)
    assert ctx['shell_is_org_admin']
    assert ctx['studio_theme'] == ctx['viewer_studio_theme'] == 'dracula'
    with django_assert_num_queries(0):
        assert explicit_studio_theme(org_admin, studio, request=request) == 'dracula'
        assert viewer_theme_override(org_admin, studio, request=request) == 'dracula'


def test_preference_cache_keeps_raw_locked_value_and_actor_boundary(member, org_admin, org, studio, django_assert_num_queries):
    StudioPreference.objects.create(user=member, studio=studio, theme='dracula')
    org.lock_studio_theme = True
    studio.org = org
    request = request_for(member)
    assert explicit_studio_theme(member, studio, request=request) == ''
    with django_assert_num_queries(1):
        assert viewer_theme_override(member, studio, request=request) == 'dracula'
    request.user = org_admin
    with django_assert_num_queries(1):
        assert viewer_theme_override(org_admin, studio, request=request) == ''


def test_shell_key_scope_and_actor_switch(org_admin, member, org, other_org, studio):
    OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_MEMBER)
    request = request_for(org_admin, other_org, studio)
    request.api_key = SimpleNamespace(org_id=org.pk)
    ctx = shell(request)
    assert [item.pk for item in ctx['shell_orgs']] == [org.pk]
    assert ctx['shell_org'].pk == org.pk
    assert ctx['shell_is_org_admin']
    request.user = member
    switched = shell(request)
    assert not switched['shell_is_org_admin']
    assert switched['shell_studio'] is None


def test_nested_guards_reuse_known_tenant_objects(org_admin, org, studio, django_assert_num_queries):
    from django.http import HttpResponse

    request = request_for(org_admin, org, studio)
    get_effective(request, org)

    @require_studio_role(roles.VIEWER)
    @require_studio_role(roles.VIEWER)
    def view(request, **kwargs):
        assert request.studio.org is request.org
        return HttpResponse('ok')

    with django_assert_num_queries(0):
        assert view(request, org_slug=org.slug, studio_slug=studio.slug).status_code == 200


def test_next_request_reflects_membership_revocation(org_admin, org):
    assert get_effective(request_for(org_admin), org).is_org_admin
    OrgMembership.objects.filter(org=org, user=org_admin).delete()
    assert not get_effective(request_for(org_admin), org).is_member


def test_request_report_check_reuses_roles_and_enforces_bearer_scope(org_admin, org, other_org, report_row, django_assert_num_queries):
    from apps.core.report_access import can_view_report

    request = request_for(org_admin)
    report_row.studio.org = org
    get_effective(request, org)
    with django_assert_num_queries(0):
        assert can_view_report(org_admin, report_row, request=request)
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not can_view_report(org_admin, report_row, request=request)


def test_next_http_request_reflects_report_grant_revocation(
    member, org, studio, report_row, make_group, attach_group, login, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group('Selected revocation', grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save()
    attach_group(member, group)
    assignment = ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    client = login(member)
    path = f'/s/{org.slug}/{studio.slug}/api/registry'
    before = client.get(path)
    assert before.status_code == 200
    assert report_row.slug in {row['slug'] for row in before.json()['reports']}
    assignment.delete()
    after = client.get(path)
    assert after.status_code == 200
    assert report_row.slug not in {row['slug'] for row in after.json()['reports']}


def test_shell_cache_tracks_known_tenant_studio_and_active_user(
    org_admin, org, other_org, studio, studio2, other_studio,
):
    OrgMembership.objects.create(user=org_admin, org=other_org, role=roles.ORG_ADMIN)
    request = request_for(org_admin, org, studio)
    assert shell(request)['shell_studio'] == studio
    request.studio = studio2
    assert shell(request)['shell_studio'] == studio2
    request.org, request.studio = other_org, other_studio
    assert shell(request)['shell_org'] == other_org
    assert shell(request)['shell_studio'] == other_studio
    assert shell(request)['shell_can_manage_deliveries']
    org_admin.is_active = False
    assert not shell(request)['shell_can_manage_deliveries']
