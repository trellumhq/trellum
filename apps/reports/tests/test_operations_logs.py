"""Real files and PostgreSQL contracts for opt-in Operations log helpers."""
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.http import Http404
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core import roles
from apps.reports import operations_logs as logs
from apps.reports.models import Report
from apps.runner.models import Run


def request_for(user, org, studio, **extra):
    request = RequestFactory().get('/')
    request.user, request.org, request.studio = user, org, studio
    for key, value in extra.items():
        setattr(request, key, value)
    return request


def postgres():
    assert connection.vendor == 'postgresql', 'Operations log gate requires PostgreSQL'


def assert_no_full_tails(queries):
    raw = re.compile(r'(?:SELECT\s+|,\s+)"[^"]+"\."(?:stdout_tail|stderr_tail)"(?:,|\s+FROM)')
    assert all(not raw.search(row['sql']) for row in queries)


def run_for(report, **values):
    return Run.objects.create(report=report, studio=report.studio, slug=report.slug,
                              status=values.pop('status', Run.SUCCESS), **values)


@pytest.fixture
def live_files(settings, tmp_path):
    settings.DATA_DIR = tmp_path / 'data'
    identity = uuid4()
    directory = settings.DATA_DIR / 'tmp' / 'runs' / str(identity)
    directory.mkdir(parents=True)
    for name in logs.STREAMS:
        (directory / (name + '.log')).write_bytes(b'')
    return {'id': identity, 'log_dir': str(directory)}, directory


@pytest.fixture
def descriptors(monkeypatch):
    original_open, original_close, original_read = os.open, os.close, os.pread
    active, sizes = set(), []
    peak = [0]

    def opened(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        active.add(descriptor)
        peak[0] = max(peak[0], len(active))
        return descriptor

    def closed(descriptor):
        try:
            return original_close(descriptor)
        finally:
            active.discard(descriptor)

    def read(descriptor, size, offset):
        sizes.append(size)
        return original_read(descriptor, size, offset)

    monkeypatch.setattr(os, 'open', opened)
    monkeypatch.setattr(os, 'close', closed)
    monkeypatch.setattr(os, 'pread', read)
    monkeypatch.setattr(os, 'supports_dir_fd', os.supports_dir_fd | {opened})
    return active, peak, sizes


@pytest.mark.parametrize('value', [None, True, 1, '1', '', 'x'*36, '../' + 'a'*33, uuid4().hex])
def test_uuid_selection_rejects_ambiguous_or_path_inputs(value):
    with pytest.raises(ValueError):
        logs._uuid(value)


def test_readable_empty_files_are_quiet_and_never_create_directories(live_files, descriptors, monkeypatch):
    run, directory = live_files
    monkeypatch.setattr(Path, 'mkdir', lambda *args, **kwargs: pytest.fail('Log read attempted directory creation'))
    texts, originals, truncated = logs._live_tails(run)
    assert texts == ['', ''] and originals == [0, 0] and truncated == [False, False]
    assert sorted(path.name for path in directory.iterdir()) == ['stderr.log', 'stdout.log']
    assert not descriptors[0] and descriptors[1][0] <= 2


@pytest.mark.parametrize('failure', ['missing', 'file_symlink', 'directory_symlink', 'parent_symlink', 'fifo', 'wrong_path'])
def test_unavailable_files_never_become_quiet_or_follow_symlinks(live_files, descriptors, tmp_path, failure):
    run, directory = live_files
    target = directory / 'stdout.log'
    if failure == 'missing':
        target.unlink()
    elif failure == 'file_symlink':
        secret = tmp_path / 'private.txt'
        secret.write_text('must never be read')
        target.unlink()
        target.symlink_to(secret)
    elif failure == 'directory_symlink':
        moved = directory.with_name('moved')
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif failure == 'parent_symlink':
        parent = directory.parents[1]
        moved = parent.with_name('moved-parent')
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
    elif failure == 'fifo':
        target.unlink()
        os.mkfifo(target)
    else:
        run['log_dir'] = str(directory.parent / '..' / 'runs' / directory.name)
    with pytest.raises((OSError, logs._Unavailable)):
        logs._live_tails(run)
    assert not descriptors[0] and descriptors[1][0] <= 2
    assert descriptors[2] == []


def test_replacement_between_stat_and_open_is_rejected_without_read(live_files, descriptors, monkeypatch):
    run, directory = live_files
    original = os.open

    def swapped(path, *args, **kwargs):
        if path == 'stdout.log':
            (directory / 'stdout.log').rename(directory / 'old.log')
            (directory / 'stdout.log').write_bytes(b'must not be read')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, 'open', swapped)
    monkeypatch.setattr(os, 'supports_dir_fd', os.supports_dir_fd | {swapped})
    with pytest.raises(logs._Unavailable):
        logs._live_tails(run)
    assert descriptors[2] == []
    assert not descriptors[0] and descriptors[1][0] <= 2


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_large_live_output_is_bounded_before_read_and_reuses_quiet_quota(live_files, descriptors, stream):
    run, directory = live_files
    data = ('界😀' * 40000 + 'final marker').encode('utf-8')
    (directory / (stream + '.log')).write_bytes(data)
    texts, originals, truncated = logs._live_tails(run)
    index = logs.STREAMS.index(stream)
    assert texts[index].endswith('final marker') and '\ufffd' not in texts[index]
    assert originals[index] == len(data) and truncated[index]
    assert sum(len(text.encode('utf-8')) for text in texts) <= logs.TAIL_BYTES
    assert sum(descriptors[2]) <= logs.TAIL_BYTES
    assert max(descriptors[2]) == logs.TAIL_BYTES
    assert not descriptors[0] and descriptors[1][0] <= 2


