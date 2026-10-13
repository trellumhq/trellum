"""Report visibility layered on top of organization and studio roles."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.db.models import Exists, F, OuterRef, Q

from apps.core import roles
from apps.core.permissions import bulk_effective_roles, _forbidden, effective_roles, get_effective


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


def can_view_report(user, report, *, request=None) -> bool:
    if request is not None:
        if user is not request.user:
            raise ValueError("request report access must use request.user")
        er = get_effective(request, report.studio.org)
    else:
        er = effective_roles(user, report.studio.org)
    scope = er.report_scope_for(report.studio)
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

    from apps.orgs.models import OrgMembership, PermissionGroupMembership, PermissionGroupGrant
    from apps.studios.models import StudioMembership
    from apps.reports.models import ReportPermissionGrant

    org_member = OrgMembership.objects.filter(user=user, org_id=OuterRef("studio__org_id"))
    studio_member = StudioMembership.objects.filter(user=user, studio_id=OuterRef("studio_id"))
    group_member = PermissionGroupMembership.objects.filter(user=user, group__org_id=OuterRef("studio__org_id"))
    scoped_grant = PermissionGroupGrant.objects.filter(
        group__memberships__user=user, studio_id=OuterRef("studio_id"),
        group__org_id=OuterRef("studio__org_id"))
    queryset = queryset.alias(
        _ra_member=Exists(org_member),
        _ra_org_admin=Exists(org_member.filter(role=roles.ORG_ADMIN)),
        _ra_studio_manager=Exists(studio_member.filter(role__in=(roles.DEVELOPER, roles.ADMIN))),
        _ra_group_org_admin=Exists(group_member.filter(group__org_role=roles.ORG_ADMIN)),
        _ra_group_manager=Exists(group_member.filter(group__default_studio_role__in=(roles.DEVELOPER, roles.ADMIN))),
        _ra_grant_manager=Exists(scoped_grant.filter(role__in=(roles.DEVELOPER, roles.ADMIN))),
        _ra_studio_viewer=Exists(studio_member.filter(role=roles.VIEWER)),
        _ra_group_viewer=Exists(group_member.filter(group__default_studio_role=roles.VIEWER)),
        _ra_grant_viewer=Exists(scoped_grant.filter(role=roles.VIEWER, viewer_scope="all")),
    )
    management = (Q(_ra_org_admin=True) | Q(_ra_studio_manager=True) |
                  Q(_ra_group_org_admin=True) | Q(_ra_group_manager=True) | Q(_ra_grant_manager=True))
    full_viewer = (Q(_ra_studio_viewer=True) | Q(_ra_group_viewer=True) | Q(_ra_grant_viewer=True)) & Q(audience=Report.AUDIENCE_STUDIO)
    if blocked:
        queryset = queryset.alias(
            _has_private_reports=Exists(Report.objects.filter(studio_id=OuterRef("studio_id"), audience=Report.AUDIENCE_PRIVATE)))
        full_viewer &= Q(_has_private_reports=False)
    access = management | full_viewer
    if not blocked:
        explicit = ReportPermissionGrant.objects.filter(
            report_id=OuterRef("pk"), grant__group__memberships__user=user,
            grant__role=roles.VIEWER, grant__studio_id=OuterRef("studio_id"),
            grant__group__org_id=OuterRef("studio__org_id"))
        queryset = queryset.alias(
            _ra_selected=Exists(explicit.filter(grant__viewer_scope="selected")),
            _ra_private=Exists(explicit.filter(grant__viewer_scope="all")))
        access |= Q(_ra_selected=True) | (Q(audience=Report.AUDIENCE_PRIVATE) & Q(_ra_private=True))
    return queryset.filter(studio__org__is_active=True, _ra_member=True).filter(access).distinct()


def bulk_can_view_report(users, report) -> dict[int, bool]:
    """Return current report visibility for every supplied user id."""
    users = list(users)
    if not users:
        return {}
    org = report.studio.org
    blocked = selected_report_access_block_reason()
    if not org.is_active or (report.audience == report.AUDIENCE_PRIVATE and blocked):
        return {user.pk: False for user in users}
    resolved = bulk_effective_roles(users, org)
    result = {}
    for user in users:
        if user.is_authenticated and user.is_superuser:
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
