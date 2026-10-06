"""Centralized data source configuration loader.

Reads ``data-sources/config.yaml`` and provides lookup helpers so that
reports can reference sources by name instead of duplicating connection
details in every ``report.yaml``.

Config format supports non-secret connection info (host, port, database)
inline and references credentials separately:

    credentials:
      local: BI_PRIMARY                # env var prefix (reads _USER, _PASS)
      production: bi-reports/primary   # opaque id for a registered resolver

Sources can be added, edited and removed programmatically; see
``save_datasource_config``.
"""

from __future__ import annotations

import os

import yaml

from trellum.project import get_project_root

_cache: dict[str, dict] | None = None


def _config_path() -> str:
    return os.path.join(get_project_root(), "data-sources", "config.yaml")


def load_datasource_config() -> dict[str, dict]:
    """Read ``data-sources/config.yaml`` and return ``{name: source_dict}``.

    Returns an empty dict if the file does not exist (backward compatible).
    The result is cached after the first successful load.
    """
    global _cache
    if _cache is not None:
        return _cache

    path = _config_path()
    if not os.path.isfile(path):
        _cache = {}
        return _cache

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    sources: dict[str, dict] = {}
    for name, entry in (raw.get("sources") or {}).items():
        entry = dict(entry)
        entry.setdefault("name", name)
        sources[name] = entry

    _cache = sources
    return _cache


def invalidate_datasource_cache() -> None:
    """Force a re-read of the config on the next call."""
    global _cache
    _cache = None


def get_datasource(name: str) -> dict | None:
    """Look up a single source by name. Returns None if not found."""
    return load_datasource_config().get(name)


def resolve_to_legacy_format(name: str) -> dict | None:
    """Convert a central config entry to the dict format that resolve_connection() expects.

    The legacy format: {name, type, local_env, secret, host, port, database, ...}
    The new format has: {name, type, credentials: {local, production}, host, port, ...}

    This bridges the two so the existing resolver chain works unchanged.
    """
    src = get_datasource(name)
    if src is None:
        return None

    result = dict(src)
    creds = result.pop("credentials", None)
    if creds:
        # Map credentials.local → local_env (for LocalEnvResolver)
        if isinstance(creds, dict):
            if creds.get("local"):
                result["local_env"] = creds["local"]
            if creds.get("production"):
                result["secret"] = creds["production"]
        elif isinstance(creds, str):
            # Simple string — treat as local_env prefix
            result["local_env"] = creds

    return result


def list_datasources() -> list[dict]:
    """Return all configured sources as a list of dicts."""
    return list(load_datasource_config().values())


def get_upload_path(name: str) -> str | None:
    """Return the file path for an uploadable source, or None."""
    src = get_datasource(name)
    if src is None or not src.get("upload"):
        return None
    rel_path = src.get("path")
    if not rel_path:
        return None
    return os.path.join(get_project_root(), rel_path)


def save_datasource_config(sources: dict[str, dict]) -> None:
    """Write the sources dict back to config.yaml."""
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Strip 'name' key from entries (it's the dict key, not a field)
    clean = {}
    for name, entry in sources.items():
        entry = dict(entry)
        entry.pop("name", None)
        clean[name] = entry

    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(
            {"sources": clean},
            fh,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    invalidate_datasource_cache()
