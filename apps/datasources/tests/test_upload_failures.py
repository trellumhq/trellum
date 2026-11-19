"""File and database changes survive failures together without orphaned bytes."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection

from apps.datasources.models import DataSource

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def upload_source(studio_tree):
    source = DataSource.objects.create(
        studio=studio_tree, name="budget", type="file",
        config={"path": "data-sources/files/budget.xlsx", "upload": True},
    )
    target = studio_tree.project_root / source.config["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original synthetic bytes")
    return source


def request_payload(operation):
    if operation == "remove":
        return {"action": "remove"}
    extension = ".xlsx" if operation == "same" else ".csv"
    return {"file": SimpleUploadedFile(f"replacement{extension}", b"new synthetic bytes")}


def install_failure(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise DatabaseError("simulated persistence failure")

    if failure == "save":
        monkeypatch.setattr(DataSource, "save", fail)
    elif failure == "audit":
        monkeypatch.setattr("apps.datasources.views.audit", fail)
    else:
        monkeypatch.setattr(connection, "commit", fail)


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
@pytest.mark.parametrize("failure", ["save", "audit", "commit"])
def test_failed_change_restores_original_file_and_database(
    login, org_admin, org, studio_tree, upload_source, monkeypatch, operation, failure,
):
    source = upload_source
    original_config = dict(source.config)
    directory = studio_tree.project_root / "data-sources/files"
    original = directory / "budget.xlsx"
    client = login(org_admin)
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    with monkeypatch.context() as patch:
        install_failure(patch, failure)
        with pytest.raises(DatabaseError, match="simulated persistence failure"):
            client.post(url, request_payload(operation))
    source.refresh_from_db()
    assert source.config == original_config
    assert original.read_bytes() == b"original synthetic bytes"
    assert list(directory.iterdir()) == [original]


@pytest.mark.parametrize("failure", ["save", "audit", "commit"])
def test_failed_first_upload_leaves_empty_slot_and_no_file(
    login, org_admin, org, studio_tree, monkeypatch, failure,
):
    source = DataSource.objects.create(
        studio=studio_tree, name="fresh", type="file", config={"upload": True},
    )
    client = login(org_admin)
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/fresh/upload"
    with monkeypatch.context() as patch:
        install_failure(patch, failure)
        with pytest.raises(DatabaseError, match="simulated persistence failure"):
            client.post(url, {"file": SimpleUploadedFile("fresh.csv", b"new synthetic bytes")})
    source.refresh_from_db()
    assert source.config == {"upload": True}
    directory = studio_tree.project_root / "data-sources/files"
    assert not directory.exists() or list(directory.iterdir()) == []


@pytest.mark.parametrize("referenced", [False, True])
def test_extension_collision_preserves_current_and_destination_files(
    login, org_admin, org, studio_tree, upload_source, referenced,
):
    source = upload_source
    original_config = dict(source.config)
    directory = studio_tree.project_root / "data-sources/files"
    original = directory / "budget.xlsx"
    destination = directory / "budget.csv"
    destination.write_bytes(b"another synthetic file")
    if referenced:
        other = DataSource.objects.create(
            studio=studio_tree, name="other", type="file",
            config={"path": "data-sources/files/budget.csv", "upload": True},
        )
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    response = login(org_admin).post(url, request_payload("cross"))
    assert response.status_code == 409
    source.refresh_from_db()
    assert source.config == original_config
    assert original.read_bytes() == b"original synthetic bytes"
    assert destination.read_bytes() == b"another synthetic file"
    assert set(directory.iterdir()) == {original, destination}
    if referenced:
        other.refresh_from_db()
        assert other.config["path"] == "data-sources/files/budget.csv"


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
def test_rollback_uses_retained_inode_even_when_disk_fills(tmp_path, monkeypatch, operation):
    import builtins
    import errno
    import os

    from apps.datasources.uploads import UploadChange

    current = tmp_path / "budget.xlsx"
    current.write_bytes(b"old contents")
    inode = current.stat().st_ino
    staged = tmp_path / "new.part"
    staged.write_bytes(b"new contents")
    target = current if operation == "same" else current.with_suffix(".csv")
    restore = os.replace
    restored = []

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "synthetic full disk")

    def restore_inode(source, destination):
        assert source.stat().st_ino == inode
        assert destination == current
        restored.append(source)
        restore(source, destination)

    with monkeypatch.context() as patch:
        with pytest.raises(DatabaseError, match="persistence failed after disk filled"):
            with UploadChange() as change:
                if operation == "remove":
                    change.remove(current)
                else:
                    change.replace(staged, target, current)
                assert change.backup.stat().st_ino == inode
                # No allocating a fresh backup or recopying the old bytes is
                # possible once the write/commit encounters a full disk.
                patch.setattr(builtins, "open", full)
                patch.setattr(os, "open", full)
                patch.setattr(os, "link", full)
                patch.setattr(os, "replace", restore_inode)
                raise DatabaseError("persistence failed after disk filled")
    assert restored
    assert current.stat().st_ino == inode
    assert current.read_bytes() == b"old contents"
    assert set(tmp_path.iterdir()) == ({current} if operation == "same" else {current, staged})


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
@pytest.mark.parametrize("error_number", [28, 1])
def test_backup_hardlink_failure_keeps_current_file(
    login, org_admin, org, studio_tree, upload_source, monkeypatch, operation, error_number,
):
    import os

    from apps.datasources.materialize import stored_file

    original = stored_file(upload_source)
    original_config = dict(upload_source.config)
    link = os.link

    def fail_backup(source, destination, **kwargs):
        if str(destination).endswith(".previous"):
            raise OSError(error_number, "synthetic hard-link failure")
        return link(source, destination, **kwargs)

    monkeypatch.setattr("apps.datasources.uploads.os.link", fail_backup)
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    response = login(org_admin).post(url, request_payload(operation))
    assert response.status_code == 500
    assert "current file was kept" in response.json()["message"]
    upload_source.refresh_from_db()
    assert upload_source.config == original_config
    assert original.read_bytes() == b"original synthetic bytes"
    assert list(original.parent.iterdir()) == [original]


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
def test_restore_failure_retains_recovery_and_never_claims_current_file_kept(
    login, org_admin, org, studio_tree, upload_source, monkeypatch, operation, caplog,
):
    import os

    from apps.datasources.materialize import stored_file

    original = stored_file(upload_source)
    original_config = dict(upload_source.config)
    replace = os.replace

    def fail_restore(source, destination):
        if str(source).endswith(".previous"):
            raise PermissionError("synthetic restore failure")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("apps.datasources.uploads.os.replace", fail_restore)
        install_failure(patch, "save")
        url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
        response = login(org_admin).post(url, request_payload(operation), HTTP_X_TRELLUM_FORM="1")
    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False
    assert "could not be confirmed" in body["message"]
    assert "administrator recovery" in body["errors"]["__all__"][0]
    assert "kept" not in str(body)
    upload_source.refresh_from_db()
    assert upload_source.config == original_config
    recovery, = original.parent.glob("*.previous")
    assert recovery.read_bytes() == b"original synthetic bytes"
    assert "Upload rollback failed" in caplog.text
    if operation == "same":
        assert original.read_bytes() == b"new synthetic bytes"
    else:
        assert not original.exists()


def test_cleanup_failure_after_commit_does_not_report_failed_save(
    login, org_admin, org, studio_tree, upload_source, monkeypatch, caplog,
):
    from pathlib import Path

    from apps.datasources.materialize import stored_file

    unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path.name.endswith(".previous"):
            raise PermissionError("synthetic cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    response = login(org_admin).post(url, request_payload("same"))
    assert response.status_code == 200
    upload_source.refresh_from_db()
    assert stored_file(upload_source).read_bytes() == b"new synthetic bytes"
    assert "Could not clean up upload recovery link" in caplog.text
