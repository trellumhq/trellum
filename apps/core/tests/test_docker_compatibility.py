"""Installation checks run without starting a deployment or needing a database."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
BASH = (
    "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
)


@pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason="Bash not installed")
@pytest.mark.parametrize("api,mode,ok", [
    ("1.43", "", False), ("1.44", "", False), ("1.45", "", True),
    ("1.43", "--bind-data", False), ("1.44", "--bind-data", True),
    ("1.45", "--bind-data", True), ("", "", False),
    ("1.44.0", "--bind-data", False), ("garbage", "", False),
    ("1.45", "--unknown", False),
])
def test_host_preflight(api, mode, ok):
    # Stub only the read-only daemon query, exercising the shipped Bash script.
    result = subprocess.run(
        [BASH, "-c", 'docker() { printf "%s\\n" "$TEST_API"; }; export -f docker; '
         'bash "$1" ${2:+"$2"}', "check", "scripts/check-docker.sh", mode],
        cwd=ROOT, env={**os.environ, "TEST_API": api}, capture_output=True, text=True,
        timeout=15,
    )
    assert (result.returncode == 0) is ok, result.stdout + result.stderr


@pytest.mark.skipif(not BASH or not Path(BASH).exists(), reason="Bash not installed")
def test_host_preflight_reports_unavailable_daemon():
    result = subprocess.run(
        [BASH, "-c", 'docker() { return 1; }; export -f docker; bash scripts/check-docker.sh'],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "Cannot reach the Docker Engine" in result.stderr


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI not installed")
@pytest.mark.parametrize("bind", [False, True])
def test_compose_data_mounts_preserve_service_boundaries(tmp_path, bind):
    env_file = tmp_path / "compose.env"
    env_file.write_text("POSTGRES_PASSWORD=test-only\nDOCKER_GID=0\n", encoding="utf-8")
    command = ["docker", "compose", "--env-file", str(env_file), "--profile", "split",
               "-f", "docker-compose.yml"]
    if bind:
        command += ["-f", "docker-compose.bind-data.yml"]
    command += ["config", "--format", "json"]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, "TRELLUM_DATA_HOST_PATH": str(tmp_path / "data"),
             "TRELLUM_DATA_VOLUME": "stale-volume"},
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    data_sources = set()
    for name in ("migrate", "web", "worker", "coordinator", "runner", "backup"):
        mounts = {m["target"]: m for m in services[name]["volumes"]}
        data = mounts["/data"]
        assert data["type"] == ("bind" if bind else "volume")
        assert bool(data.get("read_only")) is (name == "backup")
        data_sources.add(data["source"])
        assert mounts["/backups"]["type"] == "volume"
        assert bool(mounts["/backups"].get("read_only")) is (name != "backup")
        assert ("/var/run/docker.sock" in mounts) is (name in ("worker", "runner"))
        if bind:
            # Older Compose serializers omit false boolean fields.
            assert data.get("bind", {}).get("create_host_path", False) is False
            if name in ("worker", "runner"):
                assert services[name]["environment"]["TRELLUM_DATA_VOLUME"] == ""
                assert services[name]["environment"]["TRELLUM_SANDBOX"] == "docker"
    assert len(data_sources) == 1


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI not installed")
def test_release_compose_uses_pinned_images_without_build_and_keeps_overlays(tmp_path):
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "POSTGRES_PASSWORD=test-only\nDOCKER_GID=0\nTRELLUM_VERSION=v0.5.0\n"
        "TRELLUM_IMAGE=local/stale-portal:dev\nTRELLUM_RUNNER_IMAGE=local/stale-runner:dev\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    command = ["docker", "compose", "--env-file", str(env_file), "--profile", "split",
               "-f", "docker-compose.yml", "-f", "docker-compose.release.yml",
               "-f", "docker-compose.bind-data.yml", "-f", "docker-compose.external-db.yml",
               "config", "--format", "json"]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=30,
        env={**{k: v for k, v in os.environ.items() if not k.startswith("TRELLUM_")},
             "TRELLUM_DATA_HOST_PATH": str(data_dir),
             "DATABASE_URL": "postgres://test:test@db.example.test:5432/trellum"},
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    portal = "ghcr.io/trellumhq/trellum:v0.5.0"
    runner = "ghcr.io/trellumhq/trellum-runner:v0.5.0"
    for name in ("migrate", "web", "worker", "coordinator", "runner"):
        service = services[name]
        assert service["image"] == portal
        assert "build" not in service
        if name in ("worker", "runner"):
            assert service["environment"]["TRELLUM_SANDBOX_IMAGE"] == runner
        mounts = {m["target"]: m for m in service["volumes"]}
        assert mounts["/data"]["type"] == "bind"
        assert mounts["/data"]["source"] == str(data_dir)
        assert mounts["/backups"]["type"] == "volume"
        assert bool(mounts["/backups"].get("read_only"))
        assert ("/var/run/docker.sock" in mounts) is (name in ("worker", "runner"))
    sandbox = services["sandbox-image"]
    assert sandbox["image"] == runner
    assert "build" not in sandbox
    backup_mounts = {m["target"]: m for m in services["backup"]["volumes"]}
    assert backup_mounts["/data"]["type"] == "bind"
    assert backup_mounts["/data"]["source"] == str(data_dir)
    assert backup_mounts["/data"]["read_only"]
    assert backup_mounts["/backups"]["type"] == "volume"
    assert not backup_mounts["/backups"].get("read_only")


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI not installed")
@pytest.mark.parametrize("version", [None, ""])
def test_release_compose_requires_version(tmp_path, version):
    env_file = tmp_path / "compose.env"
    env_file.write_text("POSTGRES_PASSWORD=test-only\nDOCKER_GID=0\n", encoding="utf-8")
    test_env = {k: v for k, v in os.environ.items() if k != "TRELLUM_VERSION"}
    if version is not None:
        test_env["TRELLUM_VERSION"] = version
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "--profile", "split",
         "-f", "docker-compose.yml",
         "-f", "docker-compose.release.yml", "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, timeout=30, env=test_env,
    )
    assert result.returncode != 0
    assert "TRELLUM_VERSION" in result.stderr


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI not installed")
def test_base_compose_retains_source_builds(tmp_path):
    env_file = tmp_path / "compose.env"
    env_file.write_text("POSTGRES_PASSWORD=test-only\nDOCKER_GID=0\n", encoding="utf-8")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "--profile", "split",
         "-f", "docker-compose.yml",
         "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for name in ("migrate", "web", "worker", "coordinator", "runner", "sandbox-image"):
        assert services[name].get("build")
