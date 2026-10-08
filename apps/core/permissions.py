"""Effective-permission resolution and view guards.

The rules (see the plan's permission spec):

- Instance superuser: everything, everywhere.
- Org admin (direct role, or any permission group with ``org_role="admin"``):
  implicit admin on every studio in the org, plus org management.
- Org member: sees exactly the studios where something grants them a role —
  a direct StudioMembership, a group's per-studio grant, or a group's
  ``default_studio_role`` (which covers all studios in the org).
- Studio roles rank viewer < developer < admin; the effective role is the MAX
  over every applicable source.
- No org membership at all ⇒ the org's URLs 404 (existence is not leaked).
  Insufficient role within the org ⇒ 403.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.db.models import Prefetch
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from apps.core import roles


@dataclass
class EffectiveRoles:
    org_role: str | None = None          # "admin" / "member" / None (not a member)
    is_org_admin: bool = False
    studio_roles: dict[int, str] = field(default_factory=dict)  # explicit grants by studio id
    default_studio_role: str | None = None  # best group-default across all studios
    full_report_studios: set[int] = field(default_factory=set)
    report_ids_by_studio: dict[int, set[int]] = field(default_factory=dict)
    studio_report_ids: dict[int, set[int]] = field(default_factory=dict)
    private_report_ids: dict[int, set[int]] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self.org_role is not None

    def role_for(self, studio) -> str | None:
        """Effective role on one studio (studio must belong to the org)."""
        if not self.is_member:
            return None
        if self.is_org_admin:
            return roles.ADMIN
        return roles.max_role(self.studio_roles.get(studio.pk), self.default_studio_role)

    def report_scope_for(self, studio) -> frozenset[int] | None:
        """Visible report ids, or ``None`` when every report is visible."""
        role = self.role_for(studio)
        if role is None:
            return frozenset()
        if roles.at_least(role, roles.DEVELOPER):
            return None
        if self.default_studio_role or studio.pk in self.full_report_studios:
            private = self.private_report_ids.get(studio.pk)
            if not private:
                return None
            return frozenset(
                (self.studio_report_ids.get(studio.pk, set()) - private)
                | self.report_ids_by_studio.get(studio.pk, set())
            )
        return frozenset(self.report_ids_by_studio.get(studio.pk, ()))

    def has_full_studio_visibility(self, studio) -> bool:
        return self.role_for(studio) is not None and self.report_scope_for(studio) is None


def _bulk_effective_roles(users, org) -> dict[int, EffectiveRoles]:
    """Resolve many users with a bounded membership / report / group walk.

    Callers handle superusers and inactive organizations.
    """
    from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupGrant
    from apps.reports.models import Report
    from apps.studios.models import StudioMembership

    ids = [u.pk for u in users]
    result: dict[int, EffectiveRoles] = {}
    if not ids:
        return result

    for uid, role in OrgMembership.objects.filter(org=org, user_id__in=ids).values_list(
        "user_id", "role"
    ):
        result[uid] = EffectiveRoles(org_role=role, is_org_admin=role == roles.ORG_ADMIN)
    if not result:
        return result

    report_rows = {
        pk: (studio_id, audience)
        for pk, studio_id, audience in Report.objects.filter(studio__org=org).values_list(
            "pk", "studio_id", "audience"
        )
    }
    studio_report_ids: dict[int, set[int]] = {}
    private_report_ids: dict[int, set[int]] = {}
    for pk, (studio_id, audience) in report_rows.items():
        studio_report_ids.setdefault(studio_id, set()).add(pk)
        if audience == Report.AUDIENCE_PRIVATE:
            private_report_ids.setdefault(studio_id, set()).add(pk)
    for er in result.values():
        er.studio_report_ids = studio_report_ids
        er.private_report_ids = private_report_ids

    member_ids = set(result)
    for uid, studio_id, role in StudioMembership.objects.filter(
        user_id__in=member_ids, studio__org=org
    ).values_list("user_id", "studio_id", "role"):
        er = result[uid]
        er.studio_roles[studio_id] = roles.max_role(er.studio_roles.get(studio_id), role)
        er.full_report_studios.add(studio_id)

    groups = (
        PermissionGroup.objects.filter(org=org, memberships__user_id__in=member_ids)
        .distinct()
        .prefetch_related(
            Prefetch("grants", queryset=PermissionGroupGrant.objects.filter(studio__org=org)),
            "grants__report_grants", "memberships",
        )
    )
    for group in groups:
        for m in group.memberships.all():
            er = result.get(m.user_id)
            if er is None:
                continue
            if group.org_role == roles.ORG_ADMIN:
                er.is_org_admin = True
            if group.default_studio_role:
                er.default_studio_role = roles.max_role(
                    er.default_studio_role, group.default_studio_role
                )
            for grant in group.grants.all():
                er.studio_roles[grant.studio_id] = roles.max_role(
                    er.studio_roles.get(grant.studio_id), grant.role
                )
                if grant.role == roles.VIEWER:
                    er.report_ids_by_studio.setdefault(grant.studio_id, set()).update(
                        row.report_id for row in grant.report_grants.all()
                        if row.report_id in report_rows
                        and report_rows[row.report_id][0] == grant.studio_id
                        and (grant.viewer_scope == grant.REPORT_SCOPE_SELECTED
                             or report_rows[row.report_id][1] == Report.AUDIENCE_PRIVATE)
                    )
                if grant.viewer_scope != grant.REPORT_SCOPE_SELECTED or grant.role != roles.VIEWER:
                    er.full_report_studios.add(grant.studio_id)
    return result


def effective_roles(user, org) -> EffectiveRoles:
    """Resolve a user's effective roles with a bounded set of queries."""
    if user is None or not user.is_authenticated or not org.is_active:
        return EffectiveRoles()
    if user.is_superuser:
        return EffectiveRoles(org_role=roles.ORG_ADMIN, is_org_admin=True)
    return _bulk_effective_roles([user], org).get(user.pk) or EffectiveRoles()


