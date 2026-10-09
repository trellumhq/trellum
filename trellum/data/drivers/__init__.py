"""Pluggable database connection drivers.

Each driver lives in its own module (e.g. ``drivers.postgres``,
``drivers.bigquery``).  Drivers are auto-registered at import time
via ``register_driver()`` calls at module level.

Usage in reports is unchanged::

    conn = ctx.get_connection("primary_warehouse")   # resolves type → driver
    df = query_df(conn, sql, params={...})

Host projects can register additional drivers::

    from trellum.data.drivers import register_driver

    class MyDriver:
        @property
        def default_port(self) -> int:
            return 9999
        def connect(self, conn_info: dict):
            ...

    register_driver("mydb", MyDriver())
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConnectionDriver(Protocol):
    """Interface that all database drivers must implement."""

    def connect(self, conn_info: dict) -> Any:
        """Create and return a database connection from resolved credentials."""
        ...

    @property
    def default_port(self) -> int:
        """Default port for this database type."""
        ...


_registry: dict[str, ConnectionDriver] = {}


def register_driver(type_name: str, driver: ConnectionDriver) -> None:
    """Register a connection driver for a database type."""
    _registry[type_name] = driver


def get_driver(type_name: str) -> ConnectionDriver:
    """Look up a registered driver by type name."""
    if type_name not in _registry:
        available = ", ".join(sorted(_registry)) or "(none)"
        raise ValueError(
            f"No driver registered for type '{type_name}'. "
            f"Available: {available}. "
            f"Call register_driver('{type_name}', YourDriver()) to add one."
        )
    return _registry[type_name]


def connect(type_name: str, conn_info: dict) -> Any:
    """``get_driver(type_name).connect(conn_info)``, through an SSH tunnel
    when ``conn_info`` names one.

    The one path every caller takes -- report builds, ``trellum query``, the
    portal's connection test and live queries -- so a source declaring
    ``ssh_host`` (see ``trellum.data.ssh_tunnel``) tunnels everywhere, and the
    tunnel lives exactly as long as the connection: closing what this returns
    closes both. The driver never sees ``ssh_*`` keys; it gets ``127.0.0.1``
    and the tunnel's local port as ``host``/``port``.
    """
    if "new_connection_per_query" in conn_info:
        conn_info = dict(conn_info)
        conn_info.pop("new_connection_per_query")
    driver = get_driver(type_name)
    if not conn_info.get("ssh_host"):
        return driver.connect(conn_info)

    from trellum.data.ssh_tunnel import SSHTunnel, TunnelledConnection

    if not conn_info.get("host"):
        raise ValueError("ssh_host needs host (the database as the bastion sees it)")
    remote = (conn_info["host"], int(conn_info.get("port") or driver.default_port))
    tunnel = SSHTunnel(conn_info, remote)
    info = {k: v for k, v in conn_info.items() if not k.startswith("ssh_")}
    info["host"], info["port"] = "127.0.0.1", tunnel.local_port
    try:
        inner = driver.connect(info)
    except BaseException:
        tunnel.close()
        raise
    return TunnelledConnection(inner, tunnel)


# Import all built-in driver modules to trigger their register_driver() calls.

# Each module registers itself at import time.
from trellum.data.drivers import (  # noqa: E402, F401
    bigquery,
    clickhouse,
    databricks_driver,
    duckdb_driver,
    mysql,
    postgres,
    redshift,
    snowflake_driver,
    sqlite,
    sqlserver,
    trino_driver,
    vertica,
)
