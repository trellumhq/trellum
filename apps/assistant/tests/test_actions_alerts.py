"""Alerts from the assistant (``create_alert`` / ``update_alert``) and the
assistant from an alert (the email's "open in the assistant" link).

internal planning ticket #148; design ``internal design notes`` §3, §5.
"""
import pytest
from django.conf import settings

from apps.alerts.models import AlertRule, AlertRun
from apps.assistant.models import AssistantSession, ProposedAction
from apps.assistant.system_prompt import build_system_prompt
from apps.assistant.tools import AssistantToolbox
from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs.models import PermissionGroupGrant
from apps.reports.models import ReportPermissionGrant

from .test_actions import (  # noqa: F401 -- fixtures
    SEES_WORKER_THREAD,
    actions_on,
    approve,
    developer,
    make_session,
    propose_via_model,
)
from .test_message_sse import fake_llm  # noqa: F401

pytestmark = pytest.mark.django_db

ARGS = {
    "name": "Paid revenue watch",
    "report": "player-overview",
    "instructions": "Paid revenue in DE was 12.4k on 2026-09-07; tell me if it drops 10% week on week.",
    "recipient_roles": ["developers"],
    "cooldown_hours": 12,
}


@pytest.fixture
def run(report_row, developer, org, studio_tree):
    rule = AlertRule.objects.create(
        org=org, studio=studio_tree, report=report_row, name="Revenue watch",
        instructions="Tell me if revenue drops.", created_by=developer,
    )
    return AlertRun.objects.create(
        rule=rule, decision=AlertRun.DECISION_ALERT, title="Revenue fell",
        message="Revenue is down 30% on the week.",
        evidence={"cited": ["gross_revenue 2026-09-07: 700 vs 1000 a week earlier"]},
    )


class TestCatalogue:
    def test_developer_sees_both_and_a_viewer_neither(self, studio_tree):
        dev = [s["name"] for s in AssistantToolbox(studio_tree, role=roles.DEVELOPER, may_write=True).schemas]
        assert "create_alert" in dev and "update_alert" in dev
        viewer = [s["name"] for s in AssistantToolbox(studio_tree, role=roles.VIEWER, may_write=True).schemas]
        assert "create_alert" not in viewer and "update_alert" not in viewer

    def test_write_header_points_watch_requests_at_create_alert(self, studio_tree):
        text = build_system_prompt(AssistantToolbox(studio_tree, role=roles.DEVELOPER, may_write=True))[0]["text"]
        assert "`create_alert`" in text and "watch this for me" in text
        read_only = build_system_prompt(AssistantToolbox(studio_tree))[0]["text"]
        assert "create_alert" not in read_only


@SEES_WORKER_THREAD
class TestCreate:
    def test_call_proposes_and_approve_creates_the_rule(
        self, login, developer, prefix, actions_on, fake_llm, make_session, report_row,
        make_user, grant_studio, org, studio_tree,
    ):
        picked = make_user("picked@demo.example", org=org)
        grant_studio(picked, studio_tree, roles.VIEWER)
        client = login(developer)
        events, _ = propose_via_model(
            client, prefix, make_session(developer), fake_llm,
            "create_alert", {**ARGS, "recipient_emails": [picked.email]},
        )
        card = dict(events)["proposal"]
        assert card["tool"] == "create_alert" and card["secret_fields"] == []
        assert "Paid revenue watch" in card["summary"] and "developers" in card["summary"]
        # The rule as it will be saved: defaults filled in, recipients resolved.
        assert card["arguments"]["trigger"] == "after_build"
        assert card["arguments"]["recipient_emails"] == [picked.email]
        assert card["arguments"]["cooldown_hours"] == 12
        assert card["required_role"] == roles.DEVELOPER
        assert not AlertRule.objects.exists()

        proposal = ProposedAction.objects.get()
        resp = approve(client, prefix, proposal)
        assert resp.status_code == 200 and resp.json()["status"] == "executed"
        rule = AlertRule.objects.get()
        assert rule.created_by == developer and rule.report == report_row
        assert rule.org == org and rule.studio == studio_tree
        assert rule.name == "Paid revenue watch" and rule.instructions == ARGS["instructions"]
        assert rule.trigger == "after_build" and rule.cooldown_hours == 12
        assert rule.recipient_roles == ["developers"] and list(rule.recipients.all()) == [picked]
        assert f"/s/{org.slug}/{studio_tree.slug}/alerts" in resp.json()["result"]
        row = AuditLog.objects.get(action="alert.create")
        assert row.actor == developer
        assert row.metadata["rule_id"] == rule.pk and row.metadata["proposal_id"] == proposal.pk

    def test_non_member_recipient_is_an_error_and_no_rule(
        self, login, developer, prefix, actions_on, fake_llm, make_session, report_row, make_user, org
    ):
        make_user("member-elsewhere@demo.example", org=org)  # in the org, not the studio
        events, _ = propose_via_model(
            login(developer), prefix, make_session(developer), fake_llm,
            "create_alert", {**ARGS, "recipient_emails": ["member-elsewhere@demo.example", "nobody@else.example"]},
        )
        result = dict(events)["tool_result"]
        assert result["is_error"] is True
        assert "member-elsewhere@demo.example" in result["excerpt"] and "nobody@else.example" in result["excerpt"]
        assert "proposal" not in [n for n, _ in events]
        assert not ProposedAction.objects.exists() and not AlertRule.objects.exists()

    def test_schedule_fields_are_checked_like_the_form(
        self, login, developer, prefix, actions_on, fake_llm, make_session, report_row
    ):
        events, _ = propose_via_model(
            login(developer), prefix, make_session(developer), fake_llm,
            "create_alert", {**ARGS, "trigger": "schedule", "freq": "weekly", "weekday": 9},
        )
        result = dict(events)["tool_result"]
        assert result["is_error"] is True and "weekday" in result["excerpt"]
        assert not ProposedAction.objects.exists()

    def test_unknown_report_is_an_error(
        self, login, developer, prefix, actions_on, fake_llm, make_session
    ):
        events, _ = propose_via_model(
            login(developer), prefix, make_session(developer), fake_llm,
            "create_alert", {**ARGS, "report": "ghost"},
        )
        assert dict(events)["tool_result"]["is_error"] is True
        assert not ProposedAction.objects.exists()


