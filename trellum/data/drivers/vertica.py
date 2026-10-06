"""Vertica database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class VerticaDriver:
    """Built-in Vertica driver (requires ``vertica-python`` at runtime)."""

    @property
    def default_port(self) -> int:
        return 5433

    def connect(self, conn_info: dict) -> Any:
        import logging

        import vertica_python

        host = conn_info["host"]
        port = conn_info.get("port", self.default_port)
        database = conn_info.get("database", "vertica")
        user = conn_info["user"]

        logger = logging.getLogger(__name__)
        logger.info("Connecting to Vertica: host=%s port=%s database=%s user=%s", host, port, database, user)

        try:
            return vertica_python.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=conn_info["password"],
                autocommit=True,
                tlsmode="disable",
            )
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to Vertica at {host}:{port} "
                f"(database={database}, user={user}): {exc}"
            ) from exc


register_driver("vertica", VerticaDriver())
