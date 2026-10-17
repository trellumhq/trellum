"""One registry row and one last-status rule, for the dashboard, Operations and API clients."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.datasources.models import DataSource, RepoDataSource
from apps.orgs.models import PermissionGroupGrant
from apps.reports import operations
from apps.reports.catalog import registry_page_payload, registry_payload
from apps.reports.models import Report, ReportPermissionGrant, ReportSourceSummary
from apps.reports.registry_rows import (
    ROWS_PER_PAGE,
    derive_status,
    source_states_for,
    source_states_payload,
)
from apps.runner.models import Run

NOW = timezone.now()


def minutes(count):
    return NOW + timedelta(minutes=count)


def run_row(status, start, end=None, tail=""):
    return {"slug": "r", "status": status, "created_at": start, "started_at": start, "finished_at": end, "tail": tail}


def attempt(status, at, error="", blocked=()):
    return {"status": status, "at": at, "error": error, "blocked": list(blocked)}


# ── The status rule, without a database ────────────────────────────────────

def test_a_report_nobody_built_is_not_run():
    result = derive_status(None, None, None)
    assert (result["last_status"], result["build_status"], result["last_run"], result["error"]) == ("not_run", "not_run", None, "")


def test_a_run_alone_decides_with_its_own_detail():
    done = run_row(Run.OOM_KILLED, minutes(-10), minutes(-9), tail="killed")
    result = derive_status(None, done, done)
    assert (result["last_status"], result["build_status"], result["last_run"], result["error"]) == ("error", "oom_killed", minutes(-9), "killed")


def test_a_summary_alone_decides():
    result = derive_status(attempt("success", minutes(-5)), None, None)
    assert (result["last_status"], result["build_status"], result["last_run"]) == ("success", "success", minutes(-5))


def test_a_summary_that_saw_the_run_gives_the_text_and_the_run_the_detail():
    done = run_row(Run.TIMEOUT, minutes(-10), minutes(-8), tail="raw stderr")
    result = derive_status(attempt("error", minutes(-9), error="TimeoutError: slow"), done, done)
    assert (result["last_status"], result["build_status"]) == ("error", "timeout")
    assert result["last_run"] == minutes(-9) and result["error"] == "TimeoutError: slow"


def test_a_run_newer_than_the_summary_wins_over_it():
    done = run_row(Run.SUCCESS, minutes(-3), minutes(-2))
    result = derive_status(attempt("error", minutes(-30), error="old failure"), done, done)
    assert (result["last_status"], result["last_run"], result["error"]) == ("success", minutes(-2), "")


def test_a_build_in_progress_changes_only_the_build_status():
    done = run_row(Run.SUCCESS, minutes(-30), minutes(-29))
    active = run_row(Run.RUNNING, minutes(-1))
    result = derive_status(None, active, done)
    assert (result["last_status"], result["build_status"], result["last_run"]) == ("success", "running", minutes(-29))


def test_an_active_row_older_than_the_last_outcome_is_ignored():
    stuck = run_row(Run.RUNNING, minutes(-60))
    result = derive_status(attempt("success", minutes(-5)), stuck, None)
    assert (result["last_status"], result["build_status"]) == ("success", "success")


def test_the_run_tail_is_the_error_when_the_build_wrote_none():
    done = run_row(Run.ERROR, minutes(-3), minutes(-2), tail="Traceback boom")
    assert derive_status(attempt("error", minutes(-2)), done, done)["error"] == "Traceback boom"


def test_source_attribution_in_the_run_tail_wins_over_a_plain_error():
    done = run_row(Run.ERROR, minutes(-3), minutes(-2), tail="Data source 'db': refused\nTraceback")
    result = derive_status(attempt("error", minutes(-2), error="Traceback"), done, done)
    assert result["error"].startswith("Data source 'db': refused")


def test_a_summary_later_than_every_run_stands_on_its_own():
    done = run_row(Run.SUCCESS, minutes(-60), minutes(-59))
    result = derive_status(attempt("error", minutes(-5), error="later build"), done, done)
    assert (result["last_status"], result["build_status"], result["last_run"], result["error"]) == ("error", "error", minutes(-5), "later build")


def test_the_error_is_capped_and_only_failures_have_one():
    done = run_row(Run.ERROR, minutes(-3), minutes(-2), tail="y" * 5000)
    assert len(derive_status(None, done, done)["error"]) == 500
    ok = run_row(Run.SUCCESS, minutes(-3), minutes(-2), tail="noise")
    assert derive_status(None, ok, ok)["error"] == ""


# ── Rows from the database ─────────────────────────────────────────────────

def request(user, studio):
    return SimpleNamespace(user=user, studio=studio, org=studio.org)


def observe(report, **fields):
    """The summary row a worker projects for ``report``."""
    values = {"schema_version": 2, "state": "ready", "runtime_state": "ready", "quality": "observed", "observed_at": NOW,
              "attempt_at": NOW, "attempt_status": "success", "has_output": True, "html_entry": "index.html", **fields}
    return ReportSourceSummary.objects.update_or_create(report=report, defaults=values)[0]


def whole(req, **kwargs):
    return registry_payload(req, **kwargs)["reports"]


def paged(req, **kwargs):
    rows, page = [], 1
    while True:
        payload = registry_page_payload(req, {"page": str(page)}, **kwargs)
        rows += payload["reports"]
        if not payload["pagination"]["has_next"]:
            return rows
        page += 1


@pytest.mark.django_db
def test_the_paged_the_whole_and_the_operations_rows_are_the_same_row(studio, org_admin):
    reports = [
        Report.objects.create(studio=studio, slug="built", name="Built", description="d" * 3000, tags=[f"tag{i}" for i in range(40)],
                              schedule_cron="0 6 * * *", last_built_at=NOW - timedelta(hours=1), config={"data_sources": ["db"]}),
        Report.objects.create(studio=studio, slug="paused", refresh_suspended_at=NOW - timedelta(days=2), schedule_cron="0 6 * * *"),
        Report.objects.create(studio=studio, slug="failed", category="Ops"),
        Report.objects.create(studio=studio, slug="fresh"),
        Report.objects.create(studio=studio, slug="an", kind=Report.KIND_ANALYSIS),
        Report.objects.create(studio=studio, slug="metrics", tags=["metrics"]),
    ]
    observe(reports[0], attempt_at=NOW - timedelta(minutes=5), runtime={
        "validation": {"summary": {"pass": 3, "fail": 0, "warn": 1}},
        "details": {"totals": {"chart_count": 4, "total_rows": 120}, "data_source": "real"}, "framework_version": "0.8.0"},
        has_live_queries=True)
    observe(reports[2], attempt_status="error", attempt_error="boom", has_output=False)
    Run.objects.create(report=reports[0], studio=studio, slug="built", status=Run.SUCCESS, created_at=NOW - timedelta(minutes=6),
                       started_at=NOW - timedelta(minutes=6), finished_at=NOW - timedelta(minutes=5))
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "x"})
    req = request(org_admin, studio)
    everything = whole(req)
    assert [row["slug"] for row in everything] == [r.slug for r in Report.objects.filter(studio=studio).order_by("priority", "slug")]
    assert everything == paged(req)
    ops = operations.operations_page_payload(request_for(org_admin, studio), {"page_size": "500"})["rows"]
    assert [{k: v for k, v in row.items() if k != "next_run"} for row in ops] == everything
    built = {row["slug"]: row for row in everything}["built"]
    assert len(built["tags"]) == 40 and len(built["description"]) == 3000
    assert built["validation"] == {"summary": {"pass": 3, "fail": 0, "warn": 1}}
    assert built["details"] == {"totals": {"chart_count": 4, "total_rows": 120}, "data_source": "real"}
    assert built["framework_version"] == "0.8.0" and built["live"] is True and built["last_built_at"]
    paused = {row["slug"]: row for row in everything}["paused"]
    assert paused["refresh_suspended_at"]
    assert {row["slug"]: row for row in everything}["metrics"]["metrics_report"] is True


def request_for(user, studio):
    req = RequestFactory().get("/")
    req.user, req.org, req.studio = user, studio.org, studio
    return req


@pytest.mark.django_db
def test_the_paged_registry_keeps_the_paused_badge_data(studio, org_admin):
    Report.objects.create(studio=studio, slug="paused", refresh_suspended_at=NOW - timedelta(days=2))
    row = registry_page_payload(request(org_admin, studio), {"page": "1"})["reports"][0]
    assert row["refresh_suspended_at"]


@pytest.mark.django_db
def test_both_kinds_unless_one_is_asked_for(studio, org_admin):
    Report.objects.create(studio=studio, slug="r")
    Report.objects.create(studio=studio, slug="a", kind=Report.KIND_ANALYSIS)
    req = request(org_admin, studio)
    assert sorted(row["slug"] for row in registry_page_payload(req, {"page": "1"})["reports"]) == ["a", "r"]
    assert [row["slug"] for row in registry_page_payload(req, {"page": "1"}, kind=Report.KIND_ANALYSIS)["reports"]] == ["a"]
    assert [row["slug"] for row in whole(req, kind=Report.KIND_REPORT)] == ["r"]
    with pytest.raises(ValueError):
        whole(req, kind="other")


@pytest.mark.django_db
@pytest.mark.parametrize("ready", [False, True])
def test_a_scoped_viewer_gets_the_same_list_from_both_paths(studio, member, make_group, attach_group, settings, ready):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True  # to configure the grant
    chosen = Report.objects.create(studio=studio, slug="chosen", audience="private")
    Report.objects.create(studio=studio, slug="hidden")
    group = make_group("selected", grants=[(studio, roles.VIEWER)])
    grant = group.grants.get()
    grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
    grant.save(update_fields=["viewer_scope"])
    ReportPermissionGrant.objects.create(grant=grant, report=chosen)
    attach_group(member, group)
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = ready
    req = request(member, studio)
    expected = ["chosen"] if ready else []
    assert [row["slug"] for row in whole(req)] == [row["slug"] for row in paged(req)] == expected


@pytest.mark.django_db
def test_unknown_output_is_unknown_not_present(studio, org_admin):
    Report.objects.create(studio=studio, slug="fresh")
    row = whole(request(org_admin, studio))[0]
    assert row["has_output"] is None and row["html_entry"] == "" and row["live"] is False
    assert row["validation"] is None and row["details"] is None and row["framework_version"] is None


@pytest.mark.django_db
def test_expired_data_has_no_output_even_when_the_summary_is_unknown(studio, org_admin):
    Report.objects.create(studio=studio, slug="expired", data_expired_at=NOW)
    row = whole(request(org_admin, studio))[0]
    assert row["has_output"] is False and row["html_entry"] == ""


@pytest.mark.django_db
def test_a_summary_that_is_not_ready_never_claims_output(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="odd")
    observe(report, runtime_state="unavailable")
    assert whole(request(org_admin, studio))[0]["has_output"] is None
    observe(report, state="uncertain", runtime_state="ready")
    assert whole(request(org_admin, studio))[0]["has_output"] is None
    observe(report, schema_version=1)
    assert whole(request(org_admin, studio))[0]["has_output"] is None


@pytest.mark.django_db
def test_every_tag_and_the_whole_description_are_searchable(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="long", tags=[f"tag{i:02}" for i in range(40)] + ["needle-tag"],
                                   description="x" * 2000 + " needle-words", category="Finance")
    Report.objects.create(studio=studio, slug="other")
    req = request(org_admin, studio)
    for term in ("needle-tag", "needle-words", "finance"):
        assert [r["id"] for r in registry_page_payload(req, {"q": term})["reports"]] == [report.pk]
        assert [r["id"] for r in operations.operations_page_payload(request_for(org_admin, studio), {"q": term})["rows"]] == [report.pk]
    assert len({r["slug"]: r for r in whole(req)}["long"]["tags"]) == 41


@pytest.mark.django_db
def test_operations_and_the_registry_search_the_same_fields(studio, org_admin):
    row = Report.objects.create(studio=studio, slug="the-slug", name="Alpha", description="Beta", tags=["Gamma"], category="Epsilon")
    Report.objects.create(studio=studio, slug="unrelated")
    for q in ("alpha", "the-slug", "beta", "gamma", "epsilon", f"epsilon {studio.slug}", "alpha beta"):
        assert [r["id"] for r in registry_page_payload(request(org_admin, studio), {"q": q})["reports"]] == [row.pk]
        assert [r["id"] for r in operations.operations_page_payload(request_for(org_admin, studio), {"q": q})["rows"]] == [row.pk]


@pytest.mark.django_db
def test_the_wait_ends_when_its_reason_does(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="held", config={"data_sources": ["db"]})
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "h", "database": "d"})
    observe(report, attempt_status="error", attempt_blocked_by=["db"])
    req = request(org_admin, studio)
    first = whole(req)[0]
    assert first["waiting"] is True and first["blocked_by"] == ["db"]
    binding = DataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "h", "database": "d"},
                                        credentials={"user": "reader", "password": "never-emitted"})
    cleared = whole(req)
    assert cleared[0]["waiting"] is False and cleared[0]["blocked_by"] == []
    assert "never-emitted" not in json.dumps(cleared)
    binding.delete()
    report.disabled = True
    report.save(update_fields=["disabled"])
    assert whole(req)[0]["waiting"] is False
    report.disabled = False
    report.config = {}
    report.save(update_fields=["disabled", "config"])
    assert whole(req)[0]["blocked_by"] == []


@pytest.mark.django_db
def test_a_blocked_run_without_a_summary_reads_as_waiting(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="held", config={"data_sources": ["db"]})
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "h"})
    Run.objects.create(report=report, studio=studio, slug="held", status=Run.ERROR, finished_at=NOW,
                       stderr_tail="Waiting for data source 'db': missing: user, password")
    row = whole(request(org_admin, studio))[0]
    assert row["waiting"] is True and row["blocked_by"] == ["db"]


@pytest.mark.django_db
def test_a_built_report_names_its_failing_source(studio, org_admin):
    report = Report.objects.create(studio=studio, slug="built", config={"data_sources": ["db"]})
    RepoDataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "h", "database": "d"})
    DataSource.objects.create(studio=studio, name="db", type="postgres", config={"host": "h", "database": "d"}, credentials={"user": "u", "password": "p"},
                              last_check_at=NOW, last_check_ok=False, last_check_error="refused")
    observe(report)
    row = whole(request(org_admin, studio))[0]
    assert row["failing_source"]["name"] == "db" and row["waiting"] is False


@pytest.mark.django_db
def test_an_overlarge_source_is_never_decrypted_and_the_observed_wait_stands(studio, org_admin, monkeypatch):
    report = Report.objects.create(studio=studio, slug="held", config={"data_sources": ["db"]})
    observe(report, attempt_status="error", attempt_blocked_by=["db"])
    DataSource.objects.create(studio=studio, name="db", type="postgres", config={"padding": "x" * 70000}, credentials={"password": "secret"})
    from apps.core.crypto import EncryptedJSONField

    def forbidden(*args, **kwargs):
        raise AssertionError("An oversized binding must not decrypt")

    monkeypatch.setattr(EncryptedJSONField, "from_db_value", forbidden)
    row = whole(request(org_admin, studio))[0]
    assert row["waiting"] is True and row["blocked_by"] == ["db"]
    assert source_states_for(studio, studio.org, ["db"]) == {}


@pytest.mark.django_db
def test_more_references_than_a_row_resolves_fall_back_to_the_observed_wait(studio, org_admin):
    names = [f"db{i:02}" for i in range(40)]
    report = Report.objects.create(studio=studio, slug="many", config={"data_sources": names})
    observe(report, attempt_status="error", attempt_blocked_by=[names[0]])
    for name in names:
        RepoDataSource.objects.create(studio=studio, name=name, type="postgres", config={"host": "h"})
    row = whole(request(org_admin, studio))[0]
    assert row["blocked_by"] == [names[0]]


@pytest.mark.django_db
def test_a_page_costs_the_same_queries_in_a_small_and_a_large_studio(studio, org_admin):
    counts = {}
    for size in (10, 400):
        Run.objects.filter(studio=studio).delete()
        Report.objects.filter(studio=studio).delete()
        reports = Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"R {i}", priority=i % 3,
                                                     config={"data_sources": ["db"]}) for i in range(size)])
        ReportSourceSummary.objects.bulk_create([ReportSourceSummary(report=r, state="ready", runtime_state="ready", quality="observed",
                                                                     observed_at=NOW, attempt_at=NOW, attempt_status="success") for r in reports])
        Run.objects.bulk_create([Run(report=r, studio=studio, slug=r.slug, status=Run.SUCCESS, created_at=NOW - timedelta(minutes=i + 1))
                                 for r in reports for i in range(3)])
        for label, call in (("paged", lambda: registry_page_payload(request(org_admin, studio), {"page": "1"})),
                            ("ops", lambda: operations.operations_page_payload(request_for(org_admin, studio), {"page_size": "50"}))):
            with CaptureQueriesContext(connection) as queries:
                payload = call()
            assert len(payload["reports" if label == "paged" else "rows"]) == min(size, ROWS_PER_PAGE)
            counts.setdefault(label, []).append(len(queries))
    assert counts["paged"][0] == counts["paged"][1] and counts["ops"][0] == counts["ops"][1]


@pytest.mark.django_db
def test_no_request_path_reads_storage(studio, org_admin, monkeypatch):
    from apps.core import storage

    def forbidden(*args, **kwargs):
        raise AssertionError("Registry rows are database facts")

    for name in ("read_meta", "html_index", "live_query_index", "summary_output_facts", "read_summary_artifact"):
        monkeypatch.setattr(storage, name, forbidden)
    Report.objects.create(studio=studio, slug="r")
    assert whole(request(org_admin, studio))
    assert registry_page_payload(request(org_admin, studio), {"page": "1"})["reports"]
    assert operations.operations_page_payload(request_for(org_admin, studio), {})["rows"]


@pytest.mark.django_db
def test_download_is_offered_from_stored_metadata_only(studio, org_admin, monkeypatch):
    from apps.datasources import materialize

    def forbidden(*args, **kwargs):
        raise AssertionError("Source rows must not touch the file system")

    monkeypatch.setattr(materialize, "stored_file", forbidden)
    monkeypatch.setattr(materialize, "resolve_path", forbidden)
    for name in ("uploaded", "waiting", "plain"):
        RepoDataSource.objects.create(studio=studio, name=name, type="file", config={"upload": True, "path": f"{name}.csv"} if name != "plain" else {"path": "p.csv"})
    DataSource.objects.create(studio=studio, name="uploaded", type="file", config={"upload": True, "path": "files/uploaded.csv"})
    DataSource.objects.create(studio=studio, name="waiting", type="file", config={"upload": True})
    for name in ("uploaded", "waiting", "plain"):
        Report.objects.create(studio=studio, slug=f"use-{name}", config={"data_sources": [name]})
    rows = {row["name"]: row for row in operations.operations_sources_payload(request_for(org_admin, studio), {})["rows"]}
    assert {name: row["download_available"] for name, row in rows.items()} == {"uploaded": True, "waiting": False, "plain": False}
    legacy = {row["name"]: row["file"] for row in source_states_payload(studio)}
    assert legacy == {"uploaded": True, "waiting": False, "plain": False}


@pytest.mark.django_db
def test_the_whole_registry_is_read_in_a_few_large_slices(studio, org_admin):
    Report.objects.bulk_create([Report(studio=studio, slug=f"r-{i:04}", name=f"R {i}", priority=i % 5) for i in range(450)])
    req = request(org_admin, studio)
    with CaptureQueriesContext(connection) as queries:
        everything = whole(req)
    assert len(everything) == 450
    assert everything == paged(req)
    assert [r["slug"] for r in everything] == [r.slug for r in Report.objects.filter(studio=studio).order_by("priority", "slug")]
    # Three slices, each a handful of queries: never one per report.
    assert len(queries) <= 3 * 10
