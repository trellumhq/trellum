"""Per-engine adapters for the itest harness.

Each ``EngineSpec`` bundles everything ``test_contract.py`` needs to run
the shared contract against one database engine, so the tests themselves
contain no per-engine if/elif branching -- add a new engine here and it is
immediately exercised by every existing contract test.

This module must import cleanly with only the standard library at module
scope: it is imported by conftest.py before any engine-specific selection
happens, and engines whose driver library isn't installed (or whose
container isn't running) must still be *definable* -- just not
*connectable*. Driver/emulator imports (``fakesnow``, etc.) happen lazily,
inside the callables below.

Contract table (same logical schema everywhere; DDL text is per-engine
because dialects differ):

    id INT, amount_dec DECIMAL(12,2), ratio FLOAT, label VARCHAR,
    note VARCHAR NULL, day DATE, ts TIMESTAMP

Four rows cover: NULLs (row 2 has no note, row 4 has no ratio), unicode
text end to end (rows 2 and 4), and an embedded single quote (row 3,
"O'Brien" -- also the value used for the `:param` binding contract test,
since it's the classic case that breaks naive string interpolation).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

# ── Shared contract fixture data ─────────────────────────────────────────

COLUMNS: tuple[str, ...] = ("id", "amount_dec", "ratio", "label", "note", "day", "ts")

ROWS: list[tuple[int, Decimal, Optional[float], str, Optional[str], date, datetime]] = [
    (1, Decimal("1234.56"), 0.5, "alpha", "first row",
     date(2026, 1, 1), datetime(2026, 1, 1, 10, 0, 0)),
    (2, Decimal("0.00"), 1.0, "beta — ünïcode", None,
     date(2026, 1, 15), datetime(2026, 1, 15, 23, 59, 59)),
    (3, Decimal("-99.99"), 0.0, "O'Brien", "quote's here too",
     date(2026, 2, 1), datetime(2026, 2, 1, 0, 0, 1)),
    (4, Decimal("100000.00"), None, "héllo — ünïcode", "unicode note: héllo",
     date(2026, 3, 1), datetime(2026, 3, 1, 12, 30, 0)),
]

# Identifiers are written lowercase and unquoted everywhere. Every engine
# in this harness resolves unquoted identifiers case-insensitively at
# parse time (Snowflake/fakesnow included -- it *stores* them uppercase,
# see the "upper-cols" quirk, but still matches "contract"/"label" written
# lowercase in SQL), so one SQL string works for every engine.
SELECT_ALL_SQL = "SELECT * FROM contract ORDER BY id"

# Param-binding probe (test_param_binding_with_quote). Same story as
# SELECT_ALL_SQL: one lowercase unquoted string works everywhere except
# BigQuery, whose spec overrides it with a dataset-qualified table name
# (the emulator has no default dataset on the query path).
PARAM_SQL = "SELECT * FROM contract WHERE label = :who"


def _literal(value: Any, *, date_cast: bool, dialect: str = "standard") -> str:
    """Render one Python value as a SQL literal for a hand-built INSERT.

    Shared by every SQL engine's seed. ``date_cast`` controls whether
    DATE/TIMESTAMP values are wrapped in an ANSI type-cast literal
    (``DATE '2026-01-01'``) or left as a plain quoted string. Engines with
    real DATE/TIMESTAMP types and ANSI literal support (duckdb, fakesnow,
    postgres/mysql/sqlserver/redshift/clickhouse, bigquery) want the cast;
    SQLite has no such syntax and no real temporal types (columns are
    NUMERIC-affinity TEXT under the hood), so it gets a plain string.

    ``dialect="googlesql"`` (BigQuery) escapes quotes with a backslash --
    the SQL standard's doubled quote is a *syntax error* in GoogleSQL, and
    the goccy emulator reacts to it worse than an error: the job wedges
    and never completes. This mirrors bind_params/_sql_dialect in
    trellum/data/query.py, whose BigQuery escaping bug this harness
    caught in the first place.
    """
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        s = value.strftime("%Y-%m-%d %H:%M:%S")
        return f"TIMESTAMP '{s}'" if date_cast else f"'{s}'"
    if isinstance(value, date):
        s = value.isoformat()
        return f"DATE '{s}'" if date_cast else f"'{s}'"
    if dialect == "googlesql":
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    else:
        escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _insert_statements(
    table: str, *, date_cast: bool, dialect: str = "standard"
) -> list[str]:
    cols = ", ".join(COLUMNS)
    stmts = []
    for row in ROWS:
        values = ", ".join(
            _literal(v, date_cast=date_cast, dialect=dialect) for v in row
        )
        stmts.append(f"INSERT INTO {table} ({cols}) VALUES ({values})")
    return stmts


def _run_statements(conn: Any, statements: list[str]) -> None:
    cursor = conn.cursor()
    for stmt in statements:
        cursor.execute(stmt)
    if hasattr(conn, "commit"):
        conn.commit()


def _tmp_db_path(engine: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"itest_{engine}_{uuid.uuid4().hex}.db")


def _env(engine: str, suffix: str, default: str) -> str:
    return os.environ.get(f"ITEST_{engine.upper()}_{suffix}", default)


def ensure_ssh_keypair() -> tuple[str, str]:
    """(private key path, public key line) for the ssh bastion lane.

    Generated once per process into the temp dir -- never a key file in the
    repository -- and published as ITEST_SSH_KEY_PATH / ITEST_SSH_PUBLIC_KEY
    so the bastion container (compose reads the public half) and the test
    client (the spec reads the path) agree. A pair already in the
    environment wins, which is how a manual `docker compose up` and a
    manual pytest share one.
    """
    path = os.environ.get("ITEST_SSH_KEY_PATH")
    public = os.environ.get("ITEST_SSH_PUBLIC_KEY")
    if path and public:
        return path, public
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    path = os.path.join(tempfile.gettempdir(), f"itest_ssh_{uuid.uuid4().hex}.key")
    with open(path, "wb") as fh:
        fh.write(key.private_bytes(
            ser.Encoding.PEM, ser.PrivateFormat.OpenSSH, ser.NoEncryption(),
        ))
    public = key.public_key().public_bytes(
        ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH,
    ).decode()
    os.environ["ITEST_SSH_KEY_PATH"], os.environ["ITEST_SSH_PUBLIC_KEY"] = path, public
    return path, public


# ── EngineSpec ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineSpec:
    name: str                                 # driver type ("postgres", "duckdb", ...)
    conn_info: Callable[[], dict]             # from ITEST_<ENGINE>_* env, localhost defaults
    seed: Callable[[Any], None]               # create + populate the contract table
    select_all_sql: str
    param_sql: str = PARAM_SQL                # :who probe against the label column
    bad_conn_info: Callable[[], dict] | None = None  # wrong password (None for embedded)
    compose_service: str | None = None        # None = no container
    startup_timeout: int = 60
    quirks: frozenset[str] = frozenset()      # e.g. {"upper-cols"}
    setup: Callable[[], AbstractContextManager] | None = None  # e.g. fakesnow.patch


# ── sqlite (embedded) ──────────────────────────────────────────────────


def _sqlite_conn_info() -> dict:
    return {"path": os.environ.get("ITEST_SQLITE_PATH") or _tmp_db_path("sqlite")}


def _sqlite_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INT, amount_dec DECIMAL(12,2), ratio FLOAT, "
        "label VARCHAR(200), note VARCHAR(200), day DATE, ts TIMESTAMP)"
    )
    # SQLite has no real DATE/TIMESTAMP type (NUMERIC affinity, stored as
    # TEXT for values that don't look like numbers) and no `DATE '...'`
    # literal syntax, so date_cast=False.
    _run_statements(conn, [ddl, *_insert_statements("contract", date_cast=False)])


# ── duckdb (embedded) ──────────────────────────────────────────────────


def _duckdb_conn_info() -> dict:
    return {"path": os.environ.get("ITEST_DUCKDB_PATH") or _tmp_db_path("duckdb")}


def _duckdb_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INTEGER, amount_dec DECIMAL(12,2), ratio DOUBLE, "
        "label VARCHAR, note VARCHAR, day DATE, ts TIMESTAMP)"
    )
    _run_statements(conn, [ddl, *_insert_statements("contract", date_cast=True)])


# ── fakesnow (embedded snowflake emulator, duckdb-backed) ──────────────


def _fakesnow_conn_info() -> dict:
    return {
        "account": _env("fakesnow", "ACCOUNT", "itest"),
        "user": _env("fakesnow", "USER", "itest"),
        "password": _env("fakesnow", "PASSWORD", "itest_pw"),
        "warehouse": _env("fakesnow", "WAREHOUSE", "itest_wh"),
        "database": _env("fakesnow", "DATABASE", "itest_db"),
        "schema": _env("fakesnow", "SCHEMA", "PUBLIC"),
    }


def _fakesnow_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INTEGER, amount_dec NUMBER(12,2), ratio FLOAT, "
        "label VARCHAR, note VARCHAR, day DATE, ts TIMESTAMP)"
    )
    _run_statements(conn, [ddl, *_insert_statements("contract", date_cast=True)])


def _fakesnow_setup() -> AbstractContextManager:
    import fakesnow
    return fakesnow.patch()


# Known fidelity limits, documented here rather than discovered mid-test:
#
# 1. fakesnow's connection object lives in the `fakesnow` module, not
#    `snowflake.connector`, so trellum.data.query._execute_query's type
#    check for "snowflake.connector" in the connection's module path
#    misses and the query runs down the GENERIC DBAPI path (cursor +
#    fetchall), not the `cursor.fetch_pandas_all()` branch real Snowflake
#    takes. Both paths land in the same DataFrame shape, so the contract
#    still holds -- but this spec never exercises the fetch_pandas_all
#    code path. Proving that requires real Snowflake or a fake with an
#    identical connection module name, out of scope here.
# 2. Snowflake upper-cases unquoted identifiers; so does fakesnow's
#    emulation. See the "upper-cols" quirk below.
# 3. bad_conn_info is None: fakesnow's patched connect() never validates
#    credentials (there's no real account to authenticate against), so a
#    "wrong password" case can't be exercised meaningfully here.
FAKESNOW = EngineSpec(
    name="snowflake",
    conn_info=_fakesnow_conn_info,
    seed=_fakesnow_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=None,
    compose_service=None,
    startup_timeout=60,
    quirks=frozenset({"upper-cols"}),
    setup=_fakesnow_setup,
)


# ── postgres (container, phase 4) ───────────────────────────────────────


def _postgres_conn_info() -> dict:
    return {
        "host": _env("postgres", "HOST", "localhost"),
        "port": int(_env("postgres", "PORT", "55432")),
        "database": _env("postgres", "DB", "itest"),
        "user": _env("postgres", "USER", "itest"),
        "password": _env("postgres", "PASS", "itest_pw"),
    }


def _postgres_bad_conn_info() -> dict:
    info = _postgres_conn_info()
    info["password"] = "wrong-password"
    return info


def _postgres_seed(conn: Any) -> None:
    # DROP first: this seed also runs for the "redshift" spec below, which
    # piggybacks the *same* physical postgres container/database (it's the
    # wire-compatible stand-in -- see REDSHIFT's compose_service). Selecting
    # both "postgres" and "redshift" in one --engines run seeds this table
    # twice against the same server, so it has to be idempotent rather than
    # erroring on "relation already exists" the second time.
    ddl = (
        "CREATE TABLE contract ("
        "id INT, amount_dec DECIMAL(12,2), ratio FLOAT, "
        "label VARCHAR(200), note VARCHAR(200), day DATE, ts TIMESTAMP)"
    )
    _run_statements(conn, [
        "DROP TABLE IF EXISTS contract",
        ddl,
        *_insert_statements("contract", date_cast=True),
    ])


POSTGRES = EngineSpec(
    name="postgres",
    conn_info=_postgres_conn_info,
    seed=_postgres_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_postgres_bad_conn_info,
    compose_service="postgres",
    startup_timeout=60,
)


# ── postgres_ssh (the postgres container, through the ssh bastion) ─────


def _postgres_ssh_conn_info() -> dict:
    # `host`/`port` are the database AS THE BASTION SEES IT: the compose
    # service name and its in-network port, unreachable from this machine
    # directly. The ssh_* fields are what the framework tunnels through.
    key_path = os.environ.get("ITEST_SSH_KEY_PATH")
    if not key_path:
        raise RuntimeError(
            "postgres_ssh needs ITEST_SSH_KEY_PATH, and a bastion started with "
            "the matching ITEST_SSH_PUBLIC_KEY -- itest/run.py sets both "
            "(engines.ensure_ssh_keypair). See itest/README.md for a manual run."
        )
    return {
        "host": _env("postgres_ssh", "HOST", "postgres"),
        "port": int(_env("postgres_ssh", "PORT", "5432")),
        "database": _env("postgres_ssh", "DB", "itest"),
        "user": _env("postgres_ssh", "USER", "itest"),
        "password": _env("postgres_ssh", "PASS", "itest_pw"),
        # The bastion is its own compose service, so ITEST_SSH_* -- the same
        # ITEST_SSH_PORT docker-compose.yml maps the host port from.
        "ssh_host": _env("ssh", "HOST", "localhost"),
        "ssh_port": int(_env("ssh", "PORT", "52222")),
        "ssh_user": _env("ssh", "USER", "itest"),
        "ssh_key_path": key_path,
    }


def _postgres_ssh_bad_conn_info() -> dict:
    # A wrong DATABASE password: the tunnel itself comes up, the refusal
    # travels back through it.
    info = _postgres_ssh_conn_info()
    info["password"] = "wrong-password"
    return info


POSTGRES_SSH = EngineSpec(
    name="postgres",
    conn_info=_postgres_ssh_conn_info,
    seed=_postgres_seed,  # idempotent: shares the postgres server with POSTGRES/REDSHIFT
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_postgres_ssh_bad_conn_info,
    compose_service="ssh",  # depends_on brings postgres up with it
    startup_timeout=60,
)


# ── redshift (piggybacks the postgres container -- wire-compatible) ────


def _redshift_conn_info() -> dict:
    # Defaults to the *postgres* container's mapped port: psycopg2 speaks
    # the Redshift wire protocol, so a real postgres server stands in for
    # it here (see data/drivers/redshift.py's docstring for the fidelity
    # limit -- this proves connect()/query_df() dispatch, not Redshift's
    # SQL dialect extensions).
    return {
        "host": _env("redshift", "HOST", "localhost"),
        "port": int(_env("redshift", "PORT", "55432")),
        "database": _env("redshift", "DB", "itest"),
        "user": _env("redshift", "USER", "itest"),
        "password": _env("redshift", "PASS", "itest_pw"),
    }


def _redshift_bad_conn_info() -> dict:
    info = _redshift_conn_info()
    info["password"] = "wrong-password"
    return info


REDSHIFT = EngineSpec(
    name="redshift",
    conn_info=_redshift_conn_info,
    seed=_postgres_seed,  # same wire protocol, same DDL/DML
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_redshift_bad_conn_info,
    compose_service="postgres",
    startup_timeout=60,
)


# ── mysql (container, phase 4) ──────────────────────────────────────────


def _mysql_conn_info() -> dict:
    return {
        "host": _env("mysql", "HOST", "localhost"),
        # 54306, not 5xx06 like the others: Windows' post-reboot WinNAT
        # excluded ranges regularly swallow the 53xxx band (see
        # docker-compose.yml's ports comment).
        "port": int(_env("mysql", "PORT", "54306")),
        "database": _env("mysql", "DB", "itest"),
        "user": _env("mysql", "USER", "itest"),
        "password": _env("mysql", "PASS", "itest_pw"),
    }


def _mysql_bad_conn_info() -> dict:
    info = _mysql_conn_info()
    info["password"] = "wrong-password"
    return info


def _mysql_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INT, amount_dec DECIMAL(12,2), ratio DOUBLE, "
        "label VARCHAR(200), note VARCHAR(200), day DATE, ts DATETIME)"
    )
    _run_statements(conn, [
        "DROP TABLE IF EXISTS contract",
        ddl,
        *_insert_statements("contract", date_cast=False),
    ])


MYSQL = EngineSpec(
    name="mysql",
    conn_info=_mysql_conn_info,
    seed=_mysql_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_mysql_bad_conn_info,
    compose_service="mysql",
    startup_timeout=60,
)


# ── clickhouse (container, phase 4) ─────────────────────────────────────


def _clickhouse_conn_info() -> dict:
    return {
        "host": _env("clickhouse", "HOST", "localhost"),
        "port": int(_env("clickhouse", "PORT", "58123")),
        "database": _env("clickhouse", "DB", "itest"),
        "user": _env("clickhouse", "USER", "itest"),
        "password": _env("clickhouse", "PASS", "itest_pw"),
        # Plain HTTP -- the compose container has no TLS. Still
        # env-overridable (ITEST_CLICKHOUSE_SECURE) for the rare case of
        # pointing this spec at a real, TLS-terminated ClickHouse.
        "secure": _env("clickhouse", "SECURE", "false").lower() in ("1", "true", "yes"),
    }


def _clickhouse_bad_conn_info() -> dict:
    info = _clickhouse_conn_info()
    info["password"] = "wrong-password"
    return info


def _clickhouse_seed(conn: Any) -> None:
    # ClickHouse needs an explicit engine/ordering key and Nullable(...)
    # wrappers for columns that carry NULLs (ratio, note) -- plain types
    # are NOT NULL by default.
    ddl = (
        "CREATE TABLE contract ("
        "id Int32, amount_dec Decimal(12,2), ratio Nullable(Float64), "
        "label String, note Nullable(String), day Date, ts DateTime"
        ") ENGINE = MergeTree ORDER BY id"
    )
    # clickhouse_connect's client is not a DBAPI connection -- it has no
    # cursor()/execute(), so this can't go through _run_statements like
    # every other SQL engine here. `command()` is its DDL/DML entry point;
    # `query_df()` (used at query time, see query.py's _execute_query) is
    # for SELECTs only.
    conn.command("DROP TABLE IF EXISTS contract")
    conn.command(ddl)
    for stmt in _insert_statements("contract", date_cast=True):
        conn.command(stmt)


CLICKHOUSE = EngineSpec(
    name="clickhouse",
    conn_info=_clickhouse_conn_info,
    seed=_clickhouse_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_clickhouse_bad_conn_info,
    compose_service="clickhouse",
    startup_timeout=60,
)


# ── sqlserver (container, phase 4) ──────────────────────────────────────


def _sqlserver_conn_info() -> dict:
    return {
        "host": _env("sqlserver", "HOST", "localhost"),
        "port": int(_env("sqlserver", "PORT", "51433")),
        # "master", not "itest": a fresh mssql container has only the
        # system databases and no env-var way to create one (unlike
        # POSTGRES_DB/MYSQL_DATABASE), and the spec's connection must
        # succeed before any seed SQL could create it. The contract table
        # lives in master; SQL Server treats a missing database as the
        # same 18456 login failure as a wrong password, which is a
        # miserable thing to debug from the retry loop's error message.
        "database": _env("sqlserver", "DB", "master"),
        "user": _env("sqlserver", "USER", "itest"),
        "password": _env("sqlserver", "PASS", "itest_pw"),
    }


def _sqlserver_bad_conn_info() -> dict:
    info = _sqlserver_conn_info()
    info["password"] = "wrong-password"
    return info


def _sqlserver_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INT, amount_dec DECIMAL(12,2), ratio FLOAT, "
        "label VARCHAR(200), note VARCHAR(200), day DATE, ts DATETIME2)"
    )
    _run_statements(conn, [
        "DROP TABLE IF EXISTS contract",
        ddl,
        *_insert_statements("contract", date_cast=False),
    ])


SQLSERVER = EngineSpec(
    name="sqlserver",
    conn_info=_sqlserver_conn_info,
    seed=_sqlserver_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_sqlserver_bad_conn_info,
    compose_service="sqlserver",
    # No compose healthcheck (sqlcmd path is unstable across image
    # revisions) -- the connect-retry loop in conftest.py is the real
    # gate, so this needs generous headroom for a cold container.
    startup_timeout=120,
)


# ── bigquery (container: goccy/bigquery-emulator, phase 4) ─────────────


def _bigquery_conn_info() -> dict:
    return {
        "project": _env("bigquery", "PROJECT", "itest-project"),
        "api_endpoint": _env("bigquery", "API_ENDPOINT", "http://localhost:59050"),
    }


def _bigquery_seed(conn: Any) -> None:
    # bigquery.Client has no cursor()/execute() DBAPI surface -- seed via
    # job-based SQL instead. Requires the "itest" dataset to already exist
    # (the emulator is started with --dataset=itest; see docker-compose.yml
    # in phase 4).
    #
    # This was originally written against load_table_from_json (a BigQuery
    # "load" job), which is the real-BigQuery-idiomatic way to seed a
    # table. goccy/bigquery-emulator's /jobs endpoint doesn't implement
    # that job type, though -- it 400s with "unspecified job configuration
    # query", i.e. it only understands "query" job configurations. A DML
    # INSERT run as a query job (conn.query(sql).result()) hits that
    # supported path, so this reuses the same hand-built literal INSERTs
    # every DBAPI-cursor engine's seed uses (_insert_statements), which are
    # already standard-SQL enough (ANSI DATE/TIMESTAMP casts) to work
    # unchanged here.
    from google.cloud import bigquery

    table_ref = "itest-project.itest.contract"
    conn.delete_table(table_ref, not_found_ok=True)
    schema = [
        bigquery.SchemaField("id", "INTEGER"),
        bigquery.SchemaField("amount_dec", "NUMERIC"),
        bigquery.SchemaField("ratio", "FLOAT"),
        bigquery.SchemaField("label", "STRING"),
        bigquery.SchemaField("note", "STRING"),
        bigquery.SchemaField("day", "DATE"),
        bigquery.SchemaField("ts", "TIMESTAMP"),
    ]
    conn.create_table(bigquery.Table(table_ref, schema=schema), exists_ok=True)
    for stmt in _insert_statements("`itest.contract`", date_cast=True, dialect="googlesql"):
        # timeout: on SQL the emulator can't execute, the job wedges in a
        # never-DONE state instead of erroring -- without a bound, result()
        # polls it forever and the whole session looks hung.
        conn.query(stmt).result(timeout=60)


BIGQUERY = EngineSpec(
    name="bigquery",
    conn_info=_bigquery_conn_info,
    seed=_bigquery_seed,
    select_all_sql="SELECT * FROM `itest.contract` ORDER BY id",
    param_sql="SELECT * FROM `itest.contract` WHERE label = :who",
    # bigquery.Client() construction is lazy -- it never touches the
    # network until a query runs, so a "bad credentials" case can't raise
    # from connect() itself the way every other driver's does. Proving an
    # error path here would mean running a query, which is out of this
    # contract's scope (test_contract.py only calls driver.connect()).
    bad_conn_info=None,
    compose_service="bigquery",
    startup_timeout=60,
)


# ── trino (container: stock image, memory connector) ───────────────────


def _trino_conn_info() -> dict:
    return {
        "host": _env("trino", "HOST", "localhost"),
        "port": int(_env("trino", "PORT", "58080")),
        "user": _env("trino", "USER", "itest"),
        # The stock image ships a `memory` catalog; the seed creates the
        # `itest` schema inside it. No password: the container runs no
        # authenticator, and Trino only accepts one over HTTPS anyway.
        "catalog": _env("trino", "CATALOG", "memory"),
        "schema": _env("trino", "SCHEMA", "itest"),
        "secure": _env("trino", "SECURE", "false").lower() in ("1", "true", "yes"),
    }


def _trino_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INTEGER, amount_dec DECIMAL(12,2), ratio DOUBLE, "
        "label VARCHAR, note VARCHAR, day DATE, ts TIMESTAMP)"
    )
    _run_statements(conn, [
        # Qualified: the session's default schema is this one, and it does
        # not exist until this statement has run.
        "CREATE SCHEMA IF NOT EXISTS memory.itest",
        "DROP TABLE IF EXISTS contract",
        ddl,
        *_insert_statements("contract", date_cast=True),
    ])


TRINO = EngineSpec(
    name="trino",
    conn_info=_trino_conn_info,
    seed=_trino_seed,
    select_all_sql=SELECT_ALL_SQL,
    # trino.dbapi.connect() is lazy -- nothing touches the network until a
    # query runs, so a bad-credentials case can't raise from connect()
    # itself (same story as BIGQUERY above).
    bad_conn_info=None,
    compose_service="trino",
)


# ── vertica (container, heavy profile -- gated, opt-in, never default) ─


def _vertica_conn_info() -> dict:
    return {
        "host": _env("vertica", "HOST", "localhost"),
        "port": int(_env("vertica", "PORT", "55433")),
        "database": _env("vertica", "DB", "itest"),
        "user": _env("vertica", "USER", "itest"),
        "password": _env("vertica", "PASS", "itest_pw"),
    }


def _vertica_bad_conn_info() -> dict:
    info = _vertica_conn_info()
    info["password"] = "wrong-password"
    return info


def _vertica_seed(conn: Any) -> None:
    ddl = (
        "CREATE TABLE contract ("
        "id INT, amount_dec DECIMAL(12,2), ratio FLOAT, "
        "label VARCHAR(200), note VARCHAR(200), day DATE, ts TIMESTAMP)"
    )
    _run_statements(conn, [
        "DROP TABLE IF EXISTS contract",
        ddl,
        *_insert_statements("contract", date_cast=True),
    ])


# Vertica CE is multi-GB and slow to start (1-3 minutes) -- it lives
# behind the "heavy" compose profile and is deliberately absent from
# every default --engines list (see conftest.py's _DEFAULT_ENGINES and
# ENGINES membership below). The spec still exists so run.py --profile
# heavy has something to select.
VERTICA = EngineSpec(
    name="vertica",
    conn_info=_vertica_conn_info,
    seed=_vertica_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=_vertica_bad_conn_info,
    compose_service="vertica",
    startup_timeout=300,
)


SQLITE = EngineSpec(
    name="sqlite",
    conn_info=_sqlite_conn_info,
    seed=_sqlite_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=None,  # embedded, no credentials to get wrong
    compose_service=None,
)

DUCKDB = EngineSpec(
    name="duckdb",
    conn_info=_duckdb_conn_info,
    seed=_duckdb_seed,
    select_all_sql=SELECT_ALL_SQL,
    bad_conn_info=None,  # embedded, no credentials to get wrong
    compose_service=None,
)


# Engines keyed by driver type name (== EngineSpec.name for every entry
# except fakesnow, which emulates the "snowflake" driver and is keyed by
# its own harness name so --engines can select it independently of a real
# Snowflake lane, should one be added later).
ENGINES: dict[str, EngineSpec] = {
    "sqlite": SQLITE,
    "duckdb": DUCKDB,
    "fakesnow": FAKESNOW,
    "postgres": POSTGRES,
    "postgres_ssh": POSTGRES_SSH,
    "redshift": REDSHIFT,
    "mysql": MYSQL,
    "clickhouse": CLICKHOUSE,
    "sqlserver": SQLSERVER,
    "bigquery": BIGQUERY,
    "trino": TRINO,
    "vertica": VERTICA,
}
