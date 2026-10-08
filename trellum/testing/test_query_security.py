"""Parameter tokens and portal DuckDB boundaries, using synthetic local data."""
import sqlite3
from pathlib import Path

import pytest

from trellum.data.query import bind_params, query_df
from trellum.validation.checks.live_query import _sql_placeholders


@pytest.mark.parametrize("dialect", ["standard", "backslash", "googlesql", "bracket"])
@pytest.mark.parametrize("reverse", [False, True])
def test_inserted_parameter_tokens_are_inert(dialect, reverse):
    params = {"longname": ":x", "x": " || 42 || "}
    if reverse:
        params = dict(reversed(list(params.items())))
    assert bind_params("SELECT :longname, :x, :longname", params, dialect) == (
        "SELECT ':x', ' || 42 || ', ':x'"
    )


@pytest.mark.parametrize("sql,dialect", [
    ("SELECT ':p', 'a'' :p', \"a:p\", `a:p`, :actual -- :p\r\n/* :p */", "standard"),
    ("SELECT [a:p], [escaped]]:p], :actual", "bracket"),
    ("SELECT $$ :p $$, $body$ :p $body$, :actual", "standard"),
    ("SELECT /* outer :p /* inner :p */ :p */ :actual", "standard"),
    (r"SELECT E'escaped\' :p', :actual", "standard"),
    (r"SELECT 'escaped\' :p', :actual # :p", "backslash"),
    (r'''SELECT """ :p """, ''' + "''' :p '''" + ", :actual", "googlesql"),
])
def test_quoted_and_comment_tokens_are_unchanged(sql, dialect):
    assert bind_params(sql, {"p": 99, "actual": 42}, dialect) == sql.replace(":actual", "42")
    assert _sql_placeholders(sql, dialect) == {"actual"}


def test_casts_missing_and_prefix_parameters():
    assert bind_params(
        "SELECT :p::integer, :prefix, :missing, value::p",
        {"p": 42, "prefix": "value"},
    ) == "SELECT 42::integer, 'value', :missing, value::p"
    assert bind_params("SELECT [:p, :p]", {"p": 42}) == "SELECT [42, 42]"


def test_sqlite_execution_keeps_sql_shaped_values_as_data():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE quoted ([:p] INTEGER)")
        conn.execute("INSERT INTO quoted VALUES (7)")
        assert query_df(conn, "SELECT [:p] FROM quoted WHERE [:p] = :value", {
            "p": 99, "value": 7,
        }, cache_ttl=0).values.tolist() == [[7]]
        frame = query_df(
            conn, "SELECT :longname AS first, :x AS second, :longname AS repeated",
            {"longname": ":x", "x": " || 42 || "}, cache_ttl=0,
        )
        assert frame.values.tolist() == [[":x", " || 42 || ", ":x"]]
        assert query_df(conn, "SELECT :i AS i, :f AS f, :s AS s", {
            "i": 4, "f": 2.5, "s": "O'Brien\\path",
        }, cache_ttl=0).values.tolist() == [[4, 2.5, "O'Brien\\path"]]
        with pytest.raises(sqlite3.ProgrammingError):
            query_df(conn, "SELECT :missing", {"other": 1}, cache_ttl=0)


@pytest.fixture
def duckdb_source(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    source = tmp_path / "source.duckdb"
    with duckdb.connect(str(source)) as conn:
        conn.execute("CREATE TABLE intended AS SELECT 42 AS value")
    sibling = tmp_path / "sibling.csv"
    sibling.write_text("value\nnot-a-database-row\n", encoding="utf-8")
    outside = tmp_path / "outside" / "sentinel.txt"
    outside.parent.mkdir()
    outside.write_text("synthetic-sentinel", encoding="utf-8")
    with duckdb.connect(str(source)) as conn:
        conn.execute(f"CREATE VIEW external_view AS SELECT * FROM read_text('{outside.as_posix()}')")
    other = tmp_path / "other.duckdb"
    with duckdb.connect(str(other)) as conn:
        conn.execute("CREATE TABLE unrelated AS SELECT 99 AS value")
    return duckdb, source, sibling, outside, other


def test_portal_duckdb_confinement_on_actual_cursor(duckdb_source):
    from trellum.data.drivers import connect

    duckdb, source, sibling, outside, other = duckdb_source
    conn = connect("duckdb", {"path": str(source), "portal_live_query": True})
    try:
        cur = conn.cursor()
        assert cur.execute("SELECT value FROM intended").fetchall() == [(42,)]
        allowed_dirs = cur.execute(
            "SELECT current_setting('allowed_directories')",
        ).fetchone()[0]
        assert [Path(p).resolve() for p in allowed_dirs] == [Path(str(source) + ".tmp").resolve()]
        for sql in (
            f"SELECT * FROM read_csv('{sibling.as_posix()}')",
            f"SELECT * FROM read_text('{outside.as_posix()}')",
            f"SELECT * FROM read_blob('{outside.as_posix()}')",
            "SELECT * FROM external_view",
            f"ATTACH '{other.as_posix()}' AS other (READ_ONLY)",
            "SET enable_external_access = true",
            "SET autoinstall_known_extensions = true",
            "SET autoload_known_extensions = true",
            "SET python_enable_replacements = true",
            "SET lock_configuration = false",
            f"SET allowed_directories = ['{outside.parent.as_posix()}']",
            "INSTALL httpfs",
            "LOAD httpfs",
            "INSERT INTO intended VALUES (7)",
        ):
            with pytest.raises(duckdb.Error):
                cur.execute(sql)
        # No network request: failure must occur before reading even localhost.
        with pytest.raises(duckdb.Error):
            cur.execute("SELECT * FROM read_csv('http://127.0.0.1:1/sentinel.csv')")
        assert cur.execute("SELECT value FROM intended").fetchall() == [(42,)]
    finally:
        conn.close()


def test_standalone_duckdb_keeps_file_access(duckdb_source):
    from trellum.data.drivers import connect

    _, source, _, outside, _ = duckdb_source
    with connect("duckdb", {"path": str(source)}) as conn:
        assert conn.execute(
            f"SELECT content FROM read_text('{outside.as_posix()}')",
        ).fetchone() == ("synthetic-sentinel",)
