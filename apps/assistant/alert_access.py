"""Shared alert visibility and optimistic settings tokens for panel and MCP."""
import hashlib
import json

from apps.alerts.models import AlertRule
from apps.core.report_access import visible_reports
from apps.reports.models import Report


def alert_id(value):
    return value if type(value) is int and 0 < value < 2**63 else None


def visible_alerts(studio, actor=None, report_id=None):
    reports = Report.objects.filter(studio=studio, present_in_scan=True, kind=Report.KIND_REPORT)
    if actor is not None:
        reports = visible_reports(actor, reports)
    if report_id is not None:
        reports = reports.filter(pk=report_id)
    return AlertRule.objects.filter(studio=studio, org=studio.org, report__in=reports)


def edit_revision(rule):
    # Evaluator observations and ownership are not editable settings. The
    # condition revision remains separate so cadence edits keep the episode.
    values = {name: getattr(rule, name) for name in (
        "org_id", "studio_id", "report_id", "name", "instructions", "trigger",
        "freq", "send_hour", "send_minute", "weekday", "month_day", "timezone",
        "recipient_roles", "cooldown_hours", "notification_policy", "enabled",
    )}
    values["recipients"] = list(rule.recipients.order_by("pk").values_list("pk", flat=True))
    values["recipient_groups"] = list(rule.recipient_groups.order_by("pk").values_list("pk", flat=True))
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def detail_url(rule):
    from django.urls import reverse

    return reverse("studio-alert", args=[rule.studio.org.slug, rule.studio.slug, rule.pk])
