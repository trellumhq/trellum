"""Container-per-run sandbox for tenant report code.

Report code (``reports/<slug>/generator.py``) is arbitrary Python authored
outside the portal's trust boundary. Running it as a subprocess of the worker —
same uid, same PID namespace, same view of the ``/data`` volume — means it can
read the worker's environment (``/proc/<pid>/environ`` → ``DATABASE_URL``, the
encryption key), every other studio's files, and reach the internal Postgres.

In ``docker`` mode each build instead runs in a throwaway sibling container:

    * non-root, read-only rootfs, all capabilities dropped, no-new-privileges
    * only that run's directories mounted (its sandbox copy rw, the studio
      project ro, its output dir rw) — never the git checkout, never another
      studio, never the docker socket
    * memory / cpu / pids limited
    * on a bridge network segregated from the compose-internal network, so
      stolen warehouse credentials cannot reach Postgres or the web tier

The worker holds the Docker socket; tenant code never does. ``SandboxProc`` is
a ``subprocess.Popen``-shaped facade so the Executor's monitor loop (poll,
send_signal, kill, the timeout ladder, peak-memory sampling) is unchanged.
"""
from __future__ import annotations

import shlex
import socket
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.core.docs import docs_url

#: Every sandbox container carries this label so orphans can be swept.
SANDBOX_LABEL = "trellum.sandbox"
RUN_ID_LABEL = "trellum.run-id"

#: What the previous release wrote. Only the orphan sweep reads these: a
#: container that outlived the upgrade still wears the old label, and nothing
#: else will ever come looking for it.
LEGACY_SANDBOX_LABEL = "bi.sandbox"
LEGACY_RUN_ID_LABEL = "bi.run-id"

#: Minimum Docker Engine API for `volume-subpath` mounts (Engine 26 / 1.45).
_MIN_API = (1, 45)


class SandboxError(RuntimeError):
    """A sandbox could not be prepared or started. The message is written for
    an operator (what to mount / build / set), not for a tenant."""


#: The only two isolation modes. Anything else is a configuration error, not a
#: third behaviour.
SANDBOX_DOCKER = "docker"
SANDBOX_OFF = "off"
_SANDBOX_MODES = (SANDBOX_DOCKER, SANDBOX_OFF)


def sandbox_mode() -> str:
    """The configured isolation mode, normalised, failing closed.

    Every caller branches on ``== "docker"``, so an unrecognised value used to
    select the host-subprocess path silently: ``TRELLUM_SANDBOX=Docker``, ``=on``,
    ``=true`` or a trailing space all ran tenant code unsandboxed in the worker
    container, announced by nothing louder than a boot warning. That is the
    wrong direction for a typo to fail in — the whole point of the setting is
    that the unsafe mode must be chosen deliberately.

    Case and surrounding whitespace are forgiven because they carry no intent.
    Anything else raises, which surfaces as a failed boot on a runner, a failed
    run in the executor, and a red `report sandbox` check in doctor / /system.
    DEBUG stays lenient so a dev box with a stray value still runs.
    """
    raw = getattr(settings, "TRELLUM_SANDBOX", SANDBOX_OFF)
    mode = str(raw).strip().lower()
    if mode in _SANDBOX_MODES:
        return mode
    if settings.DEBUG:
        return SANDBOX_OFF
    raise ImproperlyConfigured(
        f"TRELLUM_SANDBOX={raw!r} is not a valid isolation mode. "
        f"Use 'docker' (production) or 'off' (development only — tenant report "
        f"code then runs unsandboxed in the worker container)."
    )


def _rel_to_data(path) -> str:
    """Path relative to DATA_DIR, as forward-slash POSIX (the sandbox is
    Linux). Raises if the path escapes DATA_DIR — a guard against ever
    mounting something outside the data volume."""
    data = Path(settings.DATA_DIR).resolve()
    rel = Path(path).resolve().relative_to(data)
    return rel.as_posix()


