"""Deployment state transitions without contacting Docker or a database."""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("deployment", Path(__file__).resolve().parents[3] / "scripts/deployment.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("KEEP=this\nTRELLUM_IMAGE=old\nTRELLUM_RUNNER_IMAGE=old-runner\n")
    for key in ("COMPOSE_PROFILES", "TRELLUM_TARGET_IMAGE", "TRELLUM_TARGET_RUNNER_IMAGE",
                "TRELLUM_REGISTRY", "TRELLUM_REPO"):
        monkeypatch.delenv(key, raising=False)
    d = deploy.Deployment()
    events = []
    failure = set()

    def config(env=None):
        pins = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if "=" in line)
        if env:
            pins.update({k: v for k, v in env.items() if k in ("TRELLUM_IMAGE", "TRELLUM_RUNNER_IMAGE")})
        env = {"DATABASE_URL": "postgres://fake:fake@db:5432/fake"}
        return {"web": {"image": pins["TRELLUM_IMAGE"], "environment": env},
                "migrate": {"image": pins["TRELLUM_IMAGE"], "environment": env},
                "worker": {"image": pins["TRELLUM_IMAGE"], "environment": dict(env, TRELLUM_SANDBOX="docker", TRELLUM_SANDBOX_IMAGE=pins["TRELLUM_RUNNER_IMAGE"])},
                "sandbox-image": {"image": pins["TRELLUM_RUNNER_IMAGE"]},
                "backup": {"environment": env},
                "db": {"environment": {"POSTGRES_USER": "fake", "POSTGRES_DB": "fake"}}}

    def c(*args, **kwargs):
        events.append(args)
        if args[0] in failure:
            raise deploy.DeploymentError("injected " + args[0])
        if args[:2] == ("ps", "-q"):
            return args[2] if args[2] in ("web", "worker") else ""
        if "pg_dump" in args:
            kwargs["output"].write(b"fake archive")
        if args[-1] == "import os; print(os.environ['TRELLUM_SANDBOX_IMAGE'])":
            return "sha256:old-runner"
        if args[-1] == "import trellum_portal; print(trellum_portal.__version__)":
            return "1.0.0"
        return ""

    def prepare(portal, runner, version=None):
        events.append(("prepare", portal, runner))
        if "prepare" in failure:
            raise deploy.DeploymentError("pull/version failed")
        return (portal, runner)

    monkeypatch.setattr(d, "config", config)
    monkeypatch.setattr(d, "c", c)
    monkeypatch.setattr(d, "prepare", prepare)
    monkeypatch.setattr(d, "image_id", lambda image: image)
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: "sha256:old-portal")
    def verify(*args):
        events.append(("verify",))
        if "verify" in failure:
            raise deploy.DeploymentError("unhealthy")
    monkeypatch.setattr(d, "verify", verify)
    return d, events, failure


def upgrade(d):
    d.execute(argparse.Namespace(action="upgrade", version="v1.1.0", yes=True))


def rollback(d, dump):
    d.execute(argparse.Namespace(action="rollback", dump=str(dump), yes=True))


def test_paired_defaults_and_custom_images(monkeypatch):
    for key in ("TRELLUM_REGISTRY", "TRELLUM_REPO", "TRELLUM_TARGET_IMAGE", "TRELLUM_TARGET_RUNNER_IMAGE"):
        monkeypatch.delenv(key, raising=False)
    assert deploy.targets("v1.2.3") == ("ghcr.io/trellumhq/trellum:v1.2.3", "ghcr.io/trellumhq/trellum-runner:v1.2.3")
    monkeypatch.setenv("TRELLUM_REGISTRY", "registry.local")
    monkeypatch.setenv("TRELLUM_REPO", "custom/app")
    assert deploy.targets("v1.2.3") == ("registry.local/custom/app:v1.2.3", "registry.local/custom/app-runner:v1.2.3")
    monkeypatch.setenv("TRELLUM_TARGET_IMAGE", "custom:portal")
    monkeypatch.setenv("TRELLUM_TARGET_RUNNER_IMAGE", "custom:runner")
    assert deploy.targets("v1.2.3") == ("custom:portal", "custom:runner")


