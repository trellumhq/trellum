"""The alert evaluator: deterministic around, agent in the middle.

Triggered by the executor after a successful build (:func:`evaluate_after_build`)
or by the coordinator's ``alert-<pk>`` jobs (``runworker._evaluate_alert``).

1. Pre-work, no model: trigger claim, the report's ``_meta.json`` (a failed last
   build is not evaluated -- that failure has its own mail), the owner's
   budget, data-source blockers, the previous evaluation's evidence snapshot
   and the last few decisions.
2. One assistant turn with the studio's read tools plus ``decide``, a tool
   that exists only here. The turn ends the moment ``decide`` is called; a
   model that stops without deciding, or hits the turn cap, is asked once
   more with ``decide`` forced.
3. Post-work, no model: record condition/episode, claim policy-eligible
   delivery under a short lock, then send outside it. Cooldown never skips
   condition evaluation. Tests simulate policy without changing live state.

``evaluate(rule, deliver=False)`` is the dry run the Alerts page's Test now
and the scenario suite use: it never mails and never touches
``last_alerted_at``. ``now=`` pins the evaluation's calendar -- the prompt's
"today", the build date in the status line, the run's timestamps -- for the
scenario suite, whose builds are pinned with ``FW_NOW``; production leaves it
at the real time. Design: vault ``Architecture/design/portal-agent/00-plan`` §5.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import close_old_connections, connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.alerts.models import AlertEvaluationClaim, AlertRule, AlertRun

logger = logging.getLogger(__name__)

#: Tool steps one evaluation may take before ``decide`` is forced. Bounds the
#: cost of a single evaluation as much as it bounds the wall clock.
ALERT_MAX_TURNS = 10
#: Prior evidence helps interpret comparisons, never notification eligibility.
HISTORY = 5

DECIDE_SCHEMA = {
    "name": "decide",
    "description": (
        "Record your decision and end the evaluation. Call it exactly once, "
        "after looking at the data. Judge only whether the requested condition "
        "is met. Missing or incomplete evidence means unknown. Never decide whether to send mail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "condition": {"type": "string", "enum": ["met", "not_met", "unknown"]},
            "interpretation": {
                "type": "object", "properties": {
                    "window": {"type": "string"}, "timezone": {"type": "string"},
                    "scope": {"type": "string"}, "freshness": {"type": "string"},
                }, "required": ["window", "timezone", "scope", "freshness"],
            },
            "title": {"type": "string", "description": "One line; the headline, or a short summary when quiet"},
            "message": {
                "type": "string",
                "description": (
                    "A few plain-language sentences with the numbers you used and "
                    "what you compared them against"
                ),
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The figures you relied on, one per entry: metric, value, date, "
                    "and what it was compared with"
                ),
            },
        },
        "required": ["condition", "interpretation", "title", "message", "evidence"],
    },
}

EVALUATOR_BLOCK = """\
====================================================================
# ALERT EVALUATION MODE
====================================================================

This is not a conversation. You are evaluating an alert rule against one
report on behalf of the people who wrote it. Use the tools to look at the
data the instructions point to (the DATA CATALOG above says which datasets
and dates exist), then call `decide` exactly once -- that ends the
evaluation. Never answer in prose instead of calling it.

Judge the requested condition only: met, not_met, or unknown. Previous
notifications and cooldown never affect condition truth. Repeated conditions
are still met. Missing, stale or incomplete data is unknown, never healthy.
For blank instructions, look for something the reader would act on; ordinary
seasonality, records on an existing trend, gradual growth and noise alone
do not satisfy that condition. Every figure must come from a tool result.
Persist interpretation: exact evaluated time window, timezone, dataset scope
and freshness/coverage. Use rule timezone {timezone} unless the instructions
explicitly specify another. Rolling last 60 minutes differs from the last
completed clock hour. A net drain over 48 hours differs from any draining
hour in those 48 hours. Strict >1000 excludes exactly 1000. Preserve currency;
never compare different currencies without evidenced conversion. If the
tools cannot establish requested coverage or freshness, choose unknown.

