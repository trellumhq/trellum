"""Per-studio git sync against real local repositories."""
import subprocess
from pathlib import Path

import pytest

from apps.datasources.models import RepoDataSource
from apps.reports.models import Report
from apps.runner.gitsync import StudioGitSync, due_repos
from apps.runner.models import Run
from apps.studios.models import StudioRepo

pytestmark = pytest.mark.django_db


def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


@pytest.fixture
def upstream(tmp_path_factory):
    """A bare 'origin' repo + a working clone helper to push from."""
    base = tmp_path_factory.mktemp("git-upstream")
    bare = base / "origin.git"
    bare.mkdir()
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=base)

    work = base / "work"
    _git("clone", str(bare), str(work), cwd=base)
    _git("config", "user.email", "test@demo.example", cwd=work)
    _git("config", "user.name", "Test", cwd=work)

    def write_report(slug, content="print('hi')"):
        d = work / "reports" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.yaml").write_text(f"slug: {slug}\nname: {slug}\n", encoding="utf-8")
        (d / "generator.py").write_text(content, encoding="utf-8")

    def push(message="update"):
        _git("add", "-A", cwd=work)
        _git("commit", "-m", message, cwd=work)
        _git("push", "origin", "main", cwd=work)

    def delete_report(slug):
        import shutil

        shutil.rmtree(work / "reports" / slug)

    def write_events(text, at=""):
        """Write events.yaml at ``at`` (relative to the repo root, default:
        repo root itself -- the parent of the default ``path="reports"``)."""
        target = (work / at / "events.yaml") if at else (work / "events.yaml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def write_datasources(text):
        target = work / "data-sources" / "config.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def write_file(rel, content=b""):
        """Any file at ``rel`` in the repo -- a committed data file, say."""
        target = work / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    write_report("alpha")
    push("initial")
    return {
        "bare": bare,
        "work": work,
        "write_report": write_report,
        "push": push,
        "delete_report": delete_report,
        "write_events": write_events,
        "write_datasources": write_datasources,
        "write_file": write_file,
    }


@pytest.fixture
def studio_repo(studio_tree, upstream):
    return StudioRepo.objects.create(
        studio=studio_tree,
        repo_url=str(upstream["bare"]).replace("\\", "/"),
        branch="main",
        path="reports",
        auth_method="none",
        sync_interval_minutes=0,
    )


def _fresh_repo(studio_repo):
    return StudioRepo.objects.select_related("studio", "studio__org").get(pk=studio_repo.pk)


class TestSync:
    def test_initial_clone_copies_and_registers(self, studio_repo, studio_tree):
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert (studio_tree.reports_dir / "alpha" / "report.yaml").is_file()
        assert Report.objects.filter(studio=studio_tree, slug="alpha").exists()
        repo = _fresh_repo(studio_repo)
        assert repo.last_sync_at is not None
        assert len(repo.last_synced_sha) == 40
        assert repo.last_error == ""

    def test_pull_detects_changed_slugs(self, studio_repo, studio_tree, upstream):
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        upstream["write_report"]("beta")
        upstream["write_report"]("alpha", content="print('v2')")
        upstream["push"]("add beta, change alpha")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"]
        assert set(result["changed"]) == {"alpha", "beta"}
        assert (studio_tree.reports_dir / "beta").is_dir()
        assert "v2" in (studio_tree.reports_dir / "alpha" / "generator.py").read_text()
        assert Report.objects.filter(studio=studio_tree, slug="beta").exists()

    def test_no_changes_is_noop(self, studio_repo):
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"] and result["changed"] == [] and result["had_changes"] is False

    def test_deleted_report_removed_and_deregistered(self, studio_repo, studio_tree, upstream):
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        upstream["delete_report"]("alpha")
        upstream["push"]("remove alpha")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"]
        assert not (studio_tree.reports_dir / "alpha").exists()
        assert Report.objects.get(studio=studio_tree, slug="alpha").present_in_scan is False

    def test_auto_run_enqueues_changed(self, studio_repo, studio_tree, upstream):
        StudioRepo.objects.filter(pk=studio_repo.pk).update(auto_run_changed=True)
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert Run.objects.count() == 0  # initial full sync doesn't storm the queue? see below
        upstream["write_report"]("alpha", content="print('v3')")
        upstream["push"]("change alpha")
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        run = Run.objects.get()
        assert run.slug == "alpha"
        assert run.trigger == "git"

    def test_bad_repo_records_error(self, studio_tree):
        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url="https://example.invalid/nope.git",
            auth_method="none",
        )
        result = StudioGitSync(_fresh_repo(repo)).sync()
        assert result["ok"] is False
        repo.refresh_from_db()
        assert repo.last_error

    def test_token_scrubbed_from_errors(self, studio_tree):
        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url="https://example.invalid/nope.git",
            token="sekrit-token-123",
            auth_method="https_token",
        )
        result = StudioGitSync(_fresh_repo(repo)).sync()
        repo.refresh_from_db()
        assert "sekrit-token-123" not in (result["error"] + repo.last_error)


class TestArgvHardening:
    def test_validate_rejects_option_like_path(self):
        from apps.runner.gitsync import validate_repo_fields

        with pytest.raises(ValueError):
            validate_repo_fields("--upload-pack=/evil", "main")
        with pytest.raises(ValueError):
            validate_repo_fields("../escape", "main")
        with pytest.raises(ValueError):
            validate_repo_fields("reports", "--evil-branch")

    def test_validate_accepts_normal_values(self):
        from apps.runner.gitsync import validate_repo_fields

        validate_repo_fields("reports", "main")
        validate_repo_fields("dir/sub-reports_v2", "release/2024")

    def test_model_clean_blocks_hostile_path(self, studio_tree):
        from django.core.exceptions import ValidationError

        repo = StudioRepo(
            studio=studio_tree,
            repo_url="https://example.com/x.git",
            branch="main",
            path="--upload-pack=/evil",
            auth_method="none",
        )
        with pytest.raises(ValidationError):
            repo.full_clean()

    def test_sync_refuses_hostile_path(self, studio_repo):
        StudioRepo.objects.filter(pk=studio_repo.pk).update(path="--evil")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"] is False
        assert "unsafe" in result["error"]

    def test_sparse_checkout_uses_double_dash(self, studio_repo, monkeypatch):
        from apps.runner import gitsync as gs

        calls: list[list[str]] = []
        real = gs.StudioGitSync._run_git

        def _spy(self, *args, **kwargs):
            calls.append(list(args))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(gs.StudioGitSync, "_run_git", _spy)
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        sparse_set = [c for c in calls if c[:2] == ["sparse-checkout", "set"]]
        assert sparse_set, calls
        assert sparse_set[0] == ["sparse-checkout", "set", "--", "reports"]


