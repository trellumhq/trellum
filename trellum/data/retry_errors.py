"""Driver-aware classification of failures that can recover on a new connection."""

from __future__ import annotations

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
_PERMANENT_PHRASES = (
    "authentication failed",
    "access denied",
    "permission denied",
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
            children = (current.__cause__, current.__context__)
        except Exception:
            children = ()
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
                or state[:2] in _AUTH_SQLSTATES
                or state[:2] in _PERMANENT_SQLSTATES
            ):
                return False
    for current in chain:
        for value in _metadata(current, "errno", "errorcode", "code"):
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
                return True
        # Only these narrow phrases identify a broken transport without driver
        # codes; a generic "connection error" is too ambiguous to retry.
        message = str(current).lower()
        if any(phrase in message for phrase in _TRANSPORT_PHRASES):
            return True
    return False
