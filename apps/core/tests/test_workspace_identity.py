from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from apps.core import workspace


def test_atomic_creation_has_one_winner(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: workspace.workspace_identity(tmp_path), range(8)))
    assert len(set(values)) == 1
    assert (tmp_path / workspace.MARKER).read_bytes() == values[0].encode() + b"\n"
    assert (tmp_path / workspace.MARKER).stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [tmp_path / workspace.MARKER]


@pytest.mark.parametrize("value", [b"corrupt", b"a" * 32, b"a" * 33 + b"\n", b"A" * 32 + b"\n", b"a" * 32 + b"\nextra"])
def test_corrupt_marker_is_not_replaced(tmp_path, value):
    marker = tmp_path / workspace.MARKER
    marker.write_bytes(value)
    with pytest.raises(workspace.WorkspaceUnavailable):
        workspace.workspace_identity(tmp_path)
    assert marker.read_bytes() == value


def test_symlink_and_fifo_are_unavailable(tmp_path):
    target = tmp_path / "other"
    target.write_bytes(b"a" * 32 + b"\n")
    marker = tmp_path / workspace.MARKER
    marker.symlink_to(target)
    with pytest.raises(workspace.WorkspaceUnavailable):
        workspace.workspace_identity(tmp_path)
    marker.unlink()
    os.mkfifo(marker)
    with pytest.raises(workspace.WorkspaceUnavailable):
        workspace.workspace_identity(tmp_path)


def test_cached_identity_never_reads_files_and_invalidates_setting(settings, tmp_path, monkeypatch):
    settings.DATA_DIR = tmp_path
    identity = workspace.initialize_workspace_identity()
    assert identity
    def forbidden(*args, **kwargs):
        raise AssertionError("request read filesystem")
    monkeypatch.setattr(workspace.os, "open", forbidden)
    assert workspace.cached_workspace_identity() == identity
    settings.DATA_DIR = tmp_path / "new"
    assert workspace.cached_workspace_identity() is None


def test_startup_failure_is_cached(settings, tmp_path, monkeypatch):
    settings.DATA_DIR = tmp_path
    monkeypatch.setattr(workspace, "workspace_identity", lambda root: (_ for _ in ()).throw(workspace.WorkspaceUnavailable()))
    assert workspace.initialize_workspace_identity() is None
    assert workspace.cached_workspace_identity() is None
