"""Unit tests for multi-data-source connection system.

Tests cover:
- Driver registry (all types registered, correct default ports)
- LocalEnvResolver (standard + BigQuery + Snowflake + file env var patterns)
- _execute_query dispatch (BigQuery, Snowflake, ClickHouse, DB-API 2.0)
- read_source (Excel, CSV, Parquet, Google Sheets, OneDrive)
- read_api (GET/POST, JSON path extraction)
- ReportContext.read_source (integration with report.yaml config)
- query_df backward compatibility (caching, params, Decimal conversion)

All tests use mocks. No real database connections or network calls.
"""

import importlib.util
import os
import sys
import types
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# These optional drivers live in requirements-drivers.txt, not the core
# requirements.txt. A core-only venv won't have them installed.
def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        # find_spec("pkg.sub") raises (rather than returning None) when the
        # parent package "pkg" isn't importable at all -- e.g. no `google`
        # namespace package present in a core-only venv.
        return False


_HAS_MSAL = _has_module("msal")


# ── Driver Registry ─────────────────────────────────────────────────────


class TestDriverRegistry:
    """Every driver type must be registered with correct default port."""

    EXPECTED_DRIVERS = {
        "vertica": 5433,
        "bigquery": 0,
        "postgres": 5432,
        "mysql": 3306,
        "snowflake": 443,
        "clickhouse": 8443,
        "sqlite": 0,
        "duckdb": 0,
        "sqlserver": 1433,
        "redshift": 5439,
        "trino": 8080,
        "databricks": 443,
    }

    def test_all_drivers_registered(self):
        from trellum.data.drivers import get_driver
        for type_name in self.EXPECTED_DRIVERS:
            driver = get_driver(type_name)
            assert driver is not None, f"Driver '{type_name}' not registered"

    @pytest.mark.parametrize("type_name,expected_port", EXPECTED_DRIVERS.items())
    def test_default_ports(self, type_name, expected_port):
        from trellum.data.drivers import get_driver
        driver = get_driver(type_name)
        assert driver.default_port == expected_port

    def test_unknown_driver_raises(self):
        from trellum.data.drivers import get_driver
        with pytest.raises(ValueError, match="No driver registered"):
            get_driver("nonexistent_db")


# ── Driver Connect Methods ───────────────────────────────────────────────


class TestBigQueryDriver:
    """BigQuery driver must call google.cloud.bigquery.Client."""

    def test_connect_with_project(self):
        mock_client = MagicMock()
        mock_cls = MagicMock(return_value=mock_client)
        google_mod = types.ModuleType("google")
        cloud_mod = types.ModuleType("google.cloud")
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = mock_cls
        oauth2_mod = types.ModuleType("google.oauth2")
        sa_mod = types.ModuleType("google.oauth2.service_account")
        sa_mod.Credentials = MagicMock()
        cloud_mod.bigquery = bq_mod
        oauth2_mod.service_account = sa_mod
        fake_modules = {
            "google": google_mod,
            "google.cloud": cloud_mod,
            "google.cloud.bigquery": bq_mod,
            "google.oauth2": oauth2_mod,
            "google.oauth2.service_account": sa_mod,
        }
        with patch.dict(sys.modules, fake_modules):
            from trellum.data.drivers.bigquery import BigQueryDriver
            BigQueryDriver().connect({"project": "my-project"})
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            assert call_kwargs[1].get("project") == "my-project" or (
                call_kwargs[0] and "my-project" in str(call_kwargs)
            )

    def test_inline_json_wins_over_the_file(self):
        """Same order as the Google Sheets reader: json, then path, then ADC."""
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = MagicMock()
        sa_mod = types.ModuleType("google.oauth2.service_account")
        sa_mod.Credentials = MagicMock()
        google_mod, cloud_mod, oauth2_mod = (
            types.ModuleType(n) for n in ("google", "google.cloud", "google.oauth2")
        )
        cloud_mod.bigquery, oauth2_mod.service_account = bq_mod, sa_mod
        fake_modules = {
            "google": google_mod, "google.cloud": cloud_mod, "google.cloud.bigquery": bq_mod,
            "google.oauth2": oauth2_mod, "google.oauth2.service_account": sa_mod,
        }
        with patch.dict(sys.modules, fake_modules):
            from trellum.data.drivers.bigquery import BigQueryDriver

            BigQueryDriver().connect({
                "credentials_json": '{"type": "service_account"}',
                "credentials_path": "/missing/sa.json",
            })
        sa_mod.Credentials.from_service_account_info.assert_called_once_with(
            {"type": "service_account"}
        )
        sa_mod.Credentials.from_service_account_file.assert_not_called()
        assert bq_mod.Client.call_args[1]["credentials"] is (
            sa_mod.Credentials.from_service_account_info.return_value
        )

    def test_connect_with_api_endpoint_uses_anonymous_credentials(self):
        """api_endpoint (private endpoint / emulator) sets client_options and,
        absent real credentials, an AnonymousCredentials instance."""
        mock_client = MagicMock()
        mock_cls = MagicMock(return_value=mock_client)
        google_mod = types.ModuleType("google")
        cloud_mod = types.ModuleType("google.cloud")
        bq_mod = types.ModuleType("google.cloud.bigquery")
        bq_mod.Client = mock_cls
        oauth2_mod = types.ModuleType("google.oauth2")
        sa_mod = types.ModuleType("google.oauth2.service_account")
        sa_mod.Credentials = MagicMock()
        auth_mod = types.ModuleType("google.auth")
        auth_credentials_mod = types.ModuleType("google.auth.credentials")
        mock_anon_cls = MagicMock()
        auth_credentials_mod.AnonymousCredentials = mock_anon_cls
        auth_mod.credentials = auth_credentials_mod
        cloud_mod.bigquery = bq_mod
        oauth2_mod.service_account = sa_mod
        fake_modules = {
            "google": google_mod,
            "google.cloud": cloud_mod,
            "google.cloud.bigquery": bq_mod,
            "google.oauth2": oauth2_mod,
            "google.oauth2.service_account": sa_mod,
            "google.auth": auth_mod,
            "google.auth.credentials": auth_credentials_mod,
        }
        with patch.dict(sys.modules, fake_modules):
            from trellum.data.drivers.bigquery import BigQueryDriver
            BigQueryDriver().connect({
                "project": "itest-project",
                "api_endpoint": "http://localhost:9050",
            })
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["client_options"] == {"api_endpoint": "http://localhost:9050"}
            mock_anon_cls.assert_called_once()
            assert call_kwargs["credentials"] is mock_anon_cls.return_value


class TestPostgreSQLDriver:
    """PostgreSQL driver must call psycopg2.connect with correct args."""

    def test_connect(self):
        mock_conn = MagicMock()
        mock_psycopg2 = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            from trellum.data.drivers.postgres import PostgreSQLDriver
            result = PostgreSQLDriver().connect({
                "host": "localhost",
                "port": 5432,
                "database": "testdb",
                "user": "testuser",
                "password": "testpass",
            })
            mock_psycopg2.connect.assert_called_once()
            args = mock_psycopg2.connect.call_args
            assert args[1]["host"] == "localhost"
            assert args[1]["user"] == "testuser"


class TestMySQLDriver:
    """MySQL driver must call pymysql.connect."""

    def test_connect(self):
        mock_conn = MagicMock()
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        mock_pymysql.cursors = MagicMock()

        with patch.dict("sys.modules", {"pymysql": mock_pymysql}):
            from trellum.data.drivers.mysql import MySQLDriver
            result = MySQLDriver().connect({
                "host": "localhost",
                "port": 3306,
                "database": "testdb",
                "user": "root",
                "password": "secret",
            })
            mock_pymysql.connect.assert_called_once()


