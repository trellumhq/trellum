"""Current recipient audiences with bounded presentation, shared by editors."""
from django.core.paginator import Paginator
from apps.core import roles
from apps.core.permissions import bulk_role_for_studio
from apps.core.report_access import ReportAccessBatch
from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupMembership
from apps.studios.models import StudioMembership

PAGE_SIZE = 25


def recipient_snapshot(studio, report=None, *, include_inactive=False):
    memberships = OrgMembership.objects.filter(org_id=studio.org_id)
    if not include_inactive:
        memberships = memberships.filter(user__is_active=True)
    users = [row.user for row in memberships.select_related('user')]
    group_memberships = list(PermissionGroupMembership.objects.filter(
        group__org_id=studio.org_id).select_related('user'))
    candidates = {user.pk: user for user in users}
    for membership in group_memberships:
        if membership.user.is_active:
            candidates.setdefault(membership.user_id, membership.user)
    effective = bulk_role_for_studio(candidates.values(), studio)
    access = ReportAccessBatch(candidates.values(), studio.org) if report is not None else None
    eligible = {uid: user for uid, user in candidates.items() if effective.get(uid)
                and (access is None or access.can_view(user, report))}
    org_ids = {user.pk for user in users}
    role_counts = {'admins': 0, 'developers': 0, 'everyone': 0}
    for uid, role in StudioMembership.objects.filter(studio=studio, user__is_active=True).values_list('user_id', 'role'):
        if access is not None and (uid not in eligible or uid not in org_ids):
            continue
        role_counts['everyone'] += 1
        if role == roles.ADMIN:
            role_counts['admins'] += 1
        elif role == roles.DEVELOPER:
            role_counts['developers'] += 1
    counts = {}
    for membership in group_memberships:
        if membership.user.is_active and membership.user_id in eligible:
            gid = membership.group_id
            counts[gid] = counts.get(gid, 0) + 1
    groups = [{'id': group.pk, 'name': group.name, 'count': counts.get(group.pk, 0)}
              for group in PermissionGroup.objects.filter(org_id=studio.org_id).order_by('name', 'pk')
              if report is None or counts.get(group.pk, 0)]
    members = [{'id': user.pk, 'email': user.email, 'name': user.display_name or user.email}
               for user in sorted((user for uid, user in eligible.items() if uid in org_ids), key=lambda user: (user.email, user.pk))]
    return {'members': members, 'groups': groups, 'roles': role_counts, 'studio_name': studio.name,
            'active_people_count': sum(user.is_active for uid, user in eligible.items() if uid in org_ids)}


def selected_ids(values):
    if isinstance(values, str):
        values = values.split(',')
    return {int(value) for value in values if isinstance(value, str)
            and value.isascii() and value.isdigit() and 0 < len(value) < 19}


def page_snapshot(snapshot, *, query='', page=1, people_page=None, group_page=None,
                  selected_people=(), selected_groups=()):
    query = str(query or '')[:200].casefold()
    result = {key: snapshot[key] for key in ('roles', 'studio_name')}
    result['pagination'] = {}
    result['selected'] = {}
    for key, number, ids in [('members', people_page or page, selected_people),
                             ('groups', group_page or page, selected_groups)]:
        all_rows = snapshot[key]
        matches = [row for row in all_rows if query in (row['name'] + ' ' + row.get('email', '')).casefold()]
        current = Paginator(matches, PAGE_SIZE).get_page(number)
        result[key] = list(current.object_list)
        chosen = selected_ids(ids)
        result['selected'][key] = [row for row in all_rows if row['id'] in chosen]
        result['pagination'][key] = {'page': current.number, 'per_page': PAGE_SIZE,
            'total': current.paginator.count, 'choice_total': len(all_rows),
            'audience_total': snapshot['active_people_count'] if key == 'members' else len(all_rows),
            'has_previous': current.has_previous(), 'has_next': current.has_next()}
    return result


def recipient_payload(request, report=None):
    from apps.core.choice_requests import choice_request_params
    parameters = choice_request_params(request)
    return page_snapshot(recipient_snapshot(request.studio, report),
        query=parameters.get('q', ''), page=parameters.get('page', 1),
        selected_people=parameters.get('selected_people', ''),
        selected_groups=parameters.get('selected_groups', ''))
