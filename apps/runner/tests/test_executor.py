"""Executor tests: the sacred spawn mechanics, env scrubbing, the timeout
ladder, stop requests, and failure handling. Subprocesses are stubbed —
the real end-to-end build runs in CI's compose e2e."""
from __future__ import annotations

import json
import os
import sys
import signal
from datetime import timedelta
from pathlib import Path

import pytest
import requests
from django.utils import timezone

from apps.runner import executor as executor_mod
from apps.reports.models import Report
from apps.runner.executor import Executor, RunningProc, child_env, portal_extensions
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


# FakeProc / fake_popen / queued_run live in the repo-root conftest.py — the core
# overlay-seam suite spawns builds too.
from conftest import FakeProc  # noqa: E402,F401  (re-exported for tests below)


class TestChildEnv:
    def test_portal_secrets_are_scrubbed(self, studio_tree, monkeypatch):
        for key in (
            "DATABASE_URL",
            "SESSION_SECRET_KEY",
            "SECRET_ENCRYPTION_KEY",
            "ANTHROPIC_API_KEY",
            "POSTGRES_PASSWORD",
            "EMAIL_URL",
        ):
            monkeypatch.setenv(key, "leaky-secret")
        env = child_env(studio_tree)
        for key in (
            "DATABASE_URL",
            "SESSION_SECRET_KEY",
            "SECRET_ENCRYPTION_KEY",
            "ANTHROPIC_API_KEY",
            "POSTGRES_PASSWORD",
            "EMAIL_URL",
        ):
            assert key not in env, key
        assert "leaky-secret" not in json.dumps(env)

    def test_allowlisted_vars_survive(self, studio_tree, monkeypatch):
        monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
        monkeypatch.setenv("LC_ALL", "C.UTF-8")
        env = child_env(studio_tree)
        assert "PATH" in env
        assert env["LC_ALL"] == "C.UTF-8"

    def test_fw_project_root_is_absolute(self, studio_tree):
        env = child_env(studio_tree)
        assert Path(env["FW_PROJECT_ROOT"]).is_absolute()
        assert env["FW_PROJECT_ROOT"] == str(Path(studio_tree.project_root).resolve())

    def test_builds_never_ask_the_releases_feed(self, studio_tree, monkeypatch):
        """The portal's own release check is opt-in and lives on /system; a
        build it spawns must not make the framework's once-a-day attempt."""
        monkeypatch.delenv("FW_UPDATE_CHECK", raising=False)
        assert child_env(studio_tree)["FW_UPDATE_CHECK"] == "0"

    def test_safe_source_override_is_the_only_trellum_setting_forwarded(
        self, studio_tree, settings
    ):
        settings.TRELLUM_SOURCE_URL = "https://code.example/fork/tree/abc123"
        settings.TRELLUM_PRIVATE_VALUE = "must-not-leak"
        env = child_env(studio_tree)
        assert env["TRELLUM_SOURCE_URL"] == settings.TRELLUM_SOURCE_URL
        assert "TRELLUM_PRIVATE_VALUE" not in env

    def test_empty_source_override_preserves_runner_image_default(
        self, studio_tree, settings
    ):
        settings.TRELLUM_SOURCE_URL = ""
        assert "TRELLUM_SOURCE_URL" not in child_env(studio_tree)

    def test_extensions_carry_backlink_and_widget(self, studio_tree):
        env = child_env(studio_tree)
        ext = json.loads(env["FW_EXTENSIONS_JSON"])
        assert f"/s/{studio_tree.org.slug}/{studio_tree.slug}/" in ext["nav_html"]
        assert "fw-back-link" in ext["nav_html"]
        assert ext["scripts"] == [
            "/api/reports/menu-widget.js",
            "/api/assistant/widget.js",
            "/api/reports/theme-widget.js",
            "/api/reports/delivery-widget.js",
            "/api/reports/share-widget.js",
            "/api/reports/views-widget.js",
            "/api/reports/metrics-widget.js",
        ]

    def test_storage_backend_pinned_local(self, studio_tree):
        # The framework's variable name, not the portal's: this one is read by
        # the framework's runner, and a --production build refuses to start
        # without it.
        env = child_env(studio_tree)
        assert env["BI_STORAGE_BACKEND"] == "local"
        assert "TRELLUM_STORAGE_BACKEND" not in env

    def test_extra_env_merged(self, studio_tree):
        env = child_env(studio_tree, {"TRELLUM_DS_1_USER": "u"})
        assert env["TRELLUM_DS_1_USER"] == "u"

    def test_extensions_shape_matches_legacy_contract(self, studio_tree):
        ext = portal_extensions(studio_tree)
        # the frozen extensions block + the live-query advertisement (M1)
        assert set(ext) == {"nav_html", "scripts", "live_query_url"}

    def test_extensions_advertise_live_query_url(self, studio_tree):
        # Per-studio dict, so the report slug travels as a literal "{slug}"
        # placeholder the framework substitutes at build time.
        ext = portal_extensions(studio_tree)
        org, studio = studio_tree.org.slug, studio_tree.slug
        assert ext["live_query_url"] == (
            f"/s/{org}/{studio}/api/reports/{{slug}}/live-query"
        )
        assert ext["live_query_url"].format(slug="r1") == (
            f"/s/{org}/{studio}/api/reports/r1/live-query"
        )


