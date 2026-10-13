"""Counts and filters precede delivery pagination."""
import pytest
from apps.reports.models import EmailSchedule, Report

pytestmark = pytest.mark.django_db


def test_schedule_api_is_bounded_and_badge_counts_full_audience(login, org_admin, report_row):
    rows = EmailSchedule.objects.bulk_create([EmailSchedule(report=report_row, created_by=org_admin, recipient_roles=['everyone']) for _ in range(57)])
    client = login(org_admin)
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/schedules'
    first = client.get(path).json()
    second = client.get(path + '?page=2').json()
    assert len(first['schedules']) == 50
    assert len(second['schedules']) == 7
    assert first['subscribed_count'] == second['subscribed_count'] == 57
    assert first['pagination']['total'] == 57
    assert {row['id'] for row in first['schedules']}.isdisjoint(row['id'] for row in second['schedules'])
    assert [row['id'] for row in second['schedules']] == [row.pk for row in rows[50:]]


def test_list_paging_and_full_search(login, org_admin, report_row):
    EmailSchedule.objects.bulk_create([EmailSchedule(report=report_row, created_by=org_admin) for _ in range(51)])
    other = Report.objects.create(studio=report_row.studio, slug='off-page', name='Zebra off-page')
    match = EmailSchedule.objects.create(report=other, created_by=org_admin)
    client = login(org_admin)
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries'
    first = client.get(path)
    second = client.get(path + '?page=2')
    assert len(first.context['schedule_rows']) == 50
    assert len(second.context['schedule_rows']) == 2
    assert first.wsgi_request.delivery_page.paginator.count == 52
    searched = client.get(path + '?q=Zebra')
    assert [row['schedule'].pk for row in searched.context['schedule_rows']] == [match.pk]
