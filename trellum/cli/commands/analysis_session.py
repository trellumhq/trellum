"""Agent-facing local analysis session commands."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from trellum.cli.commands.review import (
    _cmd_review_start,
    _resolve_report_dir,
    _review_http,
    _review_port_or_explain,
)
from trellum.project import get_project_root
from trellum.report import BaseReport


def _cmd_analysis_session(args: argparse.Namespace) -> int:
    report_dir = _resolve_report_dir(args.report)
    if report_dir is None:
        return 1
    project = Path(get_project_root()).resolve()
    if not report_dir.is_relative_to(project / "reports"):
        print("Analysis session requires a report in this project's reports/ directory",
              file=sys.stderr)
        return 1
    try:
        config = BaseReport.load_config(str(report_dir))
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1
    if config.get("kind") != "analysis":
        print("Analysis session requires a kind: analysis directory", file=sys.stderr)
        return 1
    if config.get("slug", report_dir.name) != report_dir.name:
        print("Analysis manifest slug must match its reports/ directory for a local session",
              file=sys.stderr)
        return 1

    # Reuse the report review lifecycle; activate capture intake only after it starts.
    review_args = argparse.Namespace(
        report=args.report,
        port=args.port,
        no_browser=True,
        rebuild=True,
        restart_server=args.restart_server,
    )
    if _cmd_review_start(review_args):
        return 1
    output_base = (Path(get_project_root()) / "output").resolve()
    port = _review_port_or_explain(output_base)
    if port is None:
        return 1
    slug = config.get("slug", report_dir.name)
    try:
        code, response = _review_http(
            port, "POST", "/_fw/review/analysis/start", {"slug": slug}
        )
    except OSError as exc:
        print(f"Could not activate analysis inbox: {exc}", file=sys.stderr)
        return 1
    if code in (404, 501):
        if not args.restart_server:
            print("The running server predates analysis sessions. Retry with "
                  f"analysis session {args.report} --restart-server", file=sys.stderr)
            return 1
        from trellum import runner
        from trellum.cli.commands.serve import _ensure_background_server

        runner._claim_port(port, str(output_base))
        port, _ = _ensure_background_server(output_base, args.port)
        if port is None:
            print("Could not restart the local server for analysis", file=sys.stderr)
            return 1
        try:
            code, response = _review_http(
                port, "POST", "/_fw/review/analysis/start", {"slug": slug}
            )
        except OSError as exc:
            print(f"Could not activate analysis inbox: {exc}", file=sys.stderr)
            return 1
    if code != 200:
        print(f"Could not activate analysis inbox: HTTP {code} {response}", file=sys.stderr)
        return 1
    url = f"http://localhost:{port}/{slug}/index.html"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"Analysis session: {url}")
    print(f"Local reports: http://localhost:{port}/")
    print(f"Listen with: python -m trellum review poll --report {slug}")
    return 0


def _cmd_analysis_import_inbox(args: argparse.Namespace) -> int:
    from trellum.analysis_inbox import import_pending_capture

    try:
        print(import_pending_capture(args.report_dir, args.inbox, args.name))
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_analysis_discard(args: argparse.Namespace) -> int:
    from trellum.analysis_inbox import discard_pending_capture

    try:
        discard_pending_capture(args.report_dir, args.capture_id)
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1
    print(f"Discarded pending capture {args.capture_id}")
    return 0


def _cmd_analysis_inbox(args: argparse.Namespace) -> int:
    from trellum.analysis_inbox import list_pending_captures

    try:
        captures = list_pending_captures(args.report_dir)
    except (OSError, ValueError) as exc:
        print(f"Analysis: {exc}", file=sys.stderr)
        return 1
    if not captures:
        print("No pending captures")
    for capture in captures:
        source = "".join(c if c.isprintable() else " " for c in capture["source_slug"])
        title = "".join(c if c.isprintable() else " " for c in capture["component_title"])
        print(f"{capture['capture_id']}  {source}  {title}")
    return 0