def bulk_role_for_studio(users, studio) -> dict:
    """Effective role on ``studio`` for every user in ``users`` -- exactly
    ``{u.pk: effective_roles(u, org).role_for(studio) for u in users}``, but
    computed with a handful of queries total rather than several per user.

    Intended for call sites that used to fan out ``effective_roles`` over a
    whole studio's membership, a whole permission group's membership, or a
    schedule's whole picked-recipients list (see
    ``apps.reports.notify.intersect_with_studio`` and
    ``apps.reports.views.api_studio_members``).

    Returns a ``{user_id: role_or_None}`` dict covering every id in
    ``users`` (``None`` for anyone with no access at all)."""
    users = list(users)
    if not users:
        return {}
    org = studio.org
    result: dict[int, str | None] = {u.pk: None for u in users}
    if not org.is_active:
        return result

    for u in users:
        if u.is_superuser:
            result[u.pk] = roles.ADMIN

    resolved = _bulk_effective_roles([u for u in users if not u.is_superuser], org)
    for uid, er in resolved.items():
        result[uid] = er.role_for(studio)
    return result


def get_effective(request, org) -> EffectiveRoles:
    """Per-request cache around :func:`effective_roles`.

    A request authenticated with an API key (``request.api_key``) resolves
    as a non-member of every org but the key's own, so a key never crosses
    orgs -- even for a user who is a member of both. Both view guards below
    go through here, so this is the one place that rule lives.
    """
    cache = getattr(request, "_bi_effective_roles", None)
    if cache is None:
        cache = request._bi_effective_roles = {}
    if org.pk not in cache:
        key = getattr(request, "api_key", None)
        crosses = key is not None and key.org_id != org.pk
        cache[org.pk] = EffectiveRoles() if crosses else effective_roles(request.user, org)
    return cache[org.pk]


