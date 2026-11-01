"""Fail-closed, single-host online upgrades for the bundled Compose topology."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid


class DeploymentError(Exception):
    pass


def run(*args, data=None, stdin=None, output=None, env=None, timeout=300):
    try:
        result = subprocess.run(args, input=data, stdin=stdin, env=env, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        # Never retry writes: a timeout may mean the server committed the write.
        raise DeploymentError(f"Command failed or outcome unknown: {shlex.join(args)}\n{exc}") from exc
    return result.stdout.decode().strip() if result.stdout is not None else ""


class Deployment:
    services = ("web", "worker", "coordinator", "runner", "backup")

    def __init__(self):
        self.compose = shlex.split(os.environ.get("TRELLUM_COMPOSE", "docker compose"))
        self.health_timeout = int(os.environ.get("TRELLUM_HEALTH_TIMEOUT", "180"))
        self.command_timeout = int(os.environ.get("TRELLUM_COMMAND_TIMEOUT", "900"))
        self.marker = Path(".trellum-deployment-incomplete.json")
        self.lock = Path(".trellum-deployment-lock")
        self.stopped = False

    def c(self, *args, **kwargs):
        if args[:3] == ("exec", "-T", "db"):
            # Killing a Docker client does not stop an exec process. The bundled
            # Postgres image includes timeout; stop server-side work first.
            args = (*args[:3], "timeout", "-s", "KILL",
                    str(max(1, self.command_timeout - 5)), *args[3:])
        return run(*self.compose, *args, timeout=self.command_timeout, **kwargs)

    def config(self, env=None):
        return json.loads(self.c("config", "--format", "json", env=env))["services"]

    def check_pins(self, ids):
        env = dict(os.environ)
        env.setdefault("TRELLUM_IMAGE", ids[0])
        env.setdefault("TRELLUM_RUNNER_IMAGE", ids[1])
        if self.pins(self.config(env=env)) != ids:
            raise DeploymentError("Compose overrides image pins; release overlays/exported pins unsupported")

    def validate(self, config):
        # Reject overlays which could make us dump/restore a different database,
        # or start an unintended topology. Do not print the URL (credentials).
        required = {"web", "worker", "migrate", "sandbox-image", "db", "backup"}
        if not required.issubset(config):
            raise DeploymentError("Requires the bundled database and combined-worker Compose services")
        from urllib.parse import urlsplit
        urls = [config[s].get("environment", {}).get("DATABASE_URL", "")
                for s in ("web", "worker", "migrate", "backup")]
        db_env = config["db"].get("environment", {})
        if (len(set(urls)) != 1 or urlsplit(urls[0]).hostname != "db"
                or urlsplit(urls[0]).port not in (None, 5432)
                or urlsplit(urls[0]).path != "/" + db_env.get("POSTGRES_DB", "trellum_portal")
                or config["db"].get("profiles")
                or db_env.get("POSTGRES_DB") in ("postgres", "template0", "template1")):
            raise DeploymentError("External Postgres is unsupported by these helpers; use an operator restore procedure")
        if os.environ.get("COMPOSE_PROFILES") or "--profile" in self.compose:
            raise DeploymentError("Profiles/split topology are unsupported by these helpers")
        for service in ("coordinator", "runner"):
            if self.c("ps", "-q", service):
                raise DeploymentError("Stop split services through your operator procedure; combined worker only")
        if config["worker"].get("environment", {}).get("TRELLUM_SANDBOX") != "docker":
            raise DeploymentError("Requires Docker report sandboxing")

    def pins(self, config):
        portal = config["web"]["image"]
        runner = config["sandbox-image"]["image"]
        if any(config[s]["image"] != portal for s in ("worker", "migrate")):
            raise DeploymentError("Portal services select different images")
        if config["worker"]["environment"].get("TRELLUM_SANDBOX_IMAGE") != runner:
            raise DeploymentError("Worker runner image does not match sandbox-image")
        return portal, runner

    def image_id(self, image):
        return run("docker", "image", "inspect", "--format", "{{.Id}}", image)

    def prepare(self, portal, runner, version=None):
        ids = []
        for image, module in ((portal, "trellum_portal"), (runner, "trellum")):
            print(f"Preparing {module} image {image}", flush=True)
            # Recorded local immutable IDs need no registry lookup; other refs
            # must pull. This is not an offline-upgrade mode.
            if not image.startswith("sha256:"):
                run("docker", "pull", image, timeout=self.command_timeout)
            image_id = self.image_id(image)
            probe = "trellum-image-check-" + uuid.uuid4().hex
            try:
                got = run("docker", "run", "--rm", "--name", probe, "--network", "none",
                          "--memory", "512m", "--cpus", "1", "--entrypoint", "python", image_id,
                          "-c", f"import {module}; print({module}.__version__)")
            except BaseException:
                try:
                    run("docker", "rm", "-f", probe, timeout=60)
                except DeploymentError:
                    print(f"Image-check termination unconfirmed: inspect {probe}", file=sys.stderr)
                raise
            if version and got != version.removeprefix("v"):
                raise DeploymentError(f"{module} version {got} differs from requested {version}")
            ids.append(image_id)
        return tuple(ids)

    def atomic_json(self, path, value):
        temporary = path.with_name(path.name + ".tmp")
        with open(temporary, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)

    def pin(self, portal, runner):
        path = Path(".env")
        text = path.read_text()
        for key, value in (("TRELLUM_IMAGE", portal), ("TRELLUM_RUNNER_IMAGE", runner)):
            text = re.sub(rf"^{key}=.*\n?", "", text, flags=re.MULTILINE)
            text += f"\n{key}={value}\n"
        temp = path.with_name(".env.deployment-tmp")
        with open(temp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, path.stat().st_mode & 0o777)
        temp.replace(path)
        sync_directory(path.parent)
        if self.pins(self.config()) != (portal, runner):
            raise DeploymentError("Compose overrides .env image pins; remove exported pins/release overlays before recovery")

    def stop(self):
        self.stopped = True
        print("Stopping application writers and backup", flush=True)
        self.c("stop", *self.services)

    def dump(self, path, config):
        env = config["db"]["environment"]
        with open(path, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            self.c("exec", "-T", "db", "pg_dump", "-U", env["POSTGRES_USER"],
                   "-d", env["POSTGRES_DB"], "--format=custom", output=stream)
        self.verify_dump(path)

    def verify_dump(self, path):
        # Use the already-running bundled Postgres client; no extra image pull.
        with path.open("rb") as stream:
            self.c("exec", "-T", "db", "pg_restore", "--list", stdin=stream)

    def restore(self, dump, config, record):
        # Preserve the current database too, even when it may be partially
        # migrated. Never discard a restore error or restart on partial data.
        safety = Path(str(dump) + f".before-rollback-{time.time_ns()}.dump")
        self.dump(safety, config)
        env = config["db"]["environment"]
        suffix = uuid.uuid4().hex[:16]
        scratch = "trellum_restore_" + suffix
        retained = "trellum_retained_" + suffix
        self.atomic_json(self.marker, dict(record, action="rollback",
                                          scratch_database=scratch,
                                          retained_database=retained))
        self.atomic_json(Path(str(safety) + ".json"), {"retained_database": retained, "scratch_database": scratch, "restored_from": str(dump.resolve())})
        self.c("exec", "-T", "db", "createdb", "-U", env["POSTGRES_USER"],
               "--template", "template0", scratch)
        with dump.open("rb") as stream:
            self.c("exec", "-T", "db", "pg_restore", "-U", env["POSTGRES_USER"],
                   "-d", scratch, "--no-owner", "--no-acl",
                   "--exit-on-error", "--single-transaction", stdin=stream)
        # Restore into an empty DB so newer-release objects cannot survive.
        # Rename both databases transactionally from a third database.
        # Retain the current database as well as its verified safety dump.
        database = env["POSTGRES_DB"]
        identifier = lambda value: '"' + value.replace('"', '""') + '"'
        literal = lambda value: "'" + value.replace("'", "''") + "'"
        sql = ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
               f"WHERE datname IN ({literal(database)}, {literal(scratch)}) "
               "AND pid <> pg_backend_pid(); "
               f"ALTER DATABASE {identifier(database)} RENAME TO {identifier(retained)}; "
               f"ALTER DATABASE {identifier(scratch)} RENAME TO {identifier(database)};")
        self.c("exec", "-T", "db", "psql", "-U", env["POSTGRES_USER"],
               "-d", "postgres", "--single-transaction", "--set", "ON_ERROR_STOP=1",
               data=sql.encode())
        print(f"Pre-rollback database retained as {retained}; safety dump {safety}", flush=True)

    def verify(self, ids, version):
        deadline = time.monotonic() + self.health_timeout
        while True:
            try:
                raw = self.c("exec", "-T", "web", "python", "-c",
                             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/healthz', timeout=5).read(1024); print(urllib.request.urlopen('http://127.0.0.1:8050/api/version', timeout=5).read(65536).decode())")
                status = json.loads(raw)
                if status["schema"] != "ok" or status["version"] != version.removeprefix("v"):
                    raise DeploymentError("Serving version/schema differs from expected release")
                break
            except (DeploymentError, ValueError, KeyError):
                if time.monotonic() >= deadline:
                    raise DeploymentError("Version/schema verification timed out")
                time.sleep(2)
        for service in ("web", "worker"):
            container = self.c("ps", "-q", service)
            if not container or run("docker", "inspect", "--format", "{{.Image}}", container) != ids[0]:
                raise DeploymentError(f"{service} is not running the selected portal image")
        worker = self.c("exec", "-T", "worker", "python", "-c",
                        "import os; print(os.environ['TRELLUM_SANDBOX_IMAGE'])")
        if self.image_id(worker) != ids[1]:
            raise DeploymentError("Worker is not configured to launch the selected runner")
        self.c("exec", "-T", "web", "python", "manage.py", "doctor")

    def execute(self, args):
        if not Path(".env").is_file():
            raise DeploymentError(".env not found in deployment directory")
        try:
            self.lock.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise DeploymentError("Deployment lock exists; investigate previous operation before removing it") from exc
        try:
            self.perform(args)
        except BaseException:
            if self.stopped:
                # No automatic restore or restart after an unknown write outcome.
                try:
                    self.c("stop", *self.services)
                except DeploymentError:
                    print("WARNING: could not confirm all application services stopped", file=sys.stderr)
                print(f"RECOVERY REQUIRED: services left stopped; inspect {self.marker}. Do not retry upgrade.", file=sys.stderr)
            raise
        finally:
            self.lock.rmdir()

    def perform(self, args):
        config = self.config()
        self.validate(config)
        self.pins(config)
        if args.action == "upgrade":
            if self.marker.exists():
                raise DeploymentError(f"Previous deployment incomplete; recover using {self.marker}")
            portal, runner = targets(args.version)
            ids = self.prepare(portal, runner, args.version)
            # Confirm pins actually control every service BEFORE downtime.
            self.check_pins(ids)
            previous = []
            for service in ("web", "worker"):
                container = self.c("ps", "-q", service)
                if not container:
                    raise DeploymentError(f"{service} must be running before upgrade")
                previous.append(run("docker", "inspect", "--format", "{{.Image}}", container))
            if previous[0] != previous[1]:
                raise DeploymentError("Existing web/worker images differ; repair before upgrade")
            old_runner = self.c("exec", "-T", "worker", "python", "-c",
                                "import os; print(os.environ['TRELLUM_SANDBOX_IMAGE'])")
            old_version = self.c("exec", "-T", "web", "python", "-c",
                                 "import trellum_portal; print(trellum_portal.__version__)")
            old_ids = self.prepare(previous[0], self.image_id(old_runner), old_version)
            self.c("exec", "-T", "web", "python", "manage.py", "doctor")
            directory = Path(os.environ.get("TRELLUM_BACKUP_DIR", "upgrade-backups"))
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            dump = directory / ("pre-" + time.strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}.dump")
            record = {"portal": old_ids[0], "runner": old_ids[1], "version": old_version,
                      "previous_pins": self.pins(config), "dump": str(dump.resolve()),
                      "target_portal": ids[0], "target_runner": ids[1]}
        else:
            if self.marker.exists() and json.loads(self.marker.read_text()).get("action") == "rollback":
                raise DeploymentError("Previous rollback outcome is unconfirmed; inspect database and marker before any manual retry")
            if self.marker.exists():
                migration = json.loads(self.marker.read_text()).get("migration_container")
                if migration:
                    state = run("docker", "ps", "-a", "--filter", f"name=^/{migration}$",
                                "--format", "{{.State}}")
                    if state not in ("", "exited", "dead"):
                        raise DeploymentError(f"Migration {migration} termination is unconfirmed; inspect before restoring")
            dump = Path(args.dump)
            record = json.loads(Path(str(dump) + ".images.json").read_text())
            if str(dump.resolve()) != record["dump"]:
                raise DeploymentError("Backup manifest belongs to a different dump path")
            if file_hash(dump) != record["dump_sha256"]:
                raise DeploymentError("Dump hash differs from backup manifest")
            ids = self.prepare(record["portal"], record["runner"], record["version"])
            self.verify_dump(dump)
            self.check_pins(ids)
        if not args.yes:
            prompt = "Proceed? [y/N] " if args.action == "upgrade" else "DISCARDS database writes since backup. Type ROLLBACK: "
            if input(prompt) != ("y" if args.action == "upgrade" else "ROLLBACK"):
                raise DeploymentError("Aborted")
        self.atomic_json(self.marker, dict(record, action=args.action))
        self.stop()
        if args.action == "upgrade":
            self.dump(dump, config)
            record["dump_sha256"] = file_hash(dump)
            self.atomic_json(Path(str(dump) + ".images.json"), record)
            print(f"Backup: {dump}; rollback: scripts/rollback.sh {shlex.quote(str(dump))}", flush=True)
            self.pin(*ids)
            migration = "trellum-migration-" + uuid.uuid4().hex
            self.atomic_json(self.marker, dict(record, action="upgrade", migration_container=migration))
            try:
                self.c("run", "--rm", "-T", "--name", migration, "--no-deps", "--no-build", "--pull", "never", "migrate")
            except BaseException:
                # Killing the CLI does not stop a container-side migration.
                try:
                    run("docker", "rm", "-f", migration, timeout=60)
                except DeploymentError:
                    print(f"Migration termination unconfirmed: inspect {migration} before restoring", file=sys.stderr)
                raise
            version = args.version
        else:
            self.restore(dump, config, record)
            self.pin(*ids)
            version = record["version"]
        # No builds, registry pulls or implicit migration reruns during startup.
        self.c("up", "-d", "--no-deps", "--no-build", "--pull", "never", "sandbox-image", "web", "worker", "backup")
        self.verify(ids, version)
        self.marker.unlink()
        sync_directory(self.marker.parent)
        self.stopped = False
        print(f"{args.action} verified: portal {ids[0]}, runner {ids[1]}; backup {dump}")
        print("Run a report and verify its sandbox image before accepting report-building health.")


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def targets(version):
    if not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise DeploymentError("Expected a release version, e.g. v1.2.3")
    registry = os.environ.get("TRELLUM_REGISTRY", "ghcr.io")
    repo = os.environ.get("TRELLUM_REPO", "trellumhq/trellum")
    images = (os.environ.get("TRELLUM_TARGET_IMAGE", f"{registry}/{repo}:{version}"),
            os.environ.get("TRELLUM_TARGET_RUNNER_IMAGE", f"{registry}/{repo}-runner:{version}"))
    if any(image.startswith("sha256:") for image in images):
        raise DeploymentError("Upgrade targets require registry references; offline local-ID upgrades are unsupported")
    return images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    upgrade = sub.add_parser("upgrade")
    upgrade.add_argument("version")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("dump", help="dump with adjacent .images.json manifest from upgrade")
    for command in (upgrade, rollback):
        command.add_argument("--yes", action="store_true")
    try:
        Deployment().execute(parser.parse_args())
    except (DeploymentError, OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