class TestSnowflakeDriver:
    """Snowflake driver must call snowflake.connector.connect."""

    def test_connect(self):
        mock_conn = MagicMock()
        sf_pkg = types.ModuleType("snowflake")
        sf_connector = types.ModuleType("snowflake.connector")
        sf_connector.connect = MagicMock(return_value=mock_conn)
        sf_pkg.connector = sf_connector
        saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "snowflake" or k.startswith("snowflake.")}
        try:
            sys.modules["snowflake"] = sf_pkg
            sys.modules["snowflake.connector"] = sf_connector
            from trellum.data.drivers.snowflake_driver import SnowflakeDriver
            SnowflakeDriver().connect({
                "account": "xy12345.us-east-1",
                "user": "bi_user",
                "password": "secret",
                "warehouse": "COMPUTE_WH",
                "database": "ANALYTICS",
            })
            sf_connector.connect.assert_called_once()
            args = sf_connector.connect.call_args
            assert args[1]["account"] == "xy12345.us-east-1"
        finally:
            for k in list(sys.modules):
                if k == "snowflake" or k.startswith("snowflake."):
                    del sys.modules[k]
            sys.modules.update(saved)


class TestClickHouseDriver:
    """ClickHouse driver must call clickhouse_connect.get_client."""

    def test_connect(self):
        mock_client = MagicMock()
        mock_cc = MagicMock()
        mock_cc.get_client.return_value = mock_client

        with patch.dict("sys.modules", {"clickhouse_connect": mock_cc}):
            from trellum.data.drivers.clickhouse import ClickHouseDriver
            result = ClickHouseDriver().connect({
                "host": "ch.example.com",
                "port": 8443,
                "user": "default",
                "password": "secret",
            })
            mock_cc.get_client.assert_called_once()


class TestSQLiteDriver:
    """SQLite driver must call sqlite3.connect with path."""

    def test_connect(self):
        from trellum.data.drivers.sqlite import SQLiteDriver
        with patch("sqlite3.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            result = SQLiteDriver().connect({"path": "/tmp/test.db"})
            mock_connect.assert_called_once_with("/tmp/test.db")


class TestDuckDBDriver:
    """DuckDB driver must call duckdb.connect with path."""

    def test_connect(self):
        mock_conn = MagicMock()
        mock_duckdb = MagicMock()
        mock_duckdb.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"duckdb": mock_duckdb}):
            from trellum.data.drivers.duckdb_driver import DuckDBDriver
            result = DuckDBDriver().connect({"path": "/tmp/test.duckdb"})
            mock_duckdb.connect.assert_called_once_with("/tmp/test.duckdb")

    def test_connect_in_memory(self):
        mock_conn = MagicMock()
        mock_duckdb = MagicMock()
        mock_duckdb.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"duckdb": mock_duckdb}):
            from trellum.data.drivers.duckdb_driver import DuckDBDriver
            DuckDBDriver().connect({"path": ":memory:"})
            mock_duckdb.connect.assert_called_once_with(":memory:")


class TestSQLServerDriver:
    """SQL Server driver must call pymssql.connect with str port and default database."""

    def test_connect(self):
        mock_conn = MagicMock()
        mock_pymssql = MagicMock()
        mock_pymssql.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"pymssql": mock_pymssql}):
            from trellum.data.drivers.sqlserver import SQLServerDriver
            SQLServerDriver().connect({
                "host": "sql.example.com",
                "port": 1433,
                "database": "analytics",
                "user": "sa",
                "password": "secret",
            })
            mock_pymssql.connect.assert_called_once()
            kwargs = mock_pymssql.connect.call_args[1]
            assert kwargs["server"] == "sql.example.com"
            assert kwargs["port"] == "1433"
            assert kwargs["database"] == "analytics"

    def test_connect_default_database(self):
        mock_pymssql = MagicMock()

        with patch.dict("sys.modules", {"pymssql": mock_pymssql}):
            from trellum.data.drivers.sqlserver import SQLServerDriver
            SQLServerDriver().connect({
                "host": "sql.example.com",
                "user": "sa",
                "password": "secret",
            })
            kwargs = mock_pymssql.connect.call_args[1]
            assert kwargs["database"] == "master"
            assert kwargs["port"] == "1433"


class TestRedshiftDriver:
    """Redshift driver is a PostgreSQLDriver subclass defaulting to port 5439."""

    def test_default_port(self):
        from trellum.data.drivers.redshift import RedshiftDriver
        assert RedshiftDriver().default_port == 5439

    def test_connect_uses_psycopg2(self):
        mock_conn = MagicMock()
        mock_psycopg2 = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            from trellum.data.drivers.redshift import RedshiftDriver
            RedshiftDriver().connect({
                "host": "redshift.example.com",
                "database": "warehouse",
                "user": "analyst",
                "password": "secret",
            })
            mock_psycopg2.connect.assert_called_once()
            kwargs = mock_psycopg2.connect.call_args[1]
            assert kwargs["host"] == "redshift.example.com"
            assert kwargs["port"] == 5439


class TestTrinoDriver:
    """Trino driver: plain HTTP without a password, basic auth over HTTPS with one."""

    @staticmethod
    def _modules():
        mock_trino = MagicMock()
        return mock_trino, {
            "trino": mock_trino,
            "trino.dbapi": mock_trino.dbapi,
            "trino.auth": mock_trino.auth,
        }

    def test_connect_without_password_uses_http(self):
        mock_trino, modules = self._modules()
        with patch.dict("sys.modules", modules):
            from trellum.data.drivers.trino_driver import TrinoDriver
            TrinoDriver().connect({
                "host": "trino.example.com",
                "user": "reader",
                "catalog": "hive",
                "schema": "analytics",
            })
            mock_trino.dbapi.connect.assert_called_once()
            kwargs = mock_trino.dbapi.connect.call_args[1]
            assert kwargs["host"] == "trino.example.com"
            assert kwargs["port"] == 8080
            assert kwargs["user"] == "reader"
            assert kwargs["catalog"] == "hive"
            assert kwargs["schema"] == "analytics"
            assert kwargs["http_scheme"] == "http"
            assert kwargs["auth"] is None
            mock_trino.auth.BasicAuthentication.assert_not_called()

    def test_connect_with_password_uses_https_basic_auth(self):
        mock_trino, modules = self._modules()
        with patch.dict("sys.modules", modules):
            from trellum.data.drivers.trino_driver import TrinoDriver
            TrinoDriver().connect({
                "host": "trino.example.com",
                "port": 443,
                "user": "reader",
                "password": "secret",
            })
            mock_trino.auth.BasicAuthentication.assert_called_once_with("reader", "secret")
            kwargs = mock_trino.dbapi.connect.call_args[1]
            assert kwargs["port"] == 443
            assert kwargs["http_scheme"] == "https"
            assert kwargs["auth"] is mock_trino.auth.BasicAuthentication.return_value

    def test_port_443_defaults_to_https_and_explicit_secure_wins(self):
        mock_trino, modules = self._modules()
        with patch.dict("sys.modules", modules):
            from trellum.data.drivers.trino_driver import TrinoDriver
            driver = TrinoDriver()

            def scheme(info):
                driver.connect({"host": "trino.example.com", "user": "reader", **info})
                return mock_trino.dbapi.connect.call_args[1]["http_scheme"]

            assert scheme({"port": 443}) == "https"
            assert scheme({"port": 443, "secure": False}) == "http"
            assert scheme({"secure": True}) == "https"


