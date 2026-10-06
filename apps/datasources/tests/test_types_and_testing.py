"""Per-type field filtering, the image type, and Test connection."""
import pytest

from apps.core import roles
from apps.datasources.models import DataSource
from apps.datasources.testing import test_datasource as check_source

pytestmark = pytest.mark.django_db


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


class TestTypeFieldFiltering:
    def test_irrelevant_fields_dropped_on_save(self, login, org_admin, org, studio_tree):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        # A postgres source submitted with a (stale) file path and tenant id:
        # only postgres-relevant fields survive.
        login(org_admin).post(
            url,
            {
                "name": "wh", "type": "postgres", "host": "h", "user": "u",
                "password": "p", "path": "leftover.csv", "tenant_id": "stale",
            },
        )
        ds = DataSource.objects.get(studio=studio_tree, name="wh")
        assert ds.config == {"host": "h"}
        assert ds.credentials == {"user": "u", "password": "p"}

    @pytest.mark.parametrize("type_, posted, config, credentials", [
        ("sqlserver",
         {"host": "h", "port": "1433", "database": "d", "user": "u", "password": "p"},
         {"host": "h", "port": 1433, "database": "d"}, {"user": "u", "password": "p"}),
        ("redshift",
         {"host": "h", "user": "u", "password": "p"},
         {"host": "h"}, {"user": "u", "password": "p"}),
        ("trino",
         {"host": "h", "user": "u", "catalog": "hive", "schema": "s", "secure": "on"},
         {"host": "h", "catalog": "hive", "schema": "s", "secure": True}, {"user": "u"}),
        ("databricks",
         {"host": "h", "http_path": "/sql/1", "access_token": "tok", "catalog": "main"},
         {"host": "h", "http_path": "/sql/1", "catalog": "main"}, {"access_token": "tok"}),
        ("google_sheets",
         {"path": "https://docs.google.com/spreadsheets/d/x", "credentials_path": "/k.json",
          "credentials_json": '{"type": "service_account"}'},
         {"path": "https://docs.google.com/spreadsheets/d/x", "credentials_path": "/k.json"},
         {"credentials_json": '{"type": "service_account"}'}),
        ("duckdb",
         {"path": "data-sources/files/x.duckdb", "upload": "on"},
         {"path": "data-sources/files/x.duckdb", "upload": True}, None),
    ])
    def test_new_types_split_config_and_secrets(
        self, login, org_admin, org, studio_tree, type_, posted, config, credentials,
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(url, {"name": "src", "type": type_, **posted})
        ds = DataSource.objects.get(studio=studio_tree, name="src")
        assert (ds.config, ds.credentials) == (config, credentials)

    def test_editing_a_trino_source_keeps_secure(self, login, org_admin, org, studio_tree):
        """The form stores only a ticked box (False is dropped), so an edit
        must carry True through and an untick must remove it."""
        ds = DataSource.objects.create(
            studio=studio_tree, name="tr", type="trino",
            config={"host": "h", "secure": True}, credentials={"user": "u"},
        )
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        client = login(org_admin)
        client.post(url, {"id": ds.pk, "name": "tr", "type": "trino", "host": "h2", "user": "u", "secure": "on"})
        ds.refresh_from_db()
        assert ds.config == {"host": "h2", "secure": True}
        client.post(url, {"id": ds.pk, "name": "tr", "type": "trino", "host": "h2", "user": "u"})
        ds.refresh_from_db()
        assert ds.config == {"host": "h2"}

    def test_ssh_tunnel_fields_split_and_secrets_survive_a_blank_edit(
        self, login, org_admin, org, studio_tree,
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        client = login(org_admin)
        client.post(url, {
            "name": "wh", "type": "postgres", "host": "h", "user": "u", "password": "p",
            "ssh_host": "bastion", "ssh_port": "2222", "ssh_user": "ops",
            "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh_password": "spw",
            "ssh_host_key": "ssh-ed25519 AAAA",
        })
        ds = DataSource.objects.get(studio=studio_tree, name="wh")
        assert ds.config == {
            "host": "h", "ssh_host": "bastion", "ssh_port": 2222, "ssh_user": "ops",
            "ssh_host_key": "ssh-ed25519 AAAA",
        }
        assert ds.credentials == {
            "user": "u", "password": "p",
            "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh_password": "spw",
        }
        # The edit form never echoes the secrets, and leaving them blank keeps them.
        html = client.get(f"{url}?edit={ds.pk}").content.decode()
        assert "BEGIN OPENSSH" not in html and "spw" not in html
        client.post(url, {
            "id": ds.pk, "name": "wh", "type": "postgres", "host": "h", "user": "u",
            "ssh_host": "bastion", "ssh_user": "ops",
        })
        ds.refresh_from_db()
        assert ds.credentials["ssh_private_key"] == "-----BEGIN OPENSSH PRIVATE KEY-----"
        assert ds.credentials["ssh_password"] == "spw"
        assert "ssh_port" not in ds.config  # blank = the framework's default 22
        masked = DataSource(
            type="postgres",
            credentials={"user": "u", "password": "dbpw", "ssh_private_key": "PEMTEXT", "ssh_password": "spw"},
        ).scrub("key PEMTEXT ssh spw db dbpw user u")
        assert masked == "key *** ssh *** db *** user u"

    def test_editing_a_sheets_source_keeps_credentials_json(self, login, org_admin, org, studio_tree):
        """The key is write-only: never echoed back, and a blank field on
        edit keeps the stored one."""
        key = '{"type": "service_account", "private_key": "k"}'
        ds = DataSource.objects.create(
            studio=studio_tree, name="gs", type="google_sheets",
            config={"path": "https://docs.google.com/spreadsheets/d/x"},
            credentials={"credentials_json": key},
        )
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        client = login(org_admin)
        # A token of the secret itself: the SSH block's field is named ssh_private_key.
        assert "service_account" not in client.get(f"{url}?edit={ds.pk}").content.decode()
        client.post(url, {
            "id": ds.pk, "name": "gs", "type": "google_sheets",
            "path": "https://docs.google.com/spreadsheets/d/y",
        })
        ds.refresh_from_db()
        assert ds.config == {"path": "https://docs.google.com/spreadsheets/d/y"}
        assert ds.credentials == {"credentials_json": key}

    def test_form_offers_every_type_and_its_fields(self, login, org_admin, org, studio_tree):
        from apps.datasources.models import TYPE_FIELDS

        html = login(org_admin).get(
            f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        ).content.decode()
        for type_, fields in TYPE_FIELDS.items():
            assert f'value="{type_}"' in html, type_
            for field in fields:
                assert f'data-f="{field}"' in html, (type_, field)

    def test_switching_type_drops_stale_credentials(self, login, org_admin, org, studio_tree):
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"id": ds.pk, "name": "wh", "type": "file",
             "path": "data-sources/files/x.csv"},
        )
        ds.refresh_from_db()
        assert ds.type == "file"
        assert ds.config == {"path": "data-sources/files/x.csv"}
        assert not ds.credentials  # postgres password did not survive the switch

    def test_image_type_creatable_and_uploadable(self, login, org_admin, org, studio_tree):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"name": "logo", "type": "image", "path": "data-sources/files/logo.png",
             "upload": "on"},
        )
        ds = DataSource.objects.get(studio=studio_tree, name="logo")
        assert ds.config == {"path": "data-sources/files/logo.png", "upload": True}
        assert ds.is_configured

    def test_a_repo_path_can_still_be_uploadable(self, login, org_admin, org, studio_tree):
        """The two settings are independent: a file that arrives by git push can
        also be refreshed from the portal between pushes."""
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url,
            {"name": "budget", "type": "file", "path": "reports/sales/data.csv",
             "upload": "on"},
        )
        ds = DataSource.objects.get(studio=studio_tree, name="budget")
        assert ds.config == {"path": "reports/sales/data.csv", "upload": True}
        assert ds.is_uploaded and ds.path_is_git_managed

    def test_upload_defaults_to_checked_on_the_form(self, login, org_admin, org, studio_tree):
        """The "auto enable uploads" default lives in the rendered form, not in
        a server-side coercion — an explicit uncheck must still mean False."""
        from apps.datasources.forms import DataSourceForm

        assert DataSourceForm(studio=studio_tree).fields["upload"].initial is True
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url, {"name": "fixed", "type": "file", "path": "data-sources/files/f.csv"}
        )
        assert DataSource.objects.get(name="fixed").is_uploaded is False

    def test_a_file_source_needs_a_path_or_an_upload(self, login, org_admin, org, studio_tree):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        resp = login(org_admin).post(url, {"name": "budget", "type": "file"})
        assert resp.status_code == 200  # re-rendered with the field error
        assert not DataSource.objects.filter(name="budget").exists()


