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
from apps.core.request_access import actor_studio_access, request_org_access
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
    access = request_org_access(request, org)
    access.studio_access(studio)

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


def test_request_report_check_reuses_roles_and_enforces_bearer_scope(org_admin, org, other_org, report_row):
    from apps.core.report_access import can_view_report

    request = request_for(org_admin)
    report_row.studio.org = org
    assert can_view_report(org_admin, report_row, request=request)
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not can_view_report(org_admin, report_row, request=request)


def test_request_facts_match_complete_roles_across_sources(
    member, org_admin, superuser, org, studio, studio2, make_user,
    make_group, attach_group, grant_studio, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    Report.objects.create(studio=studio, slug="private-facts", audience=Report.AUDIENCE_PRIVATE)
    grant_studio(member, studio, roles.VIEWER)
    selected = make_user("selected-facts@example.test", org=org)
    group = make_group("selected facts", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(selected, group)
    defaulted = make_user("default-facts@example.test", org=org)
    attach_group(defaulted, make_group("default facts", default_studio_role=roles.VIEWER))
    grouped_admin = make_user("group-admin-facts@example.test", org=org)
    attach_group(grouped_admin, make_group("admin facts", org_role=roles.ORG_ADMIN))
    outsider = make_user("outsider-facts@example.test")
    for user in (member, selected, defaulted, grouped_admin, org_admin, superuser, outsider):
        access = request_org_access(request_for(user), org)
        complete = effective_roles(user, org)
        assert (access.is_member, access.is_org_admin) == (complete.is_member, complete.is_org_admin)
        for target in (studio, studio2):
            assert (access.role_for(target), access.has_full_studio_visibility(target)) == (
                complete.role_for(target), complete.has_full_studio_visibility(target)
            )
            nonrequest = actor_studio_access(user, target)
            assert (nonrequest.role, nonrequest.full, nonrequest.is_org_admin) == (
                complete.role_for(target), complete.has_full_studio_visibility(target),
                complete.is_org_admin,
            )


def test_request_facts_keep_complete_cache_separate(
    member, org, studio, grant_studio,
):
    grant_studio(member, studio, roles.VIEWER)
    request = request_for(member)
    access = request_org_access(request, org)
    assert access.role_for(studio) == roles.VIEWER
    assert not hasattr(request, "_bi_effective_roles")
    assert get_effective(request, org).role_for(studio) == roles.VIEWER


def test_request_facts_cache_actor_key_and_second_studio(
    member, org_admin, org, other_org, studio, studio2, grant_studio,
):
    grant_studio(member, studio, roles.VIEWER)
    request = request_for(member)
    first = request_org_access(request, org)
    assert first is request_org_access(request, org)
    assert first.role_for(studio) == roles.VIEWER
    assert first.role_for(studio2) is None
    request.user = org_admin
    assert not first.is_member
    assert not first.is_org_admin
    assert first.role_for(studio) is None
    assert request_org_access(request, org).role_for(studio2) == roles.ADMIN
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not request_org_access(request, org).is_member
    request.api_key.org_id = org.pk
    assert request_org_access(request, org).is_org_admin
    org.is_active = False
    assert not request_org_access(request, org).is_member


def test_unknown_group_default_does_not_make_selected_viewer_full(
    member, org, studio, make_group, attach_group, settings,
):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    selected = make_group("selected with malformed default", grants=[(studio, roles.VIEWER)])
    grant = selected.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(member, selected)
    malformed = make_group("malformed default")
    type(malformed).objects.filter(pk=malformed.pk).update(default_studio_role="unknown")
    attach_group(member, malformed)
    complete = effective_roles(member, org)
    access = request_org_access(request_for(member), org)
    assert complete.role_for(studio) == access.role_for(studio) == roles.VIEWER
    assert not complete.has_full_studio_visibility(studio)
    assert not access.has_full_studio_visibility(studio)


def test_request_facts_next_request_reflects_revocation(
    member, org, studio, grant_studio,
):
    grant_studio(member, studio, roles.VIEWER)
    assert request_org_access(request_for(member), org).role_for(studio) == roles.VIEWER
    from apps.studios.models import StudioMembership
    StudioMembership.objects.filter(user=member, studio=studio).delete()
    assert request_org_access(request_for(member), org).role_for(studio) is None


def test_nonrequest_facts_use_fresh_org_and_grants(member, org, studio, grant_studio):
    from apps.orgs.models import Organization
    from apps.studios.models import StudioMembership

    grant_studio(member, studio, roles.VIEWER)
    studio.org = org  # Keep a stale related organization on the studio.
    assert actor_studio_access(member, studio).role == roles.VIEWER
    StudioMembership.objects.filter(user=member, studio=studio).delete()
    assert actor_studio_access(member, studio).role is None
    grant_studio(member, studio, roles.VIEWER)
    Organization.objects.filter(pk=org.pk).update(is_active=False)
    fresh_org = Organization.objects.get(pk=org.pk)
    assert actor_studio_access(member, studio, org=fresh_org).role is None


def test_guard_facts_do_not_fetch_unrelated_report_rows(
    member, org, studio, studio2, grant_studio,
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    grant_studio(member, studio, roles.VIEWER)
    Report.objects.bulk_create([
        Report(studio=studio2, slug=f"unrelated-{i}", config={"large": "x" * 512})
        for i in range(100)
    ])
    request = request_for(member)
    with CaptureQueriesContext(connection) as captured:
        assert request_org_access(request, org).role_for(studio) == roles.VIEWER
    assert not hasattr(request, "_bi_effective_roles")
    assert len(captured) <= 3
    assert all('"apps_reports_report"."config"' not in query["sql"] for query in captured)


def test_request_visible_reports_checks_actor_and_key_scope(
    org_admin, member, org, other_org, report_row,
):
    from apps.core.report_access import visible_reports

    request = request_for(org_admin)
    with pytest.raises(ValueError):
        visible_reports(member, Report.objects.all(), request=request)
    assert visible_reports(org_admin, Report.objects.filter(pk=report_row.pk), request=request).exists()
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not visible_reports(org_admin, Report.objects.filter(pk=report_row.pk), request=request).exists()


def test_selected_only_remains_non_full_when_every_report_is_granted(
    member, org, studio, report_row, make_group, attach_group, settings,
):
    from apps.core.report_access import can_view_report, visible_reports

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    group = make_group("all individually", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(member, group)
    ReportPermissionGrant.objects.create(grant=grant, report=report_row)
    request = request_for(member)
    access = request_org_access(request, org)
    assert access.role_for(studio) == roles.VIEWER
    assert not access.has_full_studio_visibility(studio)
    assert can_view_report(member, report_row, request=request)
    assert visible_reports(member, Report.objects.filter(pk=report_row.pk), request=request).exists()
    assert not hasattr(request, "_bi_effective_roles")
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert not can_view_report(member, report_row, request=request)


@pytest.mark.parametrize("ready", [True, False])
def test_raw_scope_matches_complete_roles_independent_of_access_readiness(
    org_admin, member, org, studio, make_user, make_group, attach_group,
    grant_studio, settings, ready,
):
    from apps.core.report_access import raw_scope_reports, visible_reports

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    public = Report.objects.create(studio=studio, slug="raw-public")
    private = Report.objects.create(studio=studio, slug="raw-private", audience=Report.AUDIENCE_PRIVATE)
    selected = make_group("raw selected", grants=[(studio, roles.VIEWER)])
    grant = selected.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(member, selected)
    ReportPermissionGrant.objects.create(grant=grant, report=private)
    full_viewer = make_user("raw-full@example.test", org=org)
    grant_studio(full_viewer, studio, roles.VIEWER)
    stranger = make_user("raw-stranger@example.test")
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = ready
    for user in (org_admin, member, full_viewer, stranger):
        request = request_for(user)
        scope = effective_roles(user, org).report_scope_for(studio)
        expected = {public.pk, private.pk} if scope is None else set(scope)
        rows = raw_scope_reports(user, Report.objects.filter(studio=studio), request=request)
        assert set(rows.values_list("pk", flat=True)) == expected
        assert not hasattr(request, "_bi_effective_roles")
    request = request_for(member)
    assert raw_scope_reports(member, Report.objects.filter(pk=private.pk), request=request).exists()
    assert visible_reports(member, Report.objects.filter(pk=private.pk), request=request).exists() is ready


def test_raw_scope_restricts_superuser_bearer_org(superuser, org, other_org, studio):
    from apps.core.report_access import raw_scope_reports

    report = Report.objects.create(studio=studio, slug="key-raw")
    request = request_for(superuser)
    assert raw_scope_reports(superuser, Report.objects.filter(pk=report.pk), request=request).exists()
    request.api_key = SimpleNamespace(org_id=other_org.pk)
    assert not raw_scope_reports(superuser, Report.objects.filter(pk=report.pk), request=request).exists()


@pytest.mark.parametrize("malformed_source", ["direct", "grant"])
def test_raw_scope_matches_full_path_from_malformed_row_and_selected_viewer(
    member, org, studio, make_group, attach_group, grant_studio, settings, malformed_source,
):
    from apps.core.report_access import can_view_report, raw_scope_reports, visible_reports
    from apps.studios.models import StudioMembership

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    public = Report.objects.create(studio=studio, slug=f"raw-{malformed_source}-public")
    private = Report.objects.create(
        studio=studio, slug=f"raw-{malformed_source}-private", audience=Report.AUDIENCE_PRIVATE
    )
    selected = make_group(f"raw {malformed_source} selected", grants=[(studio, roles.VIEWER)])
    selected_grant = selected.grants.get()
    selected_grant.viewer_scope = selected_grant.REPORT_SCOPE_SELECTED
    selected_grant.save(update_fields=["viewer_scope"])
    attach_group(member, selected)
    ReportPermissionGrant.objects.create(grant=selected_grant, report=private)
    if malformed_source == "direct":
        grant_studio(member, studio, roles.VIEWER)
        StudioMembership.objects.filter(user=member, studio=studio).update(role="unknown")
    else:
        malformed = make_group("raw malformed grant", grants=[(studio, roles.VIEWER)])
        attach_group(member, malformed)
        type(selected_grant).objects.filter(group=malformed, studio=studio).update(role="unknown")

    request = request_for(member)
    legacy = get_effective(request, org).report_scope_for(studio)
    assert legacy == frozenset({public.pk, private.pk})
    rows = raw_scope_reports(member, Report.objects.filter(studio=studio), request=request)
    assert set(rows.values_list("pk", flat=True)) == set(legacy)
    assert can_view_report(member, public, request=request)
    assert can_view_report(member, public)
    assert not visible_reports(member, Report.objects.filter(pk=public.pk), request=request).exists()
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert set(raw_scope_reports(
        member, Report.objects.filter(studio=studio), request=request
    ).values_list("pk", flat=True)) == set(legacy)
    assert not can_view_report(member, public, request=request)
    assert not can_view_report(member, public)


def test_malformed_full_path_without_private_reports_remains_full_when_scoping_blocked(
    member, org, studio, make_group, attach_group, grant_studio, settings,
):
    from apps.core.report_access import can_view_report
    from apps.studios.models import StudioMembership

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    public = Report.objects.create(studio=studio, slug="malformed-full-public")
    selected = make_group("malformed full selected", grants=[(studio, roles.VIEWER)])
    grant = selected.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(member, selected)
    grant_studio(member, studio, roles.VIEWER)
    StudioMembership.objects.filter(user=member, studio=studio).update(role="unknown")
    request = request_for(member)
    assert get_effective(request, org).report_scope_for(studio) is None
    assert request_org_access(request, org).has_full_studio_visibility(studio)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert can_view_report(member, public, request=request)
    assert can_view_report(member, public)


def test_selected_report_identity_remains_visible_before_content_503(
    member, org, studio, make_group, attach_group, settings,
):
    from apps.reports.models import ReportFavorite
    from apps.reports.views import _get_report, _selected_content_block, _visible_favorite_rows

    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    private = Report.objects.create(studio=studio, slug="raw-selected-private", audience=Report.AUDIENCE_PRIVATE)
    group = make_group("identity selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = grant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    attach_group(member, group)
    ReportPermissionGrant.objects.create(grant=grant, report=private)
    request = request_for(member, org, studio)
    request.org_roles = request_org_access(request, org)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert _get_report(request, private.slug).pk == private.pk
    assert _selected_content_block(request, private).status_code == 503
    ReportFavorite.objects.create(user=member, report=private)
    assert [row.report_id for row in _visible_favorite_rows(request)] == [private.pk]


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
