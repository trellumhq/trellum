"""`setup` and the PostToolUse hook that keeps a project wired up."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _cmd_setup(args: argparse.Namespace) -> int:
    from trellum.agent import agentsetup
    if args.what == "portal":
        return _setup_portal(args)
    if args.what == "project":
        root = Path(args.dest or ".").resolve()
        print(f"Writing agent pointers into {root}\n")
        for name, status in agentsetup.write_project_files(root):
            print(f"  {status:<10} {name}")

        # Writing a hook without running it is how a broken one ends up firing
        # on every session start.
        ok, detail = agentsetup.verify_hook(root)
        if ok:
            print(f"\n  verified   {detail}")
        else:
            print(f"\n  WARNING: the session hook does not run: {detail}",
                  file=sys.stderr)
            print("  Sessions will show an error banner. Remove the SessionStart "
                  "entry from\n  .claude/settings.json, or re-run this from the "
                  "interpreter that has the\n  framework installed.", file=sys.stderr)
        print("\nThese travel with the repository. Commit them.")
        return 0 if ok else 1

    print("Installing session hooks (machine-wide)\n")
    rows = agentsetup.install_session_hooks()
    for name, status in rows:
        print(f"  {status:<10} {name}")
    print("\nRestart your agent session for the hooks to take effect.")
    print("Note these are invisible from any repository -- `trellum setup "
          "project` is the version that travels with your code.")
    return 0 if all(s != "unreadable" for _, s in rows) else 1

def _setup_portal(args: argparse.Namespace) -> int:
    """Wire the repository to its studio: our server entry in both MCP files,
    the key in .env, the Portal section in the pointers. Then verify .env is
    ignored -- the one file the key is in must be the one git never sees."""
    from trellum.agent import agentsetup
    from trellum.cli.commands.datasource import _update_env_file

    if not (args.url and args.key):
        print("setup portal needs --url <portal>/s/<org>/<studio> and --key trellum_pk_...",
              file=sys.stderr)
        return 2
    root = Path(args.dest or ".").resolve()
    print(f"Connecting {root} to the portal\n")
    rows = agentsetup.write_mcp_files(root, args.url)
    env = root / ".env"
    before = env.read_text(encoding="utf-8") if env.is_file() else None
    _update_env_file(str(env), {agentsetup.MCP_ENV_VAR: args.key})
    after = env.read_text(encoding="utf-8")
    rows.append((".env", "created" if before is None else
                 "current" if before == after else "updated"))
    rows += agentsetup.write_project_files(root)      # the pointers' Portal section
    for name, status in rows:
        print(f"  {status:<10} {name}")

    ignored = agentsetup.env_ignored(root)
    if ignored is False:
        sys.stdout.flush()   # keep the rows above the warning when redirected
        print(f"\n  WARNING: .env now holds {agentsetup.MCP_ENV_VAR}, and git does not "
              "ignore it.\n  Add a line `.env` to .gitignore before committing anything.",
              file=sys.stderr)
        return 1
    print(f"\n  verified   .env is {'ignored by git' if ignored else 'outside a git checkout'}")
    print("\nThe MCP files and the pointers travel with the repository; .env stays here.")
    return 0


def _cmd_hook_post_edit(args: argparse.Namespace) -> int:  # noqa: ARG001
    """PostToolUse handler: hand back validator state after a report file changes.

    Silent unless it has something current and useful to say. Three rules, each
    of which exists because the alternative is noise:

    1. Only inside a report folder -- a matcher matches tool names, not paths,
       so the filter has to live here. Walk up looking for report.yaml.
    2. Only when the validation output is NEWER than the edited file. Mid-edit,
       before a build, there is no state worth reporting, and a "possibly
       stale" hedge is worse than saying nothing.
    3. Only when something is actually wrong.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    edited = (payload.get("tool_input") or {}).get("file_path")
    if not edited:
        return 0
    edited_path = Path(edited)
    if not edited_path.exists():
        return 0

    report_dir = next((p for p in [edited_path.parent, *edited_path.parents]
                       if (p / "report.yaml").is_file()), None)
    if report_dir is None:
        return 0

    project = report_dir.parent.parent
    validation = project / "output" / report_dir.name / "_validation.json"
    if not validation.is_file():
        return 0
    try:
        if validation.stat().st_mtime < edited_path.stat().st_mtime:
            return 0
        checks = json.loads(validation.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    if isinstance(checks, dict):
        checks = checks.get("checks", [])
    bad = [c for c in checks
           if str(c.get("level", "")).lower() in ("fail", "warn")
           and not c.get("suppressed")]
    if not bad:
        return 0

    fails = [c for c in bad if str(c.get("level", "")).lower() == "fail"]
    lines = [f"Validator state for {report_dir.name} as of the last build: "
             f"{len(fails)} FAIL, {len(bad) - len(fails)} WARN."]
    for c in bad[:8]:
        where = c.get("component") or c.get("dataset_id") or ""
        lines.append(f"  {str(c.get('level','?')).upper():<5} {c.get('id','?')}"
                     + (f"  ({where})" if where else ""))
    if len(bad) > 8:
        lines.append(f"  ... and {len(bad) - 8} more")
    lines.append("Rebuild after editing. `trellum guide validation` explains "
                 "the protocol and how to suppress a WARN with a rationale.")

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines),
    }}))
    return 0
