"""Sandbox tests.

The unit tests drive DockerSandbox with a fake client so they assert the exact
container configuration — the security boundary is entirely in those kwargs and
mounts, so they are what must be pinned. A real-daemon smoke lives at the bottom
behind the ``docker_sandbox`` marker and skips when no daemon is present.
"""
from __future__ import annotations

import shlex
import sys
import types
from pathlib import Path
from uuid import uuid4

import pytest
import requests

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
            return types.SimpleNamespace(attrs={
                "Internal": True,
                "Options": {"com.docker.network.bridge.enable_icc": "false"},
            })
        raise docker.errors.ImageNotFound(name)

    def pull(self, name):
        self.pulled.append(name)
        return object()


class FakeNetworks:
    def __init__(self, exists=False, *, internal=True, icc="false", driver="bridge"):
        self.exists = exists
        self.internal = internal
        self.icc = icc
        self.driver = driver
        self.created: list = []

    def get(self, name):
        if self.exists:
            return types.SimpleNamespace(attrs={
                "Internal": self.internal,
                "Driver": self.driver,
                "Options": {"com.docker.network.bridge.enable_icc": self.icc},
            })
        raise docker.errors.NotFound(name)

    def create(self, name, **kwargs):
        self.created.append((name, kwargs))
        self.exists = True
        self.internal = kwargs["internal"]
        self.icc = kwargs["options"]["com.docker.network.bridge.enable_icc"]
        return self.get(name)


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
        # Bind mounts carry exact paths, never a broad /data mount or volume options.
        assert {m["Source"] for m in run["mounts"]} == {
            "/host/data/tmp/run-alpha-xyz",
            "/host/data/tmp/runs/rid-1",
            "/host/data/studios/org/studio/project",
            "/host/data/studios/org/studio/project/output/alpha",
        }
        for m in run["mounts"]:
            assert m["Type"] == "bind"
            assert "VolumeOptions" not in m
            assert m["Target"] != "/data"
        by_source = {m["Source"]: m for m in run["mounts"]}
        assert by_source["/host/data/studios/org/studio/project"]["ReadOnly"] is True
        assert by_source["/host/data/tmp/run-alpha-xyz"]["ReadOnly"] is False
        assert by_source["/host/data/tmp/runs/rid-1"]["ReadOnly"] is False
        assert by_source["/host/data/studios/org/studio/project/output/alpha"]["ReadOnly"] is False

    def test_outside_data_path_is_rejected(self, sbox_dir):
        client = FakeClient()
        outside = sbox_dir.parent / "outside-project"
        outside.mkdir()
        with pytest.raises(ValueError):
            _start(client, sbox_dir, project_root=str(outside))

    def test_symlink_cannot_escape_data_mount(self, sbox_dir):
        outside = sbox_dir.parent / "outside-shared"
        outside.mkdir()
        link = sbox_dir / "orgs" / "escape"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("platform cannot create directory symlinks")
        with pytest.raises(ValueError):
            _start(FakeClient(), sbox_dir, extra_ro_paths=[link])


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

    @pytest.mark.parametrize(
        ("failure", "exit_code", "oom_killed"),
        [
            pytest.param(requests.exceptions.ReadTimeout("slow"), 7, False, id="read-timeout"),
            pytest.param(requests.exceptions.ConnectionError("offline"), 7, False, id="connection-error"),
            pytest.param(docker.errors.APIError("daemon error"), 7, False, id="docker-api-error"),
            pytest.param(requests.exceptions.ReadTimeout("slow"), 137, True, id="oom-exit"),
        ],
    )
    def test_transient_inspection_failures_retry_and_recover(
        self, failure, exit_code, oom_killed, caplog
    ):
        class Flaky(FakeContainer):
            failures = 1

            def reload(self):
                if self.failures:
                    self.failures -= 1
                    raise failure

        c = Flaky(state={"Running": True}, labels={"trellum.run-id": "rid-1"})
        p = SandboxProc(c, run_id="rid-1")
        assert p.poll() is None
        assert p._exit is None
        assert p.poll() is None
        c.failures = 2
        assert p.poll() is None
        assert p.poll() is None
        c.failures = 0
        c.attrs["State"] = {
            "Running": False,
            "ExitCode": exit_code,
            "OOMKilled": oom_killed,
        }
        assert p.poll() == exit_code
        assert p.poll() == exit_code
        assert p.oom_killed is oom_killed
        warnings = [r for r in caplog.records if r.message.startswith("Sandbox container inspection failed")]
        recoveries = [r for r in caplog.records if r.message == "Sandbox container inspection recovered"]
        assert len(warnings) == 2
        assert all(r.error_type == type(failure).__name__ for r in warnings)
        assert all(r.run_id == "rid-1" and r.container_id == "c1" for r in warnings)
        assert len(recoveries) == 2
        assert all(r.run_id == "rid-1" and r.container_id == "c1" for r in recoveries)

    def test_not_found_is_terminal_and_logged(self, caplog):
        class Missing(FakeContainer):
            def reload(self):
                raise docker.errors.NotFound("gone")

        p = SandboxProc(Missing(), run_id="rid-2")
        assert p.poll() == 1
        assert p.poll() == 1
        assert caplog.records[-1].run_id == "rid-2"
        assert caplog.records[-1].reason == "container_missing"

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
    @pytest.mark.parametrize("egress", ["closed", "open"])
    def test_builtin_none_network_has_no_external_access(self, settings, egress):
        settings.TRELLUM_SANDBOX_EGRESS = egress
        network = types.SimpleNamespace(attrs={
            "Name": "none", "Driver": "null", "Internal": False, "Options": {},
        })
        DockerSandbox._validate_network(network, "none")

    @pytest.mark.parametrize("name,driver", [("none", "bridge"), ("host", "host"), ("other", "null")])
    def test_none_exception_requires_builtin_identity(self, settings, name, driver):
        settings.TRELLUM_SANDBOX_EGRESS = "closed"
        network = types.SimpleNamespace(attrs={
            "Name": name, "Driver": driver, "Internal": False, "Options": {},
        })
        with pytest.raises(SandboxError):
            DockerSandbox._validate_network(network, "none")

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

    @pytest.mark.parametrize("api,kind,accepted", [
        ("1.43", "volume", False), ("1.44", "volume", False),
        ("1.45", "volume", True), ("1.43", "bind", False),
        ("1.44", "bind", True), ("1.45", "bind", True),
    ])
    def test_api_version_depends_on_storage(self, sbox_dir, settings, api, kind, accepted):
        settings.TRELLUM_DATA_VOLUME = "" if kind == "bind" else "trellum-data"
        mounts = ([{"Destination": "/data", "Type": "bind", "Source": str(sbox_dir)}]
                  if kind == "bind" else None)
        client = FakeClient(api=api, self_mounts=mounts)
        if accepted:
            DockerSandbox(client=client).preflight()
        else:
            with pytest.raises(SandboxError, match="too old") as exc:
                DockerSandbox(client=client).preflight()
            assert "upgrade" in str(exc.value).lower()
            assert "install/docker-compose/" in str(exc.value)

    def test_old_bind_api_only_recommends_engine_upgrade(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""
        client = FakeClient(api="1.43", self_mounts=[
            {"Destination": "/data", "Type": "bind", "Source": str(sbox_dir)}
        ])
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).preflight()
        assert "Engine 25 or newer" in str(exc.value)
        assert "configure" not in str(exc.value).lower()

    @pytest.mark.parametrize("api", [None, "", "garbage", "1", "1.x", "1.44.0"])
    def test_unreadable_or_malformed_api_fails_closed(self, sbox_dir, api):
        client = FakeClient(api=api)
        with pytest.raises(SandboxError, match="cannot determine.*API version"):
            DockerSandbox(client=client).preflight()

    def test_api_lookup_error_fails_closed(self, sbox_dir):
        client = FakeClient()
        client.version = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        with pytest.raises(SandboxError, match="cannot determine.*API version"):
            DockerSandbox(client=client).preflight()

    def test_explicit_volume_override_uses_named_volume_minimum(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = "configured-volume"
        client = FakeClient(api="1.44", self_mounts=[
            {"Destination": "/data", "Type": "bind", "Source": str(sbox_dir)}
        ])
        with pytest.raises(SandboxError, match="named-volume"):
            DockerSandbox(client=client).preflight()

    def test_data_mount_discovery_failure_is_actionable(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""
        client = FakeClient(self_mounts=[])
        with pytest.raises(SandboxError, match="TRELLUM_DATA_VOLUME") as exc:
            DockerSandbox(client=client).preflight()
        assert "install/docker-compose/" in str(exc.value)

    def test_data_mount_inspection_error_is_actionable(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""
        client = FakeClient()
        client.containers.get = lambda name: (_ for _ in ()).throw(RuntimeError("denied"))
        with pytest.raises(SandboxError, match="could not inspect.*data mount"):
            DockerSandbox(client=client).preflight()

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
            return types.SimpleNamespace(attrs={
                "Internal": True,
                "Driver": "bridge",
                "Options": {"com.docker.network.bridge.enable_icc": "false"},
            })

        client.networks.get = _get
        DockerSandbox(client=client).ensure_network()  # must not raise

    @pytest.mark.parametrize("internal,icc", [(False, "false"), (True, "true"), (True, None)])
    def test_existing_network_mismatch_fails_closed(self, sbox_dir, internal, icc):
        client = FakeClient(network_exists=True)
        client.networks.internal = internal
        client.networks.icc = icc
        with pytest.raises(SandboxError, match="deliberately remove and recreate"):
            DockerSandbox(client=client).ensure_network()
        assert not client.networks.created

    def test_bridge_options_do_not_validate_other_drivers(self, sbox_dir):
        client = FakeClient(network_exists=True)
        client.networks.driver = "overlay"
        with pytest.raises(SandboxError):
            DockerSandbox(client=client).ensure_network()

    def test_no_data_mount_asks_for_override(self, sbox_dir, settings):
        settings.TRELLUM_DATA_VOLUME = ""
        client = FakeClient(self_mounts=[])  # no /data mount
        with pytest.raises(SandboxError) as exc:
            DockerSandbox(client=client).data_volume()
        assert "TRELLUM_DATA_VOLUME" in str(exc.value)


# ── orphan sweep ────────────────────────────────────────────────────────────
@pytest.mark.django_db
class TestSweepOrphans:
    def test_only_inactive_runs_removed(self, report_row, caplog):
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
        cleanup = [r for r in caplog.records if getattr(r, "reason", None) == "orphan_cleanup"]
        assert len(cleanup) == 1
        assert cleanup[0].run_id == gone_id
        assert cleanup[0].container_id == "orphan"

    def test_run_created_during_container_listing_is_kept(self, report_row):
        from apps.runner.models import Run

        gone_id = "00000000-0000-0000-0000-000000000000"
        orphan = FakeContainer(
            cid="orphan", labels={"trellum.sandbox": "1", "trellum.run-id": gone_id}
        )
        client = FakeClient(listing=[orphan])
        original_list = client.containers.list
        created = []

        def list_and_start_other_run(*args, **kwargs):
            if not created:
                run = Run.objects.create(
                    report=report_row, studio=report_row.studio,
                    slug=report_row.slug, status=Run.RUNNING,
                    worker_id="another-worker",
                )
                container = FakeContainer(
                    cid="new-live",
                    labels={"trellum.sandbox": "1", "trellum.run-id": str(run.pk)},
                )
                client.containers._listing.append(container)
                created.append(container)
            return original_list(*args, **kwargs)

        client.containers.list = list_and_start_other_run

        removed = DockerSandbox(client=client).sweep_orphans()

        assert removed == 1
        assert orphan.removed is True
        assert created[0].removed is False

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

    @pytest.mark.skipif(sys.platform != "linux", reason="bind mount probe requires Linux")
    def test_start_mounts_only_allowed_data_and_enforces_access(
        self, tmp_path, settings, monkeypatch
    ):
        """Run DockerSandbox's generated binds in a real hardened BusyBox container."""
        import docker
        from docker.models.containers import ContainerCollection

        client = docker.from_env()
        try:
            client.images.get("busybox:latest")
        except docker.errors.ImageNotFound:
            client.images.pull("busybox:latest")

        data = tmp_path / "data"
        settings.DATA_DIR = data
        settings.TRELLUM_SANDBOX_IMAGE = "busybox:latest"
        settings.TRELLUM_SANDBOX_NETWORK = "none"
        settings.TRELLUM_SANDBOX_PROJECT_RW = False
        settings.TRELLUM_JOB_MEMORY_HEADROOM = 1.5
        paths = _run_paths(data)
        project = Path(paths["project_root"])
        shared = data / "orgs" / "org" / "data-sources"
        sibling = data / "studios" / "org" / "sibling"
        repo = data / "repo"
        shared.mkdir(parents=True)
        sibling.mkdir(parents=True)
        repo.mkdir(parents=True)
        (project / "report.py").write_text("report")
        (shared / "shared.txt").write_text("shared")
        (sibling / "secret.txt").write_text("sibling-secret")
        (repo / "credentials.txt").write_text("repo-credential")
        (project / "escape").symlink_to(sibling / "secret.txt")
        (project / "relative-escape").symlink_to("../../sibling/secret.txt")

        # The daemon resolves sources; only the mounted directories need to be
        # accessible to uid 10001. Do not change permissions on host ancestors.
        for path in data.rglob("*"):
            if path.is_dir():
                path.chmod(0o777)
            elif not path.is_symlink():
                path.chmod(0o666)

        run_id = str(uuid4())
        q = lambda path: shlex.quote(str(path))
        probe = " && ".join([
            f"test -r {q(project / 'report.py')}",
            f"test -r {q(shared / 'shared.txt')}",
            f"test ! -e {q(sibling / 'secret.txt')}",
            f"test ! -e {q(repo / 'credentials.txt')}",
            f"test ! -e {q(project / 'escape')}",
            f"test ! -e {q(project / 'relative-escape')}",
            "test ! -e /var/run/docker.sock",
            f"! touch {q(project / 'ro-probe')} 2>/dev/null",
            f"! touch {q(shared / 'ro-probe')} 2>/dev/null",
            f"touch {q(Path(paths['output_dir']) / 'allowed-probe')}",
            f"touch {q(Path(paths['run_dir_base']) / 'allowed-probe')}",
            f"touch {q(Path(paths['log_dir']) / 'allowed-probe')}",
        ])
        original_run = ContainerCollection.run
        observed = {}

        def run_probe(collection, *args, **kwargs):
            if collection.client is client:
                observed["mounts"] = kwargs["mounts"]
                kwargs["image"] = "busybox:latest"
                kwargs["command"] = ["sh", "-c", probe]
            return original_run(collection, *args, **kwargs)

        monkeypatch.setattr(ContainerCollection, "run", run_probe)
        sandbox = DockerSandbox(client=client)
        monkeypatch.setattr(sandbox, "data_volume", lambda: ("bind", str(data)))
        proc = None
        try:
            sandbox.preflight()
            proc = sandbox.start(
                run=types.SimpleNamespace(id=run_id),
                cmd_flags=[], run_report_dir=paths["run_report_dir"],
                run_dir_base=paths["run_dir_base"], log_dir=paths["log_dir"],
                output_dir=paths["output_dir"], project_root=paths["project_root"],
                extra_ro_paths=[shared], env={"PATH": "/bin"},
                stdout_path=paths["stdout_path"], stderr_path=paths["stderr_path"],
                memory_mb=256, cpus=1.0,
            )
            container = client.containers.get(proc.container_id)
            result = container.wait(timeout=30)
            output = container.logs().decode(errors="replace")
            assert result["StatusCode"] == 0, output
            mounts = observed["mounts"]
            assert all(mount["Type"] == "bind" for mount in mounts)
            assert all("VolumeOptions" not in mount for mount in mounts)
            assert all(mount["Target"] != "/data" for mount in mounts)
            assert len(mounts) == 5
        finally:
            if proc is not None:
                proc.cleanup()
            client.close()
