"""Snowflake connector 5 compatibility, checked against the installed connector.

No Snowflake account or internet is used: connection options are validated by
the connector itself, and logins go to a throwaway HTTP server on loopback.
"""

import json
import threading
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock

import pandas as pd
import pytest

snowflake_connector = pytest.importorskip("snowflake.connector")

from trellum.data import fetch  # noqa: E402
from trellum.data.drivers import snowflake_driver  # noqa: E402
from trellum.data.drivers.snowflake_driver import SnowflakeDriver  # noqa: E402
from trellum.data.retry_errors import is_retryable_connection_error  # noqa: E402

PASSWORD_INFO = {
    "account": "xy12345.eu-west-1",
    "user": "bi_user",
    "password": "secret",
    "warehouse": "COMPUTE_WH",
    "database": "ANALYTICS",
    "schema": "REPORTING",
}


@pytest.fixture
def driver_kwargs(monkeypatch):
    """Call the driver; return the keyword arguments it gives snowflake.connector.connect."""
    connect = MagicMock(return_value=object())
    monkeypatch.setattr(snowflake_connector, "connect", connect)

    def run(conn_info, platform="linux"):
        connect.reset_mock()
        monkeypatch.setattr(snowflake_driver.sys, "platform", platform)
        SnowflakeDriver().connect(conn_info)
        connect.assert_called_once()
        return connect.call_args.kwargs

    return run


def test_password_login_keeps_account_and_session_defaults(driver_kwargs):
    kwargs = driver_kwargs(PASSWORD_INFO)
    assert kwargs["account"] == "xy12345.eu-west-1"
    assert kwargs["user"] == "bi_user"
    assert kwargs["password"] == "secret"
    assert kwargs["warehouse"] == "COMPUTE_WH"
    assert kwargs["database"] == "ANALYTICS"
    assert kwargs["schema"] == "REPORTING"
    assert "authenticator" not in kwargs


def test_unset_schema_defaults_to_public_and_unset_names_stay_unset(driver_kwargs):
    kwargs = driver_kwargs({"account": "a", "user": "u", "password": "p"})
    assert kwargs["schema"] == "PUBLIC"
    assert kwargs["warehouse"] is None and kwargs["database"] is None


def test_missing_password_uses_external_browser(driver_kwargs):
    kwargs = driver_kwargs({"account": "a", "user": "u", "password": ""})
    assert kwargs["authenticator"] == "externalbrowser"
    assert "password" not in kwargs


@pytest.mark.parametrize("password", ["secret", None])
def test_linux_keeps_connector_4_credential_store_policy(driver_kwargs, password):
    info = {"account": "a", "user": "u", "password": password}
    assert driver_kwargs(info)["client_store_temporary_credential"] is False


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_macos_and_windows_keep_connector_default_credential_caching(driver_kwargs, platform):
    kwargs = driver_kwargs({"account": "a", "user": "u"}, platform)
    assert "client_store_temporary_credential" not in kwargs


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_certificate_revocation_is_checked_best_effort(driver_kwargs, platform):
    assert driver_kwargs(PASSWORD_INFO, platform)["crl_check_mode"] == "ADVISORY"


@pytest.mark.parametrize("platform", ["linux", "darwin"])
@pytest.mark.parametrize("password", ["secret", None])
def test_connector_accepts_every_option_the_driver_sends(driver_kwargs, platform, password):
    """Nothing is renamed, ignored, deprecated or rejected by connector 5."""
    kwargs = driver_kwargs({**PASSWORD_INFO, "password": password}, platform)
    config_class = snowflake_connector.ConnectionConfig
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        config = config_class.from_kwargs(**kwargs)
    assert config._extra == {}
    for name, value in kwargs.items():
        assert getattr(config, name) == value
    assert not {key.lower() for key in kwargs} & (
        set(config_class._UNSUPPORTED_PARAMS)
        | set(config_class._REJECTED_PARAMS)
        | set(config_class._DEPRECATED_REWRITES)
    )


class _LoginServer:
    """A loopback HTTP endpoint that answers Snowflake login requests."""

    def __init__(self, status, body):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.paths.append(self.path)
                self.rfile.read(int(self.headers.get("content-length", 0)))
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.paths = []
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _login(monkeypatch, status, body):
    """Run the driver against a fake login endpoint; return (error, server)."""
    server = _LoginServer(status, body)
    real_connect = snowflake_connector.connect
    port = server.server.server_address[1]

    def connect(**kwargs):
        # Only the destination changes; every option comes from the driver.
        return real_connect(host="127.0.0.1", port=port, protocol="http", login_timeout=20, **kwargs)

    monkeypatch.setattr(snowflake_connector, "connect", connect)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(snowflake_connector.Error) as caught:
                SnowflakeDriver().connect(PASSWORD_INFO)
    finally:
        server.close()
    return caught.value, server