@SEES_WORKER_THREAD
class TestUpdate:
    def test_update_changes_instructions_and_audits(
        self, login, developer, prefix, actions_on, fake_llm, make_session, run
    ):
        rule = run.rule
        client = login(developer)
        events, _ = propose_via_model(
            client, prefix, make_session(developer), fake_llm,
            "update_alert", {"alert_id": rule.pk, "instructions": "Tell me if revenue drops 20%."},
        )
        card = dict(events)["proposal"]
        assert card["arguments"] == {"alert_id": rule.pk, "instructions": "Tell me if revenue drops 20%."}
        assert "Revenue watch" in card["summary"] and "instructions" in card["summary"]

        proposal = ProposedAction.objects.get()
        resp = approve(client, prefix, proposal)
        assert resp.json()["status"] == "executed"
        rule.refresh_from_db()
        assert rule.instructions == "Tell me if revenue drops 20%."
        assert rule.name == "Revenue watch" and rule.cooldown_hours == 24  # untouched
        row = AuditLog.objects.get(action="alert.update")
        assert row.metadata["rule_id"] == rule.pk and row.metadata["proposal_id"] == proposal.pk
        assert row.metadata["changed"] == ["instructions"]

    def test_unknown_rule_is_an_error(
        self, login, developer, prefix, actions_on, fake_llm, make_session
    ):
        events, _ = propose_via_model(
            login(developer), prefix, make_session(developer), fake_llm,
            "update_alert", {"alert_id": 999, "instructions": "x"},
        )
        assert dict(events)["tool_result"]["is_error"] is True
        assert not ProposedAction.objects.exists()


class TestFromAlert:
    def test_selected_viewer_needs_access_to_the_alert_report(
        self, login, member, org, studio_tree, run, make_group, attach_group, settings
    ):
        settings.TRELLUM_REPORT_SCOPED_ACCESS_READY = True
        group = make_group("Selected", grants=[(studio_tree, roles.VIEWER)])
        grant = group.grants.get()
        grant.viewer_scope = PermissionGroupGrant.REPORT_SCOPE_SELECTED
        grant.save(update_fields=["viewer_scope"])
        attach_group(member, group)
        client = login(member)
        url = f"/s/{org.slug}/{studio_tree.slug}/assistant/from-alert/{run.pk}"

        assert client.get(url).status_code == 404
        ReportPermissionGrant.objects.create(grant=grant, report=run.rule.report)
        assert client.get(url).status_code == 302
        session = AssistantSession.objects.get()
        assert session.scope == AssistantSession.SCOPE_REPORT

    def test_entry_binds_a_session_and_opens_the_report(self, login, viewer, org, studio_tree, run):
        resp = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/assistant/from-alert/{run.pk}")
        assert resp.status_code == 302
        session = AssistantSession.objects.get()
        assert resp["Location"] == f"/s/{org.slug}/{studio_tree.slug}/r/{run.rule.report.slug}/?assistant={session.pk}"
        assert session.user == viewer and session.alert_run == run
        assert session.report == run.rule.report and session.title == "Alert: Revenue fell"
        assert session.to_summary()["alert_title"] == "Revenue fell"

    def test_other_studios_run_is_404(self, login, viewer, org, studio_tree, studio2, developer, run):
        from apps.reports.models import Report

        other = AlertRule.objects.create(
            org=org, studio=studio2, report=Report.objects.create(studio=studio2, slug="other"),
            name="Other", created_by=developer,
        )
        other_run = AlertRun.objects.create(rule=other, decision="alert", title="Elsewhere")
        resp = login(viewer).get(f"/s/{org.slug}/{studio_tree.slug}/assistant/from-alert/{other_run.pk}")
        assert resp.status_code == 404
        assert not AssistantSession.objects.exists()

    def test_bound_session_prompt_carries_the_run_as_data(self, viewer, org, studio_tree, run):
        session = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree, alert_run=run)
        blocks = build_system_prompt(AssistantToolbox(studio_tree, session=session))
        text = blocks[1]["text"]
        assert "cache_control" not in blocks[1]  # the fresh block, not the cached one
        data = text.split(f'<data source="alert-run:{run.pk}">', 1)[1].split("</data>", 1)[0]
        assert "Rule: Revenue watch" in data and "Tell me if revenue drops." in data
        assert "decision: alert" in data and "Title: Revenue fell" in data
        assert "Revenue is down 30% on the week." in data
        assert "- gross_revenue 2026-09-07: 700 vs 1000 a week earlier" in data
        assert f"{run.started_at:%Y-%m-%d}" in data
        assert f"report `{run.rule.report.slug}`" in text

    def test_unbound_session_has_no_alert_block(self, viewer, org, studio_tree):
        session = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree)
        assert "alert-run" not in build_system_prompt(AssistantToolbox(studio_tree, session=session))[1]["text"]

    def test_widget_reads_the_deep_link_and_pins_the_alert(self):
        source = (settings.BASE_DIR / "static" / "assistant.js").read_text(encoding="utf-8")
        assert "assistant=(\\d+)" in source and "_sp.delete('assistant')" in source
        assert "'About alert: ' + title" in source and "setAbout(s.alert_title)" in source