def test_invalid_live_bytes_preserve_replacement_then_obey_utf8_and_json_caps(live_files, descriptors):
    run, directory = live_files
    (directory / 'stdout.log').write_bytes(b'\xff' * 200000 + b'last')
    (directory / 'stderr.log').write_bytes(b'\x01' * 200000 + b'error')
    texts, originals, clipped = logs._live_tails(run)
    assert '\ufffd' in texts[0]
    payload = logs._fit({'version': 1}, texts, originals, clipped)
    assert payload['stdout'].endswith('last') and payload['stderr'].endswith('error')
    assert sum(row['returned_bytes'] for row in payload['streams'].values()) <= logs.TAIL_BYTES
    assert all(row['truncated'] for row in payload['streams'].values())
    assert len(json.dumps(payload, cls=DjangoJSONEncoder).encode('utf-8')) <= logs.RESPONSE_BYTES
    assert sum(descriptors[2]) <= logs.TAIL_BYTES
    assert not descriptors[0] and descriptors[1][0] <= 2


@pytest.mark.parametrize('rotation', ['file', 'directory', 'truncate', 'remove'])
def test_rotation_during_read_rejects_obsolete_output_and_closes_fds(live_files, descriptors, monkeypatch, rotation):
    run, directory = live_files
    target = directory / 'stdout.log'
    target.write_bytes(b'old output')
    original = os.pread
    changed = False

    def rotated(descriptor, size, offset):
        nonlocal changed
        result = original(descriptor, size, offset)
        if not changed:
            changed = True
            if rotation == 'file':
                target.rename(directory / 'old.log')
                target.write_bytes(b'new output')
            elif rotation == 'directory':
                directory.rename(directory.with_name('old-directory'))
                directory.mkdir()
                for name in logs.STREAMS:
                    (directory / (name + '.log')).write_bytes(b'new output')
            elif rotation == 'truncate':
                target.write_bytes(b'')
            else:
                target.unlink()
        return result

    monkeypatch.setattr(os, 'pread', rotated)
    with pytest.raises((OSError, logs._Unavailable)):
        logs._live_tails(run)
    assert not descriptors[0] and descriptors[1][0] <= 2


