"""Quota limits that are *enforced*, not merely stored.

These fields were editable in the operator console before they did anything,
which is worse than not having them: an operator sets "max 5 studios", sees it
saved, and the org creates fifty.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.orgs import quotas
from apps.orgs.models import OrgQuota
from apps.reports.models import Report
from apps.reports.scan import ReportQuotaExceeded, sync_studio_registry
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _enforce(settings):
    settings.TRELLUM_QUOTAS_ENABLED = True


class TestStoredQuotaPolicy:
    def test_limits_read_every_operator_configured_value(self, org):
        OrgQuota.objects.create(
            org=org,
            max_concurrent_runs=2,
            monthly_build_minutes=300,
            max_studios=4,
            max_reports=40,
            max_memory_mb=2048,
            max_storage_mb=8192,
        )

        assert quotas.limits_for(org) == {
            "max_concurrent_runs": 2,
            "monthly_build_minutes": 300,
            "max_studios": 4,
            "max_reports": 40,
            "max_memory_mb": 2048,
            "max_storage_mb": 8192,
        }

    def test_concurrency_caps_are_filtered_in_one_query(self, org, other_org):
        third = type(org).objects.create(slug="third", name="Third")
        OrgQuota.objects.create(org=org, max_concurrent_runs=2)
        OrgQuota.objects.create(org=other_org, max_concurrent_runs=0)
        OrgQuota.objects.create(org=third, max_concurrent_runs=9)

        with CaptureQueriesContext(connection) as queries:
            caps = quotas.concurrency_caps([org.pk, other_org.pk])

        assert caps == {org.pk: 2}
        assert len(queries) == 1


class TestStudioCap:
    def test_creation_is_refused_at_the_cap(self, login, org_admin, org, studio):
        OrgQuota.objects.create(org=org, max_studios=1)  # `studio` already exists
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/studios/new",
            {"slug": "second", "name": "Second"},
            follow=True,
        )
        assert not Studio.objects.filter(org=org, slug="second").exists()
        assert "limited to 1 studio" in resp.content.decode()

    def test_creation_succeeds_below_the_cap(self, login, org_admin, org, studio):
        OrgQuota.objects.create(org=org, max_studios=5)
        login(org_admin).post(
            f"/orgs/{org.slug}/studios/new", {"slug": "second", "name": "Second"}
        )
        assert Studio.objects.filter(org=org, slug="second").exists()

    def test_zero_means_unlimited(self, login, org_admin, org, studio):
        OrgQuota.objects.create(org=org, max_studios=0)
        login(org_admin).post(
            f"/orgs/{org.slug}/studios/new", {"slug": "second", "name": "Second"}
        )
        assert Studio.objects.filter(org=org, slug="second").exists()

    def test_not_enforced_when_operator_policy_is_off(
        self, login, org_admin, org, studio, settings
    ):
        settings.TRELLUM_QUOTAS_ENABLED = False
        OrgQuota.objects.create(org=org, max_studios=1)
        login(org_admin).post(
            f"/orgs/{org.slug}/studios/new", {"slug": "second", "name": "Second"}
        )
        assert Studio.objects.filter(org=org, slug="second").exists()


class TestReportCap:
    """Reports arrive by git push, so the scan keeps what fits and reports the
    overage instead of failing the whole sync."""

    def test_reports_that_fit_are_kept_and_the_overage_is_reported(
        self, studio_tree, write_report, org
    ):
        OrgQuota.objects.create(org=org, max_reports=2)
        for slug in ("alpha", "bravo", "charlie", "delta"):
            write_report(slug)

        with pytest.raises(ReportQuotaExceeded) as exc:
            sync_studio_registry(studio_tree)

        assert "limited to 2 reports" in str(exc.value)
        live = set(
            Report.objects.filter(studio=studio_tree, present_in_scan=True).values_list(
                "slug", flat=True
            )
        )
        assert len(live) == 2

    def test_existing_reports_keep_their_place(self, studio_tree, write_report, org):
        """A newly pushed report must not evict one that already worked."""
        write_report("alpha")
        write_report("bravo")
        sync_studio_registry(studio_tree)
        OrgQuota.objects.create(org=org, max_reports=2)

        write_report("aaa-newcomer")  # sorts first alphabetically
        with pytest.raises(ReportQuotaExceeded):
            sync_studio_registry(studio_tree)

        live = set(
            Report.objects.filter(studio=studio_tree, present_in_scan=True).values_list(
                "slug", flat=True
            )
        )
        assert live == {"alpha", "bravo"}

    def test_under_the_cap_nothing_is_raised(self, studio_tree, write_report, org):
        OrgQuota.objects.create(org=org, max_reports=10)
        write_report("alpha")
        assert sync_studio_registry(studio_tree) == 1

    def test_other_studios_in_the_org_spend_the_same_budget(
        self, studio_tree, studio2, write_report, org
    ):
        OrgQuota.objects.create(org=org, max_reports=2)
        write_report("alpha")
        sync_studio_registry(studio_tree)

        # studio2 gets what is left of the org-wide budget, not a fresh two.
        studio2.ensure_dirs()
        import yaml

        for slug in ("beta", "gamma"):
            d = studio2.reports_dir / slug
            d.mkdir(parents=True, exist_ok=True)
            (d / "report.yaml").write_text(
                yaml.safe_dump({"slug": slug, "name": slug}), encoding="utf-8"
            )
            (d / "generator.py").write_text("# stub\n", encoding="utf-8")

        with pytest.raises(ReportQuotaExceeded):
            sync_studio_registry(studio2)
        assert Report.objects.filter(studio__org=org, present_in_scan=True).count() == 2

    def test_git_sync_surfaces_the_overage_on_the_repo_row(
        self, studio_tree, write_report, org, monkeypatch
    ):
        """The org admin looks at the repo page when a sync misbehaves."""
        from apps.studios.models import StudioRepo

        repo = StudioRepo.objects.create(
            studio=studio_tree, repo_url="https://example.com/x.git"
        )
        OrgQuota.objects.create(org=org, max_reports=1)
        write_report("alpha")
        write_report("bravo")

        from apps.runner import gitsync

        sync = gitsync.StudioGitSync(repo)
        pending = {"from": None, "to": "abc123", "initial": True, "reports": {}, "root_files": []}
        monkeypatch.setattr(sync, "fetch", lambda: pending)
        monkeypatch.setattr(sync, "_run_git", lambda *args, **kwargs: (0, "", ""))
        monkeypatch.setattr(sync, "_copy_reports", lambda only_slugs=None: None)

        result = sync.sync()

        repo.refresh_from_db()
        assert result["ok"] is True  # the publish itself worked
        assert "limited to 1 report" in repo.last_error


class TestMemoryCap:
    def test_a_build_above_the_org_limit_is_refused(self, org):
        OrgQuota.objects.create(org=org, max_memory_mb=2048)
        decision = quotas.check_run_allowed(org, memory_mb=4096)
        assert decision.allowed is False and decision.fatal is True

    def test_at_the_limit_is_allowed(self, org):
        OrgQuota.objects.create(org=org, max_memory_mb=2048)
        assert quotas.check_run_allowed(org, memory_mb=2048).allowed is True


class TestStorageCap:
    """Uploaded data-source files are the one tenant-controlled consumer of the
    data volume that nothing else bounds."""

    @pytest.fixture
    def uploaded(self, org, data_dir):
        from apps.datasources.models import DataSource

        ds = DataSource.objects.create(
            org=org, name="extract", type="file",
            config={"upload": True, "path": "files/extract.csv"},
        )
        target = org.datasources_dir / "files" / "extract.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * (3 * 1024 * 1024))
        return ds

    def test_usage_counts_uploaded_files(self, org, uploaded):
        assert quotas.storage_bytes_used(org) == 3 * 1024 * 1024

    def test_a_repo_backed_file_is_not_counted(self, org, studio_tree, data_dir):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="committed", type="file",
            config={"path": "data-sources/files/committed.csv"},
        )
        target = studio_tree.project_root / "data-sources/files/committed.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"y" * 1024)
        assert quotas.storage_bytes_used(org) == 0

    def test_upload_over_the_cap_is_refused(self, org, uploaded):
        OrgQuota.objects.create(org=org, max_storage_mb=4)
        decision = quotas.check_upload_allowed(org, 2 * 1024 * 1024)
        assert decision.allowed is False and decision.fatal is True
        assert "limited to 4 MB" in decision.reason

    def test_replacing_a_file_frees_its_bytes(self, org, uploaded):
        """At 3 of 4 MB used, swapping the 3 MB file for another 3 MB file is
        not a 6 MB request — the old bytes go away in the same operation."""
        OrgQuota.objects.create(org=org, max_storage_mb=4)
        size = 3 * 1024 * 1024
        assert quotas.check_upload_allowed(org, size).allowed is False
        assert quotas.check_upload_allowed(org, size, replacing_bytes=size).allowed is True

    def test_no_cap_means_no_limit(self, org, uploaded):
        OrgQuota.objects.create(org=org, max_storage_mb=0)
        assert quotas.check_upload_allowed(org, 500 * 1024 * 1024).allowed is True

    def test_nothing_is_enforced_when_operator_policy_is_off(self, settings, org, uploaded):
        settings.TRELLUM_QUOTAS_ENABLED = False
        OrgQuota.objects.create(org=org, max_storage_mb=1)
        assert quotas.check_upload_allowed(org, 500 * 1024 * 1024).allowed is True
