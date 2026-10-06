"""List the reports in this project, with their last run status.

Reads report.yaml for each report and output/<slug>/_meta.json for run state.
Deliberately reads the artifacts directly rather than asking a control plane:
this is a framework-level tool and must work with nothing else installed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Running a script puts its own directory on sys.path, not the working
# directory, so the demo project root (which holds the `trellum` import
# shim) has to be added explicitly.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import yaml

from trellum.project import get_project_root
from trellum.runner import scan_report_configs


def _age(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
    except ValueError:
        return "?"
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    for unit, size in (("m", 60), ("h", 3600), ("d", 86400)):
        if secs < size * 60 or unit == "d":
            return f"{int(secs // size)}{unit} ago"
    return "?"


def _skipped_dirs(reports_dir: str, live: set[str]) -> list[tuple[str, str]]:
    """Report directories the runner will not pick up, and why."""
    out = []
    if not os.path.isdir(reports_dir):
        return out
    for entry in sorted(os.listdir(reports_dir)):
        path = os.path.join(reports_dir, entry)
        if not os.path.isdir(path) or entry in live:
            continue
        if entry.startswith("_"):
            out.append((entry, "underscore prefix"))
        elif not os.path.isfile(os.path.join(path, "report.yaml")):
            out.append((entry, "no report.yaml"))
        elif not os.path.isfile(os.path.join(path, "generator.py")):
            out.append((entry, "no generator.py"))
        else:
            cfg = {}
            try:
                with open(os.path.join(path, "report.yaml"), encoding="utf-8") as fh:
                    cfg = yaml.safe_load(fh) or {}
            except Exception:
                pass
            if cfg.get("disabled"):
                out.append((entry, "disabled: true"))
            elif cfg.get("hidden"):
                out.append((entry, "hidden: true"))
    return out


def main() -> int:
    root = get_project_root()
    found = scan_report_configs()

    if not found:
        print(f"No reports found under {os.path.join(root, 'reports')}.")
        print("Scaffold one with: python -m trellum.new <name>")
        return 0

    rows = []
    for entry in found:
        cfg = entry["config"]
        slug = entry["slug"]
        meta_path = os.path.join(root, "output", slug, "_meta.json")
        status, last_run = "-", None
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
                status = meta.get("last_status") or "-"
                last_run = meta.get("last_run")
            except Exception:
                status = "?"
        rows.append({
            "slug": slug,
            "name": cfg.get("name", slug),
            "category": cfg.get("category", "-"),
            "cron": (cfg.get("schedule") or {}).get("cron", "-"),
            "status": status,
            "age": _age(last_run),
        })

    widths = {k: max(len(k), max(len(str(r[k])) for r in rows))
              for k in ("slug", "name", "category", "cron", "status", "age")}
    header = "  ".join(k.upper().ljust(widths[k]) for k in
                       ("slug", "name", "category", "cron", "status", "age"))
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: r["slug"]):
        print("  ".join(str(r[k]).ljust(widths[k]) for k in
                        ("slug", "name", "category", "cron", "status", "age")))

    skipped = _skipped_dirs(os.path.join(root, "reports"), {r["slug"] for r in rows})
    if skipped:
        print()
        print("Not picked up by --all:")
        for name, why in skipped:
            print(f"  {name}  ({why})")

    print()
    print(f"{len(rows)} report(s). Run one with: dev run <slug>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
