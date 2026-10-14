"""Whole-request native lists, full totals and DB-only quota presentation."""
from urllib.parse import parse_qs, urlsplit

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


@pytest.mark.parametrize("size", [20, 200, 1000])
def test_org_growth_and_complete_search(console, size):
    Organization.objects.bulk_create([
        Organization(slug=f"bounded-{i:04}", name=f"Organization {i:04}", is_active=i % 2 == 0)
        for i in range(size)
    ])
    with CaptureQueriesContext(connection) as queries:
        response = console.get("/operator/orgs/")
    assert response.status_code == 200
    assert len(response.context["orgs"]) == min(25, size)
    assert response.context["org_page"]["total"] == size
    assert len(response.content) <= 160 * 1024
    assert len(queries) <= 24
    searched = console.get("/operator/orgs/", {"q": f"Organization {size - 1:04}", "state": "suspended"})
    assert searched.context["org_page"]["total"] == size
    assert searched.context["org_page"]["matching"] == 1
    assert [row.slug for row in searched.context["orgs"]] == [f"bounded-{size - 1:04}"]
    missing = console.get("/operator/orgs/", {"q": "absent identity"})
    assert b"No matching organizations." in missing.content
    assert missing.context["org_page"]["matching"] == 0


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
    response = console.get("/operator/orgs/", {"q": org.slug})
    row = list(response.context["orgs"])[0]
    assert row.studio_count == Studio.objects.filter(org=org).count()
    assert row.member_count == OrgMembership.objects.filter(org=org).count()
    assert row.active_runs == 1
    assert "default_theme" in row.get_deferred_fields()
    assert "quota" not in getattr(row, "_prefetched_objects_cache", {})


def test_paging_complete_and_stable(console):
    Organization.objects.bulk_create([
        Organization(slug=f"page-{i:03}", name="Same name") for i in range(61)
    ])
    seen = []
    for page in (1, 2, 3):
        response = console.get("/operator/orgs/", {"q": "Same name", "page": page})
        seen.extend(row.pk for row in response.context["orgs"])
    assert len(seen) == len(set(seen)) == 61
    final = console.get("/operator/orgs/", {"page": 999})
    assert final.context["org_page"]["page"].number == 3


def test_detail_independent_pages_search_totals_and_narrow_fields(console, org):
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
        response = console.get(path, {"studio_page": 2, "member_page": 3})
    assert response.status_code == 200
    assert len(response.context["studios"]) == len(response.context["members"]) == 25
    assert response.context["studio_page"]["total"] == response.context["usage"]["studios"] == 50
    assert response.context["member_page"]["total"] == 100
    assert response.context["usage"]["reports"] == 1
    assert len(queries) <= 28
    assert len(response.content) <= 160 * 1024
    assert list(response.context["studios"])[-1].report_count == 1
    assert "password" in list(response.context["members"])[0].user.get_deferred_fields()
    previous = parse_qs(urlsplit(response.context["studio_page"]["previous"]).query)
    assert previous["member_page"] == ["3"]
    assert previous["studio_page"] == ["1"]
    matched = console.get(path, {"member_q": "Person 099", "studio_q": "Studio 049"})
    assert matched.context["member_page"]["matching"] == matched.context["studio_page"]["matching"] == 1
    assert matched.context["member_page"]["total"] == 100
    assert matched.context["studio_page"]["total"] == 50
    assert ("member_q", "Person 099") in matched.context["studio_preserved"]
    missing = console.get(path, {"studio_q": "absent", "member_q": "absent"})
    assert b"No matching studios." in missing.content
    assert b"No matching members." in missing.content


def test_fleet_full_aggregates_and_bounded_existing_sections(console, org, studio):
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
    response = console.get("/operator/", {"worker_role": "runner", "worker_page": 4})
    assert len(response.context["workers"]) == 25
    assert response.context["worker_page"]["total"] == 101
    assert response.context["worker_page"]["matching"] == 100
    assert response.context["worker_totals"]["runners"] == 100
    assert response.context["worker_totals"]["coordinators"] == 1
    assert response.context["worker_totals"]["running"] == 100
    assert response.context["worker_totals"]["capacity"] == 400
    assert response.context["worker_totals"]["reserved_memory"] == 2500
    assert len(response.context["recent_failures"]) == 20
    assert len(response.context["heaviest"]) == 10
    assert "stdout_tail" in list(response.context["recent_failures"])[0].get_deferred_fields()
    found = console.get("/operator/", {"worker_q": "bounded-worker-099"})
    assert found.context["worker_page"]["matching"] == 1
    assert found.context["worker_totals"]["total"] == 101
    assert b"No matching workers." in console.get("/operator/", {"worker_q": "absent"}).content


@pytest.mark.parametrize("path,params", [
    ("/operator/orgs/", {"page": "-1"}),
    ("/operator/orgs/", {"page": "0"}),
    ("/operator/orgs/", {"page": "1.5"}),
    ("/operator/orgs/", {"q": "x" * 201}),
    ("/operator/orgs/", {"state": "unknown"}),
    ("/operator/", {"worker_role": "unknown"}),
    ("/operator/", {"worker_page": "９"}),
])
def test_invalid_browse_parameters(console, path, params):
    assert console.get(path, params).status_code == 400


def test_database_usage_and_native_error_never_measure_storage(console, org, monkeypatch):
    def forbidden(*args):
        raise AssertionError("primary render measured uploaded files")
    monkeypatch.setattr(quotas, "storage_bytes_used", forbidden)
    path = f"/operator/orgs/{org.slug}/"
    OrgQuota.objects.create(org=org, max_storage_mb=4096, max_reports=77)
    response = console.get(path)
    assert b"Storage has not been measured." in response.content
    assert b"4096 MB" in response.content
    failed = console.post(path + "quota", {"max_concurrent_runs": "draft", "max_reports": "12"})
    assert failed.status_code == 400
    assert b'value="draft"' in failed.content
    assert b"Storage has not been measured." in failed.content
    assert OrgQuota.objects.get(org=org).max_reports == 77
    assert not AuditLog.objects.filter(action="operator.org.quota").exists()


def test_offpage_mutations_remain_authoritative_and_audited(console, org):
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
