"""Opt-in real PostgreSQL tests, using a disposable synthetic-only container."""
import json
import shutil
import subprocess
import time
import uuid

import pytest

from trellum.data.assistant_query import (
    QueryExecutionError,
    QueryRejected,
    describe_source,
    execute_query,
)

pytestmark = pytest.mark.heavy


@pytest.fixture(scope='module')
def source():
    import psycopg
    if not shutil.which('docker'):
        pytest.skip('Docker is required for isolated PostgreSQL security tests')
    name = 'trellum-assistant-query-test-' + uuid.uuid4().hex[:12]
    subprocess.run(['docker', 'run', '-d', '--rm', '--name', name,
                    '-e', 'POSTGRES_PASSWORD=synthetic-admin', '-e', 'POSTGRES_DB=assistant_test',
                    '-p', '127.0.0.1::5432', 'postgres:17-alpine'], check=True, capture_output=True)
    try:
        port = int(subprocess.check_output(['docker', 'port', name, '5432/tcp'], text=True).strip().rsplit(':', 1)[1])
        admin = dict(host='127.0.0.1', port=port, dbname='assistant_test', user='postgres', password='synthetic-admin')
        end = time.monotonic() + 20
        while True:
            try:
                conn = psycopg.connect(**admin, autocommit=True, connect_timeout=2)
                break
            except psycopg.OperationalError:
                if time.monotonic() > end:
                    raise
                time.sleep(.1)
        with conn:
            conn.execute("CREATE ROLE assistant_reader LOGIN PASSWORD 'synthetic-reader'")
            conn.execute("CREATE ROLE bypass_reader LOGIN BYPASSRLS PASSWORD 'synthetic-bypass'")
            conn.execute("CREATE ROLE writable_reader LOGIN PASSWORD 'synthetic-writer'")
            conn.execute('CREATE SCHEMA analytics; CREATE SCHEMA hidden')
            conn.execute('CREATE TABLE analytics.sales (id integer, amount numeric, day date, note text)')
            conn.execute("INSERT INTO analytics.sales SELECT i, 123456789.123456789, DATE '2026-01-01', 'text ' || i FROM generate_series(1,500) i")
            conn.execute('CREATE TABLE hidden.secret (id int); INSERT INTO hidden.secret VALUES(999)')
            conn.execute('CREATE TABLE analytics.rls_sales (id int); INSERT INTO analytics.rls_sales VALUES(1),(2)')
            conn.execute('ALTER TABLE analytics.rls_sales ENABLE ROW LEVEL SECURITY')
            conn.execute('CREATE POLICY reader_scope ON analytics.rls_sales USING (id=1)')
            conn.execute("CREATE FUNCTION public.slow(i int) RETURNS int LANGUAGE plpgsql AS $$BEGIN PERFORM pg_sleep(5); RETURN i; END$$")
            conn.execute('CREATE VIEW analytics.slow_view AS SELECT public.slow(id) AS id FROM analytics.sales')
            conn.execute("CREATE DOMAIN analytics.custom AS text; CREATE TABLE analytics.custom_types (value analytics.custom)")
            conn.execute("CREATE FUNCTION public.lower(text) RETURNS text LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'custom function ran'; END$$")
            conn.execute("CREATE VIEW analytics.long_row AS SELECT repeat('雪', 1000000) AS note")
            conn.execute('GRANT USAGE ON SCHEMA analytics TO assistant_reader, bypass_reader, writable_reader')
            conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO assistant_reader, bypass_reader, writable_reader')
            conn.execute('GRANT INSERT ON analytics.sales TO writable_reader')
        reader = {key: value for key, value in admin.items() if key != 'dbname'}
        reader.update(database='assistant_test', user='assistant_reader', password='synthetic-reader')
        yield reader, admin
    finally:
        subprocess.run(['docker', 'stop', '-t', '1', name], check=True, capture_output=True)


def test_reads_aggregates_precision_dates_duplicates_empty_and_evidence(source):
    reader, _ = source
    result = execute_query(reader, 'SELECT count(DISTINCT id), sum(amount), min(day) FROM analytics.sales', ['analytics.sales'])
    assert result['columns'] == ['count', 'sum', 'min']
    assert result['rows'] == [[500, '61728394561.728394500', '2026-01-01']]
    assert not result['truncated']
    assert result['executed_at'].endswith('+00:00')
    assert isinstance(result['elapsed_ms'], int)
    json.dumps(result, allow_nan=False)
    result = execute_query(reader, 'SELECT id AS same, amount AS same FROM analytics.sales ORDER BY id LIMIT 1', ['analytics.sales'])
    assert result['columns'] == ['same', 'same']
    assert result['rows'] == [[1, '123456789.123456789']]
    result = execute_query(reader, 'SELECT * FROM analytics.sales WHERE false', ['analytics.sales'])
    assert result['rows'] == []
    assert result['columns'] == ['id', 'amount', 'day', 'note']


def test_enforces_rows_bytes_and_large_cells_before_driver_materialization(source):
    reader, _ = source
    result = execute_query(reader, 'SELECT * FROM analytics.sales ORDER BY id', ['analytics.sales'], max_rows=3)
    assert len(result['rows']) == 3 and result['truncated']
    result = execute_query(reader, 'SELECT * FROM analytics.sales', ['analytics.sales'], max_rows=1000, max_bytes=1024)
    assert result['truncated'] and len(json.dumps(result).encode()) <= 1024
    result = execute_query(reader, 'SELECT * FROM analytics.long_row', ['analytics.long_row'], max_bytes=1024)
    assert result['rows'] == [] and result['truncated']


