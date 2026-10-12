"""Create Markdown analyses and import portable report captures."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from trellum.analysis_capture import import_capture, safe_name
from trellum.cli.commands.analysis_session import (
    _cmd_analysis_discard,
    _cmd_analysis_import_inbox,
    _cmd_analysis_inbox,
    _cmd_analysis_session,
)
from trellum.project import get_project_root


def new_analysis(slug: str, format: str = "article") -> Path:
    if format not in ("article", "presentation"):
        raise ValueError("Analysis format must be 'article' or 'presentation'")
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
    if format == "presentation":
        config["format"] = "presentation"
    (directory / "report.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    headings = ("Question", "Findings", "Evidence", "Assumptions", "Recommendation")
    if format == "presentation":
        slides = {"version": 1, "slides": [
            {"id": "question", "layout": "title", "title": config["name"],
             "text": "State the question and reporting period."},
            {"id": "findings", "layout": "text", "title": "Findings",
             "text": "Explain the finding, assumptions, and recommended next step."},
        ]}
        (directory / "slides.yaml").write_text(yaml.safe_dump(slides, sort_keys=False), encoding="utf-8")
    else:
        (directory / "content.md").write_text("\n\n".join(f"## {heading}\n\n" for heading in headings), encoding="utf-8")
    (directory / "evidence").mkdir()
    return directory


def _cmd_analysis(args) -> int:
    try:
        if args.verb == "new":
            print(f"Created {new_analysis(args.slug, args.format)}")
        else:
            if bool(args.capture_file) == bool(args.inbox):
                raise ValueError("Provide one capture file or --inbox ID")
            if args.inbox:
                return _cmd_analysis_import_inbox(args)
            print(import_capture(args.report_dir, args.capture_file, args.name))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1


def add_analysis_commands(sub) -> None:
    command = sub.add_parser("analysis", help="create articles or presentations and import PNG evidence")
    verbs = command.add_subparsers(dest="verb", required=True)
    new = verbs.add_parser("new", help="scaffold reports/<slug>/ with kind: analysis")
    new.add_argument("slug")
    new.add_argument("--format", choices=("article", "presentation"), default="article")
    new.set_defaults(fn=_cmd_analysis)
    capture = verbs.add_parser("import", help="import a capture into evidence/ and print Markdown")
    capture.add_argument("report_dir")
    capture.add_argument("capture_file", nargs="?", help="downloaded capture JSON file")
    capture.add_argument("--inbox", help="pending local capture ID from the review poll")
    capture.add_argument("--name", help="safe evidence name; existing files are never overwritten")
    capture.set_defaults(fn=_cmd_analysis)
    session = verbs.add_parser("session", help="build, preview and review one local analysis")
    session.add_argument("report", help="reports/<slug> or an analysis slug")
    session.add_argument("--port", type=int)
    session.add_argument("--no-browser", action="store_true")
    session.add_argument("--rebuild", action="store_true")
    session.add_argument("--restart-server", action="store_true")
    session.set_defaults(fn=_cmd_analysis_session)
    inbox = verbs.add_parser("inbox", help="list pending local capture IDs")
    inbox.add_argument("report_dir")
    inbox.set_defaults(fn=_cmd_analysis_inbox)
    discard = verbs.add_parser("discard", help="discard one pending local capture")
    discard.add_argument("report_dir")
    discard.add_argument("capture_id")
    discard.set_defaults(fn=_cmd_analysis_discard)
