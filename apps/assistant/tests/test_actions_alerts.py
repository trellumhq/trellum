"""Alerts from the assistant (``create_alert`` / ``update_alert``) and the
assistant from an alert (the email's "open in the assistant" link).

internal planning ticket #148; design ``internal design notes`` §3, §5.
"""
import pytest

from apps.alerts.models import AlertRule, AlertRun
from apps.assistant.alert_access import edit_revision
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


def test_assistant_alert_notification_policy_matches_form(studio_tree, report_row):
    from apps.assistant import actions
    spec = actions.describe(studio_tree, "create_alert", {**ARGS, "notification_policy": "transition"})
    assert spec["arguments"]["notification_policy"] == "transition"
    assert "policy transition" in spec["summary"]
    assert "notification_policy" in actions.describe(studio_tree, "create_alert", {
        **ARGS, "notification_policy": "unsupported"})


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
            "update_alert", {"alert_id": rule.pk, "edit_revision": edit_revision(rule), "instructions": "Tell me if revenue drops 20%."},
        )
        card = dict(events)["proposal"]
        assert card["arguments"] == {"alert_id": rule.pk, "edit_revision": edit_revision(rule), "instructions": "Tell me if revenue drops 20%."}
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
            "update_alert", {"alert_id": 999, "edit_revision": "unknown", "instructions": "x"},
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
        assert f"Alert ID: {run.rule_id}" in data
        assert f"/alerts/{run.rule_id}" in data and edit_revision(run.rule) in data
        assert "decision: alert" in data and "Title: Revenue fell" in data
        assert "Revenue is down 30% on the week." in data
        assert "- gross_revenue 2026-09-07: 700 vs 1000 a week earlier" in data
        assert f"{run.started_at:%Y-%m-%d}" in data
        assert f"report `{run.rule.report.slug}`" in text

    def test_unbound_session_has_no_alert_block(self, viewer, org, studio_tree):
        session = AssistantSession.objects.create(user=viewer, org=org, studio=studio_tree)
        assert "alert-run" not in build_system_prompt(AssistantToolbox(studio_tree, session=session))[1]["text"]


class TestDiscovery:
    def test_report_names_and_duplicates_are_returned_with_identity(self, run, studio_tree, developer, report_row):
        import json
        from apps.reports.models import Report
        report_row.name = "Revenue report"
        report_row.save(update_fields=["name"])
        other = Report.objects.create(studio=studio_tree, slug="other", name="Revenue report")
        duplicate = AlertRule.objects.create(org=studio_tree.org, studio=studio_tree,
            report=other, name=run.rule.name)
        box = AssistantToolbox(studio_tree, actor=developer, role=roles.DEVELOPER)
        found = json.loads(box.execute("list_alerts", {"report": "revenue report", "name": "Revenue"}))
        assert {r["alert_id"] for r in found["alerts"]} == {run.rule_id, duplicate.pk}
        assert {r["report"] for r in found["alerts"]} == {report_row.slug, "other"}
        detail = json.loads(box.execute("get_alert", {"alert_id": run.rule_id}))
        assert detail["edit_revision"] == edit_revision(run.rule)
        assert detail["condition_revision"] == run.rule.revision
        assert detail["detail_url"].endswith(f"/alerts/{run.rule_id}")
        assert "condition" in detail and "evidence" in detail and "recipients" in detail
        assert not ProposedAction.objects.exists()

    def test_bound_reads_and_foreign_ids_cannot_widen_access(self, run, studio_tree, studio2, report_row, developer):
        import json
        from apps.reports.models import Report
        other = AlertRule.objects.create(org=studio_tree.org, studio=studio2,
            report=Report.objects.create(studio=studio2, slug="secret"), name="Secret")
        box = AssistantToolbox(studio_tree, actor=developer, role=roles.DEVELOPER,
                               may_write=True, scope="report", report=report_row)
        assert len(json.loads(box.execute("list_alerts", {}))["alerts"]) == 1
        for value in (other.pk, 999999, -1, True, "1", 2**100):
            assert box.execute("get_alert", {"alert_id": value}) == "Error: alert rule not found."
        assert "not available" in box.execute("update_alert", {"alert_id": run.rule_id})
        assert "only run_report may mutate" in box.capability_help()

    @pytest.mark.parametrize("changed", ["recipients", "recipient_groups", "cooldown_hours", "send_minute", "instructions", "name", "enabled"])
    def test_discovery_token_rejects_every_intervening_settings_change(self, run, studio_tree, developer, changed, make_group):
        from apps.assistant import actions
        token = edit_revision(run.rule)
        if changed == "recipients":
            run.rule.recipients.add(developer)
        elif changed == "recipient_groups":
            run.rule.recipient_groups.add(make_group("Recipients"))
        else:
            setattr(run.rule, changed, {"cooldown_hours": 6, "send_minute": 15,
                "instructions": "new predicate", "name": "New", "enabled": False}[changed])
            run.rule.save(update_fields=[changed])
        result = actions.describe(studio_tree, "update_alert", {
            "alert_id": run.rule_id, "edit_revision": token, "cooldown_hours": 3}, actor=developer)
        assert "changed" in result

    def test_evaluator_observations_do_not_invalidate_edit_token(self, run):
        token = edit_revision(run.rule)
        AlertRule.objects.filter(pk=run.rule_id).update(live_state={"condition": "met"})
        run.rule.refresh_from_db()
        assert edit_revision(run.rule) == token


