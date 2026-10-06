"""The guard that makes anchors safe to rely on.

Addressing sections by anchor instead of heading text buys one thing: a heading
can be reworded without breaking `trellum guide`. It costs one thing: an
anchor can be deleted during an unrelated prose edit, and nothing would notice —
the CLI would simply start returning nothing for that topic, quietly.

These tests are what converts that silent failure into a loud one, so they need
to run in CI rather than only locally.
"""
from __future__ import annotations

import sys

import pytest

from trellum.agent import agentdoc


def test_every_advertised_topic_resolves():
    """`trellum guide <topic>` must work for every topic it advertises.

    This is the whole point of the file. If it fails, someone removed an
    anchor — restore it rather than removing the topic, unless the section
    genuinely no longer exists.
    """
    missing = agentdoc.missing_topics()
    assert not missing, (
        f"topics with no anchored section: {missing}. "
        f"Add `<!-- topic: NAME -->` above the relevant heading in AGENTS.md "
        f"or README.md."
    )


@pytest.mark.parametrize("topic", sorted(agentdoc.TOPICS))
def test_section_has_content(topic):
    """An anchor pointing at an empty section is as useless as no anchor."""
    section = agentdoc.guide(topic)
    assert section is not None, f"{topic} did not resolve"
    assert section.heading.startswith("#"), (
        f"{topic}: anchor is not directly above a heading (got "
        f"{section.heading[:60]!r})"
    )
    assert len(section.body) > 200, (
        f"{topic}: section body is {len(section.body)} chars — suspiciously "
        f"short, check the anchor sits above the right heading"
    )


def test_check_ids_are_extracted():
    """The validator's check ids come out of validation.py, not a hand-kept list.

    An agent otherwise recovers these by grepping the source, which is
    measurably what happens.
    """
    checks = dict(agentdoc.check_ids())
    assert len(checks) > 50, f"only {len(checks)} check ids found — extraction broke"
    # Spot-check ids that exist and matter, one per level we care about.
    assert checks.get("component-missing-dataset-id") == "FAIL"
    assert checks.get("chart-filter-coverage") == "WARN"
    assert all(lvl in ("PASS", "INFO", "WARN", "FAIL") for lvl in checks.values())


def test_inventories_are_derived_not_stale():
    comps = agentdoc.components()
    assert "KpiRow" in comps and "ScopedDataSource" in comps
    assert len(comps) > 25, "component list looks truncated"
    themes = agentdoc.themes()
    assert themes, "no themes resolved from THEME_REGISTRY"


def test_every_kpi_agg_is_documented():
    """Each `agg === '<name>'` branch in the KpiRow runtime is in the doc.

    The agg semantics table is hand-written prose (the x100 in `ratio` cannot
    be derived), so this is the drift guard in the other direction: add an
    aggregation to the runtime and this fails until the table names it. The
    table exists because one measured session spent 15 tool calls reading the
    runtime to establish what `ratio` computes.
    """
    import re

    from trellum.assets import load_js

    # Read the JS through the same loader the framework uses, so this cannot
    # drift from wherever the asset actually lives.
    src = load_js("components/kpi_row.js")
    aggs = set(re.findall(r"agg === '(\w+)'", src))
    assert len(aggs) >= 5, f"agg extraction broke: {aggs}"

    body = agentdoc.sections()["components"].body
    missing = sorted(a for a in aggs if f"`{a}`" not in body)
    assert not missing, (
        f"KpiRow agg(s) {missing} exist in the runtime but are not in the "
        f"components topic's semantics table -- document them in AGENTS.md")


