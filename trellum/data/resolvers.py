"""Pluggable credential resolvers.

The framework ships with two built-in resolvers (highest priority first):

- ``ConfigFileResolver`` -- reads the unnamed legacy ``BI_PRIMARY`` fallback
  from an INI config file (legacy path for on-prem Vertica installs).
- ``LocalEnvResolver`` -- reads credentials from environment variables
  using a prefix (e.g. ``BI_<PREFIX>_HOST``, ``..._USER``, ``..._PASS``)
  where ``<PREFIX>`` comes from a data source's ``credentials.local``.

Projects can register additional resolvers for other credential sources:

    from trellum.data.resolvers import register_resolver

    class MyVaultResolver:
        def can_resolve(self, source):
            return bool(source.get("vault_path"))

        def resolve(self, source):
            ...

    register_resolver(MyVaultResolver(), priority=30)
"""

from __future__ import annotations

import os
import threading
from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialResolver(Protocol):
    """Interface for credential resolution backends."""

    def can_resolve(self, source: dict) -> bool:
        """Return True if this resolver can handle this data_source config."""
        ...

    def resolve(self, source: dict) -> dict:
        """Return a conn_info dict with host, port, database, user, password."""
        ...


# ---------------------------------------------------------------------------
# .env loading (shared by resolvers that read env vars)
# ---------------------------------------------------------------------------

_dotenv_loaded = False
_dotenv_lock = threading.Lock()


def _load_dotenv_once() -> None:
    """Ensure .env is loaded (idempotent, thread-safe)."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    with _dotenv_lock:
        if _dotenv_loaded:
            return

        from trellum.project import get_project_root

        env_path = os.path.join(get_project_root(), ".env")

        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
            _dotenv_loaded = True
            return
        except ImportError:
            pass

        if not os.path.isfile(env_path):
            _dotenv_loaded = True
            return
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if not os.environ.get(key):
                    os.environ[key] = value
        _dotenv_loaded = True


# ---------------------------------------------------------------------------
# Built-in resolvers
# ---------------------------------------------------------------------------


#: Env suffix -> conn_info key. One table, because the set of suffixes
#: ``can_resolve`` recognised had drifted from the set ``resolve`` actually
#: read: ``_USER`` and ``_PASS`` were read but never recognised, so the
#: documented arrangement -- connection details in config.yaml, credentials in
#: .env -- never matched this resolver at all.
_ENV_SUFFIXES: dict[str, str] = {
    # Standard database fields
    "HOST": "host", "PORT": "port", "DB": "database", "DATABASE": "database",
    "USER": "user", "PASS": "password",
    # BigQuery
    "PROJECT": "project", "CREDENTIALS_PATH": "credentials_path",
    "CREDENTIALS_JSON": "credentials_json", "API_ENDPOINT": "api_endpoint",
    # Snowflake
    "ACCOUNT": "account", "WAREHOUSE": "warehouse", "SCHEMA": "schema",
    # Trino / Databricks (a token, not USER/PASS, is what authenticates Databricks)
    "CATALOG": "catalog", "HTTP_PATH": "http_path", "ACCESS_TOKEN": "access_token",
    # OneDrive / Azure AD
    "TENANT_ID": "tenant_id", "CLIENT_ID": "client_id",
    "CLIENT_SECRET": "client_secret", "SITE_URL": "site_url",
    # File sources, and the plain-HTTP transport flag
    "PATH": "path", "SECURE": "secure",
    "NEW_CONNECTION_PER_QUERY": "new_connection_per_query",
    # SSH tunnel to any host/port source (trellum.data.ssh_tunnel)
    "SSH_HOST": "ssh_host", "SSH_PORT": "ssh_port", "SSH_USER": "ssh_user",
    "SSH_KEY_PATH": "ssh_key_path", "SSH_PRIVATE_KEY": "ssh_private_key",
    "SSH_PASSWORD": "ssh_password", "SSH_HOST_KEY": "ssh_host_key",
}


#: Keys a source entry may contribute to a connection. An allowlist rather
#: than "everything except a few", so descriptive fields (name, type,
#: description, upload, credentials) can never leak into a driver's conn_info.
_SOURCE_CONN_KEYS: frozenset[str] = frozenset(_ENV_SUFFIXES.values())


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def parse_new_connection_per_query(value: object) -> bool:
    """Parse the lifecycle flag without echoing invalid configuration values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        normalized = str(value).strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    raise ValueError("new_connection_per_query must be a boolean (true/false, 1/0, yes/no, on/off)")


