"""File mutation refuses aliases without changing either binding's bytes."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections

from apps.datasources.materialize import file_mutation_problem, stored_file
from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.uploads import file_scope_lock

pytestmark = pytest.mark.django_db(transaction=True)


def payload(operation):
    if operation == "remove":
        return {"action": "remove"}
    ext = "csv" if operation == "same" else "xlsx"
    return {"file": SimpleUploadedFile(f"new.{ext}", b"value\nnew\n")}


@pytest.fixture
def source(studio_tree):
    ds = DataSource.objects.create(studio=studio_tree, name="budget", type="file",
                                   config={"upload": True, "path": "data-sources/files/budget.csv"})
    path = stored_file(ds)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"value\noriginal\n")
    return ds


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
@pytest.mark.parametrize("alias_kind", ["binding", "repository", "normalized", "symlink"])
def test_alias_prevents_destructive_upload(login, org_admin, studio_tree, source, operation, alias_kind):
    path = stored_file(source)
    alias = source.config["path"]
    if alias_kind == "normalized":
        alias = "data-sources/files/../files/budget.csv"
    if alias_kind == "symlink":
        link = path.with_name("alias.csv")
        link.symlink_to(path.name)
        alias = "data-sources/files/alias.csv"
    model = RepoDataSource if alias_kind == "repository" else DataSource
    other = model.objects.create(studio=studio_tree, name="other", type="file", config={"path": alias})
    configs = (dict(source.config), dict(other.config))
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    response = login(org_admin).post(url, payload(operation), HTTP_X_TRELLUM_FORM="1")
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert "Another data source uses" in response.json()["errors"]["__all__"][0]
    source.refresh_from_db()
    other.refresh_from_db()
    assert (source.config, other.config) == configs
    assert path.read_bytes() == b"value\noriginal\n"
    if isinstance(other, DataSource):
        assert stored_file(other).read_bytes() == b"value\noriginal\n"
    assert not path.with_suffix(".xlsx").exists()
    assert not list(path.parent.glob(".*"))


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
def test_org_alias_protection_and_studio_scope_independence(
    login, org_admin, org, studio_tree, source, operation,
):
    ds = DataSource.objects.create(org=org, name="shared", type="file",
                                    config={"upload": True, "path": "files/budget.csv"})
    alias = DataSource.objects.create(org=org, name="alias", type="file",
                                      config={"path": "files/./budget.csv"})
    path = stored_file(ds)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"organization original")
    url = f"/orgs/{org.slug}/settings/datasources/shared/upload"
    client = login(org_admin)
    assert client.post(url, payload(operation)).status_code == 409
    assert stored_file(alias).read_bytes() == b"organization original"
    # An equal relative basename in another storage scope must not block it.
    studio_url = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    assert client.post(studio_url, payload(operation)).status_code == 200
    assert stored_file(alias).read_bytes() == b"organization original"


@pytest.mark.parametrize("action", ["delete", "edit"])
@pytest.mark.parametrize("model", [DataSource, RepoDataSource])
def test_source_setting_cleanup_preserves_other_binding(login, org_admin, studio_tree, source, action, model):
    other = model.objects.create(studio=studio_tree, name="other", type="file", config={"path": source.config["path"]})
    data = {"action": "delete", "id": source.pk} if action == "delete" else {
        "id": source.pk, "name": "budget", "type": "file", "path": "data-sources/files/new.csv", "upload": "on",
    }
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources"
    assert login(org_admin).post(url, data).status_code == 302
    assert (studio_tree.project_root / other.config["path"]).read_bytes() == b"value\noriginal\n"


def test_alias_check_fails_closed_at_fixed_bound(studio_tree, source, monkeypatch, django_assert_num_queries):
    monkeypatch.setattr("apps.datasources.materialize.UPLOAD_ALIAS_LIMIT", 2)
    DataSource.objects.bulk_create([
        DataSource(studio=studio_tree, name=f"other{i}", type="file", config={"path": f"other{i}.csv"})
        for i in range(3)
    ])
    with django_assert_num_queries(1):
        assert "too many" in file_mutation_problem(source, stored_file(source))


@pytest.mark.heavy
def test_alias_check_bounds_rows_and_queries(studio_tree, source, django_assert_num_queries):
    for size in (20, 1000):
        DataSource.objects.filter(studio=studio_tree).exclude(pk=source.pk).delete()
        DataSource.objects.bulk_create([
            DataSource(studio=studio_tree, name=f"other{i}", type="file", config={"path": f"other{i}.csv"})
            for i in range(size)
        ])
        with django_assert_num_queries(2 if size == 20 else 1) as captured:
            problem = file_mutation_problem(source, stored_file(source))
        assert bool(problem) == (size == 1000)
        assert "LIMIT 501" in captured.captured_queries[0]["sql"]


@pytest.mark.parametrize("scope", ["studio", "org"])
@pytest.mark.heavy
def test_another_session_cannot_mutate_locked_scope(login, org_admin, org, studio_tree, source, scope):
    client = login(org_admin)
    if scope == "studio":
        lock_args = {"studio": studio_tree}
        base = f"/s/{org.slug}/{studio_tree.slug}"
        url = base + "/api/datasources/budget/upload"
        settings_url = base + "/settings/datasources"
    else:
        DataSource.objects.create(org=org, name="budget", type="file", config={"upload": True})
        lock_args = {"org": org}
        settings_url = f"/orgs/{org.slug}/settings/datasources"
        url = settings_url + "/budget/upload"

    def attempt_changes():
        try:
            upload = client.post(url, payload("same"), HTTP_X_TRELLUM_FORM="1")
            settings = client.post(settings_url, {"name": "new", "type": "file", "upload": "on"}, HTTP_X_TRELLUM_FORM="1")
            return upload.status_code, settings.status_code
        finally:
            connections.close_all()

    with file_scope_lock(**lock_args) as acquired:
        assert acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(attempt_changes).result(timeout=10) == (409, 409)
    assert not DataSource.objects.filter(name="new").exists()
    # Guard was released, and the supported request now succeeds.
    assert client.post(url, payload("same")).status_code == 200


@pytest.mark.heavy
def test_upload_refuses_running_repository_sync(login, org_admin, studio_tree, source):
    from apps.runner.gitsync import studio_sync_lock

    def attempt_upload():
        try:
            client = login(org_admin)
            url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
            return client.post(url, payload("remove")).status_code
        finally:
            connections.close_all()

    with studio_sync_lock(studio_tree.pk) as acquired:
        assert acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(attempt_upload).result(timeout=10) == 409
    assert stored_file(source).read_bytes() == b"value\noriginal\n"


def test_deleting_binding_preserves_its_exposed_repository_file(login, org_admin, studio_tree, source):
    declaration = RepoDataSource.objects.create(studio=studio_tree, name=source.name, type="file",
                                                config={"path": source.config["path"]})
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/settings/datasources"
    assert login(org_admin).post(url, {"action": "delete", "id": source.pk}).status_code == 302
    assert (studio_tree.project_root / declaration.config["path"]).read_bytes() == b"value\noriginal\n"


def test_alias_check_bounds_path_payload(studio_tree, source):
    DataSource.objects.create(studio=studio_tree, name="oversized", type="file", config={"path": "x" * 10000})
    assert "too long" in file_mutation_problem(source, stored_file(source))


@pytest.mark.parametrize("operation", ["remove", "same", "cross"])
def test_hardlink_alias_keeps_its_original_inode(login, org_admin, studio_tree, source, operation):
    # Atomic rename/unlink never writes through another hard link to the inode.
    import os

    path = stored_file(source)
    alias_path = path.with_name("hardlink.csv")
    os.link(path, alias_path)
    other = DataSource.objects.create(studio=studio_tree, name="other", type="file",
                                      config={"path": "data-sources/files/hardlink.csv"})
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    assert login(org_admin).post(url, payload(operation)).status_code == 200
    assert stored_file(other).read_bytes() == b"value\noriginal\n"


@pytest.mark.heavy
def test_scope_lock_is_held_during_commit_and_filesystem_rollback(
    login, org_admin, studio_tree, source, monkeypatch,
):
    import os

    from django.db import DatabaseError, connection
    from django.test import Client

    client = login(org_admin)
    concurrent = Client()
    concurrent.cookies = client.cookies.copy()
    url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources/budget/upload"
    observations = []
    replace = os.replace

    def concurrent_request():
        try:
            return concurrent.post(url, payload("remove")).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        def assert_scope_locked():
            observations.append(pool.submit(concurrent_request).result(timeout=10))
            assert observations[-1] == 409

        def fail_commit():
            assert_scope_locked()
            raise DatabaseError("synthetic commit failure")

        def observe_restore(old, new):
            if str(old).endswith(".previous"):
                assert_scope_locked()
            return replace(old, new)

        with monkeypatch.context() as patch:
            patch.setattr(connection, "commit", fail_commit)
            patch.setattr("apps.datasources.uploads.os.replace", observe_restore)
            with pytest.raises(DatabaseError, match="synthetic commit failure"):
                client.post(url, payload("cross"))
    assert observations == [409, 409]
    assert stored_file(source).read_bytes() == b"value\noriginal\n"