def visible_studios(user, org):
    """Queryset of studios in ``org`` the user may at least view."""
    from apps.studios.models import Studio

    er = effective_roles(user, org)
    qs = Studio.objects.filter(org=org).order_by("slug")
    if not er.is_member:
        return qs.none()
    if er.is_org_admin or er.default_studio_role:
        return qs
    return qs.filter(pk__in=er.studio_roles.keys())


# ── View guards ──────────────────────────────────────────────────────────────

def wants_json(request) -> bool:
    """Would this caller rather have JSON than a page?

    Public because every guard has to answer it the same way rather than
    inventing a second definition of "this is an API call".
    """
    return "/api/" in request.path or request.headers.get("Accept", "").startswith(
        "application/json"
    )


_wants_json = wants_json  # in-module callers below


def _unauthenticated(request):
    if _wants_json(request):
        return JsonResponse({"error": "authentication required"}, status=401)
    return redirect_to_login(request.get_full_path())


def _forbidden(request, why: str = "forbidden"):
    if _wants_json(request):
        return JsonResponse({"error": why}, status=403)
    from django.http import HttpResponseForbidden

    return HttpResponseForbidden(why)


def require_org_role(minimum: str):
    """Guard a view keyed by ``org_slug``. ``minimum``: "member" or "admin".

    Attaches ``request.org`` and ``request.org_roles``.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, org_slug: str, **kwargs):
            from apps.orgs.models import Organization

            if not request.user.is_authenticated:
                return _unauthenticated(request)
            org = get_object_or_404(Organization, slug=org_slug, is_active=True)
            er = get_effective(request, org)
            if not er.is_member:
                raise Http404
            if minimum == roles.ORG_ADMIN and not er.is_org_admin:
                return _forbidden(request, "organization admin required")
            request.org = org
            request.org_roles = er
            return view(request, *args, org_slug=org_slug, **kwargs)

        return wrapper

    return decorator


def require_studio_role(minimum: str):
    """Guard a view keyed by ``org_slug``/``studio_slug``.

    Attaches ``request.org``, ``request.org_roles``, ``request.studio`` and
    ``request.studio_role``.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, org_slug: str, studio_slug: str, **kwargs):
            from apps.orgs.models import Organization
            from apps.studios.models import Studio

            if not request.user.is_authenticated:
                return _unauthenticated(request)
            org = get_object_or_404(Organization, slug=org_slug, is_active=True)
            er = get_effective(request, org)
            if not er.is_member:
                raise Http404
            studio = get_object_or_404(Studio, org=org, slug=studio_slug)
            role = er.role_for(studio)
            if role is None:
                # A member with no grant on this studio: hide its existence.
                raise Http404
            if not roles.at_least(role, minimum):
                return _forbidden(request, f"studio {minimum} required")
            request.org = org
            request.org_roles = er
            request.studio = studio
            request.studio_role = role
            if not er.has_full_studio_visibility(studio):
                from apps.core.report_access import selected_report_access_block_reason

                if selected_report_access_block_reason():
                    if wants_json(request):
                        return JsonResponse(
                            {"error": "selected_report_access_unavailable"}, status=503
                        )
                    return HttpResponse(
                        "Selected report access is temporarily unavailable.",
                        status=503,
                        content_type="text/plain",
                    )
            return view(request, *args, org_slug=org_slug, studio_slug=studio_slug, **kwargs)

        return wrapper

    return decorator


def require_superuser(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _unauthenticated(request)
        if not request.user.is_superuser:
            return _forbidden(request, "instance operator required")
        return view(request, *args, **kwargs)

    return wrapper


def require_operator(view):
    """Guard the cross-organization operator console.

    Broader than an org admin (it spans every org) and narrower than a
    superuser (no Django admin), so support staff can be given the fleet view
    without handing over the database. The console's existence is not
    advertised: non-operators get a 404, not a 403.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _unauthenticated(request)
        if not getattr(request.user, "is_operator", False):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapper
