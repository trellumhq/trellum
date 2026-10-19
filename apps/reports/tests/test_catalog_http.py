"""The dashboard reads the registry in bounded slices; API clients read the same rows whole."""
import pytest

from apps.core.pagination import page_metadata
from apps.reports.models import Report, ReportFavorite, ReportSourceSummary

pytestmark = pytest.mark.django_db


def test_registry_slices_page_through_the_catalog(login, studio, org_admin):
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 4}", description="é & <needle> " * 60)
        for index in range(56)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    first = client.get(base + "/api/registry?kind=report&page=1").json()
    second = client.get(base + "/api/registry?kind=report&page=2").json()
    assert first["pagination"] == page_metadata(1, 50, 56)
    assert second["pagination"]["has_next"] is False
    slugs = [row["slug"] for row in first["reports"] + second["reports"]]
    assert slugs == sorted(slugs) and len(set(slugs)) == 56
    # The registry's row shape, so the dashboard renders it unchanged.
    row = first["reports"][0]
    for key in ("id", "kind", "slug", "name", "description", "category", "studio", "tags", "schedule",
                "last_run", "last_status", "build_status", "last_error", "blocked_by", "waiting", "failing_source",
                "last_built_at", "has_output", "html_entry", "validation", "details", "framework_version",
                "views_30d", "last_viewed", "refresh_suspended_at", "stale", "live", "metrics_report"):
        assert key in row, key
    # Nothing is clipped: the whole description, as the search sees it.
    assert len(row["description"]) == len("é & <needle> " * 60)
    assert client.get(base + "/api/registry?kind=report&page=3&limit=25").json()["pagination"]["total"] == 56


def test_registry_slices_filter_search_favorites_and_kind(login, studio, org_admin):
    reports = Report.objects.bulk_create(
        [Report(studio=studio, slug=f"report-{i:03}", name=f"Report {i:03}") for i in range(12)]
        + [Report(studio=studio, slug="needle", name="Haystack needle")]
        + [Report(studio=studio, slug="an", name="Analysis", kind=Report.KIND_ANALYSIS)]
    )
    ReportFavorite.objects.bulk_create([ReportFavorite(user=org_admin, report=r) for r in reports[:3]])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}/api/registry?kind=report&page=1"
    assert [r["slug"] for r in client.get(base + "&q=NEEDLE").json()["reports"]] == ["needle"]
    assert len(client.get(base + "&tab=_favorites").json()["reports"]) == 3
    assert [r["slug"] for r in client.get(base + "&tab=_recent&recent=report-005&recent=needle").json()["reports"]] == ["needle", "report-005"]
    assert [r["slug"] for r in client.get(base.replace("report", "analysis", 1)).json()["reports"]] == ["an"]


@pytest.mark.parametrize("query", ["kind=other&page=1", "kind=other", "kind=report&page=0", "kind=report&page=x",
                                   "kind=report&page=1&limit=4", "kind=report&page=1&limit=51",
                                   "kind=report&page=1&tab=Revenue"])
def test_registry_slice_rejects_invalid_state(login, studio, org_admin, query):
    response = login(org_admin).get(f"/s/{studio.org.slug}/{studio.slug}/api/registry?{query}")
    assert response.status_code == 400


def test_registry_without_a_page_is_still_the_whole_registry(login, studio, org_admin):
    Report.objects.create(studio=studio, slug="whole", name="Whole")
    payload = login(org_admin).get(f"/s/{studio.org.slug}/{studio.slug}/api/registry").json()
    assert "pagination" not in payload and "source_states" in payload and payload["generated_at"]
    assert [r["slug"] for r in payload["reports"]] == ["whole"]


def test_registry_without_a_kind_lists_both_kinds_with_or_without_a_page(login, studio, org_admin):
    Report.objects.create(studio=studio, slug="a-report")
    Report.objects.create(studio=studio, slug="an-analysis", kind=Report.KIND_ANALYSIS)
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}/api/registry"
    assert sorted(r["slug"] for r in client.get(base).json()["reports"]) == ["a-report", "an-analysis"]
    assert sorted(r["slug"] for r in client.get(base + "?page=1").json()["reports"]) == ["a-report", "an-analysis"]
    assert [r["slug"] for r in client.get(base + "?kind=analysis").json()["reports"]] == ["an-analysis"]


def test_registry_slices_never_show_a_report_outside_the_viewers_scope(login, studio, member, make_group, attach_group):
    Report.objects.bulk_create([Report(studio=studio, slug=f"hidden-{i}", name=f"Hidden {i}") for i in range(3)])
    payload = login(member).get(f"/s/{studio.org.slug}/{studio.slug}/api/registry?kind=report&page=1")
    assert payload.status_code in (200, 403, 404)
    if payload.status_code == 200:
        assert not [r for r in payload.json()["reports"] if r["slug"].startswith("hidden-")]


