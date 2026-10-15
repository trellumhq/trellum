"""Remote/native choices search authorized data before slicing."""
import pytest
from apps.core import roles
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def test_remote_full_search_and_selected_offpage(login, org_admin, report_row):
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'choice-{i:03}', name=f'Choice {i:03}') for i in range(60)])
    selected = Report.objects.create(studio=report_row.studio, slug='zz-selected', name='ZZ Selected')
    client = login(org_admin)
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/delivery-reports'
    response = client.get(path, {'selected': selected.pk})
    assert response.status_code == 200
    assert response['Cache-Control'] == 'private, no-store'
    payload = response.json()
    assert len(payload['results']) == 25
    assert payload['selected']['value'] == str(selected.pk)
    assert all(item['value'] != str(selected.pk) for item in payload['results'])
    searched = client.get(path, {'q': 'ZZ Selected'}).json()
    assert [item['value'] for item in searched['results']] == [str(selected.pk)]
    assert searched['pagination']['total'] == 1


def test_report_filter_lists_every_report_inline(login, org_admin, report_row):
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'choice-{i:03}', name=f'Choice {i:03}') for i in range(60)])
    client = login(org_admin)
    html = client.get(f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries').content.decode()
    assert html.count('<option value="id:') >= 61
    assert 'data-choice-source' not in html
    assert 'Browse reports' not in html and 'Previous' not in html and 'Next' not in html
    assert '>Filter</button>' not in html


def test_add_delivery_picker_lists_every_report_inline(login, org_admin, report_row):
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'pick-{i:03}', name=f'Pick {i:03}') for i in range(60)])
    client = login(org_admin)
    html = client.get(f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries/new').content.decode()
    assert html.count('<option value="') >= 62
    assert 'data-choice-source' not in html and 'name="choice_q"' not in html and '>Continue<' not in html


def test_very_long_report_lists_preview_and_search_without_pages(login, org_admin, report_row, monkeypatch):
    import apps.core.choice_requests as module
    monkeypatch.setattr(module, 'INLINE_CHOICE_LIMIT', 30)
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'long-{i:03}', name=f'Long {i:03}') for i in range(60)])
    client = login(org_admin)
    base = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}'
    html = client.get(f'{base}/deliveries').content.decode()
    assert f'data-choice-source="{base}/api/delivery-reports"' in html
    assert html.count('<option value="id:') <= 26
    assert 'Browse reports' not in html and 'choice_page' not in html


def test_viewer_private_choices_and_selected_labels_are_hidden(login, make_user, grant_studio, org, report_row, settings):
    settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
    settings.TRELLUM_REPORT_ACCESS_MODEL = 'proxy'
    user = make_user('choice-viewer@example.test', org=org)
    grant_studio(user, report_row.studio, roles.VIEWER)
    private = Report.objects.create(studio=report_row.studio, slug='secret-choice', name='Protected choice', audience='private')
    client = login(user)
    path = f'/s/{org.slug}/{report_row.studio.slug}/api/delivery-reports'
    payload = client.get(path, {'q': 'Protected', 'selected': private.pk}).json()
    assert payload['selected'] is None
    assert payload['results'] == []
    assert payload['pagination']['total'] == 0
    assert client.post(path).status_code == 405


def test_remote_choices_bearer_scope_and_global_denial(client, org_admin, report_row, other_studio):
    from apps.accounts.models import ApiKey
    key, token = ApiKey.mint(user=org_admin, org=report_row.studio.org, name='choices', scopes='read')
    headers = {'HTTP_AUTHORIZATION': 'Bearer ' + token}
    path = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/api/delivery-reports'
    assert client.get(path, **headers).status_code == 200
    assert client.get('/api/delivery-reports', **headers).status_code in (403, 404)
    foreign = f'/s/{other_studio.org.slug}/{other_studio.slug}/api/delivery-reports'
    assert client.get(foreign, **headers).status_code == 404
    key.revoke()
    assert client.get(path, **headers).status_code in (401, 403, 404)


def test_next_request_membership_revocation_removes_choices(login, make_user, grant_studio, org, report_row):
    from apps.studios.models import StudioMembership
    user = make_user('revoked-choice@example.test', org=org)
    grant_studio(user, report_row.studio, roles.VIEWER)
    client = login(user)
    path = f'/s/{org.slug}/{report_row.studio.slug}/api/delivery-reports'
    assert client.get(path).json()['pagination']['total'] == 1
    StudioMembership.objects.filter(user=user, studio=report_row.studio).delete()
    assert client.get(path).status_code == 404
