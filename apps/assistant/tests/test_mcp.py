"""MCP over streamable HTTP (internal planning ticket #062): the agent door to the toolbox.

One POST endpoint per studio, JSON-RPC 2.0, API-key only. The toolbox
decides what a key's owner sees and may call; a mutating tool runs at once
under a write key (no proposal), audited with the key id and ``via=mcp``;
and a secret never travels this way.
"""
import json

import pytest
from django.test import Client

from apps.accounts.models import ApiKey
from apps.assistant.models import ProposedAction
from apps.core import roles
from apps.core.models import AuditLog
from apps.datasources.models import DataSource, RepoDataSource
from apps.orgs.models import OrgMembership, PermissionGroupGrant
from apps.reports.models import Report, ReportPermissionGrant
from apps.runner.models import Run

pytestmark = pytest.mark.django_db

READ_TOOLS = ["list_reports", "get_report_details", "query_report_data", "read_doc"]
#: ... plus the one read tool that needs the developer role (internal planning ticket #151).
DEVELOPER_READ_TOOLS = READ_TOOLS + ["check_repo_changes"]


@pytest.fixture
def url(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}/mcp"


@pytest.fixture
def developer(make_user, org, studio_tree, grant_studio):
    user = make_user("dev@demo.example", org=org)
    grant_studio(user, studio_tree, roles.DEVELOPER)
    return user


@pytest.fixture
def studio_admin(make_user, org, studio_tree, grant_studio):
    user = make_user("sadmin@demo.example", org=org)
    grant_studio(user, studio_tree, roles.ADMIN)
    return user


@pytest.fixture
def actions_on(org, make_assistant_config):
    return make_assistant_config(org, actions_enabled=True)


@pytest.fixture
def declared(studio_tree):
    return RepoDataSource.objects.create(
        studio=studio_tree, name="warehouse", type="postgres", config={"host": "db"},
        source_file="data-sources/config.yaml",
    )


def key_for(user, org, write=False):
    """(ApiKey, secret)."""
    return ApiKey.mint(
        user=user, org=org, name="agent", scopes=ApiKey.READ_WRITE if write else ApiKey.READ
    )