class TestDatabricksDriver:
    """Databricks driver maps host/http_path/access_token onto databricks.sql.connect."""

    def test_connect(self):
        mock_databricks = MagicMock()
        modules = {"databricks": mock_databricks, "databricks.sql": mock_databricks.sql}
        with patch.dict("sys.modules", modules):
            from trellum.data.drivers.databricks_driver import DatabricksDriver
            DatabricksDriver().connect({
                "host": "workspace.example.com",
                "port": 443,  # written by `datasource add`; the connector has no port
                "http_path": "/sql/1.0/warehouses/abc123",
                "access_token": "dapi-secret",
                "catalog": "main",
                "schema": "analytics",
            })
            mock_databricks.sql.connect.assert_called_once_with(
                server_hostname="workspace.example.com",
                http_path="/sql/1.0/warehouses/abc123",
                access_token="dapi-secret",
                catalog="main",
                schema="analytics",
            )

    def test_catalog_and_schema_optional(self):
        mock_databricks = MagicMock()
        modules = {"databricks": mock_databricks, "databricks.sql": mock_databricks.sql}
        with patch.dict("sys.modules", modules):
            from trellum.data.drivers.databricks_driver import DatabricksDriver
            DatabricksDriver().connect({
                "host": "workspace.example.com",
                "http_path": "/sql/1.0/warehouses/abc123",
                "access_token": "dapi-secret",
            })
            kwargs = mock_databricks.sql.connect.call_args[1]
            assert kwargs["catalog"] is None
            assert kwargs["schema"] is None


# ── resolve_connection short-circuit (SQLite/DuckDB inline path) ────────


class TestResolveConnectionLocalDbPath:
    """SQLite/DuckDB sources with an inline path skip credential resolution."""

    def test_sqlite_inline_path(self, tmp_path):
        db_file = tmp_path / "cache.db"
        db_file.write_bytes(b"")

        from trellum.data.connections import resolve_connection

        mock_conn = MagicMock()
        with patch("trellum.data.drivers.sqlite.SQLiteDriver.connect", return_value=mock_conn) as mock_connect:
            result = resolve_connection(
                "local_db",
                [{"name": "local_db", "type": "sqlite", "path": str(db_file)}],
            )
            assert result is mock_conn
            mock_connect.assert_called_once_with({"path": str(db_file)})

    def test_sqlite_inline_path_missing_raises(self, tmp_path):
        from trellum.data.connections import resolve_connection

        missing = str(tmp_path / "nope.db")
        with pytest.raises(FileNotFoundError, match="sqlite"):
            resolve_connection(
                "local_db",
                [{"name": "local_db", "type": "sqlite", "path": missing}],
            )

    def test_duckdb_inline_path(self, tmp_path):
        db_file = tmp_path / "cache.duckdb"
        db_file.write_bytes(b"")

        from trellum.data.connections import resolve_connection

        mock_conn = MagicMock()
        with patch("trellum.data.drivers.duckdb_driver.DuckDBDriver.connect", return_value=mock_conn) as mock_connect:
            result = resolve_connection(
                "local_db",
                [{"name": "local_db", "type": "duckdb", "path": str(db_file)}],
            )
            assert result is mock_conn
            mock_connect.assert_called_once_with({"path": str(db_file)})

    def test_duckdb_in_memory_bypasses_file_check(self):
        from trellum.data.connections import resolve_connection

        mock_conn = MagicMock()
        with patch("trellum.data.drivers.duckdb_driver.DuckDBDriver.connect", return_value=mock_conn) as mock_connect:
            result = resolve_connection(
                "mem_db",
                [{"name": "mem_db", "type": "duckdb", "path": ":memory:"}],
            )
            assert result is mock_conn
            mock_connect.assert_called_once_with({"path": ":memory:"})


# ── Credential resolver precedence ─────────────────────────────────────


def test_legacy_config_only_claims_unnamed_primary_fallback(tmp_path, monkeypatch):
    from trellum.data import resolvers

    legacy_path = tmp_path / "legacy.cfg"
    legacy_path.write_text(
        "[primary_warehouse]\n"
        "hostname=legacy.example.com\n"
        "hostport=5433\n"
        "database=legacy\n"
        "username=legacy_reader\n"
        "userpass=legacy_password\n",
        encoding="utf-8",
    )
    legacy = resolvers.ConfigFileResolver(path=str(legacy_path))
    monkeypatch.setattr(
        resolvers,
        "_resolvers",
        [(10, legacy), (0, resolvers.LocalEnvResolver())],
    )
    env = {
        "BI_PRIMARY_HOST": "named.example.com",
        "BI_PRIMARY_PORT": "5432",
        "BI_PRIMARY_DB": "named",
        "BI_PRIMARY_USER": "named_reader",
        "BI_PRIMARY_PASS": "named_password",
    }

    with patch.dict(os.environ, env, clear=True):
        fallback = resolvers.resolve_credentials({"local_env": "BI_PRIMARY"})
        named = resolvers.resolve_credentials(
            {
                "name": "warehouse",
                "type": "postgres",
                "local_env": "BI_PRIMARY",
            }
        )
        unrelated = resolvers.resolve_credentials(
            {"name": "unconfigured", "type": "postgres"}
        )

    assert fallback["host"] == "legacy.example.com"
    assert named["host"] == "named.example.com"
    assert unrelated is None


# ── LocalEnvResolver ────────────────────────────────────────────────────


