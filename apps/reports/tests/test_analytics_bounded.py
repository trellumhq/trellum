"""Global analytics semantics and bounded response/query shape on PostgreSQL."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports.analytics import analytics_payload, analytics_relation
from apps.reports.models import Report, ReportSourceSummary, ReportViewDaily, ReportViewEvent, ShareLink
from apps.reports.views import _analytics_days

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(studio):
    return Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{i:04}", name=f"Report {i:04}") for i in range(1000)
    ])


def test_global_search_sort_headers_never_viewed_and_projection(studio, catalog, member):
    now = timezone.now()
    target = catalog[-1]
    link = ShareLink.objects.create(report=target)
    ReportViewDaily.objects.create(report=target, date=now.date(), views=100)
    events = [ReportViewEvent(report=target, user=member, bucket=now-timedelta(hours=i)) for i in range(2)]
    events += [ReportViewEvent(report=target, share_link=link, bucket=now-timedelta(hours=i)) for i in range(3)]
    events += [ReportViewEvent(report=target, bucket=now)]
    ReportViewEvent.objects.bulk_create(events)
    for sort in ("views", "unique", "share", "last_viewed"):
        result = analytics_payload(studio, {"sort": sort, "direction": "desc"}, 30, now=now+timedelta(seconds=1))
        assert result["rows"][0]["report"].pk == target.pk
        assert result["rows"][0]["unique_total"] == 2
        assert result["rows"][0]["share_views"] == 3
        assert len(result["rows"]) == 25
    for q in ("report-0999", "Report 0999"):
        result = analytics_payload(studio, {"q": q}, 7, now=now+timedelta(seconds=1))
        assert len(result["rows"]) == 1 and result["rows"][0]["views"] == 100
        assert result["reports_viewed_count"] == 1 and result["total_share_views"] == 3
    result = analytics_payload(studio, {"q": "Report 0000"}, 30, now=now)
    assert result["rows"][0]["views"] == 0
    assert result["reports_viewed_count"] == 1 and result["total_share_views"] == 3
    assert "config" in result["rows"][0]["report"].get_deferred_fields()
    assert "description" in result["rows"][0]["report"].get_deferred_fields()
    catalog[0].present_in_scan = False; catalog[0].save(update_fields=["present_in_scan"])
    assert analytics_payload(studio, {"q": "report-0000"}, 30)["rows"] == []


def test_daily_date_raw_timestamp_boundaries_and_alltime_last_viewed(studio, member):
    now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
    report = Report.objects.create(studio=studio, slug="boundary")
    old = ReportViewEvent.objects.create(report=report, user=member, bucket=now-timedelta(days=50))
    ReportViewEvent.objects.filter(pk=old.pk).update(created_at=now-timedelta(days=50))
    ReportViewDaily.objects.bulk_create([
        ReportViewDaily(report=report, date=now.date()-timedelta(days=7), views=5),
        ReportViewDaily(report=report, date=now.date()-timedelta(days=8), views=10),
    ])
    row = analytics_payload(studio, {}, 7, now=now)["rows"][0]
    assert row["views"] == 5 and row["unique_total"] == 0
    assert row["last_viewed"] == now-timedelta(days=50)
    assert analytics_payload(studio, {}, 30, now=now)["rows"][0]["views"] == 15
    ReportViewEvent.objects.filter(pk=old.pk).update(created_at=now-timedelta(days=7))
    assert analytics_payload(studio, {}, 7, now=now)["rows"][0]["unique_users"] == 1
    ReportViewEvent.objects.filter(pk=old.pk).update(created_at=now-timedelta(days=7, microseconds=1))
    assert analytics_payload(studio, {}, 7, now=now)["rows"][0]["unique_users"] == 0


@pytest.mark.parametrize("state,quality,status,age,expected", [
    ("ready", "observed", "success", 0, True), ("ready", "verified", "success", 0, True),
    ("ready", "verified", "failed", 0, False), ("ready", "unknown", "success", 0, False),
    ("pending", "unknown", "success", 0, False), ("unavailable", "verified", "success", 0, False),
    ("uncertain", "observed", "success", 0, False), ("expired", "verified", "success", 0, False),
    ("removed", "verified", "success", 0, False), ("ready", "verified", "success", 301, False),
])
def test_unseen_observation_gate(studio, state, quality, status, age, expected):
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="fresh")
    ReportSourceSummary.objects.create(report=report, state=state, quality=quality,
        observed_at=now-timedelta(seconds=age), attempt_at=now-timedelta(days=1), attempt_status=status,
        served_at=now-timedelta(days=2))
    row = analytics_payload(studio, {}, 7, now=now)["rows"][0]
    assert row["stale"] is expected
    if state != "ready" or quality == "unknown" or age:
        assert row["observation"] == "Build observation unavailable"
    ReportViewDaily.objects.create(report=report, date=now.date()-timedelta(days=20), views=1)
    assert not analytics_payload(studio, {}, 7, now=now)["rows"][0]["stale"]


def test_missing_unknown_and_attempt_recency(studio):
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="unknown")
    assert analytics_payload(studio, {}, 30, now=now)["rows"][0]["observation"] == "Build observation unavailable"
    summary = ReportSourceSummary.objects.create(report=report, state="missing", quality="verified", observed_at=now)
    assert analytics_payload(studio, {}, 30, now=now)["rows"][0]["observation"] == "Not built"
    summary.state = "ready"; summary.attempt_at = now-timedelta(days=8)
    summary.attempt_status = "success"; summary.served_at = now; summary.save()
    assert not analytics_payload(studio, {}, 30, now=now)["rows"][0]["stale"]


@pytest.mark.parametrize("params", [{"page": "9"*10000}, {"page": "-2"}, {"page": "zero"},
    {"page": "999999999"}, {"sort": "config", "direction": "invalid"}, {"q": "x"*10000}])
def test_pathological_controls_are_bounded(studio, catalog, params):
    result = analytics_payload(studio, params, _analytics_days("9"*10000))
    assert len(result["rows"]) <= 25 and len(result["q"]) <= 200
    assert result["sort"] in ("report", "views", "unique", "share", "last_viewed")
    assert result["direction"] in ("asc", "desc")
    assert result["page"].number <= 40


@pytest.mark.parametrize("role,cap", [(roles.ADMIN, 24), (roles.DEVELOPER, 27)])
def test_whole_middleware_growth_storage_and_html_bounds(studio, member, grant_studio, login, monkeypatch, role, cap):
    grant_studio(member, studio, role)
    def forbidden(*args, **kwargs):
        raise AssertionError("Analytics GET must never read storage")
    for name in ("read_meta", "read_live_queries", "output_root", "html_index"):
        monkeypatch.setattr(f"apps.core.storage.{name}", forbidden)
    counts = []
    for total in (20, 200, 1000):
        existing = Report.objects.filter(studio=studio).count()
        Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name="Long report name "*8+str(i), config={"huge": "x"*10000}) for i in range(existing, total)])
        # A new client exercises cold shell session behavior each time.
        client = Client()
        client.force_login(member)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(f"/s/{studio.org.slug}/{studio.slug}/analytics")
        assert response.status_code == 200
        assert len(captured) <= cap
        counts.append(len(captured))
        assert len(response.content) <= 160*1024
        assert len(response.context["rows"]) == min(total, 25)
        assert response.context["report_count"] == total
        # Projection does not fetch heavy JSON, and no expanded IN list appears.
        page_sql = [q["sql"] for q in captured if "LIMIT 25" in q["sql"] or f"LIMIT {total}" in q["sql"]]
        assert page_sql
        assert all('"config"' not in sql and '"runtime"' not in sql and '"description"' not in sql for sql in page_sql)
        print("ANALYTICS_GROWTH", role, total, len(captured), len(response.content))
    assert len(set(counts)) == 1


def test_distinct_is_per_report_and_deleted_references_preserve_view_totals(studio, member):
    now = timezone.now()
    reports = [Report.objects.create(studio=studio, slug=f"same-{i}", name="Same") for i in range(2)]
    for report in reports:
        link = ShareLink.objects.create(report=report)
        ReportViewEvent.objects.create(report=report, user=member, bucket=now)
        ReportViewEvent.objects.create(report=report, share_link=link, bucket=now)
        ReportViewDaily.objects.create(report=report, date=now.date(), views=2)
    result = analytics_payload(studio, {}, 30)
    assert [row["report"].pk for row in result["rows"]] == [report.pk for report in reports]
    assert sum(row["unique_total"] for row in result["rows"]) == 4
    assert result["reports_viewed_count"] == 2 and result["total_share_views"] == 2
    ShareLink.objects.filter(report=reports[0]).delete()
    result = analytics_payload(studio, {}, 30)
    assert [row["unique_total"] for row in result["rows"]] == [1, 2]
    assert result["rows"][0]["views"] == 2 and result["total_share_views"] == 1
    # SET_NULL does not erase retained events or daily totals.
    ReportViewEvent.objects.filter(user=member).update(user=None)
    result = analytics_payload(studio, {}, 30)
    assert [row["unique_total"] for row in result["rows"]] == [0, 1]
    assert result["reports_viewed_count"] == 2


@pytest.mark.parametrize("raw,expected", [("7",7), ("30",30), ("90",90), ("91",90), ("0",30), ("-3",30), ("x",30), ("9"*10000,30)])
def test_days_controls(raw, expected):
    assert _analytics_days(raw) == expected
