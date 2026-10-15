"""Bounded presentation of permission groups; validation stays authoritative."""
from django.core.paginator import Paginator
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from apps.core import roles
from apps.core.choice_requests import inline_choices
from apps.core.permissions import effective_roles
from apps.core.report_access import visible_reports
from apps.orgs.models import OrgMembership, PermissionGroup, PermissionGroupGrant, PermissionGroupMembership
from apps.reports.models import Report
from apps.studios.models import Studio, StudioMembership

CHOICE_SIZE = 25
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


def pager(request, page, key):
    def url(number):
        query = request.GET.copy()
        for field in ("q", "studio_q", "grant_q", "member_q", "report_q", "source_q", "link_q"):
            if field in request.POST:
                query[field] = request.POST[field]
        query[key] = number
        return "?" + query.urlencode()
    return {"page": page, "previous_url": url(page.previous_page_number()) if page.has_previous() else "",
            "next_url": url(page.next_page_number()) if page.has_next() else ""}


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


def effective_context(request, group):
    choices, searchable = inline_choices(member_choices(request.org, group, "effective"))
    raw = request.GET.get("member")
    selected = member_choices(request.org, group, "effective").filter(user_id=raw).first() if str(raw or "").isdecimal() else None
    if selected is None:
        selected = member_choices(request.org, group, "effective").first()
    if selected and all(m.user_id != selected.user_id for m in choices):
        choices.append(selected)
    result = {"effective_members": choices, "effective_searchable": searchable, "selected_member": selected,
              "effective_rows": []}
    if not selected:
        return result
    er = effective_roles(selected.user, request.org)
    studios = Studio.objects.filter(org=request.org)
    if not er.is_org_admin and not er.default_studio_role:
        studios = studios.filter(pk__in=er.studio_roles)
    studio_q = text(request, "studio_q")
    if studio_q:
        studios = studios.filter(Q(name__icontains=studio_q) | Q(slug__icontains=studio_q))
    page = window(request, studios.order_by("slug", "pk"), "studio_page")
    rows = [{"studio": s, "role": er.role_for(s), "full": roles.at_least(er.role_for(s), roles.DEVELOPER)} for s in page.object_list]
    raw_studio = request.GET.get("studio")
    detail_studio = Studio.objects.filter(org=request.org, pk=raw_studio).first() if str(raw_studio or "").isdecimal() else (rows[0]["studio"] if rows else None)
    result.update(effective_rows=rows, studio_page=page, studio_pager=pager(request, page, "studio_page"), studio_q=studio_q)
    if not detail_studio or not er.role_for(detail_studio):
        return result
    role = er.role_for(detail_studio)
    reports = visible_reports(selected.user, report_choices(detail_studio, text(request, "report_q")))
    report_page = window(request, reports, "report_page")
    sources = []
    if selected.user.is_superuser:
        sources.append("Instance superuser: Admin with all reports")
    if selected.role == roles.ORG_ADMIN:
        sources.append("Direct organization admin: Admin with all reports")
    direct = StudioMembership.objects.filter(user=selected.user, studio=detail_studio).values_list("role", flat=True).first()
    if direct:
        sources.append(f"Direct studio role: {direct.title()} with {'Studio audience' if direct == roles.VIEWER else 'all reports'}")
    grants = {g.group_id: g for g in PermissionGroupGrant.objects.filter(studio=detail_studio, group__org=request.org, group__memberships__user=selected.user).annotate(report_count=Count("report_grants"))}
    for source in PermissionGroup.objects.filter(org=request.org, memberships__user=selected.user).order_by("name", "pk"):
        if source.org_role == roles.ORG_ADMIN:
            sources.append(f"{source.name}: organization admin with all reports")
        if source.default_studio_role:
            sources.append(f"{source.name}: default {source.default_studio_role.title()} on every studio")
        grant = grants.get(source.pk)
        if grant:
            label = grant.role.title()
            if grant.role == roles.VIEWER and grant.viewer_scope == "selected":
                label += f" for {grant.report_count} selected report{'s' if grant.report_count != 1 else ''}"
            else:
                label += " with Studio audience" if grant.role == roles.VIEWER else " with all reports"
                if grant.role == roles.VIEWER and grant.report_count:
                    label += f" plus {grant.report_count} explicit Private assignments"
            sources.append(f"{source.name}: {label}")
    source_q = text(request, "source_q")
    source_total = len(sources)
    if source_q:
        sources = [s for s in sources if source_q.casefold() in s.casefold()]
    source_page = window(request, sources, "source_page")
    result.update(effective_detail={"studio": detail_studio, "role": role, "full": roles.at_least(role, roles.DEVELOPER)},
                  effective_report_page=report_page, effective_report_pager=pager(request, report_page, "report_page"),
                  source_page=source_page, source_pager=pager(request, source_page, "source_page"), source_total=source_total,
                  source_q=source_q, report_q=text(request, "report_q"))
    return result
