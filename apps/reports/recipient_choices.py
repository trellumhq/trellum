"""Current recipient audiences with bounded presentation, shared by editors."""
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import CharField, Count, Exists, OuterRef, Q, Value
from django.db.models.functions import Coalesce, Concat, NullIf
from apps.core import report_access, roles
from apps.core.limits import INLINE_LIMIT
from apps.core.permissions import bulk_role_for_studio
from apps.core.report_access import ReportAccessBatch
from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupGrant, PermissionGroupMembership
from apps.reports.models import Report, ReportPermissionGrant
from apps.studios.models import StudioMembership

PAGE_SIZE = 25
#: Choice lists up to this size render whole with client-side search, exactly as before the
#: bounded-presentation work. Only a list that exceeds it switches the shared pickers to
#: server-side search (``data-choice-source`` / ``data-recipient-source``).
CHOICE_LIMIT = INLINE_LIMIT


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


# ── Search, paging and the whole-audience list, in SQL ───────────────────────
#
# The picker's server search runs on every keystroke, so it must not load the
# organization. ``recipient_snapshot`` above resolves every candidate in Python
# and stays for the delivery editor, which validates against the whole audience
# once per page view. Everything below asks the database for one page, and for
# the counts the response reports, under the same rules: ``_eligible`` is the
# SQL form of ``bulk_role_for_studio`` (who has any role on the studio) and,
# given a report, of ``ReportAccessBatch.can_view`` (who may also see it).
# apps/reports/tests/test_recipient_choices.py proves the two agree.

_ALL_ROLES = (roles.VIEWER, roles.DEVELOPER, roles.ADMIN)
_MANAGER_ROLES = (roles.DEVELOPER, roles.ADMIN)


def _eligibility(studio, report):
    """``eligible(user_ref)``: a filter keeping rows whose ``user_ref`` column names a person
    with a role on ``studio`` (and, with a ``report``, a role that can see it).
    Instance superusers count even without membership."""
    org = studio.org
    blocked = bool(report_access.selected_report_access_block_reason())
    if not org.is_active or (report is not None and blocked and report.audience == Report.AUDIENCE_PRIVATE):
        return lambda user_ref: Q(pk__in=[])
    has_private = report is not None and Report.objects.filter(
        studio_id=studio.pk, audience=Report.AUDIENCE_PRIVATE).exists()

    def eligible(user_ref):
        ref = OuterRef(user_ref)
        superuser = Exists(get_user_model().objects.filter(pk=ref, is_superuser=True))
        org_member = OrgMembership.objects.filter(org_id=org.pk, user_id=ref)
        groups = PermissionGroupMembership.objects.filter(group__org_id=org.pk, user_id=ref)
        direct = StudioMembership.objects.filter(studio_id=studio.pk, user_id=ref)
        grants = PermissionGroupGrant.objects.filter(
            studio_id=studio.pk, group__org_id=org.pk, group__memberships__user_id=ref)
        org_admin = (Exists(org_member.filter(role=roles.ORG_ADMIN))
                     | Exists(groups.filter(group__org_role=roles.ORG_ADMIN)))
        default_role = Exists(groups.filter(group__default_studio_role__in=_ALL_ROLES))
        any_role = (org_admin | Exists(direct.filter(role__in=_ALL_ROLES))
                    | Exists(grants.filter(role__in=_ALL_ROLES)) | default_role)
        if report is None:
            return Q(superuser | (Exists(org_member) & any_role))

        manager = (org_admin | Exists(direct.filter(role__in=_MANAGER_ROLES))
                   | Exists(grants.filter(role__in=_MANAGER_ROLES))
                   | Exists(groups.filter(group__default_studio_role__in=_MANAGER_ROLES)))
        assigned = ReportPermissionGrant.objects.filter(
            report_id=report.pk, grant__studio_id=studio.pk, grant__role=roles.VIEWER,
            grant__group__org_id=org.pk, grant__group__memberships__user_id=ref)
        private = report.audience == Report.AUDIENCE_PRIVATE
        if not private:
            assigned = assigned.filter(grant__viewer_scope=PermissionGroupGrant.REPORT_SCOPE_SELECTED)
        assigned = Exists(assigned)
        # A Viewer who holds the studio through a default role, a membership, or a grant that
        # is not "selected reports" sees every non-private report; any other Viewer sees only
        # the reports a group assigned. A studio with private reports needs an assignment for
        # those, and while selected-report access is blocked only a studio without private
        # reports is visible to Viewers at all.
        wide = default_role | Exists(direct) | Exists(grants.exclude(
            viewer_scope=PermissionGroupGrant.REPORT_SCOPE_SELECTED, role=roles.VIEWER))
        visible = manager
        if not has_private:
            visible = visible | (any_role & wide)
        elif not blocked:
            visible = visible | (any_role & wide & assigned if private else any_role & wide)
        if not blocked:
            visible = visible | (any_role & ~wide & assigned)
        return Q(superuser | (Exists(org_member) & visible))

    return eligible


def _members(studio, eligible, include_inactive):
    """Eligible org members, ordered like the snapshot's member list."""
    rows = OrgMembership.objects.filter(org_id=studio.org_id)
    if not include_inactive:
        rows = rows.filter(user__is_active=True)
    return rows.filter(eligible('user_id')).select_related('user').order_by('user__email', 'user_id')


