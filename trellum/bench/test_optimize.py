"""Optimizer safety checks: mocked agents and disposable Git repos, no providers."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"bench_test_{name}", Path(__file__).with_name(f"{name}.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def optimizer(monkeypatch):
    module = _load("optimize")

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected real subprocess")

    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    return module


@pytest.fixture
def checkout(optimizer, monkeypatch, tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / ".git").write_text("fake linked worktree marker")
    monkeypatch.setattr(optimizer, "_REPO_DIR", source)
    state = dict(root=target, common=source / ".git", branch="bench/auto-test", dirty="")
    calls = []

    def git(*args, cwd):
        calls.append((args, cwd))
        if args == ("rev-parse", "--show-toplevel"):
            return str(state["root"])
        if args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return str(source / ".git" if cwd == source else state["common"])
        if args == ("symbolic-ref", "--quiet", "--short", "HEAD"):
            return state["branch"]
        if args[0] == "status":
            assert args == ("status", "--porcelain", "--untracked-files=all", "--ignored")
            return state["dirty"]
        if args[0] == "reset":
            return ""
        pytest.fail(f"Unexpected fake git command: {args}")

    monkeypatch.setattr(optimizer, "_git", git)
    return target, source, state, calls


@pytest.mark.parametrize("argv", [[], ["--enable-optimizer"], ["--worktree", "/unused"]])
def test_explicit_cli_gates_before_any_git_or_provider(optimizer, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["optimize.py", *argv])
    with pytest.raises(SystemExit) as exc:
        optimizer.main()
    assert exc.value.code == 2


def test_no_implicit_worktree(optimizer):
    with pytest.raises(SystemExit, match="Pass --worktree"):
        optimizer._ensure_worktree(None)


def test_clean_linked_checkout_is_accepted(optimizer, checkout):
    target, _, _, _ = checkout
    assert optimizer._ensure_worktree(target) == target


@pytest.mark.parametrize("field,value", [
    ("root", "/different/root"),
    ("common", "/different/repo/.git"),
    ("branch", "main"),
    ("branch", "feature/user-work"),
    ("branch", ""),
    ("dirty", " M tracked.txt"),
    ("dirty", "A  staged.txt"),
    ("dirty", "?? untracked.txt"),
    ("dirty", "!! ignored.txt"),
])
def test_unsafe_checkout_is_rejected(optimizer, checkout, field, value):
    target, _, state, _ = checkout
    state[field] = value
    with pytest.raises(SystemExit, match="Refusing target"):
        optimizer._ensure_worktree(target)


def test_source_checkout_is_rejected(optimizer, checkout):
    target, _, _, _ = checkout
    optimizer._REPO_DIR = target
    with pytest.raises(SystemExit, match="Refusing target"):
        optimizer._ensure_worktree(target)


def test_regular_clone_is_rejected(optimizer, checkout):
    target, _, _, _ = checkout
    (target / ".git").unlink()
    (target / ".git").mkdir()
    with pytest.raises(SystemExit, match="Refusing target"):
        optimizer._ensure_worktree(target)


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.CalledProcessError(1, "git")])
def test_git_errors_fail_closed(optimizer, monkeypatch, tmp_path, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(optimizer, "_git", fail)
    with pytest.raises(SystemExit, match="Cannot verify"):
        optimizer._ensure_worktree(tmp_path)


def test_git_helper_checks_exit_status(optimizer, monkeypatch, tmp_path):
    def run(cmd, **kwargs):
        assert cmd == ["git", "-C", str(tmp_path), "status"]
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 60
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(optimizer.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        optimizer._git("status", cwd=tmp_path)


def test_revert_checks_identity_then_resets_tracked_changes(optimizer, checkout):
    target, _, state, calls = checkout
    state["dirty"] = " M attempted-fix.txt"
    optimizer._discard_fix(target, "before-sha", expected_branch="bench/auto-test")
    assert calls[-1] == (("reset", "--hard", "before-sha"), target)
    assert not any(args[0] == "status" for args, _ in calls)


@pytest.mark.parametrize("field,value", [
    ("common", "/other/repo"), ("branch", "main"), ("branch", "bench/auto-other"),
])
def test_revert_refuses_changed_target(optimizer, checkout, field, value):
    target, _, state, calls = checkout
    state[field] = value
    with pytest.raises(SystemExit):
        optimizer._discard_fix(target, "before-sha", expected_branch="bench/auto-test")
    assert not any(args[0] == "reset" for args, _ in calls)


def test_fixer_uses_default_permissions_and_stdin(optimizer, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(_claude_bin=lambda: "fake-claude"))

    def popen(cmd, **kwargs):
        assert "--dangerously-skip-permissions" not in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "default"
        assert cmd[cmd.index("--max-turns") + 1] == "80"
        assert kwargs["cwd"] == str(tmp_path)

        def communicate(*, input, timeout):
            assert "Commit your change" in input
            assert timeout == 17
            return '{"total_cost_usd": 1.25}', ""
        return SimpleNamespace(communicate=communicate)

    monkeypatch.setattr(optimizer.subprocess, "Popen", popen)
    assert optimizer._run_fixer(tmp_path, {}, "test-model", 17) == 1.25


def test_benchmark_agent_uses_default_permissions(optimizer, monkeypatch, tmp_path):
    agent = _load("agent")
    monkeypatch.setattr(agent, "_claude_bin", lambda: "fake-claude")
    monkeypatch.setattr(agent, "child_env", lambda: {})
    monkeypatch.setattr(agent, "_find_transcript", lambda *args: None)

    def popen(cmd, **kwargs):
        assert "--dangerously-skip-permissions" not in cmd
        assert cmd[cmd.index("--permission-mode") + 1] == "default"
        return SimpleNamespace(communicate=lambda **kwargs: ('{"result": "ok"}', ""))

    monkeypatch.setattr(agent.subprocess, "Popen", popen)
    result = agent.run_agent("fake prompt", tmp_path, "fake-run", model="fake", max_turns=2, timeout_s=3)
    assert result["result_text"] == "ok"


@pytest.mark.parametrize("changed", [None, "agent.py", "run.py", "missing"])
def test_harness_must_match_before_provider_launch(optimizer, monkeypatch, tmp_path, changed):
    source = tmp_path / "harness"
    target = tmp_path / "target"
    bench = target / "trellum" / "bench"
    source.mkdir()
    bench.mkdir(parents=True)
    for name in ("agent.py", "run.py"):
        (source / name).write_text("safe harness")
        (bench / name).write_text("safe harness")
    monkeypatch.setattr(optimizer, "_BENCH_DIR", source)
    if changed == "missing":
        (bench / "agent.py").unlink()
    elif changed:
        (bench / changed).write_text("old unsafe harness")
    if changed:
        with pytest.raises(SystemExit, match="harness"):
            optimizer._run_matrix(target, scenarios="all", guidance="pointer", repeat=1,
                                  label="test", model="fake", py="fake-python")
    else:
        optimizer._verify_harness(target)


def test_fixer_timeout_kills_fake_process(optimizer, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(_claude_bin=lambda: "fake-claude"))
    events = []

    def communicate(**kwargs):
        if kwargs:
            raise subprocess.TimeoutExpired("fake-claude", 1)
        events.append("drained")
        return "", ""

    proc = SimpleNamespace(communicate=communicate, kill=lambda: events.append("killed"))
    monkeypatch.setattr(optimizer.subprocess, "Popen", lambda *args, **kwargs: proc)
    assert optimizer._run_fixer(tmp_path, {}, "fake", 1) == 0
    assert events == ["killed", "drained"]


@pytest.mark.parametrize("limit", ["--budget-usd", "--max-hours"])
def test_exhausted_limit_stops_before_any_benchmark(optimizer, monkeypatch, tmp_path, limit):
    monkeypatch.setattr(sys, "argv", ["optimize.py", "--enable-optimizer", "--worktree",
                                     str(tmp_path), limit, "0"])
    monkeypatch.setattr(optimizer, "_ensure_worktree", lambda path: path)
    monkeypatch.setattr(optimizer, "_verify_harness", lambda path: None)
    monkeypatch.setattr(optimizer, "_git", lambda *args, **kwargs: "bench/auto-test")
    monkeypatch.setattr(optimizer, "_run_matrix", lambda *args, **kwargs: pytest.fail("budget exceeded"))
    assert optimizer.main() == 0


@pytest.mark.parametrize("path", ["trellum/AGENTS.md.extra", "trellum/README.md/child"])
def test_whitelist_files_require_exact_names(optimizer, path):
    assert not optimizer.path_allowed(path)


@pytest.fixture
def real_checkout(monkeypatch, tmp_path):
    module = _load("optimize")
    source = tmp_path / "source"
    source.mkdir()

    def git(*args, cwd=source):
        return subprocess.run(
            ["git", "-c", "user.name=Apollo Meijer", "-c",
             "user.email=1136912+hajimeku@users.noreply.github.com", "-C", str(cwd), *args],
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout.strip()

    git("init", "-b", "bench/auto-primary")
    (source / "trellum" / "docs").mkdir(parents=True)
    (source / "trellum" / "docs" / "guide.md").write_text("original")
    (source / "protected.txt").write_text("protected")
    git("add", ".")
    git("commit", "-m", "Initial fixture")
    target = tmp_path / "target"
    git("worktree", "add", "-b", "bench/auto-test", str(target))
    monkeypatch.setattr(module, "_REPO_DIR", source)
    return module, source, target, git


@pytest.mark.heavy
def test_real_git_checkout_and_revert_identity(real_checkout):
    module, source, target, git = real_checkout
    assert module._ensure_worktree(target) == target
    with pytest.raises(SystemExit, match="Refusing target"):
        module._ensure_worktree(source)
    with pytest.raises(SystemExit, match="Refusing target"):
        module._ensure_worktree(target / "trellum")
    before = git("rev-parse", "HEAD", cwd=target)
    protected = target / "protected.txt"
    protected.write_text("attempt")
    with pytest.raises(SystemExit, match="completely clean"):
        module._ensure_worktree(target)
    git("switch", "-c", "bench/auto-other", cwd=target)
    with pytest.raises(SystemExit, match="branch changed"):
        module._discard_fix(target, before, expected_branch="bench/auto-test")
    assert protected.read_text() == "attempt"
    git("switch", "bench/auto-test", cwd=target)
    module._discard_fix(target, before, expected_branch="bench/auto-test")
    assert protected.read_text() == "protected"


@pytest.mark.heavy
@pytest.mark.parametrize("committed", [False, True])
def test_real_git_violations_preserve_names_and_rename_sources(real_checkout, committed):
    module, _, target, git = real_checkout
    before = git("rev-parse", "HEAD", cwd=target)
    # Staged and committed renames must both flag the removed source.
    git("mv", "protected.txt", "trellum/docs/moved.md", cwd=target)
    if committed:
        git("commit", "-m", "Fixture rename", cwd=target)
    # A tracked modification used to lose its first filename character to strip().
    (target / "trellum/docs/guide.md").write_text("changed")
    names = ["trellum/docs/tab\tname.md", "trellum/docs/line\nname.md"]
    for name in names:
        (target / name).write_text("allowed")
    bad = "private\nnotes.txt"
    (target / bad).write_text("denied")
    assert module._violations(target, before) == [bad, "protected.txt"]
