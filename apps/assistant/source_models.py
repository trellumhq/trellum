"""Explicit source-read consent and bounded, durable assistant query records."""
from __future__ import annotations

import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone

MAX_RELATIONS = 100
MAX_PRINCIPALS = 500
RELATION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


def validate_relations(value):
    if (
        not isinstance(value, list) or not 1 <= len(value) <= MAX_RELATIONS
        or any(not isinstance(v, str) or not RELATION.fullmatch(v) for v in value)
        or len(set(value)) != len(value)
    ):
        raise ValidationError("Choose 1–100 distinct, explicitly schema-qualified relations.")
    if any(v.split('.')[0].lower() in {"pg_catalog", "information_schema"}
           or v.split('.')[0].lower().startswith("pg_") for v in value):
        raise ValidationError("System relations cannot be granted.")


class SourceReadGrant(models.Model):
    """Opt-in to source reads and egress to the organization's assistant provider."""

    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="source_read_grants")
    datasource = models.ForeignKey("datasources.DataSource", on_delete=models.CASCADE, related_name="assistant_grants")
    enabled = models.BooleanField(default=False)
    relations = models.JSONField(default=list, validators=[validate_relations])
    users = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="source_read_grants")
    groups = models.ManyToManyField("orgs.PermissionGroup", blank=True, related_name="source_read_grants")
    revision = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["datasource__name", "pk"]
        constraints = [models.UniqueConstraint(fields=["studio", "datasource"], name="assistant_source_studio_unique")]

    def clean(self):
        super().clean()
        validate_relations(self.relations)
        if self.studio_id and self.datasource_id:
            ds = self.datasource
            if not (ds.studio_id == self.studio_id or (ds.studio_id is None and ds.org_id == self.studio.org_id)):
                raise ValidationError("The source must belong to this studio or its organization.")
            if ds.type != "postgres" or any(ds.all_fields().get(k) for k in ds.all_fields() if k.startswith("ssh_")):
                raise ValidationError("Only direct PostgreSQL sources support assistant reads.")
        if self.pk:
            if self.groups.exclude(org_id=self.studio.org_id).exists():
                raise ValidationError("Source grant groups must belong to the organization.")
            if self.users.exclude(org_memberships__org_id=self.studio.org_id).exists():
                raise ValidationError("Source grant users must belong to the organization.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


@receiver(m2m_changed, sender=SourceReadGrant.users.through)
@receiver(m2m_changed, sender=SourceReadGrant.groups.through)
def validate_grant_principals(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Validate additions from either side; queryset writes still fail closed at use."""
    if action != "pre_add" or not pk_set:
        return
    from apps.orgs.models import OrgMembership

    is_users = sender is SourceReadGrant.users.through
    grants = SourceReadGrant.objects.filter(pk__in=pk_set).select_related("studio") if reverse else [instance]
    for grant in grants:
        ids = {instance.pk} if reverse else pk_set
        existing = grant.users if is_users else grant.groups
        if existing.count() + len(ids - set(existing.filter(pk__in=ids).values_list("pk", flat=True))) > MAX_PRINCIPALS:
            raise ValidationError("A source grant supports at most 500 principals of each kind.")
        valid = (OrgMembership.objects.filter(org_id=grant.studio.org_id, user_id__in=ids).count()
                 if is_users else model.objects.filter(pk__in=ids, org_id=grant.studio.org_id).count()
                 if not reverse else int(instance.org_id == grant.studio.org_id))
        if valid != len(ids):
            raise ValidationError("Source grant principals must belong to the organization.")


class SourceQuery(models.Model):
    ACCEPTED, RUNNING, SUCCEEDED, FAILED, CANCELLED, UNCONFIRMED = (
        "accepted", "running", "succeeded", "failed", "cancelled", "unconfirmed"
    )
    ACTIVE_STATES = (ACCEPTED, RUNNING)
    STATES = [(s, s) for s in (ACCEPTED, RUNNING, SUCCEEDED, FAILED, CANCELLED, UNCONFIRMED)]
    QUERY, DESCRIBE = "query", "describe"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org = models.ForeignKey("orgs.Organization", on_delete=models.CASCADE, related_name="+")
    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    session = models.ForeignKey("assistant.AssistantSession", on_delete=models.CASCADE, related_name="source_queries")
    grant = models.ForeignKey(SourceReadGrant, on_delete=models.CASCADE, related_name="queries")
    state = models.CharField(max_length=12, choices=STATES, default=ACCEPTED)
    kind = models.CharField(max_length=8, choices=[(QUERY, QUERY), (DESCRIBE, DESCRIBE)])
    sql = models.TextField(blank=True, max_length=16384)
    policy_fingerprint = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=80, blank=True)
    result = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=200, blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    backend_pid = models.IntegerField(null=True, blank=True)
    worker_id = models.CharField(max_length=100, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "accepted_at"], name="assistant_source_pending_idx"),
            models.Index(fields=["state", "lease_expires_at"], name="assistant_source_lease_idx"),
            models.Index(fields=["accepted_at"], condition=~models.Q(idempotency_key=""), name="assistant_source_key_age_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["user", "session", "idempotency_key"], condition=~models.Q(idempotency_key=""), name="assistant_source_request_unique"),
            models.UniqueConstraint(fields=["session"], condition=models.Q(state__in=("accepted", "running")), name="assistant_source_session_active"),
        ]


class SourceExecutionReservation(models.Model):
    """Non-content capacity reservation surviving all source/session cascades."""

    id = models.UUIDField(primary_key=True)
    organization_id = models.BigIntegerField(db_index=True)
    lease_token = models.UUIDField()
    expires_at = models.DateTimeField(db_index=True)
