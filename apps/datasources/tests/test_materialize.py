"""THE contract test: what the materializer writes + injects must satisfy
the bundled framework's LocalEnvResolver field-for-field."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
from threading import Barrier
from types import SimpleNamespace

import yaml as yaml_lib
import pytest

import apps.datasources.materialize as materialize_module
from apps.datasources.materialize import materialize
from apps.datasources.materialize import stored_file
from apps.datasources.models import (
    CONFIG_KEYS,
    CREDENTIAL_KEYS,
    ENV_SUFFIXES,
    INLINE_TYPES,
    ORG_ALLOWED_TYPES,
    REQUIRED_FIELDS,
    TYPE_FIELDS,
    DataSource,
)

pytestmark = pytest.mark.django_db


def test_stored_file_contains_relative_and_in_root_absolute_paths(tmp_path):
    from types import SimpleNamespace

    root = tmp_path / "studio"
    root.mkdir()
    inside = root / "data.csv"
    inside.write_text("synthetic")
    ds = SimpleNamespace(
        config={"path": str(inside)}, org_id=None,
        studio=SimpleNamespace(project_root=root),
    )
    assert stored_file(ds) == inside.resolve()
    ds.config["path"] = "data.csv"
    assert stored_file(ds) == inside.resolve()
    outside = tmp_path / "outside.csv"
    outside.write_text("synthetic sentinel")
    ds.config["path"] = str(outside)
    assert stored_file(ds) is None
    assert outside.read_text() == "synthetic sentinel"


def test_stored_file_rejects_symlink_escape(tmp_path):
    from types import SimpleNamespace

    root = tmp_path / "studio"
    root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("synthetic sentinel")
    ds = SimpleNamespace(
        config={}, org_id=None, studio=SimpleNamespace(project_root=root),
    )
    link = root / "escape.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this platform: {exc}")
    ds.config["path"] = "escape.csv"
    assert stored_file(ds) is None
    assert outside.read_text() == "synthetic sentinel"


def test_org_stored_file_uses_org_root(tmp_path):
    from types import SimpleNamespace

    root = tmp_path / "org"
    root.mkdir()
    inside = root / "shared.csv"
    inside.write_text("shared")
    ds = SimpleNamespace(
        config={"path": str(inside)}, org_id=1,
        org=SimpleNamespace(datasources_dir=root),
    )
    assert stored_file(ds) == inside.resolve()
    ds.config["path"] = str(tmp_path / "external.csv")
    assert stored_file(ds) is None


def test_datasource_form_rejects_external_path_and_accepts_internal_absolute(studio_tree, tmp_path):
    from apps.datasources.forms import DataSourceForm

    inside = studio_tree.project_root / "data-sources" / "inside.csv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("ok")
    payload = {"name": "local_file", "type": "file", "scope": "studio",
               "path": str(inside), "upload": "on"}
    valid = DataSourceForm(payload, studio=studio_tree)
    assert valid.is_valid(), valid.errors
    outside = tmp_path / "outside.csv"
    outside.write_text("synthetic")
    payload["name"] = "external_file"
    payload["path"] = str(outside)
    invalid = DataSourceForm(payload, studio=studio_tree)
    assert not invalid.is_valid()
    assert "path" in invalid.errors


def test_org_datasource_form_uses_organization_storage_root(studio_tree, tmp_path):
    from apps.datasources.forms import DataSourceForm

    org = studio_tree.org
    inside = org.datasources_dir / "files" / "shared.csv"
    inside.parent.mkdir(parents=True, exist_ok=True)
    payload = {"name": "shared_file", "type": "file", "scope": "org",
               "path": str(inside), "upload": "on"}
    valid = DataSourceForm(payload, org=org, allow_org_scope=True)
    assert valid.is_valid(), valid.errors
    outside = tmp_path / "outside.csv"
    payload["name"] = "external_shared"
    payload["path"] = str(outside)
    invalid = DataSourceForm(payload, org=org, allow_org_scope=True)
    assert not invalid.is_valid()
    assert "path" in invalid.errors


def test_uncontained_uploaded_path_is_never_cleanup_owned(studio_tree, tmp_path):
    from types import SimpleNamespace

    from apps.datasources.views import _owned_file

    outside = tmp_path / "outside.csv"
    outside.write_text("preserve")
    ds = SimpleNamespace(
        config={"path": str(outside), "upload": True},
        org_id=None, studio=studio_tree, is_uploaded=True,
    )
    assert _owned_file(ds) is None
    assert outside.read_text() == "preserve"

#: type -> (config, credentials, what the framework must hand its driver).
_SEAM = {
    "postgres": (
        {"host": "10.0.1.5", "port": 5432, "database": "dw", "ssh_host": "bastion",
         "ssh_port": 2222, "ssh_user": "ops", "ssh_host_key": "ssh-ed25519 AAAA"},
        {"user": "svc", "password": "pw", "ssh_private_key": "-----BEGIN KEY-----\nabc\n",
         "ssh_password": "spw"},
        {"host": "10.0.1.5", "port": 5432, "database": "dw", "ssh_host": "bastion",
         "ssh_port": 2222, "ssh_user": "ops", "ssh_host_key": "ssh-ed25519 AAAA",
         "user": "svc", "password": "pw", "ssh_private_key": "-----BEGIN KEY-----\nabc\n",
         "ssh_password": "spw"},
    ),
    "sqlserver": (
        {"host": "mssql.internal", "port": 1433, "database": "sales"},
        {"user": "svc", "password": "pw"},
        {"host": "mssql.internal", "port": 1433, "database": "sales",
         "user": "svc", "password": "pw"},
    ),
    "redshift": (
        {"host": "rs.internal", "database": "dw"},
        {"user": "svc", "password": "pw"},
        {"host": "rs.internal", "database": "dw", "user": "svc", "password": "pw"},
    ),
    "trino": (
        {"host": "trino.internal", "port": 8443, "catalog": "hive",
         "schema": "default", "secure": True},
        {"user": "svc", "password": "pw"},
        {"host": "trino.internal", "port": 8443, "catalog": "hive",
         "schema": "default", "secure": True, "user": "svc", "password": "pw"},
    ),
    "databricks": (
        {"host": "adb.cloud", "http_path": "/sql/1.0/warehouses/abc",
         "catalog": "main", "schema": "default"},
        {"access_token": "dapi-secret"},
        {"host": "adb.cloud", "http_path": "/sql/1.0/warehouses/abc",
         "catalog": "main", "schema": "default", "access_token": "dapi-secret"},
    ),
    "google_sheets": (
        {"path": "https://docs.google.com/spreadsheets/d/abc",
         "credentials_path": "/run/secrets/sa.json"},
        {"credentials_json": '{"type": "service_account"}'},
        {"path": "https://docs.google.com/spreadsheets/d/abc",
         "credentials_path": "/run/secrets/sa.json",
         "credentials_json": '{"type": "service_account"}'},
    ),
}


@pytest.fixture
def pg_source(studio_tree):
    return DataSource.objects.create(
        studio=studio_tree,
        name="warehouse",
        type="postgres",
        description="Main warehouse",
        config={"host": "db.demo.internal", "port": 5439, "database": "analytics"},
        credentials={"user": "svc_reports", "password": "s3cret-pw"},
    )


def _read_yaml(studio):
    path = studio.datasources_dir / "config.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml_lib.safe_load(fh)


def test_config_is_replaced_only_after_complete_serialization(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    previous = "# previous config\nsources:\n  old:\n    type: sqlite\n    path: old.db\n"
    config.write_text(previous, encoding="utf-8")
    replace = materialize_module.os.replace
    observed = []

    def observe_then_replace(source, destination):
        assert yaml_lib.safe_load(Path(destination).read_text(encoding="utf-8")) == {
            "sources": {"old": {"type": "sqlite", "path": "old.db"}}
        }
        observed.append(Path(source).read_text(encoding="utf-8"))
        replace(source, destination)

    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])
    monkeypatch.setattr(materialize_module.os, "replace", observe_then_replace)
    assert materialize(studio) == {}
    assert len(observed) == 1
    assert yaml_lib.safe_load(config.read_text(encoding="utf-8")) == {"sources": {}}
    assert "# Managed by the portal" in observed[0]
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


@pytest.mark.parametrize("failure", ["serialization", "replacement"])
def test_config_failure_preserves_previous_file_and_cleans_temp(
    tmp_path, monkeypatch, failure,
):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    previous = "# previous config\nsources: {old: {type: sqlite, path: old.db}}\n"
    config.write_text(previous, encoding="utf-8")
    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])

    if failure == "serialization":
        def fail_after_partial_write(_data, stream, **_kwargs):
            stream.write("partial")
            raise RuntimeError("serialization failed")

        monkeypatch.setattr(materialize_module.yaml, "dump", fail_after_partial_write)
    else:
        def fail_replace(_source, _destination):
            raise OSError("replacement failed")

        monkeypatch.setattr(materialize_module.os, "replace", fail_replace)

    with pytest.raises((RuntimeError, OSError)):
        materialize(studio)
    assert config.read_text(encoding="utf-8") == previous
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def _windows_error(code):
    error = OSError("simulated Windows replacement error")
    error.winerror = code
    return error


def test_windows_sharing_error_retries_then_replaces(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("sources: {old: {type: sqlite}}\n", encoding="utf-8")
    original_replace = materialize_module.os.replace
    attempts = []
    delays = []

    def replace_with_transient_error(source, destination):
        attempts.append(None)
        if len(attempts) == 1:
            raise _windows_error(32)
        original_replace(source, destination)

    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])
    monkeypatch.setattr(materialize_module.os, "replace", replace_with_transient_error)
    monkeypatch.setattr(materialize_module.time, "sleep", delays.append)

    materialize(studio)

    assert len(attempts) == 2
    assert delays == [materialize_module._REPLACE_RETRY_DELAYS[0]]
    assert yaml_lib.safe_load(config.read_text(encoding="utf-8")) == {"sources": {}}
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_exhausted_windows_retries_preserve_config_and_clean_temp(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    previous = "sources: {old: {type: sqlite}}\n"
    config.write_text(previous, encoding="utf-8")
    attempts = []
    delays = []

    def fail_with_sharing_error(_source, _destination):
        attempts.append(None)
        raise _windows_error(33)

    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])
    monkeypatch.setattr(materialize_module.os, "replace", fail_with_sharing_error)
    monkeypatch.setattr(materialize_module.time, "sleep", delays.append)

    with pytest.raises(OSError):
        materialize(studio)

    assert len(attempts) == len(materialize_module._REPLACE_RETRY_DELAYS) + 1
    assert delays == list(materialize_module._REPLACE_RETRY_DELAYS)
    assert config.read_text(encoding="utf-8") == previous
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_non_sharing_windows_replacement_error_fails_without_retry(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    previous = "sources: {old: {type: sqlite}}\n"
    config.write_text(previous, encoding="utf-8")
    attempts = []
    delays = []

    def fail_with_other_error(_source, _destination):
        attempts.append(None)
        raise _windows_error(13)

    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])
    monkeypatch.setattr(materialize_module.os, "replace", fail_with_other_error)
    monkeypatch.setattr(materialize_module.time, "sleep", delays.append)

    with pytest.raises(OSError):
        materialize(studio)

    assert len(attempts) == 1
    assert delays == []
    assert config.read_text(encoding="utf-8") == previous
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


def test_concurrent_materializations_use_distinct_temp_files(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    barrier = Barrier(2)
    paths = []
    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])

    def hold_both_writes(_data, stream, **_kwargs):
        paths.append(Path(stream.name))
        stream.write("sources: {}\n")
        barrier.wait(timeout=5)

    monkeypatch.setattr(materialize_module.yaml, "dump", hold_both_writes)
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(materialize, [studio, studio]))

    assert len(paths) == 2
    assert len(set(paths)) == 2
    assert all(path.parent == tmp_path for path in paths)
    assert not list(tmp_path.glob(".config.yaml.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_replacement_preserves_existing_config_permissions(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("sources: {}\n", encoding="utf-8")
    config.chmod(0o640)
    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])

    materialize(studio)

    assert stat.S_IMODE(config.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_new_config_uses_normal_file_creation_permissions(tmp_path, monkeypatch):
    studio = SimpleNamespace(datasources_dir=tmp_path)
    expected = tmp_path / "normal-open.txt"
    with expected.open("w", encoding="utf-8"):
        pass
    monkeypatch.setattr(materialize_module, "source_states", lambda _studio: [])

    materialize(studio)

    assert stat.S_IMODE((tmp_path / "config.yaml").stat().st_mode) == (
        stat.S_IMODE(expected.stat().st_mode)
    )


class TestMaterializedYaml:
    def test_credentialed_entry_references_env_prefix(self, studio_tree, pg_source):
        env = materialize(studio_tree)
        doc = _read_yaml(studio_tree)
        entry = doc["sources"]["warehouse"]
        assert entry["type"] == "postgres"
        assert entry["credentials"] == {"local": pg_source.env_prefix}
        # No secrets in the file, ever.
        raw = (studio_tree.datasources_dir / "config.yaml").read_text(encoding="utf-8")
        assert "s3cret-pw" not in raw
        assert "svc_reports" not in raw
        assert env[f"{pg_source.env_prefix}_PASS"] == "s3cret-pw"

    @pytest.mark.parametrize("enabled", [True, False])
    def test_new_connection_setting_is_materialized_as_a_boolean_env_value(
        self, studio_tree, enabled
    ):
        ds = DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "db", "new_connection_per_query": enabled},
            credentials={"user": "svc", "password": "pw"},
        )
        env = materialize(studio_tree)
        assert env[f"{ds.env_prefix}_NEW_CONNECTION_PER_QUERY"] == str(enabled)

    def test_inline_entries_for_sqlite_and_file(self, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="demo_db", type="sqlite",
            config={"path": "data-sources/demo.sqlite"},
        )
        DataSource.objects.create(
            studio=studio_tree, name="ua_budget", type="file",
            config={"path": "data-sources/uploads/ua_budget.csv", "upload": True},
        )
        env = materialize(studio_tree)
        doc = _read_yaml(studio_tree)
        assert doc["sources"]["demo_db"] == {
            "type": "sqlite", "path": "data-sources/demo.sqlite",
        }
        assert doc["sources"]["ua_budget"] == {
            "type": "file", "path": "data-sources/uploads/ua_budget.csv", "upload": True,
        }
        assert env == {}  # inline types need no env vars

    def test_org_file_is_written_absolute(self, studio_tree, org, data_dir):
        """An org file has no studio project to be relative to, so it is
        emitted absolute — which the framework already resolves (os.path.join
        short-circuits, and _resolve_sqlite_path checks isabs)."""
        DataSource.objects.create(
            org=org, name="shared", type="file",
            config={"path": "files/shared.csv", "upload": True},
        )
        materialize(studio_tree)
        path = _read_yaml(studio_tree)["sources"]["shared"]["path"]
        import os

        assert os.path.isabs(path)
        assert path == str(org.datasources_dir.resolve() / "files" / "shared.csv")

    def test_studio_file_stays_relative(self, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="local", type="file",
            config={"path": "data-sources/files/local.csv", "upload": True},
        )
        materialize(studio_tree)
        assert (
            _read_yaml(studio_tree)["sources"]["local"]["path"]
            == "data-sources/files/local.csv"
        )

    def test_shared_read_paths_covers_the_org_share(self, studio_tree, org, data_dir):
        """What the sandbox has to mount on top of the studio project."""
        from apps.datasources.materialize import shared_read_paths

        assert shared_read_paths(studio_tree) == []
        DataSource.objects.create(
            org=org, name="shared", type="file",
            config={"path": "files/shared.csv", "upload": True},
        )
        # Nothing on disk yet: mounting a missing directory fails the container
        # start, and a FileNotFoundError naming the source is the better error.
        assert shared_read_paths(studio_tree) == []
        (org.datasources_dir / "files").mkdir(parents=True)
        assert shared_read_paths(studio_tree) == [org.datasources_dir.resolve()]

    def test_rewrite_replaces_stale_content(self, studio_tree, pg_source):
        materialize(studio_tree)
        pg_source.delete()
        materialize(studio_tree)
        assert _read_yaml(studio_tree)["sources"] == {}


class TestResolverContract:
    def test_env_satisfies_local_env_resolver(self, studio_tree, pg_source, monkeypatch):
        """Simulate the child process: inject the returned env and run the
        framework's actual resolver against the materialized source."""
        from trellum.data.resolvers import LocalEnvResolver

        env = materialize(studio_tree)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        source = {"local_env": pg_source.env_prefix}
        resolver = LocalEnvResolver()
        assert resolver.can_resolve(source) is True
        conn_info = resolver.resolve(source)
        assert conn_info == {
            "host": "db.demo.internal",
            "port": 5439,  # int, per the resolver contract
            "database": "analytics",
            "user": "svc_reports",
            "password": "s3cret-pw",
        }

    def test_onedrive_fields_flow_through(self, studio_tree, monkeypatch):
        from trellum.data.resolvers import LocalEnvResolver

        ds = DataSource.objects.create(
            studio=studio_tree, name="sharepoint", type="onedrive",
            config={"tenant_id": "tid", "client_id": "cid", "site_url": "https://x.sharepoint.com"},
            credentials={"client_secret": "shh"},
        )
        env = materialize(studio_tree)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        conn_info = LocalEnvResolver().resolve({"local_env": ds.env_prefix})
        assert conn_info == {
            "tenant_id": "tid",
            "client_id": "cid",
            "client_secret": "shh",
            "site_url": "https://x.sharepoint.com",
        }

    @pytest.mark.parametrize("type_", sorted(_SEAM))
    def test_new_types_resolve_through_the_framework(self, studio_tree, monkeypatch, type_):
        """The whole seam: the config.yaml the portal writes, read by the
        framework's own loader, resolved by its own resolver from the env
        the portal returns."""
        from trellum.data.datasource_config import (
            invalidate_datasource_cache,
            resolve_to_legacy_format,
        )
        from trellum.data.resolvers import resolve_credentials

        config, credentials, expected = _SEAM[type_]
        ds = DataSource.objects.create(
            studio=studio_tree, name="src", type=type_, config=config, credentials=credentials,
        )
        env = materialize(studio_tree)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        # Secrets never reach the file.
        assert not any(key in _read_yaml(studio_tree)["sources"]["src"] for key in credentials or {})

        monkeypatch.setenv("FW_PROJECT_ROOT", str(studio_tree.project_root))
        invalidate_datasource_cache()
        try:
            source = resolve_to_legacy_format("src")
        finally:
            invalidate_datasource_cache()
        assert source["type"] == type_ and source["local_env"] == ds.env_prefix
        assert resolve_credentials(source) == expected

    def test_a_declared_tunnel_keeps_its_secret_out_of_the_file(self, studio_tree):
        from apps.datasources.models import RepoDataSource

        RepoDataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            config={"host": "db", "ssh_host": "bastion", "ssh_user": "ops",
                    "ssh_host_key": "ssh-ed25519 AAAA"},
            source_file="data-sources/config.yaml",
        )
        ds = DataSource.objects.create(
            studio=studio_tree, name="wh", type="postgres",
            credentials={"user": "u", "password": "p", "ssh_private_key": "PEM"},
        )
        env = materialize(studio_tree)
        entry = _read_yaml(studio_tree)["sources"]["wh"]
        assert entry["ssh_host"] == "bastion" and entry["ssh_host_key"] == "ssh-ed25519 AAAA"
        assert "ssh_private_key" not in entry
        assert env[f"{ds.env_prefix}_SSH_HOST"] == "bastion"
        assert env[f"{ds.env_prefix}_SSH_PRIVATE_KEY"] == "PEM"

    def test_a_declaration_needing_no_binding_is_written_without_a_prefix(self, studio_tree):
        from apps.datasources.models import RepoDataSource

        RepoDataSource.objects.create(
            studio=studio_tree, name="sheets", type="google_sheets",
            config={"path": "https://docs.google.com/spreadsheets/d/abc"},
            source_file="data-sources/config.yaml",
        )
        assert materialize(studio_tree) == {}
        assert _read_yaml(studio_tree)["sources"]["sheets"] == {
            "type": "google_sheets", "path": "https://docs.google.com/spreadsheets/d/abc",
        }

    def test_duckdb_is_an_inline_path(self, studio_tree):
        DataSource.objects.create(
            studio=studio_tree, name="lake", type="duckdb",
            config={"path": "data-sources/files/lake.duckdb"},
        )
        assert materialize(studio_tree) == {}
        assert _read_yaml(studio_tree)["sources"]["lake"] == {
            "type": "duckdb", "path": "data-sources/files/lake.duckdb",
        }


