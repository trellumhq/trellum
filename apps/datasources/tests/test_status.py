"""The state matrix: what a declaration, a binding and the reports that
reference a name add up to (apps.datasources.status)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.datasources.models import DataSource, RepoDataSource
from apps.datasources.status import (
    SourceState,
    blocked_reports,
    report_blockers,
    source_states,
)
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def declare(studio, name="warehouse", type="postgres", **config):
    return RepoDataSource.objects.create(
        studio=studio, name=name, type=type, config=config,
        source_file="data-sources/config.yaml",
    )


def by_name(studio) -> dict[str, SourceState]:
    return {s.name: s for s in source_states(studio)}


def report_using(studio, slug, *names):
    return Report.objects.create(
        studio=studio, slug=slug, config={"slug": slug, "data_sources": list(names)},
    )


class TestMatrix:
    def test_declared_without_binding_needs_credentials(self, studio):
        declare(studio, host="db.internal")
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.NEEDS_CREDENTIALS
        assert st.declared and st.binding is None
        assert st.detail == "missing: user, password"

    def test_declared_with_incomplete_binding_names_the_gap(self, studio):
        declare(studio, host="db.internal")
        DataSource.objects.create(
            studio=studio, name="warehouse", type="postgres", credentials={"user": "u"},
        )
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.NEEDS_CREDENTIALS
        assert st.detail == "missing: password"

    def test_declared_host_counts_toward_a_complete_binding(self, studio):
        """The declaration carries the host; the binding only has to bring
        what the repository cannot."""
        declare(studio, host="db.internal")
        DataSource.objects.create(
            studio=studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.CONNECTED
        assert st.detail == "not tested yet"

    def test_check_outcomes(self, studio):
        declare(studio, host="db.internal")
        ds = DataSource.objects.create(
            studio=studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
            last_check_at=timezone.now() - timedelta(minutes=3), last_check_ok=True,
        )
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.CONNECTED
        assert st.detail.startswith("checked 3") and st.detail.endswith("ago")
        assert st.last_check_ok is True

        ds.last_check_ok = False
        ds.last_check_error = "OperationalError: refused"
        ds.save()
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.FAILING
        assert st.detail == "OperationalError: refused"

    def test_org_binding_satisfies_a_studio_declaration(self, org, studio):
        declare(studio, host="db.internal")
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.CONNECTED
        assert st.binding_scope == "org"

    def test_studio_binding_shadows_the_org_one(self, org, studio):
        declare(studio, host="db.internal")
        DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        mine = DataSource.objects.create(studio=studio, name="warehouse", type="postgres")
        st = by_name(studio)["warehouse"]
        assert st.binding.pk == mine.pk and st.binding_scope == "studio"
        assert st.state == SourceState.NEEDS_CREDENTIALS  # the shadow is what counts

    def test_portal_only_binding_is_not_in_repo_and_not_blocking(self, studio):
        DataSource.objects.create(
            studio=studio, name="legacy", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )
        st = by_name(studio)["legacy"]
        assert st.state == SourceState.NOT_IN_REPO
        assert not st.declared and not st.blocking

    def test_portal_only_incomplete_binding_is_shown_not_enforced(self, studio_tree):
        """Undeclared bindings are the portal's own: a source that built
        yesterday without a password (trust auth) keeps building, and is still
        written to config.yaml -- the gap is only reported."""
        import yaml

        from apps.datasources.materialize import materialize

        ds = DataSource.objects.create(
            studio=studio_tree, name="trusted", type="postgres",
            config={"host": "h"}, credentials={"user": "u"},
        )
        st = by_name(studio_tree)["trusted"]
        assert st.state == SourceState.NOT_IN_REPO and not st.blocking
        assert st.detail == "missing: password" and st.missing == ["password"]
        assert report_blockers(report_using(studio_tree, "sales", "trusted")) == []
        env = materialize(studio_tree)
        doc = yaml.safe_load(
            (studio_tree.datasources_dir / "config.yaml").read_text(encoding="utf-8")
        )
        assert doc["sources"]["trusted"]["credentials"] == {"local": ds.env_prefix}
        assert env[f"{ds.env_prefix}_USER"] == "u" and f"{ds.env_prefix}_PASS" not in env

    def test_referenced_but_neither_declared_nor_bound_is_unknown(self, studio):
        report_using(studio, "sales", "ghost")
        st = by_name(studio)["ghost"]
        assert st.state == SourceState.UNKNOWN
        assert st.used_by == ["sales"]
        assert st.blocking

    def test_repository_file_is_connected_without_a_binding(self, studio):
        declare(studio, "fixtures", type="file", path="reports/sales/data.csv")
        st = by_name(studio)["fixtures"]
        assert st.state == SourceState.CONNECTED
        assert st.detail == "file in repository"

    def test_declared_upload_waits_for_the_file(self, studio):
        declare(studio, "budget", type="file", upload=True)
        assert by_name(studio)["budget"].state == SourceState.NEEDS_UPLOAD
        # A binding that allows uploads but holds no file is the same wait.
        DataSource.objects.create(
            studio=studio, name="budget", type="file", config={"upload": True},
        )
        st = by_name(studio)["budget"]
        assert st.state == SourceState.NEEDS_UPLOAD
        assert st.detail == "no file uploaded yet"

    def test_awaiting_first_sync_is_not_orphaned(self, studio):
        """Upgrade day: rows that predate declaration sync read as connected
        until the first sync has looked, which clears the allowance."""
        from apps.datasources.repo_sync import sync_repo_datasources

        DataSource.objects.create(
            studio=studio, name="legacy", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
            awaiting_first_sync=True,
        )
        st = by_name(studio)["legacy"]
        assert st.state == SourceState.CONNECTED and st.detail == "not tested yet"

        sync_repo_datasources(studio, "")
        assert DataSource.objects.get(name="legacy").awaiting_first_sync is False
        assert by_name(studio)["legacy"].state == SourceState.NOT_IN_REPO

    def test_sync_clears_the_org_rows_too(self, org, studio):
        from apps.datasources.repo_sync import sync_repo_datasources

        DataSource.objects.create(org=org, name="shared", type="postgres", awaiting_first_sync=True)
        sync_repo_datasources(studio, "")
        assert DataSource.objects.get(name="shared").awaiting_first_sync is False

    def test_declared_type_wins_over_the_binding_type(self, studio):
        declare(studio, host="db.internal", type="mysql")
        DataSource.objects.create(
            studio=studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        st = by_name(studio)["warehouse"]
        assert st.type == "mysql"
        assert st.state == SourceState.CONNECTED

    def test_a_declaration_needing_no_secret_connects_on_its_own(self, studio):
        """A Google Sheets declaration has no credential field a binding could
        add, so 'needs credentials' would be a dead end with no Configure."""
        declare(studio, "sheets", type="google_sheets", path="https://docs.google.com/spreadsheets/d/x")
        declare(studio, "nopath", type="google_sheets")
        states = by_name(studio)
        assert states["sheets"].state == SourceState.CONNECTED
        assert states["sheets"].detail == "declared in repository"
        assert states["nopath"].state == SourceState.NEEDS_CREDENTIALS
        assert states["nopath"].detail == "missing: path"

    def test_declared_type_decides_what_is_required(self, studio):
        """A binding typed as a file cannot satisfy a declared database."""
        declare(studio, host="db.internal")
        DataSource.objects.create(
            studio=studio, name="warehouse", type="file", config={"path": "x.csv"},
        )
        st = by_name(studio)["warehouse"]
        assert st.state == SourceState.NEEDS_CREDENTIALS
        assert st.detail == "missing: user, password"


class TestDeclarationKeys:
    def test_tunnel_secrets_never_reach_a_declaration(self):
        """The bastion and its public key are connection details; the private
        key, password and a local key path are not."""
        from apps.datasources.repo_sync import parse_declaration

        (entry,) = parse_declaration(
            "sources:\n  wh:\n    type: postgres\n    host: db\n"
            "    ssh_host: bastion\n    ssh_port: 2222\n    ssh_user: ops\n"
            "    ssh_host_key: ssh-ed25519 AAAA\n    ssh_key_path: ~/.ssh/id\n"
            "    ssh_private_key: PEM\n    ssh_password: pw\n"
        )
        assert entry["config"] == {
            "host": "db", "ssh_host": "bastion", "ssh_port": 2222, "ssh_user": "ops",
            "ssh_host_key": "ssh-ed25519 AAAA",
        }


class TestReportBlockers:
    def test_only_the_referenced_blockers_come_back(self, studio):
        declare(studio, host="db.internal")  # needs credentials
        declare(studio, "fixtures", type="file", path="reports/sales/data.csv")
        DataSource.objects.create(
            studio=studio, name="other", type="postgres",
            config={"host": "h"}, credentials={"user": "u", "password": "p"},
        )
        report = report_using(studio, "sales", "warehouse", {"name": "fixtures"}, "ghost")
        blockers = report_blockers(report)
        assert [b.name for b in blockers] == ["warehouse", "ghost"]
        assert [b.state for b in blockers] == [SourceState.NEEDS_CREDENTIALS, SourceState.UNKNOWN]

    def test_a_report_without_sources_has_none(self, studio):
        assert report_blockers(report_using(studio, "static")) == []

    def test_blocked_reports_maps_slug_to_blockers(self, studio):
        declare(studio, host="db.internal")
        report_using(studio, "sales", "warehouse")
        report_using(studio, "static")
        Report.objects.create(
            studio=studio, slug="gone", present_in_scan=False,
            config={"data_sources": ["warehouse"]},
        )
        blocked = blocked_reports(studio)
        assert set(blocked) == {"sales"}
        assert blocked["sales"][0].name == "warehouse"


class TestBindingState:
    def test_org_row_reads_the_first_declaring_studio(self, org, studio, studio2):
        from apps.datasources.status import binding_state

        ds = DataSource.objects.create(
            org=org, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        # On its own the row lacks a host; the declaration supplies it.
        assert binding_state(ds).detail == "missing: host"
        declare(studio2, host="db.internal", type="mysql")
        st = binding_state(ds)
        assert st.type == "mysql" and st.state == SourceState.CONNECTED
        assert st.declaration.studio_id == studio2.pk


class TestRunReconciliation:
    """What the web tier shows when only the runner's Run row knows."""

    class _Run:
        def __init__(self, status="error", tail=""):
            self.status, self.stderr_tail = status, tail

    def test_source_failure_parses_the_attribution_line(self):
        from apps.datasources.status import source_failure

        assert source_failure("Data source 'wh': OperationalError: refused\nmore") == (
            "wh", "OperationalError: refused"
        )
        assert source_failure("Traceback ...") is None
        assert source_failure("") is None and source_failure(None) is None

    def test_build_error_prefers_an_attributed_run_over_a_raw_meta(self):
        from apps.datasources.status import build_error

        meta = {"last_error": "Traceback ... refused"}
        attributed = self._Run(tail="Data source 'wh': refused\n\nTraceback ... refused")
        assert build_error(meta, attributed) == attributed.stderr_tail
        assert build_error(meta, self._Run(tail="Traceback ... refused")) == meta["last_error"]
        assert build_error(meta, self._Run(status="success", tail="Data source 'wh': x")) == meta["last_error"]
        assert build_error({"last_error": "Data source 'wh': old"}, attributed) == "Data source 'wh': old"
        assert build_error({}, None) == ""

    def test_blocked_names_reads_meta_first_then_the_run(self):
        from apps.datasources.status import blocked_names

        assert blocked_names({"last_status": "error", "blocked_by": ["a", "b"]}, None) == ["a", "b"]
        assert blocked_names({"last_status": "success", "blocked_by": ["a"]}, None) == []
        run = self._Run(tail="Waiting for data source 'a': missing: user\nWaiting for data source 'b': no file")
        assert blocked_names({}, run) == ["a", "b"]
        assert blocked_names({}, self._Run(status="success", tail=run.stderr_tail)) == []
