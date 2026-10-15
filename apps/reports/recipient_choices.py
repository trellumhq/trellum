"""Current recipient audiences with bounded presentation, shared by editors."""
from django.core.paginator import Paginator
from apps.core import roles
from apps.core.permissions import bulk_role_for_studio
from apps.core.report_access import ReportAccessBatch
from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupMembership
from apps.studios.models import StudioMembership

PAGE_SIZE = 25
#: Choice lists up to this size render whole with client-side search, exactly as before the
#: bounded-presentation work. Only a list that exceeds it switches the shared pickers to
#: server-side search (``data-choice-source`` / ``data-recipient-source``).
CHOICE_LIMIT = 500


def cap_model_choices(field, selected=(), limit=None):
    """Materialise a model choice field's options, at most ``limit`` of them.

    Validation keeps using the field's full queryset; only rendering is bounded. Currently
    selected values (``selected`` may be a callable, evaluated only when needed) always stay
    in the rendered options. Returns True when the list was cut.
    """
    from django.forms.models import ModelChoiceIteratorValue

    limit = limit or CHOICE_LIMIT
    rows = list(field.queryset[:limit + 1])
    large = len(rows) > limit
    if large:
        rows = rows[:limit]
        wanted = selected() if callable(selected) else selected
        if isinstance(wanted, (str, int)):
            wanted = [wanted]
        present = {row.pk for row in rows}
        extra = selected_ids([str(value) for value in wanted or ()]) - present
        if extra:
            rows += list(field.queryset.filter(pk__in=extra))
    field.widget.choices = [(ModelChoiceIteratorValue(row.pk, row), field.label_from_instance(row)) for row in rows]
    return large


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


def cap_choices(field, selected=(), limit=None):
    """Bound an already-built plain choice field's rendered options (validation keeps the full set).

    Selected values always stay rendered. Returns True when the list was cut.
    """
    limit = limit or CHOICE_LIMIT
    choices = list(field.choices)
    if len(choices) <= limit:
        return False
    chosen = {str(value) for value in selected or ()}
    field.choices = choices[:limit] + [choice for choice in choices[limit:] if str(choice[0]) in chosen]
    return True


def selected_ids(values):
    if isinstance(values, str):
        values = values.split(',')
    return {int(value) for value in values if isinstance(value, str)
            and value.isascii() and value.isdigit() and 0 < len(value) < 19}


def page_snapshot(snapshot, *, query='', page=1, people_page=None, group_page=None,
                  selected_people=(), selected_groups=(), size=PAGE_SIZE):
    query = str(query or '')[:200].casefold()
    result = {key: snapshot[key] for key in ('roles', 'studio_name')}
    result['pagination'] = {}
    result['selected'] = {}
    for key, number, ids in [('members', people_page or page, selected_people),
                             ('groups', group_page or page, selected_groups)]:
        all_rows = snapshot[key]
        matches = [row for row in all_rows if query in (row['name'] + ' ' + row.get('email', '')).casefold()]
        current = Paginator(matches, size).get_page(number)
        result[key] = list(current.object_list)
        chosen = selected_ids(ids)
        result['selected'][key] = [row for row in all_rows if row['id'] in chosen]
        result['pagination'][key] = {'page': current.number, 'per_page': size,
            'total': current.paginator.count, 'choice_total': len(all_rows),
            'audience_total': snapshot['active_people_count'] if key == 'members' else len(all_rows),
            'has_previous': current.has_previous(), 'has_next': current.has_next()}
    return result


def full_snapshot(snapshot, *, limit=None):
    """Every eligible person and group, as the editors always listed them.

    Only an audience beyond ``limit`` is cut (``truncated``); the shared recipient picker then
    searches server-side through the paged form of the same endpoint.
    """
    limit = limit or CHOICE_LIMIT
    result = {key: snapshot[key] for key in ('roles', 'studio_name')}
    result['truncated'] = len(snapshot['members']) > limit or len(snapshot['groups']) > limit
    result['members'] = snapshot['members'][:limit]
    result['groups'] = snapshot['groups'][:limit]
    return result


def with_selected(rows, snapshot_rows, selected):
    """``rows`` plus any selected rows the cap cut (never an id outside the eligible set)."""
    present = {row['id'] for row in rows}
    chosen = selected_ids(selected) - present
    return rows + [row for row in snapshot_rows if row['id'] in chosen]


def recipient_payload(request, report=None):
    """Whole audience by default (what the delivery drawer loads); the picker's server-side
    search sends ``q``/``page``/``selected_*`` and gets one bounded page."""
    from apps.core.choice_requests import choice_request_params
    parameters = choice_request_params(request)
    snapshot = recipient_snapshot(request.studio, report)
    if all(parameters.get(key) is None for key in ('q', 'page', 'selected_people', 'selected_groups')):
        return full_snapshot(snapshot)
    return page_snapshot(snapshot,
        query=parameters.get('q', ''), page=parameters.get('page', 1),
        selected_people=parameters.get('selected_people', ''),
        selected_groups=parameters.get('selected_groups', ''))
