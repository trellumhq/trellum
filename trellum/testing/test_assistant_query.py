"""Pure PostgreSQL AST policy tests; never open a customer connection."""
import asyncio
import subprocess
from unittest.mock import AsyncMock

import pytest

from trellum.data.assistant_query import (
    QueryExecutionError,
    QueryRejected,
    _connection_options,
    _metadata_query,
    _resolve_address,
    _watch,
    execute_query,
    validate_query,
)

GRANTS = ['analytics.sales', 'analytics.users', '"Mixed"."Order"']


@pytest.mark.parametrize('query', [
    'select * from analytics.sales',
    'SELECT s.id, u.name FROM analytics.sales s JOIN analytics.users u ON s.id=u.id',
    "SELECT date_trunc('month', d) AS month, count(DISTINCT id), sum(amount) "
    'FROM analytics.sales GROUP BY 1 HAVING sum(amount)>0 ORDER BY 1 DESC LIMIT 12',
    'WITH X AS (SELECT * FROM analytics.sales) SELECT count(*) FROM x',
    'WITH x AS (SELECT id FROM analytics.sales), y AS (SELECT * FROM x) SELECT * FROM y',
    'SELECT * FROM (WITH x AS (SELECT * FROM analytics.sales) SELECT * FROM x) y',
    'WITH x AS (SELECT 1 id) SELECT * FROM x WHERE EXISTS (SELECT 1 FROM analytics.sales s WHERE s.id=x.id)',
    'SELECT id FROM analytics.sales UNION ALL SELECT id FROM analytics.users',
    'SELECT id FROM analytics.sales INTERSECT SELECT id FROM analytics.users',
    'SELECT id FROM analytics.sales EXCEPT SELECT id FROM analytics.users',
    'SELECT sum(amount) OVER (PARTITION BY id ORDER BY d ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM analytics.sales',
    'SELECT row_number() OVER (ORDER BY id), lag(amount) OVER (ORDER BY id) FROM analytics.sales',
    'SELECT sum(amount) FILTER (WHERE amount > 0) FROM analytics.sales',
    'SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY amount) FROM analytics.sales',
    'SELECT CASE WHEN id>0 THEN coalesce(amount,0) ELSE null END / nullif(id,0) FROM analytics.sales',
    "SELECT extract(year FROM d), d::date, current_date - INTERVAL '1 day' FROM analytics.sales",
    'SELECT lower(name), upper(name), length(name), substring(name,1,2) FROM analytics.users',
    'SELECT "ID" FROM "Mixed"."Order"',
    "SELECT 'DELETE; pg_sleep(1)' AS message /* drop table */ FROM analytics.sales;",
    'WITH "Sales" AS (SELECT 1 x) SELECT * FROM "Sales"',
    'WITH sales AS (SELECT 1 x) SELECT * FROM analytics.sales',
    'SELECT "sum"(amount) FROM analytics.sales',
    'SELECT power(2,3), bool_and(id>0), stddev(amount) FROM analytics.sales',
    'SELECT 1',
])
def test_valid_analytical_queries(query):
    effective = validate_query(query, GRANTS)
    assert effective
    assert validate_query(effective, GRANTS) == effective