def _member_row(membership):
    user = membership.user
    return {'id': user.pk, 'email': user.email, 'name': user.display_name or user.email}


def _groups(studio, report, eligible):
    """The org's groups; with a report, only those with someone who can see it."""
    rows = PermissionGroup.objects.filter(org_id=studio.org_id)
    if report is not None:
        people = PermissionGroupMembership.objects.filter(group_id=OuterRef('pk'), user__is_active=True)
        rows = rows.filter(Exists(people.filter(eligible('user_id'))))
    return rows.order_by('name', 'pk')


def _group_rows(groups, eligible):
    counts = dict(PermissionGroupMembership.objects.filter(
        group_id__in=[group.pk for group in groups], user__is_active=True,
    ).filter(eligible('user_id')).order_by().values('group_id').annotate(total=Count('pk')).values_list(
        'group_id', 'total'))
    return [{'id': group.pk, 'name': group.name, 'count': counts.get(group.pk, 0)} for group in groups]


def _role_counts(studio, report, eligible):
    rows = StudioMembership.objects.filter(studio=studio, user__is_active=True)
    if report is not None:
        rows = rows.filter(eligible('user_id')).filter(Exists(
            OrgMembership.objects.filter(org_id=studio.org_id, user_id=OuterRef('user_id'))))
    totals = rows.aggregate(everyone=Count('pk'), admins=Count('pk', filter=Q(role=roles.ADMIN)),
                            developers=Count('pk', filter=Q(role=roles.DEVELOPER)))
    return {'admins': totals['admins'], 'developers': totals['developers'], 'everyone': totals['everyone']}


def recipient_page(studio, report=None, *, include_inactive=False, query='', page=1, people_page=None,
                   group_page=None, selected_people=(), selected_groups=(), size=PAGE_SIZE):
    """One page of people and one of groups matching ``query``, plus the selected ones.

    The payload of slicing ``recipient_snapshot`` in memory, but each search, page and count is
    a bounded query: the cost does not grow with the organization.
    """
    query = str(query or '')[:200]
    eligible = _eligibility(studio, report)
    members = _members(studio, eligible, include_inactive)
    groups = _groups(studio, report, eligible)
    matched_members, matched_groups = members, groups
    if query:
        # Matches what the in-memory search did: "name email" for people, "name " for groups.
        matched_members = members.annotate(_find=Concat(
            Coalesce(NullIf('user__name', Value('')), 'user__email', output_field=CharField()),
            Value(' '), 'user__email',
            output_field=CharField())).filter(_find__icontains=query)
        matched_groups = groups.annotate(_find=Concat('name', Value(' '), output_field=CharField())).filter(
            _find__icontains=query)
    result = {'roles': _role_counts(studio, report, eligible), 'studio_name': studio.name}
    result['pagination'] = {}
    result['selected'] = {}
    active_people = members.filter(user__is_active=True).count()
    for key, number, ids, matched, everything in [
            ('members', people_page or page, selected_people, matched_members, members),
            ('groups', group_page or page, selected_groups, matched_groups, groups)]:
        current = Paginator(matched, size).get_page(number)
        rows = list(current.object_list)
        chosen = selected_ids(ids)
        picked = []
        if chosen:
            picked = list(everything.filter(**{'user_id__in' if key == 'members' else 'pk__in': chosen}))
        if key == 'members':
            result[key] = [_member_row(row) for row in rows]
            result['selected'][key] = [_member_row(row) for row in picked]
        else:
            counted = {row['id']: row for row in _group_rows(rows + [g for g in picked if g not in rows], eligible)}
            result[key] = [counted[group.pk] for group in rows]
            result['selected'][key] = [counted[group.pk] for group in picked]
        everything_total = everything.count()
        result['pagination'][key] = {'page': current.number, 'per_page': size,
            'total': current.paginator.count, 'choice_total': everything_total,
            'audience_total': active_people if key == 'members' else everything_total,
            'has_previous': current.has_previous(), 'has_next': current.has_next()}
    return result


def recipient_full(studio, report=None, *, include_inactive=False, limit=None):
    """Every eligible person and group, as the editors always listed them.

    Only an audience beyond ``limit`` is cut (``truncated``); the shared recipient picker then
    searches server-side through the paged form of the same endpoint.
    """
    limit = limit or CHOICE_LIMIT
    eligible = _eligibility(studio, report)
    members = list(_members(studio, eligible, include_inactive)[:limit + 1])
    groups = list(_groups(studio, report, eligible)[:limit + 1])
    return {'roles': _role_counts(studio, report, eligible), 'studio_name': studio.name,
            'truncated': len(members) > limit or len(groups) > limit,
            'members': [_member_row(row) for row in members[:limit]],
            'groups': _group_rows(groups[:limit], eligible)}


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
    if all(parameters.get(key) is None for key in ('q', 'page', 'selected_people', 'selected_groups')):
        return recipient_full(request.studio, report)
    return recipient_page(request.studio, report,
        query=parameters.get('q', ''), page=parameters.get('page', 1),
        selected_people=parameters.get('selected_people', ''),
        selected_groups=parameters.get('selected_groups', ''))