class TestLocalEnvResolver:
    """LocalEnvResolver must handle all source types via env var prefixes."""

    def test_standard_db_resolve(self):
        """Standard database: HOST, PORT, DB, USER, PASS."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_PG_USERS_HOST": "pg.example.com",
            "BI_PG_USERS_PORT": "5432",
            "BI_PG_USERS_DB": "users",
            "BI_PG_USERS_USER": "reader",
            "BI_PG_USERS_PASS": "secret",
        }
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_PG_USERS", "type": "postgres"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["host"] == "pg.example.com"
            assert info["port"] == 5432
            assert info["database"] == "users"
            assert info["user"] == "reader"
            assert info["password"] == "secret"

    def test_config_connection_plus_env_credentials(self):
        """The documented split: connection in config.yaml, secrets in .env.

        This is the arrangement both the config template and .env.example
        describe, and it silently did not work: can_resolve never looked at
        _USER/_PASS, so a credentials-only prefix matched no resolver, and
        resolve() discarded the source's own host/port/database anyway.
        """
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        source = {
            "local_env": "BI_SALES",
            "type": "postgres",
            "name": "sales",
            "description": "not a connection field",
            "host": "db.internal",
            "port": 5432,
            "database": "sales",
        }
        env = {"BI_SALES_USER": "reader", "BI_SALES_PASS": "secret"}
        with patch.dict(os.environ, env, clear=False):
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)

        assert info["host"] == "db.internal"
        assert info["port"] == 5432
        assert info["database"] == "sales"
        assert info["user"] == "reader"
        assert info["password"] == "secret"
        # Descriptive fields must never reach a driver's connect().
        assert "name" not in info and "description" not in info
        assert "type" not in info and "local_env" not in info

    def test_env_overrides_config_fields(self):
        """A developer points a shared source at a local server via .env."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        source = {
            "local_env": "BI_SALES", "type": "postgres",
            "host": "db.internal", "port": 5432, "database": "sales",
        }
        env = {
            "BI_SALES_HOST": "localhost",
            "BI_SALES_PORT": "55432",
            "BI_SALES_USER": "me",
            "BI_SALES_PASS": "local",
        }
        with patch.dict(os.environ, env, clear=False):
            info = resolver.resolve(source)

        assert info["host"] == "localhost"
        assert info["port"] == 55432          # coerced from the env string
        assert info["database"] == "sales"    # untouched, still from config

    def test_inline_connection_without_env_is_claimed(self):
        """A source needing no credentials is still this resolver's business.

        Declining would drop it through to the legacy fallback, which reports
        a Vertica/BI_PRIMARY problem the user does not have.
        """
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        source = {
            "local_env": "BI_TRUSTED", "type": "postgres",
            "host": "localhost", "port": 5432, "database": "dev",
        }
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("BI_TRUSTED"):
                    del os.environ[key]
            assert resolver.can_resolve(source) is True
            assert resolver.resolve(source)["host"] == "localhost"

    def test_prefix_absent_entirely_is_declined(self):
        """Nothing configured anywhere: decline, so the caller can explain."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()
        source = {"local_env": "BI_NOTHING", "type": "postgres"}
        with patch.dict(os.environ, {}, clear=False):
            for key in list(os.environ):
                if key.startswith("BI_NOTHING"):
                    del os.environ[key]
            assert resolver.can_resolve(source) is False

    def test_bigquery_resolve(self):
        """BigQuery: PROJECT and CREDENTIALS_PATH."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_BQ_ANALYTICS_PROJECT": "my-gcp-project",
            "BI_BQ_ANALYTICS_CREDENTIALS_PATH": "/path/to/sa.json",
        }
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_BQ_ANALYTICS", "type": "bigquery"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["project"] == "my-gcp-project"
            assert info["credentials_path"] == "/path/to/sa.json"

    def test_bigquery_api_endpoint_resolve(self):
        """BigQuery: API_ENDPOINT (private endpoint / emulator override)."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_BQ_ANALYTICS_PROJECT": "my-gcp-project",
            "BI_BQ_ANALYTICS_API_ENDPOINT": "http://localhost:9050",
        }
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_BQ_ANALYTICS", "type": "bigquery"}
            info = resolver.resolve(source)
            assert info["api_endpoint"] == "http://localhost:9050"

    def test_snowflake_resolve(self):
        """Snowflake: ACCOUNT, WAREHOUSE, SCHEMA."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_SF_DWH_ACCOUNT": "xy12345.us-east-1",
            "BI_SF_DWH_USER": "bi_user",
            "BI_SF_DWH_PASS": "secret",
            "BI_SF_DWH_WAREHOUSE": "COMPUTE_WH",
            "BI_SF_DWH_DB": "ANALYTICS",
            "BI_SF_DWH_SCHEMA": "PUBLIC",
        }
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_SF_DWH", "type": "snowflake"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["account"] == "xy12345.us-east-1"
            assert info["warehouse"] == "COMPUTE_WH"
            assert info["schema"] == "PUBLIC"

    def test_databricks_resolve(self):
        """Token-authenticated source: HTTP_PATH / ACCESS_TOKEN / CATALOG, no USER or PASS."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_LAKE_HOST": "workspace.example.com",
            "BI_LAKE_HTTP_PATH": "/sql/1.0/warehouses/abc123",
            "BI_LAKE_ACCESS_TOKEN": "dapi-secret",
            "BI_LAKE_CATALOG": "main",
            "BI_LAKE_SCHEMA": "analytics",
        }
        with patch.dict(os.environ, env, clear=True):
            source = {"local_env": "BI_LAKE", "type": "databricks"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["host"] == "workspace.example.com"
            assert info["http_path"] == "/sql/1.0/warehouses/abc123"
            assert info["access_token"] == "dapi-secret"
            assert info["catalog"] == "main"
            assert info["schema"] == "analytics"
            assert "user" not in info and "password" not in info

    def test_file_source_resolve(self):
        """File source: PATH only."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {"BI_SQLITE_LOCAL_PATH": "/data/cache.db"}
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_SQLITE_LOCAL", "type": "sqlite"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["path"] == "/data/cache.db"

    def test_can_resolve_false_when_no_env_vars(self):
        """Returns False when no env vars match the prefix."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        with patch.dict(os.environ, {}, clear=True):
            source = {"local_env": "BI_NONEXISTENT", "type": "postgres"}
            assert resolver.can_resolve(source) is False

    def test_can_resolve_false_when_no_local_env(self):
        """Returns False when source has no local_env key."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()
        assert resolver.can_resolve({"type": "postgres"}) is False

    def test_two_connections_same_type_different_prefix(self):
        """Two MySQL connections with different prefixes resolve independently."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_MYSQL_USERS_HOST": "users-db.com",
            "BI_MYSQL_USERS_USER": "u1",
            "BI_MYSQL_USERS_PASS": "p1",
            "BI_MYSQL_ORDERS_HOST": "orders-db.com",
            "BI_MYSQL_ORDERS_USER": "u2",
            "BI_MYSQL_ORDERS_PASS": "p2",
        }
        with patch.dict(os.environ, env, clear=False):
            info1 = resolver.resolve({"local_env": "BI_MYSQL_USERS", "type": "mysql"})
            info2 = resolver.resolve({"local_env": "BI_MYSQL_ORDERS", "type": "mysql"})
            assert info1["host"] == "users-db.com"
            assert info2["host"] == "orders-db.com"
            assert info1["user"] != info2["user"]

    def test_secure_true(self):
        """SECURE=true parses to a bool True (e.g. plain-HTTP ClickHouse override)."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {"BI_CH_LOCAL_HOST": "ch.internal", "BI_CH_LOCAL_SECURE": "true"}
        with patch.dict(os.environ, env, clear=False):
            info = resolver.resolve({"local_env": "BI_CH_LOCAL", "type": "clickhouse"})
            assert info["secure"] is True

    def test_secure_false(self):
        """SECURE=false parses to a bool False."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {"BI_CH_LOCAL_HOST": "ch.internal", "BI_CH_LOCAL_SECURE": "false"}
        with patch.dict(os.environ, env, clear=False):
            info = resolver.resolve({"local_env": "BI_CH_LOCAL", "type": "clickhouse"})
            assert info["secure"] is False

    def test_secure_absent(self):
        """No SECURE env var: key is simply absent (driver keeps its own default)."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {"BI_CH_LOCAL_HOST": "ch.internal"}
        with patch.dict(os.environ, env, clear=True):
            info = resolver.resolve({"local_env": "BI_CH_LOCAL", "type": "clickhouse"})
            assert "secure" not in info


# ── _execute_query Dispatch ─────────────────────────────────────────────


