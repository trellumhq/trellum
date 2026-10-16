"""Bounded data preparation for the permission-group screens.

Everything here is invisible to users at realistic sizes: the screens render
every studio, report and member like before, and only truncate far above that.
"""
from collections import defaultdict

from django.core.paginator import Paginator
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from apps.core.limits import INLINE_LIMIT
from apps.orgs.models import OrgMembership, PermissionGroupGrant, PermissionGroupMembership
from apps.reports.models import Report, ReportPermissionGrant
from apps.studios.models import Studio

CHOICE_SIZE = 25
CHOICE_LIMIT = INLINE_LIMIT  # lists and tables that render unpaginated
STUDIO_LIMIT = 200  # per-studio cards
REPORT_FIELDS = ("pk", "studio_id", "name", "slug", "kind", "audience", "present_in_scan", "disabled")


def report_ids(values):
    result = []
    for value in values:
        try:
            result.append(Report._meta.pk.get_prep_value(value))
        except (TypeError, ValueError, OverflowError):
            continue
    return result


def group_summary_counts(queryset):
    def count(model):
        rows = model.objects.filter(group_id=OuterRef("pk")).values("group_id").annotate(total=Count("pk")).values("total")
        return Coalesce(Subquery(rows, output_field=IntegerField()), 0)
    return queryset.annotate(grant_count=count(PermissionGroupGrant), member_count=count(PermissionGroupMembership))


def window(request, rows, key, size=CHOICE_SIZE, *, parameters=None):
    number = parameters.get(key, 1) if parameters is not None else request.POST.get(key, request.GET.get(key, 1))
    return Paginator(rows, size).get_page(number)


def text(request, key):
    return (request.POST.get(key, request.GET.get(key, "")) or "").strip()


def member_choices(org, group, kind, query=""):
    rows = OrgMembership.objects.filter(org=org).select_related("user")
    if kind == "effective":
        rows = rows.filter(user__permission_group_memberships__group=group)
    else:
        rows = rows.exclude(user__permission_group_memberships__group=group)
    if query:
        rows = rows.filter(Q(user__email__icontains=query) | Q(user__name__icontains=query))
    return rows.order_by("user__email", "user_id")


def report_choices(studio, query=""):
    rows = Report.objects.filter(studio=studio).only(*REPORT_FIELDS)
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(slug__icontains=query))
    return rows.order_by("name", "slug", "pk")


def pagination(page):
    return {"page": page.number, "per_page": page.paginator.per_page,
            "total": page.paginator.count, "has_previous": page.has_previous(), "has_next": page.has_next()}


def limited(queryset, limit=None):
    """Return ``(rows, more)`` using one extra row to detect truncation."""
    limit = CHOICE_LIMIT if limit is None else limit
    rows = list(queryset[: limit + 1])
    return rows[:limit], len(rows) > limit


def access_rows(org, group, form_factory, access_form=None):
    """One card per studio, rendered from a handful of queries.

    ``form_factory(studio, initial)`` builds the unbound per-studio form; a
    bound ``access_form`` replaces the card of the studio it was posted for.
    Studio and report lists are capped far above realistic sizes; a studio
    with more reports than the cap switches its picker to server-side search
    (``remote_reports``) while still rendering every selected report.
    """
    studios, studios_more = limited(Studio.objects.filter(org=org).order_by("slug", "pk"), STUDIO_LIMIT)
    posted = getattr(access_form, "studio", None)
    if posted is not None and all(studio.pk != posted.pk for studio in studios):
        studios.append(posted)
    studio_ids = [studio.pk for studio in studios]
    grants = {grant.studio_id: grant for grant in group.grants.filter(studio_id__in=studio_ids)}
    selected_by_grant = defaultdict(list)
    for grant_id, report_id in ReportPermissionGrant.objects.filter(grant__group=group).values_list("grant_id", "report_id"):
        selected_by_grant[grant_id].append(report_id)

    totals = dict(
        Report.objects.filter(studio_id__in=studio_ids).order_by().values("studio_id").annotate(total=Count("pk")).values_list("studio_id", "total")
    )
    remote = {pk for pk in studio_ids if totals.get(pk, 0) > CHOICE_LIMIT}
    catalog = defaultdict(list)
    for report in Report.objects.filter(studio_id__in=[pk for pk in studio_ids if pk not in remote]).only(*REPORT_FIELDS).order_by("name", "slug", "pk"):
        catalog[report.studio_id].append(report)

    def posted_ids(studio):
        if access_form is not None and posted is not None and posted.pk == studio.pk:
            return report_ids(access_form["reports"].value() or [])
        return []

    for studio in studios:
        if studio.pk not in remote:
            continue
        grant = grants.get(studio.pk)
        wanted = set(selected_by_grant.get(grant.pk, []) if grant else []) | set(posted_ids(studio))
        first = list(Report.objects.filter(studio=studio).only(*REPORT_FIELDS).order_by("name", "slug", "pk")[:CHOICE_LIMIT])
        known = {report.pk for report in first}
        extra = Report.objects.filter(studio=studio, pk__in=wanted - known).only(*REPORT_FIELDS).order_by("name", "slug", "pk")
        catalog[studio.pk] = first + list(extra)

    private = defaultdict(list)
    for report in (
        Report.objects.filter(
            studio_id__in=studio_ids, audience=Report.AUDIENCE_PRIVATE,
            permission_group_grants__grant__group=group,
        ).only(*REPORT_FIELDS).distinct().order_by("name", "slug")
    ):
        private[report.studio_id].append(report)

    rows = []
    for studio in studios:
        grant = grants.get(studio.pk)
        initial = {
            "studio": studio,
            "role": grant.role if grant else "",
            "viewer_scope": grant.viewer_scope if grant else "all",
            "reports": selected_by_grant.get(grant.pk, []) if grant else [],
        }
        form = access_form if posted is not None and posted.pk == studio.pk else form_factory(studio, initial)
        form.present_reports(catalog[studio.pk])
        rows.append({
            "studio": studio, "grant": grant, "form": form,
            "private_reports": private.get(studio.pk, []),
            "remote_reports": studio.pk in remote,
        })
    return rows, studios_more