def test_upgrade_and_restore_both_images(stack):
    d, events, _ = stack
    upgrade(d)
    dump = next(Path("upgrade-backups").glob("*.dump"))
    record = json.loads(Path(str(dump) + ".images.json").read_text())
    assert record["portal"] == "sha256:old-portal"
    assert record["runner"] == "sha256:old-runner"
    assert events.index(next(e for e in events if e[0] == "prepare")) < events.index(("stop", *d.services))
    assert events.index(("stop", *d.services)) < next(i for i, e in enumerate(events) if "pg_dump" in e)
    rollback(d, dump)
    assert "TRELLUM_IMAGE=sha256:old-portal" in Path(".env").read_text()
    assert "TRELLUM_RUNNER_IMAGE=sha256:old-runner" in Path(".env").read_text()
    assert "KEEP=this" in Path(".env").read_text()
    assert list(dump.parent.glob("*.before-rollback-*.dump"))
    assert not d.marker.exists()
    restore = next(e for e in events if "--exit-on-error" in e)
    assert "--exit-on-error" in restore and "--single-transaction" in restore


@pytest.mark.parametrize("fault", ["prepare", "run", "verify"])
def test_failures_do_not_retry_or_restart_mixed_release(stack, fault):
    d, events, failure = stack
    failure.add(fault)
    before = Path(".env").read_bytes()
    with pytest.raises(deploy.DeploymentError):
        upgrade(d)
    if fault == "prepare":
        assert Path(".env").read_bytes() == before
        assert not any(e[0] == "stop" for e in events)
    else:
        assert d.marker.exists()
        assert events[-1] == ("stop", *d.services)
        count = len(events)
        with pytest.raises(deploy.DeploymentError, match="incomplete"):
            upgrade(d)
        assert not any(e[0] == "run" for e in events[count:])
    assert not d.lock.exists()


def test_restore_failure_keeps_services_stopped(stack, monkeypatch):
    d, events, _ = stack
    upgrade(d)
    dump = next(Path("upgrade-backups").glob("*.dump"))
    original = d.c
    def fail_restore(*args, **kwargs):
        if "--exit-on-error" in args:
            raise deploy.DeploymentError("restore failed")
        return original(*args, **kwargs)
    monkeypatch.setattr(d, "c", fail_restore)
    with pytest.raises(deploy.DeploymentError, match="restore failed"):
        rollback(d, dump)
    assert d.marker.exists()
    assert events[-1] == ("stop", *d.services)


def test_corrupt_backup_refused_before_stop(stack):
    d, events, _ = stack
    upgrade(d)
    dump = next(Path("upgrade-backups").glob("*.dump"))
    dump.write_bytes(b"corrupt")
    events.clear()
    with pytest.raises(deploy.DeploymentError, match="hash"):
        rollback(d, dump)
    assert not any(e[0] == "stop" for e in events)


def test_external_database_excluded(stack):
    d, events, _ = stack
    config = d.config()
    for service in ("web", "worker", "migrate", "backup"):
        config[service]["environment"]["DATABASE_URL"] = "postgres://fake@external/fake"
    with pytest.raises(deploy.DeploymentError, match="External Postgres"):
        d.validate(config)
    assert not events


def test_target_version_failure_before_second_image_use(monkeypatch):
    d = deploy.Deployment()
    events = []
    def run(*args, **kwargs):
        events.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return "sha256:test"
        return "0.0.1" if args[:2] == ("docker", "run") else ""
    monkeypatch.setattr(deploy, "run", run)
    with pytest.raises(deploy.DeploymentError, match="differs"):
        d.prepare("portal:target", "runner:target", "v1.1.0")
    assert not any(e[:2] == ("docker", "stop") for e in events)


def test_runner_pull_failure_leaves_no_pins_changed(stack, monkeypatch):
    d, events, _ = stack
    # Exercise the real prepare loop: the second registry operation fails.
    monkeypatch.setattr(d, "prepare", deploy.Deployment.prepare.__get__(d))
    def run(*args, **kwargs):
        events.append(args)
        if args[:2] == ("docker", "pull") and "runner" in args[-1]:
            raise deploy.DeploymentError("runner pull failed")
        return "1.1.0"
    monkeypatch.setattr(deploy, "run", run)
    before = Path(".env").read_bytes()
    with pytest.raises(deploy.DeploymentError, match="runner pull failed"):
        upgrade(d)
    assert before == Path(".env").read_bytes()
    assert not any(e[0] == "stop" for e in events)


