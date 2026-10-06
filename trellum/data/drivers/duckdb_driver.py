"""DuckDB database driver.

Named ``duckdb_driver`` (not ``duckdb.py``) to avoid shadowing the
``duckdb`` pip package on import.
"""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class DuckDBDriver:

    @property
    def default_port(self) -> int:
        return 0  # DuckDB is embedded, no port

    def connect(self, conn_info: dict) -> Any:
        import duckdb
        return duckdb.connect(conn_info["path"])


register_driver("duckdb", DuckDBDriver())
