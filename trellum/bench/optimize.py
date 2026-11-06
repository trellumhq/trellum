#!/usr/bin/env python
"""Run the bench, read the worst failure, fix one thing, measure again.

    python trellum/bench/optimize.py --enable-optimizer --worktree /path/to/worktree

The loop is deliberately dumb and the guardrails are deliberately not the
fixer's job. An agent asked to improve a score and trusted to police itself
will eventually edit the grader, and a harness that can be edited by the thing
it grades measures nothing. So: the loop owns the worktree, the whitelist, the
budget and the revert, and enforces all four after the fact rather than asking.

What the fixer may touch is the guidance surface -- the text an agent reads.
Changing how data is queried or reports are rendered to make a benchmark go up
is exactly the kind of change that needs a human on it, so those paths are
refused and the commit is thrown away.

Nothing here merges or pushes. The branch is left for review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent
_FRAMEWORK_DIR = _BENCH_DIR.parent
_REPO_DIR = _FRAMEWORK_DIR.parent

#: Paths the fixer may edit, relative to the repository root. The guidance
#: layer and its docs -- what an agent reads -- and nothing else.
WHITELIST = (
    "trellum/agent/",
    "trellum/AGENTS.md",
    "trellum/README.md",
    "trellum/docs/",
    "trellum/cli/",
    "trellum/scaffold/",
)

#: Never, whatever the whitelist says. The bench grading itself is off limits:
#: a fixer that edits the scorer optimises the number instead of the product.
DENYLIST = ("trellum/bench/",)

_FIX_PROMPT = """\
You are improving the agent-facing layer of the Trellum reporting framework, \
working in {worktree}.

A benchmark just measured a cold agent attempting a real task with this \
framework and it went badly. Here is the graded result:

{record}

The full session transcript of that attempt is at:
{transcript}

Read the transcript. Find where the agent lost time, went in circles, guessed \
at something it could have asked for, or misunderstood what the framework \
offers. Diagnose the single biggest cause.

Then make ONE targeted fix to the guidance layer so the next agent does not \
hit it. You may only edit these paths:

{whitelist}

You may NOT edit the benchmark itself ({denylist}) -- that is the measuring \
device, and changing it does not improve the product.

Good fixes look like: a missing `trellum guide` topic, a pointer file that \
does not mention something load-bearing, an error message that does not say \
what to do next, a command whose help text misleads. If the real cause is in \
data or rendering logic, do NOT fix it -- say so in your commit message and \
make the smallest guidance change that warns the agent instead.

Commit your change with `[bench-auto]` as the first token of the commit \
message, followed by one line saying what you changed and why. Do not push. \
Do not merge. Do not run the benchmark.
"""


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return out.stdout if "-z" in args else out.stdout.strip()


def _ensure_worktree(path: Path | None, *, require_clean: bool = True) -> Path:
    """Validate an explicitly prepared, disposable checkout; never create one."""
    if path is None:
        raise SystemExit("Pass --worktree with a dedicated bench/auto-* worktree.")
    path = path.expanduser().resolve()
    try:
        root = Path(_git("rev-parse", "--show-toplevel", cwd=path)).resolve()
        common = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir",
                           cwd=path)).resolve()
        expected = Path(_git("rev-parse", "--path-format=absolute", "--git-common-dir",
                             cwd=_REPO_DIR)).resolve()
        branch = _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=path)
        if (root != path or path == _REPO_DIR.resolve() or common != expected
                or not (path / ".git").is_file()
                or not branch.startswith("bench/auto-")):
            raise SystemExit("Refusing target: use a linked worktree of this repository "
                             "on a dedicated bench/auto-* branch.")
        # Ignored files count too: cleanup must never inherit someone else's data.
        if require_clean and _git("status", "--porcelain", "--untracked-files=all",
                                  "--ignored", cwd=path):
            raise SystemExit("Refusing target: worktree must be completely clean "
                             "(including untracked and ignored files).")
    except (subprocess.SubprocessError, OSError) as exc:
        raise SystemExit(f"Cannot verify optimizer worktree: {exc}") from exc
    return path


def _discard_fix(worktree: Path, before_sha: str, *, expected_branch: str) -> None:
    """Recheck checkout identity before discarding only this attempt's changes."""
    _ensure_worktree(worktree, require_clean=False)
    if _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=worktree) != expected_branch:
        raise SystemExit("Refusing cleanup: optimizer branch changed during the attempt.")
    _git("reset", "--hard", before_sha, cwd=worktree)