class TestImageMaterialization:
    def test_image_materializes_as_file_entry(self, studio_tree):
        import yaml as yaml_lib

        from apps.datasources.materialize import materialize

        DataSource.objects.create(
            studio=studio_tree, name="logo", type="image",
            config={"path": "data-sources/files/logo.png", "upload": True},
        )
        env = materialize(studio_tree)
        doc = yaml_lib.safe_load(
            (studio_tree.datasources_dir / "config.yaml").read_text(encoding="utf-8")
        )
        # The framework only knows file paths; "image" is portal vocabulary.
        assert doc["sources"]["logo"] == {
            "type": "file", "path": "data-sources/files/logo.png", "upload": True,
        }
        assert env == {}


class TestConnectionTest:
    @pytest.mark.parametrize("type_, filename", [("file", "budget.csv"), ("duckdb", "lake.duckdb")])
    def test_file_source_found_and_missing(self, studio_tree, type_, filename):
        ds = DataSource.objects.create(
            studio=studio_tree, name="budget", type=type_,
            config={"path": f"data-sources/files/{filename}"},
        )
        ok, detail = check_source(ds)
        assert ok is False and "not found" in detail
        target = studio_tree.project_root / "data-sources/files" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("a,b\n", encoding="utf-8")
        ok, detail = check_source(ds)
        assert ok is True and "File found" in detail

    def test_google_sheets_opens_the_spreadsheet(self, studio_tree, monkeypatch):
        """No driver for a spreadsheet: the check reads it the way a build
        does, with the stored key inline, and scrubs the key from errors."""
        import pandas as pd

        import trellum.data.query as query_mod

        key = '{"type": "service_account", "private_key": "sekrit-key"}'
        ds = DataSource.objects.create(
            studio=studio_tree, name="gs", type="google_sheets",
            config={"path": "https://docs.google.com/spreadsheets/d/x"},
            credentials={"credentials_json": key},
        )
        captured = {}

        def fake_read_source(source_type, path, **kwargs):
            captured.update(source_type=source_type, path=path, **kwargs)
            return pd.DataFrame({"a": [1, 2, 3]})

        monkeypatch.setattr(query_mod, "read_source", fake_read_source)
        ok, detail = check_source(ds)
        assert ok is True and "3 rows" in detail
        assert captured == {
            "source_type": "google_sheets", "path": "https://docs.google.com/spreadsheets/d/x",
            "credentials_path": None, "credentials_json": key,
        }

        def failing_read_source(*args, **kwargs):
            raise RuntimeError(f"invalid key: {key}")

        monkeypatch.setattr(query_mod, "read_source", failing_read_source)
        ok, detail = check_source(ds)
        assert ok is False and "sekrit-key" not in detail and "***" in detail

    def test_unconfigured_reports_missing_fields(self, studio_tree):
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres", config={"host": "h"}
        )
        ok, detail = check_source(ds)
        assert ok is False and "user" in detail

    def test_onedrive_not_supported(self, studio_tree):
        ds = DataSource.objects.create(
            studio=studio_tree, name="od", type="onedrive",
            config={"tenant_id": "t", "client_id": "c", "site_url": "https://x"},
            credentials={"client_secret": "s"},
        )
        ok, detail = check_source(ds)
        assert ok is False and "not supported" in detail

    def test_db_success_via_driver(self, studio_tree, monkeypatch):
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "h", "port": 5432, "database": "d"},
            credentials={"user": "u", "password": "p"},
        )
        captured = {}

        class FakeConn:
            def cursor(self):
                class C:
                    def execute(self, sql):
                        captured["sql"] = sql

                    def fetchone(self):
                        return (1,)

                    def close(self):
                        pass

                return C()

            def close(self):
                captured["closed"] = True

        class FakeDriver:
            def connect(self, conn_info):
                captured["conn_info"] = conn_info
                return FakeConn()

        import trellum.data.drivers as drivers_mod

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        ok, detail = check_source(ds)
        assert ok is True and "Connected" in detail
        assert captured["conn_info"]["port"] == 5432  # int, per driver contract
        assert captured["closed"] is True

    @pytest.mark.parametrize("type_, config, credentials", [
        ("postgres", {"host": "h"}, {"user": "u", "password": "sekrit-pw"}),
        ("databricks", {"host": "h", "http_path": "/sql/1"}, {"access_token": "sekrit-pw"}),
    ])
    def test_db_failure_scrubs_secrets(self, studio_tree, monkeypatch, type_, config, credentials):
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type=type_, config=config, credentials=credentials,
        )

        class FakeDriver:
            def connect(self, conn_info):
                raise RuntimeError("auth failed for u with secret sekrit-pw")

        import trellum.data.drivers as drivers_mod

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        ok, detail = check_source(ds)
        assert ok is False
        assert "sekrit-pw" not in detail
        assert "***" in detail


