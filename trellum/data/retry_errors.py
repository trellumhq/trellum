"""Driver-aware classification of failures that can recover on a new connection."""

from __future__ import annotations

import re
import socket
import ssl

_REMOTE_SOURCES = {
    "vertica",
    "postgres",
    "redshift",
    "mysql",
    "sqlserver",
    "snowflake",
    "clickhouse",
    "bigquery",
    "trino",
    "databricks",
}
_AUTH_SQLSTATES = {"28"}
_PERMANENT_SQLSTATES = {"22", "23", "42", "53"}
_MYSQL_CONNECTION_ERRNOS = {2002, 2003, 2006, 2013, 2055}
_CLICKHOUSE_CONNECTION_CODES = {210}
_BIGQUERY_TRANSIENT_STATUSES = {500, 502, 503, 504}
_SQLSERVER_CONNECTION_CODES = {20006, 20009}
_POSTGRES_SHUTDOWN_SQLSTATES = {"57P01", "57P02", "57P03"}
_POSTGRES_SHUTDOWN_ERRORS = {"AdminShutdown", "CrashShutdown", "CannotConnectNow"}
_INVALID_SQLSTATES = {"08004", "08007", "08P01"}
_PERMANENT_PHRASES = (
    "authentication failed",
    "access denied",
    "permission denied",
    "login failed",
    "invalid configuration",
    "invalid connection option",
    "invalid url",
    "invalid uri",
    "unsupported authentication method",
    "certificate verify failed",
    "certificate verification failed",
    "hostname mismatch",
    "self-signed certificate",
    "unknown ca",
    "query canceled",
    "query cancelled",
    "query was canceled",
    "query was cancelled",
    "out of memory",
    "memory limit exceeded",
)
_TRANSPORT_PHRASES = (
    "connection reset by peer",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "broken pipe",
    "server has gone away",
    "lost connection to",
    "unexpected eof",
    "unexpected end of file",
    "read timed out",
    "operation timed out",
    "connection timed out",
    "connection aborted",
)


def _exception_chain(exc: Exception):
    """Walk explicit wrappers safely, including drivers that reuse cause cycles."""
    pending = [exc]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        try:
            children = [current.__cause__, current.__context__]
        except Exception:
            children = []
        # Databricks SQL stores the original transport exception in its
        # documented context mapping instead of always chaining it directly.
        try:
            context = current.context
            if isinstance(context, dict):
                children.append(context.get("original-exception"))
        except Exception:
            pass
        for child in children:
            if isinstance(child, BaseException) and id(child) not in seen:
                pending.append(child)


def _metadata(exc: Exception, *names):
    for name in names:
        try:
            value = getattr(exc, name, None)
        except Exception:
            continue
        if value is not None:
            yield value


def _is_class(exc: Exception, module: str, name: str) -> bool:
    return any(cls.__module__ == module and cls.__name__ == name for cls in type(exc).__mro__)


def _numeric_args(exc: Exception):
    """Read DB-API's leading numeric code, used by PyMySQL and pymssql."""
    try:
        args = exc.args
    except Exception:
        return
    if args and isinstance(args[0], (int, str)):
        try:
            yield int(args[0])
        except ValueError:
            pass


def is_retryable_connection_error(exc: Exception, source_type: str) -> bool:
    """Return whether opening a fresh connection could plausibly recover ``exc``.

    Local database errors are deliberately excluded: opening another SQLite or
    DuckDB connection cannot repair a file lock, corrupt file, or closed handle.
    """
    source = str(source_type).lower()
    if source not in _REMOTE_SOURCES:
        return False

    chain = tuple(_exception_chain(exc))
    # A wrapped server-side auth, permission, or SQL error vetoes a generic
    # outer ConnectionError (notably Vertica's connector wrapper).
    for current in chain:
        class_names = {cls.__name__ for cls in type(current).__mro__}
        if class_names & {
            "ProgrammingError",
            "ValueError",
            "TypeError",
            "InvalidURL",
            "InvalidSchema",
            "MissingSchema",
            "ConfigurationError",
            "TrinoAuthError",
            "NonRetryableTlsError",
        }:
            return False
        if isinstance(current, (MemoryError, ssl.SSLCertVerificationError)):
            return False
        if isinstance(current, BaseException):
            message = str(current).lower()
            if any(phrase in message for phrase in _PERMANENT_PHRASES):
                return False
        for value in _metadata(current, "sqlstate", "pgcode"):
            state = str(value).upper()
            if (
                state == "57014"
                or state in _INVALID_SQLSTATES
                or state[:2] in _AUTH_SQLSTATES
                or state[:2] in _PERMANENT_SQLSTATES
            ):
                return False
    for current in chain:
        class_names = {cls.__name__ for cls in type(current).__mro__}
        for value in (*_metadata(current, "errno", "errorcode", "code"), *_numeric_args(current)):
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if source == "mysql" and number in _MYSQL_CONNECTION_ERRNOS:
                return True
            if source == "clickhouse" and number in _CLICKHOUSE_CONNECTION_CODES:
                return True
            if source == "bigquery" and number in _BIGQUERY_TRANSIENT_STATUSES:
                return True
            if source == "sqlserver" and number in _SQLSERVER_CONNECTION_CODES:
                return True
            # Snowflake documents 250001 as a backend connection failure.
            # Its newer NonRetryableTlsError subtype is vetoed above.
            if source == "snowflake" and number == 250001:
                return True
        if source in {"postgres", "redshift"} and any(
            str(value).upper() in _POSTGRES_SHUTDOWN_SQLSTATES
            for value in _metadata(current, "sqlstate", "pgcode")
        ):
            return True
        if source in {"postgres", "redshift"} and class_names & _POSTGRES_SHUTDOWN_ERRORS:
            return True
        if source == "trino" and any(
            _is_class(current, "trino.exceptions", name)
            for name in ("TrinoConnectionError", "Http503Error", "Http504Error")
        ):
            return True
        if source == "databricks" and _is_class(current, "databricks.sql.exc", "RequestError"):
            for context in _metadata(current, "context"):
                if (
                    isinstance(context, dict)
                    and context.get("http-code") in _BIGQUERY_TRANSIENT_STATUSES
                ):
                    return True
        for current_type in type(current).__mro__:
            if current_type in (
                ConnectionResetError,
                ConnectionAbortedError,
                ConnectionRefusedError,
                BrokenPipeError,
                TimeoutError,
                socket.timeout,
            ):
                return True
        if isinstance(current, socket.gaierror) and current.errno == socket.EAI_AGAIN:
            return True
        if isinstance(current, OSError) and current.errno in {
            101,  # ENETUNREACH
            113,  # EHOSTUNREACH
            10051,  # Windows network unreachable
            10065,  # Windows host unreachable
        }:
            return True

    for current in chain:
        for value in _metadata(current, "sqlstate", "pgcode"):
            state = str(value).upper()
            if state.startswith("08"):
                if state in _INVALID_SQLSTATES:
                    return False
                return True
        # Only these narrow phrases identify a broken transport without driver
        # codes; a generic "connection error" is too ambiguous to retry.
        message = str(current).lower()
        if source == "vertica" and _is_class(current, "vertica_python.errors", "ConnectionError"):
            if message.strip() == "connection closed by vertica":
                return True
        if "server closed the connection unexpectedly" in message:
            return True
        if source == "clickhouse" and re.search(r"\bcode:\s*210\b", message):
            return True
        if any(phrase in message for phrase in _TRANSPORT_PHRASES):
            return True
    return False
