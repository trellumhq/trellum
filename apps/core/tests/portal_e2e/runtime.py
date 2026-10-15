"""Owned PostgreSQL/Gunicorn runtime with independent journey snapshots."""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import uuid

ROOT = Path(__file__).resolve().parents[4]
SETTINGS = "apps.core.tests.portal_e2e.settings"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def checked(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def safe_env():
    allowed = {"PATH", "LD_LIBRARY_PATH", "PLAYWRIGHT_BROWSERS_PATH", "LANG", "LC_ALL", "HOME", "PORTAL_E2E_SQL_DELAY_MS"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def relocate_files(directory, old_root, new_root):
    """Seed-local absolute references must never write into another unit."""
    old, new = str(old_root).encode(), str(new_root).encode()
    for path in Path(directory).rglob("*"):
        if path.is_symlink():
            raise RuntimeError("seed snapshot contains a symlink")
        if not path.is_file():
            continue
        data = path.read_bytes()
        if old in data:
            # Path replacement in binary Git objects would corrupt the repo.
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError("binary seed file contains absolute path") from exc
            path.write_bytes(data.replace(old, new))


def relocate_database(connection, old_root, new_root):
    from psycopg import sql
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name,column_name,udt_name FROM information_schema.columns WHERE table_schema='public' AND udt_name IN ('text','varchar','json','jsonb')")
        columns = cursor.fetchall()
        for table, column, kind in columns:
            statement = sql.SQL("UPDATE {} SET {}=replace({}::text,%s,%s)::{} WHERE strpos({}::text,%s)>0").format(
                sql.Identifier(table), sql.Identifier(column), sql.Identifier(column), sql.SQL(kind), sql.Identifier(column))
            cursor.execute(statement, (str(old_root), str(new_root), str(old_root)))


@dataclass
class Unit:
    name: str
    database: str
    data_dir: Path
    env: dict
    server: subprocess.Popen
    log: object

    @property
    def origin(self):
        return self.env["PORTAL_E2E_ORIGIN"]


class Runtime:
    def __init__(self, artifacts, *, browser="chromium", reports=20, members=3,
                 studios=1, metrics=20, schedules=1, rules=1):
        self.artifacts = Path(artifacts)
        self.browser = browser
        self.counts = (reports, members, studios, metrics, schedules, rules)
        self.container = None
        self.container_created = False
        self.temporary = None
        self.units = {}
        self.attached = False
        self.session_salt = uuid.uuid4().hex
        from .coverage import source_digest
        from .bindings import binding_digest
        self.source = source_digest(ROOT)
        self.binding_hash = binding_digest()

    def db_url(self, name):
        return f"postgres://postgres:disposable@127.0.0.1:{self.dbport}/{name}"

    def environment(self, database, directory, *, origin=None, work_log=None):
        return dict(safe_env(), DATABASE_URL=self.db_url(database),
                    DJANGO_SETTINGS_MODULE=SETTINGS, PORTAL_E2E_DATA_DIR=str(directory),
                    PORTAL_E2E_STATIC_ROOT=str(self.static_root),
                    PORTAL_E2E_WORK_LOG=str(work_log or self.artifacts / "work.jsonl"),
                    PORTAL_E2E_ARTIFACTS=str(self.artifacts),
                    PORTAL_E2E_ORIGIN=origin or "http://127.0.0.1:1",
                    PORTAL_E2E_BROWSER=self.browser, PORTAL_E2E_REAL_HASHER="1",
                    PORTAL_E2E_SOURCE_DIGEST=self.source, PORTAL_E2E_BINDING_DIGEST=self.binding_hash)

    def __enter__(self):
        if (ROOT / ".env").exists():
            raise RuntimeError("Refusing checkout with .env")
        self.temporary = tempfile.TemporaryDirectory(prefix="portal-e2e-")
        self.root = Path(self.temporary.name)
        self.template_data = self.root / "template-data"
        self.template_data.mkdir()
        self.static_root = self.root / "static"
        self.dbport = free_port()
        self.template_db = "portal_template"
        self.container = "portal-e2e-" + uuid.uuid4().hex[:12]
        try:
            checked(["docker", "run", "--rm", "-d", "--name", self.container,
                     "-e", "POSTGRES_PASSWORD=disposable", "-e", f"POSTGRES_DB={self.template_db}",
                     "-p", f"127.0.0.1:{self.dbport}:5432", "postgres:16-alpine"], stdout=subprocess.DEVNULL)
            self.container_created = True
            for _ in range(120):
                ready = subprocess.run(["docker", "exec", self.container, "pg_isready", "-U", "postgres"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ready.returncode == 0:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError("PostgreSQL readiness timeout")
            env = self.environment(self.template_db, self.template_data)
            with (self.artifacts / "setup.log").open("w") as log:
                for args in (["migrate", "--noinput"], ["makemigrations", "--check", "--dry-run"], ["collectstatic", "--noinput"]):
                    checked([sys.executable, "manage.py", *args], env=env, stdout=log, stderr=subprocess.STDOUT)
                reports, members, studios, metrics, schedules, rules = self.counts
                code = ("import django; django.setup(); from apps.core.tests.portal_e2e.seed import seed,enrich,seed_manifest; "
                        f"seed({reports},{members},{studios}); enrich({metrics},{schedules},{rules}); "
                        "import json; from pathlib import Path; "
                        f"Path({str(self.artifacts / 'seed.json')!r}).write_text(json.dumps(seed_manifest(),indent=2))")
                checked([sys.executable, "-c", code], env=env, stdout=log, stderr=subprocess.STDOUT)
            # Only the owner can spawn clones; pytest receives this local private
            # descriptor rather than a portable database credential artifact.
            self.descriptor = self.root / "runtime.json"
            self.descriptor.write_text(json.dumps({"root": str(self.root), "template_data": str(self.template_data),
                "static_root": str(self.static_root), "dbport": self.dbport, "template_db": self.template_db,
                "artifacts": str(self.artifacts), "browser": self.browser}))
            self.descriptor.chmod(0o600)
            return self
        except BaseException:
            self.close()
            raise

    @classmethod
    def attach(cls, descriptor):
        data = json.loads(Path(descriptor).read_text())
        runtime = cls(data["artifacts"], browser=data["browser"])
        runtime.attached = True
        for key in ("root", "template_data", "static_root"):
            setattr(runtime, key, Path(data[key]))
        runtime.dbport = data["dbport"]
        runtime.template_db = data["template_db"]
        runtime.descriptor = Path(descriptor)
        return runtime

    @contextmanager
    def unit(self, label, *, legacy_baseline=False, worker=False):
        import psycopg
        from psycopg import sql
        name = "unit_" + uuid.uuid4().hex[:16]
        directory = self.root / name
        server = None
        log = None
        created = False
        runner = None
        try:
            with psycopg.connect(self.db_url("postgres"), autocommit=True) as db:
                db.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(self.template_db)))
            created = True
            shutil.copytree(self.template_data, directory)
            relocate_files(directory, self.template_data, directory)
            with psycopg.connect(self.db_url(name), autocommit=True) as db:
                relocate_database(db, self.template_data, directory)
            origin = f"http://127.0.0.1:{free_port()}"
            env = self.environment(name, directory, origin=origin)
            env['PORTAL_E2E_SESSION_SALT'] = self.session_salt
            if legacy_baseline:
                baseline_directory = self.artifacts / 'baseline'
                baseline_directory.mkdir()
                env['PORTAL_E2E_PRIVATE_BASELINE_LOG'] = str(baseline_directory / 'work.jsonl')
            env["PORTAL_E2E_RUNTIME"] = str(self.descriptor)
            log = (self.artifacts / f"{name}-gunicorn.log").open("w")
            # Match deployed WSGI defaults, including the 4094-byte request line.
            # One synchronous worker makes cache state deterministic per unit.
            server = subprocess.Popen([sys.executable, "-m", "gunicorn", "trellum_portal.wsgi:application",
                "--bind", origin.removeprefix("http://"), "--workers", "1"], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            unit = Unit(label, name, directory, env, server, log)
            self.units[name] = unit
            for _ in range(160):
                if server.poll() is not None:
                    raise RuntimeError("Gunicorn exited; see unit log")
                try:
                    with urlopen(origin + "/healthz", timeout=1):
                        pass
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError("Gunicorn readiness timeout")
            if worker:
                # A runner-role worker claims report runs and maintenance actions
                # for this unit only; it never starts the cron coordinator.
                runner = subprocess.Popen([sys.executable, "manage.py", "runworker", "--role=runner"], cwd=ROOT, env=env,
                    stdout=log, stderr=subprocess.STDOUT)
            yield unit
        finally:
            if runner is not None:
                runner.terminate()
                try:
                    runner.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    runner.kill()
                    runner.wait(timeout=5)
            if server is not None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            if log is not None:
                log.close()
            self.units.pop(name, None)
            if created:
                with psycopg.connect(self.db_url("postgres"), autocommit=True) as db:
                    db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            shutil.rmtree(directory, ignore_errors=True)

    def close(self):
        if self.units:
            raise RuntimeError("live unit contexts must close before runtime")
        if self.container and self.container_created:
            checked(["docker", "rm", "-f", self.container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.container = None
            self.container_created = False
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None

    def __exit__(self, *exc):
        self.close()