def test_appending_live_writer_is_observed_without_reading_growth(live_files, descriptors, monkeypatch):
    run, directory = live_files
    target = directory / 'stdout.log'
    target.write_bytes(b'current output')
    original = os.pread

    def append(descriptor, size, offset):
        result = original(descriptor, size, offset)
        with target.open('ab') as output:
            output.write(b'next output' * 10000)
        return result

    monkeypatch.setattr(os, 'pread', append)
    texts, originals, clipped = logs._live_tails(run)
    assert texts == ['current output', '']
    assert originals == [len(b'current output'), 0] and clipped == [False, False]
    assert sum(descriptors[2]) == len(b'current output')
    assert not descriptors[0] and descriptors[1][0] <= 2


@pytest.mark.django_db
def test_pg_large_unicode_tails_transfer_bytes_in_sql_and_clip_json(org_admin, org, studio):
    postgres()
    report = Report.objects.create(studio=studio, slug='unicode')
    stdout, stderr = '😀'*100000 + 'stdout-end', '\x01'*200000 + 'stderr-end'
    run = run_for(report, stdout_tail=stdout, stderr_tail=stderr)
    request = request_for(org_admin, org, studio)
    with CaptureQueriesContext(connection) as queries:
        payload = logs.operations_log_payload(request, report.slug, run_id=str(run.pk))
    tail_queries = [row['sql'] for row in queries if 'convert_to(' in row['sql']]
    assert len(tail_queries) == 1 and 'substring(' in tail_queries[0] and 'octet_length(' in tail_queries[0]
    assert_no_full_tails(queries)
    assert payload['authority'] == 'persisted-db-tail' and payload['availability'] == 'available'
    assert payload['stdout'].endswith('stdout-end') and payload['stderr'].endswith('stderr-end')
    assert payload['streams']['stdout']['original_bytes'] == len(stdout.encode('utf-8'))
    assert payload['streams']['stderr']['original_bytes'] == len(stderr.encode('utf-8'))
    assert all(row['truncated'] for row in payload['streams'].values())
    assert sum(row['returned_bytes'] for row in payload['streams'].values()) <= logs.TAIL_BYTES
    assert len(json.dumps(payload, cls=DjangoJSONEncoder).encode('utf-8')) <= logs.RESPONSE_BYTES
    # Assert the PostgreSQL-to-Python transfer itself, not merely final output.
    row = Run.objects.filter(pk=run.pk).annotate(
        a=logs.SharedByteTail('stdout_tail', 'stderr_tail'), b=logs.SharedByteTail('stderr_tail', 'stdout_tail'),
    ).values('a', 'b').get()
    assert len(row['a']) + len(row['b']) <= logs.TAIL_BYTES


@pytest.mark.django_db
@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_pg_quiet_stream_lends_unused_byte_quota(org_admin, org, studio, stream):
    postgres()
    report = Report.objects.create(studio=studio, slug='quota')
    run = run_for(report, **{stream + '_tail': '😀'*20000 + 'last'})
    payload = logs.operations_log_payload(request_for(org_admin, org, studio), report.slug)
    assert payload[stream].endswith('last') and '\ufffd' not in payload[stream]
    # JSON's escaped Unicode budget can tighten presentation independently;
    # PostgreSQL still lends the full unused raw-byte quota before transfer.
    other = logs.STREAMS[1-logs.STREAMS.index(stream)]
    raw = Run.objects.filter(pk=run.pk).annotate(part=logs.SharedByteTail(stream + '_tail', other + '_tail')).values('part').get()
    assert len(raw['part']) == logs.TAIL_BYTES
    assert payload['streams'][stream]['returned_bytes'] > logs.TAIL_BYTES // 2
    assert payload['streams'][stream]['truncated']
    assert payload['streams'][other]['returned_bytes'] == 0


@pytest.mark.django_db
def test_pg_empty_persisted_run_is_quiet_and_last_never_opens_files(org_admin, org, studio, monkeypatch):
    postgres()
    report = Report.objects.create(studio=studio, slug='quiet')
    run_for(report)
    monkeypatch.setattr(logs, '_live_tails', lambda *args: pytest.fail('Persisted read opened live files'))
    payload = logs.operations_log_payload(request_for(org_admin, org, studio), report.slug)
    assert payload['state'] == 'ready' and payload['availability'] == 'quiet'
    assert payload['stdout'] == payload['stderr'] == ''


