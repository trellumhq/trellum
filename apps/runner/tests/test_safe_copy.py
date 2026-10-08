import os
from pathlib import Path

import pytest

from apps.runner.safe_copy import safe_copyfile, safe_copytree


def _directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        if os.name == "nt":
            import subprocess

            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return
    pytest.skip("directory symlink or junction creation unavailable on this platform")


def test_regular_tree_and_root_file_copy(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "report.yaml").write_text("title: safe")
    nested = source / "helpers"
    nested.mkdir()
    (nested / "helper.py").write_text("VALUE = 1")
    target = tmp_path / "staged"
    safe_copytree(source, target)
    assert (target / "helpers" / "helper.py").read_text() == "VALUE = 1"
    copied = tmp_path / "events.yaml"
    safe_copyfile(source / "report.yaml", copied)
    assert copied.read_text() == "title: safe"


def test_nested_symlink_is_rejected_without_reading_target(tmp_path):
    outside = tmp_path / "outside-secret"
    outside.write_text("synthetic sentinel")
    source = tmp_path / "repo"
    source.mkdir()
    (source / "report.py").write_text("pass")
    try:
        (source / "secret.txt").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this platform: {exc}")
    target = tmp_path / "staged"
    with pytest.raises(ValueError, match="symlinks"):
        safe_copytree(source, target)
    assert not target.exists()
    assert outside.read_text() == "synthetic sentinel"


def test_root_file_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("synthetic sentinel")
    link = tmp_path / "events.yaml"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this platform: {exc}")
    with pytest.raises(ValueError, match="symlinks"):
        safe_copyfile(link, tmp_path / "copy.yaml")
    assert outside.read_text() == "synthetic sentinel"


def test_symlinked_repo_ancestor_is_rejected_and_does_not_read_outside(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside-reports"
    report = outside / "alpha"
    report.mkdir(parents=True)
    (report / "generator.py").write_text("synthetic sentinel")
    _directory_link(cache / "reports", outside)
    target = tmp_path / "staged"
    with pytest.raises(ValueError, match="symlinks"):
        safe_copytree(cache / "reports" / "alpha", target, root=cache)
    assert not target.exists()
    assert (report / "generator.py").read_text() == "synthetic sentinel"


def test_gitsync_preserves_last_good_report_when_source_is_rejected(tmp_path):
    from types import SimpleNamespace

    from apps.runner.gitsync import StudioGitSync

    cache = tmp_path / "cache"
    cache.mkdir()
    outside = tmp_path / "outside-reports"
    report = outside / "alpha"
    report.mkdir(parents=True)
    (report / "generator.py").write_text("outside sentinel")
    _directory_link(cache / "reports", outside)
    destination = tmp_path / "project" / "reports" / "alpha"
    destination.mkdir(parents=True)
    (destination / "generator.py").write_text("last good")

    sync = object.__new__(StudioGitSync)
    sync.cache_dir = str(cache)
    sync.repo = SimpleNamespace(path="reports")
    sync.studio = SimpleNamespace(reports_dir=destination.parent)
    with pytest.raises(ValueError, match="symlinks"):
        sync._copy_reports()
    assert (destination / "generator.py").read_text() == "last good"


def test_nonregular_fifo_rejected_before_open(tmp_path):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform")
    fifo = tmp_path / "report.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        safe_copyfile(fifo, tmp_path / "copy")
