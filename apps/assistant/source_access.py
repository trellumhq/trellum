"""Fresh source-specific authorization, independent of toolbox prechecks."""
from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef, Q

from apps.assistant.models import AssistantSession, SourceReadGrant
from apps.assistant.source_models import MAX_PRINCIPALS, validate_relations
from apps.core.request_access import actor_studio_access
from apps.orgs.models import OrgAssistantConfig, PermissionGroupMembership
from apps.studios.models import Studio

DENIED = "Source read access is unavailable."
STUDIO_CONVERSATION = "Start a studio conversation to explore data sources."


def eligible_context(studio, actor, *, session=None):
    """Load current database authority; never trust a cached user/session/config."""
    if session is None or not getattr(session, "pk", None):
        raise PermissionDenied("Source reads require a persisted studio conversation; API-key and MCP calls are unavailable.")
    if not getattr(actor, "pk", None) or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied(DENIED)
    user = get_user_model().objects.filter(pk=actor.pk, is_active=True).first()
    current = Studio.objects.select_related("org").filter(pk=studio.pk).first()
    if user is None or current is None or not current.org.is_active:
        raise PermissionDenied(DENIED)
    conversation = AssistantSession.objects.filter(pk=session.pk, user=user, studio=current, org_id=current.org_id).first()
    if conversation is None:
        raise PermissionDenied(DENIED)
    if conversation.scope != AssistantSession.SCOPE_FULL or conversation.report_id is not None:
        raise PermissionDenied(STUDIO_CONVERSATION)
    access = actor_studio_access(user, current, org=current.org)
    if not access.role or not access.full:
        raise PermissionDenied(DENIED)
    if not OrgAssistantConfig.objects.filter(org_id=current.org_id, enabled=True).exists():
        raise PermissionDenied(DENIED)
    return current, user, conversation


def grants_for(studio, actor, *, session=None):
    """Eligible grants, SQL filtered and ordered; callers must slice their list."""
    studio, actor, _ = eligible_context(studio, actor, session=session)
    group_member = PermissionGroupMembership.objects.filter(
        user=actor, group__org_id=studio.org_id, group__source_read_grants=OuterRef("pk")
    )
    direct = SourceReadGrant.users.through.objects.filter(
        sourcereadgrant_id=OuterRef("pk"), user_id=actor.pk
    )
    return (SourceReadGrant.objects.filter(studio=studio, enabled=True, datasource__type="postgres")
            .filter(Q(datasource__studio=studio) | Q(datasource__studio__isnull=True, datasource__org_id=studio.org_id))
            .alias(_direct=Exists(direct), _group=Exists(group_member))
            .filter(Q(_direct=True) | Q(_group=True))
            .select_related("studio__org__assistant_config", "datasource")
            .prefetch_related("users", "groups").order_by("datasource__name", "pk"))


def resolve_grant(studio, actor, grant_id, *, session=None):
    try:
        grant = grants_for(studio, actor, session=session).filter(pk=grant_id).first()
    except (ValueError, TypeError, ValidationError):
        raise PermissionDenied(DENIED) from None
    if grant is None:
        raise PermissionDenied(DENIED)
    try:
        validate_relations(grant.relations)
        fields = grant.datasource.all_fields()
        if any(value for key, value in fields.items() if key.startswith("ssh_")):
            raise PermissionDenied(DENIED)
    except ValidationError:
        raise PermissionDenied(DENIED) from None
    return grant


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hmac.new(settings.SECRET_KEY.encode(), encoded, hashlib.sha256).hexdigest()


def policy_fingerprint(grant):
    """Opaque policy pin; callers must resolve a fresh grant before using it."""
    users = sorted(u.pk for u in grant.users.all())
    groups = sorted(g.pk for g in grant.groups.all())
    if len(users) > MAX_PRINCIPALS or len(groups) > MAX_PRINCIPALS:
        raise PermissionDenied(DENIED)
    try:
        provider = grant.studio.org.assistant_config
    except OrgAssistantConfig.DoesNotExist:
        raise PermissionDenied(DENIED) from None
    config = {name: getattr(provider, name) for name in (
        "enabled", "provider", "model", "base_url", "auth_mode", "config_revision",
        "api_key", "cloud_config", "cloud_credentials",
    )}
    if not config or not config["enabled"]:
        raise PermissionDenied(DENIED)
    ds = grant.datasource
    return _digest({
        "grant": grant.pk, "studio": grant.studio_id, "enabled": grant.enabled,
        "revision": grant.revision, "relations": sorted(grant.relations),
        "users": users, "groups": groups, "provider": _digest(config),
        "datasource": {"id": ds.pk, "studio": ds.studio_id, "org": ds.org_id,
                       "type": ds.type, "updated_at": ds.updated_at,
                       "config": _digest(ds.config), "credentials": _digest(ds.credentials)},
    })