class TestSpawn:
    def test_spawn_mechanics(self, fake_popen, queued_run, studio_tree, write_report, settings):
        # A shared-helpers sibling must be copied into the sandbox too.
        shared = studio_tree.reports_dir / "_shared"
        shared.mkdir()
        (shared / "helpers.py").write_text("X = 1", encoding="utf-8")

        ex = Executor("w1", max_concurrent=3)
        assert ex.start_run(queued_run) is True

        proc = fake_popen["procs"][0]
        # cwd = repo root, so the bundled source wins sys.path.
        assert proc.kwargs["cwd"] == str(settings.BASE_DIR)
        # python -m trellum.run <sandbox>/reports/<slug> --no-serve
        assert proc.cmd[1:3] == ["-m", "trellum.run"]
        assert proc.cmd[4] == "--no-serve"
        sandbox_report = Path(proc.cmd[3])
        assert sandbox_report.name == "player-overview"
        assert (sandbox_report / "report.yaml").is_file()
        assert (sandbox_report.parent / "_shared" / "helpers.py").is_file()
        # Sandbox lives under DATA_DIR/tmp, not the studio tree.
        assert str(sandbox_report).startswith(str(settings.DATA_DIR / "tmp"))

        env = proc.kwargs["env"]
        assert env["FW_PROJECT_ROOT"] == str(Path(studio_tree.project_root).resolve())
        assert "DATABASE_URL" not in env

    def test_project_root_build_files_reach_the_sandbox(
        self, fake_popen, queued_run, studio_tree, write_report, settings
    ):
        # metrics.yaml expands claims DURING the build and events.yaml drives
        # annotation overlays; both live at the project root, so an unstaged
        # one makes the sandbox build fail (metric-undefined) or silently drop
        # annotations. Regression guard: they must travel into the run copy.
        root = Path(studio_tree.project_root)
        (root / "metrics.yaml").write_text("version: 1\nmetrics: []\n", encoding="utf-8")
        (root / "events.yaml").write_text("events: []\n", encoding="utf-8")

        ex = Executor("w1", max_concurrent=3)
        assert ex.start_run(queued_run) is True

        proc = fake_popen["procs"][0]
        run_dir_base = Path(proc.cmd[3]).parent.parent  # <base>/reports/<slug>
        assert (run_dir_base / "metrics.yaml").is_file()
        assert (run_dir_base / "events.yaml").is_file()

    def test_absent_build_files_are_not_required(
        self, fake_popen, queued_run, studio_tree, write_report, settings
    ):
        # A project with no metrics.yaml/events.yaml must still build; staging
        # copies each only when present.
        ex = Executor("w1", max_concurrent=3)
        assert ex.start_run(queued_run) is True
        proc = fake_popen["procs"][0]
        run_dir_base = Path(proc.cmd[3]).parent.parent
        assert not (run_dir_base / "metrics.yaml").exists()

        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.RUNNING
        assert run.pid == 4242
        assert run.log_dir

    @pytest.mark.parametrize(
        "cache_mode,expected",
        [
            ("normal", []),
            ("fresh", ["--no-cache"]),
            ("force", ["--force-cache"]),
            ("debug", ["--debug"]),
            ("debug-fresh", ["--debug", "--no-cache"]),
        ],
    )
    def test_cache_mode_flags(self, fake_popen, report_row, cache_mode, expected):
        run = Run.objects.create(
            report=report_row,
            studio=report_row.studio,
            slug=report_row.slug,
            status=Run.STARTING,
            cache_mode=cache_mode,
        )
        Executor("w1").start_run(run)
        cmd = fake_popen["procs"][-1].cmd
        flags = [c for c in cmd if c.startswith("--") and c not in ("--no-serve", "--production")]
        assert flags == expected

    def test_production_flag_follows_debug_setting(self, fake_popen, queued_run, settings):
        settings.DEBUG = False
        Executor("w1").start_run(queued_run)
        assert "--production" in fake_popen["procs"][-1].cmd

    def test_missing_report_dir_fails_cleanly(self, fake_popen, report_row, studio_tree):
        import shutil

        shutil.rmtree(studio_tree.reports_dir / report_row.slug)
        run = Run.objects.create(
            report=report_row, studio=studio_tree, slug=report_row.slug, status=Run.STARTING
        )
        ex = Executor("w1")
        assert ex.start_run(run) is False
        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert "start_failed" in run.stderr_tail
        assert ex.running_count == 0
        # The failure landed in _meta.json where the registry finds it.
        from trellum.meta import read_meta

        meta = read_meta(str(studio_tree.output_dir / report_row.slug))
        assert meta.get("last_status") == "error"