class TestExecuteQueryDispatch:
    """_execute_query must route to the correct execution path by connection type."""

    def test_bigquery_dispatch(self):
        """BigQuery connections use client.query().to_dataframe()."""
        from trellum.data.query import _execute_query

        mock_df = pd.DataFrame({"col": [1, 2, 3]})
        mock_job = MagicMock()
        mock_job.to_dataframe.return_value = mock_df

        mock_client = MagicMock()
        mock_client.__class__.__module__ = "google.cloud.bigquery.client"
        mock_client.__class__.__qualname__ = "Client"
        mock_client.query.return_value = mock_job

        result = _execute_query(mock_client, "SELECT 1")
        mock_client.query.assert_called_once_with("SELECT 1")
        mock_job.to_dataframe.assert_called_once()
        pd.testing.assert_frame_equal(result, mock_df)

    def test_clickhouse_dispatch(self):
        """ClickHouse connections use client.query_df()."""
        from trellum.data.query import _execute_query

        mock_df = pd.DataFrame({"col": [1, 2, 3]})
        mock_client = MagicMock()
        mock_client.__class__.__module__ = "clickhouse_connect.driver.client"
        mock_client.__class__.__qualname__ = "Client"
        mock_client.query_df.return_value = mock_df

        result = _execute_query(mock_client, "SELECT 1")
        mock_client.query_df.assert_called_once_with("SELECT 1")

    def test_snowflake_dispatch(self):
        """Snowflake connections use cursor.fetch_pandas_all()."""
        from trellum.data.query import _execute_query

        mock_df = pd.DataFrame({"col": [1, 2, 3]})
        mock_cursor = MagicMock()
        mock_cursor.fetch_pandas_all.return_value = mock_df

        mock_conn = MagicMock()
        mock_conn.__class__.__module__ = "snowflake.connector.connection"
        mock_conn.__class__.__qualname__ = "SnowflakeConnection"
        mock_conn.cursor.return_value = mock_cursor

        result = _execute_query(mock_conn, "SELECT 1")
        mock_cursor.execute.assert_called_once_with("SELECT 1")
        mock_cursor.fetch_pandas_all.assert_called_once()

    def test_dbapi_dispatch(self):
        """Standard DB-API 2.0 connections (Vertica, Postgres, MySQL, SQLite)."""
        from trellum.data.query import _execute_query

        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [(1, "a"), (2, "b")]

        mock_conn = MagicMock()
        mock_conn.__class__.__module__ = "vertica_python.vertica.connection"
        mock_conn.__class__.__qualname__ = "Connection"
        mock_conn.cursor.return_value = mock_cursor

        result = _execute_query(mock_conn, "SELECT id, name FROM t")
        assert list(result.columns) == ["id", "name"]
        assert len(result) == 2

    def test_dbapi_empty_result(self):
        """DB-API path returns empty DataFrame when cursor.description is None."""
        from trellum.data.query import _execute_query

        mock_cursor = MagicMock()
        mock_cursor.description = None

        mock_conn = MagicMock()
        mock_conn.__class__.__module__ = "some.dbapi.module"
        mock_conn.__class__.__qualname__ = "Connection"
        mock_conn.cursor.return_value = mock_cursor

        result = _execute_query(mock_conn, "DELETE FROM t")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0


# ── query_df Backward Compatibility ─────────────────────────────────────


class TestQueryDfBackwardCompat:
    """query_df must preserve all existing behavior after refactor."""

    def test_param_binding(self):
        """Parameters are substituted before execution."""
        from trellum.data.query import bind_params

        sql = "SELECT * FROM t WHERE d >= DATE :start AND d <= DATE :end"
        result = bind_params(sql, {"start": "2026-01-01", "end": "2026-01-31"})
        assert "'2026-01-01'" in result
        assert "'2026-01-31'" in result
        assert ":start" not in result
        assert ":end" not in result

    def test_numeric_param_not_quoted(self):
        """Numeric parameters are not wrapped in quotes."""
        from trellum.data.query import bind_params

        sql = "SELECT * FROM t WHERE id = :user_id"
        result = bind_params(sql, {"user_id": 42})
        assert "42" in result
        assert "'42'" not in result

    def test_quote_escaping_standard_dialect(self):
        """Single quotes are doubled per the SQL standard by default."""
        from trellum.data.query import bind_params

        result = bind_params("SELECT * FROM t WHERE n = :who", {"who": "O'Brien"})
        assert "'O''Brien'" in result

    def test_quote_escaping_googlesql_dialect(self):
        """GoogleSQL (BigQuery) escapes quotes and backslashes with a
        backslash -- the standard's doubled quote is a syntax error there."""
        from trellum.data.query import bind_params

        result = bind_params(
            "SELECT * FROM t WHERE n = :who", {"who": "O'Brien"}, dialect="googlesql"
        )
        assert r"'O\'Brien'" in result
        assert "''" not in result

        result = bind_params(
            "SELECT * FROM t WHERE n = :p", {"p": "a\\b"}, dialect="googlesql"
        )
        assert r"'a\\b'" in result

    def test_backslash_dialect_doubles_both_escapes(self):
        """MySQL/ClickHouse/Snowflake treat a backslash as an escape: a value
        ending in one must not be able to close the literal."""
        from trellum.data.query import bind_params

        result = bind_params(
            "SELECT * FROM t WHERE n = :who",
            {"who": "\\' OR 1=1 -- "},
            dialect="backslash",
        )
        assert result == "SELECT * FROM t WHERE n = '\\\\'' OR 1=1 -- '"
        assert (
            bind_params("SELECT :p", {"p": "O'Brien"}, dialect="backslash")
            == "SELECT 'O''Brien'"
        )

    def test_sql_dialect_for_source_type(self):
        """The by-type dialect a host binds with, per datasource type."""
        from trellum.data.query import sql_dialect_for

        for source_type in ("bigquery", "databricks"):
            assert sql_dialect_for(source_type) == "googlesql"
        for source_type in ("mysql", "clickhouse", "snowflake"):
            assert sql_dialect_for(source_type) == "backslash"
        for source_type in (
            "postgres",
            "duckdb",
            "vertica",
            "redshift",
        ):
            assert sql_dialect_for(source_type) == "standard"
        for source_type in ("sqlserver", "sqlite"):
            assert sql_dialect_for(source_type) == "bracket"

    def test_backslash_in_value_survives_binding(self):
        """A backslash in a bound value must not be eaten as a regex group
        reference by the substitution (re.sub replacement semantics)."""
        from trellum.data.query import bind_params

        result = bind_params("SELECT * FROM t WHERE p = :p", {"p": "a\\1b"})
        assert "'a\\1b'" in result

    def test_sql_dialect_detection(self):
        """Driver modules select GoogleSQL, backslash or standard escaping."""
        from trellum.data.query import _sql_dialect

        bq_conn = MagicMock()
        bq_conn.__class__.__module__ = "google.cloud.bigquery.client"
        bq_conn.__class__.__qualname__ = "Client"
        assert _sql_dialect(bq_conn) == "googlesql"

        my_conn = MagicMock()
        my_conn.__class__.__module__ = "pymysql.connections"
        my_conn.__class__.__qualname__ = "Connection"
        assert _sql_dialect(my_conn) == "backslash"

        pg_conn = MagicMock()
        pg_conn.__class__.__module__ = "psycopg2.extensions"
        pg_conn.__class__.__qualname__ = "connection"
        assert _sql_dialect(pg_conn) == "standard"

        dbx_conn = MagicMock()
        dbx_conn.__class__.__module__ = "databricks.sql.client"
        dbx_conn.__class__.__qualname__ = "Connection"
        assert _sql_dialect(dbx_conn) == "googlesql"

    def test_decimal_conversion(self):
        """Decimal columns are converted to float after query execution."""
        from trellum.data.query import query_df

        mock_cursor = MagicMock()
        mock_cursor.description = [("revenue",), ("count",)]
        mock_cursor.fetchall.return_value = [
            (Decimal("123.45"), 10),
            (Decimal("678.90"), 20),
        ]
        mock_conn = MagicMock()
        mock_conn.__class__.__module__ = "vertica_python.vertica.connection"
        mock_conn.__class__.__qualname__ = "Connection"
        mock_conn.cursor.return_value = mock_cursor

        with patch("trellum.data.query._cache_disabled", True):
            df = query_df(mock_conn, "SELECT revenue, count FROM t", cache_ttl=0)
            assert df["revenue"].dtype == float
            assert df["revenue"].iloc[0] == 123.45


