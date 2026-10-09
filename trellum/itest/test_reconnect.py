"""Prove report recovery after interrupting only this test's PostgreSQL session."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from engines import ENGINES

from trellum.data import drivers
from trellum.data.connections import source_of
from trellum.data.drivers import connect
from trellum.data.query import query_df
from trellum.report import ReportContext


def test_report_continues_on_a_fresh_connection_after_disconnect(monkeypatch, tmp_path, caplog):
    info = ENGINES["postgres"].conn_info()
    monkeypatch.setattr("trellum.data.connections.resolve_credentials", lambda source: info)
    context = ReportContext(
        {"data_sources": [{"name": "retry_test", "type": "postgres"}]},
        "retry-test", str(tmp_path),
    )
    admin = connect("postgres", info)
    admin.autocommit = True
    try:
        conn = context.get_connection("retry_test")
        completed = query_df(conn, "SELECT pg_backend_pid() AS pid, 17 AS value", cache_ttl=0)
        original_pid = int(completed.iloc[0]["pid"])
        sql = "SELECT pg_backend_pid() AS pid, pg_sleep(1), 42 AS value"
        with caplog.at_level(logging.WARNING, logger="trellum.data.retry"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                running = executor.submit(query_df, conn, sql, cache_ttl=0)
                deadline = time.monotonic() + 5
                with admin.cursor() as cursor:
                    while time.monotonic() < deadline:
                        cursor.execute(
                            "SELECT state, query FROM pg_stat_activity WHERE pid = %s",
                            (original_pid,),
                        )
                        activity = cursor.fetchone()
                        if activity and activity[0] == "active" and activity[1] == sql:
                            cursor.execute("SELECT pg_terminate_backend(%s)", (original_pid,))
                            assert cursor.fetchone()[0] is True
                            break
                        time.sleep(0.01)
                    else:
                        raise AssertionError("The owned test query did not become active")
                recovered = running.result(timeout=10)
        replacement_pid = int(recovered.iloc[0]["pid"])
        assert replacement_pid != original_pid
        assert int(recovered.iloc[0]["value"]) == 42
        assert int(completed.iloc[0]["value"]) == 17
        assert context.get_connection("retry_test") is conn
        assert source_of(conn) == "retry_test"
        following = query_df(conn, "SELECT pg_backend_pid() AS pid", cache_ttl=0)
        assert int(following.iloc[0]["pid"]) == replacement_pid
        assert "Retrying datasource with fresh connection" in caplog.text
        assert "Datasource recovered" in caplog.text
    finally:
        context.close_connections()
        admin.close()


def test_report_opens_and_closes_a_distinct_connection_for_each_query(monkeypatch, tmp_path):
    info = ENGINES["postgres"].conn_info()
    monkeypatch.setattr("trellum.data.connections.resolve_credentials", lambda source: info)
    opened = []
    native_connect = drivers.connect

    def track_connect(source_type, conn_info):
        raw = native_connect(source_type, conn_info)
        opened.append(raw)
        return raw

    monkeypatch.setattr(drivers, "connect", track_connect)
    context = ReportContext(
        {"data_sources": [{"name": "fresh_test", "type": "postgres",
                           "new_connection_per_query": True}]},
        "fresh-test", str(tmp_path),
    )
    try:
        conn = context.get_connection("fresh_test")
        assert opened == []
        first = query_df(conn, "SELECT pg_backend_pid() AS pid, 17 AS value", cache_ttl=0)
        assert len(opened) == 1 and opened[0].closed
        second = query_df(conn, "SELECT pg_backend_pid() AS pid, 42 AS value", cache_ttl=0)
        assert len(opened) == 2 and all(raw.closed for raw in opened)
        assert int(first.iloc[0]["pid"]) != int(second.iloc[0]["pid"])
        assert int(first.iloc[0]["value"]) == 17
        assert int(second.iloc[0]["value"]) == 42
        assert context.get_connection("fresh_test") is conn
        assert source_of(conn) == "fresh_test"
    finally:
        context.close_connections()
