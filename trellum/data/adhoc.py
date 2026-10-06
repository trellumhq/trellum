"""A DataFrame from a configured source, with no report.

``reports/<slug>/`` is the right tool for a page somebody reads on a schedule.
It is the wrong tool for "what was revenue yesterday" -- that wants a number,
not a build. This is the whole surface for that case::

    from trellum import query
    df = query("warehouse", "SELECT ... WHERE day = :day", {"day": "2026-06-15"})

Sources are addressed by NAME from ``data-sources/config.yaml``, exactly as
reports and ``trellum query`` address them, so an answer given today and a
report built next week cannot disagree about where the data came from.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from trellum import project
from trellum.data.datasource_config import (
    invalidate_datasource_cache,
    list_datasources,
    load_datasource_config,
)

if TYPE_CHECKING:
    import pandas as pd

_CONFIG = os.path.join("data-sources", "config.yaml")


def _infer_root() -> None:
    """Find the project root when called from a subdirectory.

    A notebook lives in ``notebooks/``; a script runs from wherever. Reports
    are launched from the project root and ``get_project_root()`` stays exactly
    as it is for them. Here only: an explicit root (``set_project_root`` or
    ``FW_PROJECT_ROOT``) is respected, cwd wins when it holds the config, and
    otherwise the nearest ancestor holding ``data-sources/config.yaml`` becomes
    the root. No config anywhere: nothing changes, and the loader reports no
    sources.
    """
    if project._project_root_override or "FW_PROJECT_ROOT" in os.environ:
        return
    here = os.getcwd()
    if os.path.isfile(os.path.join(here, _CONFIG)):
        return
    while (parent := os.path.dirname(here)) != here:
        here = parent
        if os.path.isfile(os.path.join(here, _CONFIG)):
            project.set_project_root(here)
            invalidate_datasource_cache()
            return


def sources() -> list[dict]:
    """The configured sources, as ``data-sources/config.yaml`` declares them."""
    _infer_root()
    return list_datasources()


def connect(name: str) -> Any:
    """A live connection to the configured source *name*.

    Local file databases (sqlite and duckdb with a ``path``) are opened
    READ-ONLY: an exploration entry point must never be able to mutate the
    warehouse. Remote warehouses rely on their own permissions. The caller
    closes the connection; :func:`query` does that for you.
    """
    from trellum.data.connections import _resolve_local_db_path, resolve_connection

    _infer_root()
    configured = load_datasource_config()
    if not configured:
        raise LookupError("No data sources configured. Add them to "
                          "data-sources/config.yaml at the project root.")
    if name not in configured:
        raise KeyError(f"no such data source: {name}. "
                       f"Configured: {', '.join(configured)}")

    src = configured[name] or {}
    kind = src.get("type", "")
    if kind == "sqlite" and src.get("path"):
        import sqlite3

        path = _resolve_local_db_path(name, kind, src["path"])
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if kind == "duckdb" and src.get("path"):
        import duckdb

        path = _resolve_local_db_path(name, kind, src["path"])
        # duckdb refuses read-only for :memory:, and there is nothing on
        # disk to protect there.
        return duckdb.connect(path, read_only=path != ":memory:")
    return resolve_connection(name, [name])


def query(source: str, sql: str, params: dict | None = None,
          cache_ttl: int | None = None) -> pd.DataFrame:
    """Run *sql* against the configured source *source*; return a DataFrame.

    ``:name`` placeholders bind from *params*. Results are cached under
    ``<root>/output/.query_cache`` exactly as report queries are (24h by
    default; ``cache_ttl=0`` skips it).
    """
    from trellum.data.connections import forget_source, register_source
    from trellum.data.query import query_df

    # ponytail: one connection per call. A per-source connection cache is the
    # upgrade if remote round-trips start to hurt.
    conn = connect(source)
    register_source(conn, source)
    try:
        return query_df(conn, sql, params=params, cache_ttl=cache_ttl)
    finally:
        forget_source(conn)
        # Not optional: an open sqlite handle on Windows keeps the file locked.
        conn.close()
