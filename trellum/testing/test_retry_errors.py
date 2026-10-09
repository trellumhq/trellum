import sqlite3

import pytest

from trellum.data.retry_errors import is_retryable_connection_error

REMOTE_SOURCES = (
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
)


class DriverError(Exception):
    def __init__(self, message="driver error", *, sqlstate=None, errno=None, code=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.errno = errno
        self.code = code


@pytest.mark.parametrize("source", REMOTE_SOURCES)
def test_builtin_transport_errors_are_retryable_for_remote_drivers(source):
    assert is_retryable_connection_error(ConnectionResetError("reset"), source)


@pytest.mark.parametrize("source", REMOTE_SOURCES)
@pytest.mark.parametrize("state", ["28000", "42501", "42601"])
def test_auth_permission_and_syntax_sqlstates_are_permanent(source, state):
    assert not is_retryable_connection_error(DriverError(sqlstate=state), source)


@pytest.mark.parametrize("source", REMOTE_SOURCES)
def test_connection_sqlstate_class_is_retryable(source):
    assert is_retryable_connection_error(DriverError(sqlstate="08006"), source)


def test_driver_specific_mysql_and_clickhouse_transport_codes():
    assert is_retryable_connection_error(DriverError(errno=2013), "mysql")
    assert is_retryable_connection_error(DriverError(code=210), "clickhouse")
    assert not is_retryable_connection_error(DriverError(errno=1045), "mysql")


def test_bigquery_backend_service_status_is_retryable():
    assert is_retryable_connection_error(DriverError(code=503), "bigquery")
    assert not is_retryable_connection_error(DriverError(code=400), "bigquery")


def test_wrapped_permanent_error_vetoes_outer_generic_connection_error():
    inner = DriverError("permission denied", sqlstate="42501")
    outer = ConnectionError("Vertica connection failed")
    outer.__cause__ = inner
    assert not is_retryable_connection_error(outer, "vertica")


def test_exception_chain_cycles_are_safe():
    first = ConnectionError("wrapper")
    second = DriverError("server has gone away", sqlstate="08006")
    first.__cause__ = second
    second.__context__ = first
    assert is_retryable_connection_error(first, "vertica")


@pytest.mark.parametrize("source", ["sqlite", "duckdb"])
def test_local_database_errors_are_not_retryable(source):
    assert not is_retryable_connection_error(TimeoutError("network timeout"), source)
    assert not is_retryable_connection_error(sqlite3.OperationalError("database is locked"), source)


@pytest.mark.parametrize("source", REMOTE_SOURCES)
def test_ordinary_operational_error_is_not_blanket_retryable(source):
    error = sqlite3.OperationalError("operation failed")
    assert not is_retryable_connection_error(error, source)


def test_unknown_source_fails_closed():
    assert not is_retryable_connection_error(ConnectionResetError(), "custom")


@pytest.mark.parametrize(
    "error",
    [
        DriverError("query canceled", sqlstate="57014"),
        DriverError("out of memory"),
        DriverError("certificate verify failed"),
        MemoryError("allocation failed"),
    ],
)
def test_cancellation_oom_and_tls_verification_are_permanent(error):
    assert not is_retryable_connection_error(error, "vertica")
