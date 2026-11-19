"""Published-file health is local, tenant-scoped, and independent of access."""
import shutil
import subprocess
from unittest.mock import patch

import pytest

from apps.reports.models import Report
from apps.runner.gitsync import StudioGitSync
from apps.runner.studio_files import check_studio_files
from apps.runner.tests.test_gitsync import _fresh_repo, _git, studio_repo, upstream  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def published_repo(studio_repo):
    assert StudioGitSync(_fresh_repo(studio_repo)).sync()["ok"]
    return _fresh_repo(studio_repo)


def test_unconfigured_or_unpublished_is_unchecked(studio_repo):
    assert check_studio_files(None) == {
        "state": "unchecked", "message": "Studio files have not been checked.", "ready": False,
    }
    assert check_studio_files(studio_repo)["state"] == "unchecked"
    studio_repo.last_synced_sha = "a" * 40
    studio_repo.repo_url = ""
    assert check_studio_files(studio_repo)["state"] == "unchecked"


def test_ready_is_read_only_and_does_not_read_credentials(published_repo, django_assert_num_queries):
    from apps.studios.models import StudioRepo

    before = published_repo.last_sync_at
    with patch.object(StudioRepo, "token", new_callable=property, fget=lambda _: pytest.fail("credential read")):
        with django_assert_num_queries(1):
            assert check_studio_files(published_repo) == {
                "state": "ready", "message": "Studio files ready.", "ready": True,
            }
    published_repo.refresh_from_db()
    assert published_repo.last_sync_at == before


@pytest.mark.parametrize("relative", ["project", "repo", "repo/.git", "project/reports/alpha", "project/reports/alpha/report.yaml"])
def test_missing_paths(published_repo, relative):
    path = published_repo.studio.data_root / relative
    shutil.rmtree(path) if path.is_dir() else path.unlink()
    health = check_studio_files(published_repo)
    assert health["state"] == "missing" and health["ready"] is False
    assert health["message"].startswith("Studio files missing.")
    assert "alpha" not in health["message"]


def test_empty_cache_cannot_discover_parent_repository(published_repo):
    cache = published_repo.studio.repo_dir
    shutil.rmtree(cache / ".git")
    _git("init", cwd=cache.parent)
    assert check_studio_files(published_repo)["state"] == "missing"


@pytest.mark.parametrize("damage", ["invalid-head", "git-file", "git-symlink", "manifest-directory"])
def test_invalid_metadata_or_manifest(published_repo, tmp_path, damage):
    cache = published_repo.studio.repo_dir
    manifest = published_repo.studio.reports_dir / "alpha" / "report.yaml"
    if damage == "invalid-head":
        (cache / ".git" / "HEAD").write_text("broken\n")
    elif damage in {"git-file", "git-symlink"}:
        shutil.rmtree(cache / ".git")
        if damage == "git-file":
            (cache / ".git").write_text("gitdir: ../other\n")
        else:
            (cache / ".git").symlink_to(tmp_path, target_is_directory=True)
    else:
        manifest.unlink()
        manifest.mkdir()
    health = check_studio_files(published_repo)
    assert health["state"] == "mismatch"
    assert health["message"].startswith("Studio files need restoring.")


def test_head_mismatch(published_repo, upstream):
    upstream["write_report"]("beta")
    upstream["push"]("newer")
    sync = StudioGitSync(published_repo)
    pending = sync.fetch()
    _git("checkout", "--detach", pending["to"], cwd=published_repo.studio.repo_dir)
    assert check_studio_files(published_repo)["state"] == "mismatch"


@pytest.mark.parametrize("flags", [{"disabled": True}, {"audience": Report.AUDIENCE_PRIVATE}])
def test_all_present_rows_require_files(published_repo, flags):
    Report.objects.create(studio=published_repo.studio, slug="hidden-identity", **flags)
    health = check_studio_files(published_repo)
    assert health["state"] == "missing"
    assert "hidden-identity" not in health["message"]