# ── read_source ─────────────────────────────────────────────────────────


class TestReadSource:
    """read_source must dispatch to the correct pandas reader."""

    def test_csv(self, tmp_path):
        """CSV files are read with pd.read_csv."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("id,name\n1,alice\n2,bob\n")

        from trellum.data.query import read_source
        df = read_source("csv", str(csv_file))
        assert list(df.columns) == ["id", "name"]
        assert len(df) == 2

    def test_excel(self, tmp_path):
        """Excel files are read with pd.read_excel."""
        xlsx_file = tmp_path / "data.xlsx"
        pd.DataFrame({"x": [1, 2], "y": [3, 4]}).to_excel(str(xlsx_file), index=False)

        from trellum.data.query import read_source
        df = read_source("excel", str(xlsx_file))
        assert list(df.columns) == ["x", "y"]
        assert len(df) == 2

    def test_parquet(self, tmp_path):
        """Parquet files are read with pd.read_parquet."""
        pq_file = tmp_path / "data.parquet"
        pq_file.write_bytes(b"")

        from trellum.data.query import read_source
        expected = pd.DataFrame({"a": [10, 20], "b": [30, 40]})
        with patch("pandas.read_parquet", return_value=expected) as mock_rp:
            df = read_source("parquet", str(pq_file))
            mock_rp.assert_called_once()
        assert list(df.columns) == ["a", "b"]
        assert len(df) == 2

    def test_unsupported_type_raises(self):
        """Unsupported source types raise ValueError."""
        from trellum.data.query import read_source
        with pytest.raises(ValueError, match="Unsupported source type"):
            read_source("mongodb", "/some/path")

    def test_google_sheets_calls_gspread(self):
        """Google Sheets dispatches to _read_google_sheet."""
        from trellum.data.query import read_source

        mock_df = pd.DataFrame({"col": [1]})
        with patch("trellum.data.query._read_google_sheet", return_value=mock_df) as mock_fn:
            result = read_source(
                "google_sheets",
                "https://docs.google.com/spreadsheets/d/FAKE_ID",
                credentials_path="/fake/sa.json",
            )
            mock_fn.assert_called_once()
            pd.testing.assert_frame_equal(result, mock_df)


class TestGoogleSheetCredentials:
    """_read_google_sheet's auth chain: inline JSON, then a key file, then ADC."""

    def _read(self, credentials_path=None, **kwargs):
        sa = MagicMock()
        gspread_mod = types.ModuleType("gspread")
        gspread_mod.authorize = MagicMock()
        worksheet = (
            gspread_mod.authorize.return_value.open_by_key.return_value
            .get_worksheet.return_value
        )
        worksheet.get_all_records.return_value = [{"a": 1}]
        google_mod = types.ModuleType("google")
        auth_mod = types.ModuleType("google.auth")
        auth_mod.default = MagicMock(return_value=("adc-creds", "proj"))
        oauth2_mod = types.ModuleType("google.oauth2")
        sa_mod = types.ModuleType("google.oauth2.service_account")
        sa_mod.Credentials = sa
        google_mod.auth, google_mod.oauth2, oauth2_mod.service_account = auth_mod, oauth2_mod, sa_mod
        fake_modules = {
            "gspread": gspread_mod, "google": google_mod, "google.auth": auth_mod,
            "google.oauth2": oauth2_mod, "google.oauth2.service_account": sa_mod,
        }
        with patch.dict(sys.modules, fake_modules):
            from trellum.data.query import _read_google_sheet

            df = _read_google_sheet("SHEET_ID", 0, credentials_path, **kwargs)
        assert df["a"].tolist() == [1]
        return sa, auth_mod.default, gspread_mod.authorize.call_args[0][0]

    def test_inline_json_string_wins_over_the_file(self):
        sa, adc, authorized = self._read(
            credentials_json='{"type": "service_account", "client_email": "x@y"}',
            credentials_path="/fake/sa.json",
        )
        sa.from_service_account_info.assert_called_once()
        info = sa.from_service_account_info.call_args[0][0]
        assert info == {"type": "service_account", "client_email": "x@y"}
        sa.from_service_account_file.assert_not_called()
        adc.assert_not_called()
        assert authorized is sa.from_service_account_info.return_value

    def test_inline_json_dict_is_used_as_is(self):
        info = {"type": "service_account"}
        sa, _, _ = self._read(credentials_json=info)
        assert sa.from_service_account_info.call_args[0][0] is info

    def test_file_when_no_inline_json(self):
        sa, adc, authorized = self._read(credentials_path="/fake/sa.json")
        sa.from_service_account_file.assert_called_once()
        assert sa.from_service_account_file.call_args[0][0] == "/fake/sa.json"
        sa.from_service_account_info.assert_not_called()
        adc.assert_not_called()
        assert authorized is sa.from_service_account_file.return_value

    def test_adc_when_neither(self):
        sa, adc, authorized = self._read()
        adc.assert_called_once()
        sa.from_service_account_info.assert_not_called()
        sa.from_service_account_file.assert_not_called()
        assert authorized == "adc-creds"

    def test_malformed_inline_json_names_the_problem_not_the_key(self):
        with pytest.raises(ValueError, match="credentials_json is not a JSON object") as exc:
            self._read(credentials_json='{"type": "service_account", "private_key": "SEKRIT"')
        assert "SEKRIT" not in str(exc.value)


# ── read_api ────────────────────────────────────────────────────────────


class TestReadApi:
    """read_api must fetch JSON from HTTP endpoints and return DataFrames."""

    def _fake_requests_module(self, mock_response):
        req_mod = types.ModuleType("requests")
        req_mod.request = MagicMock(return_value=mock_response)
        return req_mod

    def test_get_flat_json(self):
        """GET request with flat JSON array response."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": 1, "value": 100},
            {"id": 2, "value": 200},
        ]
        mock_response.raise_for_status = MagicMock()

        req_mod = self._fake_requests_module(mock_response)
        with patch.dict(sys.modules, {"requests": req_mod}):
            from trellum.data.query import read_api
            df = read_api("https://api.example.com/data")
            req_mod.request.assert_called_once()
            assert req_mod.request.call_args[1]["method"] == "GET"
            assert list(df.columns) == ["id", "value"]
            assert len(df) == 2

    def test_json_path_extraction(self):
        """json_path navigates nested response to find the array."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "meta": {"page": 1},
            "data": {"results": [{"x": 1}, {"x": 2}]},
        }
        mock_response.raise_for_status = MagicMock()

        req_mod = self._fake_requests_module(mock_response)
        with patch.dict(sys.modules, {"requests": req_mod}):
            from trellum.data.query import read_api
            df = read_api("https://api.example.com/data", json_path="data.results")
            assert list(df.columns) == ["x"]
            assert len(df) == 2

    def test_post_with_body(self):
        """POST request with JSON body."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"ok": True}]
        mock_response.raise_for_status = MagicMock()

        req_mod = self._fake_requests_module(mock_response)
        with patch.dict(sys.modules, {"requests": req_mod}):
            from trellum.data.query import read_api
            read_api(
                "https://api.example.com/query",
                method="POST",
                json_body={"sql": "SELECT 1"},
                headers={"Authorization": "Bearer tok"},
            )
            call_kwargs = req_mod.request.call_args[1]
            assert call_kwargs["method"] == "POST"
            assert call_kwargs["json"] == {"sql": "SELECT 1"}
            assert call_kwargs["headers"]["Authorization"] == "Bearer tok"

    def test_timeout_passed(self):
        """Custom timeout is forwarded to requests."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"x": 1}]
        mock_response.raise_for_status = MagicMock()

        req_mod = self._fake_requests_module(mock_response)
        with patch.dict(sys.modules, {"requests": req_mod}):
            from trellum.data.query import read_api
            read_api("https://api.example.com/data", timeout=60)
            assert req_mod.request.call_args[1]["timeout"] == 60


