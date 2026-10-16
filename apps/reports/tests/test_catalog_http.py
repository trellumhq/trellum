"""The dashboard reads the registry in bounded slices; the JSON catalog stays bounded."""
import pytest

from apps.reports.models import Report, ReportFavorite, ReportSourceSummary

pytestmark = pytest.mark.django_db


def test_registry_slices_page_through_the_catalog_without_the_full_registry(login, studio, org_admin, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("A dashboard slice must not enumerate the legacy registry")

    monkeypatch.setattr("apps.reports.views.build_registry_payload", forbidden)
    Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 4}", description="é & <needle> " * 60)
        for index in range(56)
    ])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    first = client.get(base + "/api/registry?kind=report&page=1").json()
    second = client.get(base + "/api/registry?kind=report&page=2").json()
    assert first["pagination"] == {"page": 1, "per_page": 50, "total": 56, "has_next": True}
    assert second["pagination"]["has_next"] is False
    slugs = [row["slug"] for row in first["reports"] + second["reports"]]
    assert slugs == sorted(slugs) and len(set(slugs)) == 56
    # The registry's row shape, so the dashboard renders it unchanged.
    row = first["reports"][0]
    for key in ("id", "kind", "slug", "name", "description", "category", "studio", "tags", "schedule",
                "last_run", "last_status", "last_error", "blocked_by", "waiting", "failing_source",
                "has_output", "html_entry", "views_30d", "last_viewed", "stale", "live"):
        assert key in row, key
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


@pytest.mark.parametrize("query", ["kind=other&page=1", "kind=report&page=0", "kind=report&page=x",
                                   "kind=report&page=1&limit=4", "kind=report&page=1&limit=51",
                                   "kind=report&page=1&tab=Revenue"])
def test_registry_slice_rejects_invalid_state(login, studio, org_admin, query):
    response = login(org_admin).get(f"/s/{studio.org.slug}/{studio.slug}/api/registry?{query}")
    assert response.status_code == 400


def test_registry_without_a_page_is_still_the_whole_registry(login, studio, org_admin):
    Report.objects.create(studio=studio, slug="whole", name="Whole")
    payload = login(org_admin).get(f"/s/{studio.org.slug}/{studio.slug}/api/registry").json()
    assert "pagination" not in payload and "source_states" in payload
    assert [r["slug"] for r in payload["reports"]] == ["whole"]


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
    assert client.get(base + "/api/catalog?kind=analysis&page=3").json()["pagination"]["total"] == 67


def test_json_catalog_pages_stay_bounded(login, studio, org_admin):
    reports = Report.objects.bulk_create([
        Report(studio=studio, slug=f"report-{index:03}", name=f"Report {index:03}",
               category=f"Category {index % 4}") for index in range(56)
    ])
    ReportFavorite.objects.bulk_create([ReportFavorite(user=org_admin, report=r) for r in reports[:30]])
    client = login(org_admin)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    payload = client.get(base + "/api/catalog?view=list&page=2").json()
    assert payload["pagination"]["total"] == 56 and len(payload["rows"]) == 25
    assert client.get(base + "/api/catalog/personal?favorite_page=2").json()["pagination"]["total"] == 30
    assert client.get(base + "/api/catalog?tab=_favorites&page=2").json()["pagination"]["total"] == 30
    assert client.get(base + "/api/catalog/categories").json()["pagination"]["total"] == 4


def test_detail_endpoint_is_authorized_and_bad_input_is_rejected(login, studio, org_admin):
    report = Report.objects.create(studio=studio, slug="detail", description="Long text " * 200, tags=["tag"] * 40)
    base = f"/s/{studio.org.slug}/{studio.slug}"
    owner = login(org_admin)
    assert owner.get(base + f"/api/catalog/details/{report.pk}?metadata=0").json()["report_id"] == report.pk
    assert owner.get(base + "/api/catalog/indicators?id=not-an-id").status_code == 400
    assert owner.get(base + "/api/catalog?kind=invalid").status_code == 400


class TestCardsBeforeSummaries:
    """Until a worker has reconciled a report there is no summary to read; the
    cards then show what Operations shows, from the Run rows, and agree with
    the whole registry."""

    @pytest.fixture
    def built(self, studio_tree, report_row, write_meta):
        from django.utils import timezone

        from apps.reports.scan import invalidate_registry_cache
        from apps.runner.models import Run

        finished = timezone.now() - timezone.timedelta(minutes=17)
        write_meta(report_row.slug, last_status="success", last_run=finished.isoformat())
        Run.objects.create(
            studio=studio_tree, report=report_row, slug=report_row.slug, status=Run.SUCCESS,
            created_at=finished - timezone.timedelta(seconds=40), finished_at=finished,
        )
        invalidate_registry_cache(studio_tree)
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
        assert paged["last_status"] == whole["last_status"] == "success"
        assert paged["last_run"] == whole["last_run"]
        assert paged["build_status"] == whole["build_status"] == "success"

    def test_a_failed_build_reads_as_an_error(self, login, studio_tree, org_admin, built, write_meta):
        from django.utils import timezone

        from apps.reports.scan import invalidate_registry_cache
        from apps.runner.models import Run

        failed = timezone.now() - timezone.timedelta(minutes=2)
        write_meta(built.slug, last_status="error", last_run=failed.isoformat())
        Run.objects.create(studio=studio_tree, report=built, slug=built.slug, status=Run.OOM_KILLED,
                           created_at=failed - timezone.timedelta(seconds=9), finished_at=failed)
        invalidate_registry_cache(studio_tree)
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged["last_status"] == whole["last_status"] == "error"
        assert paged["last_run"] == whole["last_run"]

    def test_a_build_in_progress_does_not_replace_the_last_outcome(self, login, studio_tree, org_admin, built):
        from apps.runner.models import Run

        Run.objects.create(studio=studio_tree, report=built, slug=built.slug, status=Run.RUNNING)
        paged, whole = self.rows(login(org_admin), studio_tree)
        assert paged["last_status"] == whole["last_status"] == "success"
        assert paged["last_run"] == whole["last_run"]
        assert paged["build_status"] == whole["build_status"] == "running"

    def test_a_report_that_never_ran_is_still_not_run(self, login, studio_tree, org_admin, report_row):
        paged, _ = self.rows(login(org_admin), studio_tree)
        assert paged["last_status"] == "not_run" and paged["last_run"] is None

    def test_a_known_summary_is_not_overridden_by_runs(self, login, studio_tree, org_admin, built):
        from django.utils import timezone

        seen = timezone.now() - timezone.timedelta(hours=2)
        ReportSourceSummary.objects.update_or_create(report=built, defaults={
            "state": "ready", "runtime_state": "ready", "quality": "observed",
            "observed_at": seen, "attempt_at": seen, "attempt_status": "error",
        })
        paged, _ = self.rows(login(org_admin), studio_tree)
        assert paged["last_status"] == "error"
        assert paged["last_run"] == seen.isoformat()
