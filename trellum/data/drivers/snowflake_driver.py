"""Snowflake database driver."""

from __future__ import annotations

import sys
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

        # Connector 5 dropped OCSP and made CRL checking opt-in; ADVISORY is
        # its best-effort mode (rejects a certificate a CRL lists as revoked,
        # tolerates an unreachable CRL), the nearest match to the OCSP
        # fail-open behaviour connector 4 had by default.
        kwargs["crl_check_mode"] = "ADVISORY"
        # Connector 5 caches ID/MFA tokens in the OS credential store on every
        # platform; connector 4 did so on macOS and Windows only.
        if sys.platform.startswith("linux"):
            kwargs["client_store_temporary_credential"] = False

        return snowflake.connector.connect(**kwargs)


register_driver("snowflake", SnowflakeDriver())
