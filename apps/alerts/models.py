"""Agentic alerts: a rule is context + a report + a cadence, and the assistant
decides whether to alert. See ``apps.alerts.evaluator`` for the loop and the
design note (vault ``Architecture/design/portal-agent/00-plan`` §5) for why
there is no threshold field anywhere on these rows.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone as dj_tz

from apps.reports.models import EmailSchedule, Report
from apps.studios.models import Studio


class AlertRule(models.Model):
    TRIGGER_AFTER_BUILD = "after_build"
    TRIGGER_SCHEDULE = "schedule"
    TRIGGER_CHOICES = [
        (TRIGGER_AFTER_BUILD, "After every successful build"),
        (TRIGGER_SCHEDULE, "On a schedule"),
    ]

    #: ``EmailSchedule``'s cadences plus hourly; ``send_hour`` is ignored for
    #: hourly, ``send_minute`` still applies.
    FREQ_HOURLY = "hourly"
    FREQ_DAILY = EmailSchedule.FREQ_DAILY
    FREQ_WEEKDAYS = EmailSchedule.FREQ_WEEKDAYS
    FREQ_WEEKLY = EmailSchedule.FREQ_WEEKLY
    FREQ_MONTHLY = EmailSchedule.FREQ_MONTHLY
    FREQ_CHOICES = [(FREQ_HOURLY, "Hourly")] + EmailSchedule.FREQ_CHOICES
    RECIPIENT_ROLE_CHOICES = EmailSchedule.RECIPIENT_ROLE_CHOICES

    org = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="alert_rules")
    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="alert_rules")
    report = models.ForeignKey("reports.Report", on_delete=models.CASCADE, related_name="alert_rules")
    name = models.CharField(max_length=200)
    #: Plain text. Empty means "tell me if anything looks off".
    instructions = models.TextField(blank=True)
    trigger = models.CharField(max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_AFTER_BUILD)

    # Schedule fields, copied from EmailSchedule so _email_schedule_trigger
    # builds the job the same way. Only consulted when trigger == "schedule".
    freq = models.CharField(max_length=16, choices=FREQ_CHOICES, default=FREQ_DAILY)
    send_hour = models.PositiveSmallIntegerField(default=8)
    send_minute = models.PositiveSmallIntegerField(default=0)
    weekday = models.PositiveSmallIntegerField(default=0)
    month_day = models.PositiveSmallIntegerField(default=1)
    timezone = models.CharField(max_length=64, default="UTC")

    # Same names and types as EmailSchedule: apps.reports.notify.
    # resolve_recipients() reads these by duck typing.
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="alert_rule_subscriptions", blank=True
    )
    recipient_roles = models.JSONField(default=list, blank=True)
    recipient_groups = models.ManyToManyField(
        "orgs.PermissionGroup", related_name="alert_rules", blank=True
    )

    #: After an alert fires, the rule stays quiet for this long.
    cooldown_hours = models.PositiveIntegerField(default=24)
    enabled = models.BooleanField(default=True)
    #: Who pays: LLM spend is billed to this user (EmailSchedule.created_by
    #: is the precedent for "a studio object owned by a person").
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=dj_tz.now)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)

    def clean(self) -> None:
        # The cadence fields intentionally share EmailSchedule's contract.
        EmailSchedule.clean(self)
        errors = {}
        if self.studio_id and self.org_id and not Studio.objects.filter(
            pk=self.studio_id, org_id=self.org_id
        ).exists():
            errors["studio"] = "The studio must belong to the rule's organization."
        if self.report_id and self.studio_id and not Report.objects.filter(
            pk=self.report_id, studio_id=self.studio_id
        ).exists():
            errors["report"] = "The report must belong to the rule's studio."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def in_cooldown(self, now=None) -> bool:
        if self.last_alerted_at is None or not self.cooldown_hours:
            return False
        return (now or dj_tz.now()) - self.last_alerted_at < timedelta(hours=self.cooldown_hours)

    def __str__(self) -> str:
        return f"alert({self.name}, {self.report})"


class AlertRun(models.Model):
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_SKIPPED_BUDGET = "skipped_budget"
    STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
    STATUS_CHOICES = [
        (STATUS_OK, "OK"), (STATUS_ERROR, "Error"),
        (STATUS_SKIPPED_BUDGET, "Skipped: budget"), (STATUS_SKIPPED_COOLDOWN, "Skipped: cooldown"),
    ]
    DECISION_ALERT = "alert"
    DECISION_QUIET = "quiet"
    DECISION_CHOICES = [(DECISION_ALERT, "Alert"), (DECISION_QUIET, "Quiet")]

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="runs")
    started_at = models.DateTimeField(default=dj_tz.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_OK)
    #: Blank when the evaluator never reached a decision (skipped, error).
    decision = models.CharField(max_length=8, choices=DECISION_CHOICES, blank=True, default="")
    title = models.CharField(max_length=300, blank=True, default="")
    message = models.TextField(blank=True, default="")
    #: ``{"cited": [...lines the model gave decide()], "snapshot": {...}}`` —
    #: the snapshot is what the next evaluation compares against.
    evidence = models.JSONField(default=dict, blank=True)
    cost_usd = models.DecimalField(max_digits=11, decimal_places=6, default=Decimal("0"))
    delivered_to = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"alert-run({self.rule_id}, {self.status}, {self.decision or '-'})"
