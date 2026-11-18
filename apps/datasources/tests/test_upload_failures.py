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
