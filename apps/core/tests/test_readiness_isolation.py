"""Scoped readiness isolates restricted content from ordinary studio grants."""
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.core import roles
from apps.core.permissions import effective_roles
from apps.core.report_access import (
    ReportAccessBatch, bulk_can_view_report, can_view_report, raw_scope_reports,
    report_content_block_reason, visible_reports,
)
from apps.core.request_access import actor_studio_access, request_org_access
from apps.core.shell_scope import studio_choices, studio_facts
from apps.orgs.models import OrgMembership
from apps.reports.models import OrgSharePolicy, Report, ReportPermissionGrant
from apps.reports.recipient_choices import recipient_full, recipient_page, recipient_snapshot

pytestmark = pytest.mark.django_db


def request_for(user, *, key_org=None):
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    if key_org is not None:
        request.api_key = SimpleNamespace(org_id=key_org.pk)
    return request


@pytest.fixture
def mixed_reports(studio, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"
    public = Report.objects.create(studio=studio, slug="studio-report", name="Studio report")
    private = Report.objects.create(
        studio=studio, slug="private-report", name="Private report", audience=Report.AUDIENCE_PRIVATE,
    )
    return public, private


@pytest.fixture
def viewer_grant(member, studio, make_group, attach_group, grant_studio):
    def grant(source, reports=()):
        if source == "direct":
            grant_studio(member, studio, roles.VIEWER)
            return
        group = make_group(
            f"{source} viewers", default_studio_role=roles.VIEWER if source == "default" else "",
            grants=[] if source == "default" else [(studio, roles.VIEWER)],
        )
        attach_group(member, group)
        if source == "default":
            return
        assignment = group.grants.get()
        assignment.viewer_scope = "selected" if source == "selected" else "all"
        assignment.save()
        for report in reports:
            ReportPermissionGrant.objects.create(grant=assignment, report=report)
    return grant


@pytest.mark.parametrize("source", ["direct", "default", "all", "selected"])
@pytest.mark.parametrize("mode", ["ready", "disabled", "edge-external"])
@pytest.mark.parametrize("removed", [False, True])
def test_studio_grant_is_independent_of_private_rows(
    member, org, studio, mixed_reports, viewer_grant, settings, source, mode, removed,
):
    public, private = mixed_reports
    viewer_grant(source, reports=[public, private] if source == "selected" else [])
    if removed:
        Report.objects.filter(pk=private.pk).update(present_in_scan=False)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = mode != "disabled"
    settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external" if mode == "edge-external" else "proxy"
    ready, wide = mode == "ready", source != "selected"
    request = request_for(member)
    access = request_org_access(request, org)
    complete = effective_roles(member, org)
    facts = actor_studio_access(member, studio)
    assert access.has_studio_audience_access(studio) is wide
    assert complete.has_studio_audience_access(studio) is wide
    assert facts.studio_audience is wide
    # Removed Private metadata is retained; it cannot restore aggregate access.
    assert not access.has_full_studio_visibility(studio)
    assert not complete.has_full_studio_visibility(studio)
    assert not facts.full
    choice = studio_choices(request, org).get(pk=studio.pk)
    assert choice.shell_studio_audience is wide
    assert studio_facts(request, choice) == (roles.VIEWER, False)
    batch = ReportAccessBatch([member], org)
    for report, expected in ((public, wide or ready), (private, source == "selected" and ready)):
        assert can_view_report(member, report) is expected
        assert can_view_report(member, report, request=request) is expected
        assert bulk_can_view_report([member], report)[member.pk] is expected
        assert batch.can_view(member, report) is expected
        assert visible_reports(member, Report.objects.filter(pk=report.pk)).exists() is expected
        assert visible_reports(member, Report.objects.filter(pk=report.pk), request=request).exists() is expected
        for audience in (recipient_snapshot(studio, report), recipient_full(studio, report), recipient_page(studio, report)):
            assert (member.pk in {row["id"] for row in audience["members"]}) is expected
        if raw_scope_reports(member, Report.objects.filter(pk=report.pk)).exists():
            assert bool(report_content_block_reason(member, report, request=request)) is (not expected)


def test_selected_assignment_added_to_studio_grant_keeps_studio_access(
    member, mixed_reports, viewer_grant, settings,
):
    public, private = mixed_reports
    viewer_grant("selected", reports=[public, private])
    viewer_grant("direct")
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert can_view_report(member, public)
    assert not can_view_report(member, private)
    assert bulk_can_view_report([member], public)[member.pk]
    assert not bulk_can_view_report([member], private)[member.pk]


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("malformed_source", ["direct", "role", "scope"])
def test_malformed_path_does_not_become_a_studio_audience_grant(
    member, org, studio, mixed_reports, viewer_grant, settings, private, malformed_source,
):
    from apps.orgs.models import PermissionGroupGrant
    from apps.studios.models import StudioMembership

    public, restricted = mixed_reports
    viewer_grant("selected", reports=[restricted])
    if malformed_source == "direct":
        viewer_grant("direct")
        StudioMembership.objects.filter(user=member, studio=studio).update(role="unknown")
    else:
        viewer_grant("all")
        PermissionGroupGrant.objects.filter(
            studio=studio, group__memberships__user=member, viewer_scope="all",
        ).update(**{"role" if malformed_source == "role" else "viewer_scope": "unknown"})
    if not private:
        restricted.delete()
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    request = request_for(member)
    assert not request_org_access(request, org).has_studio_audience_access(studio)
    assert not effective_roles(member, org).has_studio_audience_access(studio)
    assert not actor_studio_access(member, studio).studio_audience
    assert not studio_choices(request, org).get(pk=studio.pk).shell_studio_audience
    # Preserve the existing raw full-visibility behavior only when no Private
    # metadata remains; malformed rows do not supply the new grant fact.
    assert can_view_report(member, public) is (not private)
    assert bulk_can_view_report([member], public)[member.pk] is (not private)
    for audience in (recipient_snapshot(studio, public), recipient_full(studio, public)):
        assert (member.pk in {row["id"] for row in audience["members"]}) is (not private)


@pytest.mark.parametrize("actor", ["developer", "admin", "org_admin", "superuser"])
@pytest.mark.parametrize("mode", ["disabled", "edge-external"])
def test_private_content_block_includes_managers_and_superuser(
    member, org_admin, superuser, mixed_reports, grant_studio, settings, actor, mode,
):
    public, private = mixed_reports
    if actor in ("developer", "admin"):
        user = member
        grant_studio(user, private.studio, actor)
    else:
        user = org_admin if actor == "org_admin" else superuser
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = mode != "disabled"
    settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external" if mode == "edge-external" else "proxy"
    request = request_for(user)
    assert can_view_report(user, public, request=request)
    assert not can_view_report(user, private, request=request)
    assert report_content_block_reason(user, private, request=request)
    assert not bulk_can_view_report([user], private)[user.pk]
    # Management visibility remains available for repairing existing records.
    assert visible_reports(user, Report.objects.filter(pk=private.pk), request=request).exists()
    assert recipient_full(private.studio, private)["members"] == []
    assert recipient_full(private.studio, private)["groups"] == []


@pytest.mark.parametrize("actor", ["member", "superuser"])
def test_request_boundaries_cover_studio_access_and_content(
    member, superuser, org, other_org, mixed_reports, viewer_grant, settings, actor,
):
    public, _ = mixed_reports
    viewer_grant("direct")
    user = member if actor == "member" else superuser
    OrgMembership.objects.create(user=user, org=other_org, role=roles.ORG_MEMBER)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    request = request_for(user, key_org=other_org)
    assert not request_org_access(request, org).has_studio_audience_access(public.studio)
    assert not can_view_report(user, public, request=request)
    assert not visible_reports(user, Report.objects.all(), request=request).exists()
    assert not studio_choices(request, org).exists()
    request.api_key.org_id = org.pk
    assert can_view_report(user, public, request=request)
    org.is_active = False
    public.studio.org = org
    assert not request_org_access(request, org).has_studio_audience_access(public.studio)
    assert not can_view_report(user, public, request=request)
    assert not bulk_can_view_report([user], public)[user.pk]
    assert not studio_choices(request, org).exists()


def test_unauthenticated_and_foreign_grants_remain_denied(
    member, other_org, other_studio, mixed_reports, make_group, attach_group, settings,
):
    public, private = mixed_reports
    foreign = make_group("Foreign", org_=other_org, grants=[(other_studio, roles.VIEWER)])
    attach_group(member, foreign)
    # Deliberately bypass validation, as a retained inconsistent row might do.
    type(foreign.grants.get()).objects.filter(group=foreign).update(studio=public.studio)
    ReportPermissionGrant.objects.bulk_create([
        ReportPermissionGrant(grant=foreign.grants.get(), report=public),
        ReportPermissionGrant(grant=foreign.grants.get(), report=private),
    ])
    for ready in (False, True):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = ready
        for user in (member, AnonymousUser()):
            assert not actor_studio_access(user, public.studio).studio_audience
            for report in mixed_reports:
                assert not can_view_report(user, report)
                assert not bulk_can_view_report([user], report)[user.pk]
                assert not visible_reports(user, Report.objects.filter(pk=report.pk)).exists()


@pytest.mark.parametrize("source", ["direct", "default", "all", "selected"])
def test_catalog_and_navigation_survive_with_external_sharing_disabled(
    member, org, studio, mixed_reports, viewer_grant, settings, source, login,
):
    public, private = mixed_reports
    viewer_grant(source, reports=[public, private] if source == "selected" else [])
    OrgSharePolicy.objects.create(org=org, share_links_enabled=False, embed_links_enabled=False)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    client = login(member)
    prefix = f"/s/{org.slug}/{studio.slug}"
    for path in ("/", "/analyses", "/alerts", "/deliveries"):
        response = client.get(prefix + path)
        assert response.status_code == 200
        assert private.name.encode() not in response.content
    catalog = client.get(prefix + "/api/registry")
    assert catalog.status_code == 200
    assert {row["slug"] for row in catalog.json()["reports"]} == ({public.slug} if source != "selected" else set())
    jump = client.get(prefix + "/api/jump")
    assert jump.status_code == 200
    assert {row["value"] for row in jump.json()["results"]} == ({public.slug} if source != "selected" else set())
    assert client.get(prefix + f"/r/{private.slug}/").status_code in (404, 503)
    # These endpoints expose studio-wide metadata and keep their narrow guards.
    for path in (
        "/metrics", "/metrics/comparison", "/metrics/compare", "/metrics/choices",
        "/api/metrics/summary", "/metrics/1/claims", "/experiments", "/annotations",
        "/api/datasources", "/api/members", "/api/system/status", "/api/system/run-stats",
    ):
        response = client.get(prefix + path)
        assert response.status_code == 403, (path, response.status_code)
        assert private.name.encode() not in response.content


@pytest.mark.heavy
def test_studio_permission_queries_do_not_materialize_report_rows(
    member, org, studio, mixed_reports, viewer_grant, settings,
):
    viewer_grant("direct")
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    counts = []
    for size in (30, 500):
        Report.objects.bulk_create([
            Report(studio=studio, slug=f"scale-{size}-{i}", config={"large": "x" * 1024})
            for i in range(size)
        ])
        with CaptureQueriesContext(connection) as captured:
            request = request_for(member)
            access = request_org_access(request, org)
            assert access.has_studio_audience_access(studio)
            assert not access.has_full_studio_visibility(studio)
            assert len(list(visible_reports(member, Report.objects.all(), request=request).values("pk")[:25])) == 25
        counts.append(len(captured))
        assert all('"config"' not in row["sql"] for row in captured)
    assert counts[0] == counts[1] <= 3
