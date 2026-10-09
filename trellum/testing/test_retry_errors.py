import importlib
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
@pytest.mark.parametrize("state", ["28000", "42501", "42601"])
def test_auth_permission_and_syntax_sqlstates_are_permanent(source, state):
    assert not is_retryable_connection_error(DriverError(sqlstate=state), source)


def _driver_exception(module, name, *args, **kwargs):
    """Use the installed optional class, or a small stub with its real identity."""
    try:
        cls = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        cls = type(name, (Exception,), {"__module__": module})
    return cls(*args, **kwargs)


@pytest.mark.parametrize("source", REMOTE_SOURCES)
def test_builtin_transport_exception_is_supported_for_remote_sources(source):
    assert is_retryable_connection_error(ConnectionResetError("reset"), source)


def test_actual_vertica_connection_closed_by_vertica_error():
    error = _driver_exception(
        "vertica_python.errors", "ConnectionError", "Connection closed by Vertica"
    )
    assert is_retryable_connection_error(error, "vertica")


def test_psycopg2_server_closed_connection_message():
    error = _driver_exception(
        "psycopg2", "OperationalError", "server closed the connection unexpectedly"
    )
    assert is_retryable_connection_error(error, "postgres")


@pytest.mark.parametrize("name", ["AdminShutdown", "CrashShutdown", "CannotConnectNow"])
def test_postgres_administrative_shutdown_exceptions(name):
    error = _driver_exception("psycopg2.errors", name, "server is shutting down")
    assert is_retryable_connection_error(error, "postgres")


def test_mysql_operational_error_code_in_args():
    error = _driver_exception(
        "pymysql.err", "OperationalError", 2013, "Lost connection to MySQL server during query"
    )
    assert is_retryable_connection_error(error, "mysql")
    auth = _driver_exception("pymysql.err", "OperationalError", 1045, "Access denied for user")
    assert not is_retryable_connection_error(auth, "mysql")


@pytest.mark.parametrize("code", [20006, 20009])
def test_pymssql_dblib_transport_codes_in_bytes_args(code):
    text = (
        b"DB-Lib error message: Write to the server failed"
        if code == 20006
        else b"Unable to connect: server unavailable"
    )
    error = _driver_exception("pymssql", "OperationalError", code, text)
    assert is_retryable_connection_error(error, "sqlserver")


def test_pymssql_login_failure_code_is_permanent():
    error = _driver_exception("pymssql", "OperationalError", 18456, b"Login failed for user")
    assert not is_retryable_connection_error(error, "sqlserver")


def test_snowflake_connection_error_code_and_nonretryable_tls_subclass():
    error = _driver_exception("snowflake.connector.errors", "OperationalError", "connect failed")
    error.errno = 250001
    assert is_retryable_connection_error(error, "snowflake")
    tls_error = _driver_exception(
        "snowflake.connector.errors", "NonRetryableTlsError", "TLS verification failed"
    )
    tls_error.errno = 250001
    assert not is_retryable_connection_error(tls_error, "snowflake")


def test_clickhouse_network_error_code_attribute_and_text_form():
    coded = _driver_exception("clickhouse_driver.errors", "NetworkError", "network failure")
    if not hasattr(coded, "code"):
        coded.code = 210
    text = _driver_exception(
        "clickhouse_connect.driver.exceptions",
        "OperationalError",
        "Code: 210. DB::NetException: Connection reset by peer",
    )
    assert is_retryable_connection_error(coded, "clickhouse")
    assert is_retryable_connection_error(text, "clickhouse")


def test_trino_connection_exception_and_authentication_exception():
    connection_error = _driver_exception(
        "trino.exceptions", "TrinoConnectionError", "connection failed"
    )
    auth_error = _driver_exception("trino.exceptions", "TrinoAuthError", "connection reset by peer")
    assert is_retryable_connection_error(connection_error, "trino")
    assert not is_retryable_connection_error(auth_error, "trino")


def test_databricks_request_error_original_transport_cause():
    original = ConnectionResetError("reset by peer")
    error = _driver_exception(
        "databricks.sql.exc",
        "RequestError",
        "request failed",
        context={"original-exception": original},
    )
    assert is_retryable_connection_error(error, "databricks")
    service_error = _driver_exception(
        "databricks.sql.exc",
        "RequestError",
        "service unavailable",
        context={"http-code": 503},
    )
    auth_error = _driver_exception(
        "databricks.sql.exc",
        "RequestError",
        "unauthorized",
        context={"http-code": 401},
    )
    assert is_retryable_connection_error(service_error, "databricks")
    assert not is_retryable_connection_error(auth_error, "databricks")


def test_bigquery_backend_service_status_is_retryable():
    service_error = _driver_exception(
        "google.api_core.exceptions", "ServiceUnavailable", "backend unavailable"
    )
    bad_request = _driver_exception("google.api_core.exceptions", "BadRequest", "invalid query")
    if not getattr(service_error, "code", None):
        service_error.code = 503
    if not getattr(bad_request, "code", None):
        bad_request.code = 400
    assert is_retryable_connection_error(service_error, "bigquery")
    assert not is_retryable_connection_error(bad_request, "bigquery")


@pytest.mark.parametrize("state", ["08004", "08007", "08P01"])
def test_connection_state_rejections_and_protocol_failures_are_permanent(state):
    assert not is_retryable_connection_error(DriverError(sqlstate=state), "postgres")


def test_wrapped_permanent_error_vetoes_outer_generic_connection_error():
    inner = DriverError("permission denied", sqlstate="42501")
    outer = ConnectionError("Vertica connection failed")
    outer.__cause__ = inner
    assert not is_retryable_connection_error(outer, "vertica")


def test_wrapped_bad_credentials_vetoes_vertica_connection_wrapper():
    inner = DriverError("authentication failed", sqlstate="28000")
    outer = ConnectionError("Failed to connect to Vertica")
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


def test_programming_error_with_transport_word_does_not_retry():
    error = _driver_exception("pymysql.err", "ProgrammingError", "connection reset by peer")
    assert not is_retryable_connection_error(error, "mysql")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_status_vetoes_incidental_transport_cause(status):
    error = _driver_exception(
        "databricks.sql.exc", "RequestError", "request rejected",
        context={"http-code": status, "original-exception": ConnectionResetError()},
    )
    assert not is_retryable_connection_error(error, "databricks")
    error = DriverError(code=status)
    error.__cause__ = ConnectionResetError()
    assert not is_retryable_connection_error(error, "bigquery")
