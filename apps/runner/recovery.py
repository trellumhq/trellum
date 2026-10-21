"""Bounded whole-report recovery, admitted through the ordinary durable Run queue.

The report lock serializes completion, cancellation and manual/cron/retry admission.
A queued/running Run is the dispatch lease: it never expires into a second process.
"""
from __future__ import annotations

from datetime import timedelta
import random

from django.db import transaction
from django.utils import timezone

from apps.reports.models import Report
from apps.runner.models import Run, RunRecovery

OPEN_STATES = ("pending", "running")
MAX_ATTEMPTS = 2
MAX_AGE = timedelta(hours=4)


def replay_safe(report) -> bool:
    config = report.config
    if not isinstance(config, dict):
        return False
    if "recovery" not in config:
        return True
    policy = config["recovery"]
    if not isinstance(policy, dict):
        return False
    if "replay_safe" not in policy:
        return True
    return type(policy["replay_safe"]) is bool and policy["replay_safe"]


def _summary(chain):
    return {
        "state": chain.state, "reason": chain.reason, "attempt": chain.attempt,
        "due_at": chain.due_at.isoformat() if chain.due_at else None,
        "root_run_id": str(chain.root_run_id),
    }


def _save_chain(chain):
    chain.save()
    # At most three rows; store the summary with history to avoid per-row API queries.
    for run in Run.objects.filter(recovery_id=chain.pk)[:3]:
        evidence = dict(run.diagnostics or {})
        evidence["recovery"] = _summary(chain)
        Run.objects.filter(pk=run.pk).update(diagnostics=evidence)


def _disposition(run, evidence, confirmed):
    if run.stop_requested or run.status == Run.STOPPED:
        return "cancelled"
    if not confirmed:
        return "unconfirmed"
    if run.status == Run.OOM_KILLED or evidence.get("cause") == "memory_allocation_failed":
        return "memory_failure"
    if not run.replay_safe or not replay_safe(run.report):
        return "report_replay_not_safe"
    if evidence.get("stage") == "publish":
        return "publication_outcome_unconfirmed"
    if evidence.get("unsafe_operation") is True or evidence.get("unknown_write_outcome") is True:
        return "operation_outcome_unconfirmed"
    # An outer deadline cannot make an already-observed permanent failure safe.
    errors = evidence.get("errors") or []
    codes = {str(item.get("code", "")) for item in errors if isinstance(item, dict)}
    classes = {item.get("class", "") for item in errors if isinstance(item, dict)}
    if "MemoryError" in classes or any(code.startswith("53") for code in codes):
        return "memory_failure"
    if (classes & {"ProgrammingError", "ConfigurationError", "ValueError", "TypeError", "AuthenticationError"}
            or any(code.startswith(("28", "42", "22", "23")) for code in codes)
            or codes & {"57014", "1045", "08004", "08007", "08P01"}
            or (evidence.get("state") == "failed" and errors and evidence.get("transient") is not True)):
        return "permanent_or_unknown_failure"
    # A managed read must be proven safe separately. No blanket replay of raw APIs.
    if run.status == Run.TIMEOUT:
        if evidence.get("stage") in ("connect", "execute", "fetch") and evidence.get("query_retry_safe") is not True:
            return "operation_outcome_unconfirmed"
        return "transient_timeout"
    if evidence.get("transient") is True and evidence.get("query_retry_safe") is True:
        return "transient_connection"
    return "permanent_or_unknown_failure"


