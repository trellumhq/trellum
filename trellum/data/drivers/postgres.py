"""PostgreSQL database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class PostgreSQLDriver:

    @property
    def default_port(self) -> int:
        return 5432

    def connect(self, conn_info: dict) -> Any:
        import psycopg2
        return psycopg2.connect(
            host=conn_info["host"],
            port=conn_info.get("port", self.default_port),
            dbname=conn_info.get("database", "postgres"),
            user=conn_info["user"],
            password=conn_info["password"],
        )


register_driver("postgres", PostgreSQLDriver())
