"""A new source summary row starts pending at schema two."""
import pytest
from apps.reports.models import Report, ReportSourceSummary

pytestmark = pytest.mark.django_db(transaction=True)


def test_new_row_is_pending_schema_two(studio_tree):
    report = Report.objects.create(studio=studio_tree, slug="new")
    summary = ReportSourceSummary.objects.create(report=report)
    assert summary.schema_version == 2
    assert summary.runtime_state == summary.experiment_state == "pending"
    assert summary.has_output is None
