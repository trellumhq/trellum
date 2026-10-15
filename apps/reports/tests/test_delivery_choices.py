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


def _main(response):
    return response.content.decode().split('<main', 1)[1]  # the shell carries its own scope pickers


def test_filter_and_picker_list_every_report_without_remote_search_or_browse_ui(login, org_admin, report_row):
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'choice-{i:03}', name=f'Choice {i:03}') for i in range(60)])
    client = login(org_admin)
    base = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}/deliveries'
    for path, options in [(base, 'report_filter_options'), (base + '/new', 'reports')]:
        response = client.get(path)
        assert len(response.context[options]) == 61
        html = _main(response)
        assert html.count('Choice 0') == 60
        for marker in ('data-choice-source', 'Browse reports', 'Search reports', 'choose_report', 'choice_page', 'ui-pager'):
            assert marker not in html
    global_page = client.get('/me/deliveries')
    assert len(global_page.context['report_filter_options']) == 61
    assert 'data-choice-source' not in _main(global_page)


def test_only_a_huge_list_is_cut_and_switches_to_remote_search(login, org_admin, report_row, monkeypatch):
    monkeypatch.setattr('apps.reports.views.CHOICE_LIMIT', 10)
    Report.objects.bulk_create([Report(studio=report_row.studio, slug=f'huge-{i:03}', name=f'Huge {i:03}') for i in range(30)])
    selected = Report.objects.create(studio=report_row.studio, slug='zz-selected', name='ZZ Selected')
    client = login(org_admin)
    prefix = f'/s/{report_row.studio.org.slug}/{report_row.studio.slug}'
    response = client.get(f'{prefix}/deliveries', {'report': f'id:{selected.pk}'})
    assert len(response.context['report_filter_options']) == 11  # the cap plus the selected report
    html = _main(response)
    assert f'data-choice-source="{prefix}/api/delivery-reports"' in html and 'data-choice-value-prefix="id:"' in html
    assert f'value="id:{selected.pk}" selected' in html
    picker = client.get(f'{prefix}/deliveries/new')
    assert len(picker.context['reports']) == 10
    assert f'data-choice-source="{prefix}/api/delivery-reports"' in _main(picker)


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