class TestPublishToRemoteStorage:
    """A build is not done when the process exits — the output still has to
    reach the store the web process reads from."""

    def _start(self, fake_popen, queued_run) -> tuple[Executor, FakeProc]:
        ex = Executor("w1")
        ex.start_run(queued_run)
        return ex, fake_popen["procs"][-1]

    def test_local_backend_publishes_nothing(self, fake_popen, queued_run, settings, monkeypatch):
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "local"
        calls = []
        monkeypatch.setattr(storage, "publish_build", lambda *a: calls.append(a))
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()
        assert calls == []
        assert Run.objects.get(pk=queued_run.pk).status == Run.SUCCESS

    def test_successful_build_is_published_under_its_tenant_prefix(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        calls = []
        monkeypatch.setattr(storage, "publish_build", lambda *a: calls.append(a))
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()
        assert len(calls) == 1
        studio, slug, _output_dir, run_id = calls[0]
        assert (studio.pk, slug) == (queued_run.studio.pk, queued_run.slug)
        assert run_id == queued_run.pk
        assert Run.objects.get(pk=queued_run.pk).status == Run.SUCCESS

    def test_a_failed_publish_fails_the_run(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        """Output that only exists on this runner's disk is output nobody can
        open. Recording that as success produces a green report that 404s."""
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"

        def boom(*_args):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(storage, "publish_build", boom)
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()

        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.ERROR
        # The build itself exited cleanly; the reason must say what went wrong.
        assert run.exit_code == 0
        assert "AccessDenied" in run.stderr_tail

    def test_a_failed_build_publishes_its_status_but_never_its_output(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        """The output directory of a failed run holds old and half-written
        files — publishing it would overwrite good objects. But the *status*
        must travel: the registry reads it from the bucket, and an error that
        stays on this runner's disk shows every web node the previous outcome
        forever."""
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        builds, metas = [], []
        monkeypatch.setattr(storage, "publish_build", lambda *a: builds.append(a))
        monkeypatch.setattr(storage, "publish_meta", lambda *a: metas.append(a))
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(1)
        ex.tick()
        assert builds == []
        assert len(metas) == 1
        studio, slug, _output_dir = metas[0]
        assert (studio.pk, slug) == (queued_run.studio.pk, queued_run.slug)
        assert Run.objects.get(pk=queued_run.pk).status == Run.ERROR

    def test_a_successful_build_stamps_the_retention_clock(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"
        monkeypatch.setattr(storage, "publish_build", lambda *a: None)
        # This report's output was expired by retention at some point.
        Report.objects.filter(pk=queued_run.report_id).update(
            data_expired_at=timezone.now() - timedelta(days=1)
        )
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()

        queued_run.report.refresh_from_db()
        assert queued_run.report.last_built_at is not None
        # A rebuild is the way back from an expired report.
        assert queued_run.report.data_expired_at is None

    def test_the_local_backend_stamps_the_clock_too(self, fake_popen, queued_run, settings):
        """The clock is about the build having succeeded, not about where its
        output went — the default single-VM shape publishes nothing at all."""
        settings.TRELLUM_STORAGE_BACKEND = "local"
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()

        queued_run.report.refresh_from_db()
        assert queued_run.report.last_built_at is not None

    def test_a_failed_build_leaves_the_retention_clock_alone(
        self, fake_popen, queued_run, monkeypatch
    ):
        """The whole reason Report.last_built_at exists rather than a reading
        of _meta.json: a report failing every night must age out on schedule,
        not look freshly built each morning."""
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(1)
        ex.tick()

        queued_run.report.refresh_from_db()
        assert queued_run.report.last_built_at is None

    def test_a_failed_publish_leaves_the_retention_clock_alone(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        """Output nobody can reach is not a build. The run is downgraded to
        ERROR above, and the clock has to agree with that."""
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"

        def boom(*_args):
            raise RuntimeError("AccessDenied")

        monkeypatch.setattr(storage, "publish_build", boom)
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(0)
        ex.tick()

        queued_run.report.refresh_from_db()
        assert queued_run.report.last_built_at is None

    def test_a_failed_status_publish_does_not_mask_the_run_failure(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        from apps.core import storage

        settings.TRELLUM_STORAGE_BACKEND = "s3"

        def boom(*_args):
            raise RuntimeError("store down too")

        monkeypatch.setattr(storage, "publish_meta", boom)
        ex, proc = self._start(fake_popen, queued_run)
        proc.finish(1)
        ex.tick()
        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.ERROR
        assert run.exit_code == 1


class TestCompletionAndTimeouts:
    def _start(self, fake_popen, queued_run) -> tuple[Executor, FakeProc]:
        ex = Executor("w1")
        ex.start_run(queued_run)
        return ex, fake_popen["procs"][-1]

    def test_success_completion(self, fake_popen, queued_run):
        ex, proc = self._start(fake_popen, queued_run)
        # Write into the live log files, then let the process "exit".
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stdout.log").write_text("built ok\n", encoding="utf-8")
        proc.finish(0)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.SUCCESS
        assert run.exit_code == 0
        assert "built ok" in run.stdout_tail
        assert run.finished_at is not None
        assert ex.running_count == 0
        # Live log dir is gone once tails are persisted.
        assert not Path(run.log_dir).exists()

    def test_error_completion_writes_meta(self, fake_popen, queued_run, studio_tree):
        ex, proc = self._start(fake_popen, queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stderr.log").write_text("Boom: bad query\n", encoding="utf-8")
        proc.finish(3)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert "Boom" in run.stderr_tail
        from trellum.meta import read_meta

        assert read_meta(str(studio_tree.output_dir / run.slug)).get("last_status") == "error"

    def test_timeout_ladder_sigterm_then_kill(self, fake_popen, queued_run, caplog):
        ex, proc = self._start(fake_popen, queued_run)
        rp = ex._procs[queued_run.pk]
        rp.timeout_seconds = 10
        rp.started_at = timezone.now() - timedelta(seconds=60)

        ex.tick()  # over budget -> SIGTERM
        assert rp.timed_out is True
        assert signal.SIGTERM in proc.signals
        assert not proc.killed

        rp.sigterm_at = rp.sigterm_at - timedelta(seconds=10)  # grace elapsed
        ex.tick()
        assert proc.killed

        proc.finish(-signal.SIGKILL if os.name != "nt" else 1)
        ex.tick()
        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.TIMEOUT  # our intent beats exit-code archaeology
        signals = [r for r in caplog.records if getattr(r, "signal", None)]
        assert [r.signal for r in signals] == ["SIGTERM", "SIGKILL"]
        assert all(r.run_id == str(run.pk) and r.worker_id == ex.worker_id for r in signals)
        completion = next(r for r in caplog.records if getattr(r, "status", None) == Run.TIMEOUT)
        assert completion.levelname == "ERROR"
        assert completion.memory_allocation_mb == rp.memory_limit_mb
        assert completion.memory_cap_mb == rp.memory_cap_mb

    def test_stop_request_marks_stopped(self, fake_popen, queued_run, caplog):
        ex, proc = self._start(fake_popen, queued_run)
        Run.objects.filter(pk=queued_run.pk).update(stop_requested=True)
        ex.tick()  # picks up the flag -> SIGTERM
        assert signal.SIGTERM in proc.signals
        stop = next(r for r in caplog.records if getattr(r, "signal", None) == "SIGTERM")
        assert stop.reason == "stop requested"
        assert stop.run_id == str(queued_run.pk)
        proc.finish(-signal.SIGTERM if os.name != "nt" else 1)
        ex.tick()
        assert Run.objects.get(pk=queued_run.pk).status == Run.STOPPED

class TestMemory:
    def test_every_run_gets_the_runner_default_and_records_it(
        self, fake_popen, queued_run, settings
    ):
        settings.TRELLUM_DEFAULT_JOB_MEMORY_MB = 777
        settings.TRELLUM_JOB_MEMORY_ENFORCE = os.name != "nt"
        settings.TRELLUM_JOB_MEMORY_HEADROOM = 1.5
        ex = Executor("w1")
        ex.start_run(queued_run)
        running = ex._procs[queued_run.pk]
        assert running.memory_limit_mb == 777
        if os.name != "nt":
            assert running.memory_cap_mb == int(777 * 1.5)
            assert running.memory_cap_kind == "address space (RLIMIT_AS)"
        else:
            assert running.memory_cap_mb is None
            assert running.memory_cap_kind == "none (Windows)"
        settings.TRELLUM_JOB_MEMORY_HEADROOM = 4.0
        settings.TRELLUM_JOB_MEMORY_ENFORCE = False
        assert running.memory_cap_mb == (None if os.name == "nt" else int(777 * 1.5))
        queued_run.refresh_from_db()
        # Recorded on the run so a later change to the setting cannot
        # retroactively change what this build was admitted against.
        assert queued_run.memory_limit_mb == 777

    def test_memory_diagnostic_is_persisted_with_allocation_cap_and_peak(
        self, fake_popen, queued_run, studio_tree, settings, monkeypatch
    ):
        settings.TRELLUM_DEFAULT_JOB_MEMORY_MB = 512
        settings.TRELLUM_JOB_MEMORY_ENFORCE = os.name != "nt"
        settings.TRELLUM_JOB_MEMORY_HEADROOM = 2.0
        ex = Executor("w1")
        ex.start_run(queued_run)
        rp = ex._procs[queued_run.pk]
        rp.peak_memory_mb = 401
        settings.TRELLUM_DEFAULT_JOB_MEMORY_MB = 9999
        run = Run.objects.get(pk=queued_run.pk)
        long_trace = (
            "Traceback...\n" + "intermediate frame\n" * 90
            + "MemoryError\nConcreteFinalException: allocation failed"
        )
        Path(run.log_dir, "stderr.log").write_text(long_trace, encoding="utf-8")
        threads = []
        monkeypatch.setattr(
            executor_mod.threading, "Thread", lambda **kwargs: threads.append(kwargs)
        )
        fake_popen["procs"][-1].finish(1)
        ex.tick()

        run.refresh_from_db()
        from trellum.meta import read_meta

        error = read_meta(str(studio_tree.output_dir / run.slug)).get("last_error") or ""
        assert "MEMORY ALLOCATION FAILED" in run.stderr_tail
        assert "operator allocation 512 MB" in run.stderr_tail
        assert "observed peak 401 MB" in run.stderr_tail
        assert long_trace in run.stderr_tail
        assert error.startswith("MEMORY ALLOCATION FAILED:")
        assert "MEMORY ALLOCATION FAILED" in error
        assert "operator allocation 512 MB" in error
        assert threads == []
        if os.name == "nt":
            assert "no enforced hard cap" in error
        else:
            assert "applied address space (RLIMIT_AS) cap 1024 MB" in error
        assert error.endswith("ConcreteFinalException: allocation failed")

    def test_137_without_docker_oom_flag_is_not_oom(
        self, fake_popen, queued_run, studio_tree, monkeypatch
    ):
        ex = Executor("w1")
        ex.start_run(queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stderr.log").write_text("terminated", encoding="utf-8")
        threads = []
        monkeypatch.setattr(
            executor_mod.threading, "Thread", lambda **kwargs: threads.append(kwargs)
        )
        fake_popen["procs"][-1].finish(137)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert "consistent with SIGKILL" in run.stderr_tail
        assert "cause unknown" in run.stderr_tail
        from trellum.meta import read_meta

        error = read_meta(str(studio_tree.output_dir / run.slug)).get("last_error") or ""
        assert "cause unknown" in error
        assert "OOM" not in error
        assert threads == []

    def test_vertica_connection_loss_does_not_claim_oom(self, fake_popen, queued_run, studio_tree):
        ex = Executor("w1")
        ex.start_run(queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stderr.log").write_text(
            "Connection closed by Vertica", encoding="utf-8"
        )
        fake_popen["procs"][-1].finish(1)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert "cause is unconfirmed" in run.stderr_tail
        assert "operator allocation" in run.stderr_tail
        assert "TRELLUM_DEFAULT_JOB_MEMORY_MB" in run.stderr_tail
        assert "observed peak unavailable" in run.stderr_tail
        assert "OOM" not in run.stderr_tail

    def test_memory_note_reports_no_cap_when_enforcement_is_disabled(self):
        proc = RunningProc(
            run_id="a", slug="s", studio_id=1, process=None,
            started_at=timezone.now(), stdout_path="", stderr_path="",
            run_dir="", output_dir="", memory_limit_mb=512,
            memory_cap_kind="none (enforcement disabled)",
        )
        note = executor_mod._memory_note(proc)
        assert "operator allocation 512 MB" in note
        assert "TRELLUM_DEFAULT_JOB_MEMORY_MB" in note
        assert "no enforced hard cap" in note
        assert "observed peak unavailable" in note

    @pytest.mark.skipif(os.name == "nt", reason="preexec_fn is POSIX-only")
    def test_rlimit_is_applied_with_headroom(
        self, fake_popen, queued_run, settings, monkeypatch
    ):
        settings.TRELLUM_DEFAULT_JOB_MEMORY_MB = 1000
        settings.TRELLUM_JOB_MEMORY_HEADROOM = 2.0
        settings.TRELLUM_JOB_MEMORY_ENFORCE = True
        Executor("w1").start_run(queued_run)

        preexec = fake_popen["procs"][-1].kwargs.get("preexec_fn")
        assert preexec is not None, "no memory cap was applied to the child"

        import resource

        applied: dict = {}
        monkeypatch.setattr(
            resource,
            "setrlimit",
            lambda which, limits: applied.update(which=which, limits=limits),
        )

        preexec()  # this is what the child runs between fork and exec

        assert applied["which"] == resource.RLIMIT_AS
        # 1000 MB declared x 2.0 headroom
        assert applied["limits"] == (2000 * 1024 * 1024, 2000 * 1024 * 1024)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX-only")
    def test_the_cap_actually_binds_a_real_child(self, queued_run, settings, tmp_path):
        """End-to-end proof the preexec_fn reaches a real process: a child
        given a tiny cap cannot allocate past it."""
        import subprocess as real_subprocess
        import sys

        cap_bytes = 64 * 1024 * 1024
        proc = real_subprocess.run(
            [sys.executable, "-c", "bytearray(256 * 1024 * 1024)"],
            preexec_fn=executor_mod._memory_limit_preexec(cap_bytes),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode != 0
        assert "MemoryError" in proc.stderr

    @pytest.mark.skipif(os.name == "nt", reason="preexec_fn is POSIX-only")
    def test_enforcement_can_be_switched_off(self, fake_popen, queued_run, settings):
        settings.TRELLUM_JOB_MEMORY_ENFORCE = False
        Executor("w1").start_run(queued_run)
        assert "preexec_fn" not in fake_popen["procs"][-1].kwargs

    def test_peak_memory_is_sampled_and_persisted(self, fake_popen, queued_run, monkeypatch):
        ex, = (Executor("w1"),)
        ex.start_run(queued_run)
        proc = fake_popen["procs"][-1]

        readings = iter([120, 640, 300])
        monkeypatch.setattr(
            executor_mod, "read_peak_rss_mb", lambda pid: next(readings, None)
        )

        ex.tick()
        ex.tick()
        ex.tick()
        proc.finish(0)
        ex.tick()

        queued_run.refresh_from_db()
        # The high-water mark survives, not the last reading.
        assert queued_run.peak_memory_mb == 640

    def test_memory_error_gets_an_actionable_message(
        self, fake_popen, queued_run, studio_tree, settings
    ):
        settings.TRELLUM_DEFAULT_JOB_MEMORY_MB = 512
        ex = Executor("w1")
        ex.start_run(queued_run)
        proc = fake_popen["procs"][-1]

        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stderr.log").write_text(
            "Traceback...\nMemoryError\n", encoding="utf-8"
        )
        proc.finish(1)
        ex.tick()

        from trellum.meta import read_meta

        meta = read_meta(str(studio_tree.output_dir / run.slug))
        error = meta.get("last_error") or ""
        assert "MEMORY ALLOCATION FAILED" in error
        assert "512 MB" in error
        assert "TRELLUM_DEFAULT_JOB_MEMORY_MB" in error


class FakeSandboxProc:
    """Popen-shaped stand-in for a sandbox container (mirrors SandboxProc)."""

    pid = None

    def __init__(self, container_id="cont-123"):
        self.container_id = container_id
        self._rc = None
        self._oom = False
        self.signals: list = []
        self.killed = False
        self.cleaned = False

    def poll(self):
        return self._rc

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    @property
    def oom_killed(self):
        return self._oom

    def peak_rss_mb(self):
        return 123

    def cleanup(self):
        self.cleaned = True

    def finish(self, code, oom=False):
        self._rc = code
        self._oom = oom


class FakeSandbox:
    def __init__(self, proc=None, error=None):
        self._proc = proc or FakeSandboxProc()
        self._error = error
        self.started = []

    def start(self, **kwargs):
        self.started.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._proc


@pytest.mark.django_db
class TestSandboxMode:
    """Docker-mode spawn: the Executor drives the container facade instead of
    Popen. The suite default is TRELLUM_SANDBOX=off, so this class opts in."""

    def _executor_with(self, sandbox):
        ex = Executor("w1")
        ex._get_sandbox = lambda: sandbox  # type: ignore[method-assign]
        return ex

    @pytest.fixture(autouse=True)
    def _docker_mode(self, settings):
        settings.TRELLUM_SANDBOX = "docker"
        settings.TRELLUM_SANDBOX_CPUS = 1.0

    def test_start_uses_sandbox_not_popen(self, fake_popen, queued_run):
        sandbox = FakeSandbox()
        ex = self._executor_with(sandbox)
        assert ex.start_run(queued_run) is True

        # No subprocess was spawned; the container carries the work.
        assert fake_popen.get("procs", []) == []
        assert sandbox.started, "sandbox.start was not called"
        # The flags the container runs are the shared build flags.
        assert "--no-serve" in sandbox.started[0]["cmd_flags"]

        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.RUNNING
        assert run.container_id == "cont-123"
        assert run.pid is None

    def test_oom_flag_maps_to_oom_killed(self, fake_popen, queued_run, studio_tree):
        proc = FakeSandboxProc()
        ex = self._executor_with(FakeSandbox(proc=proc))
        ex.start_run(queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        long_trace = "MemoryError misleading diagnostic\n" + "frame\n" * 90 + "FinalContainerError: OOM"
        Path(run.log_dir, "stderr.log").write_text(long_trace, encoding="utf-8")
        proc.finish(137, oom=True)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.OOM_KILLED
        assert "CONTAINER OOM" in run.stderr_tail
        assert "MEMORY ALLOCATION FAILED" not in run.stderr_tail
        assert long_trace in run.stderr_tail
        from trellum.meta import read_meta

        error = read_meta(str(studio_tree.output_dir / run.slug)).get("last_error") or ""
        assert error.startswith("CONTAINER OOM: Docker reported an out-of-memory kill.")
        assert "FinalContainerError: OOM" in error
        assert "exceeding its memory cap" not in error
        assert proc.cleaned is True  # container was removed

    def test_success_records_peak_and_cleans_up(self, fake_popen, queued_run):
        proc = FakeSandboxProc()
        ex = self._executor_with(FakeSandbox(proc=proc))
        ex.start_run(queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stdout.log").write_text("ok\n", encoding="utf-8")
        # peak sampled off the facade while running
        ex.tick()
        proc.finish(0)
        ex.tick()
        run.refresh_from_db()
        assert run.status == Run.SUCCESS
        assert run.peak_memory_mb == 123
        assert proc.cleaned is True

    def test_inspection_outage_keeps_live_run_and_allows_stop(self, fake_popen, queued_run):
        from apps.runner.sandbox import SandboxProc

        class FlakyContainer:
            id = "cont-outage"
            attrs = {"State": {"Running": True}}
            failed = False
            removed = False
            killed = []

            def reload(self):
                if not self.failed:
                    self.failed = True
                    raise requests.exceptions.ReadTimeout("slow daemon")

            def stats(self, stream=False):
                return {"memory_stats": {"max_usage": 0}}

            def kill(self, signal=None):
                self.killed.append(signal or "SIGKILL")

            def remove(self, force=False):
                self.removed = True

        container = FlakyContainer()
        proc = SandboxProc(container, run_id=str(queued_run.pk))
        ex = self._executor_with(FakeSandbox(proc=proc))
        ex.start_run(queued_run)

        Run.objects.filter(pk=queued_run.pk).update(stop_requested=True)
        ex.tick()
        run = Run.objects.get(pk=queued_run.pk)
        assert queued_run.pk in ex._procs
        assert run.status == Run.RUNNING
        assert not container.removed
        assert container.killed == ["SIGTERM"]

        container.attrs["State"] = {"Running": False, "ExitCode": 143, "OOMKilled": False}
        ex.tick()
        run.refresh_from_db()
        assert queued_run.pk not in ex._procs
        assert run.status == Run.STOPPED
        assert container.removed

    def test_timeout_escalates_during_inspection_outage_until_terminal(
        self, fake_popen, queued_run
    ):
        from apps.runner.sandbox import SandboxProc

        class UnavailableContainer:
            id = "cont-timeout-outage"
            attrs = {"State": {"Running": True}}
            removed = False
            killed = []

            def reload(self):
                raise requests.exceptions.ConnectionError("daemon unavailable")

            def stats(self, stream=False):
                return {"memory_stats": {"max_usage": 0}}

            def kill(self, signal=None):
                self.killed.append(signal or "SIGKILL")

            def remove(self, force=False):
                self.removed = True

        container = UnavailableContainer()
        proc = SandboxProc(container, run_id=str(queued_run.pk))
        ex = self._executor_with(FakeSandbox(proc=proc))
        ex.start_run(queued_run)
        running = ex._procs[queued_run.pk]
        running.timeout_seconds = 10
        running.started_at = timezone.now() - timedelta(seconds=60)

        ex.tick()
        assert queued_run.pk in ex._procs
        assert container.killed == ["SIGTERM"]
        assert not container.removed
        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.RUNNING

        running.sigterm_at = timezone.now() - timedelta(seconds=10)
        ex.tick()
        assert queued_run.pk in ex._procs
        assert container.killed == ["SIGTERM", "SIGKILL"]
        assert not container.removed
        run.refresh_from_db()
        assert run.status == Run.RUNNING

        container.reload = lambda: None
        container.attrs["State"] = {"Running": False, "ExitCode": 137, "OOMKilled": False}
        ex.tick()
        run.refresh_from_db()
        assert queued_run.pk not in ex._procs
        assert run.status == Run.TIMEOUT
        assert container.removed

    def test_start_failure_frees_the_slot(self, fake_popen, queued_run):
        from apps.runner.sandbox import SandboxError

        ex = self._executor_with(FakeSandbox(error=SandboxError("daemon down")))
        assert ex.start_run(queued_run) is False
        run = Run.objects.get(pk=queued_run.pk)
        assert run.status == Run.ERROR
        assert ex.running_count == 0

    def test_timeout_ladder_drives_the_container(self, fake_popen, queued_run):
        proc = FakeSandboxProc()
        ex = self._executor_with(FakeSandbox(proc=proc))
        ex.start_run(queued_run)
        rp = ex._procs[queued_run.pk]
        rp.timeout_seconds = 10
        rp.started_at = timezone.now() - timedelta(seconds=60)
        ex.tick()
        assert signal.SIGTERM in proc.signals
        rp.sigterm_at = rp.sigterm_at - timedelta(seconds=10)
        ex.tick()
        assert proc.killed is True


class TestCapacity:
    def test_budget_accounting_reserves_declared_memory(self):
        ex = Executor("w1", max_concurrent=10, memory_budget_mb=1000)
        assert ex.can_admit(600) is True
        ex._procs["a"] = RunningProc(
            run_id="a", slug="s", studio_id=1, process=None,
            started_at=timezone.now(), stdout_path="", stderr_path="",
            run_dir="", output_dir="", memory_limit_mb=600,
        )
        assert ex.reserved_memory_mb == 600
        assert ex.can_admit(600) is False
        assert ex.can_admit(400) is True

    def test_count_ceiling_still_applies_under_a_budget(self):
        ex = Executor("w1", max_concurrent=1, memory_budget_mb=100_000)
        ex._procs["a"] = RunningProc(
            run_id="a", slug="s", studio_id=1, process=None,
            started_at=timezone.now(), stdout_path="", stderr_path="",
            run_dir="", output_dir="", memory_limit_mb=10,
        )
        assert ex.can_admit(10) is False

    def test_no_budget_means_count_only(self):
        ex = Executor("w1", max_concurrent=3, memory_budget_mb=0)
        assert ex.can_admit(999_999) is True
        assert ex.exceeds_budget(999_999) is False

    def test_exceeds_budget_detects_the_impossible_run(self):
        ex = Executor("w1", memory_budget_mb=1000)
        assert ex.exceeds_budget(1001) is True
        assert ex.exceeds_budget(1000) is False


class TestPeakRssReader:
    def test_returns_none_when_proc_is_unavailable(self):
        # PID 0 has no /proc entry on any platform we run on.
        assert executor_mod.read_peak_rss_mb(0) is None

    @pytest.mark.skipif(
        not os.path.isdir("/proc/self"), reason="requires Linux procfs"
    )
    def test_reads_our_own_high_water_mark_on_linux(self):
        mb = executor_mod.read_peak_rss_mb(os.getpid())
        assert mb is not None and mb > 0

    @pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")
    def test_reads_our_own_peak_working_set_on_windows(self):
        mb = executor_mod.read_peak_rss_mb(os.getpid())
        assert mb is not None and mb > 0


class TestTimeouts:
    def test_every_run_gets_the_runner_default(self, fake_popen, queued_run):
        ex = Executor("w1", default_timeout=42)
        ex.start_run(queued_run)
        assert ex._procs[queued_run.pk].timeout_seconds == 42


def _wait_until(predicate, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for the attribution thread"
        time.sleep(0.05)


@pytest.mark.django_db(transaction=True)  # the attribution thread has its own connection
class TestDataSourcePreflight:
    """A report whose sources cannot connect yet never spawns; a build that
    failed on a source says which one."""

    @pytest.fixture
    def blocked_report(self, report_row):
        from apps.datasources.models import RepoDataSource

        RepoDataSource.objects.create(
            studio=report_row.studio, name="warehouse", type="postgres",
            config={"host": "db.internal"}, source_file="data-sources/config.yaml",
        )
        report_row.config = {**report_row.config, "data_sources": ["warehouse", "ghost"]}
        report_row.save(update_fields=["config"])
        return report_row

    def test_blocked_report_never_spawns(self, fake_popen, blocked_report, studio_tree):
        from trellum.meta import read_meta

        run = Run.objects.create(
            report=blocked_report, studio=studio_tree, slug=blocked_report.slug,
            status=Run.STARTING,
        )
        ex = Executor("w1")
        assert ex.start_run(run) is False
        assert "procs" not in fake_popen
        assert ex.running_count == 0
        run.refresh_from_db()
        assert run.status == Run.ERROR
        assert run.finished_at is not None and run.exit_code is None
        assert run.stderr_tail.splitlines() == [
            "Waiting for data source 'warehouse': missing: user, password",
            "Waiting for data source 'ghost': not declared in the repository and not configured",
        ]
        meta = read_meta(str(studio_tree.output_dir / run.slug))
        assert meta["last_status"] == "error"
        assert meta["last_error"].startswith("Waiting for data source 'warehouse'")
        assert meta["blocked_by"] == ["warehouse", "ghost"]

    @pytest.fixture
    def bound_report(self, report_row):
        from apps.datasources.models import DataSource

        report_row.config = {**report_row.config, "data_sources": ["warehouse"]}
        report_row.save(update_fields=["config"])
        return DataSource.objects.create(
            studio=report_row.studio, name="warehouse", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )

    def _fail_with(self, fake_popen, queued_run, monkeypatch, outcome, check=None):
        import apps.datasources.testing as testing_mod

        monkeypatch.setattr(testing_mod, "test_datasource", check or (lambda ds: outcome))
        ex = Executor("w1")
        ex.start_run(queued_run)
        run = Run.objects.get(pk=queued_run.pk)
        Path(run.log_dir, "stderr.log").write_text("Traceback: KeyError\n", encoding="utf-8")
        fake_popen["procs"][-1].finish(1)
        ex.tick()
        run.refresh_from_db()
        return run

    def test_a_stale_failure_is_rechecked_before_blocking(
        self, fake_popen, queued_run, bound_report, monkeypatch
    ):
        import apps.datasources.testing as testing_mod

        bound_report.last_check_at = timezone.now()
        bound_report.last_check_ok = False
        bound_report.last_check_error = "refused"
        bound_report.save()
        monkeypatch.setattr(testing_mod, "test_datasource", lambda ds: (True, "Connected."))
        assert Executor("w1").start_run(queued_run) is True
        assert fake_popen["procs"]
        bound_report.refresh_from_db()
        assert bound_report.last_check_ok is True

        # Failing again, and still failing on re-check: held, with the fresh reason.
        bound_report.last_check_ok = False
        bound_report.save()
        monkeypatch.setattr(testing_mod, "test_datasource", lambda ds: (False, "still refused"))
        again = Run.objects.create(
            report=queued_run.report, studio=queued_run.studio, slug=queued_run.slug,
            status=Run.STARTING,
        )
        assert Executor("w1").start_run(again) is False
        again.refresh_from_db()
        assert again.stderr_tail == "Waiting for data source 'warehouse': still refused"

    def test_failing_followup_check_is_secondary_evidence(
        self, fake_popen, queued_run, bound_report, studio_tree, monkeypatch
    ):
        from trellum.meta import read_meta
        from apps.datasources.status import source_failure

        run = self._fail_with(fake_popen, queued_run, monkeypatch, (False, "refused"))
        assert run.status == Run.ERROR
        _wait_until(
            lambda: "Subsequent data source check" in Run.objects.get(pk=run.pk).stderr_tail
        )
        run.refresh_from_db()
        assert run.stderr_tail.startswith("BUILD FAILED:")
        assert "Traceback: KeyError\n" in run.stderr_tail
        assert "Subsequent data source check for 'warehouse' failed: refused." in run.stderr_tail
        assert "This does not establish the cause of the build failure." in run.stderr_tail
        assert source_failure(run.stderr_tail) is None
        meta = read_meta(str(studio_tree.output_dir / run.slug))
        assert meta["last_error"].startswith("BUILD FAILED:")
        assert meta["last_error"].endswith("Traceback: KeyError\n")
        assert meta["blocked_by"] == []
        bound_report.refresh_from_db()
        assert bound_report.last_check_ok is False
        assert bound_report.last_check_error == "refused"

    def test_passing_check_leaves_the_error_alone(
        self, fake_popen, queued_run, bound_report, monkeypatch
    ):
        import time

        from apps.datasources.models import DataSource

        run = self._fail_with(fake_popen, queued_run, monkeypatch, (True, "Connected."))
        _wait_until(lambda: DataSource.objects.get(pk=bound_report.pk).last_check_at is not None)
        time.sleep(0.3)  # the thread's remaining step is deciding NOT to rewrite
        run.refresh_from_db()
        assert run.stderr_tail.endswith("Traceback: KeyError\n")
        bound_report.refresh_from_db()
        assert bound_report.last_check_ok is True

    def test_the_tick_does_not_wait_on_a_slow_check(
        self, fake_popen, queued_run, bound_report, monkeypatch
    ):
        import time

        def slow(ds):
            time.sleep(1.5)
            return False, "slow refusal"

        started = time.monotonic()
        run = self._fail_with(fake_popen, queued_run, monkeypatch, None, check=slow)
        assert time.monotonic() - started < 1.0
        assert run.status == Run.ERROR and run.stderr_tail.endswith("Traceback: KeyError\n")
        _wait_until(
            lambda: "Subsequent data source check for 'warehouse' failed: slow refusal"
            in Run.objects.get(pk=run.pk).stderr_tail
        )

    def test_a_newer_run_keeps_its_own_error(
        self, fake_popen, queued_run, bound_report, monkeypatch
    ):
        import time

        from apps.datasources.models import DataSource

        def slow(ds):
            time.sleep(1.0)
            return False, "slow refusal"

        run = self._fail_with(fake_popen, queued_run, monkeypatch, None, check=slow)
        Run.objects.create(
            report=queued_run.report, studio=queued_run.studio, slug=queued_run.slug,
        )
        _wait_until(lambda: DataSource.objects.get(pk=bound_report.pk).last_check_at is not None)
        time.sleep(0.3)
        run.refresh_from_db()
        assert run.stderr_tail.endswith("Traceback: KeyError\n")
