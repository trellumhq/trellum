"""The Metrics catalog: the stale-definition comparison, and the page view.

See apps.reports.metrics_catalog's module docstring for the design: current
identity/spec/description always come from the synced MetricDefinition row
(the current definition), and "current vs. stale" per claiming report comes
from comparing that row's definition_hash against what the report's own
_meta.json says it was built against -- never a report's data.json.
"""
import json
import shutil
import subprocess

import pytest
from django.utils import timezone as dj_tz

from apps.core import roles
from apps.reports.metrics_catalog import (
    invalidate_metrics_overview_cache,
    studio_metrics_overview,
)
from apps.reports.scan import (
    build_registry_payload,
    sync_studio_metrics,
    sync_studio_registry,
)

pytestmark = pytest.mark.django_db

GROSS_REVENUE = {
    "name": "gross_revenue",
    "label": "Gross Revenue",
    "description": "IAP plus ad revenue.",
    "format": "currency",
    "version": 2,
    "agg": "sum",
    "column": "total_revenue",
}


@pytest.fixture(autouse=True)
def _no_overview_cache():
    """The overview has its own short TTL cache -- irrelevant to a fast test
    suite that writes then immediately reads, but a shared module-level dict
    must not leak state between tests either way."""
    invalidate_metrics_overview_cache()
    yield
    invalidate_metrics_overview_cache()


def _now_iso():
    return dj_tz.now().isoformat()


def _indexed_summary(client, studio):
    from apps.reports.models import MetricCatalogState
    from apps.reports.source_summary import reconcile
    for report in studio.reports.all():
        reconcile(report.pk)
    revision = MetricCatalogState.objects.get(studio=studio).definition_revision
    ids = ",".join(str(pk) for pk in studio.metric_definitions.values_list("pk", flat=True))
    return client.get(f"/s/{studio.org.slug}/{studio.slug}/api/metrics/summary",
                      {"ids": ids, "definition_revision": revision}).json()


