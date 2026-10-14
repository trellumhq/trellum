"""Bounded, request-local organization and studio authorization facts."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import BooleanField, Case, CharField, Exists, OuterRef, Q, Value, When

from apps.core import roles
from apps.core.request_context import actor_key


@dataclass(frozen=True)
class StudioAccess:
    role: str | None = None
    full: bool = False


@dataclass
class RequestOrgAccess:
    request: object
    org: object
    org_role: str | None = None
    is_org_admin: bool = False
    _studios: dict[int, StudioAccess] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self.org_role is not None

    def studio_access(self, studio) -> StudioAccess:
        if studio.org_id != self.org.pk:
            return StudioAccess()
        if studio.pk not in self._studios:
            self._studios[studio.pk] = _studio_access(self, studio)
        return self._studios[studio.pk]

    def role_for(self, studio) -> str | None:
        return self.studio_access(studio).role

    def has_full_studio_visibility(self, studio) -> bool:
        return self.studio_access(studio).full

    def report_scope_for(self, studio) -> frozenset[int] | None:
        """Compatibility path for callers still requiring complete report IDs.

        This deliberately uses the separate complete cache. The final caller
        migration must remove this fallback from ordinary request paths.
        """
        from apps.core.permissions import get_effective

        self.request._bi_report_scope_fallbacks = getattr(
            self.request, "_bi_report_scope_fallbacks", 0
        ) + 1
        return get_effective(self.request, self.org).report_scope_for(studio)


def request_org_access(request, org) -> RequestOrgAccess:
    cache = getattr(request, "_bi_request_org_access", None)
    if cache is None:
        cache = request._bi_request_org_access = {}
    key = (actor_key(request), org.pk, org.is_active)
    if key not in cache:
        cache[key] = _org_access(request, org)
    return cache[key]


def _org_access(request, org) -> RequestOrgAccess:
    user = request.user
    if not org.is_active or not user.is_authenticated:
        return RequestOrgAccess(request, org)
    api_key = getattr(request, "api_key", None)
    if api_key is not None and api_key.org_id != org.pk:
        return RequestOrgAccess(request, org)
    if user.is_superuser:
        return RequestOrgAccess(request, org, roles.ORG_ADMIN, True)

    from apps.orgs.models import OrgMembership, PermissionGroupMembership

    group_admin = PermissionGroupMembership.objects.filter(
        user=user, group__org_id=OuterRef("org_id"), group__org_role=roles.ORG_ADMIN
    )
    membership = OrgMembership.objects.filter(org=org, user=user).annotate(
        group_admin=Exists(group_admin)
    ).values_list("role", "group_admin").first()
    if membership is None:
        return RequestOrgAccess(request, org)
    role, is_group_admin = membership
    return RequestOrgAccess(request, org, role, role == roles.ORG_ADMIN or is_group_admin)


def _studio_access(access: RequestOrgAccess, studio) -> StudioAccess:
    if not access.is_member:
        return StudioAccess()
    if access.is_org_admin:
        return StudioAccess(roles.ADMIN, True)

    from apps.orgs.models import PermissionGroupGrant, PermissionGroupMembership
    from apps.reports.models import Report
    from apps.studios.models import Studio, StudioMembership

    user, org = access.request.user, access.org
    direct = StudioMembership.objects.filter(user=user, studio_id=OuterRef("pk"))
    groups = PermissionGroupMembership.objects.filter(user=user, group__org=org)
    grants = PermissionGroupGrant.objects.filter(
        group__org=org, group__memberships__user=user, studio_id=OuterRef("pk")
    )
    rows = Studio.objects.filter(pk=studio.pk, org=org).alias(
        _direct_admin=Exists(direct.filter(role=roles.ADMIN)),
        _direct_developer=Exists(direct.filter(role=roles.DEVELOPER)),
        _direct_viewer=Exists(direct.filter(role=roles.VIEWER)),
        _group_admin=Exists(groups.filter(group__default_studio_role=roles.ADMIN)),
        _group_developer=Exists(groups.filter(group__default_studio_role=roles.DEVELOPER)),
        _group_viewer=Exists(groups.filter(group__default_studio_role=roles.VIEWER)),
        _grant_admin=Exists(grants.filter(role=roles.ADMIN)),
        _grant_developer=Exists(grants.filter(role=roles.DEVELOPER)),
        _grant_viewer=Exists(grants.filter(role=roles.VIEWER)),
        _full_path=Exists(direct) | Exists(groups.exclude(group__default_studio_role="").exclude(group__default_studio_role__isnull=True)) | Exists(grants.exclude(viewer_scope="selected", role=roles.VIEWER)),
        _private=Exists(Report.objects.filter(studio_id=OuterRef("pk"), audience=Report.AUDIENCE_PRIVATE)),
    ).annotate(
        access_role=Case(
            When(Q(_direct_admin=True) | Q(_group_admin=True) | Q(_grant_admin=True), then=Value(roles.ADMIN)),
            When(Q(_direct_developer=True) | Q(_group_developer=True) | Q(_grant_developer=True), then=Value(roles.DEVELOPER)),
            When(Q(_direct_viewer=True) | Q(_group_viewer=True) | Q(_grant_viewer=True), then=Value(roles.VIEWER)),
            default=Value(None), output_field=CharField(),
        ),
    ).annotate(
        access_full=Case(
            When(access_role__in=(roles.ADMIN, roles.DEVELOPER), then=Value(True)),
            When(access_role=roles.VIEWER, _full_path=True, _private=False, then=Value(True)),
            default=Value(False), output_field=BooleanField(),
        ),
    ).values_list("access_role", "access_full").first()
    return StudioAccess(*rows) if rows is not None else StudioAccess()