def rpc(client, url, secret, method, params=None, id_=1, raw=None):
    body = raw if raw is not None else json.dumps(
        {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    )
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"} if secret else {}
    return client.post(url, data=body, content_type="application/json", **headers)


def tool_names(client, url, secret):
    return [t["name"] for t in rpc(client, url, secret, "tools/list").json()["result"]["tools"]]


def call(client, url, secret, name, arguments=None):
    resp = rpc(client, url, secret, "tools/call", {"name": name, "arguments": arguments or {}})
    assert resp.status_code == 200
    return resp.json()["result"]


# ── The protocol ────────────────────────────────────────────────────────────

class TestProtocol:
    def test_initialize_ping_list_call_round_trip(self, client, viewer, org, url, report_row):
        _, secret = key_for(viewer, org)

        resp = rpc(client, url, secret, "initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0" and body["id"] == 1
        result = body["result"]
        assert result["protocolVersion"] == "2025-03-26"  # the client's, echoed
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"]["name"] == "trellum" and result["serverInfo"]["version"]
        assert "secrets" in result["instructions"]

        resp = rpc(client, url, secret, "notifications/initialized", raw=json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ))
        assert resp.status_code == 202 and resp.content == b""

        assert rpc(client, url, secret, "ping", id_="p").json() == {"jsonrpc": "2.0", "id": "p", "result": {}}

        tools = rpc(client, url, secret, "tools/list").json()["result"]["tools"]
        assert [t["name"] for t in tools] == READ_TOOLS
        for t in tools:
            assert set(t) == {"name", "description", "inputSchema"}
            assert t["inputSchema"]["type"] == "object"

        result = call(client, url, secret, "list_reports")
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert result["content"][0]["type"] == "text"
        assert report_row.slug in text
        assert "<data" not in text  # unframed: readable to a coding agent

    def test_initialize_without_a_client_version_answers_ours(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        assert rpc(client, url, secret, "initialize").json()["result"]["protocolVersion"] == "2025-06-18"

    def test_tool_error_is_is_error_not_a_protocol_error(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        result = call(client, url, secret, "get_report_details", {"slug": "nope"})
        assert result["isError"] is True
        assert result["content"][0]["text"].startswith("Error")

    def test_unknown_method_is_32601(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        body = rpc(client, url, secret, "resources/list").json()
        assert body["error"]["code"] == -32601 and body["id"] == 1

    def test_malformed_json_is_32700(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        body = rpc(client, url, secret, None, raw="{not json").json()
        assert body["error"]["code"] == -32700 and body["id"] is None

    def test_not_a_request_is_32600(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        assert rpc(client, url, secret, None, raw="[]").json()["error"]["code"] == -32600
        assert rpc(client, url, secret, None, raw='{"id": 1, "method": "ping"}').json()["error"]["code"] == -32600

    def test_unknown_tool_and_bad_params_are_32602(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        body = rpc(client, url, secret, "tools/call", {"name": "drop_tables", "arguments": {}}).json()
        assert body["error"]["code"] == -32602
        body = rpc(client, url, secret, "tools/call", {"name": "list_reports", "arguments": "x"}).json()
        assert body["error"]["code"] == -32602

    def test_only_post(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        assert client.get(url, HTTP_AUTHORIZATION=f"Bearer {secret}").status_code == 405


# ── Who gets in ─────────────────────────────────────────────────────────────

class TestAuth:
    def test_no_key_is_401(self, client, url):
        resp = rpc(client, url, None, "ping")
        assert resp.status_code == 401 and resp.json() == {"error": "api_key_required"}

    def test_a_session_is_not_a_credential_here(self, login, viewer, url):
        # Logged in, no bearer: still 401 -- and no CSRF failure in front of it.
        assert rpc(login(viewer), url, None, "ping").status_code == 401
        c = Client(enforce_csrf_checks=True)
        c.force_login(viewer)
        assert rpc(c, url, None, "ping").status_code == 401

    def test_bad_key_is_401(self, client, viewer, org, url):
        _, secret = key_for(viewer, org)
        assert rpc(client, url, secret[:-4] + "XXXX", "ping").status_code == 401

    def test_a_key_never_crosses_orgs(
        self, client, viewer, org, other_org, other_studio, grant_studio, url
    ):
        OrgMembership.objects.create(user=viewer, org=other_org, role=roles.ORG_ADMIN)
        grant_studio(viewer, other_studio, roles.ADMIN)
        _, other_secret = key_for(viewer, other_org)
        assert rpc(client, url, other_secret, "ping").status_code == 404
        assert rpc(client, f"/s/{other_org.slug}/{other_studio.slug}/mcp", other_secret, "ping").status_code == 200

    def test_no_studio_grant_is_404(self, client, member, org, url):
        _, secret = key_for(member, org)
        assert rpc(client, url, secret, "ping").status_code == 404

    def test_write_key_needs_no_csrf(self, viewer, org, url):
        _, secret = key_for(viewer, org, write=True)
        assert rpc(Client(enforce_csrf_checks=True), url, secret, "ping").status_code == 200


# ── What a key may see and call ─────────────────────────────────────────────

class TestVisibility:
    def test_selected_report_key_sees_only_its_reports(
        self, client, member, org, studio_tree, report_row, make_group, attach_group,
        url, settings,
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        hidden = Report.objects.create(studio=studio_tree, slug="hidden", name="Hidden")
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        ReportPermissionGrant.objects.create(grant=grant, report=report_row)
        _, secret = key_for(member, org)

        listed = call(client, url, secret, "list_reports")["content"][0]["text"]
        assert report_row.slug in listed and hidden.slug not in listed
        assert call(
            client, url, secret, "get_report_details", {"slug": hidden.slug}
        )["isError"] is True

    def test_read_key_sees_no_mutating_tools_and_cannot_call_one(
        self, client, developer, org, url, actions_on, report_row
    ):
        _, secret = key_for(developer, org)
        assert tool_names(client, url, secret) == DEVELOPER_READ_TOOLS
        result = call(client, url, secret, "run_report", {"slug": report_row.slug})
        assert result["isError"] is True
        assert "write scope required" in result["content"][0]["text"]
        assert not Run.objects.exists()

    def test_actions_off_hides_every_action_from_a_write_key(
        self, client, developer, org, url, make_assistant_config, report_row
    ):
        make_assistant_config(org, actions_enabled=False)
        _, secret = key_for(developer, org, write=True)
        assert tool_names(client, url, secret) == DEVELOPER_READ_TOOLS
        result = call(client, url, secret, "run_report", {"slug": report_row.slug})
        assert result["isError"] is True and "not available" in result["content"][0]["text"]
        assert not Run.objects.exists()

    def test_reads_work_with_no_assistant_configured_at_all(self, client, viewer, org, url, report_row):
        _, secret = key_for(viewer, org)
        assert tool_names(client, url, secret) == READ_TOOLS
        assert call(client, url, secret, "list_reports")["isError"] is False

    def test_viewer_with_a_write_key_sees_no_action_tools(
        self, client, viewer, org, url, actions_on, declared, report_row
    ):
        _, secret = key_for(viewer, org, write=True)
        assert tool_names(client, url, secret) == READ_TOOLS
        for name, args in (
            ("configure_data_source", {"name": "warehouse"}),
            ("run_report", {"slug": report_row.slug}),
        ):
            assert call(client, url, secret, name, args)["isError"] is True
        assert not Run.objects.exists() and not DataSource.objects.exists()

    def test_developer_sees_developer_actions_not_admin_ones(
        self, client, developer, org, url, actions_on
    ):
        _, secret = key_for(developer, org, write=True)
        names = tool_names(client, url, secret)
        assert names[:4] == READ_TOOLS
        assert {"run_report", "test_data_source", "publish_repo_changes"} <= set(names)
        assert "configure_data_source" not in names


# ── Writes: immediate, audited, no proposal ─────────────────────────────────

class TestWrites:
    def test_run_report_enqueues_at_once_and_audits_the_key(
        self, client, developer, org, url, actions_on, report_row
    ):
        key, secret = key_for(developer, org, write=True)
        result = call(client, url, secret, "run_report", {"slug": report_row.slug})
        assert result["isError"] is False
        assert "queued" in result["content"][0]["text"]

        run = Run.objects.get(report=report_row)
        assert run.status == Run.QUEUED and run.requested_by == developer and run.trigger == "manual"
        assert not ProposedAction.objects.exists()

        row = AuditLog.objects.get(action="run.enqueue")
        assert row.actor == developer and row.org == org
        assert row.metadata == {"slug": report_row.slug, "via": "mcp", "api_key": key.pk}

    def test_a_failed_action_is_is_error(self, client, developer, org, url, actions_on):
        _, secret = key_for(developer, org, write=True)
        result = call(client, url, secret, "test_data_source", {"name": "ghost"})
        assert result["isError"] is True
        assert "no credentials are bound" in result["content"][0]["text"]

    def test_configure_with_a_secret_argument_is_refused_and_nothing_stored(
        self, client, studio_admin, org, url, actions_on, declared
    ):
        _, secret = key_for(studio_admin, org, write=True)
        assert "configure_data_source" in tool_names(client, url, secret)
        result = call(client, url, secret, "configure_data_source", {
            "name": "warehouse", "user": "analyst", "password": "s3cret",
        })
        assert result["isError"] is True
        text = result["content"][0]["text"]
        assert "never accepted" in text
        assert "http://testserver/s/demo/casino/settings/datasources?configure=warehouse#configure" in text
        assert not DataSource.objects.exists()
        assert not AuditLog.objects.filter(action__startswith="datasource").exists()
        assert "s3cret" not in json.dumps(list(AuditLog.objects.values_list("metadata", flat=True)))

    def test_configure_without_secrets_returns_the_link_and_the_fields(
        self, client, studio_admin, org, url, actions_on, declared
    ):
        _, secret = key_for(studio_admin, org, write=True)
        result = call(client, url, secret, "configure_data_source", {"name": "warehouse"})
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "warehouse" in text and "user, password" in text
        assert "http://testserver/s/demo/casino/settings/datasources?configure=warehouse#configure" in text
        assert not DataSource.objects.exists()
        assert not AuditLog.objects.filter(action__startswith="datasource").exists()

    def test_configure_unknown_source_is_an_error(self, client, studio_admin, org, url, actions_on):
        _, secret = key_for(studio_admin, org, write=True)
        result = call(client, url, secret, "configure_data_source", {"name": "nope"})
        assert result["isError"] is True and "no data source named" in result["content"][0]["text"]

    def test_org_level_configure_needs_an_org_admin(
        self, client, studio_admin, org, url, actions_on, declared
    ):
        _, secret = key_for(studio_admin, org, write=True)
        result = call(client, url, secret, "configure_data_source", {"name": "warehouse", "org_level": True})
        assert result["isError"] is True and "org_admin" in result["content"][0]["text"]