class TestStaleComparison:
    def test_unclaimed_metric_has_no_usage(self, studio_tree, write_metrics_yaml):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)

        data = studio_metrics_overview(studio_tree)
        assert data["defined_count"] == 1
        assert data["claimed_count"] == 0
        assert data["metrics"][0]["usage"] == []

    def test_matching_hash_is_current(self, studio_tree, write_metrics_yaml, write_report, write_meta):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        current_hash = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash

        write_report("monetization")
        sync_studio_registry(studio_tree)
        write_meta(
            "monetization",
            last_run=_now_iso(), last_status="success",
            metrics_used=[{"id": "gross_revenue", "definition_hash": current_hash, "version": 2}],
        )

        data = studio_metrics_overview(studio_tree)
        entry = data["metrics"][0]
        assert data["claimed_count"] == 1
        assert len(entry["usage"]) == 1
        assert entry["usage"][0]["current"] is True
        assert entry["usage"][0]["report_url"] == (
            f"/s/{studio_tree.org.slug}/{studio_tree.slug}/r/monetization/"
            "?display=console"
        )
        assert entry["all_current"] is True

    def test_mismatched_hash_is_stale(self, studio_tree, write_metrics_yaml, write_report, write_meta):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)

        write_report("insert-coin")
        sync_studio_registry(studio_tree)
        write_meta(
            "insert-coin",
            last_run=_now_iso(), last_status="success",
            metrics_used=[{"id": "gross_revenue", "definition_hash": "old-hash-from-v1", "version": 1}],
        )

        data = studio_metrics_overview(studio_tree)
        entry = data["metrics"][0]
        assert entry["usage"][0]["current"] is False
        assert entry["usage"][0]["claimed_version"] == 1
        assert entry["all_current"] is False

    def test_bare_id_v1_claim_is_treated_as_stale_not_assumed_current(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """A build from before schema v2 recorded no hash at all -- 'unknown'
        must never silently read as 'current'."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)

        write_report("legacy-build")
        sync_studio_registry(studio_tree)
        write_meta("legacy-build", last_run=_now_iso(), last_status="success",
                   metrics_used=["gross_revenue"])

        data = studio_metrics_overview(studio_tree)
        entry = data["metrics"][0]
        assert entry["usage"][0]["current"] is False
        assert entry["usage"][0]["claimed_version"] is None

    def test_mixed_current_and_stale_reports_both_listed(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        current_hash = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash

        write_report("fresh")
        write_report("stale")
        sync_studio_registry(studio_tree)
        write_meta("fresh", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": current_hash, "version": 2}])
        write_meta("stale", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": "old", "version": 1}])

        data = studio_metrics_overview(studio_tree)
        entry = data["metrics"][0]
        assert len(entry["usage"]) == 2
        assert entry["all_current"] is False
        statuses = {u["report"].slug: u["current"] for u in entry["usage"]}
        assert statuses == {"fresh": True, "stale": False}

    def test_a_report_that_never_built_is_not_a_claim(self, studio_tree, write_metrics_yaml, write_report):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report("never-built")
        sync_studio_registry(studio_tree)

        data = studio_metrics_overview(studio_tree)
        assert data["metrics"][0]["usage"] == []
        assert data["claimed_count"] == 0

    def test_storage_outage_degrades_rather_than_raises(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, monkeypatch
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report("alpha")
        sync_studio_registry(studio_tree)
        write_meta("alpha", last_run=_now_iso(), last_status="success",
                   metrics_used=["gross_revenue"])

        from apps.core import storage as storage_mod

        def _boom(_studio, _slug):
            raise OSError("store unreachable")

        monkeypatch.setattr(storage_mod, "read_meta", _boom)

        data = studio_metrics_overview(studio_tree)
        assert data["storage_unavailable"] is True
        assert data["metrics"][0]["usage"] == []


class TestMetricsPageView:
    def _url(self, studio):
        return f"/s/{studio.org.slug}/{studio.slug}/metrics"

    def test_viewer_sees_the_page(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Gross Revenue" in body
        assert "sum(total_revenue)" in body

    def test_built_report_keeps_overview_on_definitions(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)
        write_meta("metrics", last_run=_now_iso(), metrics_used=["gross_revenue"])
        grant_studio(member, studio_tree, roles.VIEWER)
        body = login(member).get(self._url(studio_tree)).content.decode()
        assert 'id="metWorkspace"' not in body
        assert '<section class="met-definitions" aria-labelledby="metDefinitionsTitle">' in body
        assert 'Metric definitions and report usage' in body
        assert 'met-compare-choice' not in body
        assert 'metCompareOpen' not in body
        assert 'aria-current="page">Overview</a>' in body
        assert '/metrics/comparison' in body

    def test_comparison_section_shows_generated_workspace(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)
        write_meta("metrics", last_run=_now_iso(), metrics_used=["gross_revenue"], metrics_workspace={'version': 1})
        from apps.reports.models import Report
        from apps.reports.source_summary import reconcile
        assert reconcile(Report.objects.get(studio=studio_tree, slug='metrics').pk)['status'] == 'ready'
        grant_studio(member, studio_tree, roles.VIEWER)
        url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics/comparison"
        body = login(member).get(url).content.decode()
        assert '/r/metrics/?workspace=1' in body
        assert 'aria-current="page">Comparison</a>' in body

    def test_no_build_keeps_definitions_visible(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)
        body = login(member).get(self._url(studio_tree)).content.decode()
        assert 'id="metWorkspace"' not in body
        assert 'Metric definitions and report usage' in body
        assert '<details class="met-definitions"' not in body

    def test_metrics_catalog_is_available_to_viewers(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        assert "Gross Revenue" in resp.content.decode()

    def test_empty_state_when_no_metrics_yaml(self, studio_tree, member, grant_studio, login):
        grant_studio(member, studio_tree, roles.VIEWER)
        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 200
        assert "No metrics defined" in resp.content.decode()

    def test_unclaimed_metric_says_no_reports_claim_it(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)
        client = login(member)
        resp = client.get(self._url(studio_tree))
        assert "data-metric-usage" in resp.content.decode()
        data = _indexed_summary(client, studio_tree)
        assert data["state"] == "ready"
        assert data["metrics"][0]["claim_count"] == 0 and data["metrics"][0]["count_complete"]

    def test_non_member_gets_404(self, studio_tree, org, make_user, login):
        outsider = make_user("outsider@demo.example", org=org)
        client = login(outsider)
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 404

    def test_unauthenticated_redirects_to_login(self, studio_tree, client):
        resp = client.get(self._url(studio_tree))
        assert resp.status_code == 302
        assert "/login" in resp.url

    def test_stale_badge_renders_in_the_expand_table(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report("insert-coin")
        sync_studio_registry(studio_tree)
        write_meta("insert-coin", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": "old", "version": 1}])
        grant_studio(member, studio_tree, roles.VIEWER)

        client = login(member)
        resp = client.get(self._url(studio_tree))
        body = resp.content.decode()
        assert "data-metric-usage" in body
        data = _indexed_summary(client, studio_tree)
        claim = data["metrics"][0]["claims"][0]
        assert claim["current"] is False and claim["claimed_version"] == 1
        assert data["state"] == "partial"  # legacy local source is observed, not verified


#: What the framework's scaffold writes into reports/metrics/report.yaml --
#: the slug it hardcodes and the tag it sets. See
#: apps.reports.metrics_catalog.is_generated_metrics_report.
GENERATED_METRICS_REPORT = {"slug": "metrics", "tags": ["metrics"], "name": "Metrics"}

DESCRIPTIVE = {
    "name": "arpdau",
    "label": "ARPDAU",
    "description": "Revenue per daily active user.",
    "format": "currency",
    "sql": "iap_revenue / dau",
}


class TestCatalogOverlay:
    """Clicking a metric opens its own block from the generated metrics
    report (internal planning ticket #114). The catalog therefore has to answer, per metric:
    is there a chart, where is it framed from, and if not, why not."""

    def _generated(self, write_report, write_meta, studio, metrics_used, **meta):
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio)
        write_meta("metrics", last_run=_now_iso(), last_status="success",
                   metrics_used=metrics_used, **meta)

    def _entry(self, studio, name):
        data = studio_metrics_overview(studio)
        return data, next(m for m in data["metrics"] if m["row"].name == name)

    def test_monitored_metric_gets_an_embed_url_for_its_own_block(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        h = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
        self._generated(write_report, write_meta, studio_tree,
                        [{"id": "gross_revenue", "definition_hash": h, "version": 2}])

        data, entry = self._entry(studio_tree, "gross_revenue")
        prefix = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/r/metrics/"
        assert data["metrics_report_built"] is True
        assert data["metrics_report_url"] == prefix
        assert entry["monitored"] is True
        assert entry["embed_url"] == prefix + "?only=metric-gross_revenue"
        assert entry["chart_url"] == prefix + "?only=metric-gross_revenue"
        assert entry["overview_url"] == prefix + "#metric-gross_revenue"
        assert entry["not_monitored_reason"] == ""

    def test_descriptive_metric_says_why_it_has_no_chart(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        write_metrics_yaml([GROSS_REVENUE, DESCRIPTIVE])
        sync_studio_metrics(studio_tree)
        h = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
        self._generated(write_report, write_meta, studio_tree,
                        [{"id": "gross_revenue", "definition_hash": h, "version": 2}])

        _, entry = self._entry(studio_tree, "arpdau")
        assert entry["monitored"] is False
        assert entry["embed_url"] == "" and entry["chart_url"] == ""
        assert "descriptive" in entry["not_monitored_reason"]

    def test_executable_but_unclaimed_metric_reads_as_unbound(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """Executable and the report IS built, so its absence from the
        build's claims means it is bound to no dataset."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        self._generated(write_report, write_meta, studio_tree, [])

        data, entry = self._entry(studio_tree, "gross_revenue")
        assert data["metrics_report_built"] is True
        assert entry["monitored"] is False
        assert entry["not_monitored_reason"] == "not bound to a dataset in metrics.yaml"

    def test_unbuilt_metrics_report_is_not_reported_as_built(
        self, studio_tree, write_metrics_yaml, write_report
    ):
        """No build, no chart -- and the page must be able to say so rather
        than blame the metric's definition."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)

        data, entry = self._entry(studio_tree, "gross_revenue")
        assert data["metrics_report_built"] is False
        assert entry["monitored"] is False and entry["embed_url"] == ""

    def test_a_handwritten_report_called_metrics_is_not_the_generated_one(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """Slug AND tag, so a studio that happens to name a report "metrics"
        does not have it swallowed by the catalog."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        h = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
        write_report("metrics", name="Metrics", tags=["revenue"])
        sync_studio_registry(studio_tree)
        write_meta("metrics", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": h, "version": 2}])

        data, entry = self._entry(studio_tree, "gross_revenue")
        assert data["metrics_report_url"] == ""
        assert entry["monitored"] is False

    def test_page_renders_the_overlay_and_the_row_trigger(
        self, studio_tree, write_metrics_yaml, write_report, write_meta,
        member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        h = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
        self._generated(write_report, write_meta, studio_tree,
                        [{"id": "gross_revenue", "definition_hash": h, "version": 2}])
        grant_studio(member, studio_tree, roles.VIEWER)

        body = login(member).get(f"/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics").content.decode()
        assert 'class="met-open"' in body
        assert 'data-chart-state="loading"' in body
        data = _indexed_summary(login(member), studio_tree)
        assert data["metrics"][0]["claim_count"] == 1
        assert data["metrics"][0]["chart_state"] == "legacy"
        assert data["metrics"][0]["embed_url"] == ''
        assert 'role="dialog" aria-modal="true"' in body
        assert "metrics_overlay.js" in body


class TestGeneratedMetricsReportIsNotACard:
    """Decision 3 (internal planning ticket #114): the generated metrics report is the build
    behind this page's charts, not a report anyone wrote. The registry flags
    it so the dashboard can leave it to the Metrics page; Operations reads
    the same payload and keeps listing it."""

    def test_registry_flags_the_generated_report_only(
        self, studio_tree, write_report
    ):
        write_report(**GENERATED_METRICS_REPORT)
        write_report("player-overview", tags=["daily"])
        write_report("metrics-ish", tags=["metrics"])
        sync_studio_registry(studio_tree)

        flags = {r["slug"]: r["metrics_report"]
                 for r in build_registry_payload(studio_tree)["reports"]}
        assert flags == {"metrics": True, "player-overview": False, "metrics-ish": False}

    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_dashboard_reports_filters_the_records(self):
        """Run the actual client-side filter against records for both pages."""
        from pathlib import Path

        js = Path("static/portal.js").read_text(encoding="utf-8")
        start = js.index("function dashboardReports()")
        end = js.index("\n}\n", start) + 2
        dashboard_reports = js[start:end]
        script = f"""
const vm = require('node:vm');
const functionSource = {json.dumps(dashboard_reports)};
const reports = [
  {{ slug: 'generated-metrics', metrics_report: true, kind: 'analysis' }},
  {{ slug: 'analysis', kind: 'analysis' }},
  {{ slug: 'report', kind: 'report' }},
  {{ slug: 'legacy-report' }}
];
function filtered(isAnalysesPage) {{
  const context = {{ reports, IS_ANALYSES_PAGE: isAnalysesPage }};
  vm.createContext(context);
  vm.runInContext(functionSource + '\\ndashboardReportsResult = dashboardReports().map(r => r.slug)', context);
  return context.dashboardReportsResult;
}}
const result = {{ reports: filtered(false), analyses: filtered(true) }};
if (JSON.stringify(result) !== JSON.stringify({{
  reports: ['report', 'legacy-report'], analyses: ['analysis']
}})) process.exit(1);
"""
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr


#: The generated metrics report's own shape, minimised: a `_metrics` entry per
#: claim, the kpi component that claims it (which names the dataset), the
#: chart the report draws for it (which names the time column), and the
#: columnar DataSource the pair reads. Mirrors what
#: trellum.metrics_report.MetricsReport actually builds -- see the real one in
#: any studio's output/metrics/data.json.
def _built_data(rows, *, x="event_date", dates=("2026-01-01", "2026-01-02", "2026-01-03")):
    return {
        "components": {
            "c1": {"type": "kpi_row_live", "dataset_id": "daily",
                   "kpis": [{"metric": "gross_revenue", "agg": "sum", "column": "total_revenue"}]},
            "c2": {"type": "chart", "dataset_id": "daily", "chartType": "line", "x": x,
                   "value_cols": ["total_revenue"], "title": "Gross Revenue"},
        },
        "_metrics": {
            "gross_revenue": {"label": "Gross Revenue", "format": "currency", "version": 2,
                              "definition_hash": "h", "component_ids": ["c1"],
                              "agg": "sum", "column": "total_revenue"},
        },
        "_ds_daily": {
            "_cols": [x, "platform", "total_revenue"],
            "_data": rows,
            "_dict": {x: list(dates), "platform": ["ios", "android"]},
        },
    }


class TestSparklines:
    """Every monitored card carries the shape of its own series (internal planning ticket #114),
    read out of the ONE data.json the generated metrics report already built
    -- never recomputed, never a second query."""

    def _write_data(self, studio, payload):
        import json

        out = studio.output_dir / "metrics"
        out.mkdir(parents=True, exist_ok=True)
        (out / "data.json").write_text(json.dumps(payload), encoding="utf-8")

    def _overview(self, studio, write_metrics_yaml, write_report, write_meta, payload=None):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio)
        h = studio.metric_definitions.get(name="gross_revenue").definition_hash
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio)
        write_meta("metrics", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": h, "version": 2}])
        if payload is not None:
            self._write_data(studio, payload)
        invalidate_metrics_overview_cache()
        data = studio_metrics_overview(studio)
        return next(m for m in data["metrics"] if m["row"].name == "gross_revenue")

    def test_metric_with_data_gets_a_polyline(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """Rows are per (date, platform); the series is one point per date,
        summed across the dimensions -- exactly what the report's own chart
        plots. 10+20, 30+40, 50+60 rises, so the line falls in SVG y."""
        entry = self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[0, 0, 10.0], [0, 1, 20.0],
                         [1, 0, 30.0], [1, 1, 40.0],
                         [2, 0, 50.0], [2, 1, 60.0]]),
        )
        points = [p.split(",") for p in entry["spark"].split(" ")]
        assert len(points) == 3
        assert [float(x) for x, _ in points] == [0.0, 70.0, 140.0]
        ys = [float(y) for _, y in points]
        assert ys[0] > ys[1] > ys[2]          # rising values, falling SVG y
        assert min(ys) >= 2.0 and max(ys) <= 34.0   # inside the padded box

    def test_ratio_metric_divides_the_summed_columns_per_bucket(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """A ratio is recomputed from its own numerator and denominator per
        bucket -- never an average of per-row ratios."""
        payload = _built_data([[0, 0, 0.0]])
        payload["_metrics"]["gross_revenue"].update(
            {"agg": "ratio", "numerator": "payers", "denominator": "dau", "column": None}
        )
        payload["_ds_daily"] = {
            "_cols": ["event_date", "payers", "dau"],
            # Day 0: 30/300 = 0.10. Day 1: 90/300 = 0.30. Day 2: nothing at all.
            "_data": [[0, 10, 100], [0, 20, 200],
                      [1, 90, 300],
                      [2, 5, 0]],
            "_dict": {"event_date": ["2026-01-01", "2026-01-02", "2026-01-03"]},
        }
        entry = self._overview(studio_tree, write_metrics_yaml, write_report, write_meta, payload)

        ys = [float(p.split(",")[1]) for p in entry["spark"].split(" ")]
        # The empty denominator is dropped, not plotted as zero.
        assert len(ys) == 2
        assert ys[0] > ys[1]

    def test_flat_series_draws_through_the_middle(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        entry = self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[0, 0, 7.0], [1, 0, 7.0], [2, 0, 7.0]]),
        )
        ys = {p.split(",")[1] for p in entry["spark"].split(" ")}
        assert ys == {"18.0"}   # the 36-high box's middle, inside its padding

    def test_series_is_capped_and_keeps_the_most_recent_buckets(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(80)]
        entry = self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[i, 0, float(i)] for i in range(80)], dates=dates),
        )
        ys = [float(p.split(",")[1]) for p in entry["spark"].split(" ")]
        assert len(ys) == 60
        # The window is the tail: a strictly rising series stays strictly
        # rising, and the first point kept is 20, not 0.
        assert ys[0] > ys[-1] and ys == sorted(ys, reverse=True)

    def test_metric_with_no_series_in_the_build_has_no_sparkline(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        """Built, claimed, charted -- but the column the spec names is not in
        the dataset. No line rather than a straight one."""
        payload = _built_data([[0, 0, 1.0], [1, 0, 2.0]])
        payload["_ds_daily"]["_cols"] = ["event_date", "platform", "something_else"]
        entry = self._overview(studio_tree, write_metrics_yaml, write_report, write_meta, payload)
        assert entry["monitored"] is True
        assert entry["spark"] == ""

    def test_single_bucket_is_not_a_line(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        entry = self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[0, 0, 5.0], [0, 1, 6.0]]),
        )
        assert entry["spark"] == ""

    def test_no_built_report_means_no_sparkline_and_no_crash(
        self, studio_tree, write_metrics_yaml, write_report
    ):
        """The page has to render before anything has ever been built --
        there is no data.json to read, and that is not an error."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)

        data = studio_metrics_overview(studio_tree)
        entry = data["metrics"][0]
        assert data["metrics_report_built"] is False
        assert entry["spark"] == ""

    def test_oversized_data_json_is_skipped_not_parsed(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, monkeypatch
    ):
        from apps.reports import metrics_catalog

        monkeypatch.setattr(metrics_catalog, "_SPARK_MAX_DATA_BYTES", 10)
        entry = self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[0, 0, 1.0], [1, 0, 2.0], [2, 0, 3.0]]),
        )
        assert entry["monitored"] is True and entry["spark"] == ""

    def test_unreadable_data_json_does_not_break_the_page(
        self, studio_tree, write_metrics_yaml, write_report, write_meta
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        h = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)
        write_meta("metrics", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "gross_revenue", "definition_hash": h, "version": 2}])
        out = studio_tree.output_dir / "metrics"
        (out / "data.json").write_text("{not json", encoding="utf-8")

        entry = next(m for m in studio_metrics_overview(studio_tree)["metrics"]
                     if m["row"].name == "gross_revenue")
        assert entry["spark"] == ""

    def test_page_renders_the_svg_for_a_monitored_metric(
        self, studio_tree, write_metrics_yaml, write_report, write_meta,
        member, grant_studio, login
    ):
        self._overview(
            studio_tree, write_metrics_yaml, write_report, write_meta,
            _built_data([[0, 0, 10.0], [1, 0, 30.0], [2, 0, 20.0]]),
        )
        grant_studio(member, studio_tree, roles.VIEWER)

        body = login(member).get(
            f"/s/{studio_tree.org.slug}/{studio_tree.slug}/metrics"
        ).content.decode()
        assert 'class="met-spark"' not in body  # secondary owns chart projections
        data = _indexed_summary(login(member), studio_tree)
        assert data["metrics"][0]["chart_state"] == "legacy"
        assert data["metrics"][0]["embed_url"] == ''
        from apps.reports.models import ReportSourceSummary
        ReportSourceSummary.objects.filter(report__studio=studio_tree).update(quality="verified")
        row = studio_tree.metric_definitions.get(name="gross_revenue")
        from apps.reports.models import MetricCatalogState
        revision = MetricCatalogState.objects.get(studio=studio_tree).definition_revision
        secondary = login(member).get(f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/metrics/summary",
                                     {"ids": row.pk, "definition_revision": revision}).json()
        assert len(secondary["metrics"][0]["spark"].split()) == 3
        assert secondary["metrics"][0]["chart_state"] == "legacy"
        assert secondary['metrics'][0]['embed_url'] == ''


SEARCHABLE = {
    "name": "refund_rate",
    "label": "Refund Rate",
    "description": "Refunds as a share of gross bookings.",
    "owner": "finance@demo.example",
    "tags": ["revenue", "risk"],
    "format": "percent",
    "agg": "ratio",
    "numerator": "refunded",
    "denominator": "gross_revenue",
}


class TestCatalogSearch:
    """Filtering is client-side over the cards the server already rendered
    (internal planning ticket #114), so what the server owes is the haystack and the controls
    that read it -- pinned here, since nothing else would notice a field
    quietly dropping out of `data-search`."""

    def _body(self, studio, member, grant_studio, login):
        grant_studio(member, studio, roles.VIEWER)
        return login(member).get(
            f"/s/{studio.org.slug}/{studio.slug}/metrics"
        ).content.decode()

    def test_the_box_is_a_labelled_search_input_with_a_live_count(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        body = self._body(studio_tree, member, grant_studio, login)

        assert 'type="search" id="metSearch"' in body
        assert '<label class="sr-only" for="metSearch">' in body   # a real label, not a placeholder
        assert 'id="metCount"' in body and 'aria-live="polite"' in body
        assert 'id="metEmpty"' in body

    def test_every_card_carries_name_id_description_tags_and_owner(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([SEARCHABLE])
        sync_studio_metrics(studio_tree)
        body = self._body(studio_tree, member, grant_studio, login)

        haystack = body.split('data-search="')[1].split('"')[0]
        assert "Refund Rate" in haystack           # label
        assert "refund_rate" in haystack           # identifier
        assert "gross bookings" in haystack        # description
        assert "revenue risk" in haystack          # tags, space-joined
        assert "finance@demo.example" in haystack  # owner

    def test_the_haystack_stops_at_the_claims_zone(
        self, studio_tree, write_metrics_yaml, write_report, write_meta,
        member, grant_studio, login
    ):
        """Matching the card's own textContent would make "current" a query
        that hits every claimed metric on the page. The attribute is the
        five fields and nothing else."""
        write_metrics_yaml([SEARCHABLE])
        sync_studio_metrics(studio_tree)
        write_report("insert-coin")
        sync_studio_registry(studio_tree)
        write_meta("insert-coin", last_run=_now_iso(), last_status="success",
                   metrics_used=[{"id": "refund_rate", "definition_hash": "old", "version": 1}])
        body = self._body(studio_tree, member, grant_studio, login)

        haystack = body.split('data-search="')[1].split('"')[0]
        assert "insert-coin" not in haystack
        assert "stale" not in haystack and "built against" not in haystack

    def test_the_script_owns_the_filter_and_clears_on_escape_without_closing(self):
        """The two Escape handlers on this page have to stay out of each
        other's way: one keypress in the box empties the box, and does not
        also close an open overlay. Pinned because it is a propagation
        contract between two IIFEs in one file, invisible to any unit test."""
        from pathlib import Path

        js = Path("static/metrics_overlay.js").read_text(encoding="utf-8")
        assert 'root.querySelector("#" + \'metSearch\')' in js
        assert "card.dataset.search" in js
        assert "ev.stopPropagation();" in js
        assert "card.hidden = !hit;" in js


class TestMetricComparison:
    def _url(self, studio):
        return f"/s/{studio.org.slug}/{studio.slug}/metrics/compare"

    def test_stale_and_unbuilt_cards_remain_explicit(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE, {**GROSS_REVENUE, "name": "net_revenue", "label": "Net Revenue"}])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)
        client = login(member)
        response = client.get(self._url(studio_tree), {"a": "gross_revenue", "b": "net_revenue"})
        assert response.status_code == 200
        assert response.content.decode().count("has not been built yet") == 2
        write_report(**GENERATED_METRICS_REPORT)
        sync_studio_registry(studio_tree)
        write_meta("metrics", last_run=_now_iso(), metrics_used=["gross_revenue", "net_revenue"])
        invalidate_metrics_overview_cache()
        response = client.get(self._url(studio_tree), {"a": "gross_revenue", "b": "net_revenue"})
        assert response.status_code == 200
        assert response.content.decode().count("unverified or stale definition") == 2
        assert 'data-embed=""' in response.content.decode()
        from apps.reports.models import Report
        from apps.reports.source_summary import reconcile
        assert reconcile(Report.objects.get(studio=studio_tree, slug='metrics').pk)['status'] == 'ready'
        explicit = client.get(self._url(studio_tree), {'a': 'gross_revenue', 'b': 'net_revenue', 'legacy': '1'})
        assert '?only=metric-gross_revenue' in explicit.content.decode()

    @pytest.mark.parametrize("query", [
        {"a": "gross_revenue", "b": "gross_revenue"},
        {"a": "gross_revenue", "b": "../secret"},
        {"a": "gross_revenue", "b": "a" * 201},
        {"a": "gross_revenue", "b": "foreign_secret"},
        {"a": "gross_revenue", "from": "2026-02-30", "to": "2026-03-01"},
        {"a": "gross_revenue", "from": "2026-10-02", "to": "2026-10-01"},
        {"a": "gross_revenue", "split_a": "s" * 201},
    ])
    def test_invalid_or_foreign_identity_reveals_no_partial_card(
        self, query, studio_tree, other_studio, write_metrics_yaml, member, grant_studio, login
    ):
        from apps.reports.models import MetricDefinition
        MetricDefinition.objects.create(studio=other_studio, name="foreign_secret", label="Confidential metric")
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        grant_studio(member, studio_tree, roles.VIEWER)
        response = login(member).get(self._url(studio_tree), query)
        assert response.status_code == 400
        assert "Gross Revenue" not in response.content.decode()
        assert "Confidential" not in response.content.decode()

    def test_removed_identity_is_rechecked_despite_catalog_cache(
        self, studio_tree, write_metrics_yaml, member, grant_studio, login
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        studio_metrics_overview(studio_tree)
        studio_tree.metric_definitions.update(present_in_scan=False)
        grant_studio(member, studio_tree, roles.VIEWER)
        response = login(member).get(self._url(studio_tree), {"a": "gross_revenue"})
        assert response.status_code == 400
        assert "Gross Revenue" not in response.content.decode()

    def test_selected_report_viewer_cannot_read_comparison(
        self, studio_tree, write_metrics_yaml, member, make_group, attach_group, login, settings, monkeypatch
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get(studio=studio_tree)
        grant.viewer_scope = "selected"
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        def forbidden_read(*_args):
            raise AssertionError("Denied requests must not read report metadata")
        monkeypatch.setattr("apps.core.storage.read_meta", forbidden_read)
        response = login(member).get(self._url(studio_tree), {"a": "gross_revenue"})
        assert response.status_code == 403
        assert "Gross Revenue" not in response.content.decode()

    def test_anonymous_and_foreign_org_access(self, studio_tree, client, other_org, make_user, login):
        assert client.get(self._url(studio_tree)).status_code == 302
        outsider = make_user("comparison-outsider@demo.example", org=other_org)
        assert login(outsider).get(self._url(studio_tree)).status_code == 404
