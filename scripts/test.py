"""Reproducible focused local and complete ordinary portal test commands."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def worker_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 16") from exc
    if not 1 <= count <= 16:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 16")
    return count


def nonnegative(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("durations must be a nonnegative integer") from exc
    if count < 0:
        raise argparse.ArgumentTypeError("durations must be a nonnegative integer")
    return count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="suite", required=True)
    local = commands.add_parser("local", help="run explicitly selected portal tests")
    portal = commands.add_parser("portal", help="run every ordinary portal test, including browsers")
    local.add_argument("tests", nargs="+", help="test paths or node IDs, relative to the repository root")
    local.add_argument("-k", dest="keyword", help="further narrow the explicit selection")
    local.add_argument("--reuse-db", action="store_true", help="keep worker test databases between runs")
    local.add_argument("--create-db", action="store_true", help="rebuild reused databases after schema changes")
    for command, workers in ((local, 4), (portal, 2)):
        command.add_argument("--workers", type=worker_count, default=workers, help=f"1 to 16; 1 runs serially (default: {workers})")
        command.add_argument("--durations", type=nonnegative, default=20, help="number of slowest tests to report (default: 20)")
        command.add_argument("--junitxml", type=Path, help="JUnit output path (default: .data-test/results/<command>.xml)")
        command.add_argument("--collect-only", action="store_true", help="collect the selected tests without running them")
        command.add_argument("--dry-run", action="store_true", help="print the command without starting pytest")
    args = parser.parse_args(argv)
    if args.suite == "local" and args.create_db and not args.reuse_db:
        parser.error("--create-db requires --reuse-db; otherwise each run already creates fresh databases")
    if args.junitxml is None:
        args.junitxml = Path(".data-test/results") / f"{args.suite}.xml"
    return args


def pytest_command(args: argparse.Namespace) -> list[str]:
    # Explicit counts prevent machine CPU counts from unexpectedly multiplying
    # PostgreSQL databases or browser processes. One worker means plain pytest.
    command = [
        sys.executable, "-m", "pytest", "-q", "-ra",
        "-n", str(0 if args.workers == 1 else args.workers),
        "--dist", "loadscope", "--durations", str(args.durations),
        "--junitxml", str(args.junitxml), "-m", "not docker_sandbox and not portal_e2e",
    ]
    if args.collect_only:
        command.append("--collect-only")
    if args.suite == "local":
        if args.reuse_db:
            command.append("--reuse-db")
        if args.create_db:
            command.append("--create-db")
        if args.keyword:
            command.extend(["-k", args.keyword])
        command.extend(args.tests)
    else:
        command.append("apps")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    command = pytest_command(args)
    print(f"Repository: {ROOT}", flush=True)
    print(shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    junit_path = args.junitxml if args.junitxml.is_absolute() else ROOT / args.junitxml
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # An inherited -k/-m, --lf, or automatic worker count would change the
    # advertised selection. Keep this command independent of shell defaults.
    env.pop("PYTEST_ADDOPTS", None)
    started = time.monotonic()
    try:
        return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
    finally:
        print(f"Test command elapsed: {time.monotonic() - started:.1f}s", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
