"""Operations helper contracts; PostgreSQL tests await serial runtime ownership."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.http import QueryDict
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.datasources.models import DataSource, RepoDataSource
from apps.reports import operations
from apps.reports.models import Report, ReportFavorite, ReportSourceSummary
from apps.runner.models import Run
from apps.runner.stats import studio_run_stats


def request_for(user, org, studio, **kwargs):
    request = RequestFactory().get('/')
    request.user, request.org, request.studio = user, org, studio
    for key, value in kwargs.items():
        setattr(request, key, value)
    return request


def summary(report, *, now=None, status='success', blocked=None, runtime=None, **kwargs):
    now = now or timezone.now()
    return ReportSourceSummary.objects.create(
        report=report, schema_version=2, observed_at=now, attempt_at=now,
        state='ready', quality='observed', attempt_status=status,
        attempt_blocked_by=blocked or [], runtime_state='ready',
        runtime=runtime if runtime is not None else {'validation': {'summary': {'pass': 1, 'fail': 0, 'warn': 0}}},
        has_output=True, html_entry='index.html', has_live_queries=False, **kwargs,
    )


def postgres():
    assert connection.vendor == 'postgresql', 'Operations gate requires PostgreSQL; SQLite is not equivalent'


@pytest.fixture
def forbid_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Operations primary read attempted source I/O or repair')
    from apps.core import storage
    from apps.reports import scan, source_summary
    for name in ('read_meta', 'html_index', 'live_query_index', 'summary_output_facts', 'read_summary_artifact'):
        monkeypatch.setattr(storage, name, forbidden)
    monkeypatch.setattr(scan, 'build_registry_payload', forbidden)
    monkeypatch.setattr(scan, 'sync_studio_registry', forbidden)
    monkeypatch.setattr(source_summary, 'reconcile', forbidden)


@pytest.mark.parametrize('query', [
    'page=0', 'page=-1', 'page=1.0', 'page=1e2', 'page=1000001', 'page=1&page=2',
    'page_size=0', 'page_size=501', 'page_size=1.5', 'page_size=-1', 'page_size=10&page_size=20',
    'source_page=0', 'q=a&q=b', 'state=bogus', 'sort=bogus', 'asc=true',
    'folder=unknown', 'recent=x/y', 'recent=x%5Cy', 'recent=%00',
])
def test_reject_ambiguous_or_hostile_parameters(query):
    with pytest.raises(ValueError):
        operations.validate_operations_params(QueryDict(query))


def test_input_caps_are_explicit():
    for params in ({'q': 'x'*1001}, {'source_q': 'x'*1001}, {'category': 'x'*201}, {'recent': ['x']*9}, {'padding': 'x'*32769}):
        with pytest.raises(ValueError):
            operations.validate_operations_params(params)
    params = operations.validate_operations_params({'q': '界'*1000, 'page': '1000000', 'recent': ['a', 'a', 'b']})
    assert params.q == '界'*1000 and params.page == 1000000 and params.recent == ('a', 'b')
    assert params.page_size == operations.PAGE_SIZE
    assert operations.validate_operations_params({'page_size': str(operations.ROW_CAP)}).page_size == operations.ROW_CAP
    # Unknown query keys still participate in the total encoded query limit.
    with pytest.raises(ValueError):
        operations.validate_operations_params({'padding': '界'*11000})


@pytest.mark.parametrize('selection', ['slug', ['x']*26, ['x/y'], ['x\\y'], [''], ['x'*201], ['a\x7fb']])
def test_selection_bounds(selection):
    with pytest.raises(ValueError):
        operations.validate_slugs(selection)


@pytest.mark.parametrize('entry', ['/index.html', '../index.html', 'a/../b', 'a//b', 'a\\b', 'https://host/a', 'a?b', 'a#b', 'a\x00b'])
def test_output_entry_cannot_escape_authorized_report(entry):
    assert operations._safe_entry(entry) is None


def test_oversized_response_is_never_false_empty():
    result = operations._bounded(dict(version=1, state='ready', rows=[{'description': '\x00'*200000}], pagination={'total': 42}, coverage={'unknown': 4}))
    assert result['state'] == 'oversized' and result['pagination']['total'] == 42
    assert 'rows' not in result
    assert len(json.dumps(result, cls=DjangoJSONEncoder).encode()) <= operations.RESPONSE_BYTES


@pytest.mark.django_db
def test_complete_catalog_includes_metrics_and_analyses(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    for i in range(31):
        Report.objects.create(studio=studio, slug=f'r{i:02}', name=f'Report {i:02}', priority=i)
    Report.objects.create(studio=studio, slug='metrics', tags=['metrics'], priority=40)
    Report.objects.create(studio=studio, slug='analysis', kind='analysis', priority=41)
    Report.objects.create(studio=other_studio, slug='secret')
    Report.objects.create(studio=studio, slug='absent', present_in_scan=False)
    request = request_for(org_admin, org, studio)
    with CaptureQueriesContext(connection) as queries:
        first = operations.operations_page_payload(request, {})
    assert len(first['rows']) == 25 and first['pagination']['total'] == 33
    assert first['counts']['total'] == 33 and first['counts']['unknown'] == 33
    assert first['coverage']['validation']['unknown'] == 33
    assert len(queries) <= 30
    second = operations.operations_page_payload(request, {'page': '2'})
    assert {r['slug'] for r in second['rows']} >= {'metrics', 'analysis'}
    found = operations.operations_page_payload(request, {'q': 'Report 30'})
    assert [r['slug'] for r in found['rows']] == ['r30'] and found['counts']['total'] == 1
    assert len(json.dumps(first, cls=DjangoJSONEncoder).encode()) <= operations.RESPONSE_BYTES


@pytest.mark.django_db
def test_favorite_recent_and_category_filter_before_counts(org_admin, org, studio, forbid_io):
    postgres()
    reports = [Report.objects.create(studio=studio, slug=f'r{i}', category='Chosen' if i != 2 else 'Other') for i in range(3)]
    ReportFavorite.objects.create(report=reports[1], user=org_admin)
    request = request_for(org_admin, org, studio)
    assert operations.operations_page_payload(request, {'folder': 'favorites'})['counts']['total'] == 1
    assert operations.operations_page_payload(request, {'folder': 'recent', 'recent': ['r2', 'r0']})['counts']['total'] == 2
    assert operations.operations_page_payload(request, {'category': 'Chosen'})['counts']['total'] == 2


@pytest.mark.django_db
def test_denial_scope_revocation_and_api_key(org_admin, member, org, studio, other_org, grant_studio, forbid_io):
    postgres()
    for user in (member,):
        with pytest.raises(PermissionDenied):
            operations.operations_relation(request_for(user, org, studio))
    grant = grant_studio(member, studio, roles.DEVELOPER)
    assert operations.operations_relation(request_for(member, org, studio)).count() == 0
    grant.delete()
    with pytest.raises(PermissionDenied):
        operations.operations_relation(request_for(member, org, studio))
    with pytest.raises(PermissionDenied):
        operations.operations_relation(request_for(org_admin, org, studio, api_key=SimpleNamespace(org_id=other_org.pk)))
    org_admin.is_active = False
    with pytest.raises(PermissionDenied):
        operations.operations_relation(request_for(org_admin, org, studio))


@pytest.mark.django_db
def test_inactive_org_and_mismatched_studio_denied(org_admin, org, studio, other_studio):
    with pytest.raises(PermissionDenied):
        operations.operations_relation(request_for(org_admin, org, other_studio))
    org.is_active = False
    with pytest.raises(PermissionDenied):
        operations.operations_relation(request_for(org_admin, org, studio))


@pytest.mark.django_db
def test_observed_waiting_current_readiness_and_new_attempt(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug='blocked', config={'data_sources': ['db']})
    RepoDataSource.objects.create(studio=studio, name='db', type='postgres', config={'host': 'localhost'})
    summary(report, now=now, status='error', blocked=['db'])
    request = request_for(org_admin, org, studio)
    payload = operations.operations_page_payload(request, {'state': 'waiting'}, now=now)
    assert payload['counts']['waiting_observed'] == 1 and payload['rows'][0]['waiting_current'] is True
    binding = DataSource.objects.create(studio=studio, name='db', type='postgres', config={'host': 'localhost'}, credentials={'user': 'u', 'password': 's'}, last_check_ok=True, last_check_at=now)
    cleared = operations.operations_page_payload(request, {'state': 'waiting'}, now=now)
    assert cleared['counts']['waiting_observed'] == 1 and cleared['rows'][0]['waiting_current'] is False
    Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.SUCCESS, created_at=now+timedelta(seconds=1))
    after = operations.operations_page_payload(request, {}, now=now+timedelta(seconds=2))
    assert after['counts']['waiting_observed'] == 0 and after['rows'][0]['state'] == 'ok'
    assert 'password' not in json.dumps(cleared, cls=DjangoJSONEncoder)
    assert binding.pk


@pytest.mark.django_db
def test_dependency_overflow_unknown_without_global_decryption(org_admin, org, studio, forbid_io, monkeypatch):
    postgres()
    names = [f'db{i:02}' for i in range(40)]
    report = Report.objects.create(studio=studio, slug='many', config={'data_sources': names})
    summary(report, status='error', blocked=[names[-1]])
    for name in names:
        RepoDataSource.objects.create(studio=studio, name=name, type='postgres', config={'host': 'localhost'})
    seen = []
    original = operations._state
    def observe(name, *args):
        seen.append(name)
        return original(name, *args)
    monkeypatch.setattr(operations, '_state', observe)
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {})
    assert len(set(seen)) <= 25
    row = payload['rows'][0]
    assert row['waiting_current'] is None and row['referenced_count'] == 40 and len(row['references']) == 8
    sources = operations.operations_sources_payload(request_for(org_admin, org, studio), {'source_page': '2'})
    assert sources['pagination']['total'] == 40 and len(sources['rows']) == 15


@pytest.mark.django_db
@pytest.mark.parametrize('runtime', [{}, {'validation': None}, {'validation': {'summary': {}}}, {'validation': {'summary': {'fail': -1}}}, {'validation': {'summary': {'fail': True}}}])
def test_missing_or_malformed_validation_is_unknown(org_admin, org, studio, runtime, forbid_io):
    postgres()
    report = Report.objects.create(studio=studio, slug='missing')
    summary(report, runtime=runtime)
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {})
    assert payload['rows'][0]['validation'] is None
    assert payload['counts']['healthy'] == 0 and payload['counts']['v_unknown'] == 1


@pytest.mark.django_db
def test_validation_filter_covers_full_relation_and_old_checks_keep_last_known(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    for i in range(30):
        report = Report.objects.create(studio=studio, slug=f'r{i:02}', priority=i)
        summary(report, now=now, runtime={'validation': {'summary': {'fail': 1 if i == 29 else 0, 'pass': 1, 'warn': 0}, 'checks': ['secret']}})
    # The age of the last inspection never blanks what the build reported...
    old = Report.objects.create(studio=studio, slug='old')
    summary(old, now=now-timedelta(hours=3))
    # ...but an observation that is not a ready/missing one still says unknown.
    uncertain = Report.objects.create(studio=studio, slug='uncertain')
    summary(uncertain, now=now)
    ReportSourceSummary.objects.filter(report=uncertain).update(state='uncertain')
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {'state': 'v_fail'}, now=now)
    assert payload['pagination']['total'] == 1 and payload['rows'][0]['slug'] == 'r29'
    assert payload['counts']['healthy'] == 30 and payload['counts']['v_unknown'] == 1
    assert 'secret' not in json.dumps(payload, cls=DjangoJSONEncoder)
    healthy = operations.operations_page_payload(request_for(org_admin, org, studio), {'q': 'old'}, now=now)
    assert healthy['rows'][0]['validation']['summary']['pass'] == 1


@pytest.mark.django_db
def test_sources_complete_union_search_shadow_and_bounded_usage(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    RepoDataSource.objects.create(studio=studio, name='shared', type='postgres', config={'host': 'localhost'})
    DataSource.objects.create(org=org, name='shared', type='postgres', config={'host': 'elsewhere'}, credentials={'user': 'o', 'password': 'private-org'})
    scoped = DataSource.objects.create(studio=studio, name='shared', type='postgres', config={'host': 'localhost'}, credentials={'user': 's', 'password': 'private-studio'})
    DataSource.objects.create(studio=other_studio, name='secret-source', type='file')
    for i in range(30):
        Report.objects.create(studio=studio, slug=f'r{i:02}', config={'data_sources': ['shared', {'name': f'ref{i:02}'}]})
    request = request_for(org_admin, org, studio)
    page = operations.operations_sources_payload(request, {})
    assert page['pagination']['total'] == 31 and len(page['rows']) == 25
    shared = operations.operations_sources_payload(request, {'source_q': 'shared'})['rows'][0]
    assert shared['binding_id'] == scoped.pk and shared['used_by_total'] == 30
    assert len(shared['used_by']) == 8 and shared['used_by_complete'] is False
    found = operations.operations_sources_payload(request, {'source_q': 'ref29'})
    assert found['rows'][0]['name'] == 'ref29' and found['rows'][0]['state'] == 'unknown'
    serialized = json.dumps(page, cls=DjangoJSONEncoder)
    assert 'private-' not in serialized and 'secret-source' not in serialized


@pytest.mark.django_db
def test_status_page_bounded_global_active_counts_and_cross_org_slugs(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    slugs = []
    for i in range(30):
        report = Report.objects.create(studio=studio, slug=f'r{i:02}')
        Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.QUEUED)
        slugs.append(report.slug)
    foreign = Report.objects.create(studio=other_studio, slug='foreign')
    Run.objects.create(report=foreign, studio=other_studio, slug=foreign.slug, status=Run.RUNNING)
    request = request_for(org_admin, org, studio)
    payload = operations.operations_status_payload(request, slugs[:24]+['foreign'])
    assert len(payload['rows']) == 24 and payload['active_jobs_total']['queued'] == 30
    assert all(r['slug'] != 'foreign' for r in payload['rows'])
    full = operations.operations_page_payload(request, {'state': 'queued', 'page': '2'})
    assert full['pagination']['total'] == 30 and len(full['rows']) == 5


@pytest.mark.django_db
def test_studio_wide_status_and_stats_cover_every_authorized_report(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    now = timezone.now()
    for i in range(30):
        report = Report.objects.create(studio=studio, slug=f'r{i:02}')
        Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.QUEUED)
        Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.SUCCESS, created_at=now-timedelta(seconds=5), finished_at=now)
    hidden = Report.objects.create(studio=studio, slug='hidden', present_in_scan=False)
    Run.objects.create(report=hidden, studio=studio, slug='hidden', status=Run.RUNNING)
    foreign = Report.objects.create(studio=other_studio, slug='foreign')
    Run.objects.create(report=foreign, studio=other_studio, slug='foreign', status=Run.RUNNING)
    request = request_for(org_admin, org, studio)
    with CaptureQueriesContext(connection) as queries:
        payload = operations.operations_status_payload(request, [], studio_wide=True)
    assert len(payload['rows']) == 30 and {r['slug'] for r in payload['rows']} == {f'r{i:02}' for i in range(30)}
    assert payload['coverage']['active_rows_complete'] and payload['active_jobs_total']['queued'] == 30
    assert payload['server_uptime_seconds'] >= 0 and len(queries) <= 6
    # Without the studio scope an empty selection is still an empty read.
    assert operations.operations_status_payload(request, [])['rows'] == []
    stats = operations.operations_stats_payload(request, [], studio_wide=True)
    assert set(stats['stats']) == {f'r{i:02}' for i in range(30)}
    assert operations.operations_stats_payload(request, [])['stats'] == {}


@pytest.mark.django_db
def test_capped_page_resolves_every_source_it_lists(org_admin, org, studio, forbid_io, monkeypatch):
    postgres()
    for i in range(40):
        name = f'db{i:02}'
        report = Report.objects.create(studio=studio, slug=f'r{i:02}', priority=i, config={'data_sources': [name]})
        summary(report, status='error', blocked=[name])
        RepoDataSource.objects.create(studio=studio, name=name, type='postgres', config={'host': 'localhost'})
    request = request_for(org_admin, org, studio)
    with CaptureQueriesContext(connection) as queries:
        payload = operations.operations_page_payload(request, {'page_size': '500'})
    assert len(payload['rows']) == 40 and payload['pagination'] == dict(page=1, page_size=500, total=40, pages=1)
    # Past the default page's 25 sources, readiness is still known for the 40th row.
    assert payload['rows'][-1]['waiting_current'] is not None
    assert len(queries) <= 30
    default = operations.operations_page_payload(request, {})
    assert len(default['rows']) == 25 and default['pagination']['page_size'] == 25
    sources = operations.operations_sources_payload(request, {'page_size': '500'})
    assert len(sources['rows']) == 40 and sources['pagination']['pages'] == 1


@pytest.mark.django_db
def test_stats_optional_selection_preserves_legacy_and_excludes_output(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    for slug, peak in [('a', 100), ('b', None)]:
        report = Report.objects.create(studio=studio, slug=slug)
        Run.objects.create(report=report, studio=studio, slug=slug, status=Run.SUCCESS, created_at=now-timedelta(seconds=12), started_at=now-timedelta(seconds=10), finished_at=now, peak_memory_mb=peak, stdout_tail='private-output'*10000)
    legacy = studio_run_stats(studio)
    with CaptureQueriesContext(connection) as queries:
        selected = studio_run_stats(studio, slugs=['a'])
    assert selected == {'a': legacy['a']} and len(queries) == 2
    latest_sql = queries[-1]['sql'].lower()
    assert 'stdout_tail' not in latest_sql and 'stderr_tail' not in latest_sql and 'log_dir' not in latest_sql
    with CaptureQueriesContext(connection) as queries:
        assert studio_run_stats(studio, slugs=[]) == {}
    assert len(queries) == 0 and legacy['b']['last']['peak_memory_mb'] is None
    payload = operations.operations_stats_payload(request_for(org_admin, org, studio), ['a', 'foreign'])
    assert payload['stats'] == selected and payload['window_days'] == 30


@pytest.mark.django_db
def test_developer_and_denied_read_ceilings(member, org, studio, grant_studio, forbid_io):
    postgres()
    grant = grant_studio(member, studio, roles.DEVELOPER)
    report = Report.objects.create(studio=studio, slug='report')
    summary(report)
    with CaptureQueriesContext(connection) as queries:
        page = operations.operations_page_payload(request_for(member, org, studio), {})
    assert len(queries) <= 36 and page['pagination']['total'] == 1
    grant.delete()
    with CaptureQueriesContext(connection) as queries:
        with pytest.raises(PermissionDenied):
            operations.operations_page_payload(request_for(member, org, studio), {})
    assert len(queries) <= 20


@pytest.mark.django_db
def test_sources_huge_malformed_reference_is_bounded_unknown(org_admin, org, studio, forbid_io):
    postgres()
    name = 'invalid-' + '\u0000'*200000
    # JSONB cannot contain NUL, but a huge ordinary malformed name must not
    # cross the database connection as the entire original reference.
    name = name.replace('\u0000', 'x')
    Report.objects.create(studio=studio, slug='report', config={'data_sources': [name]})
    with CaptureQueriesContext(connection) as queries:
        result = operations.operations_sources_payload(request_for(org_admin, org, studio), {})
    row = result['rows'][0]
    assert row['state'] == 'unknown' and row['name_truncated'] is True and len(row['name']) == 200
    assert row['identity']['name'] is None and row['identity']['reference_digest']
    assert result['pagination']['total'] == 1 and len(queries) <= 30
    assert len(json.dumps(result, cls=DjangoJSONEncoder).encode()) <= operations.RESPONSE_BYTES


@pytest.mark.django_db
def test_expired_output_and_disabled_waiting_never_claim_current_availability(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug='expired', disabled=True, data_expired_at=now, config={'data_sources': ['missing']})
    summary(report, now=now, status='error', blocked=['missing'])
    row = operations.operations_page_payload(request_for(org_admin, org, studio), {}, now=now)['rows'][0]
    assert row['has_output'] is False and row['html_entry'] is None and row['waiting_current'] is False


@pytest.mark.django_db
def test_future_and_incompatible_summary_never_claim_known_validation_or_output(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    for i, (version, offset) in enumerate([(1, 0), (2, 60)]):
        report = Report.objects.create(studio=studio, slug=f'r{i}')
        observation = summary(report, now=now+timedelta(seconds=offset))
        observation.schema_version = version
        observation.save(update_fields=['schema_version'])
    result = operations.operations_page_payload(request_for(org_admin, org, studio), {}, now=now)
    assert result['counts']['healthy'] == 0 and result['counts']['v_unknown'] == 2
    assert all(row['has_output'] is None and row['validation'] is None for row in result['rows'])


@pytest.mark.django_db
def test_complete_source_usage_paging_is_not_report_name_search(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    for i in range(30):
        report = Report.objects.create(studio=studio, slug=f'r{i:02}', name='No source name here', config={'data_sources': ['db', 'db']})
        summary(report, status='error', blocked=['db'])
    Report.objects.create(studio=other_studio, slug='foreign', config={'data_sources': ['db']})
    request = request_for(org_admin, org, studio)
    page = operations.operations_source_reports_payload(request, 'db', {'page': '2'})
    assert page['pagination']['total'] == 30 and len(page['rows']) == 5
    assert [row['slug'] for row in page['rows']] == [f'r{i:02}' for i in range(25, 30)]
    blocked = operations.operations_source_reports_payload(request, 'db', {'page': '2'}, observed_blocks=True)
    assert blocked['mode'] == 'last-observed-blocks' and blocked['rows'] == page['rows']
    with pytest.raises(ValueError):
        operations.operations_source_reports_payload(request, 'db/../secret', {})


@pytest.mark.django_db
def test_failed_pill_preserves_oom_timeout_union_and_exclusive_totals(org_admin, org, studio, forbid_io):
    postgres()
    for slug, status in [('error', Run.ERROR), ('oom', Run.OOM_KILLED), ('timeout', Run.TIMEOUT), ('success', Run.SUCCESS)]:
        report = Report.objects.create(studio=studio, slug=slug)
        Run.objects.create(report=report, studio=studio, slug=slug, status=status)
    result = operations.operations_page_payload(request_for(org_admin, org, studio), {'state': 'error'})
    assert result['pagination']['total'] == 3 and result['counts']['error'] == 3
    assert result['counts']['outcomes']['error'] == 1
    assert sum(result['counts']['outcomes'].values()) == result['counts']['total'] == 4
