"""Fresh connection recovery never publishes an incomplete report result."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pandas as pd
import pytest

from trellum.data import connections, drivers, query, retry
from trellum.data.ssh_tunnel import TunnelledConnection, unwrap


class Cursor:
    description = [("n",)]

    def __init__(self, error=None, *, execute_error=None):
        self.error = error
        self.execute_error = execute_error
        self.closes = 0

    def execute(self, sql):
        self.sql = sql
        if self.execute_error:
            raise self.execute_error

    def fetchall(self):
        if self.error:
            # Some rows may have arrived before the transport failed.
            self.partial_rows = [(999,)]
            raise self.error
        return [(1,), (2,)]

    def close(self):
        self.closes += 1


class Connection:
    autocommit = True

    def __init__(self, error=None, **kwargs):
        self.cursors = []
        self.closes = 0
        self.error = error
        self.kwargs = kwargs

    def cursor(self):
        cursor = Cursor(self.error, **self.kwargs)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closes += 1

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", Mock())


def managed(monkeypatch, *connections_or_errors):
    factory = Mock(side_effect=connections_or_errors)
    monkeypatch.setattr(drivers, "connect", factory)
    return retry.connect_managed("postgres", {"password": "secret"}), factory


@pytest.mark.parametrize("stage", ["execute", "fetch"])
def test_query_retry_replaces_connection_preserves_source_and_caches_complete_frame(monkeypatch, tmp_path, stage):
    error = ConnectionResetError("secret SQL")
    failed = Connection(error if stage == "fetch" else None,
                        execute_error=error if stage == "execute" else None)
    replacement = Connection()
    handle, factory = managed(monkeypatch, failed, replacement)
    monkeypatch.setattr(query, "_cache_dir", lambda: tmp_path)
    query.enable_cache()
    connections.register_source(handle, "warehouse")
    try:
        result = query.query_df(handle, "SELECT n FROM t", cache_ttl=60)
        assert result.n.tolist() == [1, 2]
        assert failed.closes == 1 and failed.cursors[0].closes == 1
        assert replacement.cursors[0].closes == 1
        assert connections.source_of(handle) == "warehouse"
        assert pd.read_pickle(next(tmp_path.glob("*.pkl"))).n.tolist() == [1, 2]
        assert query.query_df(handle, "SELECT n FROM t", cache_ttl=60).n.tolist() == [1, 2]
        assert len(replacement.cursors) == 1  # Cache hit does not touch the driver.
        query.query_df(handle, "SELECT n FROM t WHERE n > 0", cache_ttl=0)
        assert len(replacement.cursors) == 2 and factory.call_count == 2
    finally:
        handle.close()
        connections.forget_source(handle)


def test_initial_open_retry_and_safe_logs(monkeypatch, caplog):
    handle, factory = managed(monkeypatch, ConnectionRefusedError("password=secret"), Connection())
    assert factory.call_count == 2
    assert retry.time.sleep.call_args_list[0].args == (1,)
    assert "secret" not in caplog.text and "recovered" in caplog.text
    assert "secret" not in repr(handle)


def test_exact_vertica_fetch_eof_uses_fresh_connection(monkeypatch):
    try:
        from vertica_python.errors import ConnectionError as VerticaError
    except ImportError:
        VerticaError = type("ConnectionError", (Exception,), {"__module__": "vertica_python.errors"})
    old, fresh = Connection(VerticaError("Connection closed by Vertica")), Connection()
    factory = Mock(side_effect=[old, fresh])
    monkeypatch.setattr(drivers, "connect", factory)
    handle = retry.connect_managed("vertica", {})
    assert query.query_df(handle, "SELECT n FROM t", cache_ttl=0).n.tolist() == [1, 2]
    assert old.closes == 1 and old.cursors[0].closes == 1 and factory.call_count == 2


@pytest.mark.parametrize("error", [PermissionError("permission denied"), ValueError("bad configuration"),
                                  KeyboardInterrupt(), SystemExit()])
def test_initial_permanent_failure_or_cancellation_is_not_retried(monkeypatch, error):
    factory = Mock(side_effect=error)
    monkeypatch.setattr(drivers, "connect", factory)
    with pytest.raises(type(error)) as caught:
        retry.connect_managed("postgres", {})
    assert caught.value is error and factory.call_count == 1


def test_connect_reconnect_share_one_budget_and_exhaustion_leaves_no_stale_connection(monkeypatch, tmp_path):
    failed = Connection(ConnectionResetError())
    final = TimeoutError("secret")
    handle, factory = managed(monkeypatch, failed, TimeoutError(), final, Connection())
    monkeypatch.setattr(query, "_cache_dir", lambda: tmp_path)
    with pytest.raises(TimeoutError) as caught:
        query.query_df(handle, "SELECT n FROM t", cache_ttl=60)
    assert caught.value is final
    assert "3 attempts" in final.__notes__[0]
    assert failed.closes == 1 and factory.call_count == 3
    assert retry.time.sleep.call_args_list[0].args == (1,)
    assert retry.time.sleep.call_args_list[1].args == (2,)
    assert not list(tmp_path.glob("*.pkl"))
    query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 4


@pytest.mark.parametrize("error", [ValueError("bad SQL"), PermissionError("permission denied"),
                                  MemoryError(), KeyboardInterrupt(), SystemExit()])
def test_permanent_or_cancelled_queries_never_reconnect(monkeypatch, error):
    raw = Connection(error)
    handle, factory = managed(monkeypatch, raw)
    with pytest.raises(type(error)) as caught:
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert caught.value is error and factory.call_count == 1
    assert raw.cursors[0].closes == 1


@pytest.mark.parametrize("sql", [
    "UPDATE t SET n = 1", "SELECT n FROM t; SELECT n FROM t", "SELECT n INTO t2 FROM t",
    "WITH changed AS (DELETE FROM t RETURNING n) SELECT n FROM changed",
    "SELECT n FROM t FOR UPDATE", "BEGIN", "SET timezone = 'UTC'",
    "SELECT n FROM t FOR KEY SHARE",
    "SELECT pg_advisory_lock(1)", "SELECT 'back\\slash'", "SELECT $$value$$", "SELECT 1 # comment",
    "SELECT n FROM t /*! INTO OUTFILE '/tmp/export' */",
    "SELECT n FROM t /*M! INTO OUTFILE '/tmp/export' */",
])
def test_stateful_and_unsupported_sql_is_not_replayed(monkeypatch, sql):
    handle, factory = managed(monkeypatch, Connection(ConnectionResetError()))
    with pytest.raises(ConnectionResetError):
        query.query_df(handle, sql, cache_ttl=0)
    assert factory.call_count == 1


@pytest.mark.parametrize("sql", [
    "SELECT n FROM t", "-- DELETE\nSELECT n FROM t;", "SELECT 'a; DELETE ''x''' FROM t",
    "SELECT n FROM t WHERE day = DATE '2026-10-09' /* UPDATE */",
    'WITH rows AS (SELECT "update" FROM t) SELECT * FROM rows',
])
def test_ordinary_reads_are_replay_candidates(sql):
    assert retry._can_replay(sql)


@pytest.mark.parametrize("access", [lambda c: c.cursor(), lambda c: c.commit(), lambda c: c.rollback(),
                                     lambda c: setattr(c, "autocommit", False), lambda c: c.__enter__()])
def test_manual_session_access_disables_replay(monkeypatch, access):
    raw = Connection(ConnectionResetError())
    handle, factory = managed(monkeypatch, raw)
    access(handle)
    with pytest.raises(ConnectionResetError):
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 1


def test_property_reads_do_not_disable_replay(monkeypatch):
    handle, factory = managed(monkeypatch, Connection(ConnectionResetError()), Connection())
    assert handle.autocommit is True
    query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 2


def test_successful_stateful_query_disables_later_read_replay(monkeypatch):
    raw = Connection()
    handle, factory = managed(monkeypatch, raw)
    query.query_df(handle, "SET search_path = reporting", cache_ttl=0)
    raw.error = ConnectionResetError()
    with pytest.raises(ConnectionResetError):
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 1


def test_managed_dialect_survives_exhaustion_and_binding(monkeypatch):
    fresh = Connection()
    factory = Mock(side_effect=[Connection(ConnectionResetError()), TimeoutError(), TimeoutError(), fresh])
    monkeypatch.setattr(drivers, "connect", factory)
    handle = retry.connect_managed("bigquery", {})
    with pytest.raises(TimeoutError):
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert query._sql_dialect(handle) == "googlesql"
    query.query_df(handle, "SELECT :name", {"name": "O'Brien"}, cache_ttl=0)
    # GoogleSQL backslash literals are conservatively not replayed, but bind correctly.
    assert factory.call_count == 4
    assert fresh.cursors[0].sql == "SELECT 'O\\'Brien'"


def test_google_sql_triple_quotes_are_conservatively_not_replayed():
    assert not retry._can_replay("SELECT '''value'''", "bigquery")


def test_close_is_terminal_and_idempotent(monkeypatch):
    raw = Connection()
    handle, factory = managed(monkeypatch, raw)
    handle.close()
    handle.close()
    with pytest.raises(RuntimeError, match="explicitly closed"):
        query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert raw.closes == 1 and factory.call_count == 1 and handle._factory is None
    assert handle.closed


def test_failed_explicit_close_still_clears_factory_and_is_terminal(monkeypatch):
    raw = Connection()
    raw.close = Mock(side_effect=ValueError("close"))
    handle, factory = managed(monkeypatch, raw)
    with pytest.raises(ValueError):
        handle.close()
    handle.close()
    with pytest.raises(RuntimeError, match="explicitly closed"):
        retry.run_with_retry(handle, lambda raw: raw, sql="SELECT 1")
    assert handle._factory is None and factory.call_count == 1
    raw.close.assert_called_once()


@pytest.mark.parametrize("source", ["sqlite", "duckdb"])
def test_embedded_connections_are_native_and_never_replayed(monkeypatch, source):
    raw = Connection(ConnectionResetError())
    factory = Mock(return_value=raw)
    monkeypatch.setattr(drivers, "connect", factory)
    assert retry.connect_managed(source, {"path": ":memory:"}) is raw
    with pytest.raises(ConnectionResetError):
        query.query_df(raw, "SELECT n FROM t", cache_ttl=0)
    assert factory.call_count == 1


@pytest.mark.parametrize("stage", ["execute", "fetch"])
def test_cursor_cleanup_error_does_not_mask_driver_failure(stage):
    error = ConnectionResetError()
    raw = Connection(error if stage == "fetch" else None,
                     execute_error=error if stage == "execute" else None)
    cursor = raw.cursor()
    cursor.close = Mock(side_effect=ValueError("cleanup"))
    raw.cursor = lambda: cursor
    with pytest.raises(ConnectionResetError) as caught:
        query._execute_query(raw, "SELECT 1")
    assert caught.value is error and cursor.close.call_count == 1


def test_ssh_replacement_closes_tunnel_and_preserves_unwrap(monkeypatch):
    failed, fresh = Connection(ConnectionResetError()), Connection()
    first_tunnel, second_tunnel = Mock(), Mock()
    handle, factory = managed(monkeypatch, TunnelledConnection(failed, first_tunnel),
                              TunnelledConnection(fresh, second_tunnel))
    assert unwrap(handle) is failed
    query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    first_tunnel.close.assert_called_once()
    assert unwrap(handle) is fresh and factory.call_count == 2
    handle.close()
    second_tunnel.close.assert_called_once()


def test_native_factory_reopens_ssh_tunnel_and_driver(monkeypatch):
    first_tunnel, second_tunnel = Mock(local_port=1001), Mock(local_port=1002)
    tunnel_factory = Mock(side_effect=[first_tunnel, second_tunnel])
    monkeypatch.setattr("trellum.data.ssh_tunnel.SSHTunnel", tunnel_factory)
    driver = Mock(default_port=5433)
    driver.connect.side_effect = [Connection(ConnectionResetError()), Connection()]
    monkeypatch.setattr(drivers, "get_driver", lambda name: driver)
    handle = retry.connect_managed("vertica", {"host": "db", "ssh_host": "bastion"})
    query.query_df(handle, "SELECT n FROM t", cache_ttl=0)
    assert tunnel_factory.call_count == driver.connect.call_count == 2
    assert driver.connect.call_args_list[0].args[0] == {"host": "127.0.0.1", "port": 1001}
    assert driver.connect.call_args_list[1].args[0] == {"host": "127.0.0.1", "port": 1002}
    first_tunnel.close.assert_called_once()
    handle.autocommit = False
    assert unwrap(handle).autocommit is False
    handle.close()
    second_tunnel.close.assert_called_once()


@pytest.mark.parametrize("module,method", [("google.cloud.bigquery.client", "query"),
                                           ("clickhouse_connect.driver.client", "query_df")])
def test_managed_native_clients_still_dispatch_and_retry(monkeypatch, module, method):
    client_type = type("Client", (), {"__module__": module})
    clients = [client_type(), client_type()]
    expected = pd.DataFrame({"n": [1]})
    for index, client in enumerate(clients):
        client.close = Mock()
        response = Mock(to_dataframe=Mock(return_value=expected)) if method == "query" else expected
        setattr(client, method, Mock(side_effect=ConnectionResetError()) if index == 0 else Mock(return_value=response))
    handle, factory = managed(monkeypatch, *clients)
    pd.testing.assert_frame_equal(query.query_df(handle, "SELECT n FROM t", cache_ttl=0), expected)
    assert factory.call_count == 2
    clients[0].close.assert_called_once()


def test_snowflake_fetch_pandas_error_closes_cursor_and_retries(monkeypatch):
    connection_type = type("Connection", (Connection,), {"__module__": "snowflake.connector.connection"})
    clients = [connection_type(), connection_type()]
    expected = pd.DataFrame({"n": [1]})
    cursors = [Cursor(), Cursor()]
    cursors[0].fetch_pandas_all = Mock(side_effect=ConnectionResetError())
    cursors[1].fetch_pandas_all = Mock(return_value=expected)
    for client, cursor in zip(clients, cursors):
        client.cursor = Mock(return_value=cursor)
    handle, factory = managed(monkeypatch, *clients)
    pd.testing.assert_frame_equal(query.query_df(handle, "SELECT n FROM t", cache_ttl=0), expected)
    assert [cursor.closes for cursor in cursors] == [1, 1]
    assert factory.call_count == 2


def test_concurrent_queries_cannot_use_connection_during_replacement(monkeypatch):
    failed, fresh = Connection(), Connection()
    handle, factory = managed(monkeypatch, failed, fresh)
    entered, release, second_started, second_entered = (threading.Event() for _ in range(4))

    def first_operation(raw):
        if raw is failed:
            entered.set()
            assert release.wait(2)
            raise ConnectionResetError()
        return raw

    def second_query():
        second_started.set()
        return retry.run_with_retry(handle, lambda raw: (second_entered.set(), raw)[1], sql="SELECT 1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(retry.run_with_retry, handle, first_operation, sql="SELECT 1")
        assert entered.wait(2)
        second = pool.submit(second_query)
        assert second_started.wait(2)
        assert not second_entered.wait(0.05)
        release.set()
        assert first.result(2) is fresh and second.result(2) is fresh
    assert failed.closes == 1 and factory.call_count == 2