def test_backup_failure_blocks_migration(stack, monkeypatch):
    d, events, _ = stack
    def dump(*args):
        raise deploy.DeploymentError("disk full")
    monkeypatch.setattr(d, "dump", dump)
    with pytest.raises(deploy.DeploymentError, match="disk full"):
        upgrade(d)
    assert not any(e[0] in ("run", "up") for e in events)
    assert d.marker.exists()
    assert not list(Path("upgrade-backups").glob("*.images.json"))


def test_unconfirmed_restore_cannot_be_retried(stack, monkeypatch):
    d, events, _ = stack
    upgrade(d)
    dump = next(Path("upgrade-backups").glob("*.dump"))
    d.atomic_json(d.marker, {"action": "rollback"})
    events.clear()
    with pytest.raises(deploy.DeploymentError, match="unconfirmed"):
        rollback(d, dump)
    assert not any(e[0] in ("stop", "up", "prepare") for e in events)


def test_release_overlay_and_exported_pins_refused(stack, monkeypatch):
    d, events, _ = stack
    monkeypatch.setenv("TRELLUM_IMAGE", "exported:old")
    before = Path(".env").read_bytes()
    with pytest.raises(deploy.DeploymentError, match="overrides"):
        upgrade(d)
    assert before == Path(".env").read_bytes()
    assert not any(e[0] == "stop" for e in events)


@pytest.mark.parametrize("status", [{"schema": "pending", "version": "1.1.0"}, {"schema": "ok", "version": "1.0.0"}])
def test_serving_schema_and_version_must_match(stack, monkeypatch, status):
    d, _, _ = stack
    monkeypatch.setattr(d, "verify", deploy.Deployment.verify.__get__(d))
    monkeypatch.setattr(d, "c", lambda *args, **kwargs: json.dumps(status))
    d.health_timeout = 0
    with pytest.raises(deploy.DeploymentError, match="Version/schema"):
        d.verify(("portal", "runner"), "v1.1.0")


def test_running_portal_image_must_match(stack, monkeypatch):
    d, _, _ = stack
    monkeypatch.setattr(d, "verify", deploy.Deployment.verify.__get__(d))
    def c(*args, **kwargs):
        return "container" if args[0] == "ps" else json.dumps({"schema": "ok", "version": "1.1.0"})
    monkeypatch.setattr(d, "c", c)
    with pytest.raises(deploy.DeploymentError, match="web is not running"):
        d.verify(("target-portal", "target-runner"), "v1.1.0")


def test_lock_refuses_concurrent_deployment(stack):
    d, events, _ = stack
    d.lock.mkdir()
    with pytest.raises(deploy.DeploymentError, match="lock exists"):
        upgrade(d)
    assert not events


