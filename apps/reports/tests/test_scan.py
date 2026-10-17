"""Registry sync + the registry rows it feeds, against a real studio tree and the
bundled framework's scan_report_configs."""
import pytest

from apps.reports.models import Report
from apps.reports.models import ReportSourceSummary
from apps.reports.registry_rows import all_rows
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db


class TestSync:
    def test_generatorless_analysis_discovered_and_removed(self, studio_tree, write_report):
        directory = write_report("article", kind="analysis")
        (directory / "generator.py").unlink()
        (directory / "content.md").write_text("# Analysis\n\nA published finding.", encoding="utf-8")
        assert sync_studio_registry(studio_tree) == 1
        row = Report.objects.get(studio=studio_tree, slug="article")
        assert row.kind == "analysis"
        (directory / "report.yaml").unlink()
        assert sync_studio_registry(studio_tree) == 0
        row.refresh_from_db()
        assert row.present_in_scan is False

    def test_scan_upserts_rows(self, studio_tree, write_report):
        write_report(
            "alpha",
            category="Revenue",
            tags=["daily"],
            schedule={"cron": "0 7 * * *", "timezone": "Europe/Amsterdam"},
            display={"priority": 3},
        )
        # Disabled reports are excluded by the framework scan itself
        # (legacy parity: they never appeared in the registry either).
        write_report("beta", disabled=True)
        n = sync_studio_registry(studio_tree)
        assert n == 1
        alpha = Report.objects.get(studio=studio_tree, slug="alpha")
        assert alpha.category == "Revenue"
        assert alpha.schedule_cron == "0 7 * * *"
        assert alpha.schedule_timezone == "Europe/Amsterdam"
        assert alpha.priority == 3
        assert alpha.tags == ["daily"]
        assert not Report.objects.filter(studio=studio_tree, slug="beta").exists()

    def test_vanished_report_flagged_not_deleted(self, studio_tree, write_report):
        import shutil

        write_report("alpha")
        sync_studio_registry(studio_tree)
        shutil.rmtree(studio_tree.reports_dir / "alpha")
        sync_studio_registry(studio_tree)
        row = Report.objects.get(studio=studio_tree, slug="alpha")
        assert row.present_in_scan is False

    def test_rescan_updates_config(self, studio_tree, write_report):
        write_report("alpha", display={"priority": 1})
        sync_studio_registry(studio_tree)
        write_report("alpha", display={"priority": 8})
        sync_studio_registry(studio_tree)
        assert Report.objects.get(studio=studio_tree, slug="alpha").priority == 8

    def test_empty_reports_dir_ok(self, studio_tree):
        assert sync_studio_registry(studio_tree) == 0


