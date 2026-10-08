"""Creation defaults and private audiences share the report permission boundary."""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection
from django.utils import timezone

from apps.core import cdn, roles
from apps.core.permissions import effective_roles
from apps.core.report_access import (
    bulk_can_view_report,
    can_view_report,
    selected_report_access_configuration_error,
    visible_reports,
)
from apps.reports.models import Report, ReportFavorite, ReportPermissionGrant
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def scoped_access(settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = "proxy"


@pytest.fixture
def private(report_row):
    report_row.audience = Report.AUDIENCE_PRIVATE
    report_row.save(update_fields=["audience"])
    return report_row


def assert_access(user, report, expected):
    assert can_view_report(user, report) is expected
    assert bulk_can_view_report([user], report) == {user.pk: expected}
    assert visible_reports(user, Report.objects.filter(pk=report.pk)).exists() is expected
    scope = effective_roles(user, report.studio.org).report_scope_for(report.studio)
    assert (scope is None or report.pk in scope) is expected


@pytest.mark.parametrize("source", ["direct", "default", "all"])
def test_private_denies_implicit_viewers(
    source, private, member, make_group, attach_group, grant_studio,
):
    if source == "direct":
        grant_studio(member, private.studio, roles.VIEWER)
    else:
        group = make_group(
            source, default_studio_role=roles.VIEWER if source == "default" else "",
            grants=[(private.studio, roles.VIEWER)] if source == "all" else [],
        )
        attach_group(member, group)
    assert_access(member, private, False)
    public = Report.objects.create(studio=private.studio, slug="studio-item")
    assert_access(member, public, True)
    assert not effective_roles(member, private.studio.org).has_full_studio_visibility(private.studio)


@pytest.mark.parametrize("role", [roles.DEVELOPER, roles.ADMIN])
def test_private_management_bypass(private, member, grant_studio, role):
    grant_studio(member, private.studio, role)
    assert_access(member, private, True)


def test_private_org_admin_and_superuser(private, org_admin, superuser):
    assert_access(org_admin, private, True)
    assert_access(superuser, private, True)


@pytest.mark.parametrize("scope", ["selected", "all"])
def test_private_explicit_grant_is_additive_and_revocable(
    private, member, make_group, attach_group, grant_studio, scope,
):
    grant_studio(member, private.studio, roles.VIEWER)
    group = make_group("Explicit", grants=[(private.studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = scope
    grant.save()
    attach_group(member, group)
    assignment = ReportPermissionGrant.objects.create(grant=grant, report=private)
    assert_access(member, private, True)
    assignment.delete()
    assert_access(member, private, False)


def test_private_anonymous_denied(private):
    assert_access(AnonymousUser(), private, False)


def test_private_corrupt_cross_org_assignment_never_grants(
    private, member, other_org, other_studio, make_group, attach_group, grant_studio,
):
    from apps.orgs.models import OrgMembership

    OrgMembership.objects.create(user=member, org=other_org, role=roles.ORG_MEMBER)
    grant_studio(member, private.studio, roles.VIEWER)
    group = make_group("Foreign", org_=other_org, grants=[(other_studio, roles.VIEWER)])
    attach_group(member, group)
    with pytest.raises(ValidationError):
        ReportPermissionGrant.objects.create(grant=group.grants.get(), report=private)
    ReportPermissionGrant.objects.bulk_create([
        ReportPermissionGrant(grant=group.grants.get(), report=private),
    ])
    assert_access(member, private, False)


@pytest.mark.parametrize("kind", [Report.KIND_REPORT, Report.KIND_ANALYSIS])
@pytest.mark.parametrize("default", [Report.AUDIENCE_STUDIO, Report.AUDIENCE_PRIVATE])
def test_new_scan_sets_kind_default_before_build(studio_tree, monkeypatch, kind, default):
    field = "default_analysis_audience" if kind == Report.KIND_ANALYSIS else "default_report_audience"
    setattr(studio_tree, field, default)
    studio_tree.save()
    config = {"kind": kind}
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"slug": "new", "config": config},
    ])
    sync_studio_registry(studio_tree)
    report = studio_tree.reports.get(slug="new")
    assert report.audience == default
    assert report.last_built_at is None
    config["initial_audience"] = "private" if default == "studio" else "studio"
    setattr(studio_tree, field, config["initial_audience"])
    studio_tree.save()
    sync_studio_registry(studio_tree)
    report.refresh_from_db()
    assert report.audience == default


def test_initial_override_and_explicit_decision_survive_scan(studio_tree, monkeypatch):
    config = {"initial_audience": "private"}
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"slug": "new", "config": config},
    ])
    sync_studio_registry(studio_tree)
    report = studio_tree.reports.get(slug="new")
    assert report.audience == "private"
    report.audience = "studio"
    report.save()
    sync_studio_registry(studio_tree)
    report.refresh_from_db()
    assert report.audience == "studio"


@pytest.mark.parametrize("unsafe", [None, True, "public", "PRIVATE", [], {}])
def test_scan_rejects_invalid_initial_audience(studio_tree, monkeypatch, unsafe):
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"slug": "unsafe", "config": {"initial_audience": unsafe}},
    ])
    with pytest.raises(ValidationError):
        sync_studio_registry(studio_tree)
    assert not studio_tree.reports.exists()