def complete_run(run, *, diagnostics, termination_confirmed):
    """Record evidence and recovery intent inside caller's terminal-save transaction.

    Caller holds Report then Run locks and rejects duplicate completions.
    Terminal run fields must be saved by the caller in this same transaction.
    """
    evidence = dict(diagnostics or {})
    if not evidence.get("cause"):
        errors = evidence.get("errors") or []
        codes = {str(item.get("code", "")) for item in errors if isinstance(item, dict)}
        classes = {item.get("class", "") for item in errors if isinstance(item, dict)}
        if evidence.get("transient") is True:
            evidence["cause"] = "transient_connection_failure"
        elif any(code.startswith("28") for code in codes) or codes & {"1045"}:
            evidence["cause"] = "authentication_failed"
        elif any(code.startswith(("42", "22", "23")) for code in codes) or "ProgrammingError" in classes:
            evidence["cause"] = "sql_error"
        elif "57014" in codes:
            evidence["cause"] = "query_cancelled_or_timeout"
        elif classes & {"ConfigurationError", "ValueError", "TypeError"}:
            evidence["cause"] = "configuration_error"
        elif run.status != Run.SUCCESS:
            evidence["cause"] = "unknown"
    run.diagnostics = evidence
    run.termination_confirmed = termination_confirmed
    run.save(update_fields=["diagnostics", "termination_confirmed"])
    chain = RunRecovery.objects.filter(pk=run.recovery_id).first() if run.recovery_id else None
    now = timezone.now()
    if run.status == Run.SUCCESS:
        for old in RunRecovery.objects.filter(report_id=run.report_id, state__in=OPEN_STATES):
            old.state = "succeeded" if old.pk == run.recovery_id and run.trigger == "retry" else "superseded"
            old.reason = "successful_build"
            old.due_at = None
            _save_chain(old)
        if chain is not None and chain.state not in OPEN_STATES:
            _save_chain(chain)
        return True
    reason = _disposition(run, evidence, termination_confirmed)
    eligible = reason in ("transient_timeout", "transient_connection")
    if chain is None:
        # Every newer terminal attempt consumes/supersedes prior intent. An unsafe
        # manual failure must never leave an older safe retry eligible to run.
        chain = RunRecovery.objects.filter(report_id=run.report_id, state="pending").first()
        if chain is not None:
            chain.attempt += 1
            run.recovery, run.recovery_attempt = chain, chain.attempt
            run.save(update_fields=["recovery", "recovery_attempt"])
    if chain is None and eligible:
        chain = RunRecovery.objects.create(
            root_run=run, report_id=run.report_id, current_run=run,
            created_at=run.created_at, expires_at=run.created_at + MAX_AGE,
        )
        run.recovery = chain
        run.recovery_attempt = 0
        run.save(update_fields=["recovery", "recovery_attempt"])
    if chain is None:
        evidence["recovery"] = {"state": "cancelled" if reason == "cancelled" else "unconfirmed" if reason == "unconfirmed" else "ineligible", "reason": reason, "attempt": 0, "due_at": None}
        run.diagnostics = evidence
        run.save(update_fields=["diagnostics"])
        return True
    if chain.state not in OPEN_STATES:
        _save_chain(chain)
        return True  # cancellation/supersession cannot be undone by a late completion
    chain.current_run = run
    chain.reason = reason
    chain.due_at = None
    if not eligible:
        chain.state = "cancelled" if reason == "cancelled" else "ineligible"
    elif now >= chain.expires_at:
        chain.state, chain.reason = "expired", "recovery_age_exceeded"
    elif chain.attempt >= MAX_ATTEMPTS:
        chain.state, chain.reason = "exhausted", "attempt_budget_exhausted"
    else:
        delay = (60, 300)[chain.attempt]
        chain.due_at = now + timedelta(seconds=delay + random.uniform(0, delay * .1))
        chain.state = "pending"
    _save_chain(chain)
    return True


def cancel_pending(report):
    """Caller holds the report lock. A running process still needs confirmed stop."""
    count = 0
    for chain in RunRecovery.objects.filter(report=report, state__in=OPEN_STATES):
        chain.state, chain.reason, chain.due_at = "cancelled", "stop_requested", None
        _save_chain(chain)
        count += 1
    return count


def dispatch_due(*, limit=25):
    """Bounded restart-safe coordinator pass, no sleeping worker slots or timers."""
    now = timezone.now()
    ids = list(RunRecovery.objects.filter(state="pending", due_at__lte=now).order_by("due_at").values_list("pk", "report_id")[:limit])
    for chain_id, report_id in ids:
        with transaction.atomic():
            report = Report.objects.select_for_update().select_related("studio").get(pk=report_id)
            chain = RunRecovery.objects.select_for_update().get(pk=chain_id)
            if chain.state != "pending" or not chain.due_at or chain.due_at > now:
                continue
            if now >= chain.expires_at:
                chain.state, chain.reason, chain.due_at = "expired", "recovery_age_exceeded", None
            elif Run.objects.filter(report=report, status=Run.SUCCESS, created_at__gt=chain.root_run.created_at).exists():
                chain.state, chain.reason, chain.due_at = "superseded", "newer_successful_build", None
            elif not report.present_in_scan or report.disabled or report.refresh_suspended_at is not None or not replay_safe(report):
                chain.state, chain.reason, chain.due_at = "cancelled", "report_unavailable_or_unsafe", None
            elif Run.objects.filter(report=report, status__in=Run.ACTIVE_STATUSES).exists():
                chain.reason = "active_run_deferred"
                # Keep the durable intent and rescan at a bounded cadence.
                chain.due_at = now + timedelta(seconds=30)
            else:
                chain.attempt += 1
                run = Run.objects.create(
                    report=report, studio=report.studio, slug=report.slug, pool=report.studio.pool,
                    cache_mode=chain.root_run.cache_mode, priority=report.priority, trigger="retry",
                    replay_safe=True, recovery=chain, recovery_attempt=chain.attempt,
                )
                chain.current_run, chain.state, chain.reason, chain.due_at = run, "running", "retry_admitted", None
            _save_chain(chain)


def reconcile(*, limit=25):
    """Repair terminal paths outside executor (preflight/quota/setup failures).

    New executor completions write intent atomically. These known non-replayable
    terminal paths can close a running chain without inventing transient evidence.
    """
    ids = list(RunRecovery.objects.filter(state="running").exclude(current_run__status__in=Run.ACTIVE_STATUSES).values_list("pk", "report_id")[:limit])
    for chain_id, report_id in ids:
        with transaction.atomic():
            Report.objects.select_for_update().get(pk=report_id)
            chain = RunRecovery.objects.select_for_update().get(pk=chain_id)
            if chain.state == "running" and chain.current_run and not chain.current_run.is_active:
                run = chain.current_run
                complete_run(run, diagnostics=run.diagnostics or {}, termination_confirmed=run.termination_confirmed is True)
