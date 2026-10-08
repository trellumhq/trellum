"""Report subprocess execution — the sacred pipeline, ported from
``portal/jobs.py``.

Invariants preserved verbatim from the legacy JobManager (and covered by
tests, because they are the whole product):

- Builds run ``[sys.executable, -m, trellum.run, <sandbox report dir>,
  --no-serve, ...]`` with ``cwd`` = this repository's root, so THIS
  repository's ``trellum/`` wins ``sys.path`` over any ``trellum/`` inside
  the served project (for ``python -m``, cwd is ``sys.path[0]`` and beats
  PYTHONPATH).
- ``FW_PROJECT_ROOT`` is passed absolute, always.
- The report dir is copied into a per-run sandbox (git sync cannot touch an
  in-flight build), including ``reports/_*`` shared-helper siblings.
- Timeout ladder: SIGTERM at ``TRELLUM_RUN_TIMEOUT`` seconds, SIGKILL 5 s later.
- On failure, ``trellum.meta.write_error`` puts the error where the
  registry (and the UI) will find it.

New here: state lives on the ``Run`` row instead of in-memory dicts, live
logs live under ``<DATA_DIR>/tmp/runs/<run id>/`` so the web containers can
tail them, and the child environment is built from an ALLOWLIST — report
code is arbitrary tenant code and must never see portal secrets.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.utils import timezone as dj_tz

from apps.runner.models import Run
from apps.runner.sandbox import DockerSandbox, sandbox_mode

logger = logging.getLogger("trellum.runner")

#: Project-root files the framework reads while a report builds, and that the
#: portal must both sync into the studio project and stage into each run's
#: sandbox copy. config.yaml carries the repo's declared `theme:` (synced by
#: apps.reports.scan.sync_studio_registry into Studio.repo_theme, and staged
#: here so trellum.project.load_project_config() sees it during the build --
#: see trellum.themes.resolve_theme's project-config fallback); its
#: `extensions:` block, if a repo happens to also declare one, is always
#: shadowed by FW_EXTENSIONS_JSON below (trellum.project.load_extensions
#: checks the env var first), so the portal's own extensions injection can
#: never be clobbered by a synced file. Data sources are still materialized
#: separately, not copied.
PROJECT_ROOT_BUILD_FILES = ("metrics.yaml", "events.yaml", "config.yaml")


def project_root_materialized_files() -> tuple[str, ...]:
    """Every project-root file the portal puts into a studio's project.

    The build files above, plus the assistant's briefing. Both ways a
    project arrives -- ``apps.runner.gitsync`` for a git-backed studio and
    ``import_project`` for a seeded one -- must materialize the same set,
    or a file works on one path and silently does not exist on the other.
    That has already happened once: the assistant's original context
    paths were read from a project root nothing ever wrote them to.

    Note the asymmetry this deliberately preserves: everything here is
    *materialized*, but only ``PROJECT_ROOT_BUILD_FILES`` is *staged into
    the build sandbox*. No build reads the assistant's file.
    """
    from apps.assistant.system_prompt import PROJECT_CONTEXT_FILE

    return (*PROJECT_ROOT_BUILD_FILES, PROJECT_CONTEXT_FILE)


_CHEVRON_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6">'
    '</polyline></svg>'
)

# The lattice mark in monochrome (currentColor) so it sits on any report
# theme's header color — the report header is the bridge surface.
_LATTICE_SVG = (
    '<svg viewBox="0 0 40 40" width="15" height="15" aria-hidden="true" '
    'style="vertical-align:-2px;margin-right:2px">'
    '<g stroke="currentColor" stroke-width="2.6" opacity="0.45">'
    '<line x1="8" y1="8" x2="8" y2="32"/><line x1="20" y1="8" x2="20" y2="32"/>'
    '<line x1="32" y1="8" x2="32" y2="32"/></g>'
    '<circle cx="8" cy="32" r="4.4" fill="currentColor"/>'
    '<circle cx="20" cy="20" r="4.4" fill="currentColor" opacity="0.75"/>'
    '<circle cx="32" cy="8" r="4.4" fill="currentColor"/></svg>'
)

#: Environment variables a report subprocess may inherit from the worker.
#: Everything else is dropped: DATABASE_URL, SESSION_SECRET_KEY,
#: SECRET_ENCRYPTION_KEY and friends must never reach tenant code.
ENV_ALLOWLIST = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
    "LANG", "LANGUAGE", "TZ", "PYTHONIOENCODING", "PYTHONUTF8",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}
ENV_ALLOW_PREFIXES = ("LC_",)


def breadcrumb_nav_html(org, studio) -> str:
    """The trellum breadcrumb injected into report headers: lattice glyph,
    org (→ hub), studio (→ dashboard). The studio link keeps the
    ``fw-back-link`` class — it is the host-contract canary the e2e suite
    asserts on, and the framework already styles it as a translucent pill
    that sits on any header color."""
    import html as _html

    org_name = _html.escape(org.name)
    studio_name = _html.escape(studio.name)
    return (
        f"{_LATTICE_SVG}"
        # /?org=<slug>, never a bare "/": the hub opens whatever org the
        # session last remembered, which is not necessarily THIS report's
        # org — the shell's own org crumb carries the same query for the
        # same reason.
        f'<a class="fw-crumb" href="/?org={org.slug}" '
        f'style="color:inherit;opacity:.85;text-decoration:none">{org_name}</a>'
        f'<span class="fw-crumb-sep" style="opacity:.5">▸</span>'
        f'<a class="fw-back-link" href="/s/{org.slug}/{studio.slug}/" '
        f'title="Back to {studio_name}">{_CHEVRON_SVG}{studio_name}</a>'
    )


def portal_extensions(studio) -> dict:
    """What the portal injects into every report it builds, via the
    framework's neutral ``extensions`` contract. Per-studio: the breadcrumb's
    back link points at the studio's dashboard."""
    return {
        "nav_html": breadcrumb_nav_html(studio.org, studio),
        # Live queries (M1): where a built report page POSTs to re-run a
        # query it declared in _live_queries.json. Per-studio like the rest
        # of this dict, so the report slug travels as a literal "{slug}"
        # placeholder the framework substitutes at build time (both
        # str.format- and str.replace-friendly). The portal only advertises
        # the URL; the framework decides how the page uses it.
        "live_query_url": (
            f"/s/{studio.org.slug}/{studio.slug}/api/reports/{{slug}}/live-query"
        ),
        "scripts": [
            # The report-page "Options" menu host (static/report_menu.js)
            # MUST load before the three widget scripts below: they each
            # call window.__reportMenu.register(...) instead of mounting
            # their own header button, so the registry has to already
            # exist. All four are `defer` scripts (trellum/rendering/
            # html_builder.py), which execute in document order -- this
            # ordering is what makes that safe.
            "/api/reports/menu-widget.js",
            "/api/assistant/widget.js",
            # Theming redesign: wires the report's own theme picker to POST
            # its choice to the per-studio setter instead of writing
            # localStorage only -- see static/report_theme_widget.js. Does
            # not call window.__reportMenu.register(), so it has no
            # ordering dependency on menu-widget.js above.
            "/api/reports/theme-widget.js",
            "/api/reports/delivery-widget.js",
            # Public share links (internal planning#6): baked into every build like
            # the two widgets above, but its Options menu item only
            # registers for viewers who can manage links -- see
            # static/report_share.js.
            "/api/reports/share-widget.js",
            # View analytics (internal planning#4): "Activity" panel, same
            # register-only-if-permitted gating -- see
            # static/report_views.js.
            "/api/reports/views-widget.js",
            # "Metrics in this report" panel (semantic-layer Phase 2): reads
            # the build's own claimed metrics against the studio's synced
            # definitions -- see static/report_metrics.js.
            "/api/reports/metrics-widget.js",
        ],
    }


