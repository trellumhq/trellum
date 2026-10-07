"""The studio Alerts page: rules, a run log per rule, and Test now.

DEVELOPER and above create, edit, enable/disable, delete and test rules;
VIEWER gets the same pages read-only. Design: vault
``Architecture/design/portal-agent/00-plan`` §5 "UI".
"""
from __future__ import annotations

import calendar

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.alerts.evaluator import evaluate
from apps.alerts.forms import AlertRuleForm
from apps.alerts.models import AlertRule, AlertRun
from apps.core import roles
from apps.core.audit import audit
from apps.core.permissions import require_studio_role
from apps.core.report_access import visible_reports
from apps.reports.models import Report
# Duck-typed on the schedule shape (recipients / recipient_roles /
# recipient_groups), like notify.resolve_recipients -- same words as the
# deliveries list, on purpose.
from apps.reports.views import _recipient_summary

RUNS_PER_PAGE = 25
_STATUS_LABELS = dict(AlertRun.STATUS_CHOICES)


def _prefix(request) -> str:
    return f"/s/{request.org.slug}/{request.studio.slug}/alerts"


def _rule(request, rule_id) -> AlertRule:
    reports = visible_reports(
        request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, kind=Report.KIND_REPORT)
    )
    return get_object_or_404(
        AlertRule.objects.select_related("report__studio", "created_by"),
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
    last = AlertRun.objects.filter(rule=OuterRef("pk")).order_by("-started_at")
    reports = visible_reports(
        request.user, Report.objects.filter(studio=request.studio, present_in_scan=True, kind=Report.KIND_REPORT)
    )
    rules = (
        AlertRule.objects.filter(studio=request.studio, report__in=reports)
        .select_related("report__studio")
        .prefetch_related("recipients", "recipient_groups")
        .annotate(
            last_decision=Subquery(last.values("decision")[:1]),
            last_status=Subquery(last.values("status")[:1]),
        )
        .order_by("name")
    )
    rows = [
        {
            "rule": rule,
            "trigger": _trigger_words(rule),
            "recipients": _recipient_summary(rule),
            "last": rule.last_decision or _STATUS_LABELS.get(rule.last_status, ""),
        }
        for rule in rules
    ]
    return render(request, "alerts/alerts.html", _context(request, rows=rows))


@require_studio_role(roles.DEVELOPER)
def rule_form(request, org_slug, studio_slug, rule_id=None):  # noqa: ARG001
    rule = _rule(request, rule_id) if rule_id else AlertRule(
        org=request.org, studio=request.studio, created_by=request.user
    )
    form = AlertRuleForm(request.POST or None, instance=rule, studio=request.studio)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "alert.update" if rule_id else "alert.create", target=rule, rule_id=rule.pk)
        messages.success(request, f"Saved “{rule.name}”.")
        return redirect(_prefix(request))
    return render(request, "alerts/alert_form.html", _context(request, form=form, rule=rule))


@require_studio_role(roles.VIEWER)
def rule_detail(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    rule = _rule(request, rule_id)
    page = Paginator(rule.runs.all(), RUNS_PER_PAGE).get_page(request.GET.get("page"))
    new = request.GET.get("run", "")
    return render(request, "alerts/alert_detail.html", _context(
        request, rule=rule, page=page, trigger=_trigger_words(rule),
        recipients=_recipient_summary(rule), highlight=int(new) if new.isdigit() else None,
    ))


@require_studio_role(roles.DEVELOPER)
@require_POST
def rule_test(request, org_slug, studio_slug, rule_id):  # noqa: ARG001
    from apps.assistant import llm

    rule = _rule(request, rule_id)
    ok, why = llm.is_available(request.org)
    if not ok:
        messages.error(request, why)
        return redirect(f"{_prefix(request)}/{rule.pk}")
    # Synchronous on purpose: bounded by the evaluator's turn cap and the
    # assistant deadline, and the answer is what the person is waiting for.
    run = evaluate(rule, deliver=False)
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
