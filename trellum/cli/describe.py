"""What the framework says about itself when asked with no arguments."""

from __future__ import annotations

from trellum.agent import agentdoc


def _describe() -> str:
    checks = agentdoc.check_ids()
    fails = sum(1 for _, lvl in checks if lvl == "FAIL")
    comps = agentdoc.components()
    lines = [
        f"trellum {agentdoc.version()} -- build themed, interactive reports as "
        f"self-contained HTML + JSON.",
        "",
        # The routing decision comes before anything else on this page: most
        # asks want a number, and a number is one call, not a report.
        "A user's ask has two shapes. Route by it:",
        "  A NUMBER or answer (\"what was revenue yesterday?\") -> no report.",
        "    Check `python -m trellum metrics` for a defined metric, use its SQL,",
        "    query the configured source BY NAME (local dbs open read-only):",
        "      python -m trellum query \"SELECT ...\"",
        "      from trellum import query; query(\"<source>\", \"SELECT ...\")",
        "    Reply with the number, the source name, the as-of date, the SQL.",
        "  A REPORT (a schedule, readers beyond the asker, interactive filters)",
        "    -> reports/<slug>/, below. Unsure? Answer first, promote later:",
        "    python -m trellum guide answer",
        "",
        "A report is a directory under reports/<slug>/ containing:",
        "  report.yaml    identity, schedule, theme, data_sources",
        "  queries.py     SQL, parameterised",
        "  generator.py   a BaseReport subclass whose generate(ctx) adds sections",
        "Articles: report.yaml kind: analysis + content.md + evidence/; builds offline.",
        "  trellum analysis new <slug>                 scaffold an article",
        "  trellum analysis import <report-dir> <capture-file> [--name <safe-name>]",
        "",
        "Build (exits when done -- use this one):",
        "  python -m trellum.run reports/<slug> --no-serve",
        "  python -m trellum.new <slug>              scaffold a new one",
        "  python -m trellum.demo                    install the demo project",
        "",
        "Serve, only when a human is going to look (BLOCKS until killed):",
        "  python -m trellum.run reports/<slug>      one report, served at /",
        "  python -m trellum.run --all --serve       every report, index at /",
        "  A single report is at / -- NOT at /<slug>, which 404s. Run it in the",
        "  background, then wait for its 'Serving at' line before opening the",
        "  URL: it rebuilds before it binds.",
        "",
        "THE TWO RULES THAT ARE NOT GUESSABLE FROM THE API:",
        "  1. Aggregate in the generator with pandas, NOT in SQL. Queries return",
        "     rows at native grain; groupby/pivot happens in generate().",
        "  2. Long format, never wide. Dimensions stay in ROWS, one column per",
        "     metric -- `platform` as a column of values, not one column per",
        "     platform. Wide data builds cleanly and filters silently do nothing.",
        "  Full versions: python -m trellum guide queries format",
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
        "When the work is verified and you are about to tell the user it is",
        "done, the LAST action is to hand them a clickable link:",
        "  python -m trellum serve --background     serves output/, prints the",
        "                                             URL, returns immediately",
        "",
        "Live review loop -- the user clicks elements in the browser, types",
        "change requests, and sends them back to you:",
        "  python -m trellum review start reports/<slug>   build+serve+open",
        "  python -m trellum review poll             BLOCKS until they hit Send;",
        "                                              then edit, rebuild, --reply",
        "",
        f"Themes ({len(agentdoc.themes())}):",
        "  " + ", ".join(agentdoc.themes()),
        "",
        f"Validator: {len(checks)} checks ({fails} of them fatal). A report is not "
        f"done until every FAIL is fixed.",
        "  python -m trellum checks                 list them",
        "  python -m trellum checks <id>            what one means, verbatim",
        "",
        "Depth on demand. Ask for several at once -- each command re-reads your",
        "whole context, so one call for four topics beats four calls. Anything",
        "over ~24KB is held back and named, so ask freely; nothing is truncated:",
        "  python -m trellum guide report           the whole authoring contract",
        "  python -m trellum guide <a> <b> <c>      any combination",
        "  topics: " + ", ".join(agentdoc.TOPICS),
    ]
    missing = agentdoc.missing_topics()
    if missing:
        lines += ["", f"WARNING: unavailable topics in this install: "
                      f"{', '.join(missing)}"]
    return "\n".join(lines)