@SEES_WORKER_THREAD
class TestSafeApproval:
    @pytest.mark.parametrize("revoke", ["actions", "role"])
    def test_current_permission_is_required_at_approval(self, login, developer, prefix,
            actions_on, fake_llm, make_session, run, revoke):
        from apps.studios.models import StudioMembership

        client = login(developer)
        propose_via_model(client, prefix, make_session(developer), fake_llm, "update_alert", {
            "alert_id": run.rule_id, "edit_revision": edit_revision(run.rule), "name": "Proposed"})
        if revoke == "actions":
            actions_on.actions_enabled = False
            actions_on.save(update_fields=["actions_enabled"])
        else:
            StudioMembership.objects.filter(user=developer, studio=run.rule.studio).update(role=roles.VIEWER)
        assert approve(client, prefix, ProposedAction.objects.get()).status_code == 403
        run.rule.refresh_from_db()
        assert run.rule.name == "Revenue watch"

    @pytest.mark.parametrize("changed", ["recipients", "cooldown_hours", "instructions"])
    def test_approved_update_rejects_changed_settings(self, login, developer, prefix,
            actions_on, fake_llm, make_session, run, changed):
        client = login(developer)
        propose_via_model(client, prefix, make_session(developer), fake_llm, "update_alert", {
            "alert_id": run.rule_id, "edit_revision": edit_revision(run.rule), "name": "Proposed"})
        if changed == "recipients":
            run.rule.recipients.add(developer)
        else:
            setattr(run.rule, changed, 2 if changed == "cooldown_hours" else "Changed")
            run.rule.save(update_fields=[changed])
        response = approve(client, prefix, ProposedAction.objects.get())
        assert response.json()["status"] == "failed"
        assert "changed" in response.json()["result"]
        run.rule.refresh_from_db()
        assert run.rule.name == "Revenue watch"

    def test_create_discover_propose_approve_and_preserve_episode(self, login, developer,
            prefix, actions_on, fake_llm, make_session, report_row, studio_tree):
        import json
        client = login(developer)
        session = make_session(developer)
        propose_via_model(client, prefix, session, fake_llm, "create_alert", ARGS)
        made = approve(client, prefix, ProposedAction.objects.get())
        rule = AlertRule.objects.get()
        assert f"alert_id: {rule.pk}" in made.json()["result"]
        AlertRule.objects.filter(pk=rule.pk).update(live_state={"revision": rule.revision, "condition": "met"})
        box = AssistantToolbox(studio_tree, actor=developer)
        found = json.loads(box.execute("list_alerts", {"report": report_row.slug}))["alerts"][0]
        detail = json.loads(box.execute("get_alert", {"alert_id": found["alert_id"]}))
        propose_via_model(client, prefix, session, fake_llm, "update_alert", {
            "alert_id": rule.pk, "edit_revision": detail["edit_revision"], "cooldown_hours": 2})
        updated = approve(client, prefix, ProposedAction.objects.order_by("pk").last())
        assert updated.json()["status"] == "executed"
        rule.refresh_from_db()
        assert rule.cooldown_hours == 2 and rule.live_state["condition"] == "met"

    def test_legacy_update_requires_reproposal(self, login, developer, prefix, actions_on, make_session, run):
        from django.utils import timezone

        proposal = ProposedAction.objects.create(session=make_session(developer), org=run.rule.org,
            studio=run.rule.studio, user=developer, tool="update_alert", summary="Legacy update",
            arguments={"alert_id": run.rule_id, "name": "Unsafe"}, required_role=roles.DEVELOPER,
            expires_at=timezone.now() + ProposedAction.EXPIRY)
        response = approve(login(developer), prefix, proposal)
        assert response.json()["status"] == "failed" and "propose again" in response.json()["result"]


@pytest.mark.django_db(transaction=True)
def test_portal_recipient_save_holds_lock_until_assistant_revision_check(
        login, developer, run, actions_on, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from django.db import close_old_connections
    from apps.alerts.forms import AlertRuleForm
    from apps.alerts.tests.test_views import _form
    from apps.assistant import actions

    saving, release, attempting, finished = Event(), Event(), Event(), Event()
    original = AlertRuleForm._save_m2m
    run.rule.recipient_roles = ["developers"]
    run.rule.save(update_fields=["recipient_roles"])
    args = {"alert_id": run.rule_id, "edit_revision": edit_revision(run.rule), "name": "Assistant edit"}
    client = login(developer)
    url = f"/s/{run.rule.org.slug}/{run.rule.studio.slug}/alerts/{run.rule_id}/edit"

    def held_m2m(form):
        saving.set()
        assert release.wait(5)
        original(form)

    monkeypatch.setattr(AlertRuleForm, "_save_m2m", held_m2m)

    def portal_save():
        try:
            return client.post(url, _form(run.rule, recipients=[developer.pk]))
        finally:
            close_old_connections()

    def assistant_save():
        attempting.set()
        try:
            with pytest.raises(actions.ActionFailed, match="settings changed"):
                actions.execute(SimpleNamespace(studio=run.rule.studio, user=developer), "update_alert", args, {})
        finally:
            finished.set()
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        portal = pool.submit(portal_save)
        try:
            assert saving.wait(5)
            assistant = pool.submit(assistant_save)
            assert attempting.wait(5)
            assert not finished.wait(0.2)
        finally:
            release.set()
        assert portal.result(timeout=5).status_code == 302
        assistant.result(timeout=5)
    run.rule.refresh_from_db()
    assert run.rule.name == "Revenue watch"
    assert list(run.rule.recipients.all()) == [developer]
