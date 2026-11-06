"""Conservative PostgreSQL analytical reads, independent of any host application.

The caller owns current source authorization and supplies explicit relation grants.
Database administrators must grant only trusted tables/views to a dedicated read-only
identity. A granted view (including its functions and owner/RLS semantics) is a
trusted boundary. This validator is deliberately a subset of PostgreSQL, not a
sandbox for arbitrary extensions, types, functions or administrator-authored views.

Rows and UTF-8 JSON bytes are output bounds, never scan bounds. PostgreSQL's own
statement timeout remains active if the calling process disappears. This blocking
API is intended for a durable worker, not an event-loop/request thread.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope


class QueryRejected(ValueError):
    """A query or source is unsupported; messages contain no supplied values."""


class QueryExecutionError(RuntimeError):
    """Safe failure with an explicit remote-completion classification."""

    def __init__(self, message, *, confirmed=False, cancelled=False):
        super().__init__(message)
        self.confirmed = confirmed
        self.cancelled = cancelled


_NODES = frozenset("""
Select Union Intersect Except Subquery With CTE From Join Table TableAlias
Identifier Column Star Alias Literal Null Boolean Paren Tuple Distinct
Where Group Having Order Ordered Limit Offset Window WindowSpec Filter WithinGroup
And Or Not EQ NEQ GT GTE LT LTE Is In Between Like ILike Escape Exists
Add Sub Mul Div Mod Neg Case If Cast DataType DataTypeParam Interval Var
""".split())
_FUNCTIONS = frozenset("""
Count Sum Avg Min Max Abs Round Ceil Floor Sqrt Pow Sign Coalesce Nullif
Greatest Least Lower Upper Length Trim LTrim RTrim Substring Replace Concat
ConcatWs Left Right StrPosition TimestampTrunc DateTrunc Extract CurrentDate
CurrentTimestamp RowNumber Rank DenseRank PercentRank CumeDist Ntile Lag Lead
FirstValue LastValue NthValue PercentileCont PercentileDisc Stddev StddevPop
StddevSamp Variance VariancePop VarianceSamp LogicalAnd LogicalOr
""".split())
_ANONYMOUS = frozenset({"date_bin", "to_char", "btrim", "char_length"})
_TYPES = frozenset({"BOOLEAN", "SMALLINT", "INT", "BIGINT", "DECIMAL", "FLOAT",
                    "DOUBLE", "TEXT", "VARCHAR", "CHAR", "DATE", "TIME",
                    "TIMESTAMP", "TIMESTAMPTZ", "TIMETZ", "INTERVAL"})
_UNITS = frozenset("""year quarter month week day hour minute second millisecond
microsecond decade century millennium dow isodow doy epoch isoyear timezone
 timezone_hour timezone_minute""".split())
# Only built-in scalar output types: domains, composites, arrays and extension
# types could execute administrator-defined casts/operators/output routines.
_UDT = frozenset({"bool", "int2", "int4", "int8", "numeric", "float4", "float8",
                  "text", "varchar", "bpchar", "date", "time", "timetz",
                  "timestamp", "timestamptz", "interval", "uuid"})


def _parse(sql):
    if not isinstance(sql, str) or not sql:
        raise QueryRejected("Query is empty or exceeds the supported size.")
    try:
        if len(sql.encode("utf-8")) > 16384:
            raise ValueError
        statements = sqlglot.parse(sql, read="postgres", error_level=sqlglot.ErrorLevel.RAISE)
        if len(statements) != 1 or statements[0] is None:
            raise ValueError
        tree = normalize_identifiers(statements[0], dialect="postgres")
        pending = [(tree, 0)]
        count = 0
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > 2000 or depth > 50:
                raise ValueError
            pending.extend((child, depth + 1) for child in node.iter_expressions())
        return tree
    except Exception:
        raise QueryRejected("Query syntax or complexity is unsupported.") from None


def _relations(relations):
    if not isinstance(relations, list) or not 1 <= len(relations) <= 100:
        raise QueryRejected("Explicit schema-qualified relation grants are required.")
    allowed = set()
    for relation in relations:
        tree = _parse("SELECT * FROM " + relation) if isinstance(relation, str) else None
        table = tree.args.get("from_").this if isinstance(tree, exp.Select) and tree.args.get("from_") else None
        if (not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier)
                or not isinstance(table.args.get("db"), exp.Identifier)
                or set(table.args) - {"this", "db", "catalog"}
                or table.catalog or {k for k, v in tree.args.items() if v} - {"expressions", "from_"}
                or len(tree.expressions) != 1 or not isinstance(tree.expressions[0], exp.Star)):
            raise QueryRejected("Explicit schema-qualified relation grants are required.")
        pair = (table.db, table.name)
        if (pair[0].startswith("pg_") or pair[0] == "information_schema"
                or any(not name or "\x00" in name or len(name.encode("utf-8")) > 63 for name in pair)):
            raise QueryRejected("Relation grant is unsupported.")
        allowed.add(pair)
    return allowed


def validate_query(sql: str, relations: list[str]) -> str:
    """Return normalized PostgreSQL SQL or reject it with a fixed safe message."""
    allowed = _relations(relations)
    tree = _parse(sql)
    if type(tree) not in (exp.Select, exp.Union, exp.Intersect, exp.Except):
        raise QueryRejected("Only a single analytical SELECT is supported.")
    try:
        for node in tree.walk():
            name = type(node).__name__
            if name not in _NODES and name not in _FUNCTIONS:
                if not (type(node) is exp.Anonymous and node.name.lower() in _ANONYMOUS):
                    raise ValueError
            if isinstance(node, exp.With) and node.args.get("recursive"):
                raise ValueError
            if isinstance(node, exp.DataType):
                if node.this.name not in _TYPES or node.args.get("kind"):
                    raise ValueError
            if isinstance(node, exp.Var) and node.name.lower() not in _UNITS:
                raise ValueError
            if isinstance(node, exp.Identifier) and ("\x00" in node.name or len(node.name.encode("utf-8")) > 63):
                raise ValueError
            if isinstance(node, exp.Table) and (not isinstance(node.this, exp.Identifier)
                    or node.catalog or set(node.args) - {"this", "db", "catalog", "alias"}):
                raise ValueError
        seen = set()
        for scope in traverse_scope(tree):
            for _, (node, source) in scope.selected_sources.items():
                if isinstance(source, Scope):
                    if isinstance(node, exp.Table):
                        seen.add(id(node))
                    continue
                if not isinstance(source, exp.Table) or (source.db, source.name) not in allowed:
                    raise ValueError
                seen.add(id(source))
        if any(id(table) not in seen for table in tree.find_all(exp.Table)):
            raise ValueError
        # Re-render rather than forwarding the original text or comments. Fail
        # closed when the PostgreSQL generator cannot represent an AST feature.
        return tree.sql(dialect="postgres", identify=True, comments=False,
                        unsupported_level=sqlglot.ErrorLevel.RAISE)
    except Exception:
        raise QueryRejected("Query uses an unsupported operation or relation.") from None


def _connection_options(conn_info, timeout):
    keys = {"host", "port", "database", "user", "password", "sslmode"}
    if not isinstance(conn_info, dict) or set(conn_info) - keys:
        raise QueryRejected("Only direct PostgreSQL connections are supported.")
    if any(os.environ.get(key) for key in (
            "PGOPTIONS", "PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR", "PGSSLKEY",
            "PGSSLCERT", "PGSSLROOTCERT", "PGSSLCRL", "PGSSLCRLDIR")):
        raise QueryRejected("PostgreSQL environment configuration is unsupported.")
    for key in ("host", "database", "user", "password"):
        if not isinstance(conn_info.get(key), str) or not conn_info[key] or "\x00" in conn_info[key]:
            raise QueryRejected("PostgreSQL connection configuration is invalid.")
    if any(ch in conn_info["host"] for ch in (",", "=", "/")) or "://" in conn_info["database"] or "=" in conn_info["database"]:
        raise QueryRejected("Only direct PostgreSQL connections are supported.")
    try:
        port = int(conn_info.get("port", 5432))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        raise QueryRejected("PostgreSQL connection configuration is invalid.") from None
    try:
        local = ipaddress.ip_address(conn_info["host"]).is_loopback
    except ValueError:
        local = conn_info["host"].lower() == "localhost"
    sslmode = conn_info.get("sslmode", "prefer" if local else "verify-full")
    if not isinstance(sslmode, str) or sslmode not in {"disable", "prefer", "require", "verify-ca", "verify-full"}:
        raise QueryRejected("PostgreSQL connection configuration is invalid.")
    if not local and sslmode != "verify-full":
        raise QueryRejected("Remote PostgreSQL connections require verified TLS.")
    milliseconds = max(1, int(timeout * 1000))
    return dict(host=conn_info["host"], port=port, dbname=conn_info["database"],
                user=conn_info["user"], password=conn_info["password"], sslmode=sslmode,
                connect_timeout=max(2, min(10, math.ceil(timeout))),
                options=f"-c default_transaction_read_only=on -c statement_timeout={milliseconds} "
                        f"-c lock_timeout={min(milliseconds, 1000)} -c search_path=pg_catalog "
                        "-c standard_conforming_strings=on -c client_encoding=UTF8 "
                        "-c idle_in_transaction_session_timeout=15000",
                application_name="trellum-assistant-query", passfile="/dev/null")


def _resolve_address(options, timeout):
    """Resolve DNS in a killable child, never an unbounded executor thread.

    Keep the original host for TLS hostname verification. Supplying hostaddr
    prevents psycopg/asyncio from starting its own threaded DNS resolution.
    """
    try:
        ipaddress.ip_address(options["host"])
        return options["host"]
    except ValueError:
        pass
    resolver = (
        "import socket,sys; "
        "addresses=socket.getaddrinfo(sys.argv[1],sys.argv[2],type=socket.SOCK_STREAM); "
        "print(addresses[0][4][0])"
    )
    try:
        result = subprocess.run([sys.executable, "-I", "-c", resolver,
                                 options["host"], str(options["port"])],
                                capture_output=True, timeout=min(timeout, 5), check=True)
        address = result.stdout.decode("ascii").strip()
        ipaddress.ip_address(address)
        return address
    except Exception:
        raise QueryExecutionError("PostgreSQL address resolution failed.") from None


def _metadata_query(allowed):
    # This is the only catalog read exposed by describe_source. Relation names
    # become literals, never SQL structure; no model SQL enters this path.
    conditions = [exp.and_(exp.column("table_schema").eq(exp.Literal.string(schema)),
                           exp.column("table_name").eq(exp.Literal.string(table)))
                  for schema, table in sorted(allowed)]
    return (exp.select("table_schema", "table_name", "column_name", "data_type",
                       "udt_schema", "udt_name", "domain_name", "ordinal_position")
            .from_("information_schema.columns").where(exp.or_(*conditions))
            .order_by("table_schema", "table_name", "ordinal_position")
            .sql(dialect="postgres", identify=True, comments=False))


async def _watch(conn, operation, deadline, cancelled):
    """Poll without worker threads; cancellation targets this connection's key."""
    try:
        if cancelled and await asyncio.to_thread(cancelled):
            raise QueryExecutionError("Query cancelled.", confirmed=True, cancelled=True)
    except Exception:
        operation.close()
        raise
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            reason = ("Query cancelled." if cancelled and await asyncio.to_thread(cancelled) else
                      "Query timed out." if time.monotonic() >= deadline else None)
            if reason:
                try:
                    await conn.cancel_safe(timeout=2)
                    # A cancel request alone proves nothing: consume the server
                    # response before claiming cancellation. Completion racing
                    # cancellation also confirms the operation is no longer active.
                    try:
                        await asyncio.wait_for(asyncio.shield(task), 3)
                    except Exception as exc:
                        if getattr(exc, "sqlstate", None) != "57014":
                            raise QueryExecutionError("Query cancellation is unconfirmed.") from None
                except QueryExecutionError:
                    raise
                except Exception:
                    raise QueryExecutionError("Query cancellation is unconfirmed.") from None
                raise QueryExecutionError(reason, confirmed=True, cancelled=reason == "Query cancelled.")
            await asyncio.wait({task}, timeout=0.05)
        return await task
    finally:
        if not task.done():
            # Closing the socket is bounded. The server timeout is independent
            # protection if remote cancellation could not be confirmed.
            await conn.close()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _execute(options, query, allowed, timeout, max_rows, max_bytes, cancelled, on_started, metadata):
    import psycopg
    from psycopg import sql as compose

    # Older libpq silently falls back to unbounded blocking cancellation.
    if not psycopg.capabilities.has_cancel_safe():
        raise QueryRejected("Bounded PostgreSQL cancellation is unavailable.")
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    conn = await asyncio.wait_for(psycopg.AsyncConnection.connect(**options), min(timeout, 10))
    try:
        async def run(statement, params=None):
            return await _watch(conn, conn.execute(statement, params), deadline, cancelled)

        await run("BEGIN READ ONLY")
        identity = await run("SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication "
                             "FROM pg_catalog.pg_roles WHERE rolname = CURRENT_USER")
        if (await identity.fetchone())[0]:
            raise QueryRejected("A dedicated read-only PostgreSQL identity is required.")
        # Refuse writable/owner identities, including privileges inherited from
        # roles and PUBLIC. Catalogs are not part of the source grant surface.
        writable = await run("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c "
                             "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                             "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' "
                             "AND c.relkind IN ('r','p','v','m','f') AND "
                             "pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER'))")
        if (await writable.fetchone())[0]:
            raise QueryRejected("A dedicated read-only PostgreSQL identity is required.")
        # Stabilize relation definitions across metadata validation and both
        # cursor plans. The lock also refuses unsupported foreign relations.
        locks = compose.SQL(", ").join(compose.Identifier(schema, table) for schema, table in sorted(allowed))
        await run(compose.SQL("LOCK TABLE {} IN ACCESS SHARE MODE").format(locks))
        # A bounded catalog preflight prevents overloaded operators/casts on
        # custom source types. Granted views themselves remain trusted.
        types = await run("SELECT table_schema, table_name, udt_schema, udt_name, domain_name "
                          "FROM (" + _metadata_query(allowed) + ") AS source_columns LIMIT 2001")
        columns = await types.fetchmany(2001)
        if (len(columns) > 2000 or {(r[0], r[1]) for r in columns} != allowed
                or (not metadata and any(r[2] != "pg_catalog" or r[3] not in _UDT or r[4] is not None for r in columns))):
            raise QueryRejected("Source relations or column types are unsupported.")
        if cancelled and await asyncio.to_thread(cancelled):
            raise QueryExecutionError("Query cancelled.", confirmed=True, cancelled=True)
        if on_started:
            await asyncio.to_thread(on_started, conn.info.backend_pid)
        # A server cursor's initial FETCH 0 obtains names without materializing
        # the result. A second server cursor bounds each wire row in PostgreSQL
        # before libpq or Python allocates it. Duplicate column names survive.
        cursor = conn.cursor(name="assistant_columns")
        await _watch(conn, cursor.execute(query), deadline, cancelled)
        names = [c.name for c in cursor.description]
        await _watch(conn, cursor.close(), deadline, cancelled)
        if len(names) > 200 or len(json.dumps(names).encode()) > max_bytes // 2:
            raise QueryRejected("Query result columns exceed the supported size.")
        wrapper = compose.SQL("SELECT pg_catalog.substring(pg_catalog.convert_to("
                              "pg_catalog.row_to_json(result_row)::text, 'UTF8'), 1, {}) "
                              "FROM ({}) AS result_row LIMIT {}").format(
                                  compose.Literal(max_bytes + 1), compose.SQL(query), compose.Literal(max_rows + 1))
        rows = []
        truncated = False
        result = dict(columns=names, rows=rows, truncated=False, elapsed_ms=999999,
                      query=query, executed_at=datetime.now(timezone.utc).isoformat())
        used = len(json.dumps(result, allow_nan=False).encode("utf-8"))
        if used > max_bytes:
            raise QueryRejected("Query evidence exceeds the supported byte size.")
        cursor = conn.cursor(name="assistant_rows")
        await _watch(conn, cursor.execute(wrapper), deadline, cancelled)
        for index in range(max_rows + 1):
            row = await _watch(conn, cursor.fetchone(), deadline, cancelled)
            if row is None:
                break
            raw = bytes(row[0])
            if index == max_rows or len(raw) > max_bytes or used + len(raw) > max_bytes:
                truncated = True
                break
            pairs = json.loads(raw, object_pairs_hook=lambda pairs: pairs,
                               parse_float=str, parse_int=int)
            values = [value for _, value in pairs]
            # PostgreSQL renders numeric decimals losslessly; parse_float
            # keeps that precision as strings. Date/time/UUID/interval
            # values arrive as ISO/text JSON scalars, never driver objects.
            if any(isinstance(value, (list, dict)) for value in values):
                raise QueryRejected("Query result type is unsupported.")
            # Bound the complete JSON envelope, including ASCII escaping,
            # decimal strings, evidence SQL and column names.
            row_size = len(json.dumps(values, allow_nan=False).encode("utf-8")) + (2 if rows else 0)
            if used + row_size > max_bytes:
                truncated = True
                break
            used += row_size
            rows.append(values)
        result.update(truncated=truncated, elapsed_ms=int((time.monotonic() - started) * 1000))
        return result
    finally:
        # Never commit, including successful reads. Closing rolls back and also
        # avoids an unbounded network roundtrip during error cleanup.
        await conn.close()


