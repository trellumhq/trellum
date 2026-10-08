"""Report visibility layered on top of organization and studio roles."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.db.models import Exists, F, OuterRef, Q

from apps.core import roles
from apps.core.permissions import _bulk_effective_roles, _forbidden, effective_roles


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
    """Return an error when configured report restrictions cannot be enforced."""
    from apps.orgs.models import PermissionGroupGrant
    from apps.reports.models import Report

    if not (
        PermissionGroupGrant.objects.filter(viewer_scope="selected").exists()
        or Report.objects.filter(audience=Report.AUDIENCE_PRIVATE).exists()
    ):
        return None
    return selected_report_access_block_reason()


def can_view_report(user, report) -> bool:
    scope = effective_roles(user, report.studio.org).report_scope_for(report.studio)
    if (
        scope is not None or report.audience == report.AUDIENCE_PRIVATE
    ) and selected_report_access_block_reason():
        return False
    return scope is None or report.pk in scope


def visible_reports(user, queryset):
    """Filter a Report queryset to rows visible to ``user`` without N+1 queries."""
    from apps.reports.models import Report

    if user is None or not user.is_authenticated:
        return queryset.none()
    blocked = selected_report_access_block_reason()
    if user.is_superuser:
        return queryset.filter(studio__org__is_active=True)

    org_member = Q(studio__org__memberships__user=user)
    management = (
        Q(
            studio__org__memberships__user=user,
            studio__org__memberships__role=roles.ORG_ADMIN,
        )
        | Q(
            studio__memberships__user=user,
            studio__memberships__role__in=(roles.DEVELOPER, roles.ADMIN),
        )
        | Q(
            studio__org__permission_groups__memberships__user=user,
            studio__org__permission_groups__org_role=roles.ORG_ADMIN,
        )
        | Q(
            studio__org__permission_groups__memberships__user=user,
            studio__org__permission_groups__default_studio_role__in=(
                roles.DEVELOPER,
                roles.ADMIN,
            ),
        )
        | (
            Q(studio__group_grants__group__memberships__user=user)
            & Q(studio__group_grants__role__in=(roles.DEVELOPER, roles.ADMIN))
            & Q(studio__group_grants__group__org_id=F("studio__org_id"))
        )
    )
    full_viewer = (
        Q(studio__memberships__user=user, studio__memberships__role=roles.VIEWER)
        | Q(
            studio__org__permission_groups__memberships__user=user,
            studio__org__permission_groups__default_studio_role=roles.VIEWER,
        )
        | Q(
            studio__group_grants__group__memberships__user=user,
            studio__group_grants__role=roles.VIEWER,
            studio__group_grants__viewer_scope="all",
            studio__group_grants__group__org_id=F("studio__org_id"),
        )
    ) & Q(audience=Report.AUDIENCE_STUDIO)
    if blocked:
        queryset = queryset.alias(
            _has_private_reports=Exists(
                Report.objects.filter(
                    studio_id=OuterRef("studio_id"), audience=Report.AUDIENCE_PRIVATE
                )
            )
        )
        full_viewer &= Q(_has_private_reports=False)
    access = management | full_viewer
    if not blocked:
        access |= Q(
            permission_group_grants__grant__group__memberships__user=user,
            permission_group_grants__grant__role=roles.VIEWER,
            permission_group_grants__grant__studio_id=F("studio_id"),
            permission_group_grants__grant__group__org_id=F("studio__org_id"),
        ) & (
            Q(permission_group_grants__grant__viewer_scope="selected")
            | Q(
                audience=Report.AUDIENCE_PRIVATE,
                permission_group_grants__grant__viewer_scope="all",
            )
        )
    return (
        queryset.filter(studio__org__is_active=True)
        .filter(org_member & access)
        .distinct()
    )


def bulk_can_view_report(users, report) -> dict[int, bool]:
    """Return current report visibility for every supplied user id."""
    users = list(users)
    if not users:
        return {}
    org = report.studio.org
    blocked = selected_report_access_block_reason()
    if not org.is_active or (report.audience == report.AUDIENCE_PRIVATE and blocked):
        return {user.pk: False for user in users}
    resolved = _bulk_effective_roles(
        [user for user in users if not user.is_superuser], org
    )
    result = {}
    for user in users:
        if user.is_superuser:
            result[user.pk] = True
            continue
        er = resolved.get(user.pk)
        scope = er.report_scope_for(report.studio) if er else frozenset()
        result[user.pk] = scope is None or (not blocked and report.pk in scope)
    return result


def require_full_studio_visibility(view):
    """Require an already-resolved studio request to expose all its reports."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.org_roles.has_full_studio_visibility(request.studio):
            return _forbidden(request, "full studio access required")
        return view(request, *args, **kwargs)

    return wrapper
