"""Database-bounded navigation facts; never substitutes for route authorization."""
from dataclasses import dataclass

from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, CharField, Exists, OuterRef, Q, Value, When

from apps.core import roles
from apps.core.request_context import actor_key
from apps.orgs.models import Organization, OrgMembership, PermissionGroupGrant, PermissionGroupMembership
from apps.reports.models import Report
from apps.studios.models import Studio, StudioMembership

PAGE_SIZE = 25


def cached_roles(request, org):
    return getattr(request, '_bi_effective_roles', {}).get((actor_key(request), org.pk, org.is_active))


def organization_choices(request):
    rows = Organization.objects.filter(is_active=True).order_by('slug', 'pk')
    user = request.user
    if not user.is_authenticated:
        return rows.none()
    key = getattr(request, 'api_key', None)
    if key is not None:
        rows = rows.filter(pk=key.org_id)
    if user.is_superuser:
        return rows.annotate(shell_admin=Value(True, output_field=BooleanField()))
    memberships = OrgMembership.objects.filter(user=user, org_id=OuterRef('pk'))
    group_admin = PermissionGroupMembership.objects.filter(
        user=user, group__org_id=OuterRef('pk'), group__org_role=roles.ORG_ADMIN)
    return rows.alias(shell_member=Exists(memberships)).filter(shell_member=True).annotate(
        shell_admin=Exists(memberships.filter(role=roles.ORG_ADMIN)) | Exists(group_admin))


def studio_choices(request, org):
    rows = Studio.objects.filter(org=org).order_by('slug', 'pk')
    if not org.is_active or not request.user.is_authenticated:
        return rows.none()
    key = getattr(request, 'api_key', None)
    if key is not None and key.org_id != org.pk:
        return rows.none()
    er = cached_roles(request, org)
    if er is not None:
        if not er.is_member:
            return rows.none()
        if er.is_org_admin or er.default_studio_role:
            return rows
        # Keep authorization filtering in SQL even if guards already loaded ids.
    if request.user.is_superuser:
        return rows.annotate(shell_role=Value(roles.ADMIN), shell_full=Value(True))
    memberships = OrgMembership.objects.filter(user=request.user, org=org)
    groups = PermissionGroupMembership.objects.filter(user=request.user, group__org=org)
    direct = StudioMembership.objects.filter(user=request.user, studio_id=OuterRef('pk'))
    grants = PermissionGroupGrant.objects.filter(
        group__org=org, group__memberships__user=request.user, studio_id=OuterRef('pk'))
    rows = rows.alias(
        _shell_member=Exists(memberships),
        _shell_admin=Exists(memberships.filter(role=roles.ORG_ADMIN)) | Exists(groups.filter(group__org_role=roles.ORG_ADMIN)),
        _shell_default=Exists(groups.filter(group__default_studio_role__in=[roles.VIEWER, roles.DEVELOPER, roles.ADMIN])),
        _shell_direct=Exists(direct), _shell_grant=Exists(grants),
        _shell_full_path=Exists(direct) | Exists(groups.filter(group__default_studio_role__in=[roles.VIEWER, roles.DEVELOPER, roles.ADMIN])) | Exists(grants.exclude(viewer_scope='selected')),
        _shell_private=Exists(Report.objects.filter(studio_id=OuterRef('pk'), audience=Report.AUDIENCE_PRIVATE)),
    ).filter(_shell_member=True).filter(Q(_shell_admin=True) | Q(_shell_default=True) | Q(_shell_direct=True) | Q(_shell_grant=True))
    for role in (roles.ADMIN, roles.DEVELOPER):
        rows = rows.alias(**{f'_shell_role_{role}': Exists(direct.filter(role=role)) | Exists(grants.filter(role=role)) | Exists(groups.filter(group__default_studio_role=role))})
    return rows.annotate(shell_role=Case(
        When(Q(_shell_admin=True) | Q(**{f'_shell_role_{roles.ADMIN}': True}), then=Value(roles.ADMIN)),
        When(**{f'_shell_role_{roles.DEVELOPER}': True}, then=Value(roles.DEVELOPER)),
        When(Exists(direct.filter(role=roles.VIEWER)) | Exists(grants.filter(role=roles.VIEWER)) | Exists(groups.filter(group__default_studio_role=roles.VIEWER)), then=Value(roles.VIEWER)),
        default=Value(None), output_field=CharField(),
    )).annotate(shell_full=Case(
        When(shell_role__in=[roles.ADMIN, roles.DEVELOPER], then=Value(True)),
        When(shell_role=roles.VIEWER, _shell_full_path=True, _shell_private=False, then=Value(True)),
        default=Value(False), output_field=BooleanField(),
    ))


def studio_facts(request, studio):
    if studio is None:
        return None, False
    er = cached_roles(request, studio.org)
    if er is not None:
        return er.role_for(studio), er.has_full_studio_visibility(studio)
    if hasattr(studio, 'shell_role') and hasattr(studio, 'shell_full'):
        return studio.shell_role, studio.shell_full
    from apps.core.request_access import request_org_access

    access = request_org_access(request, studio.org)
    return access.role_for(studio), access.has_full_studio_visibility(studio)


def can_manage_any_org(request, rows, *, has_more=True):
    if not request.user.is_authenticated or not request.user.is_active:
        return False
    if request.user.is_superuser and getattr(request, 'api_key', None) is None:
        return True
    if any(row.shell_admin for row in rows):
        return True
    return has_more and organization_choices(request).filter(shell_admin=True).exists()


def initial_choices(rows):
    sentinel = list(rows[:PAGE_SIZE + 1])
    return sentinel[:PAGE_SIZE], len(sentinel) > PAGE_SIZE


def selected_choice(rows, choices, *, pk=None, slug=None):
    if pk is None and not slug:
        return None
    match = next((row for row in choices if (row.pk == pk if pk is not None else row.slug == slug)), None)
    return match or rows.filter(**({'pk': pk} if pk is not None else {'slug': slug})).first()


def with_selected(choices, selected):
    return choices if selected is None or any(row.pk == selected.pk for row in choices) else [*choices, selected]


@dataclass
class ChoicePage:
    page: object
    selected: object


def choice_page(rows, *, q='', page=1, selected=''):
    filtered = rows.filter(Q(name__icontains=q) | Q(slug__icontains=q)) if q else rows
    result = Paginator(filtered, PAGE_SIZE).get_page(page)
    result.object_list = list(result.object_list)
    return ChoicePage(result, selected_choice(rows, result.object_list, slug=selected))
