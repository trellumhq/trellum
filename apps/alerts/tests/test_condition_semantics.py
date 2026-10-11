"""Synthetic decisions exercise policy/state, not a live model's judgement."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as utc
import json
from threading import Barrier, Event

import pytest
from django.core import mail
from django.db import close_old_connections, connections
from django.utils import timezone

from apps.alerts import evaluator
from apps.alerts.forms import AlertRuleForm
from apps.alerts.models import AlertEvaluationClaim, AlertRule, AlertRun
from apps.alerts.tests.conftest import decide
from apps.assistant.tests.test_message_sse import FakeResponse, ToolUseBlock
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def run_at(rule, fake_llm, condition, hour, **kwargs):
    fake_llm(decide(condition, **kwargs))
    return evaluator.evaluate(rule, now=datetime(2026, 10, 1, tzinfo=utc.utc) + timedelta(hours=hour))


def transition(rule):
    rule.notification_policy = "transition"
    rule.cooldown_hours = 2
    rule.save()
    rule.recipients.add(rule.created_by)


def test_repeat_policy_retains_met_through_cooldown_and_recovery(rule, fake_llm):
    rule.cooldown_hours = 2
    rule.save()
    rule.recipients.add(rule.created_by)
    first = run_at(rule, fake_llm, True, 0)
    assert first.delivery_status == "accepted"
    second = run_at(rule, fake_llm, True, 1)
    assert second.condition == "met" and second.status == "ok"
    assert second.delivery_disposition == "cooldown"
    run_at(rule, fake_llm, False, 1.5)
    rule.refresh_from_db()
    assert rule.last_alerted_at == first.started_at
    third = run_at(rule, fake_llm, True, 2)
    assert third.delivery_status == "accepted" and len(mail.outbox) == 2


def test_transition_initial_pending_unknown_and_next_eligible(rule, fake_llm):
    transition(rule)
    at = datetime(2026, 10, 1, tzinfo=utc.utc)
    AlertRule.objects.filter(pk=rule.pk).update(last_alerted_at=at)
    initial = run_at(rule, fake_llm, True, 1)
    assert initial.delivery_disposition == "cooldown"
    rule.refresh_from_db()
    pending = rule.live_state.copy()
    assert pending["condition"] == "met" and pending["pending"]
    unknown = run_at(rule, fake_llm, None, 2)
    assert unknown.delivery_disposition == "unknown" and not mail.outbox
    rule.refresh_from_db()
    assert rule.live_state["episode"] == pending["episode"] and rule.live_state["pending"]
    assert run_at(rule, fake_llm, True, 3).delivery_status == "accepted"
    assert run_at(rule, fake_llm, True, 6).delivery_disposition == "episode_delivered"
    assert len(mail.outbox) == 1


def test_recovery_cancels_pending_then_recurrence_starts_new_episode(rule, fake_llm):
    transition(rule)
    at = datetime(2026, 10, 1, tzinfo=utc.utc)
    AlertRule.objects.filter(pk=rule.pk).update(last_alerted_at=at)
    run_at(rule, fake_llm, True, 1)
    rule.refresh_from_db()
    old_episode = rule.live_state["episode"]
    run_at(rule, fake_llm, False, 1.5)
    assert run_at(rule, fake_llm, False, 3).delivery_disposition == "not_met"
    assert mail.outbox == []
    assert run_at(rule, fake_llm, True, 4).delivery_status == "accepted"
    rule.refresh_from_db()
    assert rule.live_state["episode"] != old_episode
    assert not rule.live_state["pending"]
    assert rule.last_alerted_at == at + timedelta(hours=4)


def test_preview_does_not_mutate_any_production_state(rule, fake_llm):
    transition(rule)
    run_at(rule, fake_llm, True, 0)
    rule.refresh_from_db()
    before = (rule.live_state.copy(), rule.last_run_at, rule.last_alerted_at,
              AlertEvaluationClaim.objects.count())
    fake_llm(decide(False))
    preview = evaluator.evaluate(rule, deliver=False, evaluation_key="preview-does-not-claim")
    rule.refresh_from_db()
    assert preview.condition == "not_met" and preview.delivery_disposition == "test"
    assert (rule.live_state, rule.last_run_at, rule.last_alerted_at,
            AlertEvaluationClaim.objects.count()) == before
    assert len(mail.outbox) == 1


@pytest.mark.parametrize("changed", ["instructions", "timezone", "report"])
def test_predicate_edits_reset_baseline_preserving_cooldown(rule, fake_llm, changed):
    from apps.reports.models import Report
    transition(rule)
    run_at(rule, fake_llm, True, 0)
    rule.refresh_from_db()
    old_revision, alerted = rule.revision, rule.last_alerted_at
    if changed == "report":
        rule.report = Report.objects.create(studio=rule.studio, slug="other")
    else:
        setattr(rule, changed, "Europe/Amsterdam" if changed == "timezone" else "New predicate")
    rule.save()
    rule.refresh_from_db()
    assert rule.revision != old_revision and rule.live_state["condition"] == "unknown"
    assert not rule.live_state["pending"] and rule.last_alerted_at == alerted


def test_cadence_and_recipients_do_not_create_episode_or_overwrite_new_observations(rule, fake_llm):
    transition(rule)
    draft = AlertRule.objects.get(pk=rule.pk)
    run_at(rule, fake_llm, True, 0)
    rule.refresh_from_db()
    before = (rule.revision, rule.live_state.copy(), rule.last_run_at, rule.last_alerted_at)
    draft.freq = "weekly"
    draft.recipient_roles = ["developers"]
    draft.save()
    rule.refresh_from_db()
    assert (rule.revision, rule.live_state, rule.last_run_at, rule.last_alerted_at) == before
    assert run_at(rule, fake_llm, True, 4).delivery_disposition == "episode_delivered"


def test_missing_evidence_is_unknown_not_healthy(rule, fake_llm):
    run = run_at(rule, fake_llm, False, 0, evidence=[])
    assert run.condition == "unknown" and run.delivery_disposition == "unknown"


@pytest.mark.parametrize("condition,last,pending,policy,hours,expected", [
    ("met", "unknown", False, "transition", None, "would_notify"),
    ("met", "met", False, "transition", None, "episode_delivered"),
    ("met", "met", True, "transition", 1, "cooldown"),
    ("met", "met", True, "transition", 3, "would_notify"),
    ("unknown", "met", True, "transition", 3, "unknown"),
    ("not_met", "met", True, "repeat", 3, "not_met"),
])
def test_pure_policy_simulation(condition, last, pending, policy, hours, expected):
    at = timezone.now()
    serialized = json.dumps({"revision": "revision", "condition": last, "pending": pending})
    snapshot = evaluator.RuleSnapshot(None, 1, 1, 1, "Draft", "Condition", 2,
        at - timedelta(hours=hours) if hours else None, revision="revision",
        notification_policy=policy, live_state_json=serialized)
    result = evaluator.simulate_delivery(snapshot, condition, now=at)
    assert result["disposition"] == expected and snapshot.live_state_json == serialized


def test_changed_draft_predicate_simulates_new_episode_without_db(rule):
    from dataclasses import replace
    snapshot = replace(evaluator.RuleSnapshot.from_rule(rule), notification_policy="transition",
        revision="new", live_state_json=json.dumps({"revision": "old", "condition": "met", "pending": False}))
    assert evaluator.simulate_delivery(snapshot, "met")["disposition"] == "would_notify"
    assert AlertRun.objects.count() == AlertEvaluationClaim.objects.count() == 0


def test_known_failure_remains_pending_and_retries_next_evaluation(rule, fake_llm, monkeypatch):
    from apps.reports import notify
    transition(rule)
    monkeypatch.setattr(notify, "_send_simple", lambda *a: 0)
    assert run_at(rule, fake_llm, True, 0).delivery_status == "failed"
    rule.refresh_from_db()
    assert rule.live_state["pending"] and not rule.live_state["claim"]
    monkeypatch.setattr(notify, "_send_simple", lambda *a: 1)
    assert run_at(rule, fake_llm, True, 1).delivery_status == "accepted"


def test_uncertain_transport_remains_visible_through_recovery_and_recurrence(rule, fake_llm, monkeypatch):
    from apps.reports import notify
    transition(rule)
    def fail(*a):
        raise TimeoutError("acceptance not known")
    monkeypatch.setattr(notify, "_send_simple", fail)
    first = run_at(rule, fake_llm, True, 0)
    assert first.delivery_disposition == "unconfirmed" and first.uncertain_to == [rule.created_by.email]
    run_at(rule, fake_llm, False, 1)
    recur = run_at(rule, fake_llm, True, 4)
    assert recur.delivery_disposition == "unconfirmed"
    rule.refresh_from_db()
    assert rule.live_state["claim"]["run_id"] == first.pk and rule.last_alerted_at is None


def test_duplicate_trigger_zero_cooldown_does_not_call_provider_twice(rule, fake_llm):
    rule.cooldown_hours = 0
    rule.save()
    rule.recipients.add(rule.created_by)
    calls = fake_llm(decide(True))
    first = evaluator.evaluate(rule, evaluation_key="build:123")
    second = evaluator.evaluate(rule, evaluation_key="build:123")
    assert second.pk == first.pk and len(calls) == 1 and len(mail.outbox) == 1
    rule.instructions = "Different predicate"
    rule.save()
    fake_llm(decide(True))
    assert evaluator.evaluate(rule, evaluation_key="build:123").pk != first.pk


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_work_claim_has_one_provider_owner(rule, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    def work(snapshot, run, *args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        run.condition = "unknown"
    monkeypatch.setattr(evaluator, "_evaluate", work)
    def evaluate():
        close_old_connections()
        try:
            current = AlertRule.objects.select_related("created_by").get(pk=rule.pk)
            return evaluator.evaluate(current, evaluation_key="build:concurrent")
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(evaluate)
        assert entered.wait(5)
        second = pool.submit(evaluate).result(timeout=5)
        assert second.finished_at is None and second.status == "running"
        release.set()
        completed = first.result(timeout=5)
    assert calls == [1] and second.pk == completed.pk
    assert AlertEvaluationClaim.objects.count() == AlertRun.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_eligible_deliveries_claim_only_one(rule):
    rule.cooldown_hours = 0
    rule.save()
    snapshot = evaluator.RuleSnapshot.from_rule(rule)
    barrier = Barrier(2)
    def claim(offset):
        close_old_connections()
        try:
            run = AlertRun(rule=rule, condition="met", started_at=timezone.now() + timedelta(microseconds=offset))
            barrier.wait(timeout=5)
            current = evaluator.claim_delivery(snapshot, run, rule.created_by, rule.report)
            return current is not None, run.delivery_disposition
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [0, 1]))
    assert sum(ok for ok, _ in outcomes) == 1
    assert len(AlertRule.objects.get(pk=rule.pk).live_state["claim"]["token"]) == 32


def test_late_met_cannot_resurrect_newer_recovery(rule):
    snapshot = evaluator.RuleSnapshot.from_rule(rule)
    now = timezone.now()
    met = AlertRun(rule=rule, condition="met", started_at=now)
    assert evaluator.claim_delivery(snapshot, met, rule.created_by, rule.report)
    recovery = AlertRun(rule=rule, condition="not_met", started_at=now + timedelta(seconds=2))
    assert evaluator.claim_delivery(snapshot, recovery, rule.created_by, rule.report) is None
    older = AlertRun(rule=rule, condition="met", started_at=now + timedelta(seconds=1))
    assert evaluator.claim_delivery(snapshot, older, rule.created_by, rule.report) is None
    assert older.delivery_disposition == "stale"
    evaluator.finish_delivery(rule.pk, met, accepted=True, uncertain=False)
    rule.refresh_from_db()
    assert rule.live_state["condition"] == "not_met" and not rule.live_state["pending"]


@pytest.mark.parametrize("now", [datetime(2026, 10, 25, 0, 20, tzinfo=utc.utc),
                                  datetime(2026, 10, 25, 1, 20, tzinfo=utc.utc)])
def test_schedule_key_uses_due_slot_during_dst_fold(rule, now):
    rule.freq, rule.timezone, rule.send_minute = "hourly", "Europe/Amsterdam", 15
    key = evaluator.scheduled_evaluation_key(rule, now)
    assert key == f"schedule:{now.replace(minute=15).isoformat()}"
    assert evaluator.scheduled_evaluation_key(rule, now + timedelta(minutes=20)) == key


@pytest.mark.parametrize("name,rows,met,window", [
    ("draining", [-1200, -1500], True, "net drain over 48 hours"),
    ("not_draining", [-1200, 1500], False, "net drain over 48 hours"),
    ("insufficient", [], None, "net drain over 48 hours: insufficient history"),
    ("currency_unavailable", [1001], None, "USD >1000, but only EUR evidence and no conversion"),
    ("boundary", [1000], False, "last completed clock hour, strict amount >1000"),
    ("over_threshold", [1001], True, "last completed clock hour, strict amount >1000"),
    ("rolling", [800, 500], True, "rolling last 60 minutes ending 10:30 UTC"),
    ("completed_hour", [800], False, "last completed clock hour 09:00–10:00 UTC"),
])
def test_synthetic_evidence_and_window_persist_through_real_read_tool(rule, fake_llm, name, rows, met, window):
    out = rule.studio.output_dir / rule.report.slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps({"_ds_flows": {
        "_cols": ["row", "amount", "currency"], "_data": [[i, value, "EUR"] for i, value in enumerate(rows)], "_dict": {}}}), encoding="utf-8")
    rule.instructions = f"{window}; EUR only; preserve currency."
    rule.save()
    interpretation = {"window": window, "timezone": "UTC", "scope": "flows, EUR",
                      "freshness": "insufficient history" if met is None else "synthetic complete coverage"}
    fake_llm(FakeResponse([ToolUseBlock("read_flows", "query_report_data", {
        "slug": rule.report.slug, "dataset_id": "flows"})], stop_reason="tool_use"),
        decide(met, evidence=[f"{name}: EUR {rows}"], interpretation=interpretation))
    run = evaluator.evaluate(rule, deliver=False)
    assert run.condition == ("met" if met is True else "not_met" if met is False else "unknown")
    assert run.interpretation == interpretation and run.rule_revision == rule.revision
    assert run.evidence["snapshot"]["interpretation"]["window"] == window
    assert run.evidence["snapshot"]["looked_at"]


def test_model_prompt_separates_condition_and_strict_window_contract(rule, fake_llm):
    calls = fake_llm(decide(False))
    evaluator.evaluate(rule, deliver=False)
    text = "\n".join(b["text"] for b in calls[0]["system"]).split("# ALERT EVALUATION MODE")[1]
    assert "already covered" not in text and "Repeated conditions" in text
    for term in ("unknown, never healthy", "last 60 minutes", "completed clock hour", "net drain over 48", "exactly 1000", "currency"):
        assert term in text


def test_keyed_budget_warning_remains_once_across_distinct_slots(rule, assistant_config, org, fake_llm):
    from decimal import Decimal
    from apps.assistant.models import LlmUsage
    assistant_config.per_user_budget_usd = Decimal("1")
    assistant_config.save()
    LlmUsage.add_cost(org, rule.created_by, 2)
    calls = fake_llm(decide(True))
    first = evaluator.evaluate(rule, evaluation_key="schedule:slot1")
    second = evaluator.evaluate(rule, evaluation_key="schedule:slot2")
    assert first.status == second.status == "skipped_budget"
    assert first.budget_warning_sent and second.budget_warning_sent
    assert len(mail.outbox) == 1 and not calls


def unresolved_claim(rule):
    at = timezone.now() - timedelta(hours=2)
    run = AlertRun.objects.create(rule=rule, started_at=at, finished_at=at + timedelta(minutes=1),
        execution_mode="live", rule_revision=rule.revision, condition="met",
        delivery_disposition="unconfirmed", uncertain_to=[rule.created_by.email])
    state = {"revision": rule.revision, "condition": "met", "pending": True, "episode": "episode",
             "claim": {"run_id": run.pk, "token": "token", "episode": "episode", "started_at": at.isoformat()}}
    AlertRule.objects.filter(pk=rule.pk).update(live_state=state)
    return run, state


def reconcile_url(rule):
    return f"/s/{rule.org.slug}/{rule.studio.slug}/alerts/{rule.pk}/reconcile"


@pytest.mark.parametrize("resolution", ["notified", "not_accepted"])
def test_manual_resolution_preserves_transport_evidence_and_audits(rule, login, resolution):
    run, _ = unresolved_claim(rule)
    before = timezone.now()
    response = login(rule.created_by).post(reconcile_url(rule), {
        "resolution": resolution, "token": "token", "run_id": run.pk})
    assert response.status_code == 302
    rule.refresh_from_db()
    run.refresh_from_db()
    assert rule.live_state["claim"] is None
    assert rule.live_state["pending"] == (resolution == "not_accepted")
    assert bool(rule.last_alerted_at) == (resolution == "notified")
    if resolution == "notified":
        assert rule.last_alerted_at >= before
    assert run.delivery_disposition == "unconfirmed" and run.uncertain_to == [rule.created_by.email]
    assert run.manual_resolution["actor_id"] == rule.created_by_id
    audit = AuditLog.objects.get(action="alert.delivery_reconciled")
    assert audit.actor_id == rule.created_by_id and audit.metadata["run_id"] == run.pk
    assert audit.metadata["before"]["claim"]["token"] == "token"
    assert audit.metadata["after"]["claim"] is None and mail.outbox == []


def test_mark_notified_cannot_close_newer_episode(rule, login):
    run, state = unresolved_claim(rule)
    state.update(episode="new-episode", pending=True)
    AlertRule.objects.filter(pk=rule.pk).update(live_state=state)
    assert login(rule.created_by).post(reconcile_url(rule), {
        "resolution": "notified", "token": "token", "run_id": run.pk}).status_code == 302
    rule.refresh_from_db()
    assert rule.live_state["pending"] and rule.live_state["episode"] == "new-episode"


@pytest.mark.parametrize("resolution", ["notified", "not_accepted"])
def test_manual_resolution_after_recovery_keeps_recovered_baseline(rule, login, resolution):
    run, state = unresolved_claim(rule)
    state.update(condition="not_met", pending=False)
    AlertRule.objects.filter(pk=rule.pk).update(live_state=state)
    assert login(rule.created_by).post(reconcile_url(rule), {
        "resolution": resolution, "token": "token", "run_id": run.pk}).status_code == 302
    rule.refresh_from_db()
    assert rule.live_state["condition"] == "not_met" and not rule.live_state["pending"]


@pytest.mark.parametrize("override", [{"token": "obsolete"}, {"run_id": "123456"}])
def test_stale_reconciliation_is_rejected(rule, login, override):
    run, state = unresolved_claim(rule)
    response = login(rule.created_by).post(reconcile_url(rule), {
        "resolution": "notified", "token": "token", "run_id": run.pk, **override})
    assert response.status_code == 409
    rule.refresh_from_db()
    assert rule.live_state == state and rule.last_alerted_at is None
    assert not AuditLog.objects.filter(action="alert.delivery_reconciled").exists()


def test_reconciliation_requires_developer_same_studio_and_csrf(rule, login, make_user, grant_studio, other_org):
    from django.test import Client
    from apps.core import roles
    run, state = unresolved_claim(rule)
    data = {"resolution": "notified", "token": "token", "run_id": run.pk}
    viewer = make_user("view-review@example.test", org=rule.org)
    grant_studio(viewer, rule.studio, roles.VIEWER)
    assert login(viewer).post(reconcile_url(rule), data).status_code == 403
    outsider = make_user("other-review@example.test", org=other_org)
    assert login(outsider).post(reconcile_url(rule), data).status_code == 404
    enforcing = Client(enforce_csrf_checks=True)
    enforcing.cookies = login(rule.created_by).cookies
    assert enforcing.post(reconcile_url(rule), data).status_code == 403
    rule.refresh_from_db()
    assert rule.live_state == state


def test_crashed_claim_visible_and_reconciles_only_after_deadline(rule, login):
    run, _ = unresolved_claim(rule)
    AlertRun.objects.filter(pk=run.pk).update(finished_at=None, started_at=timezone.now())
    client = login(rule.created_by)
    response = client.get(reconcile_url(rule).removesuffix("/reconcile"))
    assert b"Delivery outcome uncertain / review required" in response.content
    assert b"Mark as notified" in response.content and b"Confirm nothing was accepted" in response.content
    assert client.post(reconcile_url(rule), {"resolution": "notified", "token": "token", "run_id": run.pk}).status_code == 409
    AlertRun.objects.filter(pk=run.pk).update(started_at=timezone.now() - timedelta(hours=2))
    assert client.post(reconcile_url(rule), {"resolution": "not_accepted", "token": "token", "run_id": run.pk}).status_code == 302


def test_stale_form_preserves_accepted_live_state_and_all_timestamps(rule, fake_llm):
    transition(rule)
    draft = AlertRuleForm(data={"name": "Draft name", "report": rule.report_id,
        "instructions": rule.instructions, "trigger": "after_build", "freq": "daily",
        "send_hour": 8, "send_minute": 0, "weekday": 0, "month_day": 1,
        "timezone": "UTC", "recipient_roles": ["developers"], "cooldown_hours": 2,
        "notification_policy": "transition", "enabled": "on"},
        instance=AlertRule.objects.get(pk=rule.pk), studio=rule.studio)
    assert draft.is_valid()
    run_at(rule, fake_llm, True, 0)
    fake_llm(decide(False))
    evaluator.evaluate(rule, deliver=False)
    rule.refresh_from_db()
    before = (rule.live_state.copy(), rule.last_run_at, rule.last_alerted_at, rule.last_test_at)
    draft.save()
    rule.refresh_from_db()
    assert rule.name == "Draft name"
    assert (rule.live_state, rule.last_run_at, rule.last_alerted_at, rule.last_test_at) == before


def test_requesting_editor_preview_bills_editor_and_simulates_without_live_mutation(
    rule, fake_llm, make_user, grant_studio
):
    from apps.core import roles
    from apps.assistant.models import LlmUsage
    editor = make_user("editor-preview@example.test", org=rule.org)
    grant_studio(editor, rule.studio, roles.DEVELOPER)
    snapshot = evaluator.RuleSnapshot.from_rule(rule)
    fake_llm(decide(True))
    run = evaluator.evaluate_snapshot(snapshot, actor=editor, billing_user=editor,
        execution_mode="test", history_namespace="draft:editor")
    assert run.delivery_disposition == "test" and run.simulated_disposition == "would_notify"
    rule.refresh_from_db()
    assert rule.live_state is None and rule.last_alerted_at is None and rule.last_run_at is None
    assert LlmUsage.user_month_total(rule.org, editor) > 0
    assert LlmUsage.user_month_total(rule.org, rule.created_by) == 0
    assert mail.outbox == []


def test_newer_unknown_observation_prevents_inflight_claim_from_sending(rule, fake_llm, monkeypatch):
    from apps.reports import notify
    transition(rule)
    original = notify.send_alert
    def observe_unknown(saved, run, **kwargs):
        newer = AlertRun(rule=saved, condition="unknown", started_at=run.started_at + timedelta(seconds=1))
        snapshot = evaluator.RuleSnapshot.from_rule(saved)
        assert evaluator.claim_delivery(snapshot, newer, saved.created_by, saved.report) is None
        return original(saved, run, **kwargs)
    monkeypatch.setattr(notify, "send_alert", observe_unknown)
    run = run_at(rule, fake_llm, True, 0)
    assert run.delivered_to == [] and mail.outbox == []
    rule.refresh_from_db()
    assert rule.live_state["condition"] == "met" and rule.live_state["pending"]
    assert rule.live_state["last_result"] == "unknown" and rule.last_alerted_at is None