def _verify_harness(worktree: Path) -> None:
    """Do not delegate permission handling to an older or modified harness."""
    for name in ("agent.py", "run.py"):
        try:
            matches = (worktree / "trellum" / "bench" / name).read_bytes() == (
                _BENCH_DIR / name).read_bytes()
        except OSError as exc:
            raise SystemExit(f"Cannot verify benchmark harness: {exc}") from exc
        if not matches:
            raise SystemExit(f"Refusing target: trellum/bench/{name} must match "
                             "the optimizer's benchmark harness.")


def _run_matrix(worktree: Path, *, scenarios: str, guidance: str,
                repeat: int, label: str, model: str, py: str) -> list[dict]:
    """Run the bench inside *worktree* and return this batch's records.

    The worktree's own copy of run.py is used, so whatever the fixer changed
    is what gets measured.
    """
    _verify_harness(worktree)
    results = worktree / "trellum" / "bench" / "results" / "results.jsonl"
    before = results.stat().st_size if results.is_file() else 0

    subprocess.run(
        [py, str(worktree / "trellum" / "bench" / "run.py"),
         "--scenarios", scenarios, "--guidance", guidance,
         "--repeat", str(repeat), "--label", label, "--model", model],
        cwd=str(worktree), timeout=None,
    )

    if not results.is_file():
        return []
    with results.open("r", encoding="utf-8") as fh:
        fh.seek(before)
        return [json.loads(line) for line in fh if line.strip()]


def _turns(record: dict) -> int:
    value = (record.get("claude") or {}).get("num_turns")
    return value if isinstance(value, int) else 0


def _target_turns(scenario_id: str) -> int | None:
    import scenarios as scen_mod

    scenario = scen_mod.SCENARIOS.get(scenario_id)
    return getattr(scenario, "target_turns", None) if scenario else None


def _below_par(record: dict) -> bool:
    """Is this run worth spending a fix on?

    Not just "did it fail". The resolver bug scored 100/100 before and after
    and cost twice as much before -- an optimizer that only chases failures is
    blind to the single largest improvement this project has measured. A
    correct run that costs well over its measured target is a finding too.
    """
    if record.get("verdict") != "pass":
        return True
    target = _target_turns(record.get("scenario", ""))
    if not target:
        return False
    # 25% over target, so ordinary run-to-run variance is not a "regression".
    return _turns(record) > target * 1.25


def _quality(record: dict) -> tuple[int, int]:
    """Higher is better. Score first, then fewer turns."""
    return (record.get("score", 0), -_turns(record))


def _cost(records: list[dict]) -> float:
    total = 0.0
    for r in records:
        c = (r.get("claude") or {}).get("total_cost_usd")
        if isinstance(c, (int, float)):
            total += c
    return total


def path_allowed(path: str) -> bool:
    """May the fixer have touched this path?

    Deny beats allow: bench/ sits under no whitelist entry today, but the
    ordering is what stops a future whitelist widening from silently handing
    the fixer its own grader.
    """
    if any(path.startswith(d) for d in DENYLIST):
        return False
    return any(path.startswith(w) if w.endswith("/") else path == w for w in WHITELIST)


def _violations(worktree: Path, before_sha: str) -> list[str]:
    """Paths the fixer touched that it had no business touching."""
    # Disable rename collapsing so both the removed and added paths are checked.
    # NUL delimiters preserve whitespace, newlines and Git-quoted filenames.
    changed = _git("diff", "--no-renames", "--name-only", "-z",
                   f"{before_sha}..HEAD", cwd=worktree).split("\0")
    dirty = [
        entry[3:]
        for entry in _git("status", "--porcelain", "--no-renames", "-z",
                          "--untracked-files=all", cwd=worktree).split("\0") if entry
    ]
    return sorted(
        p for p in {*changed, *dirty} if p and not path_allowed(p)
    )