@pytest.mark.django_db
def test_authorization_scope_revocation_api_key_and_absent_reports(org_admin, member, org, studio, other_org, other_studio, grant_studio, monkeypatch):
    postgres()
    report = Report.objects.create(studio=studio, slug='scoped')
    run = run_for(report)
    foreign = Report.objects.create(studio=other_studio, slug='scoped')
    foreign_run = run_for(foreign)
    monkeypatch.setattr(logs, '_live_tails', lambda *args: pytest.fail('Denied request opened storage'))
    for extra in ({}, {'api_key': SimpleNamespace(org_id=other_org.pk)}):
        user = member if not extra else org_admin
        with pytest.raises(PermissionDenied):
            logs.operations_log_payload(request_for(user, org, studio, **extra), report.slug, mode='live')
    grant = grant_studio(member, studio, roles.DEVELOPER)
    assert logs.operations_log_payload(request_for(member, org, studio), report.slug)['run']['id'] == str(run.pk)
    grant.delete()
    with pytest.raises(PermissionDenied):
        logs.operations_log_payload(request_for(member, org, studio), report.slug)
    with pytest.raises(Http404):
        logs.operations_log_payload(request_for(org_admin, org, studio), report.slug, run_id=str(foreign_run.pk))
    report.present_in_scan = False
    report.save(update_fields=['present_in_scan'])
    with pytest.raises(Http404):
        logs.operations_log_payload(request_for(org_admin, org, studio), report.slug)


@pytest.mark.django_db
def test_stable_run_uuid_latest_ties_and_report_rename(org_admin, org, studio):
    postgres()
    report = Report.objects.create(studio=studio, slug='old')
    when = timezone.now()
    first = run_for(report, id=UUID(int=1), created_at=when, stdout_tail='first')
    second = run_for(report, id=UUID(int=2), created_at=when, stdout_tail='second')
    request = request_for(org_admin, org, studio)
    assert logs.operations_log_payload(request, 'old')['run']['id'] == str(second.pk)
    assert logs.operations_log_payload(request, 'old', run_id=str(first.pk))['stdout'] == 'first'
    report.slug = 'new'
    report.save(update_fields=['slug'])
    assert logs.operations_log_payload(request_for(org_admin, org, studio), 'new', run_id=str(first.pk))['stdout'] == 'first'
    with pytest.raises(Http404):
        logs.operations_log_payload(request_for(org_admin, org, studio), 'old', run_id=str(first.pk))


@pytest.mark.django_db
@pytest.mark.parametrize('change', ['status', 'log_dir', 'report_visibility', 'permission'])
def test_live_result_fenced_against_run_report_and_grant_changes(member, org, studio, grant_studio, live_files, monkeypatch, change):
    postgres()
    grant = grant_studio(member, studio, roles.DEVELOPER)
    report = Report.objects.create(studio=studio, slug='live')
    identity, directory = live_files
    run = run_for(report, id=identity['id'], status=Run.RUNNING, log_dir=identity['log_dir'])
    (directory / 'stdout.log').write_text('must not commit after change')
    original = logs._live_tails

    def changing(row):
        result = original(row)
        if change == 'status':
            Run.objects.filter(pk=run.pk).update(status=Run.SUCCESS)
        elif change == 'log_dir':
            Run.objects.filter(pk=run.pk).update(log_dir='different')
        elif change == 'report_visibility':
            Report.objects.filter(pk=report.pk).update(present_in_scan=False)
        else:
            grant.delete()
        return result

    monkeypatch.setattr(logs, '_live_tails', changing)
    request = request_for(member, org, studio)
    if change == 'permission':
        with pytest.raises(PermissionDenied):
            logs.operations_log_payload(request, report.slug, mode='live')
    else:
        payload = logs.operations_log_payload(request, report.slug, mode='live')
        assert payload['state'] == 'unavailable' and payload['stdout'] is None


