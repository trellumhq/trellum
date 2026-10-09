"""AI assistant persistence: conversations and LLM spend.

The legacy portal kept assistant sessions in a module-level dict plus JSON
snapshots under ``output/assistant_sessions/<email>/``, and per-user spend in
``output/chat_budget.json``. Both are rows here instead: sessions survive a
restart, and spend is aggregated per (org, user, month) so an org admin can
cap what its own API key is billed.

``AssistantSession.state`` keeps the exact shape the legacy snapshots stored —
``{"transcript": [...], "usage": {...}}`` — so the transcript blocks, the
widget's rendering and the provider-agnostic replay all stay unchanged.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

#: Storage precision of ``LlmUsage.cost_usd`` (Decimal 11,6). A single cheap
#: call costs well under a hundredth of a cent; at four places those were
#: rounded to zero and never booked.
CENT = Decimal("0.000001")


def default_state() -> dict:
    """Fresh session state — same keys the legacy JSON snapshot carried."""
    return {
        "transcript": [],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cost_usd": 0.0,
        },
    }


def month_start(when=None) -> date:
    """First of the month (UTC) that a charge is booked against."""
    when = when or timezone.now()
    d = when.date() if hasattr(when, "date") else when
    return d.replace(day=1)


class AssistantSession(models.Model):
    """One AI assistant conversation, private to the user who started it."""

    SCOPE_FULL = "full"
    SCOPE_REPORT = "report"
    SCOPE_CHOICES = ((SCOPE_FULL, "Full studio"), (SCOPE_REPORT, "One report"))

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assistant_sessions"
    )
    org = models.ForeignKey(
        "orgs.Organization", on_delete=models.CASCADE, related_name="assistant_sessions"
    )
    studio = models.ForeignKey(
        "studios.Studio", null=True, blank=True, on_delete=models.CASCADE,
        related_name="assistant_sessions",
    )
    # The report the conversation started from, when it started on a report
    # page. SET_NULL: deleting a report must not delete its conversations.
    report = models.ForeignKey(
        "reports.Report", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assistant_sessions",
    )
    # The alert evaluation this conversation opened from (the email's "open in
    # the assistant" link), so history shows where it came from and the prompt
    # can carry the run's evidence. SET_NULL: the conversation outlives the run.
    alert_run = models.ForeignKey(
        "alerts.AlertRun", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assistant_sessions",
    )
    scope = models.CharField(max_length=8, choices=SCOPE_CHOICES, default=SCOPE_FULL)
    title = models.CharField(max_length=200, blank=True)
    state = models.JSONField(default=default_state, blank=True)
    # Set while a turn streams so a second tab cannot start another on the
    # same conversation (409). Expires with the turn deadline, so a worker
    # that died mid-stream never wedges the session.
    in_flight_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "-updated_at"], name="assistant_user_recent_idx")]

    def __str__(self) -> str:
        return f"AssistantSession({self.pk}, {self.user_id})"

    def save(self, *args, **kwargs):
        if self.scope == self.SCOPE_REPORT and self._state.adding and self.report_id is None:
            raise ValidationError({"report": "A report-scoped session requires a report."})
        if self.report_id and self.studio_id:
            from apps.reports.models import Report

            if not Report.objects.filter(pk=self.report_id, studio_id=self.studio_id).exists():
                raise ValidationError({"report": "The report must belong to the session's studio."})
        if not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values("scope", "report_id").first()
            if original and (
                original["scope"] != self.scope or original["report_id"] != self.report_id
            ):
                raise ValidationError("A conversation's scope and report cannot be changed.")
        super().save(*args, **kwargs)

    # ── State accessors ──────────────────────────────────────────────────
    @property
    def transcript(self) -> list:
        state = self.state or {}
        return state.setdefault("transcript", [])

    @property
    def usage(self) -> dict:
        state = self.state or {}
        return state.setdefault("usage", default_state()["usage"])

    # ── API shapes (legacy handler contract — assistant.js depends on these) ──
    def to_summary(self) -> dict:
        return {
            "id": self.pk,
            "title": self.title,
            "scope": self.scope,
            "report": self.report.slug if self.report_id and self.report else None,
            "created_at": self.created_at.timestamp() if self.created_at else None,
            "updated_at": self.updated_at.timestamp() if self.updated_at else None,
            "message_count": sum(
                1 for m in self.transcript if m.get("role") in ("user", "assistant")
            ),
            "cost_usd": (
                round(float(self.usage.get("cost_usd", 0.0)), 4)
                if self.usage.get("cost_usd", 0.0) is not None else None
            ),
            # The panel pins "About alert: <title>" above a seeded conversation.
            "alert_title": (
                (self.alert_run.title or self.alert_run.rule.name) if self.alert_run_id else None
            ),
        }

    def to_full(self) -> dict:
        return {
            **self.to_summary(),
            "transcript": self.transcript,
            "usage": self.usage,
        }


def infer_title(first_user_message: str) -> str:
    """Session title heuristic, ported verbatim from the legacy portal."""
    s = (first_user_message or "").strip().replace("\n", " ")
    return (s[:50] + "…") if len(s) > 50 else s or "Untitled session"


class LlmUsage(models.Model):
    """Per (org, user, month) LLM spend — the budget ledger.

    One row per user per month keeps enforcement to two cheap aggregate
    queries and makes "what did this org spend in March" answerable without
    walking every session.
    """

    org = models.ForeignKey(
        "orgs.Organization", on_delete=models.CASCADE, related_name="llm_usage"
    )
    #: SET_NULL, not CASCADE: this is a billing ledger, and
    #: docs/customer/operations/data-retention.md promises spend records are
    #: never purged -- transcripts age out, what they cost does not. The FK
    #: was CASCADE, so any ``user.delete()`` (the Django admin registers User,
    #: so one click did it) silently took the org's spend history with it.
    #: The (org, user, month) uniqueness stops applying once user is NULL,
    #: which is harmless: nothing ever creates a row for a NULL user, and
    #: add_cost() only reaches rows it looked up by a real user.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="llm_usage",
    )
    month = models.DateField(help_text="First day of the month this spend is booked to.")
    cost_usd = models.DecimalField(max_digits=11, decimal_places=6, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "user", "month"], name="uniq_llm_usage_month")
        ]
        ordering = ["-month"]

    def __str__(self) -> str:
        return f"LlmUsage({self.org_id}, {self.user_id}, {self.month}, ${self.cost_usd})"

    # ── Helpers ──────────────────────────────────────────────────────────
    @classmethod
    def add_cost(cls, org, user, usd, when=None) -> Decimal:
        """Book a charge and return the user's new month-to-date total.

        Sub-$0.000001 charges round to zero and are skipped; the row is created
        on first charge so a user who never asks anything costs no storage.
        """
        amount = Decimal(str(usd or 0)).quantize(CENT, rounding=ROUND_HALF_UP)
        if amount <= 0:
            return cls.user_month_total(org, user, when=when)
        month = month_start(when)
        with transaction.atomic():
            row, _ = cls.objects.select_for_update().get_or_create(
                org=org, user=user, month=month, defaults={"cost_usd": Decimal("0")}
            )
            row.cost_usd = (row.cost_usd or Decimal("0")) + amount
            row.save(update_fields=["cost_usd", "updated_at"])
        return row.cost_usd

    @classmethod
    def month_total(cls, org, when=None) -> Decimal:
        total = cls.objects.filter(org=org, month=month_start(when)).aggregate(
            total=Sum("cost_usd")
        )["total"]
        return total or Decimal("0")

    @classmethod
    def user_month_total(cls, org, user, when=None) -> Decimal:
        total = cls.objects.filter(org=org, user=user, month=month_start(when)).aggregate(
            total=Sum("cost_usd")
        )["total"]
        return total or Decimal("0")


class ProposedAction(models.Model):
    """A mutating tool call the model made, waiting for the user's decision.

    The model never executes an action: the toolbox writes this row, the
    panel shows a card, and the approve endpoint runs it under the user's
    *current* role. ``arguments`` never holds a secret -- the card collects
    ``secret_fields`` and hands them straight to the approve request.
    """

    PROPOSED, APPROVED, REJECTED, EXPIRED, EXECUTED, FAILED = (
        "proposed", "approved", "rejected", "expired", "executed", "failed",
    )
    STATUSES = [(s, s) for s in (PROPOSED, APPROVED, REJECTED, EXPIRED, EXECUTED, FAILED)]
    #: How long a card stays approvable. A constant: a proposal is a reply
    #: to a question the user just asked, not a ticket.
    EXPIRY = timedelta(hours=24)

    session = models.ForeignKey(
        AssistantSession, on_delete=models.CASCADE, related_name="proposals"
    )
    org = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="+")
    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tool = models.CharField(max_length=64)
    summary = models.CharField(max_length=300)
    arguments = models.JSONField(default=dict, blank=True)
    secret_fields = models.JSONField(default=list, blank=True)
    required_role = models.CharField(max_length=16)
    status = models.CharField(max_length=16, choices=STATUSES, default=PROPOSED)
    result = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ProposedAction({self.pk}, {self.tool}, {self.status})"

    def to_event(self) -> dict:
        """The card's payload: the SSE ``proposal`` frame and the transcript copy."""
        return {
            "id": self.pk,
            "tool": self.tool,
            "summary": self.summary,
            "arguments": self.arguments,
            "secret_fields": self.secret_fields,
            "required_role": self.required_role,
            "status": self.status,
            "result": self.result,
            "expires_at": self.expires_at.isoformat(),
        }
