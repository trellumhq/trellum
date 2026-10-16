"""Operator tables list every row, plainly, with flat queries and DB-only usage."""
import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.models import AuditLog
from apps.orgs import quotas
from apps.orgs.models import Organization, OrgMembership, OrgQuota
from apps.reports.models import Report
from apps.runner.models import Run, WorkerHeartbeat
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture
def console(client):
    operator = get_user_model().objects.create_user(
        email="bounded-operator@portal.local", is_operator_flag=True,
    )
    client.force_login(operator)
    return client


def no_controls(content):
    """The tables carry no filter, search box, pager or apply button."""
    html = content.decode()
    for fragment in ('type="search"', "<select name=\"state\"", "Next ", "Previous ", " matching", "Page 1 of"):
        assert fragment not in html, fragment


@pytest.mark.parametrize("size", [20, 200])
def test_org_list_shows_every_organization(console, size):
    Organization.objects.bulk_create([
        Organization(slug=f"bounded-{i:04}", name=f"Organization {i:04}", is_active=i % 2 == 0)
        for i in range(size)
    ])
    with CaptureQueriesContext(connection) as queries:
        response = console.get("/operator/orgs/")
    assert response.status_code == 200
    assert len(response.context["orgs"]) == size
    assert response.context["orgs_more"] is None
    assert b"Showing the first" not in response.content
    assert len(queries) <= 24
    no_controls(response.content)
    # Query strings from the old filter UI are ignored, not errors.
    assert len(console.get("/operator/orgs/", {"q": "x" * 300, "page": "-1"}).context["orgs"]) == size


@pytest.mark.heavy
def test_org_list_stops_growing_far_above_realistic_sizes(console):
    from apps.operator.bounded import LIST_CAP
    Organization.objects.bulk_create([
        Organization(slug=f"capped-{i:04}", name=f"Organization {i:04}") for i in range(LIST_CAP + 100)
    ])
    with CaptureQueriesContext(connection) as queries:
        response = console.get("/operator/orgs/")
    assert len(response.context["orgs"]) == LIST_CAP
    assert response.context["orgs_more"] == {"shown": LIST_CAP, "total": LIST_CAP + 100}
    assert f"Showing the first {LIST_CAP} of {LIST_CAP + 100} organizations." in response.content.decode()
    assert len(queries) <= 24
    assert [row.slug for row in response.context["orgs"]][:2] == ["capped-0000", "capped-0001"]


def test_org_counts_without_join_crossproduct(console, org, studio, member):
    extra = Studio.objects.bulk_create([
        Studio(org=org, slug=f"counts-{i}", name="Counted") for i in range(30)
    ])
    users = get_user_model().objects.bulk_create([
        get_user_model()(email=f"counted-{i}@portal.local") for i in range(30)
    ])
    OrgMembership.objects.bulk_create([OrgMembership(org=org, user=user) for user in users])
    report = Report.objects.create(studio=extra[-1], slug="building", name="Building")
    Run.objects.create(studio=extra[-1], report=report, slug="building", status=Run.RUNNING)
    response = console.get("/operator/orgs/")
    row = next(row for row in response.context["orgs"] if row.pk == org.pk)
    assert row.studio_count == Studio.objects.filter(org=org).count()
    assert row.member_count == OrgMembership.objects.filter(org=org).count()
    assert row.active_runs == 1
    assert "default_theme" in row.get_deferred_fields()
    assert "quota" not in getattr(row, "_prefetched_objects_cache", {})


def test_detail_lists_every_studio_and_member_with_narrow_fields(console, org):
    studios = Studio.objects.bulk_create([
        Studio(org=org, slug=f"detail-{i:03}", name=f"Studio {i:03}") for i in range(50)
    ])
    users = get_user_model().objects.bulk_create([
        get_user_model()(email=f"detail-{i:03}@portal.local", name=f"Person {i:03}") for i in range(100)
    ])
    OrgMembership.objects.bulk_create([OrgMembership(org=org, user=user) for user in users])
    Report.objects.create(studio=studios[-1], slug="present", name="Present", present_in_scan=True)
    Report.objects.create(studio=studios[-1], slug="absent", name="Absent", present_in_scan=False)
    path = f"/operator/orgs/{org.slug}/"
    with CaptureQueriesContext(connection) as queries:
        response = console.get(path)
    assert response.status_code == 200
    assert len(response.context["studios"]) == 50
    assert len(response.context["members"]) == 100
    assert response.context["studios_more"] is None and response.context["members_more"] is None
    assert response.context["usage"]["studios"] == 50
    assert response.context["usage"]["reports"] == 1
    assert len(queries) <= 28
    assert list(response.context["studios"])[-1].report_count == 1
    assert "password" in list(response.context["members"])[0].user.get_deferred_fields()
    assert "detail-099@portal.local" in response.content.decode()
    no_controls(response.content)


