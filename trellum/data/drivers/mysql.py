"""MySQL database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class MySQLDriver:

    @property
    def default_port(self) -> int:
        return 3306

    def connect(self, conn_info: dict) -> Any:
        import pymysql
        return pymysql.connect(
            host=conn_info["host"],
            port=conn_info.get("port", self.default_port),
            database=conn_info.get("database"),
            user=conn_info["user"],
            password=conn_info["password"],
            cursorclass=pymysql.cursors.DictCursor,
        )


register_driver("mysql", MySQLDriver())