def test_large_analysis_catalog_is_complete_in_slices(login, studio, org_admin):
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"analysis-{index:03}", kind=Report.KIND_ANALYSIS,
               name=f"Analysis {index:03}") for index in range(67)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    seen = []
    for page in (1, 2):
        payload = client.get(base + f"/api/registry?kind=analysis&page={page}").json()
        assert payload["pagination"]["total"] == 67
        seen += [row["id"] for row in payload["reports"]]
    assert len(seen) == len(set(seen)) == 67
    assert [row["id"] for row in client.get(base + "/api/registry?kind=analysis").json()["reports"]] == seen


def test_removed_catalog_route_is_gone(login, studio, org_admin):
    # One tripwire for the retired api/catalog* family.
    assert login(org_admin).get(f"/s/{studio.org.slug}/{studio.slug}/api/catalog").status_code == 404


class TestRowsBeforeSummaries:
    """Until a worker has reconciled a report there is no summary to read; the
    rows then show what Operations shows, from the Run rows, and the paged and
    whole registries agree."""

    @pytest.fixture
    def built(self, studio_tree, report_row):
        from django.utils import timezone

        from apps.runner.models import Run

        finished = timezone.now() - timezone.timedelta(minutes=17)
        Run.objects.create(
            studio=studio_tree, report=report_row, slug=report_row.slug, status=Run.SUCCESS,
            created_at=finished - timezone.timedelta(seconds=40), started_at=finished - timezone.timedelta(seconds=39),
            finished_at=finished,
        )
        return report_row

    @staticmethod
    def rows(client, studio):
        base = f"/s/{studio.org.slug}/{studio.slug}/api/registry"
        paged = client.get(base + "?kind=report&page=1").json()["reports"][0]
        whole = client.get(base).json()["reports"][0]
        return paged, whole

    @pytest.mark.parametrize("summary", ["missing", "pending"])
    def test_paged_registry_matches_the_whole_registry(self, login, studio_tree, org_admin, built, summary):
        # The registry sync leaves a pending row; a studio from before the
        # summaries has none at all.
        if summary == "missing":
            ReportSourceSummary.objects.filter(report=built).delete()
        else:
            assert ReportSourceSummary.objects.get(report=built).state == "pending"
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_status"] == "success" and paged["build_status"] == "success" and paged["last_run"]

    def test_a_failed_build_reads_as_an_error(self, login, studio_tree, org_admin, built):
        from django.utils import timezone

        from apps.runner.models import Run

        failed = timezone.now() - timezone.timedelta(minutes=2)
        Run.objects.create(studio=studio_tree, report=built, slug=built.slug, status=Run.OOM_KILLED,
                           created_at=failed - timezone.timedelta(seconds=9), finished_at=failed)
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_status"] == "error" and paged["build_status"] == "oom_killed"
        assert paged["last_run"] == failed.isoformat()

    def test_a_build_in_progress_does_not_replace_the_last_outcome(self, login, studio_tree, org_admin, built):
        from apps.runner.models import Run

        Run.objects.create(studio=studio_tree, report=built, slug=built.slug, status=Run.RUNNING)
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_status"] == "success" and paged["build_status"] == "running"

    def test_a_report_that_never_ran_is_still_not_run(self, login, studio_tree, org_admin, report_row):
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_status"] == "not_run" and paged["last_run"] is None

    def test_a_summary_older_than_the_newest_run_yields_to_it(self, login, studio_tree, org_admin, built):
        from django.utils import timezone

        seen = timezone.now() - timezone.timedelta(hours=2)
        ReportSourceSummary.objects.update_or_create(report=built, defaults={
            "state": "ready", "runtime_state": "ready", "quality": "observed",
            "observed_at": seen, "attempt_at": seen, "attempt_status": "error",
        })
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_status"] == "success"

    def test_a_summary_that_saw_the_run_supplies_the_time_and_the_error_text(self, login, studio_tree, org_admin, report_row):
        from django.utils import timezone

        from apps.runner.models import Run

        started = timezone.now() - timezone.timedelta(minutes=5)
        Run.objects.create(studio=studio_tree, report=report_row, slug=report_row.slug, status=Run.ERROR, created_at=started,
                           started_at=started, finished_at=started + timezone.timedelta(seconds=30), stderr_tail="Traceback ...")
        saw = started + timezone.timedelta(seconds=29)
        ReportSourceSummary.objects.update_or_create(report=report_row, defaults={
            "state": "ready", "runtime_state": "ready", "quality": "observed", "observed_at": saw,
            "attempt_at": saw, "attempt_status": "error", "attempt_error": "KeyError: revenue",
        })
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged == whole
        assert paged["last_run"] == saw.isoformat() and paged["last_error"] == "KeyError: revenue"
