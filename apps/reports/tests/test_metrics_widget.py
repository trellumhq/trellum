"""'Metrics in this report' panel: the frozen-URL widget bundle and its
JSON API (semantic-layer Phase 2, mirrors static/report_views.js's own
test coverage in shape)."""
import pytest

from apps.core import roles
from apps.reports.scan import sync_studio_metrics, sync_studio_registry

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


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


class TestMetricsWidgetStub:
    """The frozen-URL bundle -- same shape as delivery-widget.js/menu-widget.js,
    no auth (the API calls it makes are gated, not the script itself)."""

    def test_widget_served(self, client, db):
        resp = client.get("/api/reports/metrics-widget.js")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("application/javascript")
        assert b"window.ReportMetrics" in resp.content


class TestApiReportMetrics:
    def _url(self, prefix, slug):
        return f"{prefix}/api/reports/{slug}/metrics"

    def test_current_claim_reports_current_true(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, login, viewer, prefix
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        current_hash = studio_tree.metric_definitions.get(name="gross_revenue").definition_hash

        write_report("monetization")
        sync_studio_registry(studio_tree)
        write_meta(
            "monetization", last_run="2026-08-20T06:00:00+00:00", last_status="success",
            metrics_used=[{"id": "gross_revenue", "definition_hash": current_hash, "version": 2}],
        )

        resp = login(viewer).get(self._url(prefix, "monetization"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["built_at"] == "2026-08-20T06:00:00+00:00"
        assert body["studio_metrics_url"] == f"{prefix}/metrics"
        assert body["metrics"] == [{
            "id": "gross_revenue",
            "label": "Gross Revenue",
            "format": "currency",
            "spec": "sum(total_revenue)",
            "description": "IAP plus ad revenue.",
            "current": True,
            "removed": False,
        }]

    def test_stale_claim_reports_current_false(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, login, viewer, prefix
    ):
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)

        write_report("insert-coin")
        sync_studio_registry(studio_tree)
        write_meta(
            "insert-coin", last_run="2026-08-19T06:00:00+00:00", last_status="success",
            metrics_used=[{"id": "gross_revenue", "definition_hash": "an-old-hash", "version": 1}],
        )

        resp = login(viewer).get(self._url(prefix, "insert-coin"))
        body = resp.json()
        assert body["metrics"][0]["current"] is False

    def test_metric_removed_from_catalog_is_shown_not_dropped(
        self, studio_tree, write_report, write_meta, login, viewer, prefix
    ):
        """The build claims an id metrics.yaml no longer (or not yet) defines
        -- surfaced honestly rather than silently omitted."""
        write_report("orphaned")
        sync_studio_registry(studio_tree)
        write_meta(
            "orphaned", last_run="2026-08-19T06:00:00+00:00", last_status="success",
            metrics_used=[{"id": "ghost_metric", "definition_hash": "x", "version": 1}],
        )

        resp = login(viewer).get(self._url(prefix, "orphaned"))
        body = resp.json()
        assert body["metrics"] == [{
            "id": "ghost_metric",
            "label": "ghost_metric",
            "format": "",
            "spec": "",
            "description": "",
            "current": False,
            "removed": True,
        }]

    def test_no_claims_is_an_empty_list(
        self, studio_tree, write_report, write_meta, login, viewer, prefix
    ):
        write_report("plain")
        sync_studio_registry(studio_tree)
        write_meta("plain", last_run="2026-08-19T06:00:00+00:00", last_status="success")

        resp = login(viewer).get(self._url(prefix, "plain"))
        assert resp.status_code == 200
        assert resp.json()["metrics"] == []

    def test_viewer_role_is_sufficient(
        self, studio_tree, write_metrics_yaml, write_report, write_meta, login, viewer, prefix
    ):
        """Unlike Activity (DEVELOPER), reading metric definitions needs
        only VIEWER -- a definition is not sensitive the way 'who looked at
        this' is."""
        write_metrics_yaml([GROSS_REVENUE])
        sync_studio_metrics(studio_tree)
        write_report("monetization")
        sync_studio_registry(studio_tree)
        write_meta("monetization", last_run="2026-08-20T06:00:00+00:00", last_status="success",
                   metrics_used=["gross_revenue"])

        resp = login(viewer).get(self._url(prefix, "monetization"))
        assert resp.status_code == 200

    def test_non_member_gets_404(self, studio_tree, org, make_user, login, write_report, prefix):
        write_report("monetization")
        sync_studio_registry(studio_tree)
        outsider = make_user("outsider@demo.example", org=org)
        resp = login(outsider).get(self._url(prefix, "monetization"))
        assert resp.status_code == 404

    def test_unknown_report_404s(self, login, viewer, prefix, studio_tree):
        resp = login(viewer).get(self._url(prefix, "does-not-exist"))
        assert resp.status_code == 404