# ── ReportContext.read_source Integration ────────────────────────────────


class TestReportContextReadSource:
    """ctx.read_source() must resolve source config from report.yaml and call read_source."""

    def test_excel_source_with_path_in_yaml(self):
        """Excel source with path directly in report.yaml."""
        from trellum.report import ReportContext

        config = {
            "data_sources": [
                {"name": "vip_list", "type": "excel", "path": "/data/vips.xlsx"},
            ],
        }
        ctx = ReportContext(config=config, slug="test", output_dir="/tmp/test")

        mock_df = pd.DataFrame({"user_id": [1, 2]})
        with patch("trellum.data.query.read_source", return_value=mock_df) as mock_fn:
            with patch("trellum.data.resolvers.resolve_credentials", return_value={}):
                result = ctx.read_source("vip_list", sheet_name="Members")
                mock_fn.assert_called_once()
                assert mock_fn.call_args[1].get("sheet_name") == "Members" or \
                       mock_fn.call_args[0][0] == "excel"

    def test_unknown_source_name_raises(self):
        """Requesting a source name not in report.yaml raises ValueError."""
        from trellum.report import ReportContext

        config = {"data_sources": []}
        ctx = ReportContext(config=config, slug="test", output_dir="/tmp/test")

        with pytest.raises(ValueError, match="not found"):
            ctx.read_source("nonexistent")

    def test_source_with_env_resolved_path(self):
        """File source where path comes from env var (via resolver)."""
        from trellum.report import ReportContext

        config = {
            "data_sources": [
                {"name": "local_db", "type": "sqlite", "local_env": "BI_SQLITE_TEST"},
            ],
        }
        ctx = ReportContext(config=config, slug="test", output_dir="/tmp/test")

        resolved = {"path": "/data/cache.db"}
        mock_df = pd.DataFrame({"x": [1]})

        with patch("trellum.data.resolvers.resolve_credentials", return_value=resolved):
            with patch("trellum.data.query.read_source", return_value=mock_df) as mock_fn:
                ctx.read_source("local_db")
                # Should have resolved path from credentials
                assert mock_fn.call_args[1].get("path") == "/data/cache.db" or \
                       mock_fn.call_args[0][1] == "/data/cache.db"

    def test_sheets_credentials_json_from_env_reaches_the_reader(self):
        """<PREFIX>_CREDENTIALS_JSON in the environment -- what .env or the
        portal provides -- lands on the sheet reader untouched."""
        from trellum.report import ReportContext

        config = {
            "data_sources": [{
                "name": "sheet", "type": "google_sheets", "local_env": "MYSRC",
                "path": "https://docs.google.com/spreadsheets/d/FAKE_ID",
            }],
        }
        ctx = ReportContext(config=config, slug="test", output_dir="/tmp/test")
        key = '{"type": "service_account", "private_key": "k"}'
        with patch.dict(os.environ, {"MYSRC_CREDENTIALS_JSON": key}):
            with patch(
                "trellum.data.query._read_google_sheet", return_value=pd.DataFrame()
            ) as reader:
                ctx.read_source("sheet")
        path, _sheet_name, credentials_path, credentials_json = reader.call_args[0]
        assert path == "https://docs.google.com/spreadsheets/d/FAKE_ID"
        assert credentials_path is None and credentials_json == key


# ── OneDrive auth ───────────────────────────────────────────────────────


@pytest.mark.skipif(
    not _HAS_MSAL,
    reason="msal is in requirements-drivers.txt; not installed in a core-only venv",
)
class TestGetOneDriveToken:
    """_get_onedrive_token must use correct auth mode and fail-fast in production.

    Unlike TestReadOneDrive (which fakes msal via sys.modules), these tests
    use mock.patch("msal.ConfidentialClientApplication", ...) — a dotted
    string target, which mock resolves by actually importing the `msal`
    module before patching the attribute on it. That import fails outright
    if msal isn't installed, so the sys.modules-faking trick doesn't apply
    here and the whole class must be skipped instead.
    """

    def test_client_credentials_returns_app_only(self):
        """With full credentials: returns (token, True) — app-only."""
        from trellum.data.query import _get_onedrive_token

        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "app-token"}

        with patch("msal.ConfidentialClientApplication", return_value=mock_app):
            token, is_app_only = _get_onedrive_token("tenant", "client", "secret")
            assert token == "app-token"
            assert is_app_only is True

    def test_client_credentials_failure_raises_immediately(self):
        """With full credentials but MSAL error: raises immediately, no fallback."""
        from trellum.data.query import _get_onedrive_token

        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "Bad secret",
        }

        with patch("msal.ConfidentialClientApplication", return_value=mock_app):
            with pytest.raises(RuntimeError, match="client credentials auth failed"):
                _get_onedrive_token("tenant", "client", "bad-secret")

    def test_no_credentials_tries_az_cli(self):
        """Without credentials: tries az CLI first."""
        from trellum.data.query import _get_onedrive_token

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "az-cli-token\n"

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            token, is_app_only = _get_onedrive_token("", "", "")
            assert token == "az-cli-token"
            assert is_app_only is False
            assert "az" in mock_run.call_args[0][0]

    def test_partial_credentials_uses_local_dev(self):
        """With only some credentials set (e.g. tenant but no secret): local dev mode."""
        from trellum.data.query import _get_onedrive_token

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "delegated-token\n"

        with patch("subprocess.run", return_value=mock_proc):
            _token, is_app_only = _get_onedrive_token("tenant", "", "")
            assert is_app_only is False


# ── OneDrive file download ──────────────────────────────────────────────