def child_env(studio, extra: dict | None = None) -> dict:
    """Allowlisted environment for a report subprocess."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ENV_ALLOWLIST or k.startswith(ENV_ALLOW_PREFIXES)
    }
    env["FW_EXTENSIONS_JSON"] = json.dumps(portal_extensions(studio))
    # Absolute, always: the child's cwd is the portal repo root, so a
    # relative project root would resolve against the wrong directory.
    env["FW_PROJECT_ROOT"] = str(Path(studio.project_root).resolve())
    # Builds always write to the local project root, whatever the portal's own
    # storage backend is. The framework's S3 layout is single-project (top-level
    # prefix == report slug), which cannot express org/studio, so the portal
    # publishes the finished directory itself under a tenant-scoped prefix —
    # see apps.core.storage. Set explicitly rather than left to the allowlist:
    # inheriting a bucket backend here would have each build upload itself to
    # the wrong place.
    #
    # BI_, not TRELLUM_: this is the framework's own environment contract, read
    # by its runner, and it did not move when the portal's settings were
    # renamed. Without it a --production build stops with "requires an output
    # backend" and names a variable nobody has set.
    env["BI_STORAGE_BACKEND"] = "local"
    # A portal build never asks the releases feed (trellum/update_check.py):
    # the portal's own check is opt-in and answers on /system, and a sandbox
    # has no egress to try with anyway.
    env["FW_UPDATE_CHECK"] = "0"
    configured_source = getattr(settings, "TRELLUM_SOURCE_URL", "").strip()
    if configured_source:
        # This one public, validated value is safe to expose to report code.
        # Keep it explicit: broadening the TRELLUM_* allowlist would leak
        # portal credentials. Omitting it preserves a runner image's baked
        # exact-source URL.
        from apps.core.version import source_url

        env["TRELLUM_SOURCE_URL"] = source_url()
    if extra:
        env.update(extra)
    return env


def _tmp_root() -> Path:
    p = Path(settings.DATA_DIR) / "tmp"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Memory ───────────────────────────────────────────────────────────────────
# Report builds are the hungry part of this product, and until now the only
# backpressure was the kernel OOM killer — which picks a victim by its own
# heuristics, so a runaway build could get a *different tenant's* build killed
# and recorded as that innocent report's failure. Three pieces fix that:
#
#   limit     every build gets TRELLUM_DEFAULT_JOB_MEMORY_MB (an operator
#             setting; report.yaml carries no sandbox limits)
#   admit     a runner hosts a build only if its budget still has room
#   cap       RLIMIT_AS turns runaway allocation into a MemoryError inside the
#             offending process, with a traceback its own author can act on
#
# Note what the cap actually limits: RLIMIT_AS is *address space*, which for a
# pandas workload runs well above resident memory (allocator arenas, mapped
# libraries, thread stacks). So the limit is used verbatim for
# packing decisions, and the hard cap is set a headroom multiple above it — a
# safety net that catches genuine runaways without failing healthy builds that
# merely map a lot of address space.


def _memory_limit_preexec(limit_bytes: int):
    """preexec_fn applying RLIMIT_AS to the child. POSIX only."""
    import resource

    def _apply():
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))

    return _apply


def read_peak_rss_mb(pid: int) -> int | None:
    """Peak resident memory of a live process, in MB (Linux and Windows).

    Must be sampled while the process is alive: once it is reaped the kernel
    has dropped its mm and ``VmHWM`` is gone. On Windows the equivalent
    high-water mark is ``PeakWorkingSetSize``. Enforcement (RLIMIT_AS)
    remains POSIX-only either way — this is measurement, not a cap.
    """
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) // 1024  # kB -> MB
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "win32":
        return _read_peak_working_set_mb(pid)
    return None


def _read_peak_working_set_mb(pid: int) -> int | None:
    """Windows: PeakWorkingSetSize via GetProcessMemoryInfo (psapi)."""
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if not ok:
                return None
            return int(counters.PeakWorkingSetSize) // (1024 * 1024)
        finally:
            kernel32.CloseHandle(handle)
    except OSError:
        return None


def build_cmd_flags(cache_mode: str, production: bool) -> list[str]:
    """The trellum.run flags for a build, shared by the in-process and
    sandboxed spawn paths. Returns everything after ``trellum.run <dir>``."""
    flags = ["--no-serve"]
    if cache_mode == "debug":
        flags.append("--debug")
        cache_mode = "normal"
    elif cache_mode == "debug-fresh":
        flags += ["--debug", "--no-cache"]
        cache_mode = "normal"
    if cache_mode == "fresh":
        flags.append("--no-cache")
    elif cache_mode == "force":
        flags.append("--force-cache")
    if production:
        flags.append("--production")
    return flags


def live_log_dir(run_id) -> Path:
    return _tmp_root() / "runs" / str(run_id)


def read_tail(path: str | Path, max_bytes: int = 50_000) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read()
    except (OSError, ValueError):
        return ""


@dataclass
class RunningProc:
    run_id: object
    slug: str
    studio_id: int
    process: subprocess.Popen
    started_at: datetime
    stdout_path: str
    stderr_path: str
    run_dir: str
    output_dir: str
    timeout_seconds: int = 1800
    sigterm_at: datetime | None = None
    timed_out: bool = False
    user_stopped: bool = False
    extra_env: dict = field(default_factory=dict)
    memory_limit_mb: int = 0
    peak_memory_mb: int | None = None


class Executor:
    """Owns the report subprocesses of one runner."""

    def __init__(
        self,
        worker_id: str,
        max_concurrent: int = 3,
        default_timeout: int = 1800,
        memory_budget_mb: int = 0,
    ):
        self.worker_id = worker_id
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        #: Total build memory this runner will host at once (MB).
        #: 0 disables budget accounting and leaves only the count ceiling.
        self.memory_budget_mb = memory_budget_mb
        self._procs: dict[object, RunningProc] = {}
        self._sandbox: DockerSandbox | None = None

    def _get_sandbox(self) -> DockerSandbox:
        if self._sandbox is None:
            self._sandbox = DockerSandbox()
        return self._sandbox

    # ── capacity ─────────────────────────────────────────────────────────
    @property
    def running_count(self) -> int:
        return len(self._procs)

    @property
    def has_capacity(self) -> bool:
        return self.running_count < self.max_concurrent

    @property
    def reserved_memory_mb(self) -> int:
        """Memory already promised to in-flight builds."""
        return sum(p.memory_limit_mb for p in self._procs.values())

    def memory_for(self, run: Run) -> int:  # noqa: ARG002 - one limit for every run
        """Memory limit for this run, in MB: the runner's configured default."""
        return settings.TRELLUM_DEFAULT_JOB_MEMORY_MB

    def can_admit(self, memory_mb: int) -> bool:
        """Is there room for one more build of this size?

        Count ceiling and memory budget both apply. A build larger than the
        whole budget is not silently starved here — the claim loop refuses it
        outright with an explanation, since no amount of waiting would help.
        """
        if not self.has_capacity:
            return False
        if self.memory_budget_mb <= 0:
            return True
        return self.reserved_memory_mb + memory_mb <= self.memory_budget_mb

    def exceeds_budget(self, memory_mb: int) -> bool:
        """True when this build could never fit, however empty the runner."""
        return self.memory_budget_mb > 0 and memory_mb > self.memory_budget_mb

    # ── spawn ────────────────────────────────────────────────────────────
    def start_run(self, run: Run, extra_env: dict | None = None) -> bool:
        """Spawn the subprocess for a claimed Run (status=starting).

        Any setup failure (missing report dir, disk full, Popen error)
        lands the run in ``error`` and releases the slot — a bad report
        must never jam the queue.
        """
        studio = run.studio
        slug = run.slug
        report_dir = studio.reports_dir / slug
        output_dir = str(studio.output_dir / slug)

        run_dir_base = ""
        log_dir = live_log_dir(run.id)
        stdout_path = str(log_dir / "stdout.log")
        stderr_path = str(log_dir / "stderr.log")
        stdout_fh = stderr_fh = None
        try:
            # Pre-flight: a report whose data sources cannot connect yet is
            # not started -- the run ends with what each source is waiting
            # for, and the report is queued again once a check clears it.
            from apps.datasources.status import WAITING_PREFIX, SourceState, report_blockers
            from apps.datasources.testing import run_state_check

            blockers = []
            for blocker in report_blockers(run.report):
                # A stored failure may be stale: ask again before holding the run.
                if blocker.state == SourceState.FAILING:
                    ok, detail = run_state_check(blocker)
                    if ok:
                        continue
                    blocker.detail = detail
                blockers.append(blocker)
            if blockers:
                message = "\n".join(f"{WAITING_PREFIX}{b.name}': {b.detail}" for b in blockers)
                logger.info(
                    f"blocked {studio}/{slug}: {message.splitlines()[0]}",
                    extra={"run_id": str(run.pk), "studio": str(studio), "slug": slug},
                )
                now = dj_tz.now()
                Run.objects.filter(pk=run.pk).update(
                    status=Run.ERROR, started_at=now, finished_at=now,
                    stderr_tail=message, worker_id=self.worker_id,
                )
                self._write_error(output_dir, message, blocked_by=[b.name for b in blockers])
                return False

            if not report_dir.is_dir():
                raise FileNotFoundError(f"report directory not found: {report_dir}")

            log_dir.mkdir(parents=True, exist_ok=True)
            stdout_fh = open(stdout_path, "wb")
            stderr_fh = open(stderr_path, "wb")

            # Copy-on-run sandbox (git sync can't affect running jobs),
            # including underscore-prefixed shared-helper siblings so
            # `from reports._shared.x import y` resolves inside the sandbox.
            run_dir_base = tempfile.mkdtemp(prefix=f"run-{slug}-", dir=str(_tmp_root()))
            run_report_dir = os.path.join(run_dir_base, "reports", slug)
            os.makedirs(os.path.dirname(run_report_dir), exist_ok=True)
            shutil.copytree(report_dir, run_report_dir)
            reports_dir = studio.reports_dir
            for entry in os.listdir(reports_dir):
                if entry.startswith("_") and (reports_dir / entry).is_dir():
                    shutil.copytree(
                        reports_dir / entry, os.path.join(run_dir_base, "reports", entry)
                    )
            # Project-root config the framework reads DURING the build: a
            # metrics.yaml claim expands at build time (an unstaged one fails
            # validation with metric-undefined), and events.yaml drives chart
            # annotation overlays. The sandbox project root is run_dir_base,
            # so these have to travel there or the build never sees them.
            for name in PROJECT_ROOT_BUILD_FILES:
                src_file = studio.project_root / name
                if src_file.is_file():
                    shutil.copyfile(src_file, os.path.join(run_dir_base, name))

            timeout = self.default_timeout

            # Materialize the studio's data sources: config.yaml for the
            # framework + decrypted TRELLUM_DS_* credentials for the child env.
            from apps.datasources.materialize import materialize, shared_read_paths

            ds_env = materialize(studio)
            if extra_env:
                ds_env.update(extra_env)

            flags = build_cmd_flags(run.cache_mode, production=not settings.DEBUG)
            memory_mb = self.memory_for(run)
            env = child_env(studio, ds_env)

            if sandbox_mode() == "docker":
                # The container appends to the same log files (via its shell
                # redirect), so the web tail is unchanged — close our handles.
                stdout_fh.close()
                stderr_fh.close()
                stdout_fh = stderr_fh = None
                logger.info(
                    f"start {studio}/{slug} ({memory_mb} MB, sandbox): "
                    f"trellum.run {run_report_dir} {' '.join(flags)}",
                    extra={"run_id": str(run.pk), "studio": str(studio), "slug": slug},
                )
                proc = self._get_sandbox().start(
                    run=run,
                    cmd_flags=flags,
                    run_report_dir=run_report_dir,
                    run_dir_base=run_dir_base,
                    log_dir=str(log_dir),
                    output_dir=output_dir,
                    project_root=studio.project_root,
                    extra_ro_paths=shared_read_paths(studio),
                    env=env,
                    stdout_path=stdout_path,
                    stderr_path=stderr_path,
                    memory_mb=memory_mb,
                    cpus=settings.TRELLUM_SANDBOX_CPUS,
                )
            else:
                cmd = [sys.executable, "-m", "trellum.run", run_report_dir, *flags]
                logger.info(
                    f"start {studio}/{slug} ({memory_mb} MB, UNSANDBOXED): {' '.join(cmd)}",
                    extra={"run_id": str(run.pk), "studio": str(studio), "slug": slug},
                )
                # preexec_fn is POSIX-only; on Windows dev machines the cap is
                # simply absent and the timeout ladder remains the only guard.
                popen_extra = {}
                if os.name != "nt" and settings.TRELLUM_JOB_MEMORY_ENFORCE:
                    cap_bytes = int(memory_mb * settings.TRELLUM_JOB_MEMORY_HEADROOM) * 1024 * 1024
                    popen_extra["preexec_fn"] = _memory_limit_preexec(cap_bytes)

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(settings.BASE_DIR),  # bundled source wins sys.path
                    env=env,
                    stdout=stdout_fh,
                    stderr=stderr_fh,
                    **popen_extra,
                )
                stdout_fh.close()
                stderr_fh.close()
                stdout_fh = stderr_fh = None

            now = dj_tz.now()
            self._procs[run.id] = RunningProc(
                run_id=run.id,
                slug=slug,
                studio_id=studio.pk,
                process=proc,
                started_at=now,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                run_dir=run_dir_base,
                output_dir=output_dir,
                timeout_seconds=timeout,
                memory_limit_mb=memory_mb,
            )
            Run.objects.filter(pk=run.pk).update(
                status=Run.RUNNING,
                pid=proc.pid,  # None for sandboxed runs (containers have no host pid)
                container_id=getattr(proc, "container_id", ""),
                started_at=now,
                log_dir=str(log_dir),
                worker_id=self.worker_id,
                memory_limit_mb=memory_mb,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"start failed: {studio}/{slug}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            for fh in (stdout_fh, stderr_fh):
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
            shutil.rmtree(log_dir, ignore_errors=True)
            if run_dir_base:
                shutil.rmtree(run_dir_base, ignore_errors=True)
            Run.objects.filter(pk=run.pk).update(
                status=Run.ERROR,
                started_at=dj_tz.now(),
                finished_at=dj_tz.now(),
                stderr_tail=f"start_failed: {type(exc).__name__}: {exc}",
                worker_id=self.worker_id,
            )
            self._write_error(output_dir, f"start_failed: {type(exc).__name__}: {exc}")
            return False

    # ── monitor ──────────────────────────────────────────────────────────
    def tick(self) -> None:
        """One monitor pass: stop requests, timeouts, completions."""
        if not self._procs:
            return

        stop_ids = set(
            Run.objects.filter(pk__in=self._procs.keys(), stop_requested=True).values_list(
                "pk", flat=True
            )
        )
        now = datetime.now(dt_tz.utc)
        finished: list[tuple[RunningProc, int]] = []
        for run_id, proc in list(self._procs.items()):
            ret = proc.process.poll()
            if ret is not None:
                finished.append((proc, ret))
                continue

            # Sample peak memory while the process is alive. In-process runs
            # read /proc VmHWM; sandboxed runs expose it via the facade (the
            # container's own /proc is gone once it exits).
            sampler = getattr(proc.process, "peak_rss_mb", None)
            observed = sampler() if sampler else read_peak_rss_mb(proc.process.pid)
            if observed is not None and observed > (proc.peak_memory_mb or 0):
                proc.peak_memory_mb = observed

            if run_id in stop_ids and proc.sigterm_at is None:
                proc.user_stopped = True
                self._send_sigterm(proc, now, reason="stop requested")
            else:
                elapsed = (now - _aware(proc.started_at)).total_seconds()
                if proc.sigterm_at is None and elapsed > proc.timeout_seconds:
                    proc.timed_out = True
                    logger.warning(
                        f"{proc.slug} exceeded {proc.timeout_seconds}s, "
                        f"sending SIGTERM (5s grace)",
                        extra={"slug": proc.slug, "reason": "timeout"},
                    )
                    self._send_sigterm(proc, now, reason="timeout")
                elif proc.sigterm_at is not None:
                    if (now - proc.sigterm_at).total_seconds() > 5:
                        logger.warning(
                            f"{proc.slug} did not exit after SIGTERM, sending SIGKILL",
                            extra={"slug": proc.slug, "reason": "timeout"},
                        )
                        try:
                            proc.process.kill()
                        except OSError:
                            pass

        for proc, exit_code in finished:
            del self._procs[proc.run_id]
            self._record_completion(proc, exit_code)

    @staticmethod
    def _send_sigterm(proc: RunningProc, now: datetime, reason: str) -> None:
        try:
            proc.process.send_signal(signal.SIGTERM)
            proc.sigterm_at = now
        except OSError:
            pass

    def _record_completion(self, proc: RunningProc, exit_code: int) -> None:
        now = dj_tz.now()
        stdout = read_tail(proc.stdout_path)
        stderr = read_tail(proc.stderr_path)

        status = Run.SUCCESS if exit_code == 0 else Run.ERROR
        if os.name != "nt":
            if exit_code in (-signal.SIGTERM, 128 + signal.SIGTERM):
                status = Run.STOPPED
            elif exit_code in (-signal.SIGKILL, 128 + signal.SIGKILL):
                # SIGKILL is what the kernel OOM killer sends — unless WE
                # escalated after a timeout.
                status = Run.OOM_KILLED
        # A sandbox container reports OOM authoritatively via its OOMKilled
        # flag; trust that over exit-code archaeology (137 is forgeable).
        if getattr(proc.process, "oom_killed", False):
            status = Run.OOM_KILLED
        # Our own intent beats exit-code archaeology.
        if proc.timed_out:
            status = Run.TIMEOUT
        elif proc.user_stopped and status != Run.SUCCESS:
            status = Run.STOPPED
        elif os.name == "nt" and proc.sigterm_at is not None and status == Run.ERROR:
            status = Run.STOPPED

        run = Run.objects.filter(pk=proc.run_id).first()
        duration = (now - _aware(proc.started_at)).total_seconds()

        # With a remote store the build is not finished when the process exits
        # — the output still has to reach the place the web process reads from.
        # A publish failure therefore downgrades the run: output that only
        # exists on this runner's disk is output nobody can open, and calling
        # that "success" produces a green report that 404s.
        if status == Run.SUCCESS and run is not None:
            from apps.core import storage

            if storage.is_remote():
                try:
                    storage.publish_build(
                        run.studio, proc.slug, proc.output_dir, run.pk
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced as run failure
                    status = Run.ERROR
                    msg = f"publish to object storage failed: {type(exc).__name__}: {exc}"
                    logger.error(msg, extra={"slug": proc.slug})
                    # Appended to stderr rather than written to _meta.json here:
                    # the ERROR branch below writes the tail of stderr, so this
                    # reaches the report page through the existing path.
                    stderr = f"{stderr}\n{msg}" if stderr else msg

        # The retention clock for built output, stamped here and nowhere else:
        # this is the one point where output is known to be complete AND
        # reachable (a publish failure has already downgraded `status` above).
        # _meta.json's last_run cannot serve — failed runs bump it too, so a
        # report failing nightly would look freshly built forever and never
        # expire. See apps.reports.models.Report.last_built_at.
        if status == Run.SUCCESS and run is not None:
            from apps.reports.models import Report

            # Clearing data_expired_at here is what makes a rebuild the way
            # back from an expired report: fresh output, normal state, one
            # write.
            Report.objects.filter(pk=run.report_id).update(
                last_built_at=now, data_expired_at=None
            )

        if run is not None:
            run.status = status
            run.finished_at = now
            run.exit_code = exit_code
            run.stdout_tail = stdout
            run.stderr_tail = stderr
            run.peak_memory_mb = proc.peak_memory_mb
            run.save(
                update_fields=[
                    "status", "finished_at", "exit_code", "stdout_tail",
                    "stderr_tail", "peak_memory_mb",
                ]
            )

        if run is not None:
            # Alert-on-transition / scheduled-delivery bookkeeping must never
            # break the completed build path.
            try:
                from apps.reports.notify import run_finished as notify_run_finished

                notify_run_finished(run)
            except Exception as exc:  # noqa: BLE001 - notifications must not break builds
                logger.exception(f"notify run_finished: {type(exc).__name__}: {exc}")

            # Agentic alert rules watching this report, on success only. Same
            # never-break-a-build guarantee.
            if status == Run.SUCCESS:
                try:
                    from apps.alerts.evaluator import evaluate_after_build

                    evaluate_after_build(run)
                except Exception as exc:  # noqa: BLE001 - alerts must not break builds
                    logger.exception(f"alerts after build: {type(exc).__name__}: {exc}")

        icon = {"success": "✓", "error": "✗", "stopped": "■", "timeout": "⏱", "oom_killed": "💥"}.get(status, "?")
        logger.info(
            f"{icon} {proc.slug} — {status} in {round(duration, 1)}s (exit {exit_code})",
            extra={
                "slug": proc.slug, "status": status,
                "duration_seconds": round(duration, 1), "exit_code": exit_code,
            },
        )
        if status in (Run.ERROR, Run.OOM_KILLED, Run.TIMEOUT):
            for line in (stderr or "").strip().split("\n")[-3:]:
                if line.strip():
                    logger.info(f"{line.strip()[:120]}")
            if status == Run.OOM_KILLED:
                msg = (
                    f"OOM_KILLED: subprocess SIGKILL'd by kernel (exit {exit_code}). "
                    f"{_memory_note(proc)} "
                    f"Last stderr:\n{stderr[-300:] if stderr else '(empty)'}"
                )
            elif status == Run.TIMEOUT:
                msg = f"TIMEOUT: exceeded {proc.timeout_seconds}s and was killed."
            elif "MemoryError" in (stderr or ""):
                # The RLIMIT_AS cap doing its job: the build hit its own
                # ceiling instead of the kernel picking an arbitrary victim.
                msg = (
                    f"MEMORY LIMIT: the build exceeded its declared memory. "
                    f"{_memory_note(proc)} "
                    f"Reduce peak usage (aggregate in SQL rather than loading raw "
                    f"rows), or have the operator raise TRELLUM_DEFAULT_JOB_MEMORY_MB.\n"
                    f"{stderr[-300:]}"
                )
            else:
                msg = stderr[-500:] if stderr else f"exit code {exit_code}"
            self._write_error(proc.output_dir, msg)
            # The failure has to travel too: with a remote store the registry
            # reads status from the bucket, and an error that stays on this
            # runner's disk leaves every web node showing the previous outcome
            # forever. Status only — publishing the output directory of a
            # failed run would overwrite good objects with whatever mix of old
            # and half-written files the build died with, and the serving
            # pointer never moves on failure.
            if run is not None:
                from apps.core import storage

                if storage.is_remote():
                    try:
                        storage.publish_meta(run.studio, proc.slug, proc.output_dir)
                    except Exception as exc:  # noqa: BLE001 - never mask the run failure
                        logger.warning(
                            f"publishing failure status for {proc.slug}: "
                            f"{type(exc).__name__}: {exc}",
                            extra={"slug": proc.slug},
                        )

        # A build that died may have died on a source. Re-checking the
        # report's sources can take 30 s, so it happens off the tick; the
        # stored error is rewritten when the thread finishes.
        if status == Run.ERROR and exit_code != 0 and run is not None:
            threading.Thread(
                target=self._attribute_failure, args=(run.pk, proc.output_dir), daemon=True
            ).start()

        # Remove the finished sandbox container (no-op for in-process runs).
        cleanup = getattr(proc.process, "cleanup", None)
        if cleanup is not None:
            cleanup()

        # Tails are persisted on the row; the live files and sandbox go away.
        shutil.rmtree(os.path.dirname(proc.stdout_path), ignore_errors=True)
        if proc.run_dir:
            shutil.rmtree(proc.run_dir, ignore_errors=True)

    @staticmethod
    def _write_error(output_dir: str, message: str, blocked_by=()) -> None:
        """Surface a failure in _meta.json so the registry/UI can see it.
        ``blocked_by`` names the sources pre-flight refused to start without
        (empty for a failure of the build itself)."""
        try:
            from trellum.meta import write_error, write_meta

            os.makedirs(output_dir, exist_ok=True)
            write_error(output_dir, message)
            write_meta(output_dir, {"blocked_by": list(blocked_by)})
        except Exception:  # pragma: no cover - never mask the real failure
            pass

    def _attribute_failure(self, run_id, output_dir: str) -> None:
        """Worker thread: re-check the failed run's bound sources and, when one
        fails, put ``Data source '<name>': <error>`` above the stored error --
        unless a newer run owns the report's outcome by then. Repository files
        the portal does not upload have nothing to check."""
        import time

        from django.db import connection

        from apps.datasources.models import INLINE_TYPES
        from apps.datasources.status import effective_fields, report_states
        from apps.datasources.testing import run_state_check

        try:
            run = Run.objects.select_related("report", "studio").get(pk=run_id)
            # ponytail: each check is capped at 10 s, so this can overrun by one
            # check; a per-check budget needs test_datasource to take a timeout.
            deadline = time.monotonic() + 30
            text = ""
            for state in report_states(run.report):
                if state.binding is None or time.monotonic() >= deadline:
                    continue
                fields = effective_fields(state.declaration, state.binding)
                if state.type in INLINE_TYPES and not fields.get("upload"):
                    continue
                ok, detail = run_state_check(state)
                if not ok:
                    text = f"Data source '{state.name}': {detail}"
                    break
            latest = (
                Run.objects.filter(report_id=run.report_id)
                .order_by("-created_at").values_list("pk", flat=True).first()
            )
            if not text or latest != run.pk:
                return
            Run.objects.filter(pk=run.pk).update(
                stderr_tail=f"{text}\n\n{run.stderr_tail}" if run.stderr_tail else text
            )
            # ponytail: rewrites this runner's _meta.json only; with a remote
            # store the web nodes keep the un-attributed text until Phase 2
            # reads Run.stderr_tail instead.
            self._write_error(output_dir, f"{text}\n{run.stderr_tail[-300:]}")
        except Exception as exc:  # noqa: BLE001 - never surface attribution as a crash
            logger.warning(f"attribution skipped for run {run_id}: {type(exc).__name__}: {exc}")
        finally:
            connection.close()

    # ── shutdown ─────────────────────────────────────────────────────────
    def stop_all(self) -> None:
        for proc in self._procs.values():
            proc.user_stopped = True
            try:
                proc.process.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def drain(self, timeout: int = 540) -> None:
        """Let running builds finish; SIGTERM whatever remains at deadline."""
        import time as _t

        deadline = _t.time() + timeout
        while _t.time() < deadline:
            self.tick()
            if not self._procs:
                return
            _t.sleep(2)
        n = len(self._procs)
        if n:
            logger.warning(f"Timeout after {timeout}s, stopping {n} remaining jobs")
        self.stop_all()
        _t.sleep(5)
        self.tick()


def _memory_note(proc: RunningProc) -> str:
    """Human-readable 'declared X, peaked at Y' for a failure message."""
    parts = []
    if proc.memory_limit_mb:
        parts.append(f"limit {proc.memory_limit_mb} MB")
    if proc.peak_memory_mb is not None:
        parts.append(f"observed peak {proc.peak_memory_mb} MB")
    return f"({', '.join(parts)})." if parts else ""


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=dt_tz.utc)
    return dt
