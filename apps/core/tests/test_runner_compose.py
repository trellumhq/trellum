"""Render the standalone runner deployment without contacting a Docker daemon."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(not shutil.which("docker"), reason="Docker CLI not installed")


def render(tmp_path, *, missing=None, empty=False, pools="standard,small"):
    values = {
        "TRELLUM_VERSION": "v0.5.0",
        "DATABASE_URL": "postgres://test:test@shared.example.test:5432/trellum",
        "SECRET_KEY": "test-only-signing",
        "SECRET_ENCRYPTION_KEY": "test-only-encryption",
        "TRELLUM_DATA_HOST_PATH": str(tmp_path / "shared-data"),
        "DOCKER_GID": "123",
        "TRELLUM_RUNNER_POOLS": pools,
        # Stale per-host settings must not select another role, image or volume.
        "TRELLUM_RUNNER_ROLE": "all",
        "TRELLUM_IMAGE": "stale:dev",
        "TRELLUM_RUNNER_IMAGE": "stale-runner:dev",
        "TRELLUM_DATA_VOLUME": "stale-local-data",
        "TRELLUM_DATA_DIR": "/wrong",
        "TRELLUM_SANDBOX": "off",
        "DOCKER_HOST": "tcp://wrong.example.test:2375",
        "DOCKER_CONTEXT": "wrong",
    }
    if missing:
        if empty:
            values[missing] = ""
        else:
            values.pop(missing)
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    # No inherited deployment credentials or Compose options enter the fixture.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TRELLUM_", "COMPOSE_", "SECRET_"))
           and k not in ("DATABASE_URL", "DOCKER_GID")}
    return subprocess.run(
        ["docker", "compose", "--project-directory", str(tmp_path),
         "--env-file", str(env_file), "-f", str(ROOT / "docker-compose.runner.yml"),
         "config", "--format", "json"],
        env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("pools", ["", "large", "standard,small"])
def test_runner_preset_is_standalone_and_uses_local_sandbox(tmp_path, pools):
    result = render(tmp_path, pools=pools)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert not config.get("volumes")
    services = config["services"]
    assert set(services) == {"runner", "sandbox-image"}
    runner, sandbox = services["runner"], services["sandbox-image"]
    assert runner["image"] == "ghcr.io/trellumhq/trellum:v0.5.0"
    assert runner["command"] == ["runner"]
    assert not runner.get("ports") and not runner.get("container_name")
    assert runner["group_add"] == ["123"]
    assert runner["stop_grace_period"] == "10m0s"
    assert set(runner["depends_on"]) == {"sandbox-image"}
    assert runner["depends_on"]["sandbox-image"]["condition"] == "service_completed_successfully"
    environment = runner["environment"]
    assert environment["TRELLUM_RUNNER_ROLE"] == "runner"
    assert environment["TRELLUM_RUNNER_POOLS"] == pools
    assert environment["TRELLUM_SANDBOX"] == "docker"
    assert environment["DOCKER_HOST"] == "unix:///var/run/docker.sock"
    assert environment["DOCKER_CONTEXT"] == ""
    assert environment["TRELLUM_DATA_DIR"] == "/data"
    assert environment["TRELLUM_DATA_VOLUME"] == ""
    assert environment["DATABASE_URL"].endswith("@shared.example.test:5432/trellum")
    assert environment["TRELLUM_SANDBOX_IMAGE"] == sandbox["image"] == "ghcr.io/trellumhq/trellum-runner:v0.5.0"
    assert sandbox["entrypoint"] == ["/bin/true"]
    assert sandbox["restart"] == "no"
    for service in services.values():
        assert "build" not in service
        assert service["logging"]["options"] == {"max-file": "5", "max-size": "10m"}
    mounts = {mount["target"]: mount for mount in runner["volumes"]}
    assert set(mounts) == {"/data", "/var/run/docker.sock"}
    assert mounts["/data"]["type"] == "bind"
    assert mounts["/data"]["source"] == str(tmp_path / "shared-data")
    assert not mounts["/data"].get("read_only")
    assert not mounts["/data"].get("bind", {}).get("create_host_path", False)
    assert mounts["/var/run/docker.sock"]["source"] == "/var/run/docker.sock"


@pytest.mark.parametrize("missing", [
    "TRELLUM_VERSION", "DATABASE_URL", "SECRET_KEY", "SECRET_ENCRYPTION_KEY",
    "TRELLUM_DATA_HOST_PATH", "DOCKER_GID",
])
@pytest.mark.parametrize("empty", [False, True])
def test_runner_preset_refuses_missing_deployment_values(tmp_path, missing, empty):
    result = render(tmp_path, missing=missing, empty=empty)
    assert result.returncode != 0
    assert missing in result.stderr