class TestRepoTheme:
    """Studio.repo_theme, stamped from <studio>/project/config.yaml's
    `theme:` key -- the file apps.runner.gitsync mirrors in from the
    studio's repo alongside events.yaml/metrics.yaml (see
    apps.runner.gitsync's own tests for the mirror itself; this covers the
    parse-and-stamp apps.reports.scan.sync_studio_repo_theme does with
    whatever is already on disk)."""

    def _write_config(self, studio_tree, text: str) -> None:
        studio_tree.project_root.mkdir(parents=True, exist_ok=True)
        (studio_tree.project_root / "config.yaml").write_text(text, encoding="utf-8")

    def test_absent_file_is_empty(self, studio_tree):
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

    def test_theme_key_is_stamped(self, studio_tree):
        self._write_config(studio_tree, "theme: nord\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == "nord"

    def test_a_custom_unregistered_name_is_stamped_verbatim(self, studio_tree):
        # Not validated here -- it may be a theme the repo registers itself
        # at build time (apps.core.themes.has_custom_repo_theme).
        self._write_config(studio_tree, "theme: a-repo-custom-theme\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == "a-repo-custom-theme"

    def test_blank_theme_key_is_empty(self, studio_tree):
        self._write_config(studio_tree, "theme: ''\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

    def test_malformed_yaml_degrades_to_empty_not_an_error(self, studio_tree):
        self._write_config(studio_tree, "theme: [unclosed\n  - broken: {{{")
        sync_studio_registry(studio_tree)  # must not raise
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

    def test_non_mapping_document_degrades_to_empty(self, studio_tree):
        self._write_config(studio_tree, "- just\n- a\n- list\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

    def test_rescan_picks_up_a_changed_theme(self, studio_tree):
        self._write_config(studio_tree, "theme: nord\n")
        sync_studio_registry(studio_tree)
        self._write_config(studio_tree, "theme: money\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == "money"

    def test_theme_removed_from_the_file_clears_the_stamp(self, studio_tree):
        self._write_config(studio_tree, "theme: nord\n")
        sync_studio_registry(studio_tree)
        self._write_config(studio_tree, "extensions:\n  nav_html: ''\n")
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

    def test_a_sibling_extensions_block_is_ignored_not_an_error(self, studio_tree):
        # The portal never writes `extensions:` into this file itself
        # (FW_EXTENSIONS_JSON outranks it at build time -- trellum.project
        # .load_extensions), but a repo might declare one alongside its own
        # theme; the scan must not choke on or care about it.
        self._write_config(
            studio_tree, "theme: nord\nextensions:\n  nav_html: '<a>x</a>'\n"
        )
        sync_studio_registry(studio_tree)
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == "nord"


def registry_rows(studio):
    from django.utils import timezone

    return all_rows(Report.objects.filter(studio=studio, present_in_scan=True), studio, studio.org, now=timezone.now())


class TestPayload:
    def test_row_shape_with_summary_merge(self, studio_tree, write_report, observe_meta):
        write_report("alpha", description="Alpha report", display={"priority": 1})
        sync_studio_registry(studio_tree)
        observe_meta(
            "alpha",
            observed=True,
            last_run="2026-08-01T07:00:00+00:00",
            last_status="success",
            framework_version="0.4.1",
            validation={"summary": {"pass": 5, "fail": 0}},
        )

        entry = registry_rows(studio_tree)[0]
        expected_keys = {
            "id", "slug", "kind", "name", "description", "category", "studio", "tags",
            "schedule", "schedule_timezone", "disabled",
            "last_run", "last_status", "build_status", "last_error",
            "has_output", "html_entry", "validation", "details", "framework_version",
            # View analytics: views over the last 30 days and the last view.
            "views_30d", "last_viewed", "refresh_suspended_at", "stale",
            # Runs live queries at view time (and the org allows them).
            "live",
            # Data source state (apps.datasources.status): what holds the
            # report, which source a built one fails on now, when it was built.
            "blocked_by", "waiting", "failing_source", "last_built_at",
            # The generated metrics report, which the dashboard leaves to the
            # Metrics page (apps.reports.metrics_catalog).
            "metrics_report",
        }
        assert set(entry) == expected_keys
        assert entry["slug"] == "alpha"
        assert entry["studio"] == studio_tree.slug
        assert entry["last_status"] == "success"
        assert entry["build_status"] == "success"
        assert entry["last_run"] == "2026-08-01T07:00:00+00:00"
        assert entry["has_output"] is True
        assert entry["html_entry"] == "index.html"
        assert entry["validation"] == {"summary": {"pass": 5, "fail": 0}}
        assert entry["framework_version"] == "0.4.1"
        # No live queries in this build → a static report.
        assert entry["live"] is False


class TestLiveFlag:
    """`live` marks a report that runs queries at view time — the overview badge
    and Operations "Data" column read it. It is true only when the build shipped
    live queries (the summary's ``has_live_queries``) AND the org permits them."""

    def _entry(self, studio_tree, write_report, observe_meta, *, live):
        write_report("alpha")
        sync_studio_registry(studio_tree)
        observe_meta("alpha", observed=True, last_status="success")
        ReportSourceSummary.objects.filter(report__slug="alpha").update(has_live_queries=live)
        return registry_rows(studio_tree)[0]

    def test_live_when_the_build_shipped_live_queries(self, studio_tree, write_report, observe_meta):
        assert self._entry(studio_tree, write_report, observe_meta, live=True)["live"] is True

    def test_static_when_it_did_not(self, studio_tree, write_report, observe_meta):
        assert self._entry(studio_tree, write_report, observe_meta, live=False)["live"] is False

    def test_suppressed_when_org_disabled_live_queries(self, studio_tree, write_report, observe_meta):
        from apps.reports.models import OrgLiveQueryPolicy

        # A rate limit of 0 is the org's "off" switch — a live report then can
        # never execute, so it must not advertise itself as live.
        OrgLiveQueryPolicy.objects.update_or_create(
            org=studio_tree.org, defaults={"rate_limit_per_minute": 0}
        )
        assert self._entry(studio_tree, write_report, observe_meta, live=True)["live"] is False

    def test_not_run_defaults(self, studio_tree, write_report):
        write_report("alpha")
        sync_studio_registry(studio_tree)
        entry = registry_rows(studio_tree)[0]
        assert entry["last_status"] == "not_run"
        assert entry["has_output"] is None  # nothing observed yet: not claimed either way

    def test_priority_sort(self, studio_tree, write_report):
        """display.priority: lower first; a report declaring none is 99, last."""
        write_report("alpha")
        write_report("low", display={"priority": 9})
        write_report("high", display={"priority": 1})
        sync_studio_registry(studio_tree)
        assert Report.objects.get(studio=studio_tree, slug="alpha").priority == 99
        slugs = [r["slug"] for r in registry_rows(studio_tree)]
        assert slugs == ["high", "low", "alpha"]

    def test_a_synced_report_is_listed_at_once(self, studio_tree, write_report):
        write_report("alpha")
        sync_studio_registry(studio_tree)
        assert len(registry_rows(studio_tree)) == 1
        write_report("beta")
        sync_studio_registry(studio_tree)
        assert len(registry_rows(studio_tree)) == 2