def test_hooks_merge_without_touching_anyone_elses():
    """Re-running setup must repair its own entry, not duplicate or clobber."""
    from trellum.agent import agentsetup

    theirs = {"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": "their-tool"}]}
    ]}}
    once, changed = agentsetup.merge_hooks(theirs)
    assert changed
    starts = once["hooks"]["SessionStart"]
    assert any(h["command"] == "their-tool" for g in starts for h in g["hooks"]), \
        "an unrelated hook was dropped"
    assert any("-m trellum" in h["command"] for g in starts for h in g["hooks"])

    twice, changed_again = agentsetup.merge_hooks(once)
    assert not changed_again, "second merge should be a no-op"
    assert twice == once
    ours = [g for g in twice["hooks"]["SessionStart"]
            if any("-m trellum" in h["command"] for h in g["hooks"])]
    assert len(ours) == 1, "re-running duplicated our hook"


def test_hook_never_relies_on_the_console_script():
    """Hooks run without an activated virtualenv.

    A bare `trellum` is not on PATH there, and the harness reports it as
    "command not found" on every single session start. This shipped once; the
    test exists so it cannot ship twice.
    """
    from trellum.agent import agentsetup

    for event, groups in agentsetup.hook_config().items():
        for group in groups:
            for hook in group["hooks"]:
                cmd = hook["command"]
                assert "-m trellum" in cmd, (
                    f"{event} hook must invoke `<interpreter> -m trellum`, got {cmd!r}"
                )
                assert cmd.split()[0] != "trellum", (
                    f"{event} hook relies on the console script being on PATH, "
                    f"which it is not inside a hook: {cmd!r}"
                )


def test_bare_console_script_hooks_are_repaired(tmp_path):
    """A hook that relies on the console script being on PATH. Repair it."""
    from trellum.agent import agentsetup

    stale = {"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": "trellum"}]}
    ]}}
    fixed, changed = agentsetup.merge_hooks(stale)
    assert changed
    commands = [h["command"] for g in fixed["hooks"]["SessionStart"]
                for h in g["hooks"]]
    assert "trellum" not in commands, "the broken bare form survived"
    assert any("-m trellum" in c for c in commands)


def test_pointer_shows_a_runnable_command(tmp_path):
    """The scaffolded docs must not tell an agent to type a bare `trellum`.

    They did, and a measured run spent ten tool calls -- Get-Command, py -0p,
    reading settings.json, even reading its own transcript -- working out how to
    invoke it. Documentation that names an unreachable command is worse than
    none: it sends the reader somewhere that does not exist.
    """
    from trellum.agent import agentsetup

    agentsetup.write_project_files(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("trellum"):
            raise AssertionError(
                f"pointer names a bare `trellum` command, which is only on "
                f"PATH inside an activated virtualenv: {stripped!r}"
            )
    assert "-m trellum" in text, "pointer never shows a runnable invocation"


def test_project_files_append_to_existing_house_rules(tmp_path):
    """A project's own CLAUDE.md keeps every word, and gains a signpost.

    Skipping the file entirely was the old behaviour. It was right when an
    AGENTS.md was rare; now that most projects have one, skipping means the
    framework installs and stays invisible to every agent reading only that
    file.
    """
    from trellum.agent import agentsetup

    (tmp_path / "CLAUDE.md").write_text("# our own rules\n", encoding="utf-8")
    results = dict(agentsetup.write_project_files(tmp_path))

    assert results["CLAUDE.md"] == "appended"
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# our own rules\n"), "their content must lead, intact"
    assert "-m trellum" in text, "the signpost must name a runnable invocation"
    assert agentsetup.BLOCK_START in text and agentsetup.BLOCK_END in text

    assert results["AGENTS.md"] == "created"
    assert "trellum guide" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_appended_signpost_is_idempotent_and_repairs_in_place(tmp_path):
    """Re-running must refresh our block, never stack a second one."""
    from trellum.agent import agentsetup

    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")
    agentsetup.write_project_files(tmp_path)

    assert dict(agentsetup.write_project_files(tmp_path))["AGENTS.md"] == "current"

    path = tmp_path / "AGENTS.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("-m trellum", "-m STALE"),
        encoding="utf-8",
    )
    assert dict(agentsetup.write_project_files(tmp_path))["AGENTS.md"] == "appended"

    text = path.read_text(encoding="utf-8")
    assert text.count(agentsetup.BLOCK_START) == 1, "block was duplicated"
    assert "STALE" not in text, "stale block was left behind"
    assert text.startswith("# house\n")


