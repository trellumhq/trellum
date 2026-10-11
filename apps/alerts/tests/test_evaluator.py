"""Layer 1 of the alert design (vault portal-agent plan §11): the machinery
around the model, exercised with a scripted ``decide`` call."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.alerts import evaluator
from apps.alerts.models import AlertRule, AlertRun
from apps.alerts.tests.conftest import decide
from apps.assistant.models import LlmUsage
from apps.assistant.tests.test_message_sse import FakeResponse, TextBlock, ToolUseBlock
from apps.core import roles
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


def _system_text(call) -> str:
    return "\n".join(b["text"] for b in call["system"])


class TestDecision:
    def test_unsaved_snapshot_preview_is_immutable_billed_and_not_persisted(self, rule, fake_llm, org):
        from dataclasses import FrozenInstanceError

        rule.pk = None
        snapshot = evaluator.RuleSnapshot.from_rule(rule)
        with pytest.raises(FrozenInstanceError):
            snapshot.name = "Changed"
        fake_llm(decide(True))
        run = evaluator.evaluate_snapshot(
            snapshot, actor=rule.created_by, billing_user=rule.created_by,
            execution_mode=AlertRun.MODE_TEST, history_namespace="preview",
        )
        assert run.pk is None and run.rule_id is None
        assert run.decision == AlertRun.DECISION_ALERT and mail.outbox == []
        assert AlertRule.objects.count() == 1 and AlertRun.objects.count() == 0
        assert LlmUsage.user_month_total(org, rule.created_by) == Decimal("0.0105")

    @pytest.mark.parametrize("mode,namespace", [("live", "preview"), ("test", "live")])
    def test_snapshot_rejects_mixed_execution_history(self, rule, mode, namespace):
        with pytest.raises(ValueError):
            evaluator.evaluate_snapshot(
                evaluator.RuleSnapshot.from_rule(rule), actor=rule.created_by,
                billing_user=rule.created_by, execution_mode=mode, history_namespace=namespace,
            )
        assert AlertRun.objects.count() == 0 and mail.outbox == []

    @pytest.mark.parametrize("results,status", [
        ([0, 0], AlertRun.DELIVERY_FAILED),
        ([1, RuntimeError("transport rejected")], AlertRun.DELIVERY_PARTIAL),
        ([RuntimeError("transport rejected"), 1], AlertRun.DELIVERY_PARTIAL),
    ])
    def test_failed_and_partial_transport_results(self, rule, fake_llm, make_user, grant_studio,
                                                org, studio_tree, monkeypatch, results, status):
        from apps.reports import notify

        for email in ("first@demo.example", "second@demo.example"):
            user = make_user(email, org=org)
            grant_studio(user, studio_tree, roles.VIEWER)
            rule.recipients.add(user)
        replies = iter(results)
        def send(*args, **kwargs):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(notify, "_send_simple", send)
        fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        run.refresh_from_db()
        rule.refresh_from_db()
        assert run.delivery_status == status
        assert len(run.delivered_to) == (1 if status == AlertRun.DELIVERY_PARTIAL else 0)
        assert len(run.failed_to) == (1 if status == AlertRun.DELIVERY_PARTIAL else 2)
        assert bool(rule.last_alerted_at) == bool(run.delivered_to)

    def test_recipient_resolution_error_is_visible_without_cooldown(self, rule, fake_llm, monkeypatch):
        from apps.reports import notify

        def fail(*args):
            raise RuntimeError("private resolver failure")
        monkeypatch.setattr(notify, "resolve_recipients", fail)
        fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        assert run.delivery_status == AlertRun.DELIVERY_ERROR
        assert run.error == "Could not resolve eligible recipients."
        rule.refresh_from_db()
        assert rule.last_alerted_at is None

    def test_interrupted_provider_records_unknown_cost(self, rule, monkeypatch):
        from apps.assistant import llm

        def interrupted(*args, **kwargs):
            assert kwargs["purpose"] == "alert"
            yield {"type": "text_delta", "text": "Synthetic partial answer"}
            raise RuntimeError("private provider payload")

        monkeypatch.setattr(llm, "_adapter", lambda provider: interrupted)
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_ERROR and run.cost_usd is None
        assert "private provider payload" not in run.error

    def test_budget_is_checked_between_provider_calls(self, rule, assistant_config, monkeypatch):
        from apps.assistant import llm

        assistant_config.monthly_budget_usd = Decimal("0.005")
        assistant_config.save()
        calls = []
        def adapter(*args, **kwargs):
            calls.append(1)
            assert len(calls) == 1, "the exhausted budget must stop another provider request"
            yield {"type": "response", "blocks": [{"type": "tool_use", "id": "tool-1", "name": "list_reports", "input": {}}],
                   "stop_reason": "tool_use", "usage": {"input_tokens": 1000, "output_tokens": 500,
                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}, "provider_data": None}

        monkeypatch.setattr(llm, "_adapter", lambda provider: adapter)
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_ERROR and "budget cap reached" in run.error
        assert len(calls) == 1 and run.cost_usd == Decimal("0.0105")

    def test_every_rule_uses_a_report_bound_owner_toolbox(
        self, rule, fake_llm, monkeypatch
    ):
        from apps.assistant import tools

        real_toolbox = tools.AssistantToolbox
        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return real_toolbox(*args, **kwargs)

        monkeypatch.setattr(tools, "AssistantToolbox", capture)
        fake_llm(decide(False))

        evaluator.evaluate(rule)

        assert captured["actor"] == rule.created_by
        assert captured["scope"] == "report"
        assert captured["report"] == rule.report

    def test_alert_delivers_to_resolved_recipients(
        self, rule, fake_llm, make_user, grant_studio, make_group, attach_group, org, studio_tree
    ):
        picked = make_user("picked@demo.example", org=org)
        grant_studio(picked, studio_tree, roles.VIEWER)
        dev = make_user("dev@demo.example", org=org)
        grant_studio(dev, studio_tree, roles.DEVELOPER)
        insider = make_user("insider@demo.example", org=org)
        grant_studio(insider, studio_tree, roles.VIEWER)
        outsider = make_user("outsider@demo.example", org=org)  # no studio access
        group = make_group("Finance")
        attach_group(insider, group)
        attach_group(outsider, group)
        rule.recipients.add(picked)
        rule.recipient_roles = ["developers"]
        rule.save()
        rule.recipient_groups.add(group)
        fake_llm(decide(True))

        run = evaluator.evaluate(rule)

        assert run.status == AlertRun.STATUS_OK and run.decision == AlertRun.DECISION_ALERT
        # developers: dev + the owner (a developer); insider through the group;
        # picked directly; outsider never, they cannot see the studio.
        expected = sorted([picked.email, dev.email, insider.email, rule.created_by.email])
        assert run.delivered_to == expected
        assert sorted(m.to[0] for m in mail.outbox) == expected
        msg = mail.outbox[0]
        assert "Revenue fell" in msg.subject
        assert "Revenue is down 30% on the week." in msg.body
        assert "gross_revenue 2026-09-07: 700 vs 1000" in msg.body
        assert f"/s/{org.slug}/{studio_tree.slug}/r/{rule.report.slug}/" in msg.body
        assert f"/s/{org.slug}/{studio_tree.slug}/assistant/from-alert/{run.pk}" in msg.body
        assert run.evidence["cited"] == ["gross_revenue 2026-09-07: 700 vs 1000 a week earlier"]
        assert run.evidence["snapshot"]["built_at"] == "2026-09-08T04:00:00+00:00"
        rule.refresh_from_db()
        assert rule.last_alerted_at is not None and rule.last_run_at is not None

    def test_quiet_records_and_sends_nothing(self, rule, fake_llm, org):
        fake_llm(decide(False, title="Nothing unusual", message="Flat week."))
        run = evaluator.evaluate(rule)
        assert run.decision == AlertRun.DECISION_QUIET and run.title == "Nothing unusual"
        assert mail.outbox == [] and run.delivered_to == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None
        # Billed to the owner: FakeUsage is 1000 in / 500 out at list price.
        assert run.cost_usd == Decimal("0.0105")
        assert LlmUsage.user_month_total(org, rule.created_by) == Decimal("0.0105")

    def test_unknown_cost_is_persisted_as_unavailable_without_ledger_entry(
        self, rule, fake_llm, org, assistant_config
    ):
        from apps.orgs.models import OrgAssistantConfig

        OrgAssistantConfig.objects.filter(org=org).update(model="claude-future-9")
        fake_llm(decide(False))
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_OK
        assert run.cost_usd is None
        assert LlmUsage.user_month_total(org, rule.created_by) == Decimal("0")

    def test_dry_run_never_delivers_or_touches_cooldown(self, rule, fake_llm):
        fake_llm(decide(True))
        run = evaluator.evaluate(rule, deliver=False)
        assert run.decision == AlertRun.DECISION_ALERT
        assert mail.outbox == [] and run.delivered_to == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None
        assert run.execution_mode == AlertRun.MODE_TEST
        assert rule.last_test_at is not None and rule.last_run_at is None

    def test_model_that_stops_without_deciding_is_forced(self, rule, fake_llm):
        calls = fake_llm(
            FakeResponse([TextBlock("Everything looks normal to me.")]),
            decide(False, title="Normal"),
        )
        run = evaluator.evaluate(rule)
        assert run.decision == AlertRun.DECISION_QUIET
        assert "tool_choice" not in calls[0]
        assert calls[1]["tool_choice"] == {"type": "tool", "name": "decide"}

    def test_decide_is_offered_only_here(self, rule, fake_llm):
        calls = fake_llm(decide(False))
        evaluator.evaluate(rule)
        names = [t["name"] for t in calls[0]["tools"]]
        assert names[-1] == "decide" and "query_report_data" in names
        from apps.assistant.tools import AssistantToolbox

        assert "decide" not in [t["name"] for t in AssistantToolbox(rule.studio).schemas]

    def test_tool_error_becomes_error_status(self, rule, fake_llm, monkeypatch):
        from apps.assistant.tools import AssistantToolbox

        def boom(self, name, args):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(AssistantToolbox, "execute", boom)
        fake_llm(
            FakeResponse([ToolUseBlock("tu_1", "get_report_details", {"slug": rule.report.slug})],
                         stop_reason="tool_use"),
        )
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_ERROR
        assert "disk on fire" in run.error
        assert AlertRun.objects.filter(rule=rule).count() == 1


class TestPreWork:
    def test_budget_blocked_test_does_not_mail_or_suppress_live_warning(
        self, rule, fake_llm, assistant_config, org
    ):
        assistant_config.per_user_budget_usd = Decimal("1")
        assistant_config.save()
        LlmUsage.add_cost(org, rule.created_by, 1.5)
        calls = fake_llm(decide(True))
        test = evaluator.evaluate(rule, deliver=False)
        assert test.status == AlertRun.STATUS_SKIPPED_BUDGET
        assert mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None
        evaluator.evaluate(rule)
        evaluator.evaluate(rule)
        assert len(mail.outbox) == 1 and calls == []

    def test_dry_run_is_absent_from_identical_live_evaluation_history(self, rule, fake_llm):
        rule.recipients.add(rule.created_by)
        calls = fake_llm(decide(True, title="Test-only warning"), decide(True))
        test = evaluator.evaluate(rule, deliver=False)
        assert test.delivered_to == [] and mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None
        live = evaluator.evaluate(rule)
        text = _system_text(calls[1]).split("# ALERT EVALUATION MODE", 1)[1]
        assert "Test-only warning" not in text
        assert "(none yet)" in text
        assert live.decision == AlertRun.DECISION_ALERT
        assert live.delivery_status == AlertRun.DELIVERY_ACCEPTED
        assert live.delivered_to == [rule.created_by.email]
        assert len(mail.outbox) == 1 and mail.outbox[0].to == [rule.created_by.email]
        rule.refresh_from_db()
        assert rule.last_alerted_at is not None

    def test_no_recipients_does_not_start_cooldown(self, rule, fake_llm):
        fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        assert run.delivered_to == []
        assert run.delivery_status == AlertRun.DELIVERY_EMPTY
        rule.refresh_from_db()
        assert rule.last_alerted_at is None

    def test_legacy_alert_and_failed_delivery_do_not_claim_prior_notification(self, rule, fake_llm):
        AlertRun.objects.create(rule=rule, decision="alert", title="Legacy uncertain", delivered_to=["old@test.example"])
        AlertRun.objects.create(rule=rule, decision="alert", title="Failed send", execution_mode="live",
                                history_namespace="live", delivery_status="failed")
        calls = fake_llm(decide(False))
        evaluator.evaluate(rule)
        text = _system_text(calls[0]).split("# ALERT EVALUATION MODE", 1)[1]
        assert "Legacy uncertain" not in text and "Failed send" not in text

    @pytest.mark.parametrize("result", [0, RuntimeError("warning rejected")])
    def test_rejected_budget_warning_does_not_suppress_next_attempt(
        self, rule, fake_llm, assistant_config, org, monkeypatch, result
    ):
        assistant_config.per_user_budget_usd = Decimal("1")
        assistant_config.save()
        LlmUsage.add_cost(org, rule.created_by, 1.5)
        calls = []
        def send(**kwargs):
            calls.append(1)
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(evaluator, "send_mail", send)
        first = evaluator.evaluate(rule)
        second = evaluator.evaluate(rule)
        assert first.status == second.status == AlertRun.STATUS_SKIPPED_BUDGET
        assert not first.budget_warning_sent and not second.budget_warning_sent
        assert len(calls) == 2

    def test_test_between_exhausted_live_ticks_preserves_existing_warning_state(
        self, rule, assistant_config, org
    ):
        assistant_config.per_user_budget_usd = Decimal("1")
        assistant_config.save()
        LlmUsage.add_cost(org, rule.created_by, 1.5)
        evaluator.evaluate(rule)
        test = evaluator.evaluate(rule, deliver=False)
        evaluator.evaluate(rule)
        assert len(mail.outbox) == 1 and not test.budget_warning_sent

    def test_inactive_owner_stops_before_report_storage_or_model(
        self, rule, fake_llm, monkeypatch
    ):
        from apps.core import storage

        def forbidden(*_args, **_kwargs):
            raise AssertionError("report storage must not be read")

        monkeypatch.setattr(storage, "read_meta", forbidden)
        rule.created_by.is_active = False
        rule.created_by.save(update_fields=["is_active"])
        calls = fake_llm(decide(True))

        run = evaluator.evaluate(rule)

        assert run.status == AlertRun.STATUS_ERROR
        assert "no longer access" in run.error
        assert calls == [] and mail.outbox == []

    def test_owner_access_is_rechecked_before_delivery(
        self, rule, fake_llm, monkeypatch
    ):
        checks = iter((True, False))
        monkeypatch.setattr(evaluator, "_owner_can_evaluate", lambda *_: next(checks))
        fake_llm(decide(True))

        run = evaluator.evaluate(rule)

        assert run.status == AlertRun.STATUS_ERROR
        assert run.delivered_to == [] and mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None

    @pytest.mark.parametrize("changed", ["report", "name", "instructions"])
    def test_changed_rule_cannot_deliver_an_obsolete_snapshot(self, rule, fake_llm, monkeypatch, changed):
        from apps.reports.models import Report

        other = Report.objects.create(studio=rule.studio, slug="other", name="Other")
        rule.recipients.add(rule.created_by)
        checks = []
        def access(*args):
            checks.append(1)
            if len(checks) == 2:
                replacement = other if changed == "report" else "Edited during evaluation"
                AlertRule.objects.filter(pk=rule.pk).update(**{changed: replacement})
            return True
        monkeypatch.setattr(evaluator, "_owner_can_evaluate", access)
        fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_ERROR
        assert "changed during evaluation" in run.error
        assert run.delivered_to == [] and mail.outbox == []
        rule.refresh_from_db()
        assert rule.last_alerted_at is None

    def test_cooldown_skips_without_calling_the_model(self, rule, fake_llm):
        rule.last_alerted_at = timezone.now() - timedelta(hours=1)
        rule.save()
        calls = fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_SKIPPED_COOLDOWN and run.decision == ""
        assert calls == [] and mail.outbox == []

    def test_exhausted_budget_skips_and_mails_the_owner_once(
        self, rule, fake_llm, assistant_config, org
    ):
        assistant_config.per_user_budget_usd = Decimal("1.00")
        assistant_config.save()
        LlmUsage.add_cost(org, rule.created_by, 1.5)
        calls = fake_llm(decide(True))
        first = evaluator.evaluate(rule)
        second = evaluator.evaluate(rule)
        assert first.status == second.status == AlertRun.STATUS_SKIPPED_BUDGET
        assert "Budget cap reached" in first.error
        assert calls == []
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [rule.created_by.email]
        assert rule.name in mail.outbox[0].subject

    def test_failed_last_build_is_not_evaluated(self, rule, fake_llm, write_meta):
        write_meta(rule.report.slug, last_status="error", last_error="boom")
        calls = fake_llm(decide(True))
        run = evaluator.evaluate(rule)
        assert run.status == AlertRun.STATUS_OK and run.decision == AlertRun.DECISION_QUIET
        assert "status: error" in run.message
        assert calls == [] and mail.outbox == []

    def test_prompt_carries_previous_snapshot_and_last_five_decisions(self, rule, fake_llm):
        for i in range(6):
            AlertRun.objects.create(
                rule=rule, status=AlertRun.STATUS_OK, decision=AlertRun.DECISION_QUIET,
                execution_mode=AlertRun.MODE_LIVE, history_namespace="live",
                title=f"decision {i}", started_at=timezone.now() - timedelta(days=6 - i),
                evidence={"cited": [f"dau {i}: 100"], "snapshot": {"built_at": f"build-{i}"}},
            )
        AlertRun.objects.create(rule=rule, status=AlertRun.STATUS_ERROR, error="x", title="err")
        calls = fake_llm(decide(False))
        evaluator.evaluate(rule)
        text = _system_text(calls[0])
        assert "dau 5: 100" in text and "build-5" in text  # the previous run's snapshot
        for i in range(1, 6):
            assert f"quiet -- decision {i}" in text
        assert "decision 0" not in text and "err" not in text.split("Recent decisions")[1]
        assert "Instructions: Tell me if revenue drops." in text
        assert "Validation: 0 fail / 1 warn" in text

    def test_empty_instructions_ask_for_anything_off(self, rule, fake_llm):
        rule.instructions = ""
        rule.save()
        calls = fake_llm(decide(False))
        evaluator.evaluate(rule)
        text = _system_text(calls[0])
        assert "Instructions: Tell me if anything looks off." in text
        # The bar (#153): a weekend record on a rising trend is not news.
        assert "the reader would act on" in text and "on an existing trend, gradual growth" in text


class TestTriggers:
    @pytest.fixture
    def inline_threads(self, monkeypatch):
        """The evaluator's daemon thread runs synchronously; only the name
        bound inside apps.alerts.evaluator is replaced (the executor keeps
        its real threads)."""
        class _Now:
            def __init__(self, target=None, args=(), daemon=None):  # noqa: ARG002
                self._t, self._a = target, args

            def start(self):
                self._t(*self._a)

        class _FakeThreading:
            Thread = _Now

        monkeypatch.setattr(evaluator, "threading", _FakeThreading)

    # transaction=True: the real targets close the thread's DB connection on
    # the way out, which a test-wide atomic block cannot survive.
    @pytest.mark.django_db(transaction=True)
    def test_after_build_evaluates_enabled_rules_on_success_only(
        self, rule, fake_llm, inline_threads, org, studio_tree, owner, fake_popen, queued_run,
        monkeypatch,
    ):
        from apps.runner.executor import Executor

        report = rule.report
        AlertRule.objects.create(org=org, studio=studio_tree, report=report, name="off",
                                 created_by=owner, enabled=False)
        AlertRule.objects.create(org=org, studio=studio_tree, report=report, name="cron",
                                 created_by=owner, trigger=AlertRule.TRIGGER_SCHEDULE)
        second = AlertRule.objects.create(org=org, studio=studio_tree, report=report,
                                          name="second", created_by=owner)
        fake_llm(decide(False), decide(False))
        # A failed build's source re-check runs on a thread of its own; not this test's concern.
        monkeypatch.setattr(Executor, "_attribute_failure", lambda *a, **kw: None)

        ex = Executor("w1")
        ex.start_run(queued_run)
        fake_popen["procs"][-1].finish(0)
        ex.tick()
        assert set(AlertRun.objects.values_list("rule_id", flat=True)) == {rule.pk, second.pk}

        failed = Run.objects.create(report=report, studio=studio_tree, slug=report.slug,
                                    status=Run.STARTING, requested_by=owner)
        ex.start_run(failed)
        fake_popen["procs"][-1].finish(1)
        ex.tick()
        assert AlertRun.objects.count() == 2

    def test_scheduled_rules_get_alert_jobs(self, rule):
        from apps.runner.management.commands.runworker import _SchedulerManager

        rule.trigger = AlertRule.TRIGGER_SCHEDULE
        rule.freq = AlertRule.FREQ_HOURLY
        rule.send_minute = 15
        rule.save()
        mgr = _SchedulerManager()
        mgr.start()
        try:
            job = mgr._scheduler.get_job(f"alert-{rule.pk}")
            assert job is not None
            assert str(job.trigger.fields[job.trigger.FIELD_NAMES.index("minute")]) == "15"
            assert str(job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")]) == "*"
            rule.enabled = False
            rule.save(update_fields=["enabled"])
            mgr.refresh()
            assert mgr._scheduler.get_job(f"alert-{rule.pk}") is None
        finally:
            mgr.shutdown()

    @pytest.mark.django_db(transaction=True)
    def test_scheduled_job_target_evaluates_the_rule(self, rule, fake_llm):
        from apps.runner.management.commands.runworker import _evaluate_alert

        fake_llm(decide(False))
        _evaluate_alert(rule.pk)
        assert AlertRun.objects.filter(rule=rule, decision=AlertRun.DECISION_QUIET).exists()
        rule.enabled = False
        rule.save()
        _evaluate_alert(rule.pk)
        assert AlertRun.objects.count() == 1
