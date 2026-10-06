"""SQL Server database driver.

Uses ``pymssql`` rather than ``pyodbc`` -- pymssql bundles FreeTDS so it
has no system ODBC driver dependency (pyodbc needs msodbcsql18 installed
on every machine that runs reports).
"""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class SQLServerDriver:

    @property
    def default_port(self) -> int:
        return 1433

    def connect(self, conn_info: dict) -> Any:
        import pymssql
        return pymssql.connect(
            server=conn_info["host"],
            port=str(conn_info.get("port", self.default_port)),
            database=conn_info.get("database", "master"),
            user=conn_info["user"],
            password=conn_info["password"],
        )


register_driver("sqlserver", SQLServerDriver())
