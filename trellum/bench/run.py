#!/usr/bin/env python
"""The agent bench: how well does a cold agent do with this framework?

    python trellum/bench/run.py --scenarios sqlite-first-report --repeat 3
    python trellum/bench/run.py --scenarios sqlite-first-report --guidance pointer,skills
    python trellum/bench/run.py --scenarios pg-first-report --label after-guide-topic
    python trellum/bench/run.py --compare

One line of results.jsonl per run. Containers come up for the scenarios that
need them and go down afterwards, reusing itest's compose file and project
name so a `--keep` here and an `itest/run.py --keep` there are the same
containers rather than two competing sets.

Internal tooling: never added to pyproject's packages list, so it stays out of
the wheel exactly like itest/.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import grade as grade_mod
from scenarios import ENGINES, SCENARIOS, prompt_for, seed

import agent as agent_mod

_BENCH_DIR = Path(__file__).resolve().parent
_FRAMEWORK_DIR = _BENCH_DIR.parent
_RESULTS = _BENCH_DIR / "results" / "results.jsonl"


def _itest_run():
    """Borrow itest/run.py's compose helpers rather than restating them.

    Loaded by path under an explicit module name: a plain `import run` would
    collide with this file.
    """
    path = _FRAMEWORK_DIR / "itest" / "run.py"
    spec = importlib.util.spec_from_file_location("itest_run", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(_FRAMEWORK_DIR), *args],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(record: dict) -> None:
    _RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with _RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_one(scenario, *, guidance: str, label: str, model: str,
             py: str, keep: bool, claude_version: str) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{scenario.id}-{guidance}"
    print(f"\n=== {run_id} ===", flush=True)

    scratch = agent_mod.make_scratch(run_id, py, scenario.preexisting)
    if guidance == "skills":
        agent_mod.install_skill(scratch, py)
    seed(scenario, scratch)

    prompt = prompt_for(scenario, scratch)
    started = _now()
    result = agent_mod.run_agent(
        prompt, scratch, run_id,
        model=model, max_turns=scenario.max_turns, timeout_s=scenario.timeout_s,
    )
    graded = grade_mod.grade(
        scenario, scratch, result, py=py, env=agent_mod.child_env(),
    )

    record = {
        "v": 1,
        "run_id": run_id,
        "label": label,
        "scenario": scenario.id,
        "engine": scenario.engine,
        "guidance": guidance,
        "cred_mode": scenario.cred_mode,
        "expected_outcome": scenario.expected_outcome,
        "fw_sha": _git("rev-parse", "--short", "HEAD"),
        "fw_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "fw_dirty": bool(_git("status", "--porcelain")),
        "started": started,
        "ended": _now(),
        "wall_s": result["wall_s"],
        "claude": {
            "version": claude_version,
            "model": result["model"],
            "session_id": result["session_id"],
            "num_turns": result["num_turns"],
            "total_cost_usd": result["total_cost_usd"],
            "is_error": result["is_error"],
            "timed_out": result["timed_out"],
            "stop_subtype": result["stop_subtype"],
            "hit_turn_cap": result["hit_turn_cap"],
        },
        # The graded evidence for diagnose lanes. Kept because its absence
        # made a wrong grade genuinely hard to explain after the fact.
        "result_text": (result["result_text"] or "")[:4000],
        "checks": graded["checks"],
        "score": graded["score"],
        "verdict": graded["verdict"],
        "error": result["stderr_tail"] or None,
        "no_result_json": result["no_result_json"],
        "build_tail": graded["build_tail"] or None,
        "transcript": result["transcript"],
        "scratch": None,
    }

    if graded["verdict"] == "pass" and not keep:
        shutil.rmtree(scratch, ignore_errors=True)
    else:
        record["scratch"] = str(scratch.relative_to(_BENCH_DIR))

    _append(record)
    cost = result["total_cost_usd"]
    print(
        f"  {graded['verdict'].upper():<8} score={graded['score']:<4} "
        f"wall={result['wall_s']}s turns={result['num_turns']} "
        f"cost=${cost if cost is not None else '?'}",
        flush=True,
    )
    if result["hit_turn_cap"]:
        # Not necessarily a failure -- a report can already be built and valid
        # when the cap bites -- but the run stopped for a reason that has
        # nothing to do with the framework, so it is never a clean datum.
        print(f"  ! stopped at the {scenario.max_turns}-turn cap "
              f"({result['stop_subtype'] or 'inferred'}) -- raise it or read "
              f"the transcript before trusting this score.", flush=True)
    if result["no_result_json"]:
        # Distinguish "the agent did badly" from "the harness never got an
        # agent". Scoring the second as a DX failure would quietly poison
        # every comparison built on it.
        print("  ! no result JSON from the CLI -- harness problem, not a "
              "measurement. Check `error` in the record.", flush=True)
    return record


def _batch(group, records: list, *, guidances, repeat, label, model, py, keep,
           claude_version) -> None:
    """Run one group of scenarios, appending every outcome to *records*."""
    for _ in range(repeat):
        for scenario in group:
            for guidance in guidances:
                try:
                    records.append(_run_one(
                        scenario, guidance=guidance, label=label, model=model,
                        py=py, keep=keep, claude_version=claude_version,
                    ))
                except Exception as exc:  # noqa: BLE001 -- any setup failure
                    # Scaffolding or seeding blew up: this lane produced no
                    # measurement. Record it as a harness fault and carry on --
                    # an overnight batch should not lose every remaining
                    # scenario because one engine was unreachable.
                    print(f"  HARNESS ERROR  {type(exc).__name__}: {exc}",
                          flush=True)
                    record = {
                        "v": 1, "label": label, "scenario": scenario.id,
                        "engine": scenario.engine, "guidance": guidance,
                        "ended": _now(), "score": 0,
                        "verdict": "harness_error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "claude": {},
                    }
                    _append(record)
                    records.append(record)


def _compare(scenario_filter: str | None) -> int:
    if not _RESULTS.is_file():
        print("No results yet.")
        return 0
    rows = [json.loads(line) for line in _RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if scenario_filter:
        rows = [r for r in rows if r["scenario"] == scenario_filter]
    if not rows:
        print("No matching results.")
        return 0

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["label"], r["scenario"], r["guidance"]), []).append(r)

    def med(vals):
        """Median, rendered. Missing values are normal -- a run that never
        produced a result JSON has no turn count -- so this returns a dash
        rather than letting None reach a format spec."""
        vals = [v for v in vals if isinstance(v, (int, float))]
        return f"{round(statistics.median(vals), 2)}" if vals else "-"

    header = (f"{'label':<20} {'scenario':<22} {'guide':<8} {'n':>3} {'pass':>5} "
              f"{'err':>4} {'cap':>4} {'score':>6} {'wall':>7} {'turns':>6} "
              f"{'cost':>7}")
    print(header)
    print("-" * len(header))
    for key in sorted(groups):
        label, scenario, guidance = key
        rs = groups[key]
        # Harness errors are the absence of a measurement, so they are counted
        # separately rather than held against the product's pass rate.
        errors = [r for r in rs if r["verdict"] == "harness_error"]
        measured = [r for r in rs if r["verdict"] != "harness_error"]
        passed = sum(1 for r in measured if r["verdict"] == "pass")
        rs = measured or rs
        print(
            f"{label[:20]:<20} {scenario[:22]:<22} {guidance[:8]:<8} "
            f"{len(measured):>3} "
            f"{f'{passed}/{len(measured)}':>5} "
            f"{len(errors) or '':>4} "
            f"{sum(1 for r in measured if (r.get('claude') or {}).get('hit_turn_cap')) or '':>4} "
            f"{med(r['score'] for r in rs):>6} "
            f"{med(r.get('wall_s') for r in rs):>7} "
            f"{med(r['claude'].get('num_turns') for r in rs):>6} "
            f"{med(r['claude'].get('total_cost_usd') for r in rs):>7}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", default="sqlite-first-report",
                        help="Comma-separated scenario ids, or 'all'.")
    parser.add_argument("--guidance", default="pointer",
                        help="Comma-separated: pointer, skills.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--label", default=None,
                        help="Groups results in --compare (default: git branch).")
    parser.add_argument("--model", default="sonnet",
                        help="Model for the agent under test (default: sonnet).")
    parser.add_argument("--keep", action="store_true",
                        help="Keep scratch projects and containers.")
    parser.add_argument("--compare", action="store_true",
                        help="Print the results table and exit.")
    parser.add_argument("--scenario", default=None, help="Filter for --compare.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scaffold, seed and print the prompt; no agent.")
    args = parser.parse_args()

    if args.compare:
        return _compare(args.scenario)

    names = list(SCENARIOS) if args.scenarios == "all" else [
        n.strip() for n in args.scenarios.split(",") if n.strip()
    ]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"Unknown scenario(s): {', '.join(unknown)}\n"
              f"Available: {', '.join(SCENARIOS)}", file=sys.stderr)
        return 2
    guidances = [g.strip() for g in args.guidance.split(",") if g.strip()]

    py = sys.executable
    label = args.label or _git("rev-parse", "--abbrev-ref", "HEAD") or "local"
    scenarios = [SCENARIOS[n] for n in names]

    if args.dry_run:
        for scenario in scenarios:
            run_id = f"dryrun-{scenario.id}"
            scratch = agent_mod.make_scratch(run_id, py, scenario.preexisting)
            seed(scenario, scratch)
            print(f"\n--- {scenario.id} ({scratch}) ---\n"
                  f"{prompt_for(scenario, scratch)}")
        return 0

    # Scenarios asserting an absent engine run in their own pass, after the
    # containers come down. Mixing them costs nothing to do and everything to
    # get wrong: an agent that finds a live container the previous scenario
    # started will use it, and score full marks for diagnosing nothing.
    itest = _itest_run()
    absent = [s for s in scenarios if s.requires_absent_container]
    present = [s for s in scenarios if not s.requires_absent_container]
    if absent and present:
        print(f"note: {', '.join(s.id for s in absent)} run after teardown -- "
              f"they require the engine to be absent.", flush=True)

    services = {
        ENGINES[s.engine].compose_service
        for s in present if s.needs_container
    }
    services.discard(None)
    started_containers = False
    env = agent_mod.child_env()
    if services:
        if not itest._check_docker_available():
            print("`docker compose` is not available -- start Docker Desktop, "
                  "or pick a scenario that needs no container "
                  "(sqlite-first-report).", file=sys.stderr)
            return 1
        rc = itest._compose("up", "-d", "--wait", *sorted(services), env=env)
        if rc != 0:
            print(f"docker compose up failed ({rc})", file=sys.stderr)
            return rc
        started_containers = True

    claude_version = agent_mod.claude_version()
    records: list[dict] = []
    batch_args = dict(guidances=guidances, repeat=args.repeat, label=label,
                      model=args.model, py=py, keep=args.keep,
                      claude_version=claude_version)

    try:
        _batch(present, records, **batch_args)
    finally:
        if started_containers and not args.keep:
            itest._compose("down", "-v", env=env)

    if absent:
        if not present and itest._check_docker_available():
            # Nothing was started here, but a container left up by an earlier
            # run (or `--keep`) would falsify the premise just the same.
            itest._compose("down", "-v", env=env)
        _batch(absent, records, **batch_args)

    print()
    _compare(None if len(names) > 1 else names[0])
    return 0 if all(r["verdict"] == "pass" for r in records) else 1


if __name__ == "__main__":
    sys.exit(main())
