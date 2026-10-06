"""Sandbox tests.

The unit tests drive DockerSandbox with a fake client so they assert the exact
container configuration — the security boundary is entirely in those kwargs and
mounts, so they are what must be pinned. A real-daemon smoke lives at the bottom
behind the ``docker_sandbox`` marker and skips when no daemon is present.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

import docker.errors

from apps.runner.sandbox import DockerSandbox, SandboxError, SandboxProc


# ── fakes ───────────────────────────────────────────────────────────────────
class FakeContainer:
    def __init__(self, cid="c1", state=None, labels=None):
        self.id = cid
        self.attrs = {"State": state if state is not None else {"Running": True}}
        self.labels = labels or {}
        self.killed: list = []
        self.removed = False

    def reload(self):
        pass

    def kill(self, signal=None):
        self.killed.append(signal or "SIGKILL")

    def remove(self, force=False):
        self.removed = True

    def stats(self, stream=False):
        return {"memory_stats": {"max_usage": 200 * 1024 * 1024}}


class FakeImages:
    def __init__(self, present=True):
        self.present = present
        self.pulled: list = []

    def get(self, name):
        if self.present:
            return object()
        raise docker.errors.ImageNotFound(name)

    def pull(self, name):
        self.pulled.append(name)
        return object()


class FakeNetworks:
    def __init__(self, exists=False):
        self.exists = exists
        self.created: list = []

    def get(self, name):
        if self.exists:
            return object()
        raise docker.errors.NotFound(name)

    def create(self, name, **kwargs):
        self.created.append((name, kwargs))
        self.exists = True
        return object()


class FakeContainers:
    def __init__(self, listing=None, self_mounts=None):
        self.runs: list = []
        self._listing = listing or []
        self._self_mounts = self_mounts
        self.last_run: FakeContainer | None = None

    def run(self, **kwargs):
        self.runs.append(kwargs)
        c = FakeContainer(cid=f"c{len(self.runs)}", labels=kwargs.get("labels"))
        self.last_run = c
        return c

    def list(self, all=False, filters=None):  # noqa: A002 - mirror docker API
        # Honour the label filter the way the daemon does. The sweep asks for
        # one label at a time, so a fake that ignores filters would report
        # every container as matching and hide a wrong filter entirely.
        label = (filters or {}).get("label")
        if not label:
            return list(self._listing)
        key, _, value = label.partition("=")
        return [c for c in self._listing if c.labels.get(key) == value]

    def get(self, name):
        return types.SimpleNamespace(attrs={"Mounts": self._self_mounts or []})


class FakeClient:
    def __init__(self, *, image=True, network_exists=False, listing=None,
                 self_mounts=None, api="1.45", ping_ok=True):
        self._ping_ok = ping_ok
        self._api = api
        self.images = FakeImages(present=image)
        self.networks = FakeNetworks(exists=network_exists)
        self.containers = FakeContainers(listing=listing, self_mounts=self_mounts)

    def ping(self):
        if not self._ping_ok:
            raise RuntimeError("cannot connect")
        return True

    def version(self):
        return {"ApiVersion": self._api}


@pytest.fixture
def sbox_dir(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    settings.TRELLUM_DATA_VOLUME = "trellum-data"
    settings.TRELLUM_SANDBOX_NETWORK = "trellum-sandbox"
    settings.TRELLUM_SANDBOX_IMAGE = "trellum-runner:dev"
    settings.TRELLUM_SANDBOX_PROJECT_RW = False
    settings.TRELLUM_JOB_MEMORY_HEADROOM = 1.5
    return tmp_path


def _run_paths(data: Path):
    run_dir_base = data / "tmp" / "run-alpha-xyz"
    log_dir = data / "tmp" / "runs" / "rid-1"
    project_root = data / "studios" / "org" / "studio" / "project"
    output_dir = project_root / "output" / "alpha"
    for p in (run_dir_base / "reports" / "alpha", log_dir, output_dir):
        p.mkdir(parents=True, exist_ok=True)
    return {
        "run_report_dir": str(run_dir_base / "reports" / "alpha"),
        "run_dir_base": str(run_dir_base),
        "log_dir": str(log_dir),
        "output_dir": str(output_dir),
        "project_root": str(project_root),
        "stdout_path": str(log_dir / "stdout.log"),
        "stderr_path": str(log_dir / "stderr.log"),
    }


def _start(client, data, **overrides):
    paths = _run_paths(data)
    kwargs = dict(
        run=types.SimpleNamespace(id="rid-1"),
        cmd_flags=["--no-serve", "--production"],
        env={"FW_PROJECT_ROOT": paths["project_root"], "PATH": "/usr/bin"},
        memory_mb=1024,
        cpus=1.0,
        **paths,
    )
    kwargs.update(overrides)
    return DockerSandbox(client=client).start(**kwargs), client.containers.runs[-1]


# ── container configuration ─────────────────────────────────────────────────
class TestStartConfig:
    def test_security_kwargs(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir)
        assert run["user"] == "10001:10001"
        assert run["cap_drop"] == ["ALL"]
        assert run["security_opt"] == ["no-new-privileges:true"]
        assert run["read_only"] is True
        assert run["init"] is True
        assert run["pids_limit"] == 256
        assert run["network"] == "trellum-sandbox"
        assert run["working_dir"] == "/app"
        assert run["tmpfs"] == {"/tmp": "rw,size=512m,mode=1777"}
        assert run["name"] == "trellum-run-rid-1"
        assert run["labels"] == {"trellum.sandbox": "1", "trellum.run-id": "rid-1"}

    def test_memory_cap_uses_headroom(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir, memory_mb=1024)
        # 1024 * 1.5 headroom
        assert run["mem_limit"] == "1536m"
        assert run["memswap_limit"] == "1536m"  # no swap
        assert run["nano_cpus"] == 1_000_000_000

    def test_memory_cap_floor(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir, memory_mb=100)
        assert run["mem_limit"] == "256m"

    def test_env_passthrough_plus_home(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir, env={"PATH": "/usr/bin"})
        env = run["environment"]
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/tmp"
        assert env["MPLCONFIGDIR"] == "/tmp"
        # The sandbox adds nothing secret; whatever it is handed is all there is.
        assert "DATABASE_URL" not in env

    def test_command_redirects_to_log_files(self, sbox_dir):
        client = FakeClient()
        paths = _run_paths(sbox_dir)
        _, run = _start(client, sbox_dir, **paths)
        cmd = run["command"]
        assert cmd[0:2] == ["/bin/sh", "-c"]
        joined = cmd[2]
        assert "python -m trellum.run" in joined
        assert "--no-serve" in joined
        assert paths["stdout_path"] in joined
        assert paths["stderr_path"] in joined


class TestStartMounts:
    def _mounts(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir)
        return run["mounts"]

    def test_four_mounts_and_project_is_readonly(self, sbox_dir):
        mounts = self._mounts(sbox_dir)
        assert len(mounts) == 4
        by_sub = {m.get("VolumeOptions", {}).get("Subpath"): m for m in mounts}
        project = by_sub["studios/org/studio/project"]
        assert project["ReadOnly"] is True
        # run dir, log dir, output are writable
        for sub in ("tmp/run-alpha-xyz", "tmp/runs/rid-1",
                    "studios/org/studio/project/output/alpha"):
            assert by_sub[sub]["ReadOnly"] is False

    def test_shared_org_files_are_mounted_read_only(self, sbox_dir):
        """An organization-level data file lives outside the studio project, so
        without its own mount the absolute path in config.yaml points at
        nothing inside the container."""
        shared = sbox_dir / "orgs" / "org" / "data-sources"
        shared.mkdir(parents=True)
        client = FakeClient()
        _, run = _start(client, sbox_dir, extra_ro_paths=[shared])
        by_sub = {m.get("VolumeOptions", {}).get("Subpath"): m for m in run["mounts"]}
        assert by_sub["orgs/org/data-sources"]["ReadOnly"] is True

    def test_no_extra_mounts_without_shared_files(self, sbox_dir):
        client = FakeClient()
        _, run = _start(client, sbox_dir, extra_ro_paths=[])
        assert len(run["mounts"]) == 4

    def test_never_mounts_repo_or_other_studio(self, sbox_dir):
        mounts = self._mounts(sbox_dir)
        for m in mounts:
            sub = m.get("VolumeOptions", {}).get("Subpath", "")
            assert "/repo" not in f"/{sub}"
            assert not sub.startswith("repo")
            assert m["Source"] == "trellum-data"  # never the docker socket, never /app

    def test_project_rw_valve(self, sbox_dir, settings):
        settings.TRELLUM_SANDBOX_PROJECT_RW = True
        client = FakeClient()
        _, run = _start(client, sbox_dir)
        by_sub = {m.get("VolumeOptions", {}).get("Subpath"): m for m in run["mounts"]}
        assert by_sub["studios/org/studio/project"]["ReadOnly"] is False

    def test_bind_backed_data_volume(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""  # force self-discovery
        client = FakeClient(self_mounts=[
            {"Destination": "/data", "Type": "bind", "Source": "/host/data"}
        ])
        # DATA_DIR is tmp_path; make the bind source match so relpaths resolve.
        settings.DATA_DIR = sbox_dir
        _, run = _start(client, sbox_dir)
        # bind mounts carry no VolumeOptions; sources are host paths under /host/data
        for m in run["mounts"]:
            assert m["Type"] == "bind"
            assert str(m["Source"]).startswith("/host/data")


# ── SandboxProc facade ──────────────────────────────────────────────────────
class TestSandboxProc:
    def test_poll_running_then_exit(self):
        c = FakeContainer(state={"Running": True})
        p = SandboxProc(c)
        assert p.poll() is None
        c.attrs["State"] = {"Running": False, "ExitCode": 0, "OOMKilled": False}
        assert p.poll() == 0
        assert p.oom_killed is False

    def test_poll_oom_latched(self):
        c = FakeContainer(state={"Running": False, "ExitCode": 137, "OOMKilled": True})
        p = SandboxProc(c)
        assert p.poll() == 137
        assert p.oom_killed is True

    def test_signals_map_to_kill(self):
        c = FakeContainer()
        p = SandboxProc(c)
        p.send_signal(15)
        p.kill()
        assert c.killed == ["SIGTERM", "SIGKILL"]

    def test_cleanup_swallows_errors(self):
        class Boom(FakeContainer):
            def remove(self, force=False):
                raise docker.errors.NotFound("gone")

        SandboxProc(Boom()).cleanup()  # must not raise

    def test_peak_rss_from_stats(self):
        assert SandboxProc(FakeContainer()).peak_rss_mb() == 200


# ── mode resolution ─────────────────────────────────────────────────────────
class TestSandboxMode:
    """A typo must not be a third, unsafe mode.

    Every caller branches on ``== "docker"``, so anything unrecognised used to
    select the host-subprocess path with only a boot warning — the wrong
    direction for a mistake to fail in.
    """

    @pytest.mark.parametrize("raw", ["docker", "DOCKER", " docker ", "Docker"])
    def test_docker_is_recognised_regardless_of_case_or_padding(self, settings, raw):
        from apps.runner.sandbox import sandbox_mode

        settings.DEBUG = False
        settings.TRELLUM_SANDBOX = raw
        assert sandbox_mode() == "docker"

    @pytest.mark.parametrize("raw", ["off", "OFF", " off "])
    def test_off_is_recognised(self, settings, raw):
        from apps.runner.sandbox import sandbox_mode

        settings.DEBUG = False
        settings.TRELLUM_SANDBOX = raw
        assert sandbox_mode() == "off"

    @pytest.mark.parametrize("raw", ["on", "true", "1", "yes", "sandbox", ""])
    def test_unrecognised_values_fail_closed(self, settings, raw):
        """Previously these all silently meant "run tenant code on the host"."""
        from django.core.exceptions import ImproperlyConfigured

        from apps.runner.sandbox import sandbox_mode

        settings.DEBUG = False
        settings.TRELLUM_SANDBOX = raw
        with pytest.raises(ImproperlyConfigured):
            sandbox_mode()

    def test_debug_stays_lenient(self, settings):
        """A dev box with a stray value should still run."""
        from apps.runner.sandbox import sandbox_mode

        settings.DEBUG = True
        settings.TRELLUM_SANDBOX = "nonsense"
        assert sandbox_mode() == "off"

    def test_the_health_check_reports_it(self, settings):
        """run_checks() never raises, so a bad mode has to surface as a failed
        check rather than a 500 on /system."""
        from apps.core.health import run_checks

        settings.DEBUG = False
        settings.TRELLUM_SANDBOX = "on"
        sandbox_check = next(
            c for c in run_checks() if c["label"] == "report sandbox"
        )
        assert sandbox_check["ok"] is False
        assert "not a valid isolation mode" in sandbox_check["detail"]


# ── preflight ───────────────────────────────────────────────────────────────
class TestPreflight:
    def test_happy_path_creates_network(self, sbox_dir):
        client = FakeClient(network_exists=False)
        DockerSandbox(client=client).preflight()
        assert client.networks.created
        name, kwargs = client.networks.created[0]
        assert name == "trellum-sandbox"
        assert kwargs["options"]["com.docker.network.bridge.enable_icc"] == "false"

    def test_ping_failure_is_actionable(self, sbox_dir):
        client = FakeClient(ping_ok=False)
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).preflight()
        assert "docker.sock" in str(exc.value) or "DOCKER_GID" in str(exc.value)

    def test_missing_local_image_names_the_build_command(self, sbox_dir):
        client = FakeClient(image=False)
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).preflight()
        assert "sandbox-image" in str(exc.value)

    def test_old_api_version_rejected(self, sbox_dir):
        client = FakeClient(api="1.43")
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).preflight()
        assert "Engine 26" in str(exc.value) or "too old" in str(exc.value)

    def test_network_create_race_tolerated(self, sbox_dir):
        client = FakeClient(network_exists=False)

        def _raise(name, **kwargs):
            raise docker.errors.APIError("exists")

        client.networks.create = _raise
        # get() will still 404 the first time, then the race handler re-gets;
        # make the second get succeed.
        calls = {"n": 0}

        def _get(name):
            calls["n"] += 1
            if calls["n"] == 1:
                raise docker.errors.NotFound(name)
            return object()

        client.networks.get = _get
        DockerSandbox(client=client).ensure_network()  # must not raise

    def test_no_data_mount_asks_for_override(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""
        client = FakeClient(self_mounts=[])  # no /data mount
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).data_volume()
        assert "TRELLUM_DATA_VOLUME" in str(exc.value)


# ── orphan sweep ────────────────────────────────────────────────────────────
@pytest.mark.django_db
class TestSweepOrphans:
    def test_only_inactive_runs_removed(self, report_row):
        from apps.runner.models import Run

        active = Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.RUNNING,
        )
        gone_id = "00000000-0000-0000-0000-000000000000"
        live = FakeContainer(cid="live", labels={"trellum.sandbox": "1", "trellum.run-id": str(active.id)})
        orphan = FakeContainer(cid="orphan", labels={"trellum.sandbox": "1", "trellum.run-id": gone_id})
        client = FakeClient(listing=[live, orphan])

        removed = DockerSandbox(client=client).sweep_orphans()
        assert removed == 1
        assert orphan.removed is True
        assert live.removed is False

    def test_containers_from_before_the_rename_are_still_swept(self, report_row):
        """A sandbox that outlived the upgrade wears the old label. Nothing
        else ever looks for it, so the sweep has to."""
        from apps.runner.models import Run

        active = Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.RUNNING,
        )
        gone_id = "00000000-0000-0000-0000-000000000000"
        legacy_live = FakeContainer(
            cid="legacy-live", labels={"bi.sandbox": "1", "bi.run-id": str(active.id)}
        )
        legacy_orphan = FakeContainer(
            cid="legacy-orphan", labels={"bi.sandbox": "1", "bi.run-id": gone_id}
        )
        current_orphan = FakeContainer(
            cid="orphan", labels={"trellum.sandbox": "1", "trellum.run-id": gone_id}
        )
        client = FakeClient(listing=[legacy_live, legacy_orphan, current_orphan])

        removed = DockerSandbox(client=client).sweep_orphans()
        assert removed == 2
        assert legacy_orphan.removed is True
        assert current_orphan.removed is True
        # A live run is live whichever label names it.
        assert legacy_live.removed is False


# ── real-daemon smoke ───────────────────────────────────────────────────────
def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.mark.docker_sandbox
@pytest.mark.skipif(not _docker_available(), reason="no Docker daemon")
class TestRealDaemon:
    def test_hardening_options_take_effect(self):
        """The security kwargs are only worth anything if the daemon honours
        them. Prove read-only rootfs, non-root uid, and cap drop against a real
        container (busybox — no framework image needed)."""
        import docker

        client = docker.from_env()
        client.images.pull("busybox:latest")

        # uid is enforced
        out = client.containers.run(
            "busybox:latest", ["id", "-u"], user="10001:10001", remove=True,
        )
        assert out.strip() == b"10001"

        # read-only rootfs rejects a write outside tmpfs
        logs = client.containers.run(
            "busybox:latest",
            ["sh", "-c", "echo hi > /root/x 2>&1 || echo READONLY"],
            user="10001:10001", read_only=True, cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"], remove=True,
        )
        assert b"READONLY" in logs