class TestFrameworkLockstep:
    """The portal's hand-kept type tables against the framework in the same
    image: a driver the framework gains must become a portal type, and an
    env suffix the portal writes must be one the resolver reads."""

    def test_every_registered_driver_is_a_portal_type(self):
        from trellum.data.drivers import _registry

        assert set(_registry) <= set(dict(DataSource.TYPES))

    def test_every_live_query_type_is_a_portal_type(self):
        from trellum.data.live_query_guard import SQL_TYPES

        assert SQL_TYPES <= set(dict(DataSource.TYPES))

    def test_env_suffixes_are_what_the_resolver_reads(self):
        from trellum.data.resolvers import _ENV_SUFFIXES

        for key, suffix in ENV_SUFFIXES.items():
            assert _ENV_SUFFIXES.get(suffix) == key, (key, suffix)

    def test_type_tables_agree(self):
        types = set(dict(DataSource.TYPES))
        assert set(TYPE_FIELDS) == types == set(REQUIRED_FIELDS) == set(ORG_ALLOWED_TYPES)
        assert set(INLINE_TYPES) <= types
        for type_, fields in TYPE_FIELDS.items():
            assert set(REQUIRED_FIELDS[type_]) <= set(fields), type_
            assert set(fields) <= CONFIG_KEYS | CREDENTIAL_KEYS, type_
            if type_ not in INLINE_TYPES:
                # Anything else the materializer would silently drop.
                assert set(fields) <= set(ENV_SUFFIXES), type_