# ── the Popen-shaped facade ─────────────────────────────────────────────────
class SandboxProc:
    """Duck-types the slice of ``subprocess.Popen`` the Executor touches:
    ``pid``, ``poll()``, ``send_signal()``, ``kill()``. Adds ``oom_killed``,
    ``peak_rss_mb()`` and ``cleanup()`` which the Executor uses opportunistically
    via ``getattr``."""

    pid = None  # containers have no host pid; read_peak_rss_mb(None) is a no-op

    def __init__(self, container):
        self._c = container
        self.container_id = container.id
        self._exit: int | None = None
        self._oom = False

    def poll(self) -> int | None:
        if self._exit is not None:
            return self._exit
        try:
            self._c.reload()
        except Exception:  # noqa: BLE001 - treat a vanished container as done
            self._exit = 1
            return self._exit
        state = self._c.attrs.get("State", {})
        if state.get("Running"):
            return None
        self._oom = bool(state.get("OOMKilled"))
        try:
            self._exit = int(state.get("ExitCode", 1))
        except (TypeError, ValueError):
            self._exit = 1
        return self._exit

    def send_signal(self, sig) -> None:
        # The Executor only ever sends SIGTERM here; map anything to a graceful
        # stop. Raise OSError on failure, which is what the loop already swallows.
        try:
            self._c.kill(signal="SIGTERM")
        except Exception as exc:  # noqa: BLE001
            raise OSError(str(exc)) from exc

    def kill(self) -> None:
        try:
            self._c.kill()
        except Exception as exc:  # noqa: BLE001
            raise OSError(str(exc)) from exc

    @property
    def oom_killed(self) -> bool:
        return self._oom

    def peak_rss_mb(self) -> int | None:
        """Best-effort peak memory from container stats. None on any failure —
        the field is informational (shown on failures), never load-bearing."""
        try:
            stats = self._c.stats(stream=False)
            mem = stats.get("memory_stats", {})
            peak = mem.get("max_usage") or mem.get("peak") or mem.get("usage")
            return int(peak) // (1024 * 1024) if peak else None
        except Exception:  # noqa: BLE001
            return None

    def cleanup(self) -> None:
        try:
            self._c.remove(force=True)
        except Exception:  # noqa: BLE001 - already gone is success
            pass


