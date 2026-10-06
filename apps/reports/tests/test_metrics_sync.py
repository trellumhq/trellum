"""MetricDefinition sync: metrics.yaml -> DB rows, in the same pass as the
report registry scan (semantic-layer Phase 2).

See apps.reports.scan.sync_studio_metrics for the contract: absent or
malformed metrics.yaml degrades to "no metrics defined" rather than raising
(trellum.metrics.load_metrics_result already treats it that way), and a
metric that disappears from the file is flagged present_in_scan=False, not
deleted -- mirroring Report/report.yaml exactly.
"""
import pytest

from apps.reports.models import MetricDefinition
from apps.reports.scan import sync_studio_metrics, sync_studio_registry

pytestmark = pytest.mark.django_db


GROSS_REVENUE = {
    "name": "gross_revenue",
    "label": "Gross Revenue",
    "description": "IAP plus ad revenue, gross of platform fees.",
    "owner": "finance@example.com",
    "format": "currency",
    "version": 2,
    "agg": "sum",
    "column": "total_revenue",
    "sql": "iap_revenue + ad_revenue",
    "dimensions": ["event_date", "title"],
    "tags": ["revenue"],
}

PAYER_SHARE = {
    "name": "payer_share",
    "label": "Payer Share",
    "description": "Share of daily actives who purchased.",
    "format": "percent",
    "agg": "ratio",
    "numerator": "payers",
    "denominator": "dau",
}


class TestSync:
    def test_sync_upserts_metric_rows(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([GROSS_REVENUE, PAYER_SHARE])
        n = sync_studio_metrics(studio_tree)
        assert n == 2

        row = MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue")
        assert row.label == "Gross Revenue"
        assert row.description.startswith("IAP plus ad revenue")
        assert row.owner == "finance@example.com"
        assert row.format == "currency"
        assert row.version == 2
        assert row.agg == "sum"
        assert row.column == "total_revenue"
        assert row.sql == "iap_revenue + ad_revenue"
        assert row.dimensions == ["event_date", "title"]
        assert row.tags == ["revenue"]
        assert row.definition_hash
        assert row.present_in_scan is True

        ratio = MetricDefinition.objects.get(studio=studio_tree, name="payer_share")
        assert ratio.agg == "ratio"
        assert ratio.numerator == "payers"
        assert ratio.denominator == "dau"
        assert ratio.column == ""

    def test_spec_text_matches_the_framework(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([GROSS_REVENUE, PAYER_SHARE])
        sync_studio_metrics(studio_tree)
        gross = MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue")
        ratio = MetricDefinition.objects.get(studio=studio_tree, name="payer_share")
        assert gross.spec_text() == "sum(total_revenue)"
        assert ratio.spec_text() == "ratio(payers / dau)"

    def test_descriptive_metric_has_no_agg(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([{"name": "sessions", "label": "Sessions", "format": "number"}])
        sync_studio_metrics(studio_tree)
        row = MetricDefinition.objects.get(studio=studio_tree, name="sessions")
        assert row.agg == ""
        assert row.spec_text() == "(descriptive)"

    def test_vanished_metric_flagged_not_deleted(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_metrics_yaml([])
        sync_studio_metrics(studio_tree)

        row = MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue")
        assert row.present_in_scan is False
        # Re-adding it flips the flag back rather than duplicating the row.
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        assert MetricDefinition.objects.filter(studio=studio_tree, name="gross_revenue").count() == 1
        assert MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue").present_in_scan is True

    def test_rescan_updates_fields(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([{**GROSS_REVENUE, "version": 1}])
        sync_studio_metrics(studio_tree)
        first_hash = MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue").definition_hash

        write_metrics_yaml([{**GROSS_REVENUE, "version": 2}])
        sync_studio_metrics(studio_tree)
        row = MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue")
        assert row.version == 2
        assert row.definition_hash != first_hash

    def test_no_metrics_yaml_is_zero_not_an_error(self, studio_tree):
        assert sync_studio_metrics(studio_tree) == 0
        assert MetricDefinition.objects.filter(studio=studio_tree).count() == 0

    def test_malformed_metrics_yaml_is_zero_not_an_error(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml(text="not: [valid, yaml: at all")
        assert sync_studio_metrics(studio_tree) == 0

    def test_duplicate_id_keeps_the_first_only(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml(text=(
            "version: 1\n"
            "metrics:\n"
            "  - name: gross_revenue\n"
            "    label: First\n"
            "    format: currency\n"
            "  - name: gross_revenue\n"
            "    label: Second\n"
            "    format: number\n"
        ))
        n = sync_studio_metrics(studio_tree)
        assert n == 1
        assert MetricDefinition.objects.get(studio=studio_tree, name="gross_revenue").label == "First"

    def test_studios_do_not_leak_into_each_other(self, studio_tree, studio2, data_dir, write_metrics_yaml):
        studio2.ensure_dirs()
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        sync_studio_metrics(studio2)
        assert MetricDefinition.objects.filter(studio=studio2).count() == 0
        assert MetricDefinition.objects.filter(studio=studio_tree).count() == 1


class TestSyncedFromRegistryPass:
    """The design's whole premise: 'synced in the same pass that scans
    report.yaml'. sync_studio_registry alone must sync metrics too."""

    def test_registry_sync_also_syncs_metrics(self, studio_tree, write_report, write_metrics_yaml):
        write_report("alpha")
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_registry(studio_tree)
        assert MetricDefinition.objects.filter(studio=studio_tree, name="gross_revenue").exists()

    def test_a_metrics_yaml_problem_never_breaks_the_report_scan(
        self, studio_tree, write_report, write_metrics_yaml, monkeypatch
    ):
        write_report("alpha")
        write_metrics_yaml([GROSS_REVENUE])

        def _boom(_studio):
            raise RuntimeError("boom")

        import apps.reports.scan as scan_mod
        monkeypatch.setattr(scan_mod, "sync_studio_metrics", _boom)

        n = sync_studio_registry(studio_tree)
        assert n == 1  # the report still synced despite the metrics sync blowing up
