"""`validate`: run the validator against a built report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_validate(args: argparse.Namespace) -> int:
    """Report the validation state of an already-built report.

    Reads the build output rather than rebuilding: a status command that
    silently triggers a build is a status command nobody can trust.
    """
    target = Path(args.report_dir).resolve()
    candidates = [target / "_validation.json",
                  target.parent.parent / "output" / target.name / "_validation.json"]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        print(f"no build output for {target.name}: run "
              f"`python -m trellum.run {args.report_dir} --no-serve` first",
              file=sys.stderr)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    checks = data.get("checks", data if isinstance(data, list) else [])
    bad = [c for c in checks
           if str(c.get("level", "")).lower() in ("fail", "warn")
           and not c.get("suppressed")]
    if args.json:
        print(json.dumps(bad, indent=2))
        return 0
    if not bad:
        print(f"{target.name}: all checks passed")
        return 0
    for c in bad:
        where = c.get("component") or c.get("dataset_id") or c.get("section") or ""
        print(f"  {str(c.get('level','?')).upper():<5} {c.get('id','?')}"
              + (f"  ({where})" if where else ""))
        if c.get("message"):
            print(f"        {c['message']}")
    return 1 if any(str(c.get("level", "")).lower() == "fail" for c in bad) else 0
