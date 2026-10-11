"""Delivery pause preserves evaluations, pending episodes and owner warnings."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.db import connection
from django.utils import timezone

from apps.alerts import evaluator
from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.conftest import decide
from apps.assistant.models import LlmUsage
from apps.core.models import InstanceConfig
from apps.orgs.models import Organization

pytestmark = pytest.mark.django_db


def pause(org, *, paused=True, generation=1):
    Organization.objects.filter(pk=org.pk).update(deliveries_paused=paused, delivery_generation=generation)


@pytest.mark.parametrize('scope', ['org', 'instance'])
def test_paused_alert_evaluates_costs_and_preserves_pending_episode(rule, fake_llm, org, scope):
    rule.notification_policy = AlertRule.POLICY_TRANSITION
    rule.save()
    rule.recipients.add(rule.created_by)
    target = org if scope == 'org' else InstanceConfig.load()
    target.deliveries_paused = True
    target.save(update_fields=['deliveries_paused'])
    fake_llm(decide(True))
    run = evaluator.evaluate(rule)
    rule.refresh_from_db()
    assert run.condition == 'met' and run.delivery_disposition == 'paused'
    assert run.outcome_label == 'Paused for delivery'
    assert run.cost_usd == Decimal('0.0105')
    assert LlmUsage.user_month_total(org, rule.created_by) == Decimal('0.0105')
    assert rule.live_state['pending'] and not rule.live_state.get('claim')
    assert rule.last_alerted_at is None and mail.outbox == []
    target.deliveries_paused = False
    target.delivery_generation = 2
    target.save(update_fields=['deliveries_paused', 'delivery_generation'])
    assert mail.outbox == []
    fake_llm(decide(True))
    next_run = evaluator.evaluate(rule, now=run.started_at + timedelta(hours=1))
    assert next_run.delivery_status == 'accepted' and len(mail.outbox) == 1


def test_preview_pause_simulation_is_pure_and_does_not_claim(rule, fake_llm, org, django_assert_num_queries):
    pause(org)
    snapshot = evaluator.RuleSnapshot.from_rule(rule)
    with django_assert_num_queries(0):
        result = evaluator.simulate_delivery(snapshot, 'met')
    assert result['disposition'] == 'paused'
    fake_llm(decide(True))
    run = evaluator.evaluate(rule, deliver=False)
    assert run.simulated_disposition == 'paused' and run.delivery_disposition == 'test'
    rule.refresh_from_db()
    assert not rule.live_state and mail.outbox == []


@pytest.mark.django_db(transaction=True)
def test_pause_resume_during_provider_stops_old_decision_but_allows_fresh_evaluation(rule, fake_llm, org, monkeypatch):
    from apps.assistant import llm
    rule.recipients.add(rule.created_by)
    fake_llm(decide(True))
    original = llm.stream_turn
    def stream(*args, **kwargs):
        assert not connection.in_atomic_block, 'A provider call must not hold a database transaction.'
        pause(org)
        pause(org, paused=False, generation=2)
        yield from original(*args, **kwargs)
    monkeypatch.setattr(llm, 'stream_turn', stream)
    run = evaluator.evaluate(rule)
    assert run.condition == 'met' and run.delivery_disposition == 'paused' and mail.outbox == []
    rule.refresh_from_db()
    assert rule.live_state['pending'] and rule.last_alerted_at is None
    monkeypatch.setattr(llm, 'stream_turn', original)
    fake_llm(decide(True))
    fresh = evaluator.evaluate(rule, now=run.started_at + timedelta(hours=1))
    assert fresh.delivery_status == 'accepted' and len(mail.outbox) == 1


@pytest.mark.parametrize('change', ['pause', 'claim_replaced', 'claim_cleared'])
def test_final_alert_boundary_checks_pause_and_exact_claim_token(rule, fake_llm, org, monkeypatch, change):
    from apps.reports import notify
    rule.recipients.add(rule.created_by)
    fake_llm(decide(True))
    original = notify.render_to_string
    def render(template, ctx):
        body = original(template, ctx)
        if template == 'email/alert.html':
            if change == 'pause':
                pause(org)
            else:
                current = AlertRule.objects.get(pk=rule.pk)
                state = current.live_state
                state['claim'] = dict(state['claim'], token='replacement') if change == 'claim_replaced' else None
                AlertRule.objects.filter(pk=rule.pk).update(live_state=state)
        return body
    monkeypatch.setattr(notify, 'render_to_string', render)
    run = evaluator.evaluate(rule)
    rule.refresh_from_db()
    assert mail.outbox == [] and rule.last_alerted_at is None
    if change == 'pause':
        assert run.delivery_disposition == 'paused' and rule.live_state['pending']
        assert not rule.live_state.get('claim')
    elif change == 'claim_replaced':
        assert rule.live_state['claim']['token'] == 'replacement'
        assert 'claim changed' in run.delivery_reason
    else:
        assert not rule.live_state.get('claim')


@pytest.mark.parametrize('previously_accepted', [False, True])
def test_paused_owner_budget_warning_retains_acceptance_memory(rule, fake_llm, org, monkeypatch, previously_accepted):
    from apps.assistant import budget
    monkeypatch.setattr(budget, 'precheck', lambda *args: (False, 'Synthetic exhausted budget.'))
    first_at = timezone.now()
    if previously_accepted:
        first = evaluator.evaluate(rule, now=first_at, evaluation_key='before-pause')
        assert first.budget_warning_sent and len(mail.outbox) == 1
    pause(org)
    paused = evaluator.evaluate(rule, now=first_at + timedelta(hours=1), evaluation_key='paused')
    assert paused.delivery_disposition == 'paused'
    assert paused.budget_warning_sent == previously_accepted
    assert len(mail.outbox) == int(previously_accepted)
    pause(org, paused=False, generation=2)
    resumed = evaluator.evaluate(rule, now=first_at + timedelta(hours=2), evaluation_key='resumed')
    assert resumed.budget_warning_sent and len(mail.outbox) == 1


def test_final_alert_boundary_reads_recipient_activation_from_database(rule, fake_llm, org, make_user, grant_studio, monkeypatch):
    from apps.core import roles
    from apps.reports import notify
    recipient = make_user('recipient@demo.example', org=org)
    grant_studio(recipient, rule.studio, roles.VIEWER)
    rule.recipients.add(recipient)
    fake_llm(decide(True))
    original = notify.render_to_string
    def render(template, ctx):
        body = original(template, ctx)
        if template == 'email/alert.html':
            type(recipient).objects.filter(pk=recipient.pk).update(is_active=False)
        return body
    monkeypatch.setattr(notify, 'render_to_string', render)
    run = evaluator.evaluate(rule)
    assert mail.outbox == [] and run.delivered_to == []
    rule.refresh_from_db()
    assert rule.last_alerted_at is None


def test_claim_replaced_immediately_after_lock_release_is_not_adopted(rule, fake_llm, monkeypatch):
    rule.recipients.add(rule.created_by)
    fake_llm(decide(True))
    original = evaluator._current_rule
    changed = False
    def current(snapshot, owner):
        nonlocal changed
        saved, reason = original(snapshot, owner)
        if saved and not changed and (saved.live_state or {}).get('claim'):
            changed = True
            state = dict(saved.live_state)
            state['claim'] = dict(state['claim'], token='replacement')
            AlertRule.objects.filter(pk=saved.pk).update(live_state=state)
            saved.live_state = state
        return saved, reason
    monkeypatch.setattr(evaluator, '_current_rule', current)
    run = evaluator.evaluate(rule)
    rule.refresh_from_db()
    assert changed and mail.outbox == [] and run.delivery_disposition == 'stale'
    assert rule.live_state['claim']['token'] == 'replacement' and rule.last_alerted_at is None