@pytest.mark.parametrize('query', [
    '', ';', 'SELECT 1; SELECT 2', 'SELECT 1;;', '-- SELECT 1', 'SELECT \ud800',
    'DELETE FROM analytics.sales', 'UPDATE analytics.sales SET id=1 RETURNING id',
    'WITH x AS (DELETE FROM analytics.sales RETURNING *) SELECT * FROM x',
    'WITH x AS (INSERT INTO analytics.sales VALUES (1) RETURNING *) SELECT * FROM x',
    'SELECT * INTO public.new_table FROM analytics.sales',
    'SELECT * FROM analytics.sales FOR UPDATE', 'SELECT * FROM analytics.sales FOR SHARE',
    'COPY (SELECT * FROM analytics.sales) TO PROGRAM \'id\'',
    "SET search_path=public", 'EXPLAIN SELECT * FROM analytics.sales',
    'SELECT * FROM sales', 'SELECT * FROM hidden.sales',
    'SELECT * FROM analytics.sales WHERE id IN (SELECT id FROM hidden.sales)',
    'WITH x AS (SELECT * FROM hidden.sales) SELECT * FROM x',
    'WITH x AS (SELECT 1) SELECT * FROM hidden.x',
    'WITH sales AS (SELECT 1) SELECT * FROM hidden.sales',
    'WITH x AS (SELECT 1) SELECT * FROM (WITH x AS (SELECT * FROM hidden.sales) SELECT * FROM x) y',
    'SELECT * FROM analytics.sales UNION SELECT * FROM hidden.sales',
    'SELECT * FROM otherdb.analytics.sales',
    'SELECT * FROM pg_catalog.pg_authid', 'SELECT * FROM information_schema.tables',
    'SELECT * FROM generate_series(1,1000000000)',
    'WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x',
    "SELECT pg_read_file('/etc/passwd')", "SELECT pg_ls_dir('/')",
    "SELECT dblink('password=secret', 'SELECT 1')", 'SELECT pg_sleep(1)',
    "SELECT set_config('search_path','public',false)", 'SELECT nextval(\'secret\')',
    'SELECT pg_cancel_backend(1)', 'SELECT public.sum(amount) FROM analytics.sales',
    'SELECT pg_catalog.sum(amount) FROM analytics.sales',
    'SELECT custom_function(id) FROM analytics.sales',
    "SELECT '1'::public.evil_type", "SELECT 'pg_authid'::regclass",
    "SELECT CAST('x' AS regprocedure)", 'SELECT amount OPERATOR(public.+) id FROM analytics.sales',
    'SELECT amount COLLATE public.custom FROM analytics.sales',
    'SELECT * FROM analytics.sales TABLESAMPLE SYSTEM(10)',
    'SELECT * FROM ONLY analytics.sales',
    'SELECT unnest(ARRAY[1,2])', 'SELECT * FROM analytics.sales s, LATERAL public.evil(s.id)',
    'SELECT xmlparse(document \'<a/>\')',
])
def test_rejects_attacks_and_unsupported_features(query):
    with pytest.raises(QueryRejected):
        validate_query(query, GRANTS)


@pytest.mark.parametrize('grants', [[], ['sales'], ['a.b.c'], ['pg_catalog.pg_roles'],
    ['information_schema.columns'], ['a.b JOIN a.c ON true'], ['a.b WHERE true'],
    ['a.b; SELECT 1'], ['a.b AS b'], [None], ['a.' + 'x'*64], ['a.b, a.c']])
def test_grants_are_explicit_relations(grants):
    with pytest.raises(QueryRejected):
        validate_query('SELECT 1', grants)


def test_quoted_cte_names_and_relations_do_not_alias_hidden_relations():
    assert '"Mixed"."Order"' in validate_query('select * from "Mixed"."Order"', GRANTS)
    with pytest.raises(QueryRejected):
        validate_query('select * from mixed.order', GRANTS)
    with pytest.raises(QueryRejected):
        validate_query('WITH "X" AS (SELECT 1) SELECT * FROM x', GRANTS)


def test_size_and_depth_bounds():
    for query in ('SELECT ' + '1+'*9000 + '1', 'SELECT ' + '('*100 + '1' + ')'*100):
        with pytest.raises(QueryRejected):
            validate_query(query, GRANTS)


def test_normalization_removes_comments_preserves_literal_data():
    assert validate_query("SELECT 'a;b' /*secret*/ FROM analytics.sales;", GRANTS) == 'SELECT \'a;b\' FROM "analytics"."sales"'


