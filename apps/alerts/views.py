"""The studio Alerts page: rules, a run log per rule, and Test now.

DEVELOPER and above create, edit, enable/disable, delete and test rules;
VIEWER gets the same pages read-only. Design: vault
``Architecture/design/portal-agent/00-plan`` §5 "UI".
"""
from __future__ import annotations

import calendar
from uuid import uuid4

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_GET, require_http_methods

from apps.alerts.forms import AlertRuleForm
from apps.alerts.models import AlertRule, AlertRun
from apps.core import roles
from apps.core.audit import audit
from apps.core.choice_requests import choice_response, search_page, selected_by_id
from apps.core.form_responses import is_settings_request, settings_success, settings_error
from apps.core.permissions import require_studio_role
from apps.core.report_access import visible_reports
from apps.reports.models import Report
# Duck-typed on the schedule shape (recipients / recipient_roles /
# recipient_groups), like notify.resolve_recipients -- same words as the
# deliveries list, on purpose.
from apps.reports.recipient_access import DeliveryPresentationBatch

RUNS_PER_PAGE = 25
RULES_LIMIT = 500
_STATUS_LABELS = dict(AlertRun.STATUS_CHOICES)


def _prefix(request) -> str:
    return f"/s/{request.org.slug}/{request.studio.slug}/alerts"


def _rule(request, rule_id) -> AlertRule:
    reports = visible_reports(
        request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, kind=Report.KIND_REPORT)
    )
    return get_object_or_404(
        AlertRule.objects.select_related("report__studio__org", "created_by").prefetch_related("recipients", "recipient_groups"),
        pk=rule_id,
        studio=request.studio,
        report__in=reports,
    )


def _trigger_words(rule: AlertRule) -> str:
    if rule.trigger == AlertRule.TRIGGER_AFTER_BUILD:
        return "After every build"
    if rule.freq == AlertRule.FREQ_HOURLY:
        return f"Hourly at :{rule.send_minute:02d} {rule.timezone}"
    when = f"{rule.send_hour:02d}:{rule.send_minute:02d}"
    if rule.freq == AlertRule.FREQ_WEEKLY:
        when += f" on {calendar.day_name[rule.weekday]}"
    elif rule.freq == AlertRule.FREQ_MONTHLY:
        when += f" on day {rule.month_day}"
    return f"{rule.get_freq_display()} at {when} {rule.timezone}"


def _context(request, **extra) -> dict:
    return {
        "org": request.org,
        "studio": request.studio,
        "can_edit": roles.at_least(request.studio_role, roles.DEVELOPER),
        "prefix": _prefix(request),
        "console_active": "alerts",
        **extra,
    }


@require_studio_role(roles.VIEWER)
def alerts_page(request, org_slug, studio_slug):  # noqa: ARG001
    last = (AlertRun.objects.filter(rule=OuterRef("pk"))
            .exclude(execution_mode=AlertRun.MODE_TEST).order_by("-started_at", "-pk"))
    reports = visible_reports(
        request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, kind=Report.KIND_REPORT)
    )
    rules = (
        AlertRule.objects.filter(studio=request.studio, report__in=reports)
        .select_related("report__studio__org")
        .prefetch_related("recipients", "recipient_groups")
        .annotate(
            last_decision=Subquery(last.values("decision")[:1]),
            last_status=Subquery(last.values("status")[:1]),
            last_condition=Subquery(last.values("condition")[:1]),
            last_disposition=Subquery(last.values("delivery_disposition")[:1]),
        )
        .order_by("name")
    )
    # Far above a realistic rule count; the cap only protects the page, nothing is shown about it.
    rules = list(rules.order_by("name", "pk")[:RULES_LIMIT])
    batch = DeliveryPresentationBatch(rules)
    rows = [
        {
            "rule": rule,
            "trigger": _trigger_words(rule),
            "recipients": batch.summary(rule),
            "last": dict(AlertRun.CONDITION_CHOICES).get(rule.last_condition)
                    or ("Not evaluated" if rule.last_disposition == "not_evaluated" else
                        rule.last_decision or _STATUS_LABELS.get(rule.last_status, "")),
        }
        for rule in rules
    ]
    return render(request, "alerts/alerts.html", _context(request, rows=rows))