def _run_fixer(worktree: Path, record: dict, model: str, timeout_s: int) -> float:
    """One fix attempt. Returns its cost in USD."""
    transcript = record.get("transcript") or "(none captured)"
    if record.get("transcript"):
        transcript = str(worktree / "trellum" / "bench" / record["transcript"])

    trimmed = {k: v for k, v in record.items() if k not in ("build_tail",)}
    prompt = _FIX_PROMPT.format(
        worktree=worktree,
        record=json.dumps(trimmed, indent=2, ensure_ascii=False),
        transcript=transcript,
        whitelist="\n".join(f"  {p}" for p in WHITELIST),
        denylist=", ".join(DENYLIST),
    )

    import agent as agent_mod  # same stdin-not-argv handling as the bench

    proc = subprocess.Popen(
        [agent_mod._claude_bin(), "-p", "--output-format", "json",
         "--max-turns", "80", "--permission-mode", "default",
         "--model", model],
        cwd=str(worktree), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    try:
        stdout, _ = proc.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return 0.0
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return 0.0
    cost = payload.get("total_cost_usd")
    return cost if isinstance(cost, (int, float)) else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--enable-optimizer", action="store_true",
                        help="Explicitly allow provider calls and automatic commits/reverts.")
    parser.add_argument("--worktree", type=Path, required=True,
                        help="Clean linked worktree on a dedicated bench/auto-* branch.")
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--guidance", default="pointer")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default="sonnet",
                        help="Model for the agent under test.")
    parser.add_argument("--fix-model", default="opus",
                        help="Model for the fixer. Fix quality gates the whole "
                             "loop and it runs a handful of times a night.")
    parser.add_argument("--max-iters", type=int, default=6)
    parser.add_argument("--budget-usd", type=float, default=10.0)
    parser.add_argument("--max-hours", type=float, default=8.0)
    parser.add_argument("--fix-timeout-s", type=int, default=1800)
    args = parser.parse_args()

    if not args.enable_optimizer:
        parser.error("--enable-optimizer is required: this loop makes provider calls "
                     "and may discard fixes in the target worktree")
    worktree = _ensure_worktree(args.worktree)
    _verify_harness(worktree)
    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=worktree)
    py = sys.executable
    started = time.time()
    spent = 0.0
    stale = 0

    for iteration in range(1, args.max_iters + 1):
        elapsed_h = (time.time() - started) / 3600
        if spent >= args.budget_usd:
            print(f"\nstop: budget spent (${spent:.2f} >= ${args.budget_usd:.2f})")
            break
        if elapsed_h >= args.max_hours:
            print(f"\nstop: wall clock ({elapsed_h:.1f}h)")
            break

        print(f"\n########## iteration {iteration} "
              f"(${spent:.2f} spent, {elapsed_h:.1f}h) ##########", flush=True)

        records = _run_matrix(
            worktree, scenarios=args.scenarios, guidance=args.guidance,
            repeat=args.repeat, label=f"auto-{iteration}", model=args.model, py=py,
        )
        spent += _cost(records)
        if not records:
            print("stop: the bench produced no records -- harness problem.")
            break

        needs_work = [r for r in records if _below_par(r)]
        if not needs_work:
            print("\nstop: everything passes, and nothing costs more than its "
                  "target.")
            break

        target = min(needs_work, key=_quality)
        print(f"\ntarget: {target['scenario']} ({target['verdict']}, score "
              f"{target['score']}, {_turns(target)} turns vs target "
              f"{_target_turns(target['scenario'])})", flush=True)

        before_sha = _git("rev-parse", "HEAD", cwd=worktree)
        spent += _run_fixer(worktree, target, args.fix_model, args.fix_timeout_s)
        after_sha = _git("rev-parse", "HEAD", cwd=worktree)

        if after_sha == before_sha:
            print("no commit from the fixer; discarding any working-tree changes.")
            _discard_fix(worktree, before_sha, expected_branch=branch)
            stale += 1
            if stale >= 2:
                print("\nstop: two iterations without a usable fix.")
                break
            continue

        bad = _violations(worktree, before_sha)
        if bad:
            print(f"fix touched paths outside the whitelist: {', '.join(bad)}\n"
                  f"reverting.", flush=True)
            _discard_fix(worktree, before_sha, expected_branch=branch)
            stale += 1
            if stale >= 2:
                print("\nstop: two iterations without a usable fix.")
                break
            continue

        print(f"fix committed: {_git('log', '-1', '--oneline', cwd=worktree)}")

        # Re-measure the one scenario the fix was aimed at.
        recheck = _run_matrix(
            worktree, scenarios=target["scenario"], guidance=args.guidance,
            repeat=args.repeat, label=f"auto-{iteration}-verify",
            model=args.model, py=py,
        )
        spent += _cost(recheck)
        best_record = max(recheck, key=_quality) if recheck else None
        best = _quality(best_record) if best_record else (0, 0)

        if best <= _quality(target):
            print(f"no improvement (score {target['score']}->"
                  f"{best_record['score'] if best_record else '?'}, turns "
                  f"{_turns(target)}->{-best[1]}); reverting the fix.",
                  flush=True)
            _discard_fix(worktree, before_sha, expected_branch=branch)
            stale += 1
            if stale >= 2:
                print("\nstop: two consecutive non-improvements.")
                break
        else:
            print(f"kept: score {target['score']}->{best_record['score']}, "
                  f"turns {_turns(target)}->{-best[1]}")
            stale = 0

    print(f"\ntotal spent: ${spent:.2f}")
    print(f"branch {_git('rev-parse', '--abbrev-ref', 'HEAD', cwd=worktree)} "
          f"in {worktree} -- not merged, not pushed.")
    print(_git("log", "--oneline", "-10", cwd=worktree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