def test_signpost_can_be_removed_leaving_their_file_intact(tmp_path):
    """Anything we add to a file we do not own, we must be able to take back."""
    from trellum.agent import agentsetup

    original = "# house\n\n- Run `make test`.\n"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")
    agentsetup.write_project_files(tmp_path)
    assert "-m trellum" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    results = dict(agentsetup.remove_project_files(tmp_path))
    assert results["AGENTS.md"] == "removed"
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("args", [[], ["guide", "report"], ["checks"]])
def test_cli_survives_a_legacy_console_encoding(args):
    """The CLI must print on a default Windows console.

    Those consoles use a legacy codepage, and the documents this CLI prints
    contain characters outside it -- a checkmark was enough to abort
    `guide report` with UnicodeEncodeError. A measured run hit that on its
    second command and then carried PYTHONIOENCODING=utf-8 on every Python
    invocation for the rest of the session.

    Run as a subprocess with a restricted encoding, because that is the only way
    to reproduce what the user's shell does; in-process, pytest has already
    replaced stdout with something forgiving.
    """
    import os
    import subprocess

    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run([sys.executable, "-m", "trellum", *args],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, (
        f"`trellum {' '.join(args)}` failed under cp1252:\n"
        f"{proc.stderr[-600:]}"
    )
    assert proc.stdout.strip(), "produced no output"


def test_pointer_and_description_agree_on_the_build_command(tmp_path):
    """The scaffolded pointer must not contradict the resident description.

    These live in different files and get updated by different changes. When
    the build command was corrected to `--no-serve` it was fixed in the
    description and in this repo's own AGENTS.md, and the pointer template was
    missed -- so a consumer project was scaffolded with a CLAUDE.md telling the
    agent to run the form that blocks, while the session hook told it the
    opposite. Contradicting yourself is worse than either instruction alone.
    """
    from trellum.agent import agentsetup

    agentsetup.write_project_files(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    single = [ln.strip() for ln in text.splitlines()
              if "trellum.run reports/" in ln]
    assert single, "the pointer never shows how to build a report"
    assert "--no-serve" in single[0], (
        f"the pointer leads with a build command that never exits: {single[0]!r}"
    )


def test_pointer_names_the_working_directory(tmp_path):
    """A relative interpreter path is only correct from one directory.

    A measured run `cd`-ed into a report folder to read it, and the next two
    commands died with `No such file or directory` against the interpreter --
    an error that says nothing about the actual cause. It then prefixed a `cd`
    onto essentially every later command. The path stays relative, because this
    file is committed and a teammate's virtualenv is not at our absolute path;
    what it costs is one sentence saying where to run it from.
    """
    from trellum.agent import agentsetup

    agentsetup.write_project_files(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").lower()
    assert "repository root" in text, (
        "the pointer never says which directory its relative paths are "
        "relative to"
    )

    # The relative-path warning must appear only when the path actually is
    # relative -- otherwise the file describes something the reader cannot see.
    from pathlib import Path as _P
    py = agentsetup.hook_interpreter(tmp_path)
    warned = "interpreter path is relative" in text
    assert warned is not _P(py).is_absolute(), (
        f"interpreter is {'absolute' if _P(py).is_absolute() else 'relative'} "
        f"but the note {'is' if warned else 'is not'} present"
    )


def test_component_signatures_are_derived_and_complete():
    """Every component the description advertises must be callable from the guide.

    A component listed by name with no signature is the case this exists to
    remove: the agent knows what to reach for and still cannot write the call.
    """
    sigs = {name: (req, opt) for name, req, opt in agentdoc.signatures()}
    missing = [c for c in agentdoc.components() if c not in sigs]
    assert not missing, f"advertised but not introspectable: {missing}"
    assert len(sigs) > 25, f"only {len(sigs)} signatures — extraction broke"


def test_the_two_charts_that_look_interchangeable_are_distinguishable():
    """The specific confusion this feature was built for.

    A measured run picked `BarChart` for a share-of-total because it copied
    another report; the run that got it right introspected these two classes by
    hand first. Both are reached from the same row of the reuse table, and they
    do not take the same arguments -- so if this ever stops being visible, the
    guide is back to naming components an agent cannot call.
    """
    sigs = {name: req for name, req, _ in agentdoc.signatures()}
    assert {"label", "value"} <= set(sigs["DoughnutChart"]), sigs["DoughnutChart"]
    assert {"x", "y"} <= set(sigs["BarChart"]), sigs["BarChart"]

    text = agentdoc.signatures_text()
    assert "DoughnutChart(" in text and "label" in text
    assert "BarChart(" in text


def test_build_command_shown_to_agents_terminates():
    """The build command the description leads with must exit on its own.

    `trellum.run` serves by default, so the bare form blocks until it is
    killed. A measured run took our word for it: the build finished in 0.1s,
    the command sat in the foreground for the harness's full 120s timeout, and
    the agent then spent three tool calls (`sleep 1`, `true`, and finally a
    `cat` of the background output) working out whether it had succeeded. Two
    of a 4.3-minute run, spent waiting on a server nobody asked for.

    Any blocking form may still be shown, but only below the warning that says
    so -- an agent reading top-down must reach the terminating one first.
    """
    from trellum.cli import _describe

    lines = _describe().splitlines()
    warned_at = next((i for i, ln in enumerate(lines) if "BLOCKS" in ln), len(lines))
    for i, line in enumerate(lines[:warned_at]):
        if "trellum.run" in line and "--all" not in line:
            assert "--no-serve" in line, (
                f"line {i} offers a build command that never exits, above any "
                f"warning that it blocks: {line.strip()!r}"
            )
    assert warned_at < len(lines), (
        "the serving form is shown with no indication that it blocks"
    )


def test_doctor_fails_on_an_unwired_project(tmp_path, capsys):
    """The check that makes the rest of `doctor` worth running.

    Every failure it looks for is silent by nature -- a project with no pointer
    files and no hooks behaves exactly like a healthy one until you notice the
    agent never mentions the framework. If `doctor` returns 0 here it is a
    rubber stamp, and worse than nothing, because someone will trust it.
    """
    from trellum.cli import main

    assert main(["doctor", "--dest", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "pointer files" in out and "hooks configured" in out


def test_doctor_passes_after_setup(tmp_path, capsys):
    """And it must go green once the thing it complains about is done."""
    from trellum.agent import agentsetup
    from trellum.cli import main

    agentsetup.write_project_files(tmp_path)
    code = main(["doctor", "--dest", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0, f"doctor still fails after `setup project`:\n{out}"
    assert "FAIL" not in out


@pytest.mark.parametrize("name", ["data", "checks", "doctor"])
def test_commands_asked_for_as_topics_are_answered(name, capsys):
    """`guide data` must work, because a measured run typed exactly that.

    The description tells the agent to run `trellum data`; the agent is
    already batching names into one `guide` call to save round-trips -- which is
    the behaviour we asked for -- and `guide report data` is the natural result.
    It got `unknown topic(s): data` for asking correctly for the right thing.

    Erroring here is friction with no upside: the intent is unambiguous, so
    answer it.
    """
    from trellum.cli import main

    assert main(["guide", name]) == 0
    assert capsys.readouterr().out.strip(), f"`guide {name}` printed nothing"


def test_a_genuinely_unknown_topic_still_fails_loudly(capsys):
    """The forgiving path must not swallow real mistakes."""
    from trellum.cli import main

    assert main(["guide", "nonsense-topic"]) == 1
    err = capsys.readouterr().err
    assert "unknown topic" in err
    # And the error has to name all three things it could have meant, since
    # confusing a command for a topic is what got us here.
    assert "bundles:" in err and "or a command:" in err


def test_scaffolder_prints_on_a_default_windows_console(tmp_path):
    """`trellum.new` must survive a legacy codepage.

    It began printing the files it writes, and those templates carry box-drawing
    characters (`# ─── Identity ───`). On a default Windows console that aborts
    with UnicodeEncodeError *after* the files are on disk -- so the scaffold
    fails having succeeded, and the caller then has to check whether it worked.
    A measured run hit exactly that and spent the calls this change existed to
    save.

    Third entry point to need the UTF-8 guard that runner.py has carried for
    years, which is why it gets a test rather than more care.
    """
    import os
    import subprocess

    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    proc = subprocess.run(
        [sys.executable, "-m", "trellum.new", "encoding-probe"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0, (
        f"scaffolding aborted under cp1252:\n{proc.stderr[-600:]}"
    )
    assert "report.yaml" in proc.stdout


def test_scaffold_points_at_a_data_source_that_exists():
    """The scaffold must not invent a warehouse the project does not have.

    It hardcoded `vertica_<studio>`, so the first build of a scaffolded report
    in any non-Vertica project -- including the demo warehouse the framework
    ships -- died with `No data_source named 'vertica_default' ... Available:
    ['demo_db']`. Two measured runs hit it and each edited two files to
    recover; the first time I read it as an authoring slip rather than a
    template bug.
    """
    from trellum.scaffold import new_report

    source, block = new_report._scaffold_source("default", "DEFAULT")

    try:
        from trellum.data.datasource_config import load_datasource_config
        configured = load_datasource_config()
    except Exception:
        configured = {}

    if configured:
        assert source in configured, (
            f"scaffold points at {source!r}, which is not configured: "
            f"{list(configured)}"
        )
        assert "vertica_default" not in block or "vertica_default" in configured
    else:
        # No config at all is the greenfield case; a worked placeholder is right.
        assert "vertica" in block


def test_scaffolded_sql_runs_on_more_than_one_backend():
    """`DATE :param` is Vertica syntax and sqlite rejects it outright.

    A measured run pasted the template predicate, got
    `ERR near ":start_date": syntax error`, and spent two calls rediscovering
    that the cast has to go. Plain `BETWEEN :start_date AND :end_date` is
    accepted by sqlite, Postgres and Vertica alike.
    """
    import sqlite3

    from trellum.scaffold import new_report

    sql = new_report.QUERIES_TEMPLATE
    assert "DATE :start_date" not in sql, "template SQL is Vertica-only"

    conn = sqlite3.connect(":memory:")
    conn.execute("create table some_table (event_date text, category text, value real)")
    # The predicate shape the template teaches must at least parse.
    conn.execute(
        "select * from some_table where event_date between :start_date and :end_date",
        {"start_date": "2026-01-01", "end_date": "2026-12-31"},
    )


def test_describe_stays_within_its_budget():
    """The description is re-read on every exchange, so its size is a cost.

    This bound was raised to 130 once, to hold the component selection table
    and the constructor signatures, on the theory that fetching them was a
    decision a weaker agent might not make. Measured: `trellum guide` is
    fetched in every recorded run at call one or two, so on-demand content was
    already being reached, and the call count moved by nothing outside its
    existing spread. The block came back out and the bound came back with it.

    Kept at 80 because what the runs actually spend calls on -- live project
    state and a worked example -- is not reference material, and reference
    material expanding to fill the space is how the block stops being read.
    """
    from trellum.cli import _describe
    text = _describe()
    assert "trellum guide" in text, "the description must point at the depth"
    n = len(text.splitlines())
    assert n < 80, (
        f"description is {n} lines. It is sent on every exchange; move detail "
        f"behind `trellum guide`, or move this bound on purpose and say why."
    )


# ── setup portal (internal planning ticket #151) ─────────────────────────────────────────────

PORTAL_URL = "https://bi.example.com/s/acme/games"
PORTAL_KEY = "trellum_pk_ab12cd34_" + "x" * 32


def _setup_portal(tmp_path, url=PORTAL_URL, key=PORTAL_KEY):
    from trellum.cli import main

    return main(["setup", "portal", "--url", url, "--key", key, "--dest", str(tmp_path)])


def _read_json(path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def test_setup_portal_writes_the_server_the_key_and_the_pointer(tmp_path, capsys):
    """One command, three files, and the literal key in exactly one of them."""
    assert _setup_portal(tmp_path) == 0
    claude = _read_json(tmp_path / ".mcp.json")["mcpServers"]["trellum"]
    assert claude == {"type": "http", "url": PORTAL_URL + "/mcp",
                      "headers": {"Authorization": "Bearer ${TRELLUM_API_KEY}"}}
    cursor = _read_json(tmp_path / ".cursor" / "mcp.json")["mcpServers"]["trellum"]
    assert cursor == {"url": PORTAL_URL + "/mcp",
                      "headers": {"Authorization": "Bearer ${env:TRELLUM_API_KEY}"}}
    for name in (".mcp.json", ".cursor/mcp.json", "CLAUDE.md", "AGENTS.md"):
        assert PORTAL_KEY not in (tmp_path / name).read_text(encoding="utf-8"), name
    assert f"TRELLUM_API_KEY={PORTAL_KEY}\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "## Portal" in text and "-m trellum guide portal" in text, name
    assert "verified" in capsys.readouterr().out


def test_setup_portal_merges_in_place_and_is_idempotent(tmp_path):
    """Theirs survives, ours is replaced, and a second run changes nothing."""
    import json

    theirs = {"mcpServers": {"docs": {"command": "npx", "args": ["docs-mcp"]},
                             "trellum": {"type": "http", "url": "https://old/s/a/b/mcp"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(theirs), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "# secrets\nBI_WH_USER=ana\nTRELLUM_API_KEY=old\nBI_WH_PASS=pw\n", encoding="utf-8")

    assert _setup_portal(tmp_path) == 0
    servers = _read_json(tmp_path / ".mcp.json")["mcpServers"]
    assert servers["docs"] == theirs["mcpServers"]["docs"]
    assert servers["trellum"]["url"] == PORTAL_URL + "/mcp"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert env == f"# secrets\nBI_WH_USER=ana\nTRELLUM_API_KEY={PORTAL_KEY}\nBI_WH_PASS=pw\n"

    files = (".mcp.json", ".cursor/mcp.json", ".env", "CLAUDE.md", "AGENTS.md")
    snapshot = {n: (tmp_path / n).read_text(encoding="utf-8") for n in files}
    assert _setup_portal(tmp_path) == 0
    assert {n: (tmp_path / n).read_text(encoding="utf-8") for n in files} == snapshot

    assert _setup_portal(tmp_path, url="https://new/s/x/y/", key="trellum_pk_new") == 0
    assert _read_json(tmp_path / ".mcp.json")["mcpServers"]["trellum"]["url"] == "https://new/s/x/y/mcp"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert env.count("TRELLUM_API_KEY=") == 1 and "TRELLUM_API_KEY=trellum_pk_new\n" in env


def test_setup_portal_refuses_to_finish_with_an_unignored_env(tmp_path, capsys):
    """The key is written, and the command still exits 1 until .env is
    ignored: a warning nobody reads is how a key ends up in a commit."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    assert _setup_portal(tmp_path) == 1
    err = capsys.readouterr().err
    assert ".gitignore" in err and "TRELLUM_API_KEY" in err
    assert (tmp_path / ".env").is_file() and (tmp_path / ".mcp.json").is_file()

    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    assert _setup_portal(tmp_path) == 0


def test_pointer_names_the_portal_only_when_the_server_is_there(tmp_path):
    """A repository with no portal must not read about one."""
    import json

    from trellum.agent import agentsetup

    assert len(agentsetup.PORTAL_SECTION.strip().splitlines()) < 10

    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")   # the signpost path
    agentsetup.write_project_files(tmp_path)                              # and the POINTER path
    for name in ("CLAUDE.md", "AGENTS.md"):
        assert "## Portal" not in (tmp_path / name).read_text(encoding="utf-8")

    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "x"}}}), encoding="utf-8")
    results = dict(agentsetup.write_project_files(tmp_path))
    assert results["CLAUDE.md"] == "current" and results["AGENTS.md"] == "current", \
        "somebody else's server is not the portal"

    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trellum": {"url": "https://p/s/a/b/mcp"}}}), encoding="utf-8")
    results = dict(agentsetup.write_project_files(tmp_path))
    assert results["CLAUDE.md"] == "appended" and results["AGENTS.md"] == "appended"
    for name in ("CLAUDE.md", "AGENTS.md"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert text.count("## Portal") == 1, name
        assert "-m trellum guide portal" in text and "TRELLUM_API_KEY" in text


def test_remove_project_files_drops_our_server_entry_only(tmp_path):
    """The counterpart: what setup portal adds, remove takes back -- and
    nothing else."""
    import json

    from trellum.agent import agentsetup

    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")
    assert _setup_portal(tmp_path) == 0
    conf = _read_json(tmp_path / ".mcp.json")
    conf["mcpServers"]["docs"] = {"command": "x"}
    (tmp_path / ".mcp.json").write_text(json.dumps(conf), encoding="utf-8")

    results = dict(agentsetup.remove_project_files(tmp_path))
    assert results[".mcp.json"] == "removed" and results[".cursor/mcp.json"] == "removed"
    assert _read_json(tmp_path / ".mcp.json")["mcpServers"] == {"docs": {"command": "x"}}
    assert "trellum" not in _read_json(tmp_path / ".cursor" / "mcp.json")["mcpServers"]
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# house\n"


def test_guide_portal_walks_the_loop_in_order(capsys):
    """The topic exists, resolves, and names the steps in the order they
    happen -- an agent reads it top to bottom."""
    from trellum.cli import main

    assert main(["guide", "portal"]) == 0
    out = capsys.readouterr().out
    steps = ["validate", "push", "check_repo_changes", "publish_repo_changes", "run_report",
             "get_report_details", "test_data_source", "configure_data_source", "create_alert"]
    positions = [out.index(s) for s in steps]
    assert positions == sorted(positions), "the loop is out of order"
    assert "`write`" in out and "`read`" in out


def test_doctor_warns_about_a_broken_portal_entry_and_stays_green(tmp_path, capsys):
    """A project without a portal is fine; a half-wired one is worth a line,
    and never a failure -- a portal is optional."""
    import json

    from trellum.agent import agentsetup
    from trellum.cli import main

    def portal_row():
        return next(ln for ln in capsys.readouterr().out.splitlines() if "portal server" in ln)

    agentsetup.write_project_files(tmp_path)
    assert main(["doctor", "--dest", str(tmp_path)]) == 0
    assert "ok" in portal_row()

    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"trellum": {
        "type": "http", "url": "https://bi.example.com/s/acme/games",
        "headers": {"Authorization": "Bearer ${TRELLUM_API_KEY}"}}}}), encoding="utf-8")
    assert main(["doctor", "--dest", str(tmp_path)]) == 0
    row = portal_row()
    assert "WARN" in row and "/mcp" in row and "TRELLUM_API_KEY" in row

    assert _setup_portal(tmp_path) == 0
    assert main(["doctor", "--dest", str(tmp_path)]) == 0
    row = portal_row()
    assert "ok" in row and "WARN" not in row
