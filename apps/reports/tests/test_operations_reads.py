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
from apps.core.pagination import page_metadata
from apps.reports import operations
from apps.reports.registry_rows import _safe_entry
from apps.reports.catalog import bounded_payload
from apps.reports.models import Report, ReportSourceSummary
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
    monkeypatch.setattr(scan, 'sync_studio_registry', forbidden)
    monkeypatch.setattr(source_summary, 'reconcile', forbidden)


@pytest.mark.parametrize('query', [
    'page=0', 'page=-1', 'page=1.0', 'page=1e2', 'page=1000001', 'page=1&page=2',
    'page_size=0', 'page_size=501', 'page_size=1.5', 'page_size=-1', 'page_size=10&page_size=20',
    'q=a&q=b', 'q=%00', 'source_q=a&source_q=b',
])
def test_reject_ambiguous_or_hostile_parameters(query):
    with pytest.raises(ValueError):
        operations.validate_operations_params(QueryDict(query))


def test_input_caps_are_explicit():
    for params in ({'q': 'x'*1001}, {'source_q': 'x'*1001}, {'padding': 'x'*32769}):
        with pytest.raises(ValueError):
            operations.validate_operations_params(params)
    params = operations.validate_operations_params({'q': '界'*1000, 'page': '1000000'})
    assert params.q == '界'*1000 and params.page == 1000000
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
    assert _safe_entry(entry) == ''


def test_oversized_response_is_never_false_empty():
    result = bounded_payload(dict(version=1, state='ready', rows=[{'description': '\x00'*200000}], pagination={'total': 42}), operations.RESPONSE_BYTES)
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
    assert len(first['rows']) == 25 and first['pagination'] == page_metadata(1, 25, 33)
    assert set(first) == {'version', 'state', 'rows', 'pagination', 'observed_at'}
    assert len(queries) <= 24
    second = operations.operations_page_payload(request, {'page': '2'})
    assert {r['slug'] for r in second['rows']} >= {'metrics', 'analysis'}
    assert [r['slug'] for r in second['rows'] if r['metrics_report']] == ['metrics']
    found = operations.operations_page_payload(request, {'q': 'Report 30'})
    assert [r['slug'] for r in found['rows']] == ['r30'] and found['pagination']['total'] == 1
    assert len(json.dumps(first, cls=DjangoJSONEncoder).encode()) <= operations.RESPONSE_BYTES


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
def test_waiting_ends_with_its_reason_and_with_a_new_attempt(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug='blocked', config={'data_sources': ['db']})
    RepoDataSource.objects.create(studio=studio, name='db', type='postgres', config={'host': 'localhost'})
    summary(report, now=now, status='error', blocked=['db'])
    request = request_for(org_admin, org, studio)
    row = operations.operations_page_payload(request, {}, now=now)['rows'][0]
    assert row['waiting'] is True and row['blocked_by'] == ['db'] and row['build_status'] == 'error'
    binding = DataSource.objects.create(studio=studio, name='db', type='postgres', config={'host': 'localhost'}, credentials={'user': 'u', 'password': 's'}, last_check_ok=True, last_check_at=now)
    cleared = operations.operations_page_payload(request, {}, now=now)
    assert cleared['rows'][0]['waiting'] is False and cleared['rows'][0]['blocked_by'] == []
    Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.SUCCESS, created_at=now+timedelta(seconds=1), finished_at=now+timedelta(seconds=2))
    after = operations.operations_page_payload(request, {}, now=now+timedelta(seconds=3))['rows'][0]
    assert after['last_status'] == 'success' and after['build_status'] == 'success' and after['waiting'] is False
    assert 'password' not in json.dumps(cleared, cls=DjangoJSONEncoder)
    assert binding.pk


@pytest.mark.django_db
def test_dependency_overflow_keeps_the_observed_wait_without_global_decryption(org_admin, org, studio, forbid_io, monkeypatch):
    postgres()
    from apps.reports import registry_rows
    names = [f'db{i:02}' for i in range(40)]
    report = Report.objects.create(studio=studio, slug='many', config={'data_sources': names})
    summary(report, status='error', blocked=[names[-1]])
    for name in names:
        RepoDataSource.objects.create(studio=studio, name=name, type='postgres', config={'host': 'localhost'})
    seen = []
    original = registry_rows._state
    def observe(name, *args):
        seen.append(name)
        return original(name, *args)
    monkeypatch.setattr(registry_rows, '_state', observe)
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {})
    assert len(set(seen)) <= registry_rows.DEPENDENCY_CAP
    # More references than one row resolves: the observed wait stands.
    row = payload['rows'][0]
    assert row['waiting'] is True and row['blocked_by'] == [names[-1]]
    sources = operations.operations_sources_payload(request_for(org_admin, org, studio), {'page_size': '25'})
    assert sources['pagination']['total'] == 40 and len(sources['rows']) == 25


@pytest.mark.django_db
@pytest.mark.parametrize('runtime', [{}, {'validation': None}, {'validation': {'summary': {}}}, {'validation': {'summary': {'fail': -1}}}, {'validation': {'summary': {'fail': True}}}])
def test_missing_or_malformed_validation_is_unknown(org_admin, org, studio, runtime, forbid_io):
    postgres()
    report = Report.objects.create(studio=studio, slug='missing')
    summary(report, runtime=runtime)
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {})
    assert payload['rows'][0]['validation'] is None