@pytest.mark.heavy
def test_real_postgres_restore_and_failed_swap(tmp_path, monkeypatch):
    """Real restore safety only; this does not prove a worker/sandbox upgrade."""
    import time
    import uuid

    monkeypatch.chdir(tmp_path)
    container = "trellum-upgrade-test-" + uuid.uuid4().hex
    database = "test_trellum_cleanup168review"
    d = deploy.Deployment()
    config = {"db": {"environment": {"POSTGRES_USER": "trellum", "POSTGRES_DB": database}}}

    original_run = deploy.run

    def transport(*args, **kwargs):
        prefix = tuple(d.compose)
        if args[:len(prefix)] == prefix:
            command = args[len(prefix):]
            assert command[:3] == ("exec", "-T", "db")
            return original_run("docker", "exec", "-i", container, *command[3:], **kwargs)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(deploy, "run", transport)
    c = d.c

    def sql(query, db=database):
        return c("exec", "-T", "db", "psql", "-U", "trellum", "-d", db,
                 "-At", "--set", "ON_ERROR_STOP=1", data=query.encode())

    try:
        deploy.run("docker", "run", "-d", "--name", container, "--memory", "512m", "--cpus", "1",
                   "-e", "POSTGRES_PASSWORD=disposable-test-only", "-e", "POSTGRES_USER=trellum",
                   "-e", f"POSTGRES_DB={database}", "postgres:16-alpine")
        deadline = time.monotonic() + 60
        while True:
            try:
                sql("SELECT 1;")
                break
            except deploy.DeploymentError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        sql("CREATE TABLE fixture(id integer PRIMARY KEY); INSERT INTO fixture VALUES(1);")
        dump = tmp_path / "before.dump"
        d.dump(dump, config)
        sql("CREATE TABLE new_release(id integer REFERENCES fixture(id)); "
            "INSERT INTO new_release VALUES(1); INSERT INTO fixture VALUES(2);")
        d.restore(dump, config, {})
        record = json.loads(d.marker.read_text())
        assert sql("SELECT count(*) FROM fixture;") == "1"
        assert sql("SELECT to_regclass('public.new_release') IS NULL;") == "t"
        assert sql("SELECT count(*) FROM fixture;", record["retained_database"]) == "2"
        assert sql("SELECT count(*) FROM new_release;", record["retained_database"]) == "1"
        sql("INSERT INTO fixture VALUES(3);")
        bad = tmp_path / "bad.dump"
        bad.write_bytes(b"invalid archive")
        with pytest.raises(deploy.DeploymentError):
            d.restore(bad, config, {})
        assert sql("SELECT count(*) FROM fixture;") == "2"

        def fail_swap(*args, **kwargs):
            if "psql" in args and "--single-transaction" in args:
                kwargs["data"] += b" SELECT 1/0;"
            return c(*args, **kwargs)

        monkeypatch.setattr(d, "c", fail_swap)
        with pytest.raises(deploy.DeploymentError):
            d.restore(dump, config, {})
        assert sql("SELECT count(*) FROM fixture;") == "2"
        pending = json.loads(d.marker.read_text())
        assert sql("SELECT count(*) FROM pg_database WHERE datname='" + pending["scratch_database"] + "';", "postgres") == "1"
        assert sql("SELECT count(*) FROM pg_database WHERE datname='" + pending["retained_database"] + "';", "postgres") == "0"
    finally:
        # This name is unique to this test. No shared daemon cleanup/pruning.
        deploy.run("docker", "rm", "-fv", container, timeout=60)


def test_active_migration_blocks_rollback(stack, monkeypatch):
    d, events, _ = stack
    upgrade(d)
    dump = next(Path("upgrade-backups").glob("*.dump"))
    d.atomic_json(d.marker, {"action": "upgrade", "migration_container": "fixture-migration"})
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: "running")
    events.clear()
    with pytest.raises(deploy.DeploymentError, match="termination is unconfirmed"):
        rollback(d, dump)
    assert not any(e[0] in ("stop", "up", "prepare") for e in events)


def test_failed_stop_blocks_backup_and_migration(stack):
    d, events, failure = stack
    failure.add("stop")
    with pytest.raises(deploy.DeploymentError, match="injected stop"):
        upgrade(d)
    assert not any(e[0] in ("run", "up") or "pg_dump" in e for e in events)
    assert d.marker.exists()


def test_worker_runner_configuration_must_match(stack, monkeypatch):
    d, _, _ = stack
    monkeypatch.setattr(d, "verify", deploy.Deployment.verify.__get__(d))
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: "target-portal")
    def c(*args, **kwargs):
        if args[0] == "ps":
            return "container"
        if args[-1] == "import os; print(os.environ['TRELLUM_SANDBOX_IMAGE'])":
            return "wrong-runner"
        return json.dumps({"schema": "ok", "version": "1.1.0"})
    monkeypatch.setattr(d, "c", c)
    with pytest.raises(deploy.DeploymentError, match="selected runner"):
        d.verify(("target-portal", "target-runner"), "v1.1.0")


def test_database_command_has_server_side_deadline(monkeypatch):
    observed = []
    monkeypatch.setattr(deploy, "run", lambda *args, **kwargs: observed.append((args, kwargs)))
    d = deploy.Deployment()
    d.c("exec", "-T", "db", "pg_restore", "--list")
    assert observed[0][0] == (*d.compose, "exec", "-T", "db", "timeout", "-s", "KILL", "895", "pg_restore", "--list")
    assert observed[0][1]["timeout"] == 900