class TestExecutorInjection:
    def test_spawn_gets_ds_env_and_fresh_yaml(
        self, studio_tree, pg_source, report_row, monkeypatch
    ):
        import apps.runner.executor as executor_mod
        from apps.runner.executor import Executor
        from apps.runner.models import Run

        captured = {}

        class FakeProc:
            pid = 1

            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured.update(kwargs)

            def poll(self):
                return None

        monkeypatch.setattr(executor_mod.subprocess, "Popen", FakeProc)
        run = Run.objects.create(
            report=report_row, studio=studio_tree, slug=report_row.slug, status=Run.STARTING
        )
        assert Executor("w1").start_run(run) is True
        env = captured["env"]
        assert env[f"{pg_source.env_prefix}_HOST"] == "db.demo.internal"
        assert env[f"{pg_source.env_prefix}_PASS"] == "s3cret-pw"
        assert (studio_tree.datasources_dir / "config.yaml").is_file()
        # And the scrub still holds.
        assert "DATABASE_URL" not in env


class TestImporter:
    def test_demo_style_yaml_imports(self, studio_tree, tmp_path):
        from apps.datasources.importer import import_yaml

        src = tmp_path / "config.yaml"
        src.write_text(
            """
sources:
  demo_db:
    type: sqlite
    description: "Demo warehouse"
    path: data-sources/demo.sqlite
  ua_budget:
    type: file
    path: data-sources/uploads/ua_budget.csv
    upload: true
  warehouse:
    type: vertica
    host: dwh.internal
    credentials:
      local: TRELLUM_PRIMARY
      production: trellum-reports/primary
""",
            encoding="utf-8",
        )
        imported, needs_creds = import_yaml(studio_tree, src)
        assert set(imported) == {"demo_db", "ua_budget", "warehouse"}
        assert needs_creds == ["warehouse"]  # secrets were never in the file
        demo = DataSource.objects.get(studio=studio_tree, name="demo_db")
        assert demo.type == "sqlite" and demo.config["path"] == "data-sources/demo.sqlite"
        wh = DataSource.objects.get(studio=studio_tree, name="warehouse")
        assert wh.config == {"host": "dwh.internal"}
        assert wh.is_configured is False


