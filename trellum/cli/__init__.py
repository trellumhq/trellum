"""The framework's agent-facing entry point.

    trellum                    what this is, and what to ask for next
    trellum guide <topic>      one section of depth, on demand
    trellum checks             every validator check id and its level
    trellum validate <dir>     the current validation state of a report

Two entry points, deliberately. ``python -m trellum.run`` builds reports and is
unchanged. This one exists so that an agent -- or a person -- can find out what
the framework offers without reading its source.

The design rule is progressive disclosure. The bare command stays small enough to
sit in a session's context for free; everything else is fetched only when it is
relevant. That is not an aesthetic preference: an agent re-reads its whole
context on every API round-trip, so a large always-loaded document is paid for
dozens of times per task, while a command is paid for once when used.
"""

from __future__ import annotations

import argparse
import sys

from trellum.agent import agentdoc
from trellum.cli.commands.analysis import add_analysis_commands
from trellum.cli.commands.data import _cmd_data, _cmd_query
from trellum.cli.commands.datasource import add_datasource_commands
from trellum.cli.commands.doctor import _cmd_doctor
from trellum.cli.commands.guide import BUNDLES, _cmd_checks, _cmd_guide
from trellum.cli.commands.metrics import _cmd_metrics
from trellum.cli.commands.review import (
    _cmd_review_end,
    _cmd_review_overview,
    _cmd_review_poll,
    _cmd_review_reply,
    _cmd_review_start,
    _cmd_review_status,
)
from trellum.cli.commands.serve import _cmd_serve, _ensure_background_server
from trellum.cli.commands.setup import _cmd_hook_post_edit, _cmd_setup
from trellum.cli.commands.validate import _cmd_validate
from trellum.cli.console import force_utf8_output
from trellum.cli.describe import _describe

__all__ = [
    "main",
    "force_utf8_output",
    "_describe",
    "BUNDLES",
    # Re-exported: importable from `trellum.cli` before the split.
    "_ensure_background_server",
]


def _add_review_commands(sub) -> None:
    """The `review` command family: start, poll, reply, status, end."""
    rv = sub.add_parser("review",
                        help="live review loop: the user clicks report "
                             "elements in the browser, you resolve them")
    rv.set_defaults(fn=_cmd_review_overview)
    rvsub = rv.add_subparsers(dest="verb")

    rs = rvsub.add_parser("start", help="build if needed, serve, open browser")
    rs.add_argument("report", help="reports/<slug> path or bare slug")
    rs.add_argument("--port", type=int, default=None)
    rs.add_argument("--no-browser", action="store_true",
                    help="don't open the browser (print the URL only)")
    rs.add_argument("--rebuild", action="store_true",
                    help="rebuild even when output already exists")
    rs.add_argument("--restart-server",
                    action="store_true",
                    help="replace a running server that predates review mode")
    rs.set_defaults(fn=_cmd_review_start)

    rp = rvsub.add_parser("poll", help="block until the user sends feedback")
    rp.add_argument("--timeout", type=float, default=0,
                    help="give up after SECS (default 0 = wait forever); "
                         "exit 0 feedback, 2 timeout, 3 session ended")
    rp.add_argument("--reply", metavar="TEXT",
                    help="acknowledge the previous round before waiting")
    rp.add_argument("--report", metavar="SLUG",
                    help="only this report's feedback -- lets several agents "
                         "share one project without stealing each other's "
                         "batches (slug or reports/<slug> path)")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(fn=_cmd_review_poll)

    rr = rvsub.add_parser("reply", help="answer into the browser's chat")
    rr.add_argument("text")
    rr.add_argument("--report", metavar="SLUG",
                    help="show the reply only on this report's pages")
    rr.set_defaults(fn=_cmd_review_reply)

    rst = rvsub.add_parser("status", help="one-line session state")
    rst.add_argument("--json", action="store_true")
    rst.set_defaults(fn=_cmd_review_status)

    rend = rvsub.add_parser("end", help="end the review session")
    rend.set_defaults(fn=_cmd_review_end)


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(_describe())
        return 0
    if argv[0] in ("-V", "--version"):
        print(agentdoc.version())
        return 0

    ap = argparse.ArgumentParser(prog="trellum", add_help=False)
    sub = ap.add_subparsers(dest="cmd")

    g = sub.add_parser("guide", help="topics of depth; several at once")
    g.add_argument("topic", nargs="*")
    g.set_defaults(fn=_cmd_guide)

    c = sub.add_parser("checks", help="validator check ids, or one explained")
    c.add_argument("check_id", nargs="*")
    c.add_argument("--json", action="store_true")
    c.add_argument("-v", "--verbose", action="store_true",
                   help="include each check's message")
    c.set_defaults(fn=_cmd_checks)

    v = sub.add_parser("validate", help="validation state of a built report")
    v.add_argument("report_dir")
    v.add_argument("--json", action="store_true")
    v.set_defaults(fn=_cmd_validate)

    s = sub.add_parser("setup", help="write agent pointers / install hooks / "
                                     "connect the portal")
    s.add_argument("what", choices=["project", "hooks", "portal"])
    s.add_argument("--dest", help="project directory (setup project|portal; default: cwd)")
    s.add_argument("--url", help="setup portal: the studio, <portal>/s/<org>/<studio>")
    s.add_argument("--key", help="setup portal: a personal API key, trellum_pk_...")
    s.set_defaults(fn=_cmd_setup)

    h = sub.add_parser("hook", help="internal: hook handlers")
    h.add_argument("event", choices=["post-edit"])
    h.set_defaults(fn=_cmd_hook_post_edit)

    d = sub.add_parser("doctor", help="check the agent layer is wired up")
    d.add_argument("--dest", help="project directory (default: cwd)")
    d.set_defaults(fn=_cmd_doctor)

    da = sub.add_parser("data", help="this project's sources, tables and columns")
    da.add_argument("source", nargs="?",
                    help="one source by name; also connects to remote ones, "
                         "which are otherwise listed but not opened")
    da.set_defaults(fn=_cmd_data)

    me = sub.add_parser("metrics",
                        help="business metric definitions; claim them by id "
                             "in KPIs instead of re-deriving the number")
    me.add_argument("name", nargs="?",
                    help="one metric's full definition (description, sql, "
                         "who claims it)")
    me.add_argument("--lint", action="store_true",
                    help="project-wide report: duplicates, schema problems, "
                         "orphans (defined but claimed nowhere)")
    me.add_argument("--report", action="store_true",
                    help="scaffold reports/metrics/: a monitoring page for "
                         "every metric bound to a dataset, no user code")
    me.set_defaults(fn=_cmd_metrics)

    q = sub.add_parser("query", help="ad-hoc SQL against a configured source")
    q.add_argument("sql", help="the SQL; :key placeholders bind via --param")
    q.add_argument("--source", help="source name (default: the only one configured)")
    q.add_argument("--param", action="append", metavar="KEY=VALUE",
                   help="bind a :key placeholder (repeatable)")
    q.add_argument("--max-rows", type=int, default=50,
                   help="rows to print (default 50; --csv is never capped)")
    q.add_argument("--csv", action="store_true",
                   help="emit CSV for piping instead of an aligned table")
    q.set_defaults(fn=_cmd_query)

    sv = sub.add_parser("serve",
                        help="serve built reports; --background prints the "
                             "link and returns")
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--background", action="store_true",
                    help="start detached, print the URLs, return immediately")
    sv.set_defaults(fn=_cmd_serve)

    add_datasource_commands(sub)
    add_analysis_commands(sub)
    _add_review_commands(sub)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        print(_describe())
        return 0
    return args.fn(args)
