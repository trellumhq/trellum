"""Failure evidence remains bounded and private independently of the portal."""
import json
from unittest.mock import Mock

import pytest

from trellum import diagnostics as d
from trellum.data import drivers, query, retry
from trellum.data.connections import register_source, forget_source
from trellum.testing.test_query_retry import Connection


@pytest.fixture
def evidence(monkeypatch, tmp_path):
    path = tmp_path / 'diagnostics.json'
    monkeypatch.setenv('TRELLUM_DIAGNOSTICS_PATH', str(path))
    monkeypatch.setattr(retry.time, 'sleep', Mock())
    return path


@pytest.mark.parametrize('boundary', ['connect', 'execute', 'fetch'])
def test_failed_boundary_and_nested_retry_history_are_sanitized(evidence, monkeypatch, boundary):
    error = ConnectionResetError('password=TOPSECRET SELECT * FROM private WHERE token=123')
    # A non-permanent wrapper keeps transport classification while preserving causes.
    error.__cause__ = RuntimeError('TOPSECRET')
    source = 'postgres://user:TOPSECRET@private/warehouse'
    raw = Connection(error if boundary == 'fetch' else None,
                     execute_error=error if boundary == 'execute' else None)
    monkeypatch.setattr(drivers, 'connect', Mock(side_effect=error if boundary == 'connect' else lambda *a: raw))
    with pytest.raises(ConnectionResetError):
        conn = retry.connect_managed('postgres', {})
        register_source(conn, source)
        try:
            query.query_df(conn, 'SELECT n FROM private WHERE token=:token', {'token': 'TOPSECRET'}, cache_ttl=0)
        finally:
            forget_source(conn)
    result = d.read_diagnostics(evidence)
    assert result['stage'] == boundary and result['state'] == 'failed'
    assert result['attempt'] == 3 and result['transient'] is True
    assert result['query_retry_safe'] is True and not result['unsafe_operation']
    assert [item['class'] for item in result['errors']] == ['ConnectionResetError', 'RuntimeError']
    assert len(result['errors'][0]['frames']) <= 3
    assert 'TOPSECRET' not in evidence.read_text()
    assert 'SELECT' not in evidence.read_text() and 'private' not in evidence.read_text()
    assert evidence.stat().st_size <= d.MAX_BYTES


def test_query_identifier_is_unbound_and_failure_then_recovery_clears_transient(evidence, monkeypatch):
    old, fresh = Connection(ConnectionResetError()), Connection()
    monkeypatch.setattr(drivers, 'connect', Mock(side_effect=[old, fresh]))
    conn = retry.connect_managed('postgres', {})
    query.query_df(conn, 'SELECT n FROM t WHERE n=:n', {'n': 1}, cache_ttl=0)
    result = d.read_diagnostics(evidence)
    assert result['query_id'] == d.fingerprint('SELECT n FROM t WHERE n=:n')
    assert result['attempt'] == 2 and result['state'] == 'completed'
    assert result['transient'] is False and result['errors'] == []


def test_active_stage_is_written_before_driver_work(evidence, monkeypatch):
    def factory(*args):
        assert d.read_diagnostics(evidence)['stage'] == 'connect'
        assert d.read_diagnostics(evidence)['state'] == 'active'
        return Connection()
    monkeypatch.setattr(drivers, 'connect', factory)
    conn = retry.connect_managed('postgres', {})
    cursor = conn._inner.cursor()
    def execute(sql):
        result = d.read_diagnostics(evidence)
        assert result['stage'] == 'execute' and result['state'] == 'active'
    def fetch():
        result = d.read_diagnostics(evidence)
        assert result['stage'] == 'fetch' and result['state'] == 'active'
        return [(1,)]
    cursor.execute, cursor.fetchall = execute, fetch
    conn._inner.cursor = lambda: cursor
    assert query.query_df(conn, 'SELECT n FROM t', cache_ttl=0).n.tolist() == [1]


@pytest.mark.parametrize('unsafe', ['write', 'raw'])
def test_unsafe_evidence_survives_later_safe_queries_and_render(evidence, monkeypatch, unsafe):
    monkeypatch.setattr(drivers, 'connect', lambda *a: Connection())
    conn = retry.connect_managed('postgres', {})
    if unsafe == 'write':
        query.query_df(conn, 'UPDATE t SET n=1', cache_ttl=0)
    else:
        conn.cursor()
    query.query_df(conn, 'SELECT n FROM t', cache_ttl=0)
    with d.stage('render', query_retry_safe=False):
        pass
    assert d.read_diagnostics(evidence)['unsafe_operation'] is True