class TestTokenHandling:
    def test_auth_env_supplies_askpass_not_url(self, studio_tree):
        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url="https://example.com/x.git",
            token="sekrit-token-123",
            auth_method="https_token",
        )
        sync = StudioGitSync(_fresh_repo(repo))
        env = sync._auth_env()
        assert env["TRELLUM_GIT_ASKPASS_TOKEN"] == "sekrit-token-123"
        assert env["TRELLUM_GIT_ASKPASS_USER"] == "x-token-auth"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_ASKPASS"].endswith("git-askpass.sh")

    def test_token_never_appears_in_git_argv(self, studio_tree, monkeypatch):
        from apps.runner import gitsync as gs

        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url="https://example.invalid/nope.git",
            token="sekrit-token-123",
            auth_method="https_token",
        )
        seen_args: list[tuple] = []

        def _spy(self, *args, **kwargs):
            seen_args.append(args)
            return 1, "", "network unreachable"  # short-circuit; we only inspect argv

        monkeypatch.setattr(gs.StudioGitSync, "_run_git", _spy)
        StudioGitSync(_fresh_repo(repo)).sync()
        for args in seen_args:
            assert "sekrit-token-123" not in " ".join(str(a) for a in args)

    def test_every_git_command_carries_auth_env(self, studio_tree, upstream, monkeypatch):
        # A --filter=blob:none clone makes origin a promisor remote, so
        # `git checkout` lazily fetches blobs and needs credentials too — not
        # just clone/fetch/pull. Every invocation must get GIT_ASKPASS (with a
        # token) and GIT_TERMINAL_PROMPT=0 (so a missing one fails fast).
        from apps.runner import gitsync as gs

        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url=str(upstream["bare"]).replace("\\", "/"),
            branch="main", path="reports",
            token="sekrit-token-123", auth_method="https_token",
            sync_interval_minutes=0,
        )
        real_run = gs.subprocess.run
        seen: list[tuple] = []

        def _rec(cmd, **kw):
            seen.append((tuple(cmd), kw.get("env")))
            return real_run(cmd, **kw)

        monkeypatch.setattr(gs.subprocess, "run", _rec)
        result = StudioGitSync(_fresh_repo(repo)).sync()
        assert result["ok"], result

        checkout = [env for cmd, env in seen if cmd[:2] == ("git", "checkout")]
        assert checkout, "checkout was never run"
        for env in (env for _, env in seen):
            assert env is not None and env.get("GIT_TERMINAL_PROMPT") == "0"
        # The checkout specifically must be able to authenticate.
        assert checkout[0].get("GIT_ASKPASS", "").endswith("git-askpass.sh")
        assert checkout[0].get("TRELLUM_GIT_ASKPASS_TOKEN") == "sekrit-token-123"

    def test_pull_scrubs_existing_remote_url(self, studio_repo, monkeypatch):
        # Prime a real clone, then confirm the next sync rewrites the remote to
        # the clean URL (belt-and-braces for tokens embedded by older versions).
        from apps.runner import gitsync as gs

        StudioGitSync(_fresh_repo(studio_repo)).sync()
        calls: list[list[str]] = []
        real = gs.StudioGitSync._run_git

        def _spy(self, *args, **kwargs):
            calls.append(list(args))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(gs.StudioGitSync, "_run_git", _spy)
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert any(c[:3] == ["remote", "set-url", "origin"] for c in calls), calls


class TestEventsYamlSync:
    """events.yaml delivery -- a real gap before this: only reports/ was
    synced, so a repo's events.yaml never reached project_root and neither
    chart annotations nor the annotations calendar ever saw it.

    The core assumption this whole feature rests on -- that cone-mode sparse
    checkout materializes files in every PARENT directory of the checked-out
    path, not just the path itself -- is proven here against a real git
    repo/checkout, not assumed.
    """

    def test_fresh_clone_delivers_root_level_events_yaml(self, studio_repo, studio_tree, upstream):
        # repo.path="reports" (the studio_repo fixture default) -> events.yaml's
        # parent is the repo root itself.
        upstream["write_events"]("events:\n  - date: 2024-01-01\n    label: root event\n")
        upstream["push"]("add events.yaml at repo root")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        dst = studio_tree.project_root / "events.yaml"
        assert dst.is_file()
        assert "root event" in dst.read_text(encoding="utf-8")

    def test_fresh_clone_delivers_root_level_metrics_yaml(self, studio_repo, studio_tree, upstream):
        # metrics.yaml is a project-root build file like events.yaml: without
        # it in project_root, every report that CLAIMS a metric fails to build
        # with metric-undefined. It must be mirrored the same way.
        (upstream["work"] / "metrics.yaml").write_text(
            "version: 1\nmetrics:\n  - name: gross_revenue\n    agg: sum\n"
            "    column: total_revenue\n", encoding="utf-8"
        )
        upstream["push"]("add metrics.yaml at repo root")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        dst = studio_tree.project_root / "metrics.yaml"
        assert dst.is_file()
        assert "gross_revenue" in dst.read_text(encoding="utf-8")

    def test_incremental_metrics_only_change_is_mirrored(self, studio_repo, studio_tree, upstream):
        # A push that touches only metrics.yaml (no report) must still mirror
        # it -- the pull-diff has to notice the root file, not just reports/.
        StudioGitSync(_fresh_repo(studio_repo)).sync()  # initial clone
        (upstream["work"] / "metrics.yaml").write_text(
            "version: 1\nmetrics:\n  - name: dau\n    agg: count\n", encoding="utf-8"
        )
        upstream["push"]("add metrics.yaml only")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        dst = studio_tree.project_root / "metrics.yaml"
        assert dst.is_file()
        assert "dau" in dst.read_text(encoding="utf-8")

    def test_nested_repo_path_finds_events_yaml_at_its_parent(self, studio_tree, upstream):
        # repo.path="project/reports" -> events.yaml belongs at
        # "project/events.yaml", its PARENT -- not the repo root, and not
        # inside reports/ itself.
        work = upstream["work"]
        nested = work / "project" / "reports" / "beta"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "report.yaml").write_text("slug: beta\nname: Beta\n", encoding="utf-8")
        (nested / "generator.py").write_text("print('hi')", encoding="utf-8")
        (work / "project" / "events.yaml").write_text(
            "events:\n  - date: 2025-05-05\n    label: nested event\n", encoding="utf-8"
        )
        upstream["push"]("add nested project/reports + events.yaml")

        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url=str(upstream["bare"]).replace("\\", "/"),
            branch="main",
            path="project/reports",
            auth_method="none",
            sync_interval_minutes=0,
        )
        result = StudioGitSync(_fresh_repo(repo)).sync()
        assert result["ok"], result
        dst = studio_tree.project_root / "events.yaml"
        assert dst.is_file()
        assert "nested event" in dst.read_text(encoding="utf-8")

    def test_nested_repo_path_detects_changed_slug_on_a_second_pull(self, studio_tree, upstream):
        # Regression: the changed-slug parser used to compare
        # `parts[0] == self.repo.path`, which can never match a nested path
        # ("project/reports" never equals "project", the first "/"-segment
        # of a diff line like "project/reports/beta/report.yaml") -- so a
        # nested-path studio's reports looked unchanged forever after the
        # initial clone, and the empty-set fix made that silent: nothing
        # got copied instead of everything.
        work = upstream["work"]
        nested = work / "project" / "reports" / "beta"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "report.yaml").write_text("slug: beta\nname: Beta\n", encoding="utf-8")
        (nested / "generator.py").write_text("print('v1')", encoding="utf-8")
        upstream["push"]("add nested project/reports/beta")

        repo = StudioRepo.objects.create(
            studio=studio_tree,
            repo_url=str(upstream["bare"]).replace("\\", "/"),
            branch="main",
            path="project/reports",
            auth_method="none",
            sync_interval_minutes=0,
        )
        StudioGitSync(_fresh_repo(repo)).sync()  # fresh clone -- full copy either way
        assert "v1" in (studio_tree.reports_dir / "beta" / "generator.py").read_text(encoding="utf-8")

        (nested / "generator.py").write_text("print('v2')", encoding="utf-8")
        upstream["push"]("change nested beta")

        result = StudioGitSync(_fresh_repo(repo)).sync()  # pull -- the actual regression path
        assert result["ok"], result
        assert result["changed"] == ["beta"]
        assert "v2" in (studio_tree.reports_dir / "beta" / "generator.py").read_text(encoding="utf-8")

    def test_events_only_push_is_detected_and_copied_on_pull(self, studio_repo, studio_tree, upstream):
        StudioGitSync(_fresh_repo(studio_repo)).sync()  # initial full clone, no events.yaml yet
        dst = studio_tree.project_root / "events.yaml"
        assert not dst.exists()

        upstream["write_events"]("events:\n  - date: 2024-02-02\n    label: v2\n")
        upstream["push"]("add events.yaml only, no report changes")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert result["changed"] == []  # no report slug changed
        assert dst.is_file()
        assert "v2" in dst.read_text(encoding="utf-8")

    def test_absence_never_deletes_the_working_copy(self, studio_repo, studio_tree, upstream):
        # Seeded directly, the way import_project does -- OUTSIDE of git sync.
        # The upstream repo used by `studio_repo` has no events.yaml at all.
        seed = studio_tree.project_root / "events.yaml"
        seed.write_text("events:\n  - date: 2024-01-01\n    label: seeded\n", encoding="utf-8")

        StudioGitSync(_fresh_repo(studio_repo)).sync()  # fresh clone
        assert "seeded" in seed.read_text(encoding="utf-8")

        upstream["write_report"]("beta")
        upstream["push"]("unrelated report change")
        StudioGitSync(_fresh_repo(studio_repo)).sync()  # pull
        assert "seeded" in seed.read_text(encoding="utf-8")

    def test_events_only_change_does_not_trigger_a_full_report_recopy(
        self, studio_repo, studio_tree, upstream, monkeypatch
    ):
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        from apps.runner import gitsync as gs

        calls: list = []
        real = gs.StudioGitSync._copy_reports

        def _spy(self, only_slugs=None):
            calls.append(only_slugs)
            return real(self, only_slugs=only_slugs)

        monkeypatch.setattr(gs.StudioGitSync, "_copy_reports", _spy)

        upstream["write_events"]("events:\n  - date: 2024-03-03\n    label: only-events-change\n")
        upstream["push"]("events only")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert result["changed"] == []
        # only_slugs was an explicit empty set (events-only diff), not None
        # (which would mean "full copy").
        assert calls == [set()]

    def test_copy_reports_with_an_explicit_empty_set_copies_nothing(
        self, studio_repo, studio_tree, upstream
    ):
        """Direct unit-level proof of the falsy-empty-set fix: the old
        ``only_slugs if only_slugs else os.listdir(...)`` treated an empty
        set the same as None and re-copied every report."""
        import time

        StudioGitSync(_fresh_repo(studio_repo)).sync()
        sync = StudioGitSync(_fresh_repo(studio_repo))
        target = studio_tree.reports_dir / "alpha" / "generator.py"
        before = target.read_text(encoding="utf-8")
        time.sleep(0.01)

        sync._copy_reports(only_slugs=set())

        after = target.read_text(encoding="utf-8")
        assert before == after  # untouched -- an empty set means "nothing changed"

    def test_root_file_relpath_uses_forward_slashes_even_when_nested(self, studio_tree, upstream):
        """Regression guard: os.path.join/dirname would emit backslashes on
        Windows, silently breaking the match against git's (always
        forward-slash) diff output."""
        from apps.studios.models import StudioRepo as _StudioRepo

        repo = _StudioRepo(studio=studio_tree, path="project/reports", branch="main")
        sync = StudioGitSync(repo)
        assert sync._root_file_relpath("events.yaml") == "project/events.yaml"
        assert sync._root_file_relpath("metrics.yaml") == "project/metrics.yaml"

        repo_root = _StudioRepo(studio=studio_tree, path="reports", branch="main")
        assert StudioGitSync(repo_root)._root_file_relpath("metrics.yaml") == "metrics.yaml"


