"""Install the demo project into a directory, then generate its warehouse.

    python -m trellum.demo                  # into the current directory
    python -m trellum.demo --dest myproj
    python -m trellum.demo --list           # show what would be installed
    python -m trellum.demo --small          # 60 days of data instead of 420
    python -m trellum.demo --force          # overwrite reports already present

The point is that a fresh install has something to run. After this, the reports
are ordinary files in your project -- edit them, break them, copy from them.
Nothing here is special-cased by the framework at build time.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from trellum.demo import DEMO_ROOT

#: Never copied into a user's project.
#:   _template     -- scaffolding source for `python -m trellum.new`
#:   trellum/    -- dev-only import shim, meaningless outside this repo
#:   output/       -- build products
_SKIP_DIRS = {"_template", "trellum", "output", "tools", "__pycache__"}


def _demo_reports() -> list[Path]:
    reports = DEMO_ROOT / "reports"
    if not reports.is_dir():
        return []
    return sorted(
        p for p in reports.iterdir()
        if p.is_dir() and p.name not in _SKIP_DIRS and (p / "report.yaml").exists()
    )


def _copy_tree(src: Path, dst: Path, *, force: bool) -> tuple[int, int]:
    """Copy src -> dst. Returns (copied, skipped)."""
    copied = skipped = 0
    for item in src.rglob("*"):
        if any(part in _SKIP_DIRS for part in item.relative_to(src).parts):
            continue
        if item.is_dir():
            continue
        target = dst / item.relative_to(src)
        if target.exists() and not force:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return copied, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m trellum.demo",
        description="Install the packaged demo project and generate its data.",
    )
    parser.add_argument("--dest", default=".", metavar="DIR",
                        help="Project directory to install into (default: cwd)")
    parser.add_argument("--list", action="store_true",
                        help="List the demo reports and exit")
    # Small is the DEFAULT for a project install. The demo in somebody's
    # repository is a worked example to read and copy from, and 60 days of
    # data serves that at a fraction of the size; the full 420-day warehouse
    # exists to showcase scale, which is the published gallery's job (its
    # workflow generates --full). The two are the same reports over the same
    # generator -- only the amount of history differs.
    parser.add_argument("--small", action="store_true",
                        help="Generate 60 days of data (the default; kept as "
                             "a flag for compatibility)")
    parser.add_argument("--full", action="store_true",
                        help="Generate the full 420-day warehouse, including "
                             "the full warehouse used by the published demo "
                             "gallery")
    parser.add_argument("--days", type=int, default=None,
                        help="Days of history to generate (overrides both)")
    parser.add_argument("--anchor", default=None, metavar="YYYY-MM-DD",
                        help="Last day of generated data (default: today)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite files that already exist")
    parser.add_argument("--no-data", action="store_true",
                        help="Copy the reports but skip generating the warehouse")
    args = parser.parse_args(argv)

    reports = _demo_reports()

    if args.list:
        if not reports:
            print("No demo reports are present in this installation.", file=sys.stderr)
            return 1
        print(f"{len(reports)} demo report(s) in {DEMO_ROOT}:\n")
        for r in reports:
            print(f"  {r.name}")
        print("\nInstall them with: python -m trellum.demo")
        return 0

    if not reports:
        print("error: this installation carries no demo reports.", file=sys.stderr)
        return 1

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Installing the demo project into {dest}\n")

    copied, skipped = 0, 0
    for sub in ("reports", "data-sources"):
        src = DEMO_ROOT / sub
        if not src.is_dir():
            continue
        c, s = _copy_tree(src, dest / sub, force=args.force)
        copied += c
        skipped += s
        print(f"  {sub:<14} {c} file(s) copied" + (f", {s} kept" if s else ""))

    # Project-root files the reports depend on. Without these, reports that
    # declare governed KPI metric claims (e.g. player-overview) fail
    # validation (metric-undefined, FAIL) purely because the file defining
    # the metric never arrived at the destination project.
    for name in ("config.yaml", "metrics.yaml", "assistant.md"):
        src_file = DEMO_ROOT / name
        if not src_file.is_file():
            continue
        dst_file = dest / name
        if dst_file.exists() and not args.force:
            skipped += 1
            continue
        shutil.copy2(src_file, dst_file)
        copied += 1
        print(f"  {name:<14} copied")

    if skipped and not args.force:
        print("\n  (files that already existed were kept -- pass --force to overwrite)")

    if args.no_data:
        print("\nSkipped data generation (--no-data).")
        return 0

    # The warehouse is generated rather than shipped: it keeps the wheel small,
    # and it dates the data relative to today instead of to the release build.
    print()
    from trellum.demo.tools import make_fixtures

    fixture_argv = ["--project-root", str(dest),
                    "--out", str(dest / "data-sources" / "demo.sqlite")]
    if args.days is not None:
        fixture_argv += ["--days", str(args.days)]
    elif not args.full:
        # Small unless asked otherwise -- see the flag definitions above.
        fixture_argv.append("--small")
    if args.anchor:
        fixture_argv += ["--anchor", args.anchor]

    rc = make_fixtures.main(fixture_argv)
    if rc != 0:
        return rc

    first = reports[0].name
    print("\nReady. Try:\n")
    print(f"  python -m trellum.run reports/{first} --serve")
    print("  python -m trellum.run --all --serve      # every demo report, one server")
    print("\nThe data is a SQLite file you can open and query directly:")
    print(f"  {dest / 'data-sources' / 'demo.sqlite'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