def test_metadata_is_fixed_and_literals_are_escaped():
    query = _metadata_query({("a' OR true --", 'b')})
    assert "'a'' OR true --'" in query
    assert '"information_schema"."columns"' in query
    with pytest.raises(QueryRejected):
        validate_query(query, GRANTS)


def test_connection_policy(monkeypatch):
    info = dict(host='localhost', database='synthetic', user='reader', password='mock')
    for extra in ({'ssh_host': 'localhost'}, {'options': '-c role=postgres'},
                  {'service': 'other'}, {'host': 'localhost,elsewhere'},
                  {'database': 'host=elsewhere'}, {'port': 0}):
        with pytest.raises(QueryRejected):
            _connection_options(info | extra, 2)
    result = _connection_options(info, 2)
    assert 'statement_timeout=2000' in result['options']
    assert 'search_path=pg_catalog' in result['options']
    assert 'default_transaction_read_only=on' in result['options']
    assert _connection_options(info | {'host': 'source.example'}, 2)['sslmode'] == 'verify-full'
    with pytest.raises(QueryRejected, match='verified TLS'):
        _connection_options(info | {'host': 'source.example', 'sslmode': 'disable'}, 2)
    monkeypatch.setenv('PGOPTIONS', '-c role=postgres')
    with pytest.raises(QueryRejected):
        _connection_options(info, 2)


@pytest.mark.parametrize('bounds', [{'max_rows': 0}, {'max_rows': True}, {'max_rows': 1001},
    {'max_bytes': 1}, {'max_bytes': 1048577}, {'timeout_seconds': float('nan')},
    {'timeout_seconds': 0}, {'timeout_seconds': 61}])
def test_invalid_bounds_fail_before_connect(bounds):
    with pytest.raises(QueryRejected):
        execute_query({}, 'SELECT 1', GRANTS, **bounds)


def test_driver_errors_are_safe(monkeypatch):
    async def fail(*args):
        raise RuntimeError('password=super-secret')
    monkeypatch.setattr('trellum.data.assistant_query._execute', fail)
    with pytest.raises(QueryExecutionError, match='^PostgreSQL query failed.$') as error:
        execute_query(dict(host='localhost', database='synthetic', user='reader', password='mock'), 'SELECT 1', GRANTS)
    assert error.value.__suppress_context__


def test_cancellation_waits_for_confirmation(monkeypatch):
    monkeypatch.setattr(asyncio, "wait_for", AsyncMock(side_effect=TimeoutError))
    async def check():
        conn = AsyncMock()
        async def operation():
            await asyncio.sleep(10)
        with pytest.raises(QueryExecutionError, match='cancellation is unconfirmed'):
            # Simulate a server that ignores the cancellation request. Keep the
            # timeout short by making wait_for itself fail immediately.
            await _watch(conn, operation(), 0, None)
        conn.close.assert_awaited_once()
    asyncio.run(check())


def test_hostname_resolution_is_bounded_and_safe(monkeypatch):
    from unittest.mock import Mock
    resolver = Mock(side_effect=subprocess.TimeoutExpired('resolver', 1))
    monkeypatch.setattr(subprocess, 'run', resolver)
    assert _resolve_address({'host': '127.0.0.1', 'port': 5432}, 1) == '127.0.0.1'
    resolver.assert_not_called()
    with pytest.raises(QueryExecutionError, match='^PostgreSQL address resolution failed.$'):
        _resolve_address({'host': 'invalid.example', 'port': 5432}, 1)
    assert resolver.call_args.kwargs['timeout'] == 1


def test_cancellation_before_execute_does_not_start_work():
    async def check():
        conn = AsyncMock()
        operation = AsyncMock()
        with pytest.raises(QueryExecutionError, match='^Query cancelled.$'):
            await _watch(conn, operation(), 0, lambda: True)
        operation.assert_not_awaited()
        conn.cancel_safe.assert_not_awaited()
    asyncio.run(check())