def test_rejects_privileged_writable_and_custom_type_identities(source):
    reader, admin = source
    superuser = dict(reader, user=admin['user'], password=admin['password'])
    bypass = dict(reader, user='bypass_reader', password='synthetic-bypass')
    writable = dict(reader, user='writable_reader', password='synthetic-writer')
    for info in (superuser, bypass, writable):
        with pytest.raises(QueryRejected, match='dedicated read-only'):
            execute_query(info, 'SELECT * FROM analytics.sales', ['analytics.sales'])
    with pytest.raises(QueryRejected, match='column types'):
        execute_query(reader, 'SELECT * FROM analytics.custom_types', ['analytics.custom_types'])


def test_rls_and_catalog_metadata_are_scoped_and_public_functions_cannot_shadow(source):
    reader, _ = source
    assert execute_query(reader, 'SELECT * FROM analytics.rls_sales', ['analytics.rls_sales'])['rows'] == [[1]]
    result = describe_source(reader, ['analytics.sales'])
    assert {r[1] for r in result['rows']} == {'sales'}
    assert {r[2] for r in result['rows']} == {'id', 'amount', 'day', 'note'}
    assert describe_source(reader, ['analytics.custom_types'])['rows'][0][2] == 'value'
    result = execute_query(reader, 'SELECT lower(note) FROM analytics.sales LIMIT 1', ['analytics.sales'])
    assert result['rows'] == [['text 1']]


def test_server_deadline_and_callback_cancel_are_confirmed_remote(source):
    import psycopg
    reader, admin = source
    pids = []
    with pytest.raises(QueryExecutionError, match='timed out|cancelled'):
        execute_query(reader, 'SELECT * FROM analytics.slow_view', ['analytics.slow_view'],
                      timeout_seconds=.3, on_started=pids.append)
    started = time.monotonic()
    callback_seen = []
    def on_started(pid):
        pids.append(pid)
        callback_seen.append(time.monotonic())
    with pytest.raises(QueryExecutionError, match='Query cancelled'):
        execute_query(reader, 'SELECT * FROM analytics.slow_view', ['analytics.slow_view'],
                      timeout_seconds=10, on_started=on_started,
                      cancelled=lambda: bool(callback_seen) and time.monotonic()-callback_seen[0] > .15)
    assert time.monotonic() - started < 3
    assert len(pids) == 2 and all(isinstance(pid, int) for pid in pids)
    with psycopg.connect(**admin, autocommit=True) as conn:
        assert conn.execute('SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE pid = ANY(%s) AND state=\'active\'', (pids,)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM analytics.sales').fetchone()[0] == 500


def test_readonly_transaction_and_restricted_search_path_are_active(source):
    import psycopg
    reader, admin = source
    # Trusted test view exposes session facts without allowing these functions
    # in model SQL. This proves the server boundary independently of the AST.
    with psycopg.connect(**admin, autocommit=True) as conn:
        conn.execute("CREATE VIEW analytics.session_facts AS SELECT current_setting('transaction_read_only') AS ro, current_setting('statement_timeout') AS timeout, current_setting('lock_timeout') AS locks, current_setting('search_path') AS path")
        conn.execute('GRANT SELECT ON analytics.session_facts TO assistant_reader')
    result = execute_query(reader, 'SELECT * FROM analytics.session_facts', ['analytics.session_facts'], timeout_seconds=2)
    assert result['rows'] == [['on', '2s', '1s', 'pg_catalog']]


def test_errors_never_expose_driver_messages(source):
    reader, _ = source
    with pytest.raises(QueryExecutionError, match='^PostgreSQL query failed.$'):
        execute_query(reader, 'SELECT missing_private_column FROM analytics.sales', ['analytics.sales'])
    with pytest.raises(QueryExecutionError, match='^PostgreSQL query failed.$'):
        execute_query(dict(reader, password='secret-that-must-not-leak'), 'SELECT 1', ['analytics.sales'])


def test_server_timeout_stops_work_without_the_client_watchdog(source, monkeypatch):
    reader, _ = source
    async def without_watchdog(conn, operation, deadline, cancelled):
        return await operation
    monkeypatch.setattr('trellum.data.assistant_query._watch', without_watchdog)
    with pytest.raises(QueryExecutionError, match='cancelled by PostgreSQL'):
        execute_query(reader, 'SELECT * FROM analytics.slow_view', ['analytics.slow_view'], timeout_seconds=.2)


def test_database_readonly_boundary_rejects_actual_writes(source):
    import psycopg

    from trellum.data.assistant_query import _connection_options
    reader, admin = source
    info = dict(reader, user=admin['user'], password=admin['password'])
    with psycopg.connect(**_connection_options(info, 2)) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute('INSERT INTO analytics.sales (id) VALUES (999)')


def test_relation_definition_is_locked_until_execution_finishes(source):
    import psycopg
    reader, admin = source
    def check_lock(pid):
        with psycopg.connect(**admin, autocommit=True) as conn:
            conn.execute("SET lock_timeout='100ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                conn.execute('ALTER TABLE analytics.sales ADD COLUMN unexpected_custom_type text')
    assert execute_query(reader, 'SELECT count(*) FROM analytics.sales', ['analytics.sales'], on_started=check_lock)['rows'] == [[500]]