def test_removed_and_other_studio_rows_are_excluded(published_repo, studio2):
    Report.objects.create(studio=published_repo.studio, slug="removed", present_in_scan=False)
    Report.objects.create(studio=studio2, slug="elsewhere")
    assert check_studio_files(published_repo)["ready"]


@pytest.mark.parametrize("slug", ["../outside", "/absolute", "a/b", "a\\b", ".", "..", "a:stream", "bad\nslug"])
def test_unsafe_registry_paths_are_rejected(published_repo, slug):
    Report.objects.create(studio=published_repo.studio, slug=slug)
    assert check_studio_files(published_repo)["state"] == "mismatch"


@pytest.mark.parametrize("relative", ["project", "project/reports", "project/reports/alpha", "project/reports/alpha/report.yaml", "repo"])
def test_links_are_not_followed_even_within_studio(published_repo, relative):
    path = published_repo.studio.data_root / relative
    real = path.with_name(path.name + "-real")
    path.rename(real)
    path.symlink_to(real, target_is_directory=real.is_dir())
    assert check_studio_files(published_repo)["state"] == "mismatch"


@pytest.mark.parametrize("error", [PermissionError("secret path"), subprocess.TimeoutExpired("git", 3)])
def test_inspection_errors_are_safe(published_repo, error):
    with patch("apps.runner.studio_files.subprocess.run", side_effect=error):
        health = check_studio_files(published_repo)
    assert health["state"] == "mismatch" and not health["ready"]
    assert "secret" not in health["message"]


def test_git_inspection_is_explicit_local_and_timed(published_repo, monkeypatch):
    from apps.runner import studio_files

    real = subprocess.run
    calls = []

    def inspect(argv, **kwargs):
        calls.append((argv, kwargs))
        return real(argv, **kwargs)

    monkeypatch.setenv("GIT_DIR", "/wrong")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("TRELLUM_GIT_ASKPASS_TOKEN", "do-not-read")
    monkeypatch.setattr(studio_files.subprocess, "run", inspect)
    assert check_studio_files(published_repo)["ready"]
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[1:5] == ["--git-dir", str(published_repo.studio.repo_dir / ".git"), "--work-tree", str(published_repo.studio.repo_dir)]
    assert kwargs["timeout"] == 3
    assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert "GIT_CONFIG_COUNT" not in kwargs["env"]
    assert "TRELLUM_GIT_ASKPASS_TOKEN" not in kwargs["env"]


@pytest.mark.heavy
@pytest.mark.parametrize("count", [1, 450])
def test_report_inspection_uses_one_bounded_query(published_repo, count, django_assert_num_queries):
    studio = published_repo.studio
    Report.objects.bulk_create([Report(studio=studio, slug=f"report-{i}") for i in range(count)])
    for i in range(count):
        directory = studio.reports_dir / f"report-{i}"
        directory.mkdir()
        (directory / "report.yaml").write_text("name: Report\n")
    with django_assert_num_queries(1):
        assert check_studio_files(published_repo)["ready"]


@pytest.mark.heavy
@pytest.mark.parametrize("count", [1001, 3000])
def test_large_registry_inspection_bounds_rows_and_filesystem_work(published_repo, count):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.runner import studio_files

    Report.objects.bulk_create([
        Report(studio=published_repo.studio, slug=f"large-report-{i}")
        for i in range(count)
    ])
    with CaptureQueriesContext(connection) as queries, patch.object(
        studio_files, "_checked_path", wraps=studio_files._checked_path,
    ) as paths:
        health = check_studio_files(published_repo)
    assert health == {
        "state": "unchecked", "message": "Studio files have not been checked.", "ready": False,
    }
    assert len(queries) == 1
    assert "LIMIT 1001" in queries[0]["sql"]
    # Project and Git metadata only: no per-report filesystem probes at either size.
    assert paths.call_count == 4