def _dispatch(conn_info, query, allowed, *, timeout_seconds=10, max_rows=200,
              max_bytes=65536, cancelled=None, on_started=None, metadata=False):
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not 0.05 <= timeout_seconds <= 60
            or type(max_rows) is not int or not 1 <= max_rows <= 1000
            or type(max_bytes) is not int or not 1024 <= max_bytes <= 1048576):
        raise QueryRejected("Execution bounds are unsupported.")
    # Record the effective output cap in evidence. The extra row only detects
    # truncation; the cap makes no promise about work scanned by PostgreSQL.
    query = (exp.select("*").from_(exp.Subquery(this=_parse(query),
              alias=exp.TableAlias(this=exp.to_identifier("assistant_result"))))
             .limit(max_rows + 1).sql(dialect="postgres", identify=True, comments=False,
                                      unsupported_level=sqlglot.ErrorLevel.RAISE))
    options = _connection_options(conn_info, timeout_seconds)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise QueryRejected("Query execution requires a synchronous worker.")
    options["hostaddr"] = _resolve_address(options, timeout_seconds)
    try:
        return asyncio.run(_execute(options, query, allowed, timeout_seconds, max_rows,
                                    max_bytes, cancelled, on_started, metadata))
    except (QueryRejected, QueryExecutionError):
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "57014":
            raise QueryExecutionError("Query timed out or was cancelled by PostgreSQL.", confirmed=True) from None
        raise QueryExecutionError("PostgreSQL query failed.", confirmed=bool(getattr(exc, "sqlstate", None))) from None


def execute_query(conn_info: dict, sql: str, relations: list[str], *, timeout_seconds=10,
                  max_rows=200, max_bytes=65536, cancelled=None, on_started=None) -> dict:
    """Execute a validated query with server deadlines and bounded result bytes."""
    query = validate_query(sql, relations)
    return _dispatch(conn_info, query, _relations(relations), timeout_seconds=timeout_seconds,
                     max_rows=max_rows, max_bytes=max_bytes, cancelled=cancelled, on_started=on_started)


def describe_source(conn_info: dict, relations: list[str], **kwargs) -> dict:
    """Read only granted column metadata through the same bounded executor."""
    allowed = _relations(relations)
    return _dispatch(conn_info, _metadata_query(allowed), allowed, metadata=True, **kwargs)
