"""What we ask a cold agent to do, and how the data it needs gets there.

Each Scenario is one measurable journey: a seeded data source, a task written
the way a person would write it, and the deterministic evidence that the
journey ended somewhere useful.

Two rules shape everything here:

* The task names no framework commands. The whole experiment is whether the
  guidance layer (pointer file, hooks, `trellum guide`, and -- optionally -- an
  installed skill) is enough on its own. Naming commands in the prompt would
  measure our prompt instead of our product.
* Connection details reach the agent as prose, exactly as a colleague would
  paste them into chat. Nothing is pre-wired: no .env, no configured source.
  Writing those two files IS the journey under test.

Seeding reuses ``itest/engines.py`` -- the same EngineSpec that backs the
integration suite, so the sentinel rows (NULLs, unicode, an embedded quote)
and the ITEST_<ENGINE>_<FIELD> env overrides work identically here. One
source of truth for "what a contract table looks like".
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent
_FRAMEWORK_DIR = _BENCH_DIR.parent

# engines.py imports cleanly with only the standard library at module scope
# (see its docstring), so importing it directly is safe -- same trick
# itest/run.py uses.
sys.path.insert(0, str(_FRAMEWORK_DIR / "itest"))
from engines import ENGINES, _sqlite_seed  # noqa: E402

# The harness itself needs the framework too, for seeding container engines
# through their real driver. Child processes get it via the PYTHONPATH shim
# (see agent.py), but this process is started as `python trellum/bench/run.py`
# -- sys.path[0] is the bench directory, not the repository root -- so without
# this, seeding dies with ModuleNotFoundError after the containers are already
# up and an agent is about to be paid for.
sys.path.append(str(_FRAMEWORK_DIR.parent))

# The two contract rows worth proving end to end: an embedded single quote
# (the classic naive-interpolation break) and non-ASCII text. If both survive
# from the database into data.json, the whole resolver -> driver -> query_df
# -> serialize chain ran for real.
_SENTINELS = ("O'Brien", "héllo — ünïcode")

# Deliberately ordinary: the kind of file a team already has, saying nothing
# about reporting. Its only job is to exist, so the scaffolder skips writing
# the pointer into it.
_HOUSE_RULES = """\
# Working in this repository

- Run `make test` before committing; CI runs the same target.
- Formatting is handled by the pre-commit hook -- do not reformat by hand.
- Keep pull requests focused; one concern per branch.
"""

_TASK = """\
This is a fresh reporting project. Add the data source described below, then \
build a report called `bench-report` showing the `contract` table: every row in \
a table, plus a KPI with the total row count. The report must pass validation.

