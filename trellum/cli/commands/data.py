"""`data` and `query`: what the warehouse holds, and asking it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: Source types we can inspect without a network round-trip or a credential.
#: Everything else is named but not opened unless asked for by name -- a session
#: must never hang on a warehouse the agent may have no access to.
_LOCAL_TYPES = {"sqlite", "file"}

#: A header read should be instant. If a file is large or on a slow mount this
#: stops `trellum data` becoming the thing that costs the run its time.
_PREVIEW_ROWS = 0

#: Columns whose span decides whether a query returns anything at all.
_DATE_HINTS = ("date", "day", "month", "week", "ts", "time")

def _sqlite_tables(path: Path) -> list[str]:
    import sqlite3

    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        out = []
        for (name,) in conn.execute(
                "select name from sqlite_master where type='table' order by name"):
            cols = [r[1] for r in conn.execute(f"pragma table_info('{name}')")]
            rows = conn.execute(f"select count(*) from '{name}'").fetchone()[0]
            out.append(f"{name} ({rows:,} rows): {', '.join(cols)}")

            # The span of the date column, because "which dates exist" decides
            # whether a query returns anything, and getting it wrong returns
            # zero rows rather than an error. A run spent roughly fifteen tool
            # calls on this -- querying MIN/MAX, reading site-packages, grepping
            # for FW_NOW, opening the meta table -- to learn that demo data is
            # anchored weeks in the past while the report template defaults to
            # asking for the last seven days.
            if not rows:
                continue
            date_col = next((c for c in cols
                             if any(h in c.lower() for h in _DATE_HINTS)), None)
            if not date_col:
                continue
            try:
                lo, hi = conn.execute(
                    f"select min(\"{date_col}\"), max(\"{date_col}\") "
                    f"from '{name}'").fetchone()
            except Exception:
                continue
            if lo is not None and hi is not None:
                out.append(f"    {date_col} spans {lo} .. {hi}"
                           f"  <- query outside this and you get zero rows")
        return out

def _file_columns(path: Path) -> list[str]:
    import pandas as pd

    reader = pd.read_excel if path.suffix.lower() in (".xlsx", ".xls") else pd.read_csv
    df = reader(path, nrows=_PREVIEW_ROWS)
    return [f"{path.name}: {', '.join(map(str, df.columns))}"]

def _cmd_data(args: argparse.Namespace) -> int:
    """What this project's warehouse actually contains, read at call time.

    Every measured run rediscovers this by hand -- `cat data-sources/config.yaml`
    then a throwaway `python -c "import sqlite3; ..."` -- costing one to three
    tool calls and being the only place an agent has to guess a file path.

    Deliberately computed rather than stored. A schema written into the repo or
    into generated context is correct until someone adds a column, and a
    confidently wrong schema is worse than none: the agent has no reason to
    doubt it. Nothing here is persisted, so there is nothing to go stale.
    """
    from trellum.data.datasource_config import load_datasource_config
    from trellum.project import get_project_root

    try:
        sources = load_datasource_config()
    except Exception as exc:
        print(f"could not read data-sources/config.yaml: {exc}", file=sys.stderr)
        return 1

    if not sources:
        print("No data sources configured. Add them to data-sources/config.yaml, "
              "or run `python -m trellum.demo` for a warehouse to build against.")
        return 0

    root = Path(get_project_root())
    wanted = [args.source] if args.source else list(sources)
    unknown = [n for n in wanted if n not in sources]
    if unknown:
        print(f"no such data source: {', '.join(unknown)}. "
              f"Configured: {', '.join(sources)}", file=sys.stderr)
        return 1

    for name in wanted:
        src = sources[name] or {}
        kind = src.get("type", "?")
        desc = src.get("description", "")
        print(f"\n{name} ({kind})" + (f" -- {desc}" if desc else ""))

        # Named-but-not-opened unless asked for: connecting to a remote
        # warehouse can block or need credentials this session does not have.
        if kind not in _LOCAL_TYPES and not args.source:
            print(f"  not inspected (remote). `python -m trellum data {name}` "
                  f"to connect.")
            continue

        path = src.get("path")
        if not path:
            print("  no `path` configured; nothing to inspect.")
            continue
        target = Path(path)
        if not target.is_absolute():
            target = root / target
        if not target.exists():
            print(f"  {target} does not exist yet.")
            continue

        try:
            rows = (_sqlite_tables(target) if kind == "sqlite"
                    else _file_columns(target))
        except Exception as exc:
            print(f"  could not inspect {target.name}: {exc}")
            continue
        for row in rows:
            print(f"  {row}")

    print("\nColumns above are the real ones. Reference anything else and the "
          "build fails.")
    print("Ad-hoc SQL, by source name (no file paths): "
          "python -m trellum query \"SELECT ...\"")
    return 0

def _cmd_query(args: argparse.Namespace) -> int:
    """Ad-hoc SQL against a configured source, addressed by NAME.

    The front door this always needed. Without it, the easy route to "what
    were the sessions on the 15th" was raw sqlite3 with a hardcoded file
    path -- a measured session spent three tool calls running `find . -iname
    "*.sqlite"` first -- and every hardcoded path breaks the day the source
    moves to a warehouse. This command resolves the source the same way
    reports do, so the query that works against the demo file works
    unchanged against the production connection.

    SQLite sources are opened READ-ONLY: an exploration tool must not be
    able to mutate the warehouse, and the write-shaped mistakes an agent can
    make in one line are exactly the ones nobody notices until later.
    Remote sources rely on the warehouse's own permissions.
    """
    import time as _time

    from trellum.data.datasource_config import load_datasource_config

    try:
        sources = load_datasource_config()
    except Exception as exc:
        print(f"could not read data-sources/config.yaml: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print("No data sources configured. Add them to data-sources/config.yaml, "
              "or run `python -m trellum.demo` for a warehouse to query.",
              file=sys.stderr)
        return 1

    name = args.source
    if not name:
        if len(sources) == 1:
            name = next(iter(sources))
        else:
            print(f"several sources configured; pick one with --source: "
                  f"{', '.join(sources)}", file=sys.stderr)
            return 1
    if name not in sources:
        print(f"no such data source: {name}. Configured: {', '.join(sources)}",
              file=sys.stderr)
        return 1

    params: dict[str, str] = {}
    for raw in args.param or []:
        key, sep, value = raw.partition("=")
        if not sep or not key:
            print(f"--param must be KEY=VALUE, got {raw!r}", file=sys.stderr)
            return 1
        params[key] = value

    kind = (sources[name] or {}).get("type", "")
    try:
        # Same resolution as `from trellum import query`, so the shell and a
        # notebook cannot disagree about read-only or about which file.
        from trellum.data.adhoc import connect
        conn = connect(name)
    except Exception as exc:
        print(f"could not connect to '{name}': {exc}", file=sys.stderr)
        return 1

    import contextlib

    from trellum.data.query import query_df

    started = _time.perf_counter()
    try:
        # query_df narrates progress on stdout for build logs. Here stdout is
        # the DATA -- `--csv` must be parseable and the table must be clean --
        # so the narration goes to stderr, where it still shows but never
        # corrupts what a pipe receives.
        with contextlib.redirect_stdout(sys.stderr):
            df = query_df(conn, args.sql, params=params or None)
    except Exception as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 1
    elapsed_ms = (_time.perf_counter() - started) * 1000

    if args.csv:
        # Machine output: complete and uncapped, nothing but the data.
        print(df.to_csv(index=False), end="")
        return 0

    total = len(df)
    if total == 0:
        print("0 rows.")
        if kind == "sqlite":
            print("If you filtered by date: `python -m trellum data` prints "
                  "each table's date span -- queries outside it return nothing.")
        return 0

    shown = min(total, args.max_rows)
    print(df.head(shown).to_string(index=False))
    if shown < total:
        print(f"\n({shown} of {total:,} rows shown -- raise with --max-rows, "
              f"or --csv for all of them)")
    print(f"{total:,} row(s) in {elapsed_ms:.0f} ms")
    return 0
