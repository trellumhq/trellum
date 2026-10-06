#!/usr/bin/env python
"""The "just run something" command for the itest harness.

Brings up whatever containers the selected engines need, runs pytest
against this directory with the venv it was invoked with, then tears the
containers down again -- one command, opt-in, never part of the default
suite.

    python itest/run.py                              # light engines
    python itest/run.py --engines postgres,duckdb
    python itest/run.py --profile heavy               # adds vertica
    python itest/run.py --profile s3                  # adds an S3 emulator
    python itest/run.py --no-docker                   # embedded-only
    python itest/run.py --keep                        # leave containers up
    python itest/run.py --engines postgres -- -k test_query_df_round_trip

Pure Python / Windows-first: no shell scripts, uses ``sys.executable`` so it
always runs pytest with whatever interpreter (venv or otherwise) invoked
this script, and shells out to ``docker compose`` as a subprocess.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_ITEST_DIR = Path(__file__).resolve().parent
_COMPOSE_FILE = _ITEST_DIR / "docker-compose.yml"
_COMPOSE_PROJECT = "bi-framework-itest"

# engines.py imports cleanly with only the standard library at module scope
# (see its own docstring), so it's safe to import directly here without the
# demo/framework shim conftest.py uses -- run.py never needs to import
# `trellum` itself, it only reads EngineSpec.compose_service.
sys.path.insert(0, str(_ITEST_DIR))
from engines import ENGINES, ensure_ssh_keypair  # noqa: E402

# run.py's own default engine sets -- distinct from conftest.py's
# _DEFAULT_ENGINES (which is embedded-only, for when pytest is invoked
# directly with no --engines). This is the "light" set: every container
# lane except vertica (heavy profile, opt-in) and s3 (its own profile).
_LIGHT_ENGINES = (
    "postgres,postgres_ssh,mysql,clickhouse,sqlserver,redshift,bigquery,trino,"
    "fakesnow,sqlite,duckdb,file,api"
)
_EMBEDDED_ENGINES = "fakesnow,sqlite,duckdb,file,api"

# Names accepted in --engines that aren't SQL EngineSpecs (mirrors
# conftest.py's _NON_SQL_ENGINE_NAMES) plus the compose service each one
# needs, when it needs one at all.
_NON_SQL_COMPOSE_SERVICE = {"file": None, "api": None, "s3": "minio"}

_SQLSERVER_DEFAULT_USER = "sa"
# SQL Server rejects engines.py's own default password ("itest_pw") on
# complexity grounds, and there's no env var to seed a non-sa login -- see
# docker-compose.yml's sqlserver service and itest/README.md for the full
# explanation. This is the one password standardized here rather than left
# to engines.py, which stays untouched.
_SQLSERVER_DEFAULT_PASS = "Itest!Passw0rd"


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Split argv on a literal ``--`` -- everything after it passes through
    to pytest untouched (``-k``, ``-x``, ``-v``, node IDs, ...)."""
    if "--" in argv:
        idx = argv.index("--")
        own, passthrough = argv[:idx], argv[idx + 1 :]
    else:
        own, passthrough = argv, []

    parser = argparse.ArgumentParser(
        description="Bring up containers, run the itest harness, tear down.",
    )
    parser.add_argument(
        "--engines",
        default=None,
        help=(
            "Comma-separated engine list (default: the light set -- "
            "everything except vertica/s3 -- or the embedded set with "
            "--no-docker)."
        ),
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=["heavy", "s3"],
        default=[],
        help="Repeatable. 'heavy' adds vertica, 's3' adds the S3 lane.",
    )
    parser.add_argument(
        "--no-docker", action="store_true",
        help="Skip containers entirely; run only the embedded engines.",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Leave containers running after the test run (for iteration).",
    )
    return parser.parse_args(own), passthrough


def _resolve_engines(args: argparse.Namespace) -> list[str]:
    if args.engines:
        names = [n.strip() for n in args.engines.split(",") if n.strip()]
    elif args.no_docker:
        names = _EMBEDDED_ENGINES.split(",")
    else:
        names = _LIGHT_ENGINES.split(",")

    if "heavy" in args.profile and "vertica" not in names:
        names.append("vertica")
    if "s3" in args.profile and "s3" not in names:
        names.append("s3")
    return names


def _compose_services(engine_names: list[str]) -> set[str]:
    services: set[str] = set()
    for name in engine_names:
        spec = ENGINES.get(name)
        if spec is not None:
            if spec.compose_service:
                services.add(spec.compose_service)
        else:
            svc = _NON_SQL_COMPOSE_SERVICE.get(name)
            if svc:
                services.add(svc)
    return services


def _profiles_for(services: set[str]) -> list[str]:
    profiles = []
    if "vertica" in services:
        profiles.append("heavy")
    if "minio" in services:
        profiles.append("s3")
    return profiles


def _check_docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, check=False,
        )
        return True
    except FileNotFoundError:
        return False


def _compose(*args: str, env: dict) -> int:
    cmd = ["docker", "compose", "-f", str(_COMPOSE_FILE), "-p", _COMPOSE_PROJECT, *args]
    print(f"  $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, env=env).returncode


def main() -> int:
    args, passthrough = _parse_args(sys.argv[1:])
    engine_names = _resolve_engines(args)
    services = set() if args.no_docker else _compose_services(engine_names)
    if "ssh" in services:
        # Before the environment is copied: compose needs the public key,
        # pytest the private one, from the same pair.
        ensure_ssh_keypair()

    env = os.environ.copy()
    env.setdefault("ITEST_SQLSERVER_USER", _SQLSERVER_DEFAULT_USER)
    env.setdefault("ITEST_SQLSERVER_PASS", _SQLSERVER_DEFAULT_PASS)

    started_containers = False

    if services:
        if not _check_docker_available():
            print(
                "itest/run.py: `docker compose` is not available on PATH.\n"
                "Install/start Docker Desktop, then re-run this command. "
                "(Use --no-docker to run only the embedded engines "
                "(fakesnow, sqlite, duckdb, file, api) without it.)",
                file=sys.stderr,
            )
            return 1

        profiles = _profiles_for(services)
        up_args = ["up", "-d", "--wait"]
        for p in profiles:
            up_args = ["--profile", p, *up_args]
        up_args += sorted(services)

        rc = _compose(*up_args, env=env)
        if rc != 0:
            print(
                f"itest/run.py: `docker compose up` failed (exit {rc}) for "
                f"services: {', '.join(sorted(services))}.",
                file=sys.stderr,
            )
            return rc
        started_containers = True

    pytest_cmd = [
        sys.executable, "-m", "pytest", "-q",
        f"--engines={','.join(engine_names)}",
        *passthrough,
    ]
    print(f"  $ {' '.join(pytest_cmd)}  (cwd={_ITEST_DIR})", flush=True)
    result = subprocess.run(pytest_cmd, cwd=str(_ITEST_DIR), env=env)

    if started_containers and not args.keep:
        _compose("down", "-v", env=env)
    elif started_containers and args.keep:
        print(
            f"  (containers left running -- `docker compose -f {_COMPOSE_FILE} "
            f"-p {_COMPOSE_PROJECT} down -v` to tear down)",
            flush=True,
        )

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