{conn}"""


@dataclass(frozen=True)
class Scenario:
    id: str
    engine: str                       # key into itest ENGINES
    task: str = _TASK                 # {conn} is substituted at run time
    slug: str = "bench-report"
    expect_substrings: tuple[str, ...] = _SENTINELS
    expect_row_count: int | None = 4
    expected_outcome: str = "report"  # "report" | "diagnose"
    diagnosis_keywords: tuple[str, ...] = ()
    conn_mode: str = "ok"             # "ok" | "bad_password" | "unreachable"
    cred_mode: str = "env"            # "keychain" once #124 lands
    #: Files written into the scratch project *before* `trellum.init` runs, so
    #: the scaffolder meets a project that already has its own conventions --
    #: which, for AGENTS.md, is now the common case rather than the edge one.
    preexisting: tuple[tuple[str, str], ...] = ()
    #: What this journey should cost, in turns. Not a pass/fail threshold: a
    #: run can be entirely correct and still twice as expensive as it should
    #: be, which is what the resolver bug looked like -- 100/100 before and
    #: after, and half the cost afterwards. Without a cost expectation an
    #: optimizer that only chases failures cannot see work like that at all.
    #: Set from a measured baseline, not a guess.
    target_turns: int | None = None
    # Headroom, not a budget. The first measured matrix had two of six runs
    # stop at the cap -- one of them mid-sentence, graded as "failed to
    # diagnose" when it had simply run out of turns. A cap that binds is
    # measuring the cap. Cost is bounded by timeout_s and by the run count.
    max_turns: int = 90
    timeout_s: int = 900

    @property
    def needs_container(self) -> bool:
        if self.requires_absent_container:
            return False
        return bool(ENGINES[self.engine].compose_service)

    @property
    def requires_absent_container(self) -> bool:
        """Stronger than "needs no container": needs there to be none.

        A dead port is not a premise you can assert while the engine is
        running next door. A capable agent will run `docker ps`, find the
        container the previous scenario started, connect to it, and build a
        perfectly good report against a database it was never given -- which
        scores as a pass and measures nothing. Batches sequence around this.
        """
        return self.conn_mode == "unreachable"


# ── connection details, rendered as a human would paste them ──────────────


def _conn_text(scenario: Scenario, scratch: Path) -> str:
    """The prose block appended to the task."""
    if scenario.engine == "sqlite":
        return (
            "Engine: SQLite\n"
            "Database file: data/app.db (already in this project)\n"
            "Table: contract"
        )

    spec = ENGINES[scenario.engine]
    if scenario.conn_mode == "bad_password" and spec.bad_conn_info:
        info = spec.bad_conn_info()
    else:
        info = spec.conn_info()

    if scenario.conn_mode == "unreachable":
        info = dict(info)
        # A port nothing is listening on, in the same private band as the
        # itest mappings so it reads as plausible rather than obviously fake.
        info["port"] = 55499

    lines = [f"Engine: {scenario.engine}"]
    for key, label in (
        ("host", "Host"), ("port", "Port"), ("database", "Database"),
        ("user", "User"), ("password", "Password"),
    ):
        if key in info:
            lines.append(f"{label}: {info[key]}")
    lines.append("Table: contract")
    return "\n".join(lines)


def prompt_for(scenario: Scenario, scratch: Path) -> str:
    return scenario.task.format(conn=_conn_text(scenario, scratch))


# ── seeding ───────────────────────────────────────────────────────────────


def seed(scenario: Scenario, scratch: Path) -> None:
    """Put the contract table where the task says it is.

    Runs before the agent starts. Failures here are harness failures, not
    agent failures, so they raise rather than being graded.
    """
    if scenario.conn_mode == "unreachable":
        return  # nothing to seed; the point is that nothing is there

    if scenario.engine == "sqlite":
        db = scratch / "data" / "app.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        if db.exists():
            db.unlink()
        conn = sqlite3.connect(db)
        try:
            _sqlite_seed(conn)
        finally:
            conn.close()
        return

    # Container engines: connect through the framework's own driver (proving
    # the container is actually reachable before we spend money on an agent)
    # and run the shared seed.
    from trellum.data.drivers import connect

    spec = ENGINES[scenario.engine]
    conn = connect(spec.name, spec.conn_info())

    try:
        spec.seed(conn)
    finally:
        close = getattr(conn, "close", None)
        if close:
            close()


# ── the v1 matrix ─────────────────────────────────────────────────────────

SCENARIOS: dict[str, Scenario] = {
    "sqlite-first-report": Scenario(
        id="sqlite-first-report",
        engine="sqlite",
        target_turns=34,          # measured baseline, 2026-08-31
    ),
    "pg-first-report": Scenario(
        id="pg-first-report",
        engine="postgres",
        target_turns=35,          # measured after the resolver fix
    ),
    # A project that already has its own agent conventions -- which is most of
    # them now. write_project_files() skips a pointer file that already exists,
    # so the framework installs and stays invisible: the agent reads the file,
    # and the file says nothing about trellum.
    "sqlite-existing-pointer": Scenario(
        id="sqlite-existing-pointer",
        engine="sqlite",
        preexisting=(
            ("CLAUDE.md", _HOUSE_RULES),
            ("AGENTS.md", _HOUSE_RULES),
        ),
        # Same journey as sqlite-first-report; the only difference is that the
        # framework never got to introduce itself. Anything above this is the
        # cost of that silence.
        target_turns=34,
    ),
    # The failure lanes. Correct behaviour is naming the real cause -- not
    # quietly building a report on mock data and calling it done, which is
    # the failure mode that makes an agent actively worse than no agent.
    "pg-wrong-password": Scenario(
        id="pg-wrong-password",
        engine="postgres",
        conn_mode="bad_password",
        expected_outcome="diagnose",
        diagnosis_keywords=("password", "authentic", "credential", "login"),
        timeout_s=600,
    ),
    "pg-unreachable": Scenario(
        id="pg-unreachable",
        engine="postgres",
        conn_mode="unreachable",
        expected_outcome="diagnose",
        diagnosis_keywords=(
            "refus", "connect", "unreachable", "not running",
            "timed out", "timeout", "port",
        ),
        timeout_s=600,
    ),
}
