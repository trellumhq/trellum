"""Report visibility layered on top of organization and studio roles."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.db.models import Exists, OuterRef, Q

from apps.core import roles
from apps.core.permissions import EffectiveRoles, bulk_effective_roles, _forbidden, effective_roles


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
        from apps.core.request_access import request_org_access
        from apps.reports.models import Report

        access = request_org_access(request, report.studio.org)
        if selected_report_access_block_reason() and (
            not access.has_full_studio_visibility(report.studio)
            or report.audience == report.AUDIENCE_PRIVATE
        ):
            return False
        return raw_scope_reports(user, Report.objects.filter(pk=report.pk), request=request).exists()
    else:
        er = effective_roles(user, report.studio.org)
    scope = er.report_scope_for(report.studio)
    if (
        scope is not None or report.audience == report.AUDIENCE_PRIVATE
    ) and selected_report_access_block_reason():
        return False
    return scope is None or report.pk in scope


def raw_scope_reports(user, queryset, *, request=None):
    """Filter by effective report scope, regardless of scoped-access readiness.

    Use for report identity and metadata checks that historically called
    ``EffectiveRoles.report_scope_for``. Availability-filtered listings use
    ``visible_reports``; content checks use ``can_view_report``.
    """
    return _report_scope_queryset(user, queryset, request=request, blocked=False, raw=True)


def visible_reports(user, queryset, *, request=None):
    """Filter a Report queryset to rows visible under current access readiness."""
    return _report_scope_queryset(
        user, queryset, request=request, blocked=bool(selected_report_access_block_reason()), raw=False
    )


def _report_scope_queryset(user, queryset, *, request, blocked, raw):
    """Shared SQL authorization for raw scope and availability-filtered lists."""
    from apps.reports.models import Report

    if request is not None:
        if user is not request.user:
            raise ValueError("request report access must use request.user")
        key = getattr(request, "api_key", None)
        if key is not None:
            queryset = queryset.filter(studio__org_id=key.org_id)
    if user is None or not user.is_authenticated:
        return queryset.none()
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
    if raw:
        # The complete resolver records a full path even for a malformed
        # direct role or grant. A separate valid selected Viewer grant can
        # supply the role while that path admits studio-audience reports.
        queryset = queryset.alias(
            _ra_viewer_role=Exists(scoped_grant.filter(role=roles.VIEWER)),
            _ra_full_path=Exists(studio_member)
            | Exists(group_member.filter(group__default_studio_role__in=(
                roles.VIEWER, roles.DEVELOPER, roles.ADMIN
            )))
            | Exists(scoped_grant.exclude(viewer_scope="selected", role=roles.VIEWER)),
        )
        full_viewer = (
            (Q(_ra_studio_viewer=True) | Q(_ra_group_viewer=True) | Q(_ra_viewer_role=True))
            & Q(_ra_full_path=True, audience=Report.AUDIENCE_STUDIO)
        )
    else:
        full_viewer = (
            Q(_ra_studio_viewer=True) | Q(_ra_group_viewer=True) | Q(_ra_grant_viewer=True)
        ) & Q(audience=Report.AUDIENCE_STUDIO)
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


class ReportAccessBatch:
    """An organization-bound batch resolved from current users, never role input."""

    def __init__(self, users, org) -> None:
        self._org = org
        self._users = {user.pk: user for user in users}
        self._roles: dict[int, EffectiveRoles] = bulk_effective_roles(self._users.values(), org)
        self._blocked = selected_report_access_block_reason()

    def can_view(self, user, report) -> bool:
        if report.studio.org_id != self._org.pk:
            raise ValueError("report does not belong to the resolved organization")
        if user.pk not in self._users or self._users[user.pk] is not user:
            raise ValueError("user was not included in this access batch")
        if not self._org.is_active or not user.is_authenticated:
            return False
        if report.audience == report.AUDIENCE_PRIVATE and self._blocked:
            return False
        if user.is_superuser:
            return True
        scope = self._roles[user.pk].report_scope_for(report.studio)
        return scope is None or (not self._blocked and report.pk in scope)


def bulk_can_view_report(users, report) -> dict[int, bool]:
    """Return current report visibility for every supplied user id."""
    users = list({user.pk: user for user in users}.values())
    if not users:
        return {}
    batch = ReportAccessBatch(users, report.studio.org)
    return {user.pk: batch.can_view(user, report) for user in users}


def require_full_studio_visibility(view):
    """Require an already-resolved studio request to expose all its reports."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.org_roles.has_full_studio_visibility(request.studio):
            return _forbidden(request, "full studio access required")
        return view(request, *args, **kwargs)

    return wrapper
