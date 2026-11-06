# The agent bench

Measures what a cold agent can do with this framework: drop a headless Claude
Code session into a freshly scaffolded project, hand it connection details the
way a colleague would paste them into chat, and grade what comes out.

Internal tooling. Never added to `pyproject.toml`'s `packages` list, so it
stays out of the wheel exactly like `itest/`.

## Why it exists

Every improvement to the guidance layer — pointer files, `trellum guide`
topics, error messages, skills — is currently made on faith. This produces a
number instead.

## Running

```
python trellum/bench/run.py --dry-run                       # plumbing check, no agent, no cost
python trellum/bench/run.py --scenarios sqlite-first-report --repeat 3
python trellum/bench/run.py --scenarios sqlite-first-report --guidance pointer,skills
python trellum/bench/run.py --scenarios all --repeat 3 --label baseline
python trellum/bench/run.py --compare
```

Use the venv interpreter (`.venv/Scripts/python.exe` on Windows). Containers
come up for the scenarios that need them and go down afterwards, reusing
`itest/`'s compose file and project name — a `--keep` here and an
`itest/run.py --keep` there refer to the same containers.

Benchmark agents use the CLI's `default` permission mode, including when run
outside the optimizer. They no longer bypass permission checks. Grant only
intended tools in local configuration; headless denials can affect results.

Runs cost real money. `--repeat 3` and comparing medians is the cheapest
defence against model nondeterminism; a single run proves very little.

## What is measured

Report scenarios (0–100, additive):

| Check | Points |
| --- | --- |
| the grader's own rebuild exits 0 | 40 |
| sentinel rows and row count present in `output/<slug>/data.json` | 40 |
| `_validation.json` reports zero FAILs | 20 |

Diagnose scenarios — bad credentials, dead port — measure something else
entirely: whether the agent names the real cause (70) and refrains from
presenting a mock-data build as the answer (30). An agent that silently falls
back to mock data is worse than no agent, so that failure is scored explicitly.

Warn counts, wall-clock, turns and cost are recorded but deliberately not
scored: folding them in invites optimising the wrong thing.

The grader rebuilds the report itself rather than trusting the agent's
summary. "Done" and a working output directory are different claims.

## Guidance arms

`--guidance pointer` is the current bet: the framework describes itself
through the scaffolded `CLAUDE.md` pointer and `trellum guide`.
`--guidance skills` adds the prototype thin skill in `assets/SKILL.md` to the
scratch project. Running both on the same scenarios is what the harness is
for — it answers whether shipping skills is worth doing before we build the
installer.

## Results

`results/results.jsonl`, one line per run, gitignored along with the scratch
projects and copied transcripts. A failed run keeps its scratch project so the
failure can be reproduced by hand; a passing one is deleted unless `--keep`.

Transcripts land in `results/transcripts/<run_id>.jsonl`. They are the input
to any question that starts "why did it fail".

## Maintainer optimizer (explicit opt-in)

```
# Prepare a disposable linked worktree of this repository first.
git worktree add -b bench/auto-review /absolute/path/to/bench-review HEAD
python trellum/bench/optimize.py --enable-optimizer \
  --worktree /absolute/path/to/bench-review --max-iters 4 --budget-usd 10
```

This is a maintainer-only, paid-provider workflow. Both `--enable-optimizer`
and an explicit `--worktree` are required; there is no automatic checkout path.
Existing scenario, model, repeat, budget, iteration and timeout options remain.
Both benchmark agents and the fixer use Claude's `default` permission mode;
permission bypass is no longer supplied. Configure only the permissions you
intend to grant before running. Headless permission denials may prevent a fix
or affect scores; permissions are not a sandbox. Review the target and its local
configuration before opting in. Do not compare these runs to historical runs
that bypassed permissions without accounting for that difference.

Runs the bench, reads the worst failure's transcript, asks a fixer agent for
one targeted change to the guidance layer, re-measures that scenario, and keeps
the commit only if the score actually moved. Stops on budget, iteration count,
wall clock, everything passing, or two iterations without a usable fix.
Budget and wall-clock limits are checked between iterations, not hard provider
spending caps; an in-flight benchmark/fix/recheck can exceed them.

The guardrails are the loop's job, never the fixer's:

* a dedicated linked worktree on a `bench/auto-*` branch, sharing this
  repository's Git common directory. The working checkout, other repositories,
  detached heads, other branches and subdirectories are refused. It must start
  clean, including untracked and ignored files. Use this checkout only for the
  optimizer while it runs; its tracked changes may be discarded. Checkout
  identity and branch are checked again before each destructive revert
* the target's `agent.py` and `run.py` must match the optimizer's harness,
  checked before every benchmark batch, so an older target cannot silently
  restore permission bypasses. Prepare the worktree from the same revision
* the fixer may only touch the guidance surface (`WHITELIST` in `optimize.py`).
  `trellum/bench/` is denied outright, and deny beats allow, so widening the
  whitelist later cannot accidentally hand the fixer its own grader
* a fix that touches anything else is reverted, unread
* it never merges and never pushes — the branch is left for review

If the real cause of a failure is in data or rendering logic, the fixer is told
to say so rather than fix it. Those changes need a human.

## Self-check

```
python trellum/bench/selfcheck.py
```

Pure logic only — no agent, no containers, no cost. Covers the grader and the
whitelist predicate, the two places where a quiet bug does real damage, and
asserts that no scenario prompt names a framework command.