class TestConfigYamlSync:
    """config.yaml -- a project-root build file like events.yaml/
    metrics.yaml now (PROJECT_ROOT_BUILD_FILES), no longer portal-owned and
    deliberately excluded. Carries the repo's declared `theme:`, parsed onto
    Studio.repo_theme by apps.reports.scan.sync_studio_registry (see
    apps/reports/tests/test_scan.py::TestRepoTheme for that half)."""

    def test_fresh_clone_delivers_root_level_config_yaml(self, studio_repo, studio_tree, upstream):
        (upstream["work"] / "config.yaml").write_text("theme: nord\n", encoding="utf-8")
        upstream["push"]("add config.yaml at repo root")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        dst = studio_tree.project_root / "config.yaml"
        assert dst.is_file()
        assert "nord" in dst.read_text(encoding="utf-8")

    def test_config_only_push_is_detected_and_copied_on_pull(self, studio_repo, studio_tree, upstream):
        StudioGitSync(_fresh_repo(studio_repo)).sync()  # initial full clone, no config.yaml yet
        dst = studio_tree.project_root / "config.yaml"
        assert not dst.exists()

        (upstream["work"] / "config.yaml").write_text("theme: money\n", encoding="utf-8")
        upstream["push"]("add config.yaml only, no report changes")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert result["changed"] == []  # no report slug changed
        assert dst.is_file()
        assert "money" in dst.read_text(encoding="utf-8")

    def test_absence_never_deletes_the_working_copy(self, studio_repo, studio_tree, upstream):
        # Seeded directly (import_project's path), OUTSIDE of git sync -- the
        # upstream repo used by `studio_repo` has no config.yaml at all.
        seed = studio_tree.project_root / "config.yaml"
        seed.write_text("theme: sunset\n", encoding="utf-8")

        StudioGitSync(_fresh_repo(studio_repo)).sync()  # fresh clone
        assert "sunset" in seed.read_text(encoding="utf-8")

        upstream["write_report"]("beta")
        upstream["push"]("unrelated report change")
        StudioGitSync(_fresh_repo(studio_repo)).sync()  # pull
        assert "sunset" in seed.read_text(encoding="utf-8")

    def test_a_config_only_push_stamps_repo_theme_on_the_very_same_sync(
        self, studio_repo, studio_tree, upstream
    ):
        # Root files must land on disk BEFORE the registry re-scan reads
        # them in the SAME sync() call -- otherwise a theme-only push
        # (changed_slugs empty, so sync_studio_registry runs but scans a
        # reports/ dir with nothing new in it) would stamp Studio.repo_theme
        # one full sync cycle late.
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == ""

        (upstream["work"] / "config.yaml").write_text("theme: nord\n", encoding="utf-8")
        upstream["push"]("declare a theme, no report changes")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        studio_tree.refresh_from_db()
        assert studio_tree.repo_theme == "nord"


class TestDueRepos:
    def test_requested_is_due(self, studio_repo):
        assert due_repos() == []  # interval 0, not requested
        StudioRepo.objects.filter(pk=studio_repo.pk).update(sync_requested=True)
        assert [r.pk for r in due_repos()] == [studio_repo.pk]

    def test_interval_elapsed_is_due(self, studio_repo):
        from django.utils import timezone

        StudioRepo.objects.filter(pk=studio_repo.pk).update(
            sync_interval_minutes=5,
            last_sync_at=timezone.now() - timezone.timedelta(minutes=6),
        )
        assert [r.pk for r in due_repos()] == [studio_repo.pk]

    def test_recent_sync_not_due(self, studio_repo):
        from django.utils import timezone

        StudioRepo.objects.filter(pk=studio_repo.pk).update(
            sync_interval_minutes=5, last_sync_at=timezone.now()
        )
        assert due_repos() == []


class TestFullPipeline:
    def test_push_sync_autorun_matches_sacred_flow(
        self, studio_repo, studio_tree, upstream, monkeypatch
    ):
        """Push → sync → registry → queue: the product's core loop."""
        StudioRepo.objects.filter(pk=studio_repo.pk).update(auto_run_changed=True)
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        upstream["write_report"]("gamma")
        upstream["push"]("new report via git push")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()

        assert result["changed"] == ["gamma"]
        report = Report.objects.get(studio=studio_tree, slug="gamma")
        assert report.present_in_scan
        run = Run.objects.get(report=report)
        assert run.trigger == "git"
        assert run.status == Run.QUEUED