class TestDeclaredSources:
    """A source the repository declares is written as declared; its binding
    only brings the credentials."""

    @pytest.fixture
    def declared(self, studio_tree):
        from apps.datasources.models import RepoDataSource

        return RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="mysql",
            config={"host": "db.repo.internal", "port": 3306, "description": "From the repo"},
            source_file="data-sources/config.yaml",
        )

    def test_bound_declaration_writes_the_declared_shape(self, studio_tree, declared):
        ds = DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres",
            config={"host": "stale.internal"},
            credentials={"user": "svc", "password": "pw"},
        )
        env = materialize(studio_tree)
        entry = _read_yaml(studio_tree)["sources"]["warehouse"]
        assert entry["type"] == "mysql"
        assert entry["host"] == "db.repo.internal" and entry["port"] == 3306
        assert entry["description"] == "From the repo"
        assert entry["credentials"] == {"local": ds.env_prefix}
        # Env carries the binding's secrets and agrees with the declaration.
        assert env[f"{ds.env_prefix}_USER"] == "svc"
        assert env[f"{ds.env_prefix}_PASS"] == "pw"
        assert env[f"{ds.env_prefix}_HOST"] == "db.repo.internal"

    def test_binding_lifecycle_override_reaches_env_ahead_of_repository_yaml(
        self, studio_tree
    ):
        from apps.datasources.models import RepoDataSource

        RepoDataSource.objects.create(
            studio=studio_tree, name="warehouse", type="mysql",
            config={"host": "db", "new_connection_per_query": True},
            source_file="data-sources/config.yaml",
        )
        ds = DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="mysql",
            config={"new_connection_per_query": False},
            credentials={"user": "svc", "password": "pw"},
        )
        env = materialize(studio_tree)
        assert _read_yaml(studio_tree)["sources"]["warehouse"]["new_connection_per_query"] is True
        assert env[f"{ds.env_prefix}_NEW_CONNECTION_PER_QUERY"] == "False"

    def test_unbound_declaration_is_omitted(self, studio_tree, declared):
        env = materialize(studio_tree)
        assert _read_yaml(studio_tree)["sources"] == {}
        assert env == {}

    def test_incomplete_binding_is_omitted(self, studio_tree, declared):
        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="mysql", credentials={"user": "svc"},
        )
        materialize(studio_tree)
        assert _read_yaml(studio_tree)["sources"] == {}

    def test_portal_only_binding_is_still_written(self, studio_tree, declared, pg_source):
        """``pg_source`` has no declaration and keeps working as before."""
        env = materialize(studio_tree)
        doc = _read_yaml(studio_tree)
        assert set(doc["sources"]) == {"warehouse"}  # the declared one is unbound
        pg_source.name = "legacy"
        pg_source.save()
        env = materialize(studio_tree)
        entry = _read_yaml(studio_tree)["sources"]["legacy"]
        assert entry == {
            "type": "postgres", "description": "Main warehouse",
            "credentials": {"local": pg_source.env_prefix},
        }
        assert env[f"{pg_source.env_prefix}_HOST"] == "db.demo.internal"
        assert env[f"{pg_source.env_prefix}_PASS"] == "s3cret-pw"

    def test_repository_file_is_written_without_a_binding(self, studio_tree):
        from apps.datasources.models import RepoDataSource

        RepoDataSource.objects.create(
            studio=studio_tree, name="fixtures", type="sqlite",
            config={"path": "reports/sales/fixtures.db"}, source_file="reports/sales/report.yaml",
        )
        assert materialize(studio_tree) == {}
        assert _read_yaml(studio_tree)["sources"]["fixtures"] == {
            "type": "sqlite", "path": "reports/sales/fixtures.db",
        }