def test_fleet_lists_every_live_worker_and_bounds_the_other_sections(console, org, studio):
    WorkerHeartbeat.objects.bulk_create([
        WorkerHeartbeat(worker_id=f"bounded-worker-{i:03}", role="runner",
                        max_concurrent=4, running_count=1, memory_budget_mb=100, reserved_memory_mb=25)
        for i in range(100)
    ])
    WorkerHeartbeat.objects.create(worker_id="coordinator", role="coordinator")
    WorkerHeartbeat.objects.create(worker_id="stale", role="runner",
                                   last_beat_at=timezone.now() - timezone.timedelta(seconds=120))
    report = Report.objects.create(studio=studio, slug="failing", name="Failing")
    Run.objects.bulk_create([
        Run(studio=studio, report=report, slug=f"failed-{i}", status=Run.ERROR, peak_memory_mb=i + 1,
            stdout_tail="large output" * 100, stderr_tail="large error" * 100)
        for i in range(30)
    ])
    response = console.get("/operator/")
    assert len(response.context["workers"]) == 101
    assert response.context["workers_more"] is None
    assert [w.worker_id for w in response.context["workers"]][-2:] == ["bounded-worker-098", "bounded-worker-099"]
    assert "stale" not in [w.worker_id for w in response.context["workers"]]
    assert len(response.context["recent_failures"]) == 20
    assert len(response.context["heaviest"]) == 10
    assert "stdout_tail" in list(response.context["recent_failures"])[0].get_deferred_fields()
    html = response.content.decode()
    assert "bounded-worker-099" in html and "Search workers" not in html
    no_controls(response.content)


def test_database_usage_and_failed_save_never_measure_storage(console, org, monkeypatch, settings):
    def forbidden(*args):
        raise AssertionError("primary render measured uploaded files")
    monkeypatch.setattr(quotas, "storage_bytes_used", forbidden)
    WorkerHeartbeat.objects.create(worker_id="bounded-coordinator")  # the coordinator measures
    settings.TRELLUM_QUOTAS_ENABLED = True
    path = f"/operator/orgs/{org.slug}/"
    OrgQuota.objects.create(org=org, max_storage_mb=4096, max_reports=77)
    response = console.get(path)
    assert b"measuring" in response.content
    assert b"4096 MB" in response.content
    assert response.context["usage"]["storage_mb"] is None
    failed = console.post(path + "quota", {"max_concurrent_runs": "draft", "max_reports": "12"})
    assert failed.status_code == 400
    assert b'value="draft"' in failed.content
    assert b"measuring" in failed.content
    assert OrgQuota.objects.get(org=org).max_reports == 77
    assert not AuditLog.objects.filter(action="operator.org.quota").exists()


def test_pool_and_quota_mutations_remain_authoritative_and_audited(console, org):
    studios = Studio.objects.bulk_create([
        Studio(org=org, slug=f"pool-{i:03}", name="Pool") for i in range(30)
    ])
    target = studios[-1]
    response = console.post(f"/operator/orgs/{org.slug}/studios/{target.slug}/pool", {"pool": "large"})
    assert response.status_code == 302
    target.refresh_from_db()
    assert target.pool == "large"
    assert AuditLog.objects.filter(action="operator.studio.pool", org=org).count() == 1
    saved = console.post(f"/operator/orgs/{org.slug}/quota", {"max_studios": "40"},
                         HTTP_X_TRELLUM_FORM="1", HTTP_ACCEPT="application/json")
    assert saved.json()["refresh"] == ["quota-usage"]
    assert OrgQuota.objects.get(org=org).max_studios == 40
    assert AuditLog.objects.filter(action="operator.org.quota", org=org).count() == 1


def test_denied_read_and_operator_revocation(client, member, org):
    paths = ("/operator/", "/operator/orgs/", f"/operator/orgs/{org.slug}/")
    client.force_login(member)
    for path in paths:
        assert client.get(path, {"q": "foreign"}).status_code == 404
    operator = get_user_model().objects.create_user(email="revoked-ops@portal.local", is_operator_flag=True)
    client.force_login(operator)
    assert client.get(paths[1]).status_code == 200
    get_user_model().objects.filter(pk=operator.pk).update(is_operator_flag=False)
    for path in paths:
        assert client.get(path).status_code == 404
