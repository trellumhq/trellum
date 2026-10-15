"""Global analytics semantics and bounded response/query shape on PostgreSQL."""
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports.analytics import CLIENT_ROWS, PAGE_SIZE, analytics_payload, analytics_relation
from apps.reports.models import Report, ReportSourceSummary, ReportViewDaily, ReportViewEvent, ShareLink
from apps.reports.views import _analytics_days

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(studio):
    return Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{i:04}", name=f"Report {i:04}") for i in range(1000)
    ])


def test_large_studio_global_search_sort_never_viewed_and_projection(studio, catalog, member):
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
        assert len(result["rows"]) == PAGE_SIZE and result["server_side"]
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


@pytest.mark.parametrize("status,attempt_age_days,views,expected", [
    ("success", 0, 0, True), ("success", 6, 0, True), ("success", 8, 0, False),
    ("failed", 0, 0, False), ("", 0, 0, False), ("success", 0, 1, False),
    ("success", None, 0, False),
])
def test_unseen_chip_follows_the_last_successful_build_and_recent_views(studio, status, attempt_age_days, views, expected):
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="fresh")
    ReportSourceSummary.objects.create(
        report=report, attempt_status=status,
        attempt_at=None if attempt_age_days is None else now - timedelta(days=attempt_age_days, minutes=1),
        # The observation's own age is irrelevant to the chip.
        observed_at=now - timedelta(days=5), state="uncertain", quality="unknown")
    if views:
        ReportViewDaily.objects.create(report=report, date=now.date() - timedelta(days=20), views=views)
    row = analytics_payload(studio, {}, 7, now=now)["rows"][0]
    assert row["stale"] is expected
    assert "observation" not in row


def test_report_without_any_source_summary_shows_no_chip(studio):
    Report.objects.create(studio=studio, slug="unknown")
    assert analytics_payload(studio, {}, 30)["rows"][0]["stale"] is False


def test_up_to_the_threshold_every_row_is_sent_for_browser_side_filtering(studio):
    Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"Report {CLIENT_ROWS - i:04}") for i in range(CLIENT_ROWS)])
    result = analytics_payload(studio, {"q": "nothing-matches", "sort": "views", "direction": "desc", "page": "9"}, 30)
    assert result["server_side"] is False and result["page"] is None
    assert len(result["rows"]) == CLIENT_ROWS and result["q"] == ""
    # Name order, whatever the (ignored) server-side controls say.
    assert [row["report"].name for row in result["rows"]][:2] == ["Report 0001", "Report 0002"]
    Report.objects.create(studio=studio, slug="one-more", name="One more")
    bigger = analytics_payload(studio, {"q": "one more"}, 30)
    assert bigger["server_side"] is True and len(bigger["rows"]) == 1
    assert bigger["page"].paginator.count == 1


@pytest.mark.parametrize("params", [{"page": "9"*10000}, {"page": "-2"}, {"page": "zero"},
    {"page": "999999999"}, {"sort": "config", "direction": "invalid"}, {"q": "x"*10000}])
def test_pathological_controls_are_bounded(studio, catalog, params):
    result = analytics_payload(studio, params, _analytics_days("9"*10000))
    assert len(result["rows"]) <= PAGE_SIZE and len(result["q"]) <= 200
    assert result["sort"] in ("report", "views", "unique", "share", "last_viewed")
    assert result["direction"] in ("asc", "desc")
    assert result["page"].number <= 5


@pytest.mark.parametrize("role,cap", [(roles.ADMIN, 24), (roles.DEVELOPER, 27)])
def test_whole_middleware_growth_storage_and_html_bounds(studio, member, grant_studio, login, monkeypatch, role, cap):
    grant_studio(member, studio, role)
    def forbidden(*args, **kwargs):
        raise AssertionError("Analytics GET must never read storage")
    for name in ("read_meta", "read_live_queries", "output_root", "html_index"):
        monkeypatch.setattr(f"apps.core.storage.{name}", forbidden)
    counts = {}
    for total in (20, 200, CLIENT_ROWS, CLIENT_ROWS + 1, 1000):
        existing = Report.objects.filter(studio=studio).count()
        Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name="Long report name "*8+str(i), config={"huge": "x"*10000}) for i in range(existing, total)])
        # A new client exercises cold shell session behavior each time.
        client = Client()
        client.force_login(member)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(f"/s/{studio.org.slug}/{studio.slug}/analytics")
        assert response.status_code == 200
        assert len(captured) <= cap
        server_side = total > CLIENT_ROWS
        counts.setdefault(server_side, set()).add(len(captured))
        # Whole studio up to the threshold, one pager page beyond it.
        assert len(response.context["rows"]) == (PAGE_SIZE if server_side else total)
        assert len(response.content) <= 700*1024
        assert response.context["report_count"] == total
        # Projection does not fetch heavy JSON, and no expanded IN list appears.
        page_sql = [q["sql"] for q in captured if '"reports_report"."slug"' in q["sql"] and 'AS "views"' in q["sql"]]
        assert page_sql
        assert all('"config"' not in sql and '"runtime"' not in sql and '"description"' not in sql for sql in page_sql)
        print("ANALYTICS_GROWTH", role, total, len(captured), len(response.content))
    assert all(len(values) == 1 for values in counts.values())


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


def test_page_has_main_controls_in_both_modes(studio, member, grant_studio, login):
    grant_studio(member, studio, roles.DEVELOPER)
    base = f"/s/{studio.org.slug}/{studio.slug}/analytics"
    Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:03}", name=f"Report {i:03}") for i in range(3)])
    client = login(member)

    def content(url):
        # The shell has its own search box and buttons; check the page itself.
        return client.get(url).content.decode().split("<main", 1)[1].split("</main>", 1)[0]

    body = content(base)
    for present in ('placeholder="Filter reports…"', 'data-sort="report"', 'data-sort="last_viewed"', 'href="?days=7"', 'href="?days=90"', "report_analytics.js"):
        assert present in body, present
    for absent in ("<button", "Search report names", "Sort by", "Order</label", "Previous", "Next", "ui-pager", "data-server-side", "unavailable"):
        assert absent not in body, absent
    Report.objects.bulk_create([Report(studio=studio, slug=f"big-{i:04}", name=f"Big {i:04}") for i in range(CLIENT_ROWS)])
    body = content(base + "?q=big&sort=views&direction=desc&days=7")
    assert 'data-server-side data-sort-col="views" data-sort-dir="desc"' in body
    assert 'value="big"' in body and 'class="ui-pager"' in body and "1–200 of 500" in body
    assert 'href="?days=30&amp;q=big&amp;sort=views&amp;direction=desc"' in body
    assert "<button" not in body and "Search report names" not in body
