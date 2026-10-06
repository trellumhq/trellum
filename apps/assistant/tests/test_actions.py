"""Actions with approval: the catalogue gate, the proposal, the decision.

A mutating tool never runs inside a turn. The model's call becomes a
``ProposedAction`` and a ``proposal`` SSE frame; the approve endpoint runs
it under the user's current role through the same code the portal's own
forms use; secrets typed into the card reach the encrypted column and
nothing else.
"""
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.assistant.models import AssistantSession, ProposedAction
from apps.assistant.system_prompt import build_system_prompt
from apps.assistant.tools import TOOL_SCHEMAS, AssistantToolbox
from apps.core import roles
from apps.core.models import AuditLog
from apps.datasources.models import DataSource, RepoDataSource
from apps.studios.models import StudioMembership, StudioRepo

from .test_message_sse import (  # noqa: F401 -- fake_llm is a fixture
    FakeResponse,
    TextBlock,
    ToolUseBlock,
    fake_llm,
    frames,
    post_message,
)

pytestmark = pytest.mark.django_db

#: A tool runs on a worker thread with its own connection, which cannot see
#: rows a test created inside its still-open transaction. The SSE-path
#: classes below commit instead (the read tools never hit this: they read
#: built output from disk).
SEES_WORKER_THREAD = pytest.mark.django_db(transaction=True)

READ_TOOLS = ["list_reports", "get_report_details", "query_report_data", "read_doc"]
#: ... plus the one read tool that needs the developer role (internal planning ticket #151).
DEVELOPER_READ_TOOLS = READ_TOOLS + ["check_repo_changes"]


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


@pytest.fixture
def make_session(org, studio_tree):
    def _make(user):
        return AssistantSession.objects.create(user=user, org=org, studio=studio_tree)

    return _make


@pytest.fixture
def make_proposal(org, studio_tree, make_session):
    """A stored proposal, bypassing the model."""

    def _make(user, tool="run_report", required_role=roles.DEVELOPER, **kw):
        session = kw.pop("session", None) or make_session(user)
        fields = dict(
            session=session, org=org, studio=studio_tree, user=user, tool=tool,
            summary=f"Do {tool}", arguments={}, secret_fields=[], required_role=required_role,
            expires_at=timezone.now() + ProposedAction.EXPIRY,
        )
        fields.update(kw)
        return ProposedAction.objects.create(**fields)

    return _make


def propose_via_model(client, prefix, session, fake_llm, name, args):
    """One scripted turn whose model calls a mutating tool, then finishes."""
    calls = fake_llm(
        FakeResponse([ToolUseBlock("tu_1", name, args)], stop_reason="tool_use"),
        FakeResponse([TextBlock("I have proposed it; approve it in the panel.")]),
    )
    return frames(post_message(client, prefix, session, text="fix it")), calls


def approve(client, prefix, proposal, **body):
    return client.post(
        f"{prefix}/proposals/{proposal.pk}/approve", data=json.dumps(body),
        content_type="application/json",
    )


def reject(client, prefix, proposal, **body):
    return client.post(
        f"{prefix}/proposals/{proposal.pk}/reject", data=json.dumps(body),
        content_type="application/json",
    )


# ── The catalogue ───────────────────────────────────────────────────────────

