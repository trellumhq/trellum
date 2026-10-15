"""Delivery lists carry no pager: a silent SQL cap, and a muted note only when it cut the list."""
import pytest
from apps.reports.models import EmailSchedule, Report

pytestmark = pytest.mark.django_db


def test_schedule_api_lists_every_schedule_and_badge_counts_full_audience(login, org_admin, report_row, monkeypatch):
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/reports/{report_row.slug}/schedules'
    rows = EmailSchedule.objects.bulk_create([EmailSchedule(report=report_row, created_by=org_admin, recipient_roles=['everyone']) for _ in range(57)])
    client = login(org_admin)
    body = client.get(path).json()
    assert [row['id'] for row in body['schedules']] == [row.pk for row in rows]
    assert body['subscribed_count'] == 57 and 'pagination' not in body
    monkeypatch.setattr('apps.reports.views.DELIVERY_ROWS_LIMIT', 10)
    capped = client.get(path).json()
    assert [row['id'] for row in capped['schedules']] == [row.pk for row in rows[:10]]
    assert capped['subscribed_count'] == 57  # the badge counts the whole audience, not the capped list


def test_list_shows_every_row_without_a_pager_and_searches_everything(login, org_admin, report_row):
    EmailSchedule.objects.bulk_create([EmailSchedule(report=report_row, created_by=org_admin) for _ in range(51)])
    other = Report.objects.create(studio=report_row.studio, slug='off-page', name='Zebra off-page')
    match = EmailSchedule.objects.create(report=other, created_by=org_admin)
    client = login(org_admin)
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries'
    response = client.get(path)
    assert len(response.context['schedule_rows']) == 52
    html = response.content.decode()
    for text in ('ui-pager', 'Page 1 of', 'Previous', 'Showing the first'):
        assert text not in html
    searched = client.get(path + '?q=Zebra')
    assert [row['schedule'].pk for row in searched.context['schedule_rows']] == [match.pk]


def test_list_cap_is_silent_but_for_one_muted_line(login, org_admin, report_row, monkeypatch):
    monkeypatch.setattr('apps.reports.views.DELIVERY_ROWS_LIMIT', 5)
    EmailSchedule.objects.bulk_create([EmailSchedule(report=report_row, created_by=org_admin) for _ in range(8)])
    client = login(org_admin)
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries'
    response = client.get(path)
    assert len(response.context['schedule_rows']) == 5
    assert response.content.decode().count('<p class="muted">Showing the first 500 deliveries.') == 1
    EmailSchedule.objects.exclude(pk__in=EmailSchedule.objects.values_list('pk', flat=True)[:5]).delete()
    assert 'Showing the first' not in client.get(path).content.decode()
