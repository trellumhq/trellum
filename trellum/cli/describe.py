"""What the framework says about itself when asked with no arguments."""

from __future__ import annotations

from trellum import update_check
from trellum.agent import agentdoc


def _describe() -> str:
    checks = agentdoc.check_ids()
    fails = sum(1 for _, lvl in checks if lvl == "FAIL")
    comps = agentdoc.components()
    lines = [
        f"Trellum {agentdoc.version()}{update_check.doctor_detail()} -- explore data, build interactive reports "
        "and dashboards, write analyses.",
        "Use databases, spreadsheets, files or APIs. Reports build to HTML + JSON.",
        "Local authoring and browser review work without the optional Trellum portal.",
        "Portal viewers: ask questions and read permitted reports/data through available tools.",
        "For portal users, creating/editing/building/publishing needs developer/admin access.",
        "For portal role, key-scope and action requirements: `python -m trellum guide portal`.",
        "",
        # The routing decision comes before anything else on this page: most
        # asks want a number, and a number is one call, not a report.
        "Choose the result the user needs:",
        "  A NUMBER or answer (\"what was revenue yesterday?\") -> no report.",
        "    Check `python -m trellum metrics` for a defined metric, use its SQL,",
        "    query the configured source BY NAME (local dbs open read-only):",
        "      python -m trellum query \"SELECT ...\"",
        "      from trellum import query; query(\"<source>\", \"SELECT ...\")",
        "    Reply with the number, the source name, the as-of date, the SQL.",
        "  A REPORT or DASHBOARD -> reports/<slug>/; use Trellum components.",
        "  An ARTICLE or PRESENTATION -> `python -m trellum guide analysis`.",
        "    Use content.md or slides.yaml with captured evidence; no generator.",
        "Read context/ and analysis/ notes; retain verified source and metric knowledge.",
        "",
        "A report is a directory under reports/<slug>/ containing:",
        "  report.yaml    identity, schedule, theme, data_sources",
        "  queries.py     parameterised SQL when using a database source",
        "  generator.py   a BaseReport subclass whose generate(ctx) adds sections",
        "Articles/presentations: report.yaml kind: analysis + authored text + evidence/.",
        "  python -m trellum analysis new <slug> [--format presentation]",
        "  python -m trellum analysis session reports/<slug>    capture and review",
        "",
        "Build (exits when done -- use this one):",
        "  python -m trellum.run reports/<slug> --no-serve",
        "  python -m trellum.new <slug>              scaffold a new one",
        "  python -m trellum.demo                    install the demo project",
        "",
        "Without --no-serve, trellum.run BLOCKS on a foreground server.",
        "For the browser handoff, use review start below; see `trellum guide review`.",
        "",
        "THE TWO RULES THAT ARE NOT GUESSABLE FROM THE API:",
        "  1. Aggregate in the generator with pandas, NOT in SQL. Queries return",
        "     rows at native grain; groupby/pivot happens in generate().",
        "  2. Long format, never wide. Dimensions stay in ROWS, one column per",
        "     metric -- `platform` as a column of values, not one column per",
        "     platform. Wide data builds cleanly and filters silently do nothing.",
        "  Full versions: python -m trellum guide queries format"
        " -- and `guide upgrade` after a framework upgrade",
        "",
        # Names only. The selection table and the signatures were moved here in
        # 42b159c on the theory that fetching them was a decision an agent might
        # not make, and moved back out when that was measured: `trellum guide`
        # is fetched in every recorded run, at call 1 or 2, so on-demand content
        # was already being reached. The block cost 918 tokens per exchange and
        # changed the call count by nothing outside its existing 12-16 spread.
        #
        # What the runs actually spend calls on is project state and a worked
        # example -- neither of which is reference material, and neither of
        # which belongs in a static list.
        f"Components ({len(comps)}):",
        "  " + ", ".join(comps),
        "",
        # The schema is never printed here and never written to disk -- only
        # ever read at the moment it is asked for, so it cannot go stale.
        "Your data (run this before writing any SQL -- do not guess columns):",
        "  python -m trellum data                   sources, tables, columns",
        "  python -m trellum datasource add <name> --type <t> --host <h> ...",
        "                                           writes config.yaml + .env and",
        "                                             connects -- never hand-edit",
        "                                             those two files yourself",
        "  python -m trellum query \"SELECT ...\"     ad-hoc SQL by source name --",
        "                                             never hunt for the db file",
        "  python -m trellum metrics                defined business metrics --",
        "                                             claim by id, don't re-derive",
        "",
        "Creating/updating a report for a person includes opening it automatically:",
        "  python -m trellum review start reports/<slug>   builds, serves, opens",
        "  open the printed URL in the user's browser, verify it, and share the link",
        "  python -m trellum review poll --report <slug>   keep listening",
        "  edit the report, then rebuild with --no-serve before replying",
        "  reply with the feedback session returned by the poll, then keep listening",
        "  repeat until the user ends review; use `review end` if asked to wrap up",
        "Explicit build-only/source-only work, CI and unattended publication stop at",
        "--no-serve; honor no-browser requests. Use plain `serve` only when",
        "someone explicitly asks for a preview without review feedback.",
        "",
        f"Themes ({len(agentdoc.themes())}):",
        "  " + ", ".join(agentdoc.themes()),
        "",
        f"Validator: {len(checks)} checks ({fails} of them fatal). A report is not "
        f"done until every FAIL is fixed.",
        "  python -m trellum checks                 list them",
        "  python -m trellum checks <id>            what one means, verbatim",
        "",
        "Ask for relevant guide topics together. Larger responses name deferred topics:",
        "  python -m trellum guide report           report-building topics",
        "  python -m trellum guide <a> <b> <c>      any combination",
        "  topics: " + ", ".join(agentdoc.TOPICS),
    ]
    missing = agentdoc.missing_topics()
    if missing:
        lines += ["", f"WARNING: unavailable topics in this install: "
                      f"{', '.join(missing)}"]
    return "\n".join(lines)