class TestReadOneDrive:
    """_read_onedrive must route to correct Graph API path based on auth mode."""

    def test_sharepoint_with_app_only_token(self):
        """App-only token + site_url: resolves site ID, downloads via /sites/."""
        from trellum.data.query import _read_onedrive

        mock_site_resp = MagicMock()
        mock_site_resp.json.return_value = {"id": "site-123"}
        mock_site_resp.raise_for_status = MagicMock()

        mock_file_resp = MagicMock()
        mock_file_resp.content = b"fake-excel"
        mock_file_resp.raise_for_status = MagicMock()

        mock_df = pd.DataFrame({"col": [1, 2]})

        with patch("trellum.data.query._get_onedrive_token", return_value=("tok", True)):
            with patch("requests.get",
                       side_effect=[mock_site_resp, mock_file_resp]) as mock_get:
                with patch("pandas.read_excel", return_value=mock_df) as mock_read:
                    result = _read_onedrive(
                        file_path="/Shared Documents/test.xlsx",
                        sheet_name=0,
                        tenant_id="t", client_id="c", client_secret="s",
                        site_url="contoso.sharepoint.com:/sites/Finance",
                    )
                    assert mock_get.call_count == 2
                    assert "sites/" in mock_get.call_args_list[0][0][0]
                    mock_read.assert_called_once()
                    pd.testing.assert_frame_equal(result, mock_df)

    def test_app_only_without_site_url_raises(self):
        """App-only token WITHOUT site_url: raises ValueError (no /me/ for app tokens)."""
        from trellum.data.query import _read_onedrive

        with patch("trellum.data.query._get_onedrive_token", return_value=("tok", True)):
            with pytest.raises(ValueError, match="requires 'site_url'"):
                _read_onedrive(
                    file_path="/test.xlsx", sheet_name=0,
                    tenant_id="t", client_id="c", client_secret="s",
                    site_url=None,
                )

    def test_delegated_token_without_site_url_uses_me(self):
        """Delegated token, no site_url: uses /me/drive/ (user's OneDrive)."""
        from trellum.data.query import _read_onedrive

        mock_resp = MagicMock()
        mock_resp.content = b"fake-excel"
        mock_resp.raise_for_status = MagicMock()
        mock_df = pd.DataFrame({"x": [1]})

        with patch("trellum.data.query._get_onedrive_token", return_value=("tok", False)):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                with patch("pandas.read_excel", return_value=mock_df):
                    _read_onedrive(
                        file_path="/Documents/my.xlsx", sheet_name=0,
                        tenant_id="", client_id="", client_secret="",
                        site_url=None,
                    )
                    assert "/me/drive/" in mock_get.call_args[0][0]

    def test_file_path_url_encoded(self):
        """File paths with spaces are URL-encoded."""
        from trellum.data.query import _read_onedrive

        mock_resp = MagicMock()
        mock_resp.content = b"fake"
        mock_resp.raise_for_status = MagicMock()

        with patch("trellum.data.query._get_onedrive_token", return_value=("tok", False)):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                with patch("pandas.read_excel", return_value=pd.DataFrame()):
                    _read_onedrive(
                        file_path="/Shared Documents/Budget 2026.xlsx",
                        sheet_name=0,
                        tenant_id="", client_id="", client_secret="",
                    )
                    url = mock_get.call_args[0][0]
                    assert "Budget%202026.xlsx" in url
                    assert "Budget 2026" not in url


# ── read_source dispatch ────────────────────────────────────────────────


class TestReadSourceOneDrive:
    """read_source with type='onedrive' must dispatch to _read_onedrive."""

    def test_dispatch_passes_credentials(self):
        from trellum.data.query import read_source

        mock_df = pd.DataFrame({"x": [1]})
        with patch("trellum.data.query._read_onedrive", return_value=mock_df) as mock_fn:
            result = read_source(
                "onedrive",
                "/Shared Documents/test.xlsx",
                credentials={
                    "tenant_id": "t",
                    "client_id": "c",
                    "client_secret": "s",
                    "site_url": "contoso.sharepoint.com:/sites/Finance",
                },
            )
            mock_fn.assert_called_once()
            kwargs = mock_fn.call_args[1]
            assert kwargs["tenant_id"] == "t"
            assert kwargs["site_url"] == "contoso.sharepoint.com:/sites/Finance"
            pd.testing.assert_frame_equal(result, mock_df)

    def test_dispatch_empty_credentials(self):
        """Empty credentials dict → local dev auth (all fields empty strings)."""
        from trellum.data.query import read_source

        with patch("trellum.data.query._read_onedrive", return_value=pd.DataFrame()) as mock_fn:
            read_source("onedrive", "/test.xlsx", credentials={})
            kwargs = mock_fn.call_args[1]
            assert kwargs["tenant_id"] == ""
            assert kwargs["client_id"] == ""


# ── LocalEnvResolver for OneDrive ───────────────────────────────────────


class TestLocalEnvResolverOneDrive:
    """LocalEnvResolver must handle OneDrive Azure AD env vars."""

    def test_full_credentials(self):
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {
            "BI_OD_FINANCE_TENANT_ID": "tenant-abc",
            "BI_OD_FINANCE_CLIENT_ID": "client-xyz",
            "BI_OD_FINANCE_CLIENT_SECRET": "secret-123",
            "BI_OD_FINANCE_SITE_URL": "contoso.sharepoint.com:/sites/Finance",
        }
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_OD_FINANCE", "type": "onedrive"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["tenant_id"] == "tenant-abc"
            assert info["client_id"] == "client-xyz"
            assert info["site_url"] == "contoso.sharepoint.com:/sites/Finance"

    def test_site_url_only_resolves(self):
        """Developer sets only SITE_URL (no secrets) — can_resolve still True."""
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()

        env = {"BI_OD_TEAM_SITE_URL": "contoso.sharepoint.com:/sites/Team"}
        with patch.dict(os.environ, env, clear=False):
            source = {"local_env": "BI_OD_TEAM", "type": "onedrive"}
            assert resolver.can_resolve(source) is True
            info = resolver.resolve(source)
            assert info["site_url"] == "contoso.sharepoint.com:/sites/Team"
            assert "client_secret" not in info

    def test_no_env_vars_returns_false(self):
        from trellum.data.resolvers import LocalEnvResolver
        resolver = LocalEnvResolver()
        with patch.dict(os.environ, {}, clear=True):
            source = {"local_env": "BI_OD_NOTHING", "type": "onedrive"}
            assert resolver.can_resolve(source) is False


# ── Snowflake SSO fallback (Part 10) ────────────────────────────────────


class TestSnowflakeSSO:
    """SnowflakeDriver must use externalbrowser when no password."""

    def _fake_snowflake_modules(self):
        mock_conn = MagicMock()
        sf_pkg = types.ModuleType("snowflake")
        sf_connector = types.ModuleType("snowflake.connector")
        sf_connector.connect = MagicMock(return_value=mock_conn)
        sf_pkg.connector = sf_connector
        saved = {
            k: sys.modules.pop(k)
            for k in list(sys.modules)
            if k == "snowflake" or k.startswith("snowflake.")
        }
        sys.modules["snowflake"] = sf_pkg
        sys.modules["snowflake.connector"] = sf_connector
        return sf_connector, saved

    def _restore_snowflake_modules(self, saved):
        for k in list(sys.modules):
            if k == "snowflake" or k.startswith("snowflake."):
                del sys.modules[k]
        sys.modules.update(saved)

    def test_password_provided(self):
        """With password: passes password, no authenticator."""
        sf_connector, saved = self._fake_snowflake_modules()
        try:
            from trellum.data.drivers.snowflake_driver import SnowflakeDriver

            SnowflakeDriver().connect({
                "account": "xy12345",
                "user": "u",
                "password": "secret",
            })
            kwargs = sf_connector.connect.call_args[1]
            assert kwargs["password"] == "secret"
            assert "authenticator" not in kwargs
        finally:
            self._restore_snowflake_modules(saved)

    def test_no_password_uses_externalbrowser(self):
        """Without password: sets authenticator=externalbrowser for SSO."""
        sf_connector, saved = self._fake_snowflake_modules()
        try:
            from trellum.data.drivers.snowflake_driver import SnowflakeDriver

            SnowflakeDriver().connect({
                "account": "xy12345",
                "user": "u",
            })
            kwargs = sf_connector.connect.call_args[1]
            assert kwargs["authenticator"] == "externalbrowser"
            assert "password" not in kwargs
        finally:
            self._restore_snowflake_modules(saved)