class LocalEnvResolver:
    """Resolves a connection from the source entry plus environment variables.

    Non-secret connection details (host, port, database) may live in the
    source's own entry in ``data-sources/config.yaml``; credentials live in the
    environment under a prefix, as ``PREFIX_USER`` / ``PREFIX_PASS``. That is
    the split both the config template and ``.env.example`` describe.

    Environment values win where both define a field, so a developer can point
    a shared source at a local server without editing the committed file.
    Supplying everything through the environment still works, and remains the
    only option for a source that declares nothing but a prefix.
    """

    def can_resolve(self, source: dict) -> bool:
        if not source.get("local_env"):
            return False
        _load_dotenv_once()
        prefix = source["local_env"]
        if any(os.getenv(f"{prefix}_{s}") for s in _ENV_SUFFIXES):
            return True
        # The source may carry its whole connection inline and need nothing
        # from the environment. Claiming it here means any later failure comes
        # from the driver, naming the real problem, rather than this resolver
        # declining and the chain falling through to an unrelated fallback.
        return any(key in source for key in _SOURCE_CONN_KEYS)

    def resolve(self, source: dict) -> dict:
        _load_dotenv_once()
        prefix = source["local_env"]

        # Start from what the source itself declares. Discarding these is what
        # forced every connection field to be duplicated into .env.
        result = {
            key: source[key]
            for key in _SOURCE_CONN_KEYS
            if source.get(key) is not None
        }

        # The environment overlays it: credentials, and any local override.
        for suffix, key in _ENV_SUFFIXES.items():
            val = os.getenv(f"{prefix}_{suffix}")
            if val is not None:
                result[key] = val

        if "secure" in result:
            result["secure"] = _truthy(result["secure"])
        if "new_connection_per_query" in result:
            result["new_connection_per_query"] = parse_new_connection_per_query(result["new_connection_per_query"])
        for key in ("port", "ssh_port"):
            if key in result:
                result[key] = int(result[key])


        return result


class ConfigFileResolver:
    """Resolves credentials from an INI config file (legacy path).

    Defaults match an on-prem layout that a few legacy deployments still
    use. It only claims the unnamed ``BI_PRIMARY`` descriptor passed by the
    legacy fallback; named sources must resolve from their own configuration.
    """

    def __init__(
        self,
        path: str = "/opt/venv/bi_casual_venv/casual_db_properties.cfg",
        section: str = "primary_warehouse",
    ):
        self.path = path
        self.section = section

    def can_resolve(self, source: dict) -> bool:
        return (
            not source.get("name")
            and source.get("local_env") == "BI_PRIMARY"
            and os.path.exists(self.path)
        )

    def resolve(self, source: dict) -> dict:
        import configparser

        config = configparser.ConfigParser()
        config.read(self.path)
        s = config[self.section]
        return {
            "host": s.get("hostname"),
            "port": s.getint("hostport"),
            "database": s.get("database"),
            "user": s.get("username"),
            "password": s.get("userpass"),
        }


# ---------------------------------------------------------------------------
# Resolver registry
# ---------------------------------------------------------------------------

_resolvers: list[tuple[int, CredentialResolver]] = []


def register_resolver(resolver: CredentialResolver, priority: int = 0) -> None:
    """Register a credential resolver.  Higher priority = tried first."""
    _resolvers.append((priority, resolver))
    _resolvers.sort(key=lambda x: -x[0])


def resolve_credentials(source: dict) -> dict | None:
    """Try each registered resolver in priority order.

    Returns a ``conn_info`` dict on success, or ``None`` if no resolver
    can handle this source.
    """
    for _, resolver in _resolvers:
        if resolver.can_resolve(source):
            result = dict(resolver.resolve(source))
            option = "new_connection_per_query"
            if option not in result and option in source:
                result[option] = source[option]
            if option in result:
                result[option] = parse_new_connection_per_query(result[option])
            return result
    return None


# Register built-in resolvers (highest priority wins)
register_resolver(ConfigFileResolver(), priority=10)
register_resolver(LocalEnvResolver(), priority=0)
