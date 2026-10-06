#!/usr/bin/env python
"""Self-check for the bench's own logic: python trellum/bench/selfcheck.py

Everything here is pure -- no agent, no containers, no cost. It exists because
the grader and the optimizer's guardrail are the two places where a quiet bug
does real damage: a wrong grade poisons every comparison built on it, and a
leaky whitelist hands the fixer its own scorer.

Not named test_*.py on purpose: this must not be swept into a pytest run that
expects the framework's own suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import grade  # noqa: E402
import optimize  # noqa: E402
from scenarios import SCENARIOS, prompt_for  # noqa: E402


def check_row_counts() -> None:
    # DataSource shape: rows under _data at the top level.
    data = {
        "_ds_a": {"_data": [1, 2, 3, 4]},
        "_ds_b": {"_data": []},
        "_meta": {"not_data": 1},
        "scalar": 5,
    }
    assert grade._row_counts(data) == [4, 0], grade._row_counts(data)
    assert grade._row_counts({}) == []

    # DataTable shape: rows nested under components. A real measured run built
    # a perfectly good report this way and was scored as having no data,
    # because the counter only knew the shape above.
    nested = {
        "components": {
            "fw_c2": {"type": "kpi", "value": 4},
            "fw_c3": {"type": "table", "columns": ["id"],
                      "rows": [["1"], ["2"], ["3"], ["4"]]},
        }
    }
    assert grade._row_counts(nested) == [4], grade._row_counts(nested)


def check_find_report(tmp: Path) -> None:
    scratch = tmp / "find"
    (scratch / "reports" / "bench-report").mkdir(parents=True)
    (scratch / "reports" / "bench-report" / "report.yaml").write_text("x")
    assert grade._find_report(scratch, "bench-report") == ("bench-report", False)

    # A differently-named report is still graded, and flagged rather than
    # failed -- the agent did the work, it just ignored the name.
    other = tmp / "other"
    (other / "reports" / "sales").mkdir(parents=True)
    (other / "reports" / "sales" / "report.yaml").write_text("x")
    assert grade._find_report(other, "bench-report") == ("sales", True)

    empty = tmp / "empty"
    empty.mkdir()
    assert grade._find_report(empty, "bench-report") == (None, False)


def check_mock_detection(tmp: Path) -> None:
    scratch = tmp / "mock"
    out = scratch / "output" / "r1"
    out.mkdir(parents=True)
    (out / "_meta.json").write_text('{"details": {"data_source": "mock"}}')
    assert grade._mock_reports(scratch) == ["r1"]

    real = tmp / "real"
    out2 = real / "output" / "r1"
    out2.mkdir(parents=True)
    (out2 / "_meta.json").write_text('{"details": {"data_source": "real"}}')
    assert grade._mock_reports(real) == []


def check_diagnose_scoring(tmp: Path) -> None:
    scenario = SCENARIOS["pg-wrong-password"]

    clean = tmp / "diag_clean"
    clean.mkdir()
    good = grade._grade_diagnose(
        scenario, clean, {"result_text": "The PASSWORD was rejected by the server."})
    assert good["score"] == 100 and good["verdict"] == "pass", good

    # Naming the cause but also shipping a mock-data report loses the 30.
    mocked = tmp / "diag_mock"
    out = mocked / "output" / "r1"
    out.mkdir(parents=True)
    (out / "_meta.json").write_text('{"details": {"data_source": "mock"}}')
    partial = grade._grade_diagnose(
        scenario, mocked, {"result_text": "authentication failed"})
    assert partial["score"] == 70 and partial["verdict"] == "partial", partial

    silent = grade._grade_diagnose(
        scenario, clean, {"result_text": "Report built successfully!"})
    assert silent["score"] == 30 and silent["verdict"] == "partial", silent

    # The false positive this check exists for: a real measured run scored 100
    # on pg-unreachable by finding another postgres container, connecting to a
    # database it was never given, and building a genuinely working report --
    # transparently, and while matching every diagnosis keyword. Real seeded
    # rows in the output mean something connected, so the 30 is withheld.
    swapped = tmp / "diag_swapped"
    out2 = swapped / "output" / "bench-report"
    out2.mkdir(parents=True)
    (out2 / "data.json").write_text(
        '{"_ds": {"_data": [{"label": "O\'Brien"}]}}', encoding="utf-8")
    sneaky = grade._grade_diagnose(
        SCENARIOS["pg-unreachable"], swapped,
        {"result_text": "Connection refused on that port; used the live one."})
    assert sneaky["score"] == 70, sneaky
    assert sneaky["checks"]["substituted_source_reports"] == ["bench-report"], sneaky


def check_below_par() -> None:
    """An expensive-but-correct run must still be targetable.

    This is the case the optimizer was originally blind to: the resolver bug
    scored 100 before and after, and cost twice as much before.
    """
    def rec(scenario, verdict, turns, score=100):
        return {"scenario": scenario, "verdict": verdict, "score": score,
                "claude": {"num_turns": turns}}

    # sqlite-first-report targets 34 turns.
    assert not optimize._below_par(rec("sqlite-first-report", "pass", 34))
    assert not optimize._below_par(rec("sqlite-first-report", "pass", 40))
    assert optimize._below_par(rec("sqlite-first-report", "pass", 60))
    assert optimize._below_par(rec("sqlite-first-report", "fail", 20, score=0))
    # No target set: only correctness can condemn it.
    assert not optimize._below_par(rec("pg-wrong-password", "pass", 900))

    # Fewer turns at equal score is an improvement; more is not.
    better = rec("sqlite-first-report", "pass", 20)
    worse = rec("sqlite-first-report", "pass", 60)
    assert optimize._quality(better) > optimize._quality(worse)


def check_whitelist() -> None:
    assert optimize.path_allowed("trellum/AGENTS.md")
    assert optimize.path_allowed("trellum/agent/agentdoc.py")
    assert optimize.path_allowed("trellum/cli/commands/data.py")
    # The grader is off limits even though the loop is what enforces it.
    assert not optimize.path_allowed("trellum/bench/grade.py")
    # Anything outside the guidance surface: data logic, the portal, CI.
    assert not optimize.path_allowed("trellum/data/drivers/postgres.py")
    assert not optimize.path_allowed("apps/datasources/models.py")
    assert not optimize.path_allowed(".github/workflows/framework.yml")


def check_container_isolation() -> None:
    """An absent-engine scenario must never be batched with a live one."""
    assert SCENARIOS["pg-unreachable"].requires_absent_container
    assert not SCENARIOS["pg-unreachable"].needs_container
    # Wrong-password still needs the engine up: the server has to be there to
    # reject the login, otherwise it is just the unreachable case again.
    assert SCENARIOS["pg-wrong-password"].needs_container
    assert not SCENARIOS["pg-wrong-password"].requires_absent_container
    assert not SCENARIOS["sqlite-first-report"].needs_container


def check_prompts(tmp: Path) -> None:
    scratch = tmp / "prompt"
    scratch.mkdir()

    ok = prompt_for(SCENARIOS["pg-first-report"], scratch)
    assert "itest_pw" in ok and "55432" in ok, ok

    bad = prompt_for(SCENARIOS["pg-wrong-password"], scratch)
    assert "wrong-password" in bad, bad

    dead = prompt_for(SCENARIOS["pg-unreachable"], scratch)
    assert "55499" in dead and "55432" not in dead, dead

    lite = prompt_for(SCENARIOS["sqlite-first-report"], scratch)
    assert "data/app.db" in lite, lite

    # The task must never name a framework command -- that is the thing under
    # test, and telling the agent the answer would measure the prompt instead.
    for scenario in SCENARIOS.values():
        text = prompt_for(scenario, scratch)
        for banned in ("trellum", "guide", "report.yaml", "generator.py"):
            assert banned not in text, f"{scenario.id} leaks {banned!r}"


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        checks = [
            ("row counts", lambda: check_row_counts()),
            ("find report", lambda: check_find_report(tmp)),
            ("mock detection", lambda: check_mock_detection(tmp)),
            ("diagnose scoring", lambda: check_diagnose_scoring(tmp)),
            ("container isolation", lambda: check_container_isolation()),
            ("cost-aware targeting", lambda: check_below_par()),
            ("optimizer whitelist", lambda: check_whitelist()),
            ("prompts", lambda: check_prompts(tmp)),
        ]
        for name, fn in checks:
            fn()
            print(f"  ok  {name}")
    print("all bench self-checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
