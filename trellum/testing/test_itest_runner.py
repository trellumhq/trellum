"""Integration-harness startup hands Docker's assigned SSH port to pytest."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "itest" / "run.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("itest_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ensure_ssh_keypair", lambda: None)
    monkeypatch.setattr(module, "_check_docker_available", lambda: True)
    monkeypatch.setattr(sys, "argv", [str(path), "--engines", "postgres_ssh"])
    monkeypatch.delenv("ITEST_SSH_PORT", raising=False)
    return module


@pytest.mark.parametrize("configured", [None, "0", "52223"])
def test_managed_ssh_uses_docker_port_in_test_client(runner, monkeypatch, configured):
    if configured is not None:
        monkeypatch.setenv("ITEST_SSH_PORT", configured)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"].copy()))
        if "port" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "0.0.0.0:49173\n[::]:49173\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 0
    assert calls[0][1]["ITEST_SSH_PORT"] == (configured or "0")
    clients = [env for cmd, env in calls if "pytest" in cmd]
    assert len(clients) == 1
    assert clients[0]["ITEST_SSH_PORT"] == "49173"
    assert "down" in calls[-1][0]


@pytest.mark.parametrize("failure", ["empty", "invalid", "malformed", "command", "timeout"])
def test_missing_ssh_mapping_stops_tests_and_cleans_up(runner, monkeypatch, failure):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "port" in cmd:
            assert kwargs["timeout"] == 30
            if failure == "command":
                raise subprocess.CalledProcessError(1, cmd)
            if failure == "timeout":
                raise subprocess.TimeoutExpired(cmd, 30)
            outputs = {"empty": "", "invalid": "0.0.0.0:0\n", "malformed": "unpublished\n"}
            return subprocess.CompletedProcess(cmd, 0, outputs[failure])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 1
    assert not any("pytest" in cmd for cmd in calls)
    assert "down" in calls[-1]


def test_no_docker_preserves_external_ssh_port(runner, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run.py", "--no-docker", "--engines", "postgres_ssh"])
    monkeypatch.setenv("ITEST_SSH_PORT", "52223")
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert "pytest" in cmd
        assert kwargs["env"]["ITEST_SSH_PORT"] == "52223"
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 0
    assert len(calls) == 1