@pytest.mark.django_db
def test_old_observations_keep_last_known_validation_but_uncertain_ones_do_not(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    report = Report.objects.create(studio=studio, slug='r29', priority=1)
    summary(report, now=now, runtime={'validation': {'summary': {'fail': 1, 'pass': 1, 'warn': 0}, 'checks': ['secret']}})
    # The age of the last inspection never blanks what the build reported...
    old = Report.objects.create(studio=studio, slug='old', priority=2)
    summary(old, now=now-timedelta(hours=3))
    # ...but an observation that is not a ready/missing one still says unknown.
    uncertain = Report.objects.create(studio=studio, slug='uncertain', priority=3)
    summary(uncertain, now=now)
    ReportSourceSummary.objects.filter(report=uncertain).update(state='uncertain')
    payload = operations.operations_page_payload(request_for(org_admin, org, studio), {}, now=now)
    rows = {row['slug']: row for row in payload['rows']}
    assert rows['r29']['validation'] == {'summary': {'fail': 1, 'pass': 1, 'warn': 0}}
    assert rows['old']['validation']['summary']['pass'] == 1
    assert rows['uncertain']['validation'] is None and rows['uncertain']['has_output'] is None
    assert 'secret' not in json.dumps(payload, cls=DjangoJSONEncoder)


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
    assert page['pagination'] == page_metadata(1, 25, 31) and len(page['rows']) == 25
    shared = operations.operations_sources_payload(request, {'source_q': 'shared'})['rows'][0]
    assert shared['binding_id'] == scoped.pk and shared['used_by_total'] == 30
    assert len(shared['used_by']) == 8 and shared['used_by_complete'] is False
    found = operations.operations_sources_payload(request, {'source_q': 'ref29'})
    assert found['rows'][0]['name'] == 'ref29' and found['rows'][0]['state'] == 'unknown'
    serialized = json.dumps(page, cls=DjangoJSONEncoder)
    assert 'private-' not in serialized and 'secret-source' not in serialized


@pytest.mark.django_db
def test_status_lists_active_runs_of_this_studios_reports_only(org_admin, org, studio, other_studio, forbid_io):
    postgres()
    for i in range(30):
        report = Report.objects.create(studio=studio, slug=f'r{i:02}')
        Run.objects.create(report=report, studio=studio, slug=report.slug, status=Run.QUEUED)
    foreign = Report.objects.create(studio=other_studio, slug='foreign')
    Run.objects.create(report=foreign, studio=other_studio, slug=foreign.slug, status=Run.RUNNING)
    request = request_for(org_admin, org, studio)
    payload = operations.operations_status_payload(request)
    assert len(payload['rows']) == 30 and payload['active_jobs_total']['queued'] == 30
    assert all(r['slug'] != 'foreign' for r in payload['rows'])


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
        payload = operations.operations_status_payload(request)
    assert len(payload['rows']) == 30 and {r['slug'] for r in payload['rows']} == {f'r{i:02}' for i in range(30)}
    assert payload['coverage']['active_rows_complete'] and payload['active_jobs_total']['queued'] == 30
    assert payload['server_uptime_seconds'] >= 0 and len(queries) <= 6
    stats = operations.operations_stats_payload(request)
    assert set(stats['stats']) == {f'r{i:02}' for i in range(30)}


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
    assert len(payload['rows']) == 40 and payload['pagination'] == page_metadata(1, 500, 40)
    assert len(queries) <= 24
    default = operations.operations_page_payload(request, {})
    assert len(default['rows']) == 25 and default['pagination']['per_page'] == 25
    sources = operations.operations_sources_payload(request, {'page_size': '500'})
    assert len(sources['rows']) == 40 and sources['pagination'] == page_metadata(1, 500, 40)


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
    payload = operations.operations_stats_payload(request_for(org_admin, org, studio))
    assert payload['stats'] == legacy and payload['window_days'] == 30


@pytest.mark.django_db
def test_developer_and_denied_read_ceilings(member, org, studio, grant_studio, forbid_io):
    postgres()
    grant = grant_studio(member, studio, roles.DEVELOPER)
    report = Report.objects.create(studio=studio, slug='report')
    summary(report)
    with CaptureQueriesContext(connection) as queries:
        page = operations.operations_page_payload(request_for(member, org, studio), {})
    assert len(queries) <= 30 and page['pagination']['total'] == 1
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
    assert row['has_output'] is False and row['html_entry'] == '' and row['waiting'] is False and row['blocked_by'] == []


@pytest.mark.django_db
def test_paused_report_has_pause_and_no_next_run(org_admin, org, studio, forbid_io):
    postgres()
    now = timezone.now()
    kwargs = dict(studio=studio, schedule_cron='0 6 * * *', schedule_timezone='UTC')
    Report.objects.create(slug='paused', refresh_suspended_at=now - timedelta(days=3), **kwargs)
    Report.objects.create(slug='active', **kwargs)
    rows = {r['slug']: r for r in operations.operations_page_payload(request_for(org_admin, org, studio), {}, now=now)['rows']}
    assert rows['paused']['refresh_suspended_at'] == (now - timedelta(days=3)).isoformat() and rows['paused']['next_run'] is None
    assert rows['active']['refresh_suspended_at'] is None and rows['active']['next_run'] > now


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
    assert all(row['has_output'] is None and row['validation'] is None and row['last_status'] == 'not_run' for row in result['rows'])
