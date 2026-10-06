"""Databricks SQL warehouse driver.

Authenticates with a personal access token over HTTPS -- there is no port
to configure. ``http_path`` is the warehouse's HTTP path from its
connection details.
"""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class DatabricksDriver:

    @property
    def default_port(self) -> int:
        return 443

    def connect(self, conn_info: dict) -> Any:
        import databricks.sql
        return databricks.sql.connect(
            server_hostname=conn_info["host"],
            http_path=conn_info["http_path"],
            access_token=conn_info["access_token"],
            catalog=conn_info.get("catalog"),
            schema=conn_info.get("schema"),
        )


register_driver("databricks", DatabricksDriver())
