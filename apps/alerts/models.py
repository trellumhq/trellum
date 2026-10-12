"""Agentic alerts: a rule is context + a report + a cadence, and the assistant
decides whether to alert. See ``apps.alerts.evaluator`` for the loop and the
design note (vault ``Architecture/design/portal-agent/00-plan`` §5) for why
there is no threshold field anywhere on these rows.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone as dj_tz

from apps.reports.models import EmailSchedule, Report
from apps.studios.models import Studio


class AlertRule(models.Model):
    POLICY_REPEAT = "repeat"
    POLICY_TRANSITION = "transition"
    POLICY_CHOICES = [(POLICY_REPEAT, "While met, repeat after cooldown"),
                      (POLICY_TRANSITION, "Once per condition episode")]
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

    #: After a transport accepts an alert, the rule stays quiet for this long.
    cooldown_hours = models.PositiveIntegerField(default=24)
    enabled = models.BooleanField(default=True)
    #: Billing owner: LLM spend and evaluation access depend on this user.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=dj_tz.now)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)
    notification_policy = models.CharField(max_length=16, choices=POLICY_CHOICES, null=True, blank=True)
    live_state = models.JSONField(null=True, blank=True)

    @property
    def revision(self) -> str:
        values = [self.org_id, self.studio_id, self.report_id, self.instructions, self.timezone]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()

    @property
    def next_eligible_at(self):
        if self.last_alerted_at is not None:
            return self.last_alerted_at + timedelta(hours=self.cooldown_hours)
        return None

    @property
    def policy_label(self):
        return dict(self.POLICY_CHOICES)[self.notification_policy or self.POLICY_REPEAT]

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
        if self.report_id and Report.objects.filter(pk=self.report_id, kind=Report.KIND_ANALYSIS).exists():
            errors["report"] = "Analyses do not support alert rules."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        # Settings drafts cannot overwrite observations made while the form was open.
        if self.pk and not self._state.adding and kwargs.get("update_fields") is None:
            kwargs["update_fields"] = [f.name for f in self._meta.concrete_fields
                                      if not f.primary_key and f.name not in
                                      ("live_state", "last_alerted_at", "last_run_at", "last_test_at")]
        if self.pk and not self._state.adding:
            with transaction.atomic():
                current = AlertRule.objects.select_for_update().get(pk=self.pk)
                fields = kwargs.get("update_fields")
                previous_revision = current.revision
                for field in self._meta.concrete_fields:
                    if field.name in {"org", "studio", "report", "instructions", "timezone"}:
                        if fields is None or field.name in fields or field.attname in fields:
                            setattr(current, field.attname, getattr(self, field.attname))
                if current.revision != previous_revision:
                    self.live_state = {"revision": current.revision, "condition": "unknown", "pending": False,
                                       "claim": (current.live_state or {}).get("claim")}
                    if fields is not None:
                        kwargs["update_fields"] = list(set(fields) | {"live_state"})
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def in_cooldown(self, now=None) -> bool:
        if self.last_alerted_at is None or not self.cooldown_hours:
            return False
        return (now or dj_tz.now()) - self.last_alerted_at < timedelta(hours=self.cooldown_hours)

    def __str__(self) -> str:
        return f"alert({self.name}, {self.report})"


class AlertRun(models.Model):
    DISPOSITION_NOT_EVALUATED = "not_evaluated"
    DISPOSITION_TEST = "test"
    DISPOSITION_DUPLICATE = "duplicate"
    DISPOSITION_STALE = "stale"
    DISPOSITION_NOT_MET = "not_met"
    DISPOSITION_UNKNOWN = "unknown"
    DISPOSITION_COOLDOWN = "cooldown"
    DISPOSITION_EPISODE_DELIVERED = "episode_delivered"
    DISPOSITION_WOULD_NOTIFY = "would_notify"
    DISPOSITION_CLAIMED = "claimed"
    DISPOSITION_UNCONFIRMED = "unconfirmed"
    DISPOSITION_PAUSED = "paused"
    CONDITION_MET = "met"
    CONDITION_NOT_MET = "not_met"
    CONDITION_UNKNOWN = "unknown"
    CONDITION_CHOICES = [(CONDITION_MET, "Met"), (CONDITION_NOT_MET, "Not met"),
                         (CONDITION_UNKNOWN, "Unknown: insufficient evidence")]
    MODE_LEGACY = "legacy"
    MODE_LIVE = "live"
    MODE_TEST = "test"
    MODE_CHOICES = [(MODE_LEGACY, "Legacy: mode unknown"), (MODE_LIVE, "Live"), (MODE_TEST, "Test")]
    DELIVERY_UNKNOWN = "unknown"
    DELIVERY_NONE = "not_attempted"
    DELIVERY_EMPTY = "no_recipients"
    DELIVERY_ACCEPTED = "accepted"
    DELIVERY_PARTIAL = "partial"
    DELIVERY_FAILED = "failed"
    DELIVERY_ERROR = "error"
    DELIVERY_CHOICES = [
        (DELIVERY_UNKNOWN, "Legacy: delivery uncertain"), (DELIVERY_NONE, "Nothing sent"),
        (DELIVERY_EMPTY, "No eligible recipients"), (DELIVERY_ACCEPTED, "Accepted for delivery"),
        (DELIVERY_PARTIAL, "Partial"), (DELIVERY_FAILED, "Failed"), (DELIVERY_ERROR, "Delivery error"),
    ]
    STATUS_OK = "ok"
    STATUS_RUNNING = "running"
    STATUS_ERROR = "error"
    STATUS_SKIPPED_BUDGET = "skipped_budget"
    STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
    STATUS_CHOICES = [
        (STATUS_OK, "OK"), (STATUS_RUNNING, "In progress / interrupted"), (STATUS_ERROR, "Error"),
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
    cost_usd = models.DecimalField(max_digits=11, decimal_places=6, null=True, blank=True, default=Decimal("0"))
    #: Transport-accepted addresses for new runs; legacy rows are uncertain.
    delivered_to = models.JSONField(default=list, blank=True)
    #: Nullable additions preserve older writers; NULL means legacy uncertainty.
    execution_mode = models.CharField(max_length=8, choices=MODE_CHOICES, default=MODE_LEGACY, null=True)
    history_namespace = models.CharField(max_length=100, default="legacy", null=True)
    delivery_status = models.CharField(max_length=16, choices=DELIVERY_CHOICES, default=DELIVERY_UNKNOWN, null=True)
    failed_to = models.JSONField(default=list, blank=True, null=True)
    #: The current exhaustion has an accepted owner warning; inherited across
    #: suppressed live ticks, never written by tests.
    budget_warning_sent = models.BooleanField(default=False, null=True)
    error = models.TextField(blank=True, default="")
    condition = models.CharField(max_length=8, choices=CONDITION_CHOICES, null=True, blank=True)
    rule_revision = models.CharField(max_length=64, null=True, blank=True)
    interpretation = models.JSONField(null=True, blank=True)
    delivery_disposition = models.CharField(max_length=32, null=True, blank=True)
    delivery_reason = models.TextField(null=True, blank=True)
    uncertain_to = models.JSONField(null=True, blank=True)
    manual_resolution = models.JSONField(null=True, blank=True)
    simulated_disposition = models.CharField(max_length=32, null=True, blank=True)
    simulated_reason = models.TextField(null=True, blank=True)

    @property
    def condition_label(self):
        return dict(self.CONDITION_CHOICES).get(self.condition, "Not evaluated")

    @property
    def is_legacy(self) -> bool:
        return self.execution_mode in (None, self.MODE_LEGACY)

    def get_delivery_status_display(self) -> str:
        return dict(self.DELIVERY_CHOICES).get(self.delivery_status, "Legacy: delivery uncertain")

    @property
    def outcome_label(self) -> str:
        if self.delivery_disposition == self.DISPOSITION_PAUSED:
            return "Paused for delivery"
        if self.delivery_disposition == "unconfirmed" or (self.pk and self.finished_at is None
                                                         and self.rule_revision):
            return "Unconfirmed"
        if self.delivery_disposition in ("cooldown", "episode_delivered", "duplicate", "stale"):
            return "Suppressed"
        if self.status == self.STATUS_ERROR:
            return "Failed"
        if self.status in (self.STATUS_SKIPPED_BUDGET, self.STATUS_SKIPPED_COOLDOWN):
            return "Suppressed"
        if self.delivery_status in (self.DELIVERY_ACCEPTED, self.DELIVERY_PARTIAL, self.DELIVERY_FAILED):
            return self.get_delivery_status_display()
        if self.delivery_status == self.DELIVERY_ERROR:
            return "Failed"
        return "Evaluated"

    class Meta:
        ordering = ["-started_at", "-pk"]

    def __str__(self) -> str:
        return f"alert-run({self.rule_id}, {self.status}, {self.decision or '-'})"


class AlertEvaluationClaim(models.Model):
    """One durable work claim per trigger; an interrupted claim is not replayed."""
    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE)
    revision = models.CharField(max_length=64)
    key = models.CharField(max_length=200)
    run = models.OneToOneField(AlertRun, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rule", "revision", "key"], name="alert_evaluation_key")]
