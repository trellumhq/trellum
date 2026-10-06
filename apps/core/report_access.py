"""Report visibility layered on top of organization and studio roles."""
from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.db.models import F, Q

from apps.core import roles
from apps.core.permissions import _forbidden, bulk_role_for_studio, effective_roles


def selected_report_access_block_reason() -> str | None:
    """Why selected-report grants cannot be enforced safely."""
    from apps.core import cdn

    if cdn.access_model() == cdn.MODEL_EDGE_EXTERNAL:
        return "edge-external cannot enforce per-report permissions"
    if not getattr(settings, "TRELLUM_REPORT_SCOPED_ACCESS_READY", False):
        return "TRELLUM_REPORT_SCOPED_ACCESS_READY is not enabled"
    return None


def selected_report_access_ready() -> bool:
    return selected_report_access_block_reason() is None


def selected_report_access_configuration_error() -> str | None:
    """Return an error only when incompatible selected grants already exist."""
    from apps.orgs.models import PermissionGroupGrant

    if not PermissionGroupGrant.objects.filter(viewer_scope="selected").exists():
        return None
    return selected_report_access_block_reason()


def can_view_report(user, report) -> bool:
    scope = effective_roles(user, report.studio.org).report_scope_for(report.studio)
    if scope is not None and selected_report_access_block_reason():
        return False
    return scope is None or report.pk in scope


def visible_reports(user, queryset):
    """Filter a Report queryset to rows visible to ``user`` without N+1 queries."""
    if user is None or not user.is_authenticated:
        return queryset.none()
    if user.is_superuser:
        return queryset.filter(studio__org__is_active=True)

    org_member = Q(studio__org__memberships__user=user)
    full = (
        Q(studio__org__memberships__user=user, studio__org__memberships__role=roles.ORG_ADMIN)
        | Q(studio__memberships__user=user)
        | Q(
            studio__org__permission_groups__memberships__user=user,
            studio__org__permission_groups__org_role=roles.ORG_ADMIN,
        )
        | Q(
            studio__org__permission_groups__memberships__user=user,
            studio__org__permission_groups__default_studio_role__in=(
                roles.VIEWER,
                roles.DEVELOPER,
                roles.ADMIN,
            ),
        )
        | (
            Q(studio__group_grants__group__memberships__user=user)
            & (
                Q(studio__group_grants__viewer_scope="all")
                | Q(studio__group_grants__role__in=(roles.DEVELOPER, roles.ADMIN))
            )
        )
    )
    access = full
    if not selected_report_access_block_reason():
        access |= Q(
            permission_group_grants__grant__group__memberships__user=user,
            permission_group_grants__grant__role=roles.VIEWER,
            permission_group_grants__grant__viewer_scope="selected",
            permission_group_grants__grant__studio_id=F("studio_id"),
            permission_group_grants__grant__group__org_id=F("studio__org_id"),
        )
    return queryset.filter(studio__org__is_active=True).filter(org_member & access).distinct()


def bulk_can_view_report(users, report) -> dict[int, bool]:
    """Return current report visibility for every supplied user id."""
    from apps.orgs.models import PermissionGroupMembership
    from apps.studios.models import StudioMembership

    users = list(users)
    if not users:
        return {}
    ids = {user.pk for user in users}
    role_by_id = bulk_role_for_studio(users, report.studio)
    allowed = {
        uid
        for uid, role in role_by_id.items()
        if roles.at_least(role, roles.DEVELOPER)
    }
    viewer_ids = {uid for uid in ids if role_by_id.get(uid) == roles.VIEWER}
    if viewer_ids:
        allowed.update(
            StudioMembership.objects.filter(
                studio=report.studio, user_id__in=viewer_ids
            ).values_list("user_id", flat=True)
        )
        memberships = PermissionGroupMembership.objects.filter(
            user_id__in=viewer_ids, group__org=report.studio.org
        )
        group_access = (
            Q(group__default_studio_role__in=(roles.VIEWER, roles.DEVELOPER, roles.ADMIN))
            | Q(group__org_role=roles.ORG_ADMIN)
            | Q(group__grants__studio=report.studio, group__grants__viewer_scope="all")
            | Q(
                group__grants__studio=report.studio,
                group__grants__role__in=(roles.DEVELOPER, roles.ADMIN),
            )
        )
        if not selected_report_access_block_reason():
            group_access |= Q(
                group__grants__studio=report.studio,
                group__grants__role=roles.VIEWER,
                group__grants__viewer_scope="selected",
                group__grants__report_grants__report=report,
                group__grants__report_grants__report__studio=report.studio,
            )
        allowed.update(
            memberships.filter(group_access).values_list("user_id", flat=True)
        )
    return {uid: uid in allowed for uid in ids}


def require_full_studio_visibility(view):
    """Require an already-resolved studio request to expose all its reports."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.org_roles.has_full_studio_visibility(request.studio):
            return _forbidden(request, "full studio access required")
        return view(request, *args, **kwargs)

    return wrapper
