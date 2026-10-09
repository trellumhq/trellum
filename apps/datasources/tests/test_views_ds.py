"""Data source API + management UI: status shapes, uploads, permissions."""
import pytest

from apps.core import roles
from apps.core.models import AuditLog
from apps.datasources.models import DataSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("view@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def file_source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree, name="budget", type="file",
        config={"path": "data-sources/files/budget.csv", "upload": True},
    )


@pytest.fixture
def pg_source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree, name="warehouse", type="postgres",
        config={"host": "h"}, credentials={"user": "u", "password": "p"},
    )


class TestListApi:
    def test_status_shapes(self, login, viewer, prefix, file_source, pg_source, studio_tree):
        body = login(viewer).get(f"{prefix}/api/datasources").json()
        by_name = {s["name"]: s for s in body["sources"]}
        assert by_name["warehouse"]["status"] == "configured"
        assert by_name["budget"]["status"] == "missing"  # no file on disk yet
        target = studio_tree.project_root / "data-sources/files/budget.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("a,b\n1,2\n", encoding="utf-8")
        body = login(viewer).get(f"{prefix}/api/datasources").json()
        entry = {s["name"]: s for s in body["sources"]}["budget"]
        assert entry["status"] == "available"
        assert entry["file_size"] > 0

    def test_not_configured_lists_missing(self, login, viewer, prefix, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="half", type="postgres", config={"host": "h"}
        )
        body = login(viewer).get(f"{prefix}/api/datasources").json()
        entry = {s["name"]: s for s in body["sources"]}["half"]
        assert entry["status"] == "not_configured"
        assert "user" in entry["status_detail"]

    def test_no_secrets_in_response(self, login, viewer, prefix, pg_source):
        raw = login(viewer).get(f"{prefix}/api/datasources").content.decode()
        assert "password" not in raw.lower() or '"p"' not in raw


