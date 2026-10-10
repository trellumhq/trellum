"""`guide` and `checks`: the documentation the CLI serves to agents."""

from __future__ import annotations

import argparse
import json
import sys

from trellum.agent import agentdoc
from trellum.cli.commands.data import _cmd_data
from trellum.cli.commands.doctor import _cmd_doctor

#: Named bundles. Every extra command is a round-trip, and a round-trip re-reads
#: the whole context -- so fetching four topics one at a time costs four times
#: the context, not four times the content. Ask for the set you need at once.
#: Where an agent harness stops inlining tool output and writes it to a file
#: instead. Measured against ~30KB; this leaves room for the trailing notice.
_GUIDE_MAX_CHARS = 24_000

BUNDLES = {
    "all": list(agentdoc.TOPICS),
    "report": ["queries", "format", "generator", "header", "filters", "metrics",
               "validation"],
    "charts": ["components", "format", "filters", "rawhtml"],
}

def _cmd_guide(args: argparse.Namespace) -> int:
    if not args.topic:
        print("Topics (several at once is cheaper than one at a time):\n")
        for name, blurb in agentdoc.TOPICS.items():
            print(f"  {name:<12} {blurb}")
        print("\nBundles:")
        for name, members in BUNDLES.items():
            print(f"  {name:<12} {', '.join(members)}")
        print("\n  python -m trellum guide report      "
              "everything needed to write a generator, in one call")
        return 0

    wanted: list[str] = []
    for name in args.topic:
        wanted.extend(BUNDLES.get(name, [name]))

    seen: set[str] = set()
    ordered = [t for t in wanted if not (t in seen or seen.add(t))]

    # Some things are commands rather than topics, and asking for one as a topic
    # is a reasonable mistake: the description says `trellum data`, the agent
    # is already batching names into `guide`, and `guide report data` follows.
    # A measured run did exactly that and got an error for asking correctly for
    # the right thing. Answer it instead -- the intent is not ambiguous.
    aliases = {
        "data": lambda: _cmd_data(argparse.Namespace(source=None)),
        "checks": lambda: _cmd_checks(
            argparse.Namespace(check_id=[], json=False, verbose=False)),
        "doctor": lambda: _cmd_doctor(argparse.Namespace(dest=None)),
    }

    unknown = [t for t in ordered
               if agentdoc.guide(t) is None and t not in aliases]
    if unknown:
        print(f"unknown topic(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"try: {', '.join(agentdoc.TOPICS)}", file=sys.stderr)
        print(f"bundles: {', '.join(BUNDLES)}", file=sys.stderr)
        print(f"or a command: {', '.join(aliases)}", file=sys.stderr)
        return 1

    # Stop before the harness has to spill this to a file.
    #
    # Batching topics genuinely is cheaper -- each command re-reads the whole
    # context -- and this description says so, which is how a measured run came
    # to ask for eight at once. That produced ~29KB, past the point where an
    # agent harness stops inlining tool output and writes it to disk instead.
    # The run then read its own tool result back off disk, lost enough of it to
    # re-derive component signatures with inspect.signature by hand, and went
    # reading demo reports out of site-packages. Eleven calls, all downstream of
    # one response being slightly too large.
    #
    # So keep the advice and add the bound it was missing: serve what fits, then
    # say plainly what was left and how to get it.
    budget = _GUIDE_MAX_CHARS
    served, deferred = [], []
    for topic in ordered:
        if topic in aliases:
            served.append(topic)
            continue
        size = len(agentdoc.guide(topic).render())
        if served and size > budget:
            deferred.append(topic)
        else:
            budget -= size
            served.append(topic)

    for i, topic in enumerate(served):
        if i:
            print("\n" + "─" * 72 + "\n")
        if topic in aliases:
            aliases[topic]()
            continue
        print(agentdoc.guide(topic).render())
        # The prose says which component to reach for; it cannot say how to
        # call one without going stale. Append the generated signatures, so
        # asking "which component" and "with what arguments" is one round-trip
        # rather than a doc read followed by introspecting the classes by hand.
        if topic == "components":
            # Signatures only. Fifteen usage snippets were added here and
            # removed again: the run that first chose DoughnutChart correctly
            # did so on the signatures alone, before any snippet existed, so
            # they were paying ~1,400 tokens on every fetch to solve a problem
            # that was already solved.
            block = agentdoc.signatures_text()
            if block:
                print("\n" + block)
        # Same shape as the signatures block above: `metrics` is both a topic
        # (the prose contract) and a command (the live registry), and asking
        # for the topic should answer with both rather than making the reader
        # guess that a second call exists. The command form also still works
        # directly: `python -m trellum metrics`.
        if topic == "metrics":
            from trellum.cli.commands.metrics import _cmd_metrics
            print("\n### This project's metrics.yaml, read at call time\n")
            _cmd_metrics(argparse.Namespace(name=None, lint=False))

    if deferred:
        print("\n" + "─" * 72)
        print(f"{len(deferred)} topic(s) not shown, to keep this response inline: "
              f"{', '.join(deferred)}")
        print("Everything above is complete. Get the rest with:")
        print(f"  python -m trellum guide {' '.join(deferred)}")
    return 0

def _cmd_checks(args: argparse.Namespace) -> int:
    checks = agentdoc.check_ids()
    if not checks:
        print("no checks found -- validation.py is unreadable in this install",
              file=sys.stderr)
        return 1

    if args.check_id:
        wanted = [(i, lvl) for i, lvl in checks if i in args.check_id]
        missing = set(args.check_id) - {i for i, _ in wanted}
        if missing:
            print(f"unknown check id(s): {', '.join(sorted(missing))}",
                  file=sys.stderr)
            return 1
        for ident, level in wanted:
            print(f"{level}  {ident}")
            msg = agentdoc.check_message(ident)
            print(f"    {msg}\n" if msg else "    (no message recorded)\n")
        return 0

    if args.json:
        print(json.dumps([{"id": i, "level": lvl,
                           "message": agentdoc.check_message(i)}
                          for i, lvl in checks], indent=2))
        return 0

    order = {"FAIL": 0, "WARN": 1, "INFO": 2, "PASS": 3}
    for level, ident in sorted(((lvl, i) for i, lvl in checks),
                               key=lambda p: (order.get(p[0], 9), p[1])):
        print(f"  {level:<5} {ident}")
        if args.verbose:
            msg = agentdoc.check_message(ident)
            if msg:
                print(f"        {msg[:160]}")
    print(f"\n{len(checks)} checks.")
    print("  python -m trellum checks <id>        what that check means")
    print("  python -m trellum guide validation   the protocol and suppression")
    return 0