@pytest.mark.parametrize('value', [None, [], {}, {'version': 8, 'stage': 'fetch'},
                                   {'version': 1, 'stage': []}, {'version': 1, 'stage': 'fetch', 'state': []}])
def test_unknown_and_malformed_contract_is_rejected(value):
    assert d.sanitize_diagnostics(value) == {}


def test_contract_allowlist_and_bounds(evidence):
    data = {'version': 1, 'stage': 'fetch', 'state': 'failed', 'source_type': ['secret'],
            'attempt': 10**1000, 'timings': [{'stage': 'fetch', 'elapsed_ms': float('nan')}] * 1000,
            'errors': [{'class': 'Invalid secret SQL', 'code': 'password=secret'}] * 100,
            'sql': 'secret', 'password': 'secret', 'source_id': 'postgres://secret'}
    result = d.sanitize_diagnostics(data)
    assert result['attempt'] == 3 and len(result['timings']) == 16 and len(result['errors']) == 5
    assert result['source_id'] == result['source_type'] == ''
    assert 'secret' not in json.dumps(result)
    evidence.write_bytes(b'x' * (d.MAX_BYTES + 1))
    assert d.read_diagnostics(evidence) == {}
    evidence.write_text('{bad')
    assert d.read_diagnostics(evidence) == {}


def test_missing_unwritable_evidence_never_replaces_error(monkeypatch, tmp_path):
    monkeypatch.setenv('TRELLUM_DIAGNOSTICS_PATH', str(tmp_path / 'absent' / 'file'))
    with pytest.raises(ValueError, match='original'):
        with d.stage('render'):
            raise ValueError('original')


@pytest.mark.heavy
def test_hard_kill_retains_active_stage_without_traceback(evidence):
    import os
    import select
    import subprocess
    import sys
    code = "from trellum.diagnostics import stage; import time;\nwith stage('fetch', query_retry_safe=True):\n print('ready', flush=True); time.sleep(60)"
    process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, env=os.environ.copy())
    try:
        assert select.select([process.stdout], [], [], 10)[0], 'child failed to enter stage'
        assert process.stdout.readline() == b'ready\n'
        process.kill()
        process.wait(timeout=5)
        result = d.read_diagnostics(evidence)
        assert result['stage'] == 'fetch' and result['state'] == 'active'
        assert result['errors'] == []
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize('error', [ValueError('invalid SQL secret'), PermissionError('authentication failed secret'), MemoryError('secret')])
def test_permanent_error_has_no_transient_retry(evidence, monkeypatch, error):
    factory = Mock(return_value=Connection(error))
    monkeypatch.setattr(drivers, 'connect', factory)
    conn = retry.connect_managed('postgres', {})
    with pytest.raises(type(error)):
        query.query_df(conn, 'SELECT n FROM t', cache_ttl=0)
    result = d.read_diagnostics(evidence)
    assert result['transient'] is False and result['attempt'] == 1
    assert factory.call_count == 1 and 'secret' not in evidence.read_text()


@pytest.mark.parametrize('recovery', [True, None, 'true', {'replay_safe': 'true'}, {'replay_safe': 1}])
def test_replay_contract_requires_explicit_boolean(recovery):
    from types import SimpleNamespace
    from trellum.validation.checks.yaml_schema import _check_yaml_schema
    from trellum.validation.result import ValidationResult
    ctx = SimpleNamespace(config={'recovery': recovery}, _connections={})
    result = ValidationResult(slug='test')
    _check_yaml_schema(ctx, result)
    assert any(item.id == 'yaml-recovery-invalid' and item.level == 'fail' for item in result.checks)


def test_render_failure_is_not_confused_with_previous_transport_failure(evidence, tmp_path):
    from trellum.rendering.html_builder import render_report
    class BrokenContext:
        output_dir = str(tmp_path)
        @property
        def theme(self):
            raise ValueError('secret renderer failure')
    with d.stage('fetch', source_type='postgres'):
        pass
    with pytest.raises(ValueError):
        render_report(BrokenContext())
    result = d.read_diagnostics(evidence)
    assert result['stage'] == 'render' and result['state'] == 'failed'
    assert result['transient'] is False and result['source_type'] == ''
    assert result['errors'][0]['class'] == 'ValueError'
    assert 'secret' not in evidence.read_text()
