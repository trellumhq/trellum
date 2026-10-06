"""`doctor`: is the agent layer actually reaching an agent?"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trellum import update_check
from trellum.agent import agentdoc


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Check that the agent layer is actually reaching an agent.

    Every link below has broken at least once in practice, and the failures
    share a shape: nothing errors. A hook whose interpreter no longer exists,
    an anchor deleted during a prose edit, a pointer file a project never had
    -- each of these leaves an agent working without the guidance while
    everything looks installed. The whole layer is invisible when it works, so
    it is equally invisible when it does not.

    Exit status is 1 if anything FAILed, so this can gate a setup script.
    """
    from trellum.agent import agentsetup

    root = Path(args.dest or Path.cwd()).resolve()
    rows: list[tuple[str, str, str]] = []          # (level, check, detail)

    def ok(check, detail=""):
        rows.append(("ok", check, detail))

    def warn(check, detail):
        rows.append(("WARN", check, detail))

    def fail(check, detail):
        rows.append(("FAIL", check, detail))

    ok("cli reachable", f"{sys.executable} -m trellum")
    ok("version", agentdoc.version() + update_check.doctor_detail())

    # Pointer files. Absent is a FAIL rather than a WARN: without one, an agent
    # whose harness reads neither our hook nor this file has nothing at all.
    present = [n for n in ("CLAUDE.md", "AGENTS.md") if (root / n).is_file()]
    pointed = [n for n in present
               if "-m trellum" in (root / n).read_text(encoding="utf-8",
                                                         errors="replace")]
    if not present:
        fail("pointer files", f"neither CLAUDE.md nor AGENTS.md in {root}; "
                              f"run `setup project`")
    elif not pointed:
        warn("pointer files", f"{', '.join(present)} exist but do not mention "
                              f"the framework -- they are the project's own")
    else:
        ok("pointer files", ", ".join(pointed))

    # Hooks. This is the only path that works for a harness which reads no
    # convention file at all, so a missing hook is a FAIL too.
    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        fail("hooks configured", f"no {settings}; run `setup project`")
    else:
        try:
            conf = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            conf = None
            fail("hooks configured", f"{settings} is unreadable: {exc}")
        if conf is not None:
            hooks = (conf.get("hooks") or {})
            missing = [e for e in ("SessionStart", "PostToolUse")
                       if not any("-m trellum" in h.get("command", "")
                                  for g in (hooks.get(e) or [])
                                  for h in (g.get("hooks") or []))]
            if missing:
                fail("hooks configured", f"no framework hook for "
                                         f"{', '.join(missing)}")
            else:
                ok("hooks configured", "SessionStart, PostToolUse")

            # The interpreter a hook names is a path recorded at setup time. It
            # goes stale silently whenever the virtualenv is rebuilt somewhere
            # else, and the only symptom is a hook that stops firing.
            cmd = agentsetup.hook_config(root)["SessionStart"][0]["hooks"][0]["command"]
            exe = Path(cmd.split()[0])
            resolved = exe if exe.is_absolute() else (root / exe)
            if resolved.exists():
                ok("hook interpreter", str(exe))
            else:
                fail("hook interpreter", f"{exe} does not exist relative to {root}")

    good, detail = agentsetup.verify_hook(root)
    (ok if good else fail)("hook runs", detail if good else detail)

    _content_checks(root, ok, warn, fail)

    width = max(len(c) for _, c, _ in rows)
    print(f"trellum doctor -- {root}\n")
    for level, check, detail in rows:
        mark = {"ok": "  ok  ", "WARN": " WARN ", "FAIL": " FAIL "}[level]
        print(f"[{mark}] {check:<{width}}  {detail}")

    bad = [r for r in rows if r[0] == "FAIL"]
    warned = [r for r in rows if r[0] == "WARN"]
    print()
    if bad:
        print(f"{len(bad)} problem(s). An agent in this project is working "
              f"without part of the guidance,")
        print("and nothing about that would have surfaced on its own.")
    elif warned:
        print(f"wired up, with {len(warned)} thing(s) worth a look.")
    else:
        print("wired up: the description reaches a session, and everything it "
              "points at resolves.")
    return 1 if bad else 0


def _content_checks(root: Path, ok, warn, fail) -> None:
    """Content the CLI advertises but resolves at runtime -- an anchor removed
    in an unrelated prose edit turns `guide <topic>` into silence, not an
    error, and a half-wired portal entry just never lists its tools."""
    from trellum.agent import agentsetup

    missing_topics = agentdoc.missing_topics()
    if missing_topics:
        fail("guide topics", f"no anchored section for: "
                             f"{', '.join(missing_topics)}")
    else:
        ok("guide topics", f"{len(agentdoc.TOPICS)} resolve")

    # The portal is optional, so none is fine. Present and half-wired is the
    # silent kind of broken: the harness just never lists the server's tools.
    problems = agentsetup.portal_problems(root)
    if problems is None:
        ok("portal server", "none in .mcp.json (`setup portal` connects one)")
    elif problems:
        warn("portal server", "; ".join(problems))
    else:
        ok("portal server", agentsetup.portal_server(root)["url"])

    comps = agentdoc.components()
    sigs = {n for n, _, _ in agentdoc.signatures()}
    unsigned = [c for c in comps if c not in sigs]
    if unsigned:
        warn("component signatures", f"not introspectable: "
                                     f"{', '.join(unsigned[:6])}")
    else:
        ok("component signatures", f"{len(sigs)} components")

    checks = agentdoc.check_ids()
    (ok if checks else fail)("validator checks",
                             f"{len(checks)} ids" if checks
                             else "none found -- validation.py unreadable")