@require_studio_role(roles.DEVELOPER)
@require_http_methods(["GET", "POST"])
def recipient_choices(request, org_slug, studio_slug):
    from apps.reports.recipient_choices import recipient_page
    from apps.core.choice_requests import choice_request_params
    try:
        parameters = choice_request_params(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    response = JsonResponse(recipient_page(request.studio, include_inactive=True,
        query=parameters.get('q', ''), page=1,
        selected_people=parameters.get('selected_people', ''), selected_groups=parameters.get('selected_groups', '')))
    response['Cache-Control'] = 'private, no-store'
    return response


@require_studio_role(roles.DEVELOPER)
@require_GET
def report_choices(request, org_slug, studio_slug):
    reports = visible_reports(request.user, Report.objects.filter(studio=request.studio,
        present_in_scan=True, kind=Report.KIND_REPORT)).order_by('name', 'slug', 'pk')
    page = search_page(reports, request.GET.get('q', '')[:200], fields=('name', 'slug'), page=request.GET.get('page'))

    def item(report):
        return {'value': str(report.pk), 'label': report.name or report.slug, 'hint': ''}
    return choice_response(page, item, selected=selected_by_id(reports, request.GET.get('selected', '')),
        pagination={'page': page.number, 'per_page': 25, 'total': page.paginator.count,
                    'has_previous': page.has_previous(), 'has_next': page.has_next()})


@require_studio_role(roles.DEVELOPER)
def rule_form(request, org_slug, studio_slug, rule_id=None):  # noqa: ARG001
    rule = _rule(request, rule_id) if rule_id else AlertRule(
        org=request.org, studio=request.studio, created_by=request.user
    )
    form = AlertRuleForm(request.POST or None, instance=rule, studio=request.studio, actor=request.user)
    preview = request.method == "POST" and request.POST.get("action") == "preview"
    result = None
    if preview and form.is_valid():
        from apps.alerts.evaluator import RuleSnapshot, evaluate_snapshot

        draft = form.save(commit=False)
        draft.org = request.org
        draft.studio = request.studio
        draft.created_by = rule.created_by if rule_id else request.user
        snapshot = RuleSnapshot.from_rule(draft)
        run = evaluate_snapshot(
            snapshot, actor=request.user, billing_user=request.user,
            execution_mode=AlertRun.MODE_TEST,
            history_namespace=f"editor:{request.user.pk}:{uuid4().hex}",
        )
        result = {
            "condition": run.condition_label,
            "title": run.title,
            "message": run.message,
            "evidence": (run.evidence or {}).get("cited", []),
            "interpretation": run.interpretation or {},
            "simulation": run.simulated_disposition,
            "simulation_reason": run.simulated_reason,
            "cost": str(run.cost_usd) if run.cost_usd is not None else None,
            "error": run.error,
            "status": run.get_status_display(),
        }
        if is_settings_request(request):
            return settings_success(request, "Draft evaluated. No delivery was attempted.", result=result)
    elif request.method == "POST" and form.is_valid():
        # Keep recipient membership changes inside the same settings lock.
        with transaction.atomic():
            if rule_id:
                AlertRule.objects.select_for_update().get(pk=rule.pk)
            form.save()
        audit(request, "alert.update" if rule_id else "alert.create", target=rule, rule_id=rule.pk)
        if rule_id and is_settings_request(request):
            return settings_success(request, f"Saved “{rule.name}”.")
        messages.success(request, f"Saved “{rule.name}”.")
        return redirect(_prefix(request))
    if request.method == "POST" and is_settings_request(request) and (rule_id or preview):
        return settings_error(request, "Check the highlighted fields.", form=form)
    return render(request, "alerts/alert_form.html", _context(request, form=form, rule=rule, result=result))


@require_studio_role(roles.VIEWER)
def rule_detail(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    rule = _rule(request, rule_id)
    runs = rule.runs.all()
    requested = request.GET.get("run", "")
    try:
        run_id = int(requested) if requested.isdecimal() else 0
    except ValueError:
        run_id = 0
    target = runs.filter(pk=run_id).first() if 0 < run_id < 2**63 else None
    page_number = request.GET.get("page")
    if target is not None and page_number is None:
        before = runs.filter(Q(started_at__gt=target.started_at) |
                             Q(started_at=target.started_at, pk__gt=target.pk)).count()
        page_number = before // RUNS_PER_PAGE + 1
    page = Paginator(runs, RUNS_PER_PAGE).get_page(page_number)
    return render(request, "alerts/alert_detail.html", _context(
        request, rule=rule, page=page, trigger=_trigger_words(rule),
        recipients=DeliveryPresentationBatch([rule]).summary(rule), highlight=target.pk if target else None,
    ))


@require_studio_role(roles.DEVELOPER)
@require_POST
def rule_test(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    from apps.assistant import llm
    from apps.alerts.evaluator import RuleSnapshot, evaluate_snapshot

    rule = _rule(request, rule_id)
    ok, why = llm.is_available(request.org)
    if not ok:
        messages.error(request, why)
        return redirect(f"{_prefix(request)}/{rule.pk}")
    # Synchronous on purpose: bounded by the evaluator's turn cap and the
    # assistant deadline, and the answer is what the person is waiting for.
    run = evaluate_snapshot(
        RuleSnapshot.from_rule(rule), actor=request.user, billing_user=request.user,
        execution_mode=AlertRun.MODE_TEST,
        history_namespace=f"editor:{request.user.pk}:{uuid4().hex}",
    )
    messages.info(request, "Test run — nothing was sent.")
    return redirect(f"{_prefix(request)}/{rule.pk}?run={run.pk}")


@require_studio_role(roles.DEVELOPER)
@require_POST
def rule_toggle(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    rule = _rule(request, rule_id)
    rule.enabled = not rule.enabled
    rule.save(update_fields=["enabled"])
    audit(request, "alert.update", target=rule, rule_id=rule.pk, enabled=rule.enabled)
    return redirect(_prefix(request))


@require_studio_role(roles.DEVELOPER)
@require_POST
def rule_delete(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    rule = _rule(request, rule_id)
    rule_pk, name = rule.pk, rule.name
    rule.delete()
    audit(request, "alert.delete", org=request.org, rule_id=rule_pk, rule=name)
    messages.success(request, f"Deleted “{name}”.")
    return redirect(_prefix(request))


@require_studio_role(roles.DEVELOPER)
@require_POST
def rule_reconcile(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    rule = _rule(request, rule_id)
    resolution = request.POST.get("resolution")
    if resolution not in ("notified", "not_accepted"):
        return HttpResponse("Choose a delivery resolution.", status=400)
    with transaction.atomic():
        rule = AlertRule.objects.select_for_update().get(pk=rule.pk)
        state = dict(rule.live_state or {})
        claim = state.get("claim") or {}
        if (not claim or request.POST.get("token") != claim.get("token")
                or request.POST.get("run_id") != str(claim.get("run_id"))):
            return HttpResponse("This delivery claim changed; refresh before reconciling.", status=409)
        run = rule.runs.get(pk=claim["run_id"])
        # A claim currently sending cannot be reconciled before its bounded turn ends.
        from django.conf import settings
        from datetime import timedelta
        if run.finished_at is None and timezone.now() - run.started_at < timedelta(seconds=settings.ASSISTANT_TURN_DEADLINE_S + 60):
            return HttpResponse("This evaluation may still be running; try again after its deadline.", status=409)
        resolved_at = timezone.now()
        before = {"claim": claim, "pending": state.get("pending"), "last_alerted_at":
                  rule.last_alerted_at.isoformat() if rule.last_alerted_at else None}
        updates = {}
        if resolution == "notified":
            updates["last_alerted_at"] = resolved_at
            if claim.get("episode") == state.get("episode") and state.get("revision") == rule.revision:
                state["pending"] = False
        state["claim"] = None
        updates["live_state"] = state
        AlertRule.objects.filter(pk=rule.pk).update(**updates)
        run.manual_resolution = {"resolution": resolution, "actor_id": request.user.pk,
                                 "at": resolved_at.isoformat(), "claim_token": claim["token"]}
        run.save(update_fields=["manual_resolution"])
        audit(request, "alert.delivery_reconciled", target=rule, rule_id=rule.pk, run_id=run.pk,
              resolution=resolution, before=before,
              after={"claim": None, "pending": state.get("pending"),
                     "last_alerted_at": updates.get("last_alerted_at", rule.last_alerted_at).isoformat()
                     if updates.get("last_alerted_at", rule.last_alerted_at) else None})
    messages.success(request, "Delivery reviewed. No mail was sent by this action.")
    return redirect(f"{_prefix(request)}/{rule.pk}")