def test_legacy_pair_requires_explicit_confirmation_before_downtime(stack):
    d, events, _ = stack
    Path("legacy.dump").write_bytes(b"archive")
    with pytest.raises(deploy.DeploymentError, match="Legacy dump requires"):
        rollback(d, "legacy.dump")
    assert not any(e[0] == "stop" for e in events)


def test_explicit_legacy_pair_uses_guarded_restore(stack):
    d, events, _ = stack
    Path("legacy.dump").write_bytes(b"archive")
    d.execute(argparse.Namespace(action="rollback", dump="legacy.dump", previous="v1.0.0",
                                runner_image="runner:old", confirm_legacy_pair=True, yes=True))
    assert ("prepare", "ghcr.io/trellumhq/trellum:v1.0.0", "runner:old") in events
    assert any("--single-transaction" in e and "pg_restore" in e for e in events)
    assert list(Path(".").glob("*.before-rollback-*.dump"))


def test_manifest_never_falls_back_to_legacy(stack):
    d, events, _ = stack
    Path("legacy.dump").write_bytes(b"archive")
    Path("legacy.dump.images.json").write_text("broken")
    with pytest.raises(ValueError):
        rollback(d, "legacy.dump")
    assert not any(e[0] == "stop" for e in events)


def test_prepare_checks_pair_even_without_requested_version(monkeypatch):
    d = deploy.Deployment()
    def run(*args, **kwargs):
        if args[:2] == ("docker", "run"):
            return "1.0.0" if "trellum_portal" in args[-1] else "2.0.0"
        return "sha256:test"
    monkeypatch.setattr(deploy, "run", run)
    with pytest.raises(deploy.DeploymentError, match="versions differ"):
        d.prepare("portal:old", "runner:old")


def test_invalid_deadlines_refused(monkeypatch):
    monkeypatch.setenv("TRELLUM_COMMAND_TIMEOUT", "0")
    with pytest.raises(deploy.DeploymentError, match="timeout"):
        deploy.Deployment()


@pytest.mark.parametrize("command", ["docker --context other compose", "docker compose --env-file other.env",
                                     "docker compose --project-directory /other"])
def test_alternate_daemon_or_pin_file_refused(stack, command):
    import shlex
    d, events, _ = stack
    d.compose = shlex.split(command)
    with pytest.raises(deploy.DeploymentError, match="calling shell"):
        upgrade(d)
    assert not events


@pytest.mark.heavy
def test_timeout_kills_compose_child_process(tmp_path):
    import sys
    import time
    sentinel = tmp_path / "child-survived"
    child = f"import time; from pathlib import Path; time.sleep(1); Path({str(sentinel)!r}).touch()"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
    with pytest.raises(deploy.DeploymentError, match="outcome unknown"):
        deploy.run(sys.executable, "-c", parent, timeout=0.3)
    time.sleep(1)
    assert not sentinel.exists()


def test_legacy_archive_validation_cannot_be_bypassed(stack, monkeypatch):
    d, events, _ = stack
    Path("legacy.dump").write_bytes(b"bad archive")
    def invalid(path):
        raise deploy.DeploymentError("invalid archive")
    monkeypatch.setattr(d, "verify_dump", invalid)
    with pytest.raises(deploy.DeploymentError, match="invalid archive"):
        d.execute(argparse.Namespace(action="rollback", dump="legacy.dump", previous="v1.0.0",
                                    runner_image="runner:old", confirm_legacy_pair=True, yes=True))
    assert not any(e[0] == "stop" for e in events)


@pytest.mark.heavy
def test_migration_options_accepted_by_compose(stack, monkeypatch):
    """Validate the emitted CLI against Compose without starting a container."""
    import shutil
    import subprocess
    if not shutil.which("docker"):
        pytest.skip("Docker CLI unavailable")
    d, _, _ = stack
    original = d.c
    def c(*args, **kwargs):
        if args[0] == "run":
            subprocess.run([*d.compose, "run", "--help", *args[1:]], check=True,
                           capture_output=True, timeout=10)
        return original(*args, **kwargs)
    monkeypatch.setattr(d, "c", c)
    upgrade(d)