_WAREHOUSE = (
    "sources:\n"
    "  warehouse:\n"
    "    type: postgres\n"
    "    description: Main warehouse\n"
    "    host: db.demo.internal\n"
    "    port: 5439\n"
    "    database: analytics\n"
    "    credentials:\n"
    "      local: BI_WAREHOUSE\n"
)


class TestRepoDataSourceSync:
    """data-sources/config.yaml is READ from the commit (``git show``) into
    RepoDataSource rows -- never checked out into the sparse cone, never
    copied into project_root (apps.datasources.materialize owns that path)."""

    def test_fresh_clone_mirrors_the_declaration_without_checking_it_out(
        self, studio_repo, studio_tree, upstream
    ):
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert row.type == "postgres"
        assert row.source_file == "data-sources/config.yaml"
        assert row.config == {
            "description": "Main warehouse", "host": "db.demo.internal",
            "port": 5439, "database": "analytics",
        }
        assert row.present is True
        assert not (studio_tree.repo_dir / "data-sources").exists()
        assert not (studio_tree.datasources_dir / "config.yaml").exists()

    def test_a_sync_with_no_new_commits_still_mirrors_the_declaration(
        self, studio_repo, studio_tree, upstream
    ):
        """A clone already at origin's HEAD has nothing to pull -- and still
        has declarations to mirror (the first sync after an upgrade)."""
        from apps.datasources.models import DataSource

        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
        RepoDataSource.objects.filter(studio=studio_tree).delete()
        legacy = DataSource.objects.create(
            studio=studio_tree, name="legacy", type="postgres", awaiting_first_sync=True,
        )

        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"] and result["had_changes"] is False
        assert RepoDataSource.objects.filter(studio=studio_tree, name="warehouse").exists()
        legacy.refresh_from_db()
        assert legacy.awaiting_first_sync is False

    def test_a_declaration_only_push_updates_type_and_config(
        self, studio_repo, studio_tree, upstream
    ):
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        upstream["write_datasources"](
            _WAREHOUSE.replace("postgres", "mysql").replace("db.demo.internal", "db2.demo.internal")
        )
        upstream["push"]("move the warehouse, no report changes")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"] and result["changed"] == []
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert row.type == "mysql"
        assert row.config["host"] == "db2.demo.internal"
        assert row.present is True

    def test_removed_source_is_flagged_absent_and_kept(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        upstream["write_datasources"]("sources: {}\n")
        upstream["push"]("drop the warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert row.present is False
        assert row.type == "postgres"  # last declared shape is kept

    def test_inline_report_yaml_declaration_is_mirrored(self, studio_repo, studio_tree, upstream):
        upstream["write_report"]("beta")
        (upstream["work"] / "reports" / "beta" / "report.yaml").write_text(
            "slug: beta\nname: beta\ndata_sources:\n"
            "  - warehouse\n"  # a string references a central source
            "  - name: fixtures\n    type: sqlite\n    path: data/fixtures.db\n",
            encoding="utf-8",
        )
        upstream["push"]("beta declares an inline source")

        assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
        names = set(RepoDataSource.objects.filter(studio=studio_tree).values_list("name", flat=True))
        assert names == {"fixtures"}
        row = RepoDataSource.objects.get(studio=studio_tree, name="fixtures")
        assert row.type == "sqlite"
        assert row.config == {"path": "data/fixtures.db"}
        assert row.source_file == "reports/beta/report.yaml"

    def test_central_declaration_wins_over_inline_on_a_name_clash(
        self, studio_repo, studio_tree, upstream
    ):
        upstream["write_datasources"](_WAREHOUSE)
        (upstream["work"] / "reports" / "alpha" / "report.yaml").write_text(
            "slug: alpha\nname: alpha\ndata_sources:\n"
            "  - name: warehouse\n    type: sqlite\n    path: local.db\n",
            encoding="utf-8",
        )
        upstream["push"]("both declare warehouse")

        assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert row.type == "postgres"
        assert row.source_file == "data-sources/config.yaml"

    @pytest.mark.parametrize(
        "bad",
        [
            "sources: [\n  password: s3cret-pw\n",  # YAML error on the secret line
            "sources:\n  - password: s3cret-pw\n",  # a list, not a mapping
        ],
    )
    def test_malformed_declaration_warns_and_leaves_rows_untouched(
        self, studio_repo, studio_tree, upstream, bad
    ):
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        upstream["write_datasources"](bad)
        upstream["push"]("break the declaration")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert row.present is True and row.type == "postgres"
        repo = _fresh_repo(studio_repo)
        assert "data-sources/config.yaml" in repo.last_error
        assert "s3cret-pw" not in repo.last_error  # the error never quotes the file
        assert len(repo.last_synced_sha) == 40

    def test_deleting_the_declaration_flags_every_source_absent(
        self, studio_repo, studio_tree, upstream
    ):
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        (upstream["work"] / "data-sources" / "config.yaml").unlink()
        upstream["push"]("delete the declaration")
        assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
        assert RepoDataSource.objects.get(studio=studio_tree, name="warehouse").present is False
        assert _fresh_repo(studio_repo).last_error == ""

    def test_unreadable_declaration_is_a_warning_and_leaves_rows_untouched(
        self, studio_repo, studio_tree, upstream, monkeypatch
    ):
        # `git show` on a partial clone is a blob fetch over the network. If it
        # fails, that must not read as "the file is gone".
        upstream["write_datasources"](_WAREHOUSE)
        upstream["push"]("declare a warehouse")
        StudioGitSync(_fresh_repo(studio_repo)).sync()

        from apps.runner import gitsync as gs

        real = gs.StudioGitSync._run_git

        def _failing_show(self, *args, **kwargs):
            if args[0] == "show":
                return 128, "", "fatal: unable to read blob"
            return real(self, *args, **kwargs)

        monkeypatch.setattr(gs.StudioGitSync, "_run_git", _failing_show)
        upstream["write_report"]("beta")
        upstream["push"]("unrelated report change")
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert RepoDataSource.objects.get(studio=studio_tree, name="warehouse").present is True
        assert "data-sources/config.yaml" in _fresh_repo(studio_repo).last_error

    def test_repo_without_a_declaration_yields_no_rows_and_no_error(
        self, studio_repo, studio_tree
    ):
        result = StudioGitSync(_fresh_repo(studio_repo)).sync()
        assert result["ok"], result
        assert not RepoDataSource.objects.filter(studio=studio_tree).exists()
        assert _fresh_repo(studio_repo).last_error == ""

    def test_secrets_in_the_declaration_are_never_stored(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](
            _WAREHOUSE + "    user: svc_reports\n    password: s3cret-pw\n"
            "    credentials_json: '{\"type\": \"service_account\"}'\n"
            "    api_token: t0ken\n    private_key: k3y\n"  # a custom driver's names
        )
        upstream["push"]("someone committed a password")

        assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
        row = RepoDataSource.objects.get(studio=studio_tree, name="warehouse")
        assert "s3cret-pw" not in str(row.config)
        assert set(row.config) == {"description", "host", "port", "database"}


_TWO_SOURCES = _WAREHOUSE + "  other:\n    type: sqlite\n    path: other.db\n"
_TWO_SOURCES_CHANGED = (
    _WAREHOUSE.replace("postgres", "mysql") + "  third:\n    type: sqlite\n    path: third.db\n"
)


def _sync(studio_repo):
    return StudioGitSync(_fresh_repo(studio_repo)).sync()


def _set(studio_repo, **fields):
    StudioRepo.objects.filter(pk=studio_repo.pk).update(**fields)


class TestPublishMode:
    """fetch() records what a publish would change; publish() applies it
    only when the mode or a request allows."""

    @pytest.fixture
    def published(self, studio_repo, upstream):
        """Auto-published alpha + gamma + two data sources; then switched to
        manual so later pushes only become pending."""
        upstream["write_report"]("gamma")
        upstream["write_datasources"](_TWO_SOURCES)
        upstream["push"]("add gamma and sources")
        assert _sync(studio_repo)["ok"]
        _set(studio_repo, publish_mode="manual")
        return _fresh_repo(studio_repo).last_synced_sha

    @pytest.fixture
    def two_pushes(self, published, upstream):
        upstream["write_report"]("beta")
        upstream["write_events"]("events:\n  - date: 2024-01-01\n    label: e\n")
        upstream["push"]("first")
        upstream["write_report"]("alpha", content="print('v2')")
        (upstream["work"] / "reports" / "gamma" / "report.yaml").unlink()
        upstream["write_datasources"](_TWO_SOURCES_CHANGED)
        upstream["push"]("second")
        return published

    def test_manual_fetch_records_pending_and_touches_nothing(
        self, studio_repo, studio_tree, two_pushes
    ):
        result = _sync(studio_repo)
        assert result["ok"], result
        assert result["had_changes"] is False and result["changed"] == []
        repo = _fresh_repo(studio_repo)
        pending = repo.pending_changes
        assert pending["from"] == two_pushes == repo.last_synced_sha
        assert pending["to"] == repo.remote_sha != two_pushes
        assert pending["initial"] is False and pending["rewritten"] is False
        assert pending["branch"] == "main"
        assert [c["message"] for c in pending["commits"]] == ["second", "first"]
        assert pending["commits_total"] == 2
        assert "reports/beta/report.yaml" in pending["commits"][1]["files"]
        assert pending["commits"][0]["author"] == "Test" and pending["commits"][0]["date"]
        assert pending["reports"] == {"added": ["beta"], "modified": ["alpha"], "removed": ["gamma"]}
        assert pending["root_files"] == ["data-sources/config.yaml", "events.yaml"]
        assert pending["datasources"] == {
            "added": ["third"], "removed": ["other"], "changed": ["warehouse"],
        }
        assert pending["warnings"] == []
        # Nothing reached the project dir, the registry or the published sha.
        assert not (studio_tree.reports_dir / "beta").exists()
        assert (studio_tree.reports_dir / "gamma" / "report.yaml").is_file()
        assert "v2" not in (studio_tree.reports_dir / "alpha" / "generator.py").read_text()
        assert not (studio_tree.project_root / "events.yaml").exists()
        assert not Report.objects.filter(studio=studio_tree, slug="beta").exists()
        assert RepoDataSource.objects.get(studio=studio_tree, name="warehouse").type == "postgres"
        assert repo.remote_checked_at is not None

    def test_publish_request_applies_pending_and_records_who(
        self, studio_repo, studio_tree, two_pushes, make_user
    ):
        from apps.studios.models import RepoPublish

        _sync(studio_repo)
        user = make_user("dev@demo.example")
        _set(studio_repo, publish_requested=True, publish_requested_by=user, sync_reason="webhook")
        result = _sync(studio_repo)
        assert result["ok"], result
        assert result["changed"] == ["alpha", "beta", "gamma"] and result["had_changes"] is True
        assert (studio_tree.reports_dir / "beta").is_dir()
        assert not (studio_tree.reports_dir / "gamma" / "report.yaml").exists()
        assert "v2" in (studio_tree.reports_dir / "alpha" / "generator.py").read_text()
        assert (studio_tree.project_root / "events.yaml").is_file()
        assert Report.objects.get(studio=studio_tree, slug="gamma").present_in_scan is False
        assert RepoDataSource.objects.get(studio=studio_tree, name="warehouse").type == "mysql"
        # The sparse cone is still just the reports dir.
        assert not (studio_tree.repo_dir / "data-sources").exists()

        repo = _fresh_repo(studio_repo)
        row = RepoPublish.objects.filter(studio=studio_tree).first()
        assert row.status == "ok" and row.trigger == "manual" and row.published_by == user
        assert row.from_sha == two_pushes and row.to_sha == repo.last_synced_sha == repo.remote_sha
        assert row.summary["reports"]["added"] == ["beta"]
        assert repo.pending_changes == {} and repo.publish_requested is False
        assert repo.publish_requested_by is None and repo.sync_reason == "schedule"
        assert repo.last_error == ""

    def test_fresh_clone_in_manual_mode_waits_for_a_publish(self, studio_repo, studio_tree):
        from apps.studios.models import RepoPublish

        _set(studio_repo, publish_mode="manual")
        assert _sync(studio_repo)["ok"]
        repo = _fresh_repo(studio_repo)
        assert repo.last_synced_sha == "" and repo.pending_changes["initial"] is True
        assert repo.pending_changes["from"] is None
        assert repo.pending_changes["reports"]["added"] == ["alpha"]
        assert not (studio_tree.reports_dir / "alpha").exists()
        assert not Report.objects.filter(studio=studio_tree).exists()
        assert not RepoPublish.objects.filter(studio=studio_tree).exists()

        _set(studio_repo, publish_requested=True)
        assert _sync(studio_repo)["ok"]
        assert (studio_tree.reports_dir / "alpha" / "report.yaml").is_file()
        assert Report.objects.filter(studio=studio_tree, slug="alpha").exists()
        assert RepoPublish.objects.get(studio=studio_tree).trigger == "initial"

    def test_auto_mode_publishes_on_fetch_with_history(self, studio_repo, studio_tree, upstream):
        from apps.studios.models import RepoPublish

        _sync(studio_repo)
        first = _fresh_repo(studio_repo).last_synced_sha
        upstream["write_report"]("beta")
        upstream["push"]("add beta")
        result = _sync(studio_repo)
        assert result["changed"] == ["beta"]
        rows = list(RepoPublish.objects.filter(studio=studio_tree))
        assert [r.trigger for r in rows] == ["auto", "initial"]
        assert rows[0].from_sha == first and rows[0].to_sha == _fresh_repo(studio_repo).last_synced_sha
        assert rows[0].summary["reports"]["added"] == ["beta"] and rows[0].status == "ok"
        assert rows[1].from_sha == "" and rows[1].summary["initial"] is True

    def test_webhook_reason_stamps_the_trigger(self, studio_repo, studio_tree, upstream):
        from apps.studios.models import RepoPublish

        _sync(studio_repo)
        upstream["write_report"]("beta")
        upstream["push"]("add beta")
        _set(studio_repo, sync_requested=True, sync_reason="webhook")
        assert _sync(studio_repo)["changed"] == ["beta"]
        assert RepoPublish.objects.filter(studio=studio_tree).first().trigger == "webhook"
        repo = _fresh_repo(studio_repo)
        assert repo.sync_reason == "schedule" and repo.sync_requested is False

    def test_force_push_is_rewritten_and_lists_the_whole_log(self, studio_repo, upstream):
        _sync(studio_repo)
        upstream["write_report"]("beta")
        upstream["push"]("add beta")
        _sync(studio_repo)
        _set(studio_repo, publish_mode="manual")
        _git("commit", "--amend", "-m", "rewritten", cwd=upstream["work"])
        _git("push", "--force", "origin", "main", cwd=upstream["work"])

        assert _sync(studio_repo)["ok"]
        pending = _fresh_repo(studio_repo).pending_changes
        assert pending["rewritten"] is True and pending["initial"] is False
        assert pending["full_listing"] is False  # the published commit is still local
        assert pending["reports"] == {"added": [], "modified": [], "removed": []}
        assert [c["message"] for c in pending["commits"]] == ["rewritten", "initial"]

        _set(studio_repo, publish_requested=True)
        result = _sync(studio_repo)
        assert result["ok"] and result["changed"] == []
        assert _fresh_repo(studio_repo).last_synced_sha == pending["to"]

    def test_commit_list_is_capped_at_100(self, studio_repo, upstream):
        _sync(studio_repo)
        _set(studio_repo, publish_mode="manual")
        for i in range(101):
            _git("commit", "--allow-empty", "-m", f"c{i}", cwd=upstream["work"])
        _git("push", "origin", "main", cwd=upstream["work"])
        assert _sync(studio_repo)["ok"]
        pending = _fresh_repo(studio_repo).pending_changes
        commits = pending["commits"]
        assert len(commits) == 100 and commits[0]["message"] == "c100"
        assert pending["commits_total"] == 101

    def test_a_request_pinned_to_a_moved_remote_is_dropped(
        self, studio_repo, studio_tree, two_pushes, upstream, make_user
    ):
        from apps.studios.models import RepoPublish

        _sync(studio_repo)
        reviewed = _fresh_repo(studio_repo).remote_sha
        user = make_user("dev@demo.example")
        _set(studio_repo, publish_requested=True, publish_requested_by=user,
             publish_requested_to=reviewed, publish_rebuild_override=True)
        # A push lands between the review and the runner's tick.
        _git("commit", "--allow-empty", "-m", "third", cwd=upstream["work"])
        _git("push", "origin", "main", cwd=upstream["work"])
        result = _sync(studio_repo)
        assert result["ok"] and result["had_changes"] is False and result["changed"] == []
        repo = _fresh_repo(studio_repo)
        assert not (studio_tree.reports_dir / "beta").exists()
        assert not RepoPublish.objects.filter(studio=studio_tree, trigger="manual").exists()
        assert repo.publish_requested is False and repo.publish_requested_by is None
        assert repo.publish_requested_to == "" and repo.publish_rebuild_override is None
        assert repo.last_error == "The remote moved since you reviewed it — review and publish again."
        # The new head is pending, ready for another review.
        assert repo.pending_changes["to"] == repo.remote_sha != reviewed
        assert repo.pending_changes["commits"][0]["message"] == "third"
        assert repo.pending_changes["commits_total"] == 3

        # Reviewing the new head and publishing it goes through.
        _set(studio_repo, publish_requested=True, publish_requested_by=user,
             publish_requested_to=repo.remote_sha)
        result = _sync(studio_repo)
        assert result["ok"] and result["had_changes"] is True
        repo = _fresh_repo(studio_repo)
        assert (studio_tree.reports_dir / "beta").is_dir()
        assert repo.last_synced_sha == repo.remote_sha and repo.last_error == ""
        assert RepoPublish.objects.get(studio=studio_tree, trigger="manual").published_by == user

    def test_failed_publish_leaves_the_project_dir_alone(self, studio_repo, studio_tree, upstream):
        from apps.studios.models import RepoPublish

        _sync(studio_repo)
        before = _fresh_repo(studio_repo).last_synced_sha
        upstream["write_report"]("beta")
        (upstream["work"] / "reports" / "beta" / "report.yaml").write_text(
            "slug: [\nname: s3cret-name\n", encoding="utf-8"
        )
        upstream["write_report"]("alpha", content="print('v2')")
        upstream["push"]("break beta")

        result = _sync(studio_repo)
        assert result["ok"] and result["had_changes"] is False
        repo = _fresh_repo(studio_repo)
        row = RepoPublish.objects.filter(studio=studio_tree).first()
        assert row.status == "error" and row.trigger == "auto"
        assert "report.yaml invalid" in row.error and "beta/report.yaml" in row.error
        assert "s3cret-name" not in row.error and repo.last_error == row.error
        assert repo.last_synced_sha == before and row.to_sha == repo.remote_sha
        assert repo.pending_changes["to"] == repo.remote_sha  # retained
        assert not (studio_tree.reports_dir / "beta").exists()
        assert "v2" not in (studio_tree.reports_dir / "alpha" / "generator.py").read_text()

        # Not retried on its own -- one error row per commit.
        assert _sync(studio_repo)["ok"]
        assert RepoPublish.objects.filter(studio=studio_tree, status="error").count() == 1

        upstream["write_report"]("beta")  # a valid report.yaml again
        upstream["push"]("fix beta")
        result = _sync(studio_repo)
        assert result["changed"] == ["alpha", "beta"]
        assert (studio_tree.reports_dir / "beta").is_dir()
        assert _fresh_repo(studio_repo).last_error == ""

    def test_rebuild_override_is_consumed_by_one_publish(self, studio_repo, studio_tree, upstream):
        _sync(studio_repo)
        upstream["write_report"]("alpha", content="print('v2')")
        upstream["push"]("v2")
        _set(studio_repo, publish_requested=True, publish_rebuild_override=True)
        assert _sync(studio_repo)["changed"] == ["alpha"]
        assert Run.objects.filter(slug="alpha", trigger="git").count() == 1
        assert _fresh_repo(studio_repo).publish_rebuild_override is None

        upstream["write_report"]("alpha", content="print('v3')")
        upstream["push"]("v3")
        assert _sync(studio_repo)["changed"] == ["alpha"]
        assert Run.objects.count() == 1  # auto_run_changed is still off

    def test_quota_overage_is_a_pending_warning(self, studio_repo, org, upstream, settings):
        from apps.orgs.models import OrgQuota

        settings.TRELLUM_QUOTAS_ENABLED = True
        _sync(studio_repo)
        OrgQuota.objects.create(org=org, max_reports=1)
        _set(studio_repo, publish_mode="manual")
        upstream["write_report"]("beta")
        upstream["push"]("one too many")
        assert _sync(studio_repo)["ok"]
        warnings = _fresh_repo(studio_repo).pending_changes["warnings"]
        assert len(warnings) == 1 and "limited to 1 reports" in warnings[0]

    def test_malformed_declaration_is_a_pending_warning(self, studio_repo, upstream):
        _sync(studio_repo)
        _set(studio_repo, publish_mode="manual")
        upstream["write_datasources"]("sources: [\n  password: s3cret-pw\n")
        upstream["push"]("break the declaration")
        assert _sync(studio_repo)["ok"]
        pending = _fresh_repo(studio_repo).pending_changes
        assert pending["datasources"] == {"added": [], "removed": [], "changed": []}
        assert len(pending["warnings"]) == 1
        assert pending["warnings"][0].startswith("data-sources/config.yaml:")
        assert "s3cret-pw" not in pending["warnings"][0]

    def test_publish_request_makes_a_repo_due(self, studio_repo):
        assert due_repos() == []
        _set(studio_repo, publish_requested=True)
        assert [r.pk for r in due_repos()] == [studio_repo.pk]

    def test_force_push_diffs_against_the_published_commit_when_it_is_local(
        self, studio_repo, studio_tree, upstream
    ):
        _sync(studio_repo)
        upstream["write_report"]("beta")
        upstream["write_report"]("gamma")
        upstream["push"]("add beta and gamma")
        _sync(studio_repo)
        _set(studio_repo, auto_run_changed=True)
        assert Run.objects.count() == 0

        # Amend the published commit: beta gone, gamma changed, alpha as it was.
        work = upstream["work"]
        _git("rm", "-r", "-q", "reports/beta", cwd=work)
        upstream["write_report"]("gamma", content="print('v2')")
        _git("add", "-A", cwd=work)
        _git("commit", "--amend", "-q", "-m", "rewritten", cwd=work)
        _git("push", "--force", "origin", "main", cwd=work)

        result = _sync(studio_repo)
        assert result["ok"], result
        assert result["changed"] == ["beta", "gamma"]
        repo = _fresh_repo(studio_repo)
        summary = repo.studio.publishes.first().summary
        assert summary["rewritten"] is True and summary["full_listing"] is False
        assert summary["reports"] == {"added": [], "modified": ["gamma"], "removed": ["beta"]}
        assert [c["message"] for c in summary["commits"]] == ["rewritten", "initial"]
        assert not (studio_tree.reports_dir / "beta").exists()
        assert Report.objects.get(studio=studio_tree, slug="beta").present_in_scan is False
        assert "v2" in (studio_tree.reports_dir / "gamma" / "generator.py").read_text()
        assert list(Run.objects.values_list("slug", flat=True)) == ["gamma"]

    def test_a_published_commit_missing_from_the_cache_falls_back_to_a_full_listing(
        self, studio_repo, studio_tree, upstream
    ):
        _set(studio_repo, auto_run_changed=True)
        _sync(studio_repo)
        upstream["write_report"]("beta")
        upstream["push"]("add beta")
        _sync(studio_repo)
        assert list(Run.objects.values_list("slug", flat=True)) == ["beta"]

        # The state a shallow re-clone leaves behind: the published commit is
        # not an object the cache holds, so there is nothing to diff against.
        _set(studio_repo, last_synced_sha="f" * 40)
        upstream["write_report"]("gamma")
        upstream["push"]("add gamma")
        result = _sync(studio_repo)
        assert result["ok"], result
        repo = _fresh_repo(studio_repo)
        summary = repo.studio.publishes.first().summary
        assert summary["rewritten"] is True and summary["full_listing"] is True
        assert summary["reports"]["added"] == ["alpha", "beta", "gamma"]
        assert summary["reports"]["removed"] == []
        for slug in ("alpha", "beta", "gamma"):
            assert (studio_tree.reports_dir / slug / "report.yaml").is_file()
        assert repo.last_synced_sha == summary["to"]
        assert Run.objects.count() == 1  # nothing else was enqueued

    def test_a_failed_fetch_spends_the_publish_request(self, studio_repo, make_user):
        _sync(studio_repo)
        user = make_user("dev@demo.example")
        _set(
            studio_repo, repo_url="https://example.invalid/nope.git", sync_requested=True,
            publish_requested=True, publish_requested_by=user, publish_rebuild_override=True,
        )
        assert [r.pk for r in due_repos()] == [studio_repo.pk]
        result = _sync(studio_repo)
        assert result["ok"] is False and "fetch failed" in result["error"]
        repo = _fresh_repo(studio_repo)
        assert repo.last_error and repo.sync_requested is False
        assert repo.publish_requested is False and repo.publish_requested_by is None
        assert repo.publish_rebuild_override is None
        assert due_repos() == []


_DEMO_DB = "sources:\n  demo_db:\n    type: sqlite\n    path: data/demo.sqlite\n"
_V1 = b"SQLite format 3\x00 rows v1"
_V2 = b"SQLite format 3\x00 rows v2"


def _declaring(path):
    return f"sources:\n  demo_db:\n    type: sqlite\n    path: {path}\n"


def _state_of(studio, name):
    from apps.datasources.status import source_states

    return next(s for s in source_states(studio) if s.name == name)


class TestDeclaredFileSources:
    """A file source declared with a repo-relative path (``data/demo.sqlite``)
    must reach ``<studio>/project/``. The sparse cone covers only the reports
    directory, so the blob is copied straight out of the published commit --
    the cone never widens, and an undeclared file is never fetched at all."""

    def test_a_declared_file_reaches_the_project_dir(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")

        assert _sync(studio_repo)["ok"]
        assert (studio_tree.project_root / "data" / "demo.sqlite").read_bytes() == _V1
        # ...without materializing data/ in the checkout.
        assert not (studio_tree.repo_dir / "data").exists()
        row = RepoDataSource.objects.get(studio=studio_tree, name="demo_db")
        assert row.shipped_path == "data/demo.sqlite" and row.file_error == ""
        assert _fresh_repo(studio_repo).last_error == ""
        assert _state_of(studio_tree, "demo_db").state == "connected"

    def test_a_changed_file_is_recopied(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")
        assert _sync(studio_repo)["ok"]

        upstream["write_file"]("data/demo.sqlite", _V2)
        upstream["push"]("refresh the demo database")
        result = _sync(studio_repo)
        assert result["ok"] and result["changed"] == []  # no report changed
        assert (studio_tree.project_root / "data" / "demo.sqlite").read_bytes() == _V2

    def test_removing_the_declaration_stops_shipping_the_file(
        self, studio_repo, studio_tree, upstream
    ):
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")
        assert _sync(studio_repo)["ok"]

        upstream["write_datasources"]("sources: {}\n")
        upstream["push"]("stop declaring the database")
        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data" / "demo.sqlite").exists()
        assert RepoDataSource.objects.get(studio=studio_tree, name="demo_db").shipped_path == ""

    def test_removing_the_file_stops_shipping_it(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")
        assert _sync(studio_repo)["ok"]

        (upstream["work"] / "data" / "demo.sqlite").unlink()
        upstream["push"]("delete the database from the repository")
        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data" / "demo.sqlite").exists()
        row = RepoDataSource.objects.get(studio=studio_tree, name="demo_db")
        assert row.shipped_path == "" and row.present is True
        # A file the repository simply does not have (yet) is not an error.
        assert _fresh_repo(studio_repo).last_error == ""

    def test_a_moved_declaration_leaves_no_orphan(self, studio_repo, studio_tree, upstream):
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")
        assert _sync(studio_repo)["ok"]

        upstream["write_datasources"](_declaring("data/moved.sqlite"))
        upstream["write_file"]("data/moved.sqlite", _V2)
        upstream["push"]("move the database")
        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data" / "demo.sqlite").exists()
        assert (studio_tree.project_root / "data" / "moved.sqlite").read_bytes() == _V2

    @pytest.mark.parametrize(
        "hostile",
        ["../../etc/passwd", "/etc/passwd", "C:/Windows/System32/config/sam", "..\\..\\passwd"],
    )
    def test_a_path_escaping_the_project_is_refused(
        self, studio_repo, studio_tree, upstream, settings, hostile
    ):
        upstream["write_datasources"](_declaring(hostile))
        upstream["push"]("point a source outside the project")

        assert _sync(studio_repo)["ok"]
        repo = _fresh_repo(studio_repo)
        assert "demo_db" in repo.last_error and "inside the project" in repo.last_error
        assert not list(Path(settings.DATA_DIR).rglob("passwd"))
        assert not list(Path(settings.DATA_DIR).rglob("sam"))
        row = RepoDataSource.objects.get(studio=studio_tree, name="demo_db")
        assert row.shipped_path == "" and "inside the project" in row.file_error
        assert _state_of(studio_tree, "demo_db").state == "needs_upload"

    def test_a_symlink_is_refused_not_followed(self, studio_repo, studio_tree, upstream):
        work = upstream["work"]
        upstream["write_datasources"](_DEMO_DB)
        upstream["push"]("declare the database")
        # Staged as an index entry: git carries mode 120000 even from a
        # checkout that cannot create a symlink (Windows).
        oid = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=work, input="/etc/passwd",
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        _git("update-index", "--add", "--cacheinfo", f"120000,{oid},data/demo.sqlite", cwd=work)
        _git("commit", "-m", "symlink the database at /etc/passwd", cwd=work)
        _git("push", "origin", "main", cwd=work)

        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data").exists()
        repo = _fresh_repo(studio_repo)
        assert "demo_db" in repo.last_error and "not a regular file" in repo.last_error

    def test_an_over_cap_file_warns_instead_of_shipping(self, studio_repo, studio_tree, upstream):
        from apps.core.models import InstanceConfig

        row = InstanceConfig.load()
        row.max_upload_mb = 1
        row.save()
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", b"\x00" * (2 * 1024 * 1024))
        upstream["push"]("commit a database too big to ship")

        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data").exists()
        repo = _fresh_repo(studio_repo)
        assert "demo_db" in repo.last_error and "at most 1 MB" in repo.last_error
        assert _state_of(studio_tree, "demo_db").state == "needs_upload"

    def test_a_portal_upload_is_not_clobbered_by_the_repository(
        self, studio_repo, studio_tree, upstream
    ):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="demo_db", type="sqlite",
            config={"path": "data/demo.sqlite", "upload": True},
        )
        uploaded = studio_tree.project_root / "data" / "demo.sqlite"
        uploaded.parent.mkdir(parents=True, exist_ok=True)
        uploaded.write_bytes(b"the portal's upload")
        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit a database of the same name")

        assert _sync(studio_repo)["ok"]
        assert uploaded.read_bytes() == b"the portal's upload"
        assert RepoDataSource.objects.get(studio=studio_tree, name="demo_db").shipped_path == ""

    def test_an_upload_that_takes_over_a_shipped_path_survives(
        self, studio_repo, studio_tree, upstream
    ):
        """The repository shipped the file first and the portal took the name
        over afterwards -- the next sync must not drop the upload as if it
        were its own orphan."""
        from apps.datasources.models import DataSource

        upstream["write_datasources"](_DEMO_DB)
        upstream["write_file"]("data/demo.sqlite", _V1)
        upstream["push"]("commit the demo database")
        assert _sync(studio_repo)["ok"]

        DataSource.objects.create(
            studio=studio_tree, name="demo_db", type="sqlite",
            config={"path": "data/demo.sqlite", "upload": True},
        )
        shipped = studio_tree.project_root / "data" / "demo.sqlite"
        shipped.write_bytes(b"the portal's upload")
        upstream["write_file"]("data/demo.sqlite", _V2)
        upstream["push"]("refresh the database in the repository")

        assert _sync(studio_repo)["ok"]
        assert shipped.read_bytes() == b"the portal's upload"

    def test_an_undeclared_file_is_never_shipped(self, studio_repo, studio_tree, upstream):
        upstream["write_file"]("data/secrets.sqlite", _V1)
        upstream["push"]("commit a file nothing declares")

        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / "data").exists()


class TestDeclaredFileSourcesSafety:
    """The sync writes into a directory the portal, the materializer and the
    upload endpoint also write. Every one of these is a file it must refuse to
    touch."""

    def test_a_declaration_cannot_overwrite_another_sources_upload(
        self, studio_repo, studio_tree, upstream
    ):
        """Uploads are protected by PATH, not by name: a declaration under a
        different name must not land on another source's uploaded file."""
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="salary", type="file",
            config={"path": "data/salary.xlsx", "upload": True},
        )
        target = studio_tree.project_root / "data" / "salary.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"the admin's spreadsheet")
        upstream["write_datasources"](
            "sources:\n  sales_copy:\n    type: file\n    path: data/salary.xlsx\n"
        )
        upstream["write_file"]("data/salary.xlsx", _V1)
        upstream["push"]("declare another name over the same file")

        assert _sync(studio_repo)["ok"]
        assert target.read_bytes() == b"the admin's spreadsheet"
        row = RepoDataSource.objects.get(studio=studio_tree, name="sales_copy")
        assert row.shipped_path == "" and "uploaded data source" in row.file_error
        assert "sales_copy" in _fresh_repo(studio_repo).last_error
        assert _state_of(studio_tree, "sales_copy").state == "needs_upload"

    def test_unshipping_never_deletes_another_sources_upload(
        self, studio_repo, studio_tree, upstream
    ):
        """The repository shipped the path first and a DIFFERENT source's
        upload took it over; dropping the declaration must not take the
        upload with it."""
        from apps.datasources.models import DataSource

        upstream["write_datasources"](_declaring("data/shared.db"))
        upstream["write_file"]("data/shared.db", _V1)
        upstream["push"]("commit a database")
        assert _sync(studio_repo)["ok"]
        shipped = studio_tree.project_root / "data" / "shared.db"
        assert shipped.read_bytes() == _V1

        DataSource.objects.create(
            studio=studio_tree, name="salary", type="file",
            config={"path": "data/shared.db", "upload": True},
        )
        shipped.write_bytes(b"the admin's upload")
        upstream["write_datasources"]("sources: {}\n")
        upstream["push"]("stop declaring the database")

        assert _sync(studio_repo)["ok"]
        assert shipped.read_bytes() == b"the admin's upload"

    @pytest.mark.parametrize(
        "rel",
        [
            "reports/alpha/data.db",        # the report copy owns it
            "output/alpha/index.html",      # the served build output
            "data-sources/config.yaml",     # the materializer's own file
            "config.yaml",                  # a mirrored root build file
            "Reports/alpha/data.db",        # the same directory on Windows/macOS
            "reports./alpha/data.db",       # ...and so is this one
        ],
    )
    def test_a_path_the_portal_writes_itself_is_refused(
        self, studio_repo, studio_tree, upstream, rel
    ):
        upstream["write_datasources"](_declaring(rel))
        upstream["push"]("declare a path the portal owns")

        assert _sync(studio_repo)["ok"]
        assert not (studio_tree.project_root / rel).exists()
        row = RepoDataSource.objects.get(studio=studio_tree, name="demo_db")
        assert row.shipped_path == "" and "the portal writes itself" in row.file_error
        assert "demo_db" in _fresh_repo(studio_repo).last_error

    def test_a_refused_file_holds_the_source_even_with_a_binding(
        self, studio_repo, studio_tree, upstream
    ):
        """A binding that is not itself the file's source does not make the
        missing file any less missing -- the build must still be held."""
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio_tree, name="demo_db", type="sqlite",
            config={"path": "data/demo.sqlite"},
        )
        upstream["write_datasources"](_declaring("../../etc/passwd"))
        upstream["push"]("point the source outside the project")

        assert _sync(studio_repo)["ok"]
        state = _state_of(studio_tree, "demo_db")
        assert state.state == "needs_upload" and "inside the project" in state.detail

    def test_the_publish_stops_when_its_data_file_budget_is_spent(
        self, studio_repo, studio_tree, upstream
    ):
        from apps.core.models import InstanceConfig

        row = InstanceConfig.load()
        row.max_upload_mb = 1
        row.save()
        upstream["write_datasources"](
            "sources:\n"
            "  a_db:\n    type: sqlite\n    path: data/a.sqlite\n"
            "  z_db:\n    type: sqlite\n    path: data/z.sqlite\n"
        )
        upstream["write_file"]("data/a.sqlite", b"\x00" * (700 * 1024))
        upstream["write_file"]("data/z.sqlite", b"\x01" * (700 * 1024))
        upstream["push"]("declare two databases that together exceed the cap")

        assert _sync(studio_repo)["ok"]
        assert (studio_tree.project_root / "data" / "a.sqlite").is_file()
        assert not (studio_tree.project_root / "data" / "z.sqlite").exists()
        repo = _fresh_repo(studio_repo)
        assert "z_db" in repo.last_error and "budget" in repo.last_error
        assert _state_of(studio_tree, "z_db").state == "needs_upload"

    def test_a_path_longer_than_the_column_is_refused(
        self, studio_repo, studio_tree, upstream
    ):
        """It would copy, then raise DataError on save -- after the file was
        already on disk."""
        upstream["write_datasources"](_declaring("data/" + "x" * 300 + ".sqlite"))
        upstream["push"]("declare a path longer than the column")

        assert _sync(studio_repo)["ok"]
        row = RepoDataSource.objects.get(studio=studio_tree, name="demo_db")
        assert row.shipped_path == "" and row.file_error
        assert "demo_db" in _fresh_repo(studio_repo).last_error