class TestApiEndpoint:
    def test_developer_can_test(self, login, developer, prefix, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="budget", type="file",
            config={"path": "data-sources/files/budget.csv"},
        )
        body = login(developer).post(f"{prefix}/api/datasources/budget/test").json()
        assert body["ok"] is False and "not found" in body["detail"]

    def test_viewer_cannot_test(
        self, login, make_user, org, studio_tree, grant_studio, prefix
    ):
        viewer = make_user("v@demo.example", org=org)
        grant_studio(viewer, studio_tree, roles.VIEWER)
        DataSource.objects.create(studio=studio_tree, name="x", type="file", config={})
        assert login(viewer).post(f"{prefix}/api/datasources/x/test").status_code == 403

    def test_unknown_source_404(self, login, developer, prefix):
        assert login(developer).post(f"{prefix}/api/datasources/ghost/test").status_code == 404


class TestStoredChecks:
    """A check's outcome lands on the source, and a passing one queues the
    reports it was holding back."""

    @pytest.fixture
    def declared_and_used(self, studio_tree, write_report):
        from apps.datasources.models import RepoDataSource
        from apps.reports.scan import sync_studio_registry

        RepoDataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "db.repo.internal"}, source_file="data-sources/config.yaml",
        )
        write_report("sales", data_sources=["wh"])
        sync_studio_registry(studio_tree)

    @pytest.fixture
    def connecting_driver(self, monkeypatch):
        import trellum.data.drivers as drivers_mod

        seen = {}

        class FakeDriver:
            def connect(self, conn_info):
                seen.update(conn_info)
                return object()

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: FakeDriver())
        return seen

    def test_test_button_persists_and_unblocks(
        self, login, developer, prefix, studio_tree, declared_and_used, connecting_driver
    ):
        from apps.runner.models import Run

        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "stale.internal"}, credentials={"user": "u", "password": "p"},
        )
        assert not Run.objects.exists()
        body = login(developer).post(f"{prefix}/api/datasources/wh/test").json()
        assert body["ok"] is True
        # Tested as declared: the repository's host, the binding's secrets.
        assert connecting_driver == {"host": "db.repo.internal", "user": "u", "password": "p"}
        ds.refresh_from_db()
        assert ds.last_check_ok is True and ds.last_check_at is not None
        run = Run.objects.get(slug="sales")
        assert run.status == Run.QUEUED and run.trigger == "manual"

    def test_saving_a_source_runs_a_check(
        self, login, org_admin, org, studio_tree, connecting_driver
    ):
        url = f"/s/{org.slug}/{studio_tree.slug}/settings/datasources"
        login(org_admin).post(
            url, {"name": "wh", "type": "postgres", "host": "h", "user": "u", "password": "p"},  # noqa: S105
        )
        ds = DataSource.objects.get(studio=studio_tree, name="wh")
        assert ds.last_check_ok is True
        assert connecting_driver["host"] == "h"

    def test_a_failed_check_is_stored_and_blocks(
        self, login, developer, prefix, studio_tree, declared_and_used, monkeypatch
    ):
        import trellum.data.drivers as drivers_mod
        from apps.datasources.status import SourceState, report_blockers
        from apps.reports.models import Report

        class Refusing:
            def connect(self, conn_info):
                raise RuntimeError("refused")

        monkeypatch.setattr(drivers_mod, "get_driver", lambda t: Refusing())
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        body = login(developer).post(f"{prefix}/api/datasources/wh/test").json()
        assert body["ok"] is False
        ds.refresh_from_db()
        assert ds.last_check_ok is False and "refused" in ds.last_check_error
        report = Report.objects.get(studio=studio_tree, slug="sales")
        assert [b.state for b in report_blockers(report)] == [SourceState.FAILING]


