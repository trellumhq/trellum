"""Multi-environment database connection resolver.

Uses the pluggable driver and credential resolver registries so the
framework is not tied to any specific database or cloud provider.

Credential resolution order is determined by resolver priority
(see ``trellum.data.resolvers``).  The built-in resolvers cover
local ``.env`` files and legacy INI config files.
"""

from __future__ import annotations

import os
from typing import Any

from trellum.data.drivers import connect
from trellum.data.resolvers import _load_dotenv_once, resolve_credentials

# Which data source each live connection came from.  The query cache keys on
# it, so a result can say WHERE it came from and not just what was asked --
# two sources holding the same table under the same SQL would otherwise share
# one cache entry.  Keyed by id() and holding the connection itself: the
# strong reference is what keeps the id unique, since a freed object's address
# can be handed to the next one.  ``forget_source`` is therefore not optional
# -- ``ReportContext.close_connections`` and the ad-hoc path both call it,
# which is what stops a long ``--all`` run holding every connection open until
# the process exits.
_source_names: dict[int, tuple[str, Any]] = {}


def register_source(conn: Any, name: str) -> None:
    """Record that *conn* talks to the data source called *name*."""
    _source_names[id(conn)] = (name, conn)


def forget_source(conn: Any) -> None:
    """Drop *conn*'s registration; call this when the connection closes."""
    _source_names.pop(id(conn), None)


def source_of(conn: Any) -> str:
    """The data source *conn* was resolved from, or "" if nobody said."""
    entry = _source_names.get(id(conn))
    return entry[0] if entry else ""


def _normalize_sources(data_sources: list) -> list[dict]:
    """Convert a mixed list of dicts and string names to all-dict format.

    String entries are looked up in the central ``data-sources/config.yaml``
    and converted to the legacy dict format (with local_env/secret fields).
    Dict entries pass through unchanged.
    """
    from trellum.data.datasource_config import resolve_to_legacy_format

    result: list[dict] = []
    for entry in data_sources:
        if isinstance(entry, str):
            resolved = resolve_to_legacy_format(entry)
            if resolved is not None:
                result.append(resolved)
            else:
                result.append({"name": entry})
        else:
            result.append(entry)
    return result


def _find_source(name: str, data_sources: list) -> dict:
    normalized = _normalize_sources(data_sources)
    for src in normalized:
        if src.get("name") == name:
            return src
    raise KeyError(
        f"No data_source named '{name}' in report.yaml or data-sources/config.yaml. "
        f"Available: {[s.get('name') for s in normalized]}"
    )


def _resolve_local_db_path(name: str, db_type: str, path: str) -> str:
    """Resolve a SQLite/DuckDB ``path`` against the project root.

    Relative paths are resolved against ``get_project_root()`` rather than the
    working directory: a caller may copy reports into a temp dir before running
    them, so cwd is not stable. DuckDB's in-memory database (``:memory:``)
    bypasses the file-exists check -- there's no file to find.
    """
    from trellum.project import get_project_root

    if path == ":memory:":
        return path
    if not os.path.isabs(path):
        path = os.path.join(get_project_root(), path)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"{db_type} data source '{name}' not found at {path}. "
            f"If this is the demo project, build it with: "
            f"python tools/make_fixtures.py"
        )
    return path


def resolve_connection(name: str, data_sources: list[dict]) -> Any:
    """Resolve a named database connection.

    If no ``data_sources`` are configured, falls back to the legacy
    default-credential path (env vars with ``BI_PRIMARY`` prefix or a
    config file).
    """
    if not data_sources:
        return _resolve_legacy()

    source = _find_source(name, data_sources)
    db_type = source.get("type", "vertica")

    # SQLite/DuckDB sources may declare their path inline, mirroring how file
    # sources are handled in ReportContext.read_source. There are no
    # credentials to resolve, so short-circuit the resolver chain rather
    # than falling through to _resolve_legacy().
    if db_type in ("sqlite", "duckdb") and source.get("path"):
        path = _resolve_local_db_path(name, db_type, source["path"])
        return connect(db_type, {"path": path})

    conn_info = resolve_credentials(source)
    if conn_info is None:
        raise ValueError(_unresolved_message(name, db_type, source))

    return connect(db_type, conn_info)



def _unresolved_message(name: str, db_type: str, source: dict) -> str:
    """Explain why *this* source could not be resolved.

    A named source that no resolver claimed used to fall through to
    ``_resolve_legacy()``, which tries the ``BI_PRIMARY`` prefix and the
    Vertica driver. The resulting error named neither the source the user had
    configured nor its actual type, and sent readers looking for a Vertica
    problem they did not have.
    """
    prefix = source.get("local_env")
    if not prefix:
        return (
            f"Data source '{name}' ({db_type}) has no credentials configured. "
            f"Give its entry in data-sources/config.yaml a `credentials.local` "
            f"prefix, then set that prefix's _USER and _PASS in .env."
        )
    return (
        f"Data source '{name}' ({db_type}) could not be resolved: nothing was "
        f"found for the credentials prefix '{prefix}'. Set {prefix}_USER and "
        f"{prefix}_PASS in .env at the project root. Host, port and database "
        f"may stay in data-sources/config.yaml -- they are read from there."
    )


def _resolve_legacy() -> Any:
    """Fallback for reports without ``data_sources``.

    Attempts the resolver chain with the default ``BI_PRIMARY`` prefix,
    then connects via the Vertica driver. Projects are expected to
    define ``data_sources`` in ``report.yaml`` or ``data-sources/config.yaml``
    -- this fallback only exists for the simplest bootstrap case.
    """
    _load_dotenv_once()

    conn_info = resolve_credentials({"local_env": "BI_PRIMARY"})
    if conn_info is None:
        raise ValueError(
            "No credentials could be resolved. Either add data_sources "
            "to report.yaml / data-sources/config.yaml, or set "
            "BI_PRIMARY_HOST / _USER / _PASS in your .env file."
        )

    return connect("vertica", conn_info)

