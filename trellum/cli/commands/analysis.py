"""Create Markdown analyses and import portable report captures."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from trellum.analysis_capture import import_capture, safe_name
from trellum.project import get_project_root


def new_analysis(slug: str) -> Path:
    slug = safe_name(slug)
    project = Path(get_project_root()).resolve()
    reports = project / "reports"
    if not reports.resolve().is_relative_to(project):
        raise ValueError("Reports directory must stay inside the project")
    reports.mkdir(exist_ok=True)
    directory = reports / slug
    directory.mkdir()  # Existing content is never overwritten.
    config = {
        "kind": "analysis", "slug": slug, "name": slug.replace("-", " ").title(),
        "description": "An authored analysis explaining the question, findings, evidence, assumptions and recommendation.",
        "studio": "default", "category": "Analyses", "author": "", "tags": [],
    }
    (directory / "report.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    headings = ("Question", "Findings", "Evidence", "Assumptions", "Recommendation")
    (directory / "content.md").write_text("\n\n".join(f"## {heading}\n\n" for heading in headings), encoding="utf-8")
    (directory / "evidence").mkdir()
    return directory


def _cmd_analysis(args) -> int:
    try:
        if args.verb == "new":
            print(f"Created {new_analysis(args.slug)}")
        else:
            print(import_capture(args.report_dir, args.capture_file, args.name))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1


def add_analysis_commands(sub) -> None:
    command = sub.add_parser("analysis", help="create Markdown articles or import PNG evidence")
    verbs = command.add_subparsers(dest="verb", required=True)
    new = verbs.add_parser("new", help="scaffold reports/<slug>/ with kind: analysis")
    new.add_argument("slug")
    new.set_defaults(fn=_cmd_analysis)
    capture = verbs.add_parser("import", help="import a capture into evidence/ and print Markdown")
    capture.add_argument("report_dir")
    capture.add_argument("capture_file")
    capture.add_argument("--name", help="safe evidence name; existing files are never overwritten")
    capture.set_defaults(fn=_cmd_analysis)
