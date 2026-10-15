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
    is_org_admin: bool = False


@dataclass
class RequestOrgAccess:
    request: object
    org: object
    org_role: str | None = None
    _is_org_admin: bool = False
    _actor: tuple = field(default_factory=tuple)
    _org_active: bool = False
    _studios: dict[int, StudioAccess] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self._current_actor and self.org_role is not None

    @property
    def is_org_admin(self) -> bool:
        return self._current_actor and self._is_org_admin

    @property
    def _current_actor(self) -> bool:
        return actor_key(self.request) == self._actor and self.org.is_active == self._org_active

    def studio_access(self, studio) -> StudioAccess:
        if not self._current_actor or studio.org_id != self.org.pk:
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

        if not self._current_actor:
            return frozenset()

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
        access = _org_access(request, org)
        access._actor = key[0]
        access._org_active = org.is_active
        cache[key] = access
    return cache[key]


def _org_access(request, org) -> RequestOrgAccess:
    role, admin = _actor_org_facts(request.user, org, api_key=getattr(request, "api_key", None))
    return RequestOrgAccess(request, org, role, admin)


def _actor_org_facts(user, org, *, api_key=None) -> tuple[str | None, bool]:
    """Current actor facts, with an optional bearer restriction for requests."""
    if not org.is_active or user is None or not user.is_authenticated:
        return None, False
    if api_key is not None and api_key.org_id != org.pk:
        return None, False
    if user.is_superuser:
        return roles.ORG_ADMIN, True

    from apps.orgs.models import OrgMembership, PermissionGroupMembership

    group_admin = PermissionGroupMembership.objects.filter(
        user=user, group__org_id=OuterRef("org_id"), group__org_role=roles.ORG_ADMIN
    )
    membership = OrgMembership.objects.filter(org=org, user=user).annotate(
        group_admin=Exists(group_admin)
    ).values_list("role", "group_admin").first()
    if membership is None:
        return None, False
    role, is_group_admin = membership
    return role, role == roles.ORG_ADMIN or is_group_admin


def actor_studio_access(user, studio, *, org=None) -> StudioAccess:
    """Fresh, non-request actor facts for background/toolbox callers.

    This has no bearer authority and no cross-request cache. HTTP callers use
    ``request_org_access`` to enforce the request's API-key organization.
    """
    org = org if org is not None else studio.org
    role, admin = _actor_org_facts(user, org)
    facts = _studio_access_for_user(user, org, studio, is_member=role is not None, is_admin=admin)
    return StudioAccess(facts.role, facts.full, admin)


def _studio_access(access: RequestOrgAccess, studio) -> StudioAccess:
    return _studio_access_for_user(
        access.request.user, access.org, studio,
        is_member=access.is_member, is_admin=access.is_org_admin,
    )


def _studio_access_for_user(user, org, studio, *, is_member: bool, is_admin: bool) -> StudioAccess:
    if not is_member or studio.org_id != org.pk:
        return StudioAccess()
    if is_admin:
        return StudioAccess(roles.ADMIN, True)

    from apps.orgs.models import PermissionGroupGrant, PermissionGroupMembership
    from apps.reports.models import Report
    from apps.studios.models import Studio, StudioMembership

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
        _full_path=Exists(direct) | Exists(groups.filter(group__default_studio_role__in=(roles.VIEWER, roles.DEVELOPER, roles.ADMIN))) | Exists(grants.exclude(viewer_scope="selected", role=roles.VIEWER)),
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