# ── the sandbox controller ──────────────────────────────────────────────────
class DockerSandbox:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    # ── boot-time checks ─────────────────────────────────────────────────
    def preflight(self) -> None:
        """Verify the daemon, image, data volume and network are usable.
        Raises ``SandboxError`` with an operator-actionable message; the worker
        turns that into a fail-closed exit rather than run tenant code loose."""
        try:
            self.client.ping()
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(
                "[SANDBOX] cannot reach the Docker daemon "
                f"({type(exc).__name__}: {exc}). Mount /var/run/docker.sock into "
                "the worker/runner service and set DOCKER_GID to the host docker "
                "group id (getent group docker). See "
                f"{docs_url('install/docker-compose/')}."
            ) from exc

        self._check_api_version()
        self._check_image()
        self.data_volume()          # raises if /data can't be resolved
        self.ensure_network()

    def _check_api_version(self) -> None:
        try:
            api = self.client.version().get("ApiVersion", "")
            parts = tuple(int(x) for x in api.split("."))
        except Exception:  # noqa: BLE001 - if we can't read it, don't block
            return
        if parts and parts < _MIN_API:
            raise SandboxError(
                f"[SANDBOX] Docker Engine API {api} is too old; need "
                f"{_MIN_API[0]}.{_MIN_API[1]}+ (Engine 26+) for volume-subpath "
                "mounts. Upgrade Docker on this host."
            )

    def _check_image(self) -> None:
        import docker.errors

        image = settings.TRELLUM_SANDBOX_IMAGE
        try:
            self.client.images.get(image)
            return
        except docker.errors.ImageNotFound:
            pass
        # Only try a pull for registry-qualified refs; a bare local tag
        # (trellum-runner:dev) can only come from a local build.
        if "/" in image.split(":")[0]:
            try:
                self.client.images.pull(image)
                return
            except Exception as exc:  # noqa: BLE001
                raise SandboxError(
                    f"[SANDBOX] runner image {image!r} is not present and could "
                    f"not be pulled ({exc})."
                ) from exc
        raise SandboxError(
            f"[SANDBOX] runner image {image!r} is not present. Build it with "
            "`docker compose build sandbox-image` (or set TRELLUM_SANDBOX_IMAGE)."
        )

    def data_volume(self) -> tuple[str, str]:
        """Resolve what backs ``/data`` so a run's directories can be mounted
        into the sibling container by subpath. Returns ``("volume", name)`` or
        ``("bind", host_path)``."""
        override = settings.TRELLUM_DATA_VOLUME
        if override:
            return ("volume", override)
        try:
            me = self.client.containers.get(socket.gethostname())
            for m in me.attrs.get("Mounts", []):
                if m.get("Destination") == "/data":
                    if m.get("Type") == "volume":
                        return ("volume", m["Name"])
                    if m.get("Type") == "bind":
                        return ("bind", m["Source"])
        except Exception:  # noqa: BLE001 - fall through to the actionable error
            pass
        raise SandboxError(
            "[SANDBOX] could not discover the Docker volume backing /data. "
            "Set TRELLUM_DATA_VOLUME to the volume name (docker volume ls)."
        )

    def ensure_network(self) -> None:
        import docker.errors

        name = settings.TRELLUM_SANDBOX_NETWORK
        try:
            self.client.networks.get(name)
            return
        except docker.errors.NotFound:
            pass
        from docker.types import IPAMConfig, IPAMPool

        try:
            self.client.networks.create(
                name,
                driver="bridge",
                # internal=True is the default: report code is arbitrary tenant
                # Python, and decrypted warehouse credentials are injected into
                # it as TRELLUM_DS_*. With outbound access those credentials — and
                # every row the report reads — can leave the building, and the
                # only thing that stood between a sandbox and the cloud metadata
                # endpoint was a firewall script nobody was required to run.
                #
                # A report that legitimately needs a third-party API should
                # declare it as an API data source, so the allowlist derives
                # from configuration rather than from a blanket door. Until that
                # connector exists, TRELLUM_SANDBOX_EGRESS=open is the escape hatch,
                # and it is a deliberate, visible choice.
                internal=settings.TRELLUM_SANDBOX_EGRESS != "open",
                ipam=IPAMConfig(pool_configs=[IPAMPool(subnet="172.30.100.0/24")]),
                # Sandboxes must not talk to each other either.
                options={"com.docker.network.bridge.enable_icc": "false"},
            )
        except docker.errors.APIError:
            # A racing runner created it between our get and create; re-get.
            self.client.networks.get(name)

    # ── spawn ────────────────────────────────────────────────────────────
    def start(
        self,
        *,
        run,
        cmd_flags: list[str],
        run_report_dir: str,
        run_dir_base: str,
        log_dir: str,
        output_dir: str,
        project_root,
        extra_ro_paths: list | None = None,
        env: dict,
        stdout_path: str,
        stderr_path: str,
        memory_mb: int,
        cpus: float,
    ) -> SandboxProc:
        import os

        from docker.types import Mount

        os.makedirs(output_dir, exist_ok=True)

        kind, source = self.data_volume()

        def _mount(target, read_only):
            rel = _rel_to_data(target)
            if kind == "volume":
                # docker-py's Mount() has no `subpath` kwarg yet; the Engine
                # API takes it under VolumeOptions.Subpath (Engine 26+). Mount
                # is a dict, so inject it directly.
                m = Mount(
                    target=str(target), source=source, type="volume",
                    read_only=read_only,
                )
                m.setdefault("VolumeOptions", {})["Subpath"] = rel
                return m
            return Mount(
                target=str(target),
                # `source` is a host path the Docker Engine resolves on the
                # Linux host; join it POSIX-style so a runner on a Windows
                # dev box doesn't emit backslashes into a Linux mount source.
                source=str(PurePosixPath(source) / rel),
                type="bind",
                read_only=read_only,
            )

        project_ro = not settings.TRELLUM_SANDBOX_PROJECT_RW
        mounts = [
            _mount(run_dir_base, read_only=False),
            _mount(log_dir, read_only=False),
            _mount(str(Path(project_root).resolve()), read_only=project_ro),
            # Nested rw over the (usually) ro project mount: Docker orders
            # mounts by destination depth, so output stays writable.
            _mount(output_dir, read_only=False),
        ]
        # Data the studio may read but does not own — today, the organization's
        # shared data-source files, which live outside any studio project.
        # Read-only: a build gets at the bytes, never at replacing them.
        for extra in extra_ro_paths or []:
            mounts.append(_mount(str(Path(extra).resolve()), read_only=True))

        # The child appends to the very log files the web UI tails; keeping the
        # redirect in the shell means no streaming threads and identical
        # behaviour across a worker restart.
        inner = (
            f"exec python -m trellum.run {shlex.quote(run_report_dir)} "
            f"{' '.join(shlex.quote(f) for f in cmd_flags)} "
            f">> {shlex.quote(stdout_path)} 2>> {shlex.quote(stderr_path)}"
        )

        child_env = dict(env)
        # Read-only rootfs + a nobody-style user: anything writing to $HOME
        # (matplotlib config, etc.) must be pointed at the tmpfs.
        child_env.setdefault("HOME", "/tmp")
        child_env.setdefault("MPLCONFIGDIR", "/tmp")

        mem_cap_mb = max(256, int(memory_mb * settings.TRELLUM_JOB_MEMORY_HEADROOM))

        try:
            container = self.client.containers.run(
                image=settings.TRELLUM_SANDBOX_IMAGE,
                command=["/bin/sh", "-c", inner],
                name=f"trellum-run-{run.id}",
                labels={SANDBOX_LABEL: "1", RUN_ID_LABEL: str(run.id)},
                environment=child_env,
                working_dir="/app",  # bundled source wins sys.path
                user="10001:10001",
                read_only=True,
                tmpfs={"/tmp": "rw,size=512m,mode=1777"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                network=settings.TRELLUM_SANDBOX_NETWORK,
                mem_limit=f"{mem_cap_mb}m",
                memswap_limit=f"{mem_cap_mb}m",  # == mem_limit → no swap
                nano_cpus=int(cpus * 1_000_000_000),
                pids_limit=256,
                init=True,  # reap tenant subprocess zombies
                mounts=mounts,
                detach=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(
                f"[SANDBOX] failed to start container for run {run.id}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return SandboxProc(container)

    # ── housekeeping ─────────────────────────────────────────────────────
    def sweep_orphans(self) -> int:
        """Remove sandbox containers with no live Run. Several runners share
        one daemon, so an unconditional sweep would kill another runner's
        in-flight builds — only containers whose run is no longer ACTIVE go."""
        from apps.runner.models import Run

        active = {
            str(pk)
            for pk in Run.objects.filter(status__in=Run.ACTIVE_STATUSES).values_list(
                "pk", flat=True
            )
        }
        removed = 0
        containers = []
        # LEGACY_* too: a container started by the previous release carries the
        # old label, and the sweep is the only thing that will ever remove it.
        # Filtering on the new label alone would strand those on the host until
        # someone noticed by hand.
        seen: set[str] = set()
        for label in (SANDBOX_LABEL, LEGACY_SANDBOX_LABEL):
            try:
                found = self.client.containers.list(
                    all=True, filters={"label": f"{label}=1"}
                )
            except Exception:  # noqa: BLE001
                return 0
            for c in found:
                if c.id not in seen:
                    seen.add(c.id)
                    containers.append(c)
        for c in containers:
            run_id = c.labels.get(RUN_ID_LABEL) or c.labels.get(LEGACY_RUN_ID_LABEL)
            if run_id in active:
                continue
            try:
                c.remove(force=True)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
        return removed