@pytest.mark.django_db
def test_live_quiet_differs_from_unavailable_and_persisted_fallback(org_admin, org, studio, live_files):
    postgres()
    report = Report.objects.create(studio=studio, slug='live')
    identity, directory = live_files
    run = run_for(report, id=identity['id'], status=Run.RUNNING, log_dir=identity['log_dir'])
    request = request_for(org_admin, org, studio)
    quiet = logs.operations_log_payload(request, report.slug, mode='live')
    assert quiet['availability'] == 'quiet' and quiet['stdout'] == quiet['stderr'] == ''
    (directory / 'stderr.log').unlink()
    unavailable = logs.operations_log_payload(request, report.slug, mode='live')
    assert unavailable['availability'] == 'unavailable' and unavailable['stdout'] is None
    with pytest.raises(Http404):
        logs.operations_persisted_tail_stream(request, report.slug, run_id=str(run.pk))


@pytest.mark.django_db
def test_complete_persisted_stream_is_exact_bounded_single_pass_and_has_no_disk_authority(org_admin, org, studio, monkeypatch):
    postgres()
    report = Report.objects.create(studio=studio, slug='download')
    stdout, stderr = '😀'*100000 + '\nlast stdout', '界'*100000 + '\nlast stderr'
    run = run_for(report, stdout_tail=stdout, stderr_tail=stderr, log_dir='/unsupported/disk/logs')
    monkeypatch.setattr(logs, '_live_tails', lambda *args: pytest.fail('Persisted download accessed disk'))
    stream = logs.operations_persisted_tail_stream(request_for(org_admin, org, studio), report.slug, run_id=str(run.pk))
    with CaptureQueriesContext(connection) as queries:
        chunks = list(stream)
    text = b''.join(chunks).decode('utf-8')
    expected = (stream.label + '\n\n--- stdout (complete persisted tail) ---\n' + stdout
                + '\n--- stderr (complete persisted tail) ---\n' + stderr)
    assert text == expected
    assert max(map(len, chunks)) <= logs.TAIL_BYTES
    assert stream.run['id'] == str(run.pk) and str(run.pk) in stream.filename
    assert_no_full_tails(queries)
    assert any('SUBSTRING(' in row['sql'].upper() for row in queries)
    with pytest.raises(logs.OperationsLogChanged):
        list(stream)


@pytest.mark.django_db
@pytest.mark.parametrize('change', ['status', 'tail_size', 'report_visibility', 'permission'])
def test_stream_rechecks_selection_before_body_and_between_chunks(member, org, studio, grant_studio, change):
    postgres()
    grant = grant_studio(member, studio, roles.DEVELOPER)
    report = Report.objects.create(studio=studio, slug='stream')
    run = run_for(report, stdout_tail='first'*20000)
    stream = logs.operations_persisted_tail_stream(request_for(member, org, studio), report.slug, run_id=str(run.pk))
    iterator = iter(stream)
    assert next(iterator).decode().startswith('Complete persisted')
    assert next(iterator).decode().startswith('\n--- stdout')
    assert len(next(iterator)) <= logs.TAIL_BYTES
    if change == 'status':
        Run.objects.filter(pk=run.pk).update(status=Run.RUNNING)
    elif change == 'tail_size':
        Run.objects.filter(pk=run.pk).update(stdout_tail='changed')
    elif change == 'report_visibility':
        Report.objects.filter(pk=report.pk).update(present_in_scan=False)
    else:
        grant.delete()
    with pytest.raises((logs.OperationsLogChanged, PermissionDenied)):
        next(iterator)
    iterator.close()


@pytest.mark.django_db
def test_download_requires_uuid_and_checks_identity_before_first_body(org_admin, org, studio):
    postgres()
    report = Report.objects.create(studio=studio, slug='before-body')
    run = run_for(report, stdout_tail='original')
    request = request_for(org_admin, org, studio)
    with pytest.raises(ValueError):
        logs.operations_persisted_tail_stream(request, report.slug, run_id=None)
    stream = logs.operations_persisted_tail_stream(request, report.slug, run_id=str(run.pk))
    Run.objects.filter(pk=run.pk).update(stdout_tail='replacement')
    with pytest.raises(logs.OperationsLogChanged):
        next(iter(stream))
