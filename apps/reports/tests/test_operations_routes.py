"""HTTP contracts for the routed Operations reads, native page and logs."""
import json

import pytest
from django.db import connection

from apps.core import roles
from apps.reports.models import Report
from apps.runner.models import Run


def url(studio, path):
    return f"/s/{studio.org.slug}/{studio.slug}/{path}"


@pytest.fixture
def seeded(org_admin, org, studio, login):
    assert connection.vendor == 'postgresql'
    login(org_admin)
    reports = [Report.objects.create(studio=studio, slug=f'r{i:02d}', name=f'Report {i}', category='A' if i % 2 else 'B') for i in range(30)]
    run = Run.objects.create(report=reports[0], studio=studio, slug='r00', status=Run.SUCCESS, stdout_tail='hello out', stderr_tail='')
    return reports, run


@pytest.mark.django_db
def test_catalog_pages_filters_and_folders(client, studio, seeded):
    data = client.get(url(studio, 'api/operations/catalog')).json()
    assert data['state'] == 'ready' and len(data['rows']) == 25 and data['pagination']['total'] == 30
    assert data['folders']['all'] == 30 and {c['name'] for c in data['folders']['categories']} == {'A', 'B'}
    page2 = client.get(url(studio, 'api/operations/catalog?page=2&category=A')).json()
    assert page2['pagination']['total'] == 15 and page2['pagination']['pages'] == 1 and page2['rows'] == []
    bad = client.get(url(studio, 'api/operations/catalog?state=nonsense'))
    assert bad.status_code == 400 and bad.json()['state'] == 'invalid'
    assert client.post(url(studio, 'api/operations/catalog')).status_code == 405
    assert 'no-store' in client.get(url(studio, 'api/operations/catalog'))['Cache-Control']


@pytest.mark.django_db
def test_status_stats_sources_bounded(client, studio, seeded):
    status = client.get(url(studio, 'api/operations/status?slug=r00&slug=r01')).json()
    assert status['state'] == 'ready' and status['coverage']['selected_slugs'] == 2
    stats = client.get(url(studio, 'api/operations/stats?slug=r00')).json()
    assert stats['state'] == 'ready' and stats['window_days'] == 30
    too_many = '&'.join(f'slug=r{i:02d}' for i in range(26))
    assert client.get(url(studio, f'api/operations/status?{too_many}')).status_code == 400
    assert client.get(url(studio, 'api/operations/sources')).json()['state'] == 'ready'


@pytest.mark.django_db
def test_log_json_html_and_persisted_download(client, studio, seeded):
    _, run = seeded
    base = url(studio, 'api/operations/reports/r00/log')
    data = client.get(base).json()
    assert data['stdout'] == 'hello out' and data['authority'] == 'persisted-db-tail'
    assert client.get(base + '?run=not-a-uuid').status_code == 400
    page = client.get(url(studio, 'operations/reports/r00/log'))
    assert page.status_code == 200 and b'hello out' in page.content and b'Download persisted tails' in page.content
    download = client.get(base + f'/download?run={run.pk}')
    body = b''.join(download.streaming_content).decode()
    assert download.status_code == 200 and 'attachment' in download['Content-Disposition']
    assert 'hello out' in body and 'complete persisted tail' in body and 'no-store' in download['Cache-Control']
    assert client.get(base + '/download').status_code == 400
    assert client.get(url(studio, 'api/operations/reports/missing/log')).status_code == 404


@pytest.mark.django_db
def test_native_page_server_renders_bounded_rows_without_js(client, studio, seeded):
    response = client.get(url(studio, 'operations?state=ok'))
    html = response.content.decode()
    assert response.status_code == 200 and 'data-ops-native' in html
    assert html.count('data-ops-row=') <= 25 and 'Run and Stop controls require JavaScript' in html
    page = client.get(url(studio, 'operations')).content.decode()
    assert 'rel="next"' in page and 'page=2' in page
    assert client.get(url(studio, 'operations?state=bogus')).status_code == 400


@pytest.mark.django_db
def test_viewer_and_anonymous_are_denied(client, member, studio, grant_studio, login):
    grant_studio(member, studio, roles.VIEWER)
    assert client.get(url(studio, 'api/operations/catalog')).status_code in (302, 401, 403)
    login(member)
    for path in ('api/operations/catalog', 'api/operations/status', 'api/operations/stats', 'api/operations/sources',
                 'api/operations/reports/x/log', 'operations/reports/x/log'):
        assert client.get(url(studio, path)).status_code == 403, path


@pytest.mark.django_db
def test_whole_request_sql_and_bytes_stay_bounded_as_reports_grow(client, org_admin, studio, login, capsys):
    """Measured growth: 20 / 200 / 1,000 reports. Prints legacy baselines with -s."""
    from django.test.utils import CaptureQueriesContext

    assert connection.vendor == 'postgresql'
    login(org_admin)
    paths = ['api/operations/catalog', 'api/operations/status?slug=r0000', 'api/operations/stats?slug=r0000',
             'api/operations/sources', 'operations']
    legacy = ['api/registry', 'api/system/status', 'api/system/run-stats', 'operations']
    rows = []
    for total in (20, 200, 1000):
        existing = Report.objects.filter(studio=studio).count()
        Report.objects.bulk_create([Report(studio=studio, slug=f'r{i:04d}', name=f'Report {i}', description='d' * 1500, config={'data_sources': ['a', 'b']}) for i in range(existing, total)])
        for group, names in (('new', paths), ('legacy', legacy)):
            queries = size = 0
            for path in names:
                with CaptureQueriesContext(connection) as captured:
                    response = client.get(url(studio, path))
                assert response.status_code == 200, (path, response.status_code)
                body = b''.join(response.streaming_content) if response.streaming else response.content
                queries += len(captured)
                size += len(body)
                if group == 'new' and path != 'operations':
                    assert len(captured) <= 30 and len(body) <= 128 * 1024, (path, len(captured), len(body))
            rows.append((total, group, queries, size))
    with capsys.disabled():
        print('\nOPS-MEASURE total group sql_queries_all_requests bytes_all_requests')
        for row in rows:
            print('OPS-MEASURE', *row)
    new = [r for r in rows if r[1] == 'new']
    assert new[1][2] == new[2][2]  # SQL does not grow from 200 to 1,000 reports
    assert max(r[3] for r in new) <= 300 * 1024
