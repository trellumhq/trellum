"""Metric usage is unknown, not zero, until the worker has indexed the studio's reports.

Right after an upgrade no report has a source summary yet. The Metrics page must
not call every metric unused or say "0 claimed" for those few seconds or minutes;
once the summaries exist it behaves exactly as before.
"""
import pytest
from django.utils import timezone

from apps.core import roles
from apps.reports.metrics_catalog import metric_definition_page
from apps.reports.models import (
    MetricCatalogState,
    MetricDefinition,
    Report,
    ReportMetricClaim,
    ReportSourceSummary,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def studio_metrics(studio, member, grant_studio, login):
    grant_studio(member, studio, roles.VIEWER)
    MetricCatalogState.objects.create(studio=studio, definition_revision=4)
    rows = MetricDefinition.objects.bulk_create([
        MetricDefinition(studio=studio, name=name, label=name.title(), definition_hash="h")
        for name in ("used", "spare", "other")])
    rows = list(MetricDefinition.objects.filter(studio=studio).order_by("name"))
    return studio, login(member), rows


def _base(studio):
    return f"/s/{studio.org.slug}/{studio.slug}"


def _summary(studio_metrics, **query):
    studio, client, rows = studio_metrics
    response = client.get(_base(studio) + "/api/metrics/summary", {
        "ids": ",".join(str(row.pk) for row in rows), "definition_revision": 4, **query})
    assert response.status_code == 200, response.content
    return response.json()


def _indexed(studio, slug, *claims):
    """A report the worker has been through: observed, with the given claims."""
    report = Report.objects.create(studio=studio, slug=slug, name=slug)
    summary = ReportSourceSummary.objects.create(
        report=report, state="ready", quality="verified", observed_at=timezone.now())
    ReportMetricClaim.objects.bulk_create([
        ReportMetricClaim(summary=summary, metric_name=name, definition_hash="h", version=1) for name in claims])
    return report


def _states(data):
    return {item["id"]: item["usage_state"] for item in data["metrics"]}


@pytest.mark.parametrize("summary_row", [False, True], ids=["no-summary-row", "row-waiting-for-first-look"])
def test_no_summaries_yet_is_measuring_not_unused(studio_metrics, summary_row):
    studio, client, _rows = studio_metrics
    for slug in ("sales", "retention"):
        report = Report.objects.create(studio=studio, slug=slug, name=slug)
        if summary_row:  # what a registry reload leaves behind: created, not yet observed
            ReportSourceSummary.objects.create(report=report)
    data = _summary(studio_metrics, claimed=1)
    assert data["state"] == "partial" and data["usage_pending"] is True
    assert all(item["claim_count"] == 0 and item["usage_state"] == "measuring" for item in data["metrics"])
    # The header number is asked for, and is a real zero only once nothing is pending.
    assert data["claimed_count"] == 0

    page = metric_definition_page(studio)
    assert page["usage_pending"] is True and page["claimed_count"] is None
    html = client.get(_base(studio) + "/metrics").content.decode()
    assert "measuring report usage" in html and "<b>3 defined</b>" in html
    assert "claimed by report" not in html and " 0 claimed" not in html
    assert "data-pending" in html


def test_partial_indexing_keeps_known_claims_and_measures_the_rest(studio_metrics):
    studio, client, rows = studio_metrics
    _indexed(studio, "sales", "used")
    Report.objects.create(studio=studio, slug="retention", name="retention")  # not indexed yet
    data = _summary(studio_metrics, claimed=1)
    by_name = {row.pk: row.name for row in rows}
    states = {by_name[pk]: state for pk, state in _states(data).items()}
    # What a report is known to claim is shown as before; only the unknown is "measuring".
    assert states == {"used": "known", "spare": "measuring", "other": "measuring"}
    assert next(item for item in data["metrics"] if by_name[item["id"]] == "used")["claim_count"] == 1
    assert data["usage_pending"] is True and data["claimed_count"] == 1
    html = client.get(_base(studio) + "/metrics").content.decode()
    assert "measuring report usage" in html and "claimed by report" not in html


def test_every_report_indexed_is_unchanged(studio_metrics):
    studio, client, _rows = studio_metrics
    _indexed(studio, "sales", "used")
    _indexed(studio, "retention")  # indexed, claims nothing
    data = _summary(studio_metrics)
    assert data["usage_pending"] is False and data["state"] == "ready"
    assert {item["usage_state"] for item in data["metrics"]} == {"known"}
    assert sorted(item["claim_count"] for item in data["metrics"]) == [0, 0, 1]
    # The header number is only computed when the page asks for it.
    assert "claimed_count" not in data
    assert _summary(studio_metrics, claimed=1)["claimed_count"] == 1
    page = metric_definition_page(studio)
    assert page["usage_pending"] is False and page["claimed_count"] == 1
    html = client.get(_base(studio) + "/metrics").content.decode()
    assert "<b>3 defined, 1 claimed by report</b>" in html
    assert "measuring" not in html and "data-pending" not in html


def test_a_studio_without_reports_has_nothing_to_wait_for(studio_metrics):
    studio, client, _ = studio_metrics
    assert _summary(studio_metrics)["usage_pending"] is False
    assert "<b>3 defined, 0 claimed by reports</b>" in client.get(_base(studio) + "/metrics").content.decode()


@pytest.mark.parametrize("values", [
    {"state": "pending", "observed_at": "now"},     # re-queued after a run: history stays valid
    {"state": "unavailable", "observed_at": None},  # first look failed: settled, backs off
    {"state": "uncertain", "observed_at": None},
], ids=["observed-before", "first-look-failed", "first-look-uncertain"])
def test_only_unobserved_pending_reports_hold_usage_back(studio_metrics, values):
    studio, _client, _ = studio_metrics
    report = Report.objects.create(studio=studio, slug="sales", name="sales")
    observed = timezone.now() if values["observed_at"] == "now" else None
    ReportSourceSummary.objects.create(report=report, state=values["state"], observed_at=observed)
    data = _summary(studio_metrics)
    assert data["usage_pending"] is False
    assert {item["usage_state"] for item in data["metrics"]} == {"known"}


def test_reports_that_do_not_count_do_not_hold_usage_back(studio_metrics):
    studio, _client, _ = studio_metrics
    Report.objects.create(studio=studio, slug="gone", present_in_scan=False)
    Report.objects.create(studio=studio, slug="expired", data_expired_at=timezone.now())
    Report.objects.create(studio=studio, slug="article", kind=Report.KIND_ANALYSIS)
    assert _summary(studio_metrics)["usage_pending"] is False


def test_comparison_header_does_not_claim_a_zero_while_waiting(studio_metrics):
    studio, client, _ = studio_metrics
    Report.objects.create(studio=studio, slug="sales", name="sales")
    html = client.get(_base(studio) + "/metrics/comparison").content.decode()
    assert "<b>3 defined</b>" in html and "claimed by report" not in html
    assert "measuring report usage" not in html  # nothing on this tab would ever clear it
    _indexed(studio, "retention", "used")
    ReportSourceSummary.objects.create(
        report=Report.objects.get(slug="sales"), state="ready", quality="verified", observed_at=timezone.now())
    assert "<b>3 defined, 1 claimed by report</b>" in client.get(_base(studio) + "/metrics/comparison").content.decode()
