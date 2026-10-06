"""SQLite database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class SQLiteDriver:

    @property
    def default_port(self) -> int:
        return 0  # SQLite has no port

    def connect(self, conn_info: dict) -> Any:
        import sqlite3
        return sqlite3.connect(conn_info["path"])


register_driver("sqlite", SQLiteDriver())
