"""Delivery batches preserve live access and bounded page costs."""
import pytest
from apps.core.report_access import ReportAccessBatch, bulk_can_view_report
from apps.reports.models import EmailSchedule
from apps.reports.recipient_access import DeliveryPresentationBatch

pytestmark = pytest.mark.django_db


def test_batch_scope_and_compatibility(org_admin, report_row, other_studio):
    from apps.reports.models import Report
    duplicate = type(org_admin).objects.get(pk=org_admin.pk)
    assert bulk_can_view_report([org_admin, duplicate], report_row) == {org_admin.pk: True}
    assert bulk_can_view_report([], object()) == {}
    batch = ReportAccessBatch([org_admin], report_row.studio.org)
    with pytest.raises(ValueError):
        batch.can_view(duplicate, report_row)
    foreign = Report.objects.create(studio=other_studio, slug='foreign-batch')
    assert other_studio.org_id != report_row.studio.org_id
    with pytest.raises(ValueError):
        batch.can_view(org_admin, foreign)


def test_inactive_recipient_is_filtered_and_second_batch_revokes(org_admin, report_row):
    schedule = EmailSchedule.objects.create(report=report_row, created_by=org_admin)
    schedule.recipients.add(org_admin)
    def fresh():
        return EmailSchedule.objects.select_related('report__studio__org').prefetch_related('recipients', 'recipient_groups').get(pk=schedule.pk)
    row = fresh()
    assert len(DeliveryPresentationBatch([row]).eligible(row)) == 1
    org_admin.is_active = False
    org_admin.save(update_fields=['is_active'])
    row = fresh()
    assert DeliveryPresentationBatch([row]).eligible(row) == []