## Rule
Name: {name}
Report: {report_name} (slug `{slug}`)
Instructions: {instructions}

## Report status (already checked; do not re-verify)
{status}

## Previous evaluation's evidence (compare against this)
{previous}

## Recent decisions, newest first
{history}
"""

USER_MESSAGE = "Evaluate this rule now: look at the data, then call decide."
FORCE_MESSAGE = "Call decide now with your decision."


@dataclass(frozen=True)
class RuleSnapshot:
    rule_id: int | None
    org_id: int
    studio_id: int
    report_id: int
    name: str
    instructions: str
    cooldown_hours: int
    last_alerted_at: datetime | None
    revision: str = ""
    timezone: str = "UTC"
    notification_policy: str = AlertRule.POLICY_REPEAT
    live_state_json: str = "{}"
    enabled: bool = True

    @classmethod
    def from_rule(cls, rule: AlertRule) -> RuleSnapshot:
        """Validate the same rule fields for saved rules and unsaved previews."""
        rule.full_clean()
        return cls(rule.pk, rule.org_id, rule.studio_id, rule.report_id, rule.name,
                   rule.instructions, rule.cooldown_hours, rule.last_alerted_at,
                   rule.revision, rule.timezone, rule.notification_policy or AlertRule.POLICY_REPEAT,
                   json.dumps(rule.live_state or {}), rule.enabled)


def evaluate(rule: AlertRule, *, deliver: bool = True, now=None, history_namespace=None,
             evaluation_key: str | None = None) -> AlertRun:
    """Evaluate a saved rule; tests have their own durable provenance."""
    try:
        snapshot = RuleSnapshot.from_rule(rule)
    except ValidationError as exc:
        run = AlertRun(rule=rule, started_at=now or timezone.now(), finished_at=now or timezone.now(),
                       execution_mode=AlertRun.MODE_LIVE if deliver else AlertRun.MODE_TEST,
                       history_namespace="live" if deliver else "test", status=AlertRun.STATUS_ERROR,
                       delivery_status=AlertRun.DELIVERY_NONE, error=f"Invalid rule: {exc}"[:2000])
        if "report" in exc.message_dict or "studio" in exc.message_dict:
            run.error = "The rule's organization, studio and report do not match."
        run.save()
        timestamp = "last_run_at" if deliver else "last_test_at"
        AlertRule.objects.filter(pk=rule.pk).update(**{timestamp: run.finished_at})
        return run
    return evaluate_snapshot(
        snapshot, actor=rule.created_by, billing_user=rule.created_by,
        execution_mode=AlertRun.MODE_LIVE if deliver else AlertRun.MODE_TEST,
        history_namespace=history_namespace or ("live" if deliver else "test"), now=now,
        evaluation_key=evaluation_key,
    )


def evaluate_snapshot(snapshot: RuleSnapshot, *, actor, billing_user,
                      execution_mode: str, history_namespace: str, now=None,
                      evaluation_key: str | None = None) -> AlertRun:
    """Internal seam: an unsaved test snapshot returns an in-memory run.

    The actor is also the payer: the provider checks that actor's budget
    between calls. Production always reads the live namespace; scenario
    replay explicitly chooses a separate test namespace.
    """
    if execution_mode not in (AlertRun.MODE_LIVE, AlertRun.MODE_TEST):
        raise ValueError("Choose live or test execution.")
    if not history_namespace or len(history_namespace) > 100 or history_namespace == "legacy":
        raise ValueError("Choose an explicit history namespace.")
    if execution_mode == AlertRun.MODE_LIVE and (history_namespace != "live" or snapshot.rule_id is None):
        raise ValueError("Live evaluation requires a saved rule and live history.")
    if execution_mode == AlertRun.MODE_TEST and history_namespace == "live":
        raise ValueError("Test results cannot enter live history.")
    run = AlertRun(rule_id=snapshot.rule_id, started_at=now or timezone.now(),
                   execution_mode=execution_mode, history_namespace=history_namespace,
                   status=AlertRun.STATUS_RUNNING,
                   delivery_status=AlertRun.DELIVERY_NONE, rule_revision=snapshot.revision,
                   delivery_disposition="not_evaluated", delivery_reason="Evaluation has not completed.")
    run.evaluation_key = evaluation_key
    claim = None
    if execution_mode == AlertRun.MODE_LIVE and evaluation_key is not None:
        if not evaluation_key or len(evaluation_key) > 200:
            raise ValueError("Choose an evaluation key of 1 to 200 characters.")
        # Only this short transaction owns a trigger; provider calls happen after it.
        with transaction.atomic():
            claim, created = AlertEvaluationClaim.objects.get_or_create(
                rule_id=snapshot.rule_id, revision=snapshot.revision, key=evaluation_key)
            if not created:
                if claim.run_id:
                    return AlertRun.objects.get(pk=claim.run_id)
                run.delivery_disposition = "duplicate"
                run.delivery_reason = "This trigger is already claimed; its outcome may be unconfirmed."
                return run
            run.save()
            claim.run = run
            claim.save(update_fields=["run"])
    try:
        _evaluate(snapshot, run, actor, billing_user, now)
    except Exception as exc:  # noqa: BLE001 - every path records a run
        logger.exception("alerts: evaluating rule %s failed", snapshot.rule_id)
        run.status = AlertRun.STATUS_ERROR
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        if run.delivery_disposition == "claimed":
            run.delivery_disposition, run.delivery_reason = "unconfirmed", "Delivery was interrupted; transport acceptance requires review."
    run.finished_at = now or timezone.now()
    if run.status == AlertRun.STATUS_RUNNING:
        run.status = AlertRun.STATUS_OK
    if snapshot.rule_id is not None:
        # Deletion during external work must not recreate a deleted rule/run.
        if not AlertRule.objects.filter(pk=snapshot.rule_id).exists():
            run.pk = None
            run.delivery_disposition = "stale"
            run.delivery_reason = "The rule was deleted during evaluation."
            return run
        run.save()
        timestamp = "last_test_at" if execution_mode == AlertRun.MODE_TEST else "last_run_at"
        AlertRule.objects.filter(pk=snapshot.rule_id).update(**{timestamp: run.finished_at})
    return run


def _evaluate(rule: RuleSnapshot, run: AlertRun, actor, billing_user, now) -> None:
    from apps.assistant import budget, llm
    from apps.assistant.system_prompt import build_system_prompt
    from apps.assistant.tools import AssistantToolbox
    from apps.core import storage
    from apps.datasources.status import report_blockers

    from apps.reports.models import Report

    report = Report.objects.select_related("studio__org").get(pk=rule.report_id)
    studio, org = report.studio, report.studio.org
    deliver = run.execution_mode == AlertRun.MODE_LIVE
    if report.kind == "analysis":
        run.status, run.error = AlertRun.STATUS_ERROR, "Analyses do not support alert rules."
        return
    if rule.studio_id != report.studio_id or rule.org_id != report.studio.org_id:
        run.status = AlertRun.STATUS_ERROR
        run.error = "The rule's organization, studio and report do not match."
        return
    owner = billing_user
    if owner is None:
        # ponytail: an erased owner parks the rule with a visible error rather
        # than billing the org anonymously; the Alerts page can reassign it.
        run.status, run.error = AlertRun.STATUS_ERROR, "This rule has no owner to bill; set created_by."
        return
    if actor is None or actor.pk != owner.pk or not _owner_can_evaluate(actor, report):
        run.status = AlertRun.STATUS_ERROR
        run.error = "The rule owner can no longer access this report."
        return

    meta = storage.read_meta(studio, report.slug) or {}
    if meta.get("last_status") != "success":
        run.decision = AlertRun.DECISION_QUIET
        run.title = "Not evaluated"
        run.message = (
            f"The report's last build did not succeed (status: "
            f"{meta.get('last_status') or 'not_run'}), so there is nothing new to look at. "
            "Build failures have their own notification."
        )
        return

    ok, why = llm.is_available(org, purpose="alert")
    if not ok:
        run.status, run.error = AlertRun.STATUS_ERROR, why
        return
    allowed, why = budget.precheck(org, owner)
    if not allowed:
        run.status, run.error = AlertRun.STATUS_SKIPPED_BUDGET, why
        if deliver:
            saved_rule = AlertRule.objects.get(pk=rule.rule_id)
            run.budget_warning_sent = _mail_owner_once(saved_rule, why)
        return

    namespace = "live" if run.history_namespace == "test" else run.history_namespace
    history_rows = AlertRun.objects.filter(
        rule_id=rule.rule_id, status=AlertRun.STATUS_OK, history_namespace=namespace,
        execution_mode=AlertRun.MODE_LIVE if namespace == "live" else AlertRun.MODE_TEST,
    ) if rule.rule_id is not None else AlertRun.objects.none()
    if namespace == "live":
        # Failed/empty sends and ambiguous legacy rows cannot mean "already notified".
        history_rows = history_rows.filter(
            Q(decision=AlertRun.DECISION_QUIET) |
            Q(delivery_status__in=[AlertRun.DELIVERY_ACCEPTED, AlertRun.DELIVERY_PARTIAL])
        )
    history = list(
        history_rows
        .exclude(decision="")
        .order_by("-started_at", "-pk")[:HISTORY]
    )
    previous = next((r.evidence for r in history if r.evidence), None)
    validation = (meta.get("validation") or {}).get("summary") or {}
    # A pinned clock reads the build as today's, which is what an after-build
    # evaluation sees in production.
    built = f"{now:%Y-%m-%d}" if now else (meta.get("last_run") or "unknown")
    status_lines = [
        f"Last build {built} succeeded. "
        f"Validation: {validation.get('fail', 0)} fail / {validation.get('warn', 0)} warn."
    ]
    blockers = report_blockers(report)
    if blockers:
        status_lines.append(
            "Data sources blocking the next build: "
            + "; ".join(f"{b.name} ({b.detail})" for b in blockers)
        )
    if (meta.get("details") or {}).get("data_source") == "mock":
        status_lines.append("DATA IS MOCK (test run): say so if you alert.")

    decision: dict = {}

    def decide(args: dict) -> str:
        evidence = args.get("evidence") or []
        condition = args.get("condition")
        interpretation = args.get("interpretation")
        if condition not in dict(AlertRun.CONDITION_CHOICES):
            return "Error: condition must be met, not_met or unknown."
        if (not isinstance(interpretation, dict) or any(
                not isinstance(interpretation.get(k), str) or not interpretation[k].strip()
                for k in ("window", "timezone", "scope", "freshness"))):
            return "Error: interpretation requires nonempty window, timezone, scope and freshness."
        if not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence):
            return "Error: evidence must be a list of strings."
        # A conclusory boolean with no supporting evidence cannot mean healthy.
        if not any(e.strip() for e in evidence):
            condition = AlertRun.CONDITION_UNKNOWN
        decision.update(
            condition=condition, interpretation={k: interpretation[k][:2000]
                for k in ("window", "timezone", "scope", "freshness")},
            title=str(args.get("title") or "")[:300],
            message=str(args.get("message") or ""),
            evidence=[str(e) for e in (evidence if isinstance(evidence, list) else [evidence]) if e],
        )
        return "Decision recorded."

    config = llm.LLMConfig.for_org(org)
    toolbox = AssistantToolbox(
        studio, share_report_source=config.share_report_source,
        extra_tools=[(DECIDE_SCHEMA, decide)],
        actor=actor, scope="report", report=report,
    )
    system_blocks = build_system_prompt(toolbox, now=now) + [{
        "type": "text",
        "text": EVALUATOR_BLOCK.format(
            name=rule.name,
            report_name=report.name or report.slug,
            slug=report.slug,
            timezone=rule.timezone,
            instructions=rule.instructions.strip() or "Tell me if anything looks off.",
            status="\n".join(status_lines),
            previous=_format_evidence(previous) if previous else "(none: this is the first evaluation)",
            history="\n".join(
                f"- {r.started_at:%Y-%m-%d %H:%M} UTC: {r.decision} -- {r.title}" for r in history
            ) or "(none yet)",
        ),
    }]

    deadline_s = settings.ASSISTANT_TURN_DEADLINE_S
    deadline = time.monotonic() + deadline_s
    state: dict = {}
    looked_at: list[dict] = []
    cost: Decimal | None = Decimal("0")

    def turn(message: str, *, force_tool: str | None = None, max_turns: int = ALERT_MAX_TURNS):
        """Drive one turn; returns its terminal event, or None once decide ran."""
        nonlocal cost
        gen = llm.stream_turn(
            config, state, message, toolbox=toolbox, system_blocks=system_blocks,
            max_turns=max_turns, remaining=lambda: deadline - time.monotonic(),
            deadline_s=deadline_s, force_tool=force_tool, purpose="alert",
        )
        try:
            for event in gen:
                kind = event.get("type")
                if kind == "usage":
                    delta = event.get("cost_delta_usd")
                    if delta is None:
                        cost = None
                    elif cost is not None:
                        cost += Decimal(str(delta))
                    run.cost_usd = cost  # kept current so an error run still shows its spend
                    if delta is not None:
                        budget.record(org, owner, delta)
                elif kind == "tool_result":
                    if event.get("provenance"):
                        looked_at.append({
                            k: v for k, v in event["provenance"].items()
                            if k not in ("link", "report_name", "built_at")
                        })
                    if event.get("name") == "decide":
                        return None
                elif kind in ("done", "error"):
                    if state.get("usage", {}).get("cost_usd", 0) is None:
                        cost = None
                        run.cost_usd = None
                    return event
        finally:
            gen.close()  # a decided turn stops here: no closing prose, no extra call
        return {"type": "error", "message": "The model produced no output."}

    end = turn(USER_MESSAGE)
    if end is not None and end.get("code") in (None, "turn_limit"):
        end = turn(FORCE_MESSAGE, force_tool="decide", max_turns=1)
    if not decision:
        run.status = AlertRun.STATUS_ERROR
        run.error = (end or {}).get("message") or "The model did not call decide."
        return

    run.condition = decision["condition"]
    run.interpretation = decision["interpretation"]
    run.decision = AlertRun.DECISION_ALERT if run.condition == AlertRun.CONDITION_MET else AlertRun.DECISION_QUIET
    run.title, run.message = decision["title"], decision["message"]
    run.evidence = {
        "cited": decision["evidence"],
        "snapshot": {
            "rule_revision": rule.revision,
            "evaluation_key": getattr(run, "evaluation_key", None),
            "interpretation": run.interpretation,
            "built_at": built,
            "validation": {"fail": validation.get("fail", 0), "warn": validation.get("warn", 0)},
            "looked_at": looked_at,
        },
    }
    if not deliver:
        run.delivery_disposition, run.delivery_reason = "test", "Preview only; no live delivery or episode changes."
        simulation = simulate_delivery(rule, run.condition, now=run.started_at)
        run.simulated_disposition, run.simulated_reason = simulation["disposition"], simulation["reason"]
        return
    saved_rule = claim_delivery(rule, run, owner, report)
    if saved_rule is not None:
        from apps.core.audit import audit_system
        from apps.reports.notify import send_alert

        run.save()  # the mail links to this run by id; evaluate() updates it after
        def current_delivery():
            current, why = _current_rule(rule, owner)
            if current is None:
                return why
            state = current.live_state or {}
            claim = state.get("claim") or {}
            if (state.get("last_result") != "met" or claim.get("run_id") != run.pk
                    or state.get("episode") != claim.get("episode")):
                return "The condition or delivery claim changed before sending."
            return ""
        outcome = send_alert(saved_rule, run, eligibility_check=current_delivery)
        run.delivered_to, run.failed_to = outcome.accepted, outcome.failed
        run.uncertain_to = outcome.uncertain
        if outcome.error:
            run.delivery_status, run.error = AlertRun.DELIVERY_ERROR, outcome.error
        elif outcome.accepted:
            run.delivery_status = AlertRun.DELIVERY_PARTIAL if outcome.failed else AlertRun.DELIVERY_ACCEPTED
        else:
            run.delivery_status = AlertRun.DELIVERY_FAILED if outcome.failed else AlertRun.DELIVERY_EMPTY
        run.delivery_disposition = "unconfirmed" if outcome.uncertain else run.delivery_status
        run.delivery_reason = ("Transport acceptance is uncertain; automatic delivery remains blocked."
                               if outcome.uncertain else run.get_delivery_status_display())
        finish_delivery(saved_rule.pk, run, accepted=bool(outcome.accepted),
                        uncertain=bool(outcome.uncertain), now=now)
        if outcome.accepted:
            audit_system(
                "alert.fire", org=org, rule_id=rule.rule_id, rule=rule.name,
                title=run.title, recipients=len(run.delivered_to),
            )


def _current_rule(snapshot, owner):
    saved = AlertRule.objects.select_related("created_by", "report__studio__org").filter(pk=snapshot.rule_id).first()
    if saved is None:
        return None, "The rule was deleted during evaluation."
    if (saved.revision != snapshot.revision or saved.name != snapshot.name
            or saved.created_by_id != owner.pk):
        return None, "The rule changed during evaluation; nothing was sent. Evaluate the current rule again."
    if not saved.enabled or saved.report.disabled or not saved.report.present_in_scan:
        return None, "The rule or report is disabled or unavailable."
    if not _owner_can_evaluate(saved.created_by, saved.report):
        return None, "The rule owner can no longer access this report."
    return saved, ""


def claim_delivery(snapshot: RuleSnapshot, run: AlertRun, owner, report):
    """Record the latest condition and atomically claim eligible live delivery.

    Returns a current rule or None with run.delivery_disposition/reason set.
    No provider/transport work may run inside this transaction. A stranded
    delivery claim stays visible and is never automatically reclaimed.
    """
    saved, error = _current_rule(snapshot, owner)
    if saved is None:
        run.status, run.error = AlertRun.STATUS_ERROR, error
        run.delivery_disposition, run.delivery_reason = "stale", error
        return None
    with transaction.atomic():
        saved = AlertRule.objects.select_for_update().get(pk=saved.pk)
        state = dict(saved.live_state or {})
        if (saved.revision != snapshot.revision or saved.name != snapshot.name
                or saved.created_by_id != owner.pk or not saved.enabled):
            run.status = AlertRun.STATUS_ERROR
            run.error = "The rule changed during evaluation; nothing was sent. Evaluate the current rule again."
            run.delivery_disposition, run.delivery_reason = "stale", run.error
            return None
        if state.get("revision") != snapshot.revision:
            state = {"revision": snapshot.revision, "condition": "unknown", "pending": False,
                     "claim": state.get("claim")}
        if state.get("observed_at") and run.started_at <= datetime.fromisoformat(state["observed_at"]):
            run.delivery_disposition, run.delivery_reason = "stale", "A newer condition evaluation already completed."
            return None
        state["observed_at"] = run.started_at.isoformat()
        state["last_result"] = run.condition
        if run.condition == AlertRun.CONDITION_NOT_MET:
            state.update(condition=run.condition, pending=False)
            run.delivery_disposition, run.delivery_reason = "not_met", "The requested condition is not met."
        elif run.condition == AlertRun.CONDITION_UNKNOWN:
            run.delivery_disposition, run.delivery_reason = "unknown", "Insufficient evidence; prior episode remains unchanged."
        elif run.condition == AlertRun.CONDITION_MET:
            if state.get("condition") != AlertRun.CONDITION_MET:
                state.update(pending=True, episode=uuid4().hex)
            state["condition"] = run.condition
            result = _policy_result(saved.notification_policy or AlertRule.POLICY_REPEAT, run.condition,
                                    state, saved.last_alerted_at, saved.cooldown_hours, run.started_at)
            run.delivery_disposition, run.delivery_reason = result["disposition"], result["reason"]
            if run.delivery_disposition == "would_notify":
                run.save()
                state["claim"] = {"run_id": run.pk, "episode": state.get("episode"),
                                  "token": uuid4().hex,
                                  "started_at": run.started_at.isoformat()}
                run.delivery_disposition, run.delivery_reason = "claimed", "Eligible delivery claimed."
        AlertRule.objects.filter(pk=saved.pk).update(live_state=state)
    if run.delivery_disposition != "claimed":
        return None
    # Re-read after releasing the lock; no lock spans a mail transport call.
    current, error = _current_rule(snapshot, owner)
    current_state = (current.live_state or {}) if current else {}
    if (error or current_state.get("last_result") != "met"
            or current_state.get("episode") != state.get("episode")):
        if error:
            run.status, run.error = AlertRun.STATUS_ERROR, error
        run.delivery_disposition, run.delivery_reason = "stale", error or "The condition recovered before delivery."
        finish_delivery(saved.pk, run, accepted=False, uncertain=False)
        return None
    return current


def _policy_result(policy, condition, state, last_alerted_at, cooldown_hours, now):
    if condition == "not_met":
        return {"disposition": "not_met", "reason": "The requested condition is not met."}
    if condition != "met":
        return {"disposition": "unknown", "reason": "Insufficient evidence; prior episode remains unchanged."}
    if state.get("claim"):
        return {"disposition": "unconfirmed", "reason": "A previous delivery is in progress or its acceptance is unconfirmed."}
    eligible_at = last_alerted_at + timedelta(hours=cooldown_hours) if last_alerted_at else None
    if cooldown_hours and eligible_at and now < eligible_at:
        return {"disposition": "cooldown", "reason": f"Condition met; next eligible at {eligible_at.isoformat()}."}
    if policy == AlertRule.POLICY_TRANSITION and not state.get("pending"):
        return {"disposition": "episode_delivered", "reason": "This condition episode already has an accepted delivery."}
    return {"disposition": "would_notify", "reason": "Condition met and notification policy is eligible."}


def simulate_delivery(snapshot: RuleSnapshot, condition: str, *, now=None) -> dict:
    """Pure draft/test policy simulation; never queries or writes production state."""
    state = json.loads(snapshot.live_state_json)
    if state.get("revision") != snapshot.revision:
        state = {"condition": "unknown", "pending": False, "claim": state.get("claim")}
    if condition == "met" and state.get("condition") != "met":
        state["pending"] = True
    if not snapshot.enabled:
        return {"disposition": "disabled", "reason": "The draft rule is disabled."}
    return _policy_result(snapshot.notification_policy, condition, state,
                          snapshot.last_alerted_at, snapshot.cooldown_hours, now or timezone.now())


def finish_delivery(rule_id, run, *, accepted, uncertain, now=None):
    with transaction.atomic():
        saved = AlertRule.objects.select_for_update().filter(pk=rule_id).first()
        if saved is None:
            return
        state = dict(saved.live_state or {})
        claim = state.get("claim") or {}
        updates = {}
        if claim.get("run_id") == run.pk:
            if accepted and claim.get("episode") == state.get("episode"):
                state["pending"] = False
            if not uncertain:
                state["claim"] = None
            updates["live_state"] = state
        if accepted:
            updates["last_alerted_at"] = max(filter(None, [saved.last_alerted_at, now or timezone.now()]))
        AlertRule.objects.filter(pk=rule_id).update(**updates)


def _owner_can_evaluate(owner, report) -> bool:
    if owner is None or not owner.is_active:
        return False
    from apps.core.report_access import can_view_report

    return can_view_report(owner, report)


def scheduled_evaluation_key(rule, now=None) -> str:
    """Latest due cadence slot, including its UTC offset through DST folds."""
    from apps.runner.management.commands.runworker import _email_schedule_trigger
    now = now or timezone.now()
    trigger = _email_schedule_trigger(rule, rule.timezone)
    if trigger is None:
        raise ValueError("Invalid alert cadence.")
    days = {"hourly": 1, "weekly": 8, "monthly": 35}.get(rule.freq, 4)
    slot = trigger.get_next_fire_time(None, now - timedelta(days=days))
    latest = slot
    while slot and slot <= now:
        latest = slot
        slot = trigger.get_next_fire_time(slot, slot.astimezone(utc_timezone.utc) + timedelta(seconds=1))
    return f"schedule:{latest.astimezone(utc_timezone.utc).isoformat()}"


def _format_evidence(evidence: dict) -> str:
    snapshot = evidence.get("snapshot") or {}
    lines = [f"- {line}" for line in evidence.get("cited") or []]
    if snapshot.get("built_at"):
        lines.append(f"(that evaluation looked at the build of {snapshot['built_at']})")
    return "\n".join(lines) or "(the previous evaluation cited nothing)"


def _mail_owner_once(rule: AlertRule, why: str) -> bool:
    """One mail per exhaustion, not one per cadence tick: skip when the
    previous run already said so."""
    last = AlertRun.objects.filter(rule=rule, execution_mode=AlertRun.MODE_LIVE,
                                  finished_at__isnull=False).order_by("-started_at", "-pk").first()
    if last is not None and last.status == AlertRun.STATUS_SKIPPED_BUDGET and last.budget_warning_sent:
        return True
    from apps.core.mail import default_from_email

    try:
        return bool(send_mail(
            subject=f"Alert rule paused: {rule.name}",
            message=(
                f"The alert rule \"{rule.name}\" on report {rule.report.name or rule.report.slug} "
                f"was not evaluated.\n\n{why}\n\nIt resumes on its own once the budget allows."
            ),
            from_email=default_from_email(),
            recipient_list=[rule.created_by.email],
            fail_silently=False,
        ))
    except Exception:  # noqa: BLE001 - a rejected warning must remain retryable
        logger.exception("alerts: budget warning failed for rule %s", rule.pk)
        return False


# ── after-build trigger ──────────────────────────────────────────────────────

def evaluate_after_build(run) -> None:
    """Called directly by the executor after a successful build.

    Evaluation is a model turn of up to the assistant deadline, so like the
    executor's own failure attribution it runs off the tick, on a daemon
    thread. Nothing here may raise into the build path.
    """
    try:
        threading.Thread(
            target=_evaluate_after_build, args=(run.report_id, run.pk), daemon=True
        ).start()
    except Exception:  # noqa: BLE001
        logger.exception("alerts: could not start after-build evaluation for run %s", run)


def _evaluate_after_build(report_id: int, build_id: int) -> None:
    close_old_connections()
    try:
        rules = AlertRule.objects.filter(
            report_id=report_id, trigger=AlertRule.TRIGGER_AFTER_BUILD, enabled=True,
            report__kind="report", report__disabled=False, report__present_in_scan=True,
        ).select_related("report", "studio", "org", "created_by")
        for rule in rules:
            evaluate(rule, evaluation_key=f"build:{build_id}")
    except Exception:  # noqa: BLE001
        logger.exception("alerts: after-build evaluation failed for report %s", report_id)
    finally:
        connections.close_all()