@pytest.mark.parametrize("blocked", ["readiness", "edge-external"])
def test_private_blocks_content_when_configuration_incompatible(
    private, org_admin, login, settings, blocked,
):
    if blocked == "readiness":
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    else:
        settings.TRELLUM_REPORT_ACCESS_MODEL = "edge-external"
    assert selected_report_access_configuration_error()
    assert not can_view_report(org_admin, private)
    assert not bulk_can_view_report([org_admin], private)[org_admin.pk]
    assert visible_reports(org_admin, Report.objects.all()).exists()  # Metadata remains manageable.
    with pytest.raises(RuntimeError, match="selected report access is unsafe"):
        cdn.probe_exposure()
    prefix = f"/s/{private.studio.org.slug}/{private.studio.slug}"
    client = login(org_admin)
    for asset in ("", "index.html", "data.json"):
        assert client.get(f"{prefix}/r/{private.slug}/{asset}").status_code == 503
    assert client.post(
        f"{prefix}/api/reports/{private.slug}/live-query", data={},
        content_type="application/json",
    ).status_code == 503
    assert client.get(f"{prefix}/settings/repo").status_code == 200


def test_private_hidden_from_assets_registry_favorites_and_metadata(
    private, member, grant_studio, login,
):
    grant_studio(member, private.studio, roles.VIEWER)
    ReportFavorite.objects.create(user=member, report=private)
    prefix = f"/s/{private.studio.org.slug}/{private.studio.slug}"
    client = login(member)
    for asset in ("", "index.html", "data.json"):
        assert client.get(f"{prefix}/r/{private.slug}/{asset}").status_code == 404
    assert client.get(f"{prefix}/api/registry").json()["reports"] == []
    assert client.get(f"{prefix}/api/report-shell?report={private.slug}").status_code == 404
    assert client.get(f"{prefix}/api/system/status").json()["reports_total"] == 0
    assert client.put(f"/api/favorites/{private.pk}").status_code == 404


def test_legacy_insert_omitting_new_columns_uses_database_defaults(studio):
    from apps.studios.models import Studio

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO studios_studio (org_id, slug, name, description, pool, theme, repo_theme, created_at) "
            "VALUES (%s, 'legacy', 'Legacy', '', 'standard', '', '', %s) RETURNING id",
            [studio.org_id, timezone.now()],
        )
        studio_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO reports_report (studio_id, slug, name, description, category, tags, "
            "schedule_cron, schedule_timezone, disabled, priority, config, present_in_scan, "
            "first_seen_at, last_scanned_at) "
            "VALUES (%s, 'legacy', '', '', 'Uncategorized', '[]', '', 'UTC', false, 99, '{}', true, %s, %s)",
            [studio_id, timezone.now(), timezone.now()],
        )
    created = Studio.objects.get(pk=studio_id)
    assert created.default_report_audience == created.default_analysis_audience == "studio"
    assert created.reports.get().audience == "studio"


@pytest.mark.django_db(transaction=True)
def test_migration_preserves_legacy_rows_as_studio_audience():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    prior = [("reports", "0022_report_kind"), ("studios", "0011_studiorepo_publish_requested_to")]
    try:
        executor.migrate(prior)
        old = executor.loader.project_state(prior).apps
        org = old.get_model("orgs", "Organization").objects.create(slug="legacy", name="Legacy")
        studio = old.get_model("studios", "Studio").objects.create(org_id=org.pk, slug="legacy", name="Legacy")
        report = old.get_model("reports", "Report").objects.create(studio_id=studio.pk, slug="legacy")
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        new = executor.loader.project_state(latest).apps
        assert new.get_model("reports", "Report").objects.get(pk=report.pk).audience == "studio"
        migrated = new.get_model("studios", "Studio").objects.get(pk=studio.pk)
        assert migrated.default_report_audience == migrated.default_analysis_audience == "studio"
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.parametrize("blocked", [False, True])
def test_sample_delivery_checks_private_access_before_rendering(
    private, org_admin, member, grant_studio, monkeypatch, settings, blocked,
):
    from apps.reports import notify

    grant_studio(member, private.studio, roles.VIEWER)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = not blocked
    delivered = []
    monkeypatch.setattr(notify, "_deliver", lambda report, users, **kwargs: delivered.extend(users))
    notify.send_sample_or_raise(private, member)
    notify.send_sample_or_raise(private, org_admin)
    assert delivered == ([] if blocked else [org_admin])


def test_private_transition_alerts_respect_report_access_gate(
    private, member, grant_studio, settings,
):
    from apps.reports.notify import alert_recipients

    grant_studio(member, private.studio, roles.DEVELOPER)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    assert member in alert_recipients(private, "failure")
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = False
    assert member not in alert_recipients(private, "failure")


def test_bulk_permission_query_count_does_not_grow_per_user(
    private, org, make_user, make_group, attach_group, django_assert_num_queries,
):
    group = make_group("All viewers", grants=[(private.studio, roles.VIEWER)])
    users = [make_user(f"viewer{index}@demo.example", org=org) for index in range(20)]
    for user in users:
        attach_group(user, group)
    ReportPermissionGrant.objects.create(grant=group.grants.get(), report=private)
    assert private.studio.org == org  # Resolve the relation outside the measured bulk walk.
    with django_assert_num_queries(7):
        result = bulk_can_view_report(users, private)
    assert all(result.values())


def test_scan_uses_updated_defaults_from_database_with_stale_studio(studio_tree, monkeypatch):
    from apps.studios.models import Studio

    Studio.objects.filter(pk=studio_tree.pk).update(default_report_audience="private")
    assert studio_tree.default_report_audience == "studio"
    monkeypatch.setattr("trellum.runner.scan_report_configs", lambda *a, **k: [
        {"slug": "new", "config": {}},
    ])
    sync_studio_registry(studio_tree)
    assert studio_tree.reports.get().audience == "private"
