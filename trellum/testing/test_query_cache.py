"""The local query cache: what invalidates it, and when it expires.

The contract an author relies on is "an edited query re-runs, an untouched
one does not" -- the TTL is only the backstop for data moving underneath.
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

from trellum.data import connections as c
from trellum.data import query as q


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    """Point the cache at a temp dir; queries run against in-memory sqlite."""
    monkeypatch.setattr(q, "_cache_dir", lambda: tmp_path / ".query_cache")
    q.enable_cache()
    yield tmp_path
    q.enable_cache()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE t (n INTEGER)")
    connection.execute("INSERT INTO t VALUES (1)")
    connection.commit()
    yield connection
    connection.close()


def _bump(conn, value):
    conn.execute("UPDATE t SET n = ?", (value,))
    conn.commit()


def test_default_ttl_is_a_day():
    assert q._default_cache_ttl == 24 * 3600


def test_unchanged_query_is_served_from_cache(cache_root, conn):
    assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 1
    _bump(conn, 99)
    # Same SQL, still inside the TTL: the stale row is the point.
    assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 1


def test_edited_query_re_runs_immediately(cache_root, conn):
    q.query_df(conn, "SELECT n FROM t")
    _bump(conn, 42)
    assert q.query_df(conn, "SELECT n AS n FROM t").iloc[0]["n"] == 42


def test_changed_parameter_re_runs_immediately(cache_root, conn):
    conn.execute("INSERT INTO t VALUES (7)")
    conn.commit()
    first = q.query_df(conn, "SELECT n FROM t WHERE n = :n", params={"n": 1})
    second = q.query_df(conn, "SELECT n FROM t WHERE n = :n", params={"n": 7})
    assert first.iloc[0]["n"] == 1
    assert second.iloc[0]["n"] == 7


def test_expired_entry_re_runs(cache_root, conn):
    q.query_df(conn, "SELECT n FROM t")
    _bump(conn, 5)
    entry = next((cache_root / ".query_cache").glob("*.pkl"))
    stale = time.time() - (q._default_cache_ttl + 60)
    os.utime(entry, (stale, stale))
    assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 5


def test_same_query_on_two_sources_does_not_collide(cache_root):
    """Staging and production hold the same table under the same SQL."""
    staging, production = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    try:
        for db, n in ((staging, 1), (production, 2)):
            db.execute("CREATE TABLE t (n INTEGER)")
            db.execute("INSERT INTO t VALUES (?)", (n,))
            db.commit()
        c.register_source(staging, "staging")
        c.register_source(production, "production")

        assert q.query_df(staging, "SELECT n FROM t").iloc[0]["n"] == 1
        assert q.query_df(production, "SELECT n FROM t").iloc[0]["n"] == 2
    finally:
        for db in (staging, production):
            c.forget_source(db)
            db.close()


def test_forget_source_releases_the_connection(cache_root, conn):
    c.register_source(conn, "primary")
    assert c.source_of(conn) == "primary"
    c.forget_source(conn)
    assert c.source_of(conn) == ""
    assert id(conn) not in c._source_names


def test_unregistered_connection_still_caches(cache_root, conn):
    """A connection nobody registered keeps working, just without a name."""
    assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 1
    _bump(conn, 8)
    assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 1


def test_disable_cache_always_re_runs(cache_root, conn):
    q.query_df(conn, "SELECT n FROM t")
    _bump(conn, 3)
    q.disable_cache()
    try:
        assert q.query_df(conn, "SELECT n FROM t").iloc[0]["n"] == 3
    finally:
        q.enable_cache()
