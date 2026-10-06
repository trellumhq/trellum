"""Google BigQuery driver."""

from __future__ import annotations

from typing import Any

from trellum.data.drivers import register_driver


class BigQueryDriver:
    @property
    def default_port(self) -> int:
        return 0  # BigQuery has no port

    def connect(self, conn_info: dict) -> Any:
        from google.cloud import bigquery
        from google.oauth2 import service_account

        kwargs = {}
        if conn_info.get("project"):
            kwargs["project"] = conn_info["project"]
        # Same order as the Google Sheets reader: inline JSON, then a key
        # file, then ADC. The portal merges a declared credentials_path with
        # the pasted secret, and the secret is the one the admin just chose.
        if conn_info.get("credentials_json"):
            import json

            raw = conn_info["credentials_json"]
            try:
                info = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError as err:
                raise ValueError(f"credentials_json is not a JSON object: {err}") from None
            creds = service_account.Credentials.from_service_account_info(info)
            kwargs["credentials"] = creds
        elif conn_info.get("credentials_path"):
            creds = service_account.Credentials.from_service_account_file(
                conn_info["credentials_path"]
            )
            kwargs["credentials"] = creds
        elif conn_info.get("api_endpoint"):
            # Private endpoints / emulators rarely accept ADC; fall back to
            # an anonymous credential so the client doesn't try to hit the
            # real metadata server.
            from google.auth.credentials import AnonymousCredentials
            kwargs["credentials"] = AnonymousCredentials()
        # If none of the above is set, uses Application Default Credentials (ADC)

        if conn_info.get("api_endpoint"):
            kwargs["client_options"] = {"api_endpoint": conn_info["api_endpoint"]}

        return bigquery.Client(**kwargs)


register_driver("bigquery", BigQueryDriver())