class TestCheckHygiene:
    """Only a connection outcome is remembered, and a passing check queues
    just the reports that were waiting on that source."""

    def test_refusals_are_not_stored_as_failures(self, studio_tree):
        from apps.datasources.models import RepoDataSource
        from apps.datasources.status import SourceState, binding_state
        from apps.datasources.testing import run_check

        RepoDataSource.objects.create(
            studio=studio_tree, name="od", type="onedrive",
            config={"tenant_id": "t", "client_id": "c", "site_url": "https://x"},
            source_file="data-sources/config.yaml",
        )
        od = DataSource.objects.create(
            studio=studio_tree, name="od", type="onedrive", credentials={"client_secret": "s"},
        )
        ok, detail = run_check(od, declared_type="onedrive", declared_config={
            "tenant_id": "t", "client_id": "c", "site_url": "https://x",
        })
        assert ok is False and "not supported" in detail
        od.refresh_from_db()
        assert od.last_check_ok is None
        st = binding_state(od)
        assert st.state == SourceState.CONNECTED and st.detail == "not testable"

        # An incomplete source is refused, not "failing".
        half = DataSource.objects.create(
            studio=studio_tree, name="half", type="postgres", config={"host": "h"},
        )
        ok, detail = run_check(half)
        assert ok is False and "missing" in detail
        half.refresh_from_db()
        assert half.last_check_ok is None

    def test_a_passing_check_queues_only_the_reports_that_use_the_source(
        self, studio_tree, write_meta
    ):
        from django.utils import timezone

        from apps.datasources.testing import rebuild_unblocked
        from apps.reports.models import Report
        from apps.runner.models import Run

        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )
        for i in range(30):  # 3 use the source; 27 never-run reports do not
            Report.objects.create(
                studio=studio_tree, slug=f"r{i}",
                config={"data_sources": ["wh"]} if i < 3 else {},
            )
        # Built already and never held by this source: stays put.
        Report.objects.create(
            studio=studio_tree, slug="built", config={"data_sources": ["wh"]},
            last_built_at=timezone.now(),
        )
        # Built once, then held by this source: queued again.
        Report.objects.create(
            studio=studio_tree, slug="held", config={"data_sources": [{"name": "wh"}]},
            last_built_at=timezone.now(),
        )
        write_meta("held", last_status="error", blocked_by=["wh"])

        assert rebuild_unblocked(ds) == 4
        assert set(Run.objects.values_list("slug", flat=True)) == {"r0", "r1", "r2", "held"}
