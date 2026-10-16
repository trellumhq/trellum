"""Registry-shaped dashboard rows read from the database in bounded slices."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import storage
from apps.reports.catalog import CARDS_PAGE, catalog_cards_payload
from apps.reports.models import Report, ReportSourceSummary

pytestmark = pytest.mark.django_db


def request(user, studio):
    return SimpleNamespace(user=user, studio=studio, org=studio.org)


def test_rows_keep_the_last_observation_instead_of_blanking_after_five_minutes(studio, org_admin):
    now = timezone.now()
    built = Report.objects.create(studio=studio, slug="built", name="Built")
    failed = Report.objects.create(studio=studio, slug="failed", name="Failed")
    ReportSourceSummary.objects.create(report=built, state="ready", runtime_state="ready", quality="observed",
                                       observed_at=now - timedelta(hours=3), attempt_at=now - timedelta(hours=3),
                                       attempt_status="success", has_output=True, html_entry="report.html")
    ReportSourceSummary.objects.create(report=failed, state="ready", runtime_state="ready", quality="observed",
                                       observed_at=now - timedelta(hours=3), attempt_at=now - timedelta(hours=1),
                                       attempt_status="error", has_output=False)
    rows = {r["slug"]: r for r in catalog_cards_payload(request(org_admin, studio), {}, now=now)["reports"]}
    assert rows["built"]["last_status"] == "success" and rows["built"]["has_output"] is True
    assert rows["built"]["html_entry"] == "report.html"
    assert rows["built"]["last_run"].startswith((now - timedelta(hours=3)).date().isoformat()[:4])
    assert rows["failed"]["last_status"] == "error" and rows["failed"]["has_output"] is False


def test_unobserved_output_still_opens_the_report(studio, org_admin):
    Report.objects.create(studio=studio, slug="fresh", name="Fresh")
    row = catalog_cards_payload(request(org_admin, studio), {})["reports"][0]
    assert row["has_output"] is True and row["html_entry"] == ""  # the dashboard falls back to index.html
    assert row["last_status"] == "not_run" and row["last_run"] is None and row["stale"] is False


def test_slices_are_registry_ordered_and_cost_does_not_grow_with_the_studio(studio, org_admin, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dashboard slices read the database only")

    monkeypatch.setattr(storage, "read_meta", forbidden)
    monkeypatch.setattr(storage, "html_index", forbidden)
    counts = []
    for size in (10, 400):
        Report.objects.filter(studio=studio).delete()
        Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"R {i}", priority=i % 3) for i in range(size)])
        with CaptureQueriesContext(connection) as queries:
            payload = catalog_cards_payload(request(org_admin, studio), {"page": "1"})
        counts.append(len(queries))
        assert len(payload["reports"]) == min(size, CARDS_PAGE)
        assert payload["pagination"]["total"] == size
    assert counts[0] == counts[1]
    order = [(r["slug"]) for r in payload["reports"]]
    expected = [r.slug for r in Report.objects.filter(studio=studio).order_by("priority", "slug")[:CARDS_PAGE]]
    assert order == expected


def test_slices_cover_every_row_exactly_once(studio, org_admin):
    Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:03}", name=f"R {i}") for i in range(123)])
    seen = []
    for page in (1, 2, 3):
        payload = catalog_cards_payload(request(org_admin, studio), {"page": str(page)})
        seen += [r["id"] for r in payload["reports"]]
    assert len(seen) == len(set(seen)) == 123
    # Smaller pages cover the same rows in the same order.
    small = []
    for page in range(1, 6):
        small += [r["id"] for r in catalog_cards_payload(request(org_admin, studio), {"page": str(page), "limit": "25"})["reports"]]
    assert small == seen[:125]


def test_oversized_slice_is_explicit(studio, org_admin, monkeypatch):
    Report.objects.create(studio=studio, slug="big", name="Big")
    monkeypatch.setattr("apps.reports.catalog.CARDS_BYTES", 10)
    payload = catalog_cards_payload(request(org_admin, studio), {})
    assert payload["state"] == "oversized" and "reports" not in payload and payload["pagination"]["total"] == 1


def test_missing_summaries_cost_one_bounded_query_for_the_page(studio, org_admin, monkeypatch):
    from apps.runner.models import Run

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Dashboard slices read the database only")

    monkeypatch.setattr(storage, "read_meta", forbidden)
    monkeypatch.setattr(storage, "html_index", forbidden)
    now = timezone.now()
    counts = []
    for size in (10, 400):
        Run.objects.filter(studio=studio).delete()
        Report.objects.filter(studio=studio).delete()
        reports = Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"R {i}") for i in range(size)])
        Run.objects.bulk_create([
            Run(report=r, studio=studio, slug=r.slug, status=Run.SUCCESS, created_at=now - timedelta(minutes=i % 50 + 1))
            for r in reports for i in range(3)
        ])
        with CaptureQueriesContext(connection) as queries:
            payload = catalog_cards_payload(request(org_admin, studio), {"page": "1"}, now=now)
        counts.append(len(queries))
        assert {r["last_status"] for r in payload["reports"]} == {"success"}
        assert all(r["last_run"] for r in payload["reports"])
    assert counts[0] == counts[1]


def test_known_summaries_add_no_run_query(studio, org_admin):
    from apps.runner.models import Run

    now = timezone.now()
    report = Report.objects.create(studio=studio, slug="known", name="Known")
    ReportSourceSummary.objects.create(report=report, state="ready", runtime_state="ready", quality="observed",
                                       observed_at=now, attempt_at=now, attempt_status="success")
    Run.objects.create(report=report, studio=studio, slug="known", status=Run.ERROR)

    def run_queries():
        with CaptureQueriesContext(connection) as queries:
            catalog_cards_payload(request(org_admin, studio), {}, now=now)
        return [q for q in queries.captured_queries if '"finished_at"' in q["sql"] and "DISTINCT ON" in q["sql"]]

    assert run_queries() == []
    ReportSourceSummary.objects.filter(report=report).delete()
    assert len(run_queries()) == 1
