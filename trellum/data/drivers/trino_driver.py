"""Trino database driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class TrinoDriver:

    @property
    def default_port(self) -> int:
        return 8080

    def connect(self, conn_info: dict) -> Any:
        import trino.dbapi

        password = conn_info.get("password")
        port = conn_info.get("port", self.default_port)
        # A password means HTTP basic auth, which Trino only accepts over
        # HTTPS, and 443 is the HTTPS port; otherwise the default port is
        # Trino's plain-HTTP one. An explicit `secure` (the same flag
        # ClickHouse reads) wins either way.
        secure = conn_info.get("secure", bool(password) or port == 443)
        auth = None
        if password:
            from trino.auth import BasicAuthentication
            auth = BasicAuthentication(conn_info["user"], password)
        return trino.dbapi.connect(
            host=conn_info["host"],
            port=port,
            user=conn_info["user"],
            catalog=conn_info.get("catalog"),
            schema=conn_info.get("schema"),
            http_scheme="https" if secure else "http",
            auth=auth,
        )


register_driver("trino", TrinoDriver())
