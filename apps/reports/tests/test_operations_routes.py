"""HTTP contracts for the routed Operations reads and logs."""
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
def test_page_lists_every_report_without_a_pager_up_to_the_cap(client, studio, seeded):
    """The page reads all reports (and sources, active runs, stats) in one bounded request each."""
    data = client.get(url(studio, 'api/operations/catalog?page_size=500')).json()
    assert len(data['rows']) == 30 and data['pagination'] == {'page': 1, 'page_size': 500, 'total': 30, 'pages': 1}
    assert client.get(url(studio, 'api/operations/catalog?page_size=501')).status_code == 400
    assert client.get(url(studio, 'api/operations/catalog?page_size=0')).status_code == 400
    assert len(client.get(url(studio, 'api/operations/sources?page_size=500')).json()['rows']) == 0
    status = client.get(url(studio, 'api/operations/status?scope=studio')).json()
    assert status['state'] == 'ready' and status['server_uptime_seconds'] >= 0 and status['coverage']['selected_slugs'] is None
    assert client.get(url(studio, 'api/operations/status?scope=nonsense')).status_code == 400
    stats = client.get(url(studio, 'api/operations/stats?scope=studio')).json()
    assert stats['state'] == 'ready' and 'r00' in stats['stats']


@pytest.mark.django_db
def test_log_json_html_and_persisted_download(client, studio, seeded):
    _, run = seeded
    base = url(studio, 'api/operations/reports/r00/log')
    data = client.get(base).json()
    assert data['stdout'] == 'hello out' and data['authority'] == 'persisted-db-tail'
    assert client.get(base + '?run=not-a-uuid').status_code == 400
    download = client.get(base + f'/download?run={run.pk}')
    body = b''.join(download.streaming_content).decode()
    assert download.status_code == 200 and 'attachment' in download['Content-Disposition']
    assert 'hello out' in body and 'complete persisted tail' in body and 'no-store' in download['Cache-Control']
    assert client.get(base + '/download').status_code == 400
    assert client.get(url(studio, 'api/operations/reports/missing/log')).status_code == 404


@pytest.mark.django_db
def test_page_is_the_javascript_shell_without_server_rendered_rows(client, studio, seeded):
    response = client.get(url(studio, 'operations'))
    html = response.content.decode()
    assert response.status_code == 200 and 'data-ops-row=' not in html and 'data-ops-native' not in html
    assert client.get(url(studio, 'operations/reports/r00/log')).status_code == 404


@pytest.mark.django_db
def test_viewer_and_anonymous_are_denied(client, member, studio, grant_studio, login):
    grant_studio(member, studio, roles.VIEWER)
    assert client.get(url(studio, 'api/operations/catalog')).status_code in (302, 401, 403)
    login(member)
    for path in ('api/operations/catalog', 'api/operations/status', 'api/operations/stats', 'api/operations/sources',
                 'api/operations/reports/x/log'):
        assert client.get(url(studio, path)).status_code == 403, path


@pytest.mark.django_db
def test_whole_request_sql_and_bytes_stay_bounded_as_reports_grow(client, org_admin, studio, login, capsys):
    """Measured growth: 20 / 200 / 1,000 reports. Prints legacy baselines with -s."""
    from django.test.utils import CaptureQueriesContext

    assert connection.vendor == 'postgresql'
    login(org_admin)
    paths = ['api/operations/catalog', 'api/operations/status?slug=r0000', 'api/operations/stats?slug=r0000',
             'api/operations/sources', 'operations']
    # What the page itself reads: every report up to the cap, in one request each.
    page = ['api/operations/catalog?page_size=500', 'api/operations/status?scope=studio',
            'api/operations/stats?scope=studio', 'api/operations/sources?page_size=500']
    legacy = ['api/registry', 'api/system/status', 'api/system/run-stats', 'operations']
    rows = []
    for total in (20, 200, 1000):
        existing = Report.objects.filter(studio=studio).count()
        Report.objects.bulk_create([Report(studio=studio, slug=f'r{i:04d}', name=f'Report {i}', description='d' * 1500, config={'data_sources': ['a', 'b']}) for i in range(existing, total)])
        for group, names in (('new', paths), ('page', page), ('legacy', legacy)):
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
                if group == 'page':
                    assert len(captured) <= 40 and len(body) <= 500 * 4096, (path, len(captured), len(body))
            rows.append((total, group, queries, size))
    with capsys.disabled():
        print('\nOPS-MEASURE total group sql_queries_all_requests bytes_all_requests')
        for row in rows:
            print('OPS-MEASURE', *row)
    new = [r for r in rows if r[1] == 'new']
    assert new[1][2] == new[2][2]  # SQL does not grow from 200 to 1,000 reports
    assert max(r[3] for r in new) <= 300 * 1024
    capped = [r for r in rows if r[1] == 'page']
    assert capped[1][2] == capped[2][2]  # the capped page's SQL is flat too
