"""Snowflake database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class SnowflakeDriver:

    @property
    def default_port(self) -> int:
        return 443

    def connect(self, conn_info: dict) -> Any:
        import snowflake.connector

        kwargs = {
            "account": conn_info["account"],
            "user": conn_info["user"],
            "warehouse": conn_info.get("warehouse"),
            "database": conn_info.get("database"),
            "schema": conn_info.get("schema", "PUBLIC"),
        }

        if conn_info.get("password"):
            kwargs["password"] = conn_info["password"]
        else:
            kwargs["authenticator"] = "externalbrowser"

        return snowflake.connector.connect(**kwargs)


register_driver("snowflake", SnowflakeDriver())