class TestCatalogue:
    def test_every_entry_says_whether_it_mutates_and_which_role(self):
        for schema in TOOL_SCHEMAS:
            assert isinstance(schema["mutates"], bool)
            assert schema["role"] in (roles.VIEWER, roles.DEVELOPER, roles.ADMIN, "org_admin")
        assert [s["name"] for s in TOOL_SCHEMAS if not s["mutates"]] == DEVELOPER_READ_TOOLS
        assert {s["name"] for s in TOOL_SCHEMAS if s["mutates"]} == {
            "configure_data_source", "test_data_source", "run_report", "publish_repo_changes",
            "create_alert", "update_alert",
        }

    def test_viewer_sees_no_write_tools_even_with_actions_on(self, studio_tree):
        box = AssistantToolbox(studio_tree, role=roles.VIEWER, may_write=True)
        assert [s["name"] for s in box.schemas] == READ_TOOLS

    def test_developer_sees_developer_actions_not_admin_ones(self, studio_tree):
        names = [s["name"] for s in AssistantToolbox(studio_tree, role=roles.DEVELOPER, may_write=True).schemas]
        assert "run_report" in names and "test_data_source" in names and "publish_repo_changes" in names
        assert "configure_data_source" not in names

    def test_report_bound_developer_only_sees_the_bound_report_action(
        self, developer, org, studio_tree, report_row
    ):
        session = AssistantSession.objects.create(
            user=developer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        box = AssistantToolbox(
            studio_tree, role=roles.DEVELOPER, may_write=True, session=session,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        names = [schema["name"] for schema in box.schemas]
        assert [name for name in names if name not in READ_TOOLS] == ["run_report"]
        assert box.execute("check_repo_changes", {}).startswith("Error")

    def test_actions_off_hides_every_write_tool_from_everyone(self, studio_tree):
        for role in (roles.ADMIN, "org_admin"):
            box = AssistantToolbox(studio_tree, role=role, may_write=False)
            assert [s["name"] for s in box.schemas] == DEVELOPER_READ_TOOLS

    def test_the_provider_never_sees_our_catalogue_keys(self, studio_tree):
        for schema in AssistantToolbox(studio_tree, role="org_admin", may_write=True).schemas:
            assert set(schema) == {"name", "description", "input_schema"}

    def test_execute_rechecks_the_role(self, studio_tree, developer, make_session, declared):
        box = AssistantToolbox(
            studio_tree, role=roles.DEVELOPER, may_write=True, session=make_session(developer)
        )
        result = box.execute("configure_data_source", {"name": "warehouse"})
        assert result.startswith("Error")
        assert not ProposedAction.objects.exists()
        assert box.proposal is None

    def test_execute_refuses_with_actions_off(self, studio_tree, studio_admin, make_session, declared):
        box = AssistantToolbox(
            studio_tree, role=roles.ADMIN, may_write=False, session=make_session(studio_admin)
        )
        assert box.execute("configure_data_source", {"name": "warehouse"}).startswith("Error")
        assert not ProposedAction.objects.exists()

    def test_org_level_credentials_need_an_org_admin(self, studio_tree, studio_admin, make_session, declared):
        box = AssistantToolbox(
            studio_tree, role=roles.ADMIN, may_write=True, session=make_session(studio_admin)
        )
        result = box.execute("configure_data_source", {"name": "warehouse", "org_level": True})
        assert result.startswith("Error") and "organization admin" in result
        assert not ProposedAction.objects.exists()


# ── The prompt ──────────────────────────────────────────────────────────────

class TestHeader:
    def test_read_only_without_actions(self, studio_tree):
        text = build_system_prompt(AssistantToolbox(studio_tree))[0]["text"]
        assert "STRICTLY read-only" in text
        assert "PROPOSE changes" not in text

    def test_proposals_and_no_secrets_in_chat_with_actions(self, studio_tree):
        blocks = build_system_prompt(AssistantToolbox(studio_tree, role=roles.ADMIN, may_write=True))
        text = blocks[0]["text"]
        assert "STRICTLY read-only" not in text
        assert "PROPOSE changes" in text and "NOTHING happens until the user approves" in text
        assert "NEVER ask for a password, key or token in chat" in text
        assert "cannot edit the repository" in text
        # The cached-prompt structure is intact: header in the cached block.
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1]

    def test_report_bound_prompt_only_offers_its_report_action(self, studio_tree, report_row):
        text = build_system_prompt(AssistantToolbox(
            studio_tree, role=roles.DEVELOPER, may_write=True,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        ))[0]["text"]
        assert "rebuilding this report" in text
        assert "configure or test a data source" not in text
        assert "publish repository changes" not in text


# ── The gate: a mutating call becomes a proposal ────────────────────────────

@SEES_WORKER_THREAD
class TestProposal:
    def test_configure_call_proposes_instead_of_running(
        self, login, studio_admin, prefix, actions_on, fake_llm, make_session, declared
    ):
        session = make_session(studio_admin)
        events, calls = propose_via_model(
            login(studio_admin), prefix, session, fake_llm,
            "configure_data_source", {"name": "warehouse"},
        )
        names = [n for n, _ in events]
        assert names[:5] == ["turn_start", "usage", "tool_use", "tool_result", "proposal"]

        proposal = ProposedAction.objects.get()
        assert proposal.status == "proposed"
        assert proposal.session == session and proposal.user == studio_admin
        assert proposal.arguments == {"name": "warehouse", "org_level": False}
        assert proposal.secret_fields == ["user", "password"]
        assert proposal.required_role == roles.ADMIN
        assert timedelta(hours=23) < proposal.expires_at - timezone.now() <= timedelta(hours=24)
        # Nothing ran: no credentials were bound.
        assert not DataSource.objects.exists()

        card = dict(events)["proposal"]
        assert card["id"] == proposal.pk and card["tool"] == "configure_data_source"
        assert card["secret_fields"] == ["user", "password"]
        assert "warehouse" in card["summary"] and card["expires_at"]
        assert "password" not in json.dumps(card["arguments"])

        # The model is told to wait, and the transcript keeps the card.
        result = dict(events)["tool_result"]
        assert result["excerpt"].startswith(f"Proposed action #{proposal.pk}:")
        assert "Waiting for the user's approval" in result["excerpt"]
        sent = calls[1]["messages"][-1]["content"][0]["content"]
        assert f"Proposed action #{proposal.pk}" in sent
        session.refresh_from_db()
        tool_entry = [e for e in session.transcript if e["role"] == "tool"][0]
        assert tool_entry["proposal"]["id"] == proposal.pk
        assert tool_entry["proposal"]["status"] == "proposed"

    def test_a_scripted_call_by_a_viewer_is_refused_not_proposed(
        self, login, viewer, prefix, actions_on, fake_llm, make_session, report_row
    ):
        events, _ = propose_via_model(
            login(viewer), prefix, make_session(viewer), fake_llm, "run_report", {"slug": report_row.slug}
        )
        assert "proposal" not in [n for n, _ in events]
        assert dict(events)["tool_result"]["is_error"] is True
        assert not ProposedAction.objects.exists()

    def test_unknown_source_is_an_error_to_the_model(
        self, login, studio_admin, prefix, actions_on, fake_llm, make_session
    ):
        events, _ = propose_via_model(
            login(studio_admin), prefix, make_session(studio_admin), fake_llm,
            "configure_data_source", {"name": "nope"},
        )
        assert dict(events)["tool_result"]["is_error"] is True
        assert not ProposedAction.objects.exists()


# ── Approve ─────────────────────────────────────────────────────────────────

@SEES_WORKER_THREAD
class TestApprove:
    def test_proposal_decisions_are_session_only(
        self, client, developer, org, prefix, make_proposal, report_row
    ):
        from apps.accounts.models import ApiKey

        proposal = make_proposal(developer, arguments={"slug": report_row.slug})
        _, secret = ApiKey.mint(
            user=developer, org=org, name="agent", scopes=ApiKey.READ_WRITE,
        )
        auth = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
        for decision in ("approve", "reject"):
            response = client.post(
                f"{prefix}/proposals/{proposal.pk}/{decision}", **auth
            )
            assert response.status_code == 403
            assert response.json() == {"error": "session_required"}

    def test_approve_with_secrets_saves_tests_audits_and_records(
        self, login, studio_admin, prefix, actions_on, fake_llm, make_session, declared, monkeypatch
    ):
        from apps.datasources import views as ds_views

        checked = []
        monkeypatch.setattr(
            ds_views, "run_state_check", lambda state: checked.append(state.name) or (True, "ok")
        )
        client = login(studio_admin)
        session = make_session(studio_admin)
        propose_via_model(client, prefix, session, fake_llm, "configure_data_source", {"name": "warehouse"})
        proposal = ProposedAction.objects.get()

        resp = approve(client, prefix, proposal, user="analyst", password="s3cret")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "executed"
        assert body["result"] == "“warehouse” connected."
        assert body["message"] == f"Action #{proposal.pk} approved: “warehouse” connected."

        # The same path as the Configure form: saved, encrypted, tested.
        ds = DataSource.objects.get(studio__slug="casino", name="warehouse")
        assert ds.credentials == {"user": "analyst", "password": "s3cret"}
        assert ds.updated_by == studio_admin
        assert checked == ["warehouse"]

        # Audited under the action's own name with the proposal id, plus the decision.
        update = AuditLog.objects.get(action="datasource.update")
        assert update.metadata["proposal_id"] == proposal.pk and update.actor == studio_admin
        decision = AuditLog.objects.get(action="assistant.proposal.approve")
        assert decision.metadata == {"tool": "configure_data_source", "proposal_id": proposal.pk, "status": "executed"}

        proposal.refresh_from_db()
        assert proposal.status == "executed" and proposal.decided_at is not None
        assert proposal.result == "“warehouse” connected."

        # The transcript: the card's copy is decided, a tool entry records it,
        # and the secret is nowhere.
        session.refresh_from_db()
        entries = session.transcript
        card = [e for e in entries if e.get("proposal")][0]["proposal"]
        assert card["status"] == "executed" and card["result"] == "“warehouse” connected."
        last = entries[-1]
        assert last["role"] == "tool" and last["proposal_id"] == proposal.pk
        assert last["result"] == f"Action #{proposal.pk} approved: “warehouse” connected."
        assert "tool_use_id" not in last and last["is_error"] is False
        assert "s3cret" not in json.dumps(session.state)
        assert "s3cret" not in json.dumps(proposal.arguments)
        assert "s3cret" not in json.dumps(list(AuditLog.objects.values_list("metadata", flat=True)))

    def test_the_next_turn_replays_cleanly_after_a_decision(
        self, login, studio_admin, prefix, actions_on, fake_llm, make_session, declared, monkeypatch
    ):
        from apps.datasources import views as ds_views

        monkeypatch.setattr(ds_views, "run_state_check", lambda state: (True, "ok"))
        client = login(studio_admin)
        session = make_session(studio_admin)
        propose_via_model(client, prefix, session, fake_llm, "configure_data_source", {"name": "warehouse"})
        proposal = ProposedAction.objects.get()
        approve(client, prefix, proposal, user="u", password="p")

        calls = fake_llm(FakeResponse([TextBlock("Connected. Anything else?")]))
        events = frames(post_message(client, prefix, session, text=f"Action #{proposal.pk} approved: connected."))
        assert [n for n, _ in events][-2:] == ["done", "turn_end"]
        # Exactly one tool_result reaches the model -- the proposal's own,
        # paired with its tool_use; the decision entry is the panel's record.
        # (fake_llm shares one call log across installs: the last call is this turn's.)
        results = [
            b for m in calls[-1]["messages"] if isinstance(m["content"], list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        assert len(results) == 1 and results[0]["tool_use_id"] == "tu_1"

    def test_failed_connection_test_is_a_failed_action(
        self, login, studio_admin, prefix, actions_on, make_proposal, declared, monkeypatch
    ):
        from apps.datasources import views as ds_views

        monkeypatch.setattr(ds_views, "run_state_check", lambda state: (False, "timeout"))
        proposal = make_proposal(
            studio_admin, tool="configure_data_source", required_role=roles.ADMIN,
            arguments={"name": "warehouse", "org_level": False}, secret_fields=["user", "password"],
        )
        resp = approve(login(studio_admin), prefix, proposal, user="u", password="p")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        assert "connection test failed: timeout" in resp.json()["result"]
        proposal.refresh_from_db()
        assert proposal.status == "failed"
        assert DataSource.objects.get(name="warehouse").credentials == {"user": "u", "password": "p"}

    def test_role_dropped_since_the_proposal_is_403_and_unchanged(
        self, login, studio_admin, studio_tree, prefix, actions_on, make_proposal
    ):
        proposal = make_proposal(studio_admin, tool="configure_data_source", required_role=roles.ADMIN,
                                 arguments={"name": "warehouse", "org_level": False})
        StudioMembership.objects.filter(user=studio_admin, studio=studio_tree).update(role=roles.DEVELOPER)
        resp = approve(login(studio_admin), prefix, proposal, user="u", password="p")
        assert resp.status_code == 403
        proposal.refresh_from_db()
        assert proposal.status == "proposed" and proposal.decided_at is None
        assert not DataSource.objects.exists()
        assert not AuditLog.objects.filter(action__startswith="assistant.proposal").exists()

    def test_actions_turned_off_since_the_proposal_is_403(
        self, login, developer, org, prefix, make_assistant_config, make_proposal, report_row
    ):
        proposal = make_proposal(developer, arguments={"slug": report_row.slug})
        make_assistant_config(org, actions_enabled=False)
        assert approve(login(developer), prefix, proposal).status_code == 403
        proposal.refresh_from_db()
        assert proposal.status == "proposed"

    def test_expired_is_410_and_marked(self, login, developer, prefix, actions_on, make_proposal, report_row):
        proposal = make_proposal(
            developer, arguments={"slug": report_row.slug},
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        resp = approve(login(developer), prefix, proposal)
        assert resp.status_code == 410
        assert resp.json()["status"] == "expired"
        proposal.refresh_from_db()
        assert proposal.status == "expired"

    def test_already_decided_is_409(self, login, developer, prefix, actions_on, make_proposal, report_row):
        proposal = make_proposal(developer, arguments={"slug": report_row.slug}, status="rejected")
        resp = approve(login(developer), prefix, proposal)
        assert resp.status_code == 409 and resp.json()["status"] == "rejected"

    def test_another_users_proposal_is_404(
        self, login, developer, studio_admin, prefix, actions_on, make_proposal, report_row
    ):
        proposal = make_proposal(developer, arguments={"slug": report_row.slug})
        assert approve(login(studio_admin), prefix, proposal).status_code == 404

    def test_hidden_full_session_proposal_is_inaccessible_after_downgrade(
        self, login, selected_viewer, prefix, actions_on, make_proposal, report_row
    ):
        proposal = make_proposal(selected_viewer, arguments={"slug": report_row.slug})
        client = login(selected_viewer)
        assert approve(client, prefix, proposal).status_code == 404
        assert reject(client, prefix, proposal).status_code == 404

    def test_report_bound_session_cannot_approve_a_studio_wide_action(
        self, login, developer, org, studio_tree, prefix, actions_on,
        make_proposal, report_row,
    ):
        session = AssistantSession.objects.create(
            user=developer, org=org, studio=studio_tree,
            scope=AssistantSession.SCOPE_REPORT, report=report_row,
        )
        proposal = make_proposal(
            developer, session=session, tool="publish_repo_changes",
            required_role=roles.DEVELOPER,
        )
        assert approve(login(developer), prefix, proposal).status_code == 403

    def test_run_report_approve_enqueues_a_build(
        self, login, developer, prefix, actions_on, fake_llm, make_session, report_row
    ):
        from apps.runner.models import Run

        client = login(developer)
        session = make_session(developer)
        events, _ = propose_via_model(client, prefix, session, fake_llm, "run_report", {"slug": report_row.slug})
        card = dict(events)["proposal"]
        assert card["tool"] == "run_report" and card["secret_fields"] == []
        assert report_row.name in card["summary"]
        assert not Run.objects.exists()

        proposal = ProposedAction.objects.get()
        resp = approve(client, prefix, proposal)
        assert resp.status_code == 200 and resp.json()["status"] == "executed"
        assert "queued" in resp.json()["result"]
        run = Run.objects.get(report=report_row)
        assert run.status == Run.QUEUED and run.requested_by == developer and run.trigger == "manual"
        assert AuditLog.objects.get(action="run.enqueue").metadata["proposal_id"] == proposal.pk

    def test_publish_repo_changes_approve_publishes(
        self, login, developer, studio_tree, prefix, actions_on, fake_llm, make_session, report_row
    ):
        repo = StudioRepo.objects.create(
            studio=studio_tree, repo_url="https://github.com/demo/reports.git", publish_mode="manual",
        )
        client = login(developer)
        events, _ = propose_via_model(
            client, prefix, make_session(developer), fake_llm,
            "publish_repo_changes", {"to": "b" * 40, "rebuild": True},
        )
        card = dict(events)["proposal"]
        assert card["arguments"] == {"to": "b" * 40, "rebuild": True}
        repo.refresh_from_db()
        assert repo.publish_requested is False

        proposal = ProposedAction.objects.get()
        resp = approve(client, prefix, proposal)
        assert resp.status_code == 200 and resp.json()["status"] == "executed"
        repo.refresh_from_db()
        assert repo.publish_requested is True and repo.publish_requested_by == developer
        assert repo.publish_requested_to == "b" * 40 and repo.publish_rebuild_override is True
        assert repo.sync_requested is True and repo.sync_reason == "manual"
        assert AuditLog.objects.get(action="git.publish").metadata["proposal_id"] == proposal.pk

    def test_test_data_source_approve_runs_the_check(
        self, login, developer, studio_tree, prefix, actions_on, fake_llm, make_session, declared, monkeypatch
    ):
        from apps.datasources import testing

        DataSource.objects.create(
            studio=studio_tree, name="warehouse", type="postgres", credentials={"user": "u", "password": "p"},
        )
        monkeypatch.setattr(testing, "check_binding", lambda ds, studio=None: (True, "Connected to db"))
        client = login(developer)
        propose_via_model(client, prefix, make_session(developer), fake_llm, "test_data_source", {"name": "warehouse"})
        proposal = ProposedAction.objects.get()
        resp = approve(client, prefix, proposal)
        assert resp.json()["status"] == "executed"
        assert resp.json()["result"] == "Connection OK: Connected to db"
        row = AuditLog.objects.get(action="datasource.test")
        assert row.metadata["proposal_id"] == proposal.pk and row.metadata["ok"] is True

    def test_a_streaming_turn_blocks_the_decision(
        self, login, developer, prefix, actions_on, make_proposal, make_session, report_row
    ):
        session = make_session(developer)
        AssistantSession.objects.filter(pk=session.pk).update(
            in_flight_until=timezone.now() + timedelta(seconds=30)
        )
        proposal = make_proposal(developer, session=session, arguments={"slug": report_row.slug})
        resp = approve(login(developer), prefix, proposal)
        assert resp.status_code == 409 and resp.json()["code"] == "busy"


# ── Reject ──────────────────────────────────────────────────────────────────

@SEES_WORKER_THREAD
class TestReject:
    def test_reject_records_the_reason_and_runs_nothing(
        self, login, developer, prefix, actions_on, fake_llm, make_session, report_row
    ):
        from apps.runner.models import Run

        client = login(developer)
        session = make_session(developer)
        propose_via_model(client, prefix, session, fake_llm, "run_report", {"slug": report_row.slug})
        proposal = ProposedAction.objects.get()

        resp = reject(client, prefix, proposal, reason="not now")
        assert resp.status_code == 200
        assert resp.json() == {
            "id": proposal.pk, "status": "rejected", "result": "not now",
            "message": f"Action #{proposal.pk} rejected: not now",
        }
        assert not Run.objects.exists()
        proposal.refresh_from_db()
        assert proposal.status == "rejected" and proposal.result == "not now"
        decision = AuditLog.objects.get(action="assistant.proposal.reject")
        assert decision.metadata["reason"] == "not now" and decision.metadata["proposal_id"] == proposal.pk
        session.refresh_from_db()
        assert session.transcript[-1]["result"] == f"Action #{proposal.pk} rejected: not now"
        assert [e for e in session.transcript if e.get("proposal")][0]["proposal"]["status"] == "rejected"

    def test_reject_needs_no_reason(self, login, developer, prefix, actions_on, make_proposal, report_row):
        proposal = make_proposal(developer, arguments={"slug": report_row.slug})
        resp = reject(login(developer), prefix, proposal)
        assert resp.status_code == 200
        assert resp.json()["message"] == f"Action #{proposal.pk} rejected."


# ── The settings page ───────────────────────────────────────────────────────

class TestSettings:
    def test_org_toggle_is_off_by_default_and_saves(self, login, org_admin, org):
        from apps.orgs.models import OrgAssistantConfig

        url = f"/orgs/{org.slug}/settings/assistant"
        client = login(org_admin)
        html = client.get(url).content.decode()
        assert 'name="actions_enabled"' in html and "Let the assistant propose actions" in html
        client.post(url, {"enabled": "on", "provider": "anthropic", "api_key": "sk-k",
                          "actions_enabled": "on"})
        assert OrgAssistantConfig.objects.get(org=org).actions_enabled is True
        client.post(url, {"enabled": "on", "provider": "anthropic", "api_key": ""})
        assert OrgAssistantConfig.objects.get(org=org).actions_enabled is False