def test_real_connector_reaches_login_with_the_drivers_options(monkeypatch):
    error, server = _login(
        monkeypatch,
        200,
        {"data": None, "code": "390100", "message": "Incorrect username or password was specified.", "success": False},
    )
    assert server.paths, "the connector never sent a login request"
    assert "warehouse=COMPUTE_WH" in server.paths[0]
    assert "databaseName=ANALYTICS" in server.paths[0]
    assert "schemaName=REPORTING" in server.paths[0]
    assert isinstance(error, snowflake_connector.DatabaseError)
    assert error.errno == 390100 and error.sqlstate == "28000"
    assert not is_retryable_connection_error(error, "snowflake")


def test_unavailable_backend_is_still_retried(monkeypatch):
    error, _ = _login(monkeypatch, 503, {"message": "unavailable"})
    assert error.errno == 250001
    assert is_retryable_connection_error(error, "snowflake")


def _error(name, message, errno, sqlstate):
    return getattr(snowflake_connector, name)(message, errno=errno, sqlstate=sqlstate)


def test_login_timeout_is_retried_like_connector_4_connection_failures():
    # Connector 5 reports an unreachable endpoint as errno 14 / HYT00 after
    # login_timeout; connector 4 reported it as errno 250001.
    error = _error("OperationalError", "Failed to login: login timed out after 120s", 14, "HYT00")
    assert is_retryable_connection_error(error, "snowflake")


@pytest.mark.parametrize(
    "name, message, errno, sqlstate",
    [
        ("DatabaseError", "Failed to login: Login error: Incorrect username or password was specified.", 390100, "28000"),
        ("DatabaseError", "Failed to login: Login error: JWT token is invalid.", 390144, "28000"),
        (
            "DatabaseError",
            "Failed to login: HTTP request failed after retries: login request. "
            "invalid peer certificate: Other(OtherError(CaUsedAsEndEntity))",
            250001,
            "08001",
        ),
        ("DatabaseError", "Failed to login: invalid peer certificate: Revoked", 250001, "08001"),
        ("ProgrammingError", "Configuration error: crl_check_mode: Invalid crl_check_mode 'x'", 251007, None),
        ("ProgrammingError", "SQL compilation error: syntax error", 1003, "42000"),
    ],
)
def test_permanent_failures_are_not_retried(name, message, errno, sqlstate):
    assert not is_retryable_connection_error(_error(name, message, errno, sqlstate), "snowflake")


class _ConnectorCursor:
    """Stands in for a cursor; the connection class below is the real one."""

    def __init__(self, frame=None, error=None, close_error=None):
        self.frame, self.error, self.close_error = frame, error, close_error
        self.executed, self.closed = [], 0

    def execute(self, sql):
        self.executed.append(sql)

    def fetch_pandas_all(self):
        if self.error:
            raise self.error
        return self.frame

    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error


def _real_connection(cursor):
    """An instance of the installed connector's connection class."""
    connection_class = snowflake_connector.Connection
    connection = object.__new__(connection_class)
    connection.cursor = lambda: cursor
    return connection


def test_installed_classes_route_to_the_snowflake_fetch_path():
    connection_class = snowflake_connector.Connection
    assert "snowflake.connector" in connection_class.__module__ + "." + connection_class.__qualname__
    for method in ("cursor", "close", "__enter__", "__exit__"):
        assert callable(getattr(connection_class, method))
    for method in ("execute", "fetch_pandas_all", "close", "__enter__", "__exit__"):
        assert callable(getattr(snowflake_connector.SnowflakeCursor, method))
    assert hasattr(snowflake_connector.SnowflakeCursor, "description")


def test_execute_query_uses_fetch_pandas_all_and_closes_the_cursor():
    frame = pd.DataFrame({"city": ["a", "a", "b", "b"], "n": [1, 2, 3, 4]})
    cursor = _ConnectorCursor(frame=frame)
    result = fetch.execute_query(_real_connection(cursor), "SELECT 1", compact_text=True)
    assert cursor.executed == ["SELECT 1"] and cursor.closed == 1
    assert result.n.tolist() == [1, 2, 3, 4]
    assert isinstance(result.city.dtype, pd.CategoricalDtype)


def test_fetch_failure_is_not_replaced_by_a_cursor_close_failure():
    original = snowflake_connector.OperationalError("lost", errno=250001, sqlstate="08001")
    cursor = _ConnectorCursor(error=original, close_error=snowflake_connector.InterfaceError("closed", errno=252006))
    with pytest.raises(snowflake_connector.OperationalError) as caught:
        fetch.execute_query(_real_connection(cursor), "SELECT 1")
    assert caught.value is original and cursor.closed == 1
