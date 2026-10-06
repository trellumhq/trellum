"""ClickHouse database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class ClickHouseDriver:

    @property
    def default_port(self) -> int:
        return 8443

    def connect(self, conn_info: dict) -> Any:
        import clickhouse_connect
        return clickhouse_connect.get_client(
            host=conn_info["host"],
            port=conn_info.get("port", self.default_port),
            username=conn_info.get("user", "default"),
            password=conn_info.get("password", ""),
            secure=conn_info.get("secure", True),
        )


register_driver("clickhouse", ClickHouseDriver())