class TestUpload:
    def test_upload_writes_file(self, login, developer, prefix, file_source, studio_tree):
        from django.core.files.uploadedfile import SimpleUploadedFile

        resp = login(developer).post(
            f"{prefix}/api/datasources/budget/upload",
            {"file": SimpleUploadedFile("budget.csv", b"a,b\n3,4\n")},
        )
        body = resp.json()
        assert body["ok"] is True and body["size"] == 8
        target = studio_tree.project_root / "data-sources/files/budget.csv"
        assert target.read_bytes() == b"a,b\n3,4\n"

    def test_upload_refused_without_flag(self, login, developer, prefix, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="fixed", type="file",
            config={"path": "data-sources/files/fixed.csv"},
        )
        resp = login(developer).post(f"{prefix}/api/datasources/fixed/upload", {})
        assert resp.status_code == 403

    def test_traversal_path_rejected(self, login, developer, prefix, studio_tree):
        from django.core.files.uploadedfile import SimpleUploadedFile

        DataSource.objects.create(
            studio=studio_tree, name="evil", type="file",
            config={"path": "../../outside.csv", "upload": True},
        )
        resp = login(developer).post(
            f"{prefix}/api/datasources/evil/upload",
            {"file": SimpleUploadedFile("x.csv", b"x")},
        )
        assert resp.status_code == 400

    def test_viewer_cannot_upload(self, login, viewer, prefix, file_source):
        resp = login(viewer).post(f"{prefix}/api/datasources/budget/upload", {})
        assert resp.status_code == 403

    def test_download(self, login, developer, prefix, file_source, studio_tree):
        target = studio_tree.project_root / "data-sources/files/budget.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"a,b\n")
        resp = login(developer).get(f"{prefix}/api/datasources/budget/download")
        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == b"a,b\n"

        row = AuditLog.objects.get(action="datasource.download")
        assert row.actor_id == developer.pk
        assert row.target_id == str(file_source.pk)
        assert row.metadata["name"] == "budget"
        assert row.category == "access"
        assert row.outcome == "success"

    def test_a_404_download_is_not_audited(self, login, developer, prefix, studio_tree):
        """Only bytes actually served count -- a source with no file on disk
        never touched anything worth recording."""
        DataSource.objects.create(
            studio=studio_tree, name="ghost", type="file",
            config={"path": "data-sources/files/ghost.csv", "upload": True},
        )
        resp = login(developer).get(f"{prefix}/api/datasources/ghost/download")
        assert resp.status_code == 404
        assert not AuditLog.objects.filter(action="datasource.download").exists()

    def test_the_configured_path_is_never_moved_by_an_upload(
        self, login, developer, prefix, file_source, studio_tree
    ):
        """The admin chose this path and reports resolve it; an upload writes
        there or not at all."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        login(developer).post(
            f"{prefix}/api/datasources/budget/upload",
            {"file": SimpleUploadedFile("whatever-the-export-called-it.csv", b"a,b\n")},
        )
        file_source.refresh_from_db()
        assert file_source.config["path"] == "data-sources/files/budget.csv"
        target = studio_tree.project_root / "data-sources/files/budget.csv"
        assert target.read_bytes() == b"a,b\n"

    def test_a_mismatched_extension_is_refused(
        self, login, developer, prefix, file_source, studio_tree
    ):
        """The framework picks its reader from the extension, so an .xlsx over
        budget.csv would parse as garbage at build time. Fail here instead."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        target = studio_tree.project_root / "data-sources/files/budget.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"good data")

        resp = login(developer).post(
            f"{prefix}/api/datasources/budget/upload",
            {"file": SimpleUploadedFile("q3.xlsx", b"xl")},
        )
        assert resp.status_code == 400
        assert ".xlsx" in resp.json()["error"]
        assert target.read_bytes() == b"good data"

    def test_a_first_upload_names_the_file_when_no_path_is_set(
        self, login, developer, prefix, studio_tree
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        ds = DataSource.objects.create(
            studio=studio_tree, name="fresh", type="file", config={"upload": True}
        )
        login(developer).post(
            f"{prefix}/api/datasources/fresh/upload",
            {"file": SimpleUploadedFile("q3-export.xlsx", b"xl")},
        )
        ds.refresh_from_db()
        assert ds.config["path"] == "data-sources/files/fresh.xlsx"
        assert (studio_tree.project_root / "data-sources/files/fresh.xlsx").exists()

    def test_a_repo_path_can_be_uploaded_to(
        self, login, developer, prefix, studio_tree
    ):
        """Regression: uploading and living in the repo are independent. A
        source whose file arrives by git push may still be refreshed here."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        DataSource.objects.create(
            studio=studio_tree, name="committed", type="file",
            config={"path": "reports/sales/data.csv", "upload": True},
        )
        resp = login(developer).post(
            f"{prefix}/api/datasources/committed/upload",
            {"file": SimpleUploadedFile("data.csv", b"fresh\n")},
        )
        assert resp.json()["ok"] is True
        target = studio_tree.project_root / "reports/sales/data.csv"
        assert target.read_bytes() == b"fresh\n"

    def test_oversized_upload_leaves_the_previous_file_intact(
        self, login, developer, prefix, file_source, studio_tree
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.core.models import InstanceConfig

        target = studio_tree.project_root / "data-sources/files/budget.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"good data")

        row = InstanceConfig.load()
        row.max_upload_mb = 1
        row.save()

        resp = login(developer).post(
            f"{prefix}/api/datasources/budget/upload",
            {"file": SimpleUploadedFile("budget.csv", b"x" * (2 * 1024 * 1024))},
        )
        assert resp.status_code == 413
        assert target.read_bytes() == b"good data"
        assert not target.with_name("budget.csv.part").exists()

    def test_studio_route_refuses_an_org_source(
        self, login, developer, prefix, org, studio_tree
    ):
        """Shared bytes are replaced through the organization's own route, by
        an org admin — not by any studio developer who can see the name."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        DataSource.objects.create(
            org=org, name="shared", type="file", config={"upload": True}
        )
        resp = login(developer).post(
            f"{prefix}/api/datasources/shared/upload",
            {"file": SimpleUploadedFile("shared.csv", b"x")},
        )
        assert resp.status_code == 404


