"""Add a data source without knowing where its pieces go.

Configuring a source by hand means knowing that non-secret connection details
belong in ``data-sources/config.yaml``, that credentials belong in ``.env``
under a prefix, and which suffixes that prefix takes. None of that is knowledge
the person supplying a host and a password has, or should need -- and getting
it wrong surfaces much later as a connection error that describes the symptom
rather than the mistake.

So this command takes the values and does the wiring, then connects before
claiming anything worked. A wrong password is reported here, in the same breath
as the attempt, rather than at the next report build.

The password is deliberately awkward to pass in bulk: prompted when there is a
terminal, accepted as a flag when there is not. The values that identify a
source are structural and fine to script; the secret is the person's.
"""

from __future__ import annotations

import getpass
import os
import re
import sys

# Types whose "connection" is a file path, not a network endpoint. These carry
# no credentials at all, so they get an inline path and no .env entry (see
# connections.resolve_connection, which short-circuits the resolver chain for
# exactly this case).
_PATH_TYPES = ("sqlite", "duckdb", "file")


def _prefix_for(name: str) -> str:
    """BI_SALES_EAST for 'sales-east'. Stable, guessable, and valid as a shell
    identifier -- which it has to be, since it becomes an env var prefix."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return f"BI_{slug}"


def _scrub(text: str, secret: str | None) -> str:
    """Never echo the password back, even inside a driver's own error text."""
    if secret:
        text = text.replace(secret, "********")
    return text


def _update_env_file(path: str, values: dict[str, str]) -> list[str]:
    """Set KEY=VALUE in a .env, replacing existing keys and keeping the rest.

    Returns the keys written. Rewriting the file wholesale would discard
    comments and unrelated settings, so existing lines are edited in place and
    only genuinely new keys are appended.
    """
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out).rstrip("\n") + "\n")
    return list(values)


def _resolve_password(args, needs_credentials: bool) -> str | None:
    if not needs_credentials or not args.user:
        return None
    if args.password is not None:
        return args.password
    if sys.stdin.isatty():
        return getpass.getpass(f"Password for {args.user}: ")
    return None


def cmd_datasource_add(args) -> int:
    from trellum.data.datasource_config import (
        load_datasource_config,
        save_datasource_config,
    )
    from trellum.data.drivers import get_driver
    from trellum.project import get_project_root

    name = args.name
    ds_type = args.type
    root = get_project_root()

    try:
        driver = get_driver(ds_type)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    is_path_type = ds_type in _PATH_TYPES
    if is_path_type and not args.path:
        print(f"A {ds_type} source needs --path.", file=sys.stderr)
        return 1
    if not is_path_type and not args.host:
        print(f"A {ds_type} source needs --host.", file=sys.stderr)
        return 1

    entry: dict = {"type": ds_type}
    if args.description:
        entry["description"] = args.description

    prefix = _prefix_for(name)
    password = None

    if is_path_type:
        entry["path"] = args.path
    else:
        entry["host"] = args.host
        port = args.port if args.port is not None else driver.default_port
        if port:
            entry["port"] = port
        if args.database:
            entry["database"] = args.database
        # The prefix is recorded even when no credentials are supplied now, so
        # adding them later is editing .env rather than rediscovering this.
        entry["credentials"] = {"local": prefix}
        password = _resolve_password(args, needs_credentials=True)
        if args.user and password is None and args.password is None:
            print(
                f"No password given and no terminal to prompt on. Pass "
                f"--password, or set {prefix}_PASS in .env yourself.",
                file=sys.stderr,
            )
            return 1

    sources = dict(load_datasource_config())
    existed = name in sources
    if existed and not args.force:
        print(
            f"Data source '{name}' already exists. Re-run with --force to "
            f"replace it.",
            file=sys.stderr,
        )
        return 1
    sources[name] = entry
    save_datasource_config(sources)

    written_keys: list[str] = []
    if args.user:
        env_values = {f"{prefix}_USER": args.user}
        if password is not None:
            env_values[f"{prefix}_PASS"] = password
        written_keys = _update_env_file(os.path.join(root, ".env"), env_values)
        # Make them visible to this process too, so the connection test below
        # exercises the same values a fresh run would read from .env.
        for key, value in env_values.items():
            os.environ[key] = value

    verb = "Replaced" if existed else "Added"
    # flush: the connection test below writes failures to stderr, and an
    # unflushed stdout buffer puts "could not connect" above "added" when the
    # two are redirected separately.
    print(f"{verb} data source '{name}' ({ds_type}) in data-sources/config.yaml",
          flush=True)
    if not is_path_type:
        # A path source carries no credentials, so the hint would not apply.
        print("  a portal serving this project reads that declaration; its "
              "credentials are entered in the portal", flush=True)
    if written_keys:
        print(f"  wrote {', '.join(written_keys)} to .env", flush=True)

    if args.no_test:
        print("  connection not tested (--no-test)")
        return 0

    return _test_connection(name, password)


def _test_connection(name: str, password: str | None) -> int:
    """Connect the way a report build will, and say what happened.

    Goes through ``resolve_connection`` rather than calling a driver directly:
    the point is to prove the path the build actually takes, config file and
    resolver chain included, not a parallel implementation of it that could
    succeed while the real one fails.
    """
    from trellum.data.connections import resolve_connection
    from trellum.data.resolvers import _SOURCE_CONN_KEYS
    from trellum.data.retry import ManagedConnection, run_with_retry

    try:
        conn = resolve_connection(name, [name])
        if isinstance(conn, ManagedConnection) and conn._new_connection_per_query:
            try:
                run_with_retry(conn, lambda raw: None, sql="SELECT 1")
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001 -- driver errors are not a fixed type
        detail = _scrub(str(exc), password)
        if isinstance(exc, KeyError) and exc.args and exc.args[0] in _SOURCE_CONN_KEYS:
            # A driver asked conn_info for a field no flag of this command
            # supplies (http_path, access_token, ...). Name the env var that
            # would, rather than echoing a bare 'http_path'.
            detail = (f"missing connection field '{exc.args[0]}'; "
                      f"set {_prefix_for(name)}_{exc.args[0].upper()} in .env")
        print(f"\nCould not connect: {detail}", file=sys.stderr)
        print("The source is saved; fix the details and re-run with --force, "
              "or edit .env directly.", file=sys.stderr)
        return 1

    close = getattr(conn, "close", None)
    if close:
        try:
            close()
        except Exception:  # noqa: BLE001 -- a failed close is not a failed test
            pass
    print(f"  connected -- '{name}' is ready to use in a report's data_sources")
    return 0


def add_datasource_commands(sub) -> None:
    from trellum.data.drivers import _registry as driver_types

    ds = sub.add_parser(
        "datasource",
        help="add a data source: writes config.yaml + .env, then connects",
    )
    dssub = ds.add_subparsers(dest="datasource_cmd")

    add = dssub.add_parser("add", help="add (or replace) a source and test it")
    add.add_argument("name", help="what reports will call it")
    add.add_argument("--type", required=True,
                     help="one of: " + ", ".join(sorted(driver_types)))
    add.add_argument("--host")
    add.add_argument("--port", type=int, help="defaults to the driver's port")
    add.add_argument("--database")
    add.add_argument("--user")
    add.add_argument("--password",
                     help="prompted for when omitted and a terminal is present")
    add.add_argument("--path", help="file path for sqlite / duckdb / file types")
    add.add_argument("--description")
    add.add_argument("--force", action="store_true",
                     help="replace an existing source of the same name")
    add.add_argument("--no-test", action="store_true",
                     help="skip the connection check")
    add.set_defaults(fn=cmd_datasource_add)