class TestOrgUpload:
    @pytest.fixture
    def org_file(self, org, data_dir):
        """data_dir, always: without it these write into the real .data-test
        tree and leak between runs."""
        return DataSource.objects.create(
            org=org, name="shared", type="file", config={"upload": True}
        )

    @pytest.fixture
    def org_prefix(self, org):
        return f"/orgs/{org.slug}/settings/datasources"

    def test_upload_lands_in_the_org_share(
        self, login, org_admin, org, org_prefix, org_file, data_dir
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        resp = login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("extract.csv", b"a,b\n1,2\n")},
        )
        assert resp.json()["ok"] is True
        target = data_dir / "orgs" / org.slug / "data-sources" / "files" / "shared.csv"
        assert target.read_bytes() == b"a,b\n1,2\n"
        org_file.refresh_from_db()
        assert org_file.config["path"] == "files/shared.csv"

    def test_every_studio_sees_it(self, login, org_admin, org_prefix, org_file, studio_tree):
        from django.core.files.uploadedfile import SimpleUploadedFile

        login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("extract.csv", b"a,b\n")},
        )
        url = f"/s/{studio_tree.org.slug}/{studio_tree.slug}/api/datasources"
        body = login(org_admin).get(url).json()
        entry = {s["name"]: s for s in body["sources"]}["shared"]
        assert entry["status"] == "available" and entry["scope"] == "org"

    def test_download(self, login, org_admin, org_prefix, org_file):
        from django.core.files.uploadedfile import SimpleUploadedFile

        login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("extract.csv", b"a,b\n")},
        )
        resp = login(org_admin).get(f"{org_prefix}/shared/download")
        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == b"a,b\n"
        assert AuditLog.objects.filter(action="datasource.download", target_id=str(org_file.pk)).exists()

    def test_org_member_cannot_upload(self, login, member, org_prefix, org_file):
        from django.core.files.uploadedfile import SimpleUploadedFile

        resp = login(member).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("x.csv", b"x")},
        )
        assert resp.status_code in (403, 404)

    def test_studio_page_downloads_a_shared_file_through_its_own_route(
        self, login, org_admin, org_prefix, org_file, studio_tree, org
    ):
        """A studio developer may read every source their reports read, so the
        Download link must not point at the org-admin-only route."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("extract.csv", b"a,b\n")},
        )
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        html = login(org_admin).get(url).content.decode()
        studio_route = f"/s/{org.slug}/{studio_tree.slug}/api/datasources/shared/download"
        assert studio_route in html
        # Shared rows are read-only here: replacing the file happens in
        # Organization settings, reached via the row's manage link.
        assert f"{org_prefix}/shared/upload" not in html
        assert "Manage in organization" in html

    def test_a_developer_can_download_a_shared_file(
        self, login, org_admin, developer, org_prefix, org_file, org, studio_tree
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("extract.csv", b"a,b\n")},
        )
        prefix = f"/s/{org.slug}/{studio_tree.slug}"
        resp = login(developer).get(f"{prefix}/api/datasources/shared/download")
        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == b"a,b\n"

    def test_storage_quota_refuses_the_upload(
        self, login, org_admin, org, org_prefix, org_file, data_dir, settings
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.orgs.models import OrgQuota

        settings.TRELLUM_QUOTAS_ENABLED = True
        OrgQuota.objects.create(org=org, max_storage_mb=1)
        resp = login(org_admin).post(
            f"{org_prefix}/shared/upload",
            {"file": SimpleUploadedFile("big.csv", b"x" * (2 * 1024 * 1024))},
        )
        assert resp.status_code == 413
        assert "limited to 1 MB" in resp.json()["error"]


class TestManagementUI:
    def test_connection_option_is_available_for_remote_query_types_only(
        self, login, org_admin, org, studio_tree
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        html = login(org_admin).get(url).content.decode()
        assert 'name="new_connection_per_query"' in html
        for type_ in (
            "postgres", "mysql", "vertica", "clickhouse", "sqlserver", "redshift",
            "trino", "databricks", "snowflake", "bigquery",
        ):
            assert f'"{type_}"' in html
        # The selector's per-type map drives visibility; local/file-like sources
        # do not receive this option.
        type_map = html.split('<script id="type-fields" type="application/json">', 1)[1].split("</script>", 1)[0]
        import json

        fields = json.loads(type_map)
        assert all("new_connection_per_query" in fields[t] for t in (
            "postgres", "mysql", "vertica", "clickhouse", "sqlserver", "redshift",
            "trino", "databricks", "snowflake", "bigquery",
        ))
        assert all("new_connection_per_query" not in fields[t] for t in (
            "file", "google_sheets", "onedrive", "sqlite", "duckdb",
        ))

    def test_create_source_splits_config_and_credentials(
        self, login, org_admin, org, studio_tree
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        resp = login(org_admin).post(
            url,
            {
                "name": "warehouse", "type": "postgres", "description": "dw",
                "host": "db.internal", "port": 5432, "database": "core",
                "user": "svc", "password": "pw-secret",
            },
        )
        assert resp.status_code == 302
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.config == {"host": "db.internal", "port": 5432, "database": "core"}
        assert ds.credentials == {"user": "svc", "password": "pw-secret"}

    def test_new_connection_setting_saves_edits_and_is_removed_on_type_switch(
        self, login, org_admin, org, studio_tree
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        client = login(org_admin)
        response = client.post(url, {
            "name": "warehouse", "type": "postgres", "scope": "studio",
            "host": "db.internal", "user": "svc", "password": "pw",
            "new_connection_per_query": "on",
        })
        assert response.status_code == 302
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.config["new_connection_per_query"] is True

        response = client.post(url, {
            "id": ds.pk, "name": "warehouse", "type": "postgres", "scope": "studio",
            "host": "db.internal", "user": "svc", "password": "",
        })
        assert response.status_code == 302
        ds.refresh_from_db()
        assert ds.config["new_connection_per_query"] is False

        response = client.post(url, {
            "id": ds.pk, "name": "warehouse", "type": "file", "scope": "studio",
            "path": "data-sources/files/warehouse.csv", "upload": "on",
        })
        assert response.status_code == 302
        ds.refresh_from_db()
        assert ds.config == {"path": "data-sources/files/warehouse.csv", "upload": True}

    def test_new_connection_setting_can_be_saved_at_organization_scope(
        self, login, org_admin, org, studio_tree
    ):
        url = f"/orgs/{org.slug}/settings/datasources"
        response = login(org_admin).post(url, {
            "name": "shared_warehouse", "type": "trino", "scope": "org",
            "host": "db.internal", "user": "svc", "new_connection_per_query": "on",
        })
        assert response.status_code == 302
        ds = DataSource.objects.get(org=org, name="shared_warehouse")
        assert ds.config["new_connection_per_query"] is True

    def test_org_edit_preserves_explicit_false_override(self, login, org_admin, org):
        ds = DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            config={"host": "db", "new_connection_per_query": False},
            credentials={"user": "svc", "password": "pw"},
        )
        url = f"/orgs/{org.slug}/settings/datasources"
        response = login(org_admin).post(url, {
            "id": ds.pk, "name": "warehouse", "type": "postgres", "scope": "org",
            "host": "db2", "user": "svc", "password": "",
        })
        assert response.status_code == 302
        ds.refresh_from_db()
        assert ds.config["new_connection_per_query"] is False

    def test_blank_password_keeps_stored(self, login, org_admin, org, studio_tree, pg_source):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {
                "id": pg_source.pk, "name": "warehouse", "type": "postgres",
                "host": "h2", "user": "u", "password": "",
            },
        )
        pg_source.refresh_from_db()
        assert pg_source.config["host"] == "h2"
        assert pg_source.credentials["password"] == "p"  # kept

    def test_configure_asks_for_tunnel_secrets_only_when_the_declaration_tunnels(
        self, login, org_admin, org, studio_tree, monkeypatch,
    ):
        from apps.datasources import views
        from apps.datasources.models import RepoDataSource

        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        RepoDataSource.objects.create(
            studio=studio_tree, name="plain", type="postgres", config={"host": "db"},
            source_file="data-sources/config.yaml",
        )
        RepoDataSource.objects.create(
            studio=studio_tree, name="tunnelled", type="postgres",
            config={"host": "db", "ssh_host": "bastion", "ssh_user": "ops"},
            source_file="data-sources/config.yaml",
        )
        client = login(org_admin)

        def configure_form(name):
            # Only the Configure form: the portal-only form further down the
            # page carries every field (hidden per type by script).
            html = client.get(f"{url}?configure={name}").content.decode()
            return html.split('id="ds-configure"')[1].split("</form>")[0]

        assert 'name="ssh_private_key"' not in configure_form("plain")
        html = configure_form("tunnelled")
        assert 'name="ssh_private_key"' in html and 'name="ssh_password"' in html
        assert "bastion" in html  # shown with the other declared facts

        # Saving runs a real connection check; not the point here.
        monkeypatch.setattr(views, "run_state_check", lambda state: (True, "ok"))
        client.post(url, {
            "action": "configure", "name": "tunnelled",
            "user": "u", "password": "p", "ssh_private_key": "PEM",
        })
        ds = DataSource.objects.get(studio=studio_tree, name="tunnelled")
        assert ds.credentials == {"user": "u", "password": "p", "ssh_private_key": "PEM"}

    def test_configure_overrides_repository_setting_and_can_turn_it_off(
        self, login, org_admin, org, studio_tree, monkeypatch
    ):
        from apps.datasources import views
        from apps.datasources.models import RepoDataSource

        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db", "new_connection_per_query": True},
            source_file="data-sources/config.yaml",
        )
        client = login(org_admin)
        html = client.get(f"{url}?configure=warehouse").content.decode()
        checkbox = html.split('type="checkbox" name="new_connection_per_query"', 1)[1]
        assert "checked" in checkbox.split(">", 1)[0]
        assert html.count('id="configure_new_connection_per_query"') == 1
        assert html.count('id="id_new_connection_per_query"') == 1
        monkeypatch.setattr(views, "run_state_check", lambda _state: (True, "ok"))

        client.post(url, {
            "action": "configure", "name": "warehouse", "user": "u", "password": "p",
            "new_connection_per_query": "on",
        })
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.config["new_connection_per_query"] is True

        client.post(url, {
            "action": "configure", "name": "warehouse", "user": "u", "password": "",
            "new_connection_per_query": "false",
        })
        ds.refresh_from_db()
        assert ds.config["new_connection_per_query"] is False

        # A client that predates the checkbox retains the explicit override.
        client.post(url, {"action": "configure", "name": "warehouse", "user": "u"})
        ds.refresh_from_db()
        assert ds.config["new_connection_per_query"] is False

    def test_configure_ignores_unsupported_crafted_option(self, login, org_admin, org, studio_tree, monkeypatch):
        from apps.datasources import views
        from apps.datasources.models import RepoDataSource

        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        RepoDataSource.objects.create(
            studio=studio_tree, name="sharepoint", type="onedrive",
            config={"tenant_id": "tid", "client_id": "cid", "site_url": "https://example.com"},
            source_file="data-sources/config.yaml",
        )
        client = login(org_admin)
        html = client.get(f"{url}?configure=sharepoint").content.decode()
        configure_form = html.split('id="ds-configure"', 1)[1].split("</form>", 1)[0]
        assert 'type="checkbox" name="new_connection_per_query"' not in configure_form
        assert 'id="configure_new_connection_per_query"' not in configure_form
        monkeypatch.setattr(views, "run_state_check", lambda _state: (True, "ok"))

        client.post(url, {
            "action": "configure", "name": "sharepoint", "client_secret": "secret",
            "new_connection_per_query": "on",
        })
        ds = DataSource.objects.get(studio=studio_tree, name="sharepoint")
        assert "new_connection_per_query" not in (ds.config or {})

    def test_new_studio_binding_inherits_org_lifecycle_override_on_legacy_configure_post(
        self, login, org_admin, org, studio_tree, monkeypatch
    ):
        from apps.datasources import views
        from apps.datasources.models import RepoDataSource

        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db", "new_connection_per_query": True},
            source_file="data-sources/config.yaml",
        )
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            config={"new_connection_per_query": False},
            credentials={"user": "shared", "password": "pw"},
        )
        monkeypatch.setattr(views, "run_state_check", lambda _state: (True, "ok"))

        login(org_admin).post(url, {
            "action": "configure", "name": "warehouse", "user": "studio-user",
        })
        ds = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert ds.config["new_connection_per_query"] is False
        assert ds.credentials["password"] == "pw"

    def test_delete(self, login, org_admin, org, studio_tree, pg_source):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(url, {"action": "delete", "id": pg_source.pk})
        assert not DataSource.objects.filter(pk=pg_source.pk).exists()

    def test_developer_cannot_manage(self, login, developer, org, studio_tree):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        assert login(developer).get(url).status_code == 403

    def test_duplicate_name_rejected(self, login, org_admin, org, studio_tree, pg_source):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        resp = login(org_admin).post(
            url, {"name": "warehouse", "type": "mysql"},
        )
        assert resp.status_code == 200  # re-rendered with error
        assert DataSource.objects.filter(studio=studio_tree, name="warehouse").count() == 1
