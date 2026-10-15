"""Permission-group management for reusable role bundles."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core import roles
from apps.core.audit import audit, audit_many
from apps.core.choice_requests import choice_request_params
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import effective_roles, require_org_role, require_studio_role
from apps.core.report_access import selected_report_access_block_reason, visible_reports
from apps.orgs.models import (
    OrgMembership,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)
from apps.orgs.views import PAGE_SIZE, paginate
from apps.reports.models import Report, ReportPermissionGrant, ShareLink
from apps.studios.models import Studio, StudioMembership

from .forms import GroupForm, ReportChoiceField, StudioAccessForm
from . import presentation as present

TABS = {
    "overview": "Overview",
    "members": "Members",
    "access": "Access",
    "effective": "Effective access",
}


def _group_url(org, group, tab="overview"):
    base = f"/orgs/{org.slug}/settings/groups/{group.pk}"
    return base if tab == "overview" else f"{base}?tab={tab}"


def _group_or_404(org, group_id, *, lock=False):
    if not str(group_id or "").isdecimal():
        from django.http import Http404

        raise Http404
    qs = (
        PermissionGroup.objects.select_for_update() if lock else PermissionGroup.objects
    )
    return get_object_or_404(qs, org=org, pk=group_id)


def _save_group_access(request, group, form):
    if not form.is_valid():
        return False
    studio = form.cleaned_data["studio"]
    role = form.cleaned_data["role"]
    scope = form.cleaned_data["viewer_scope"]
    block_reason = selected_report_access_block_reason()
    if role == roles.VIEWER and scope == "selected" and block_reason:
        form.add_error(None, block_reason)
        return False
    # Keep the identities validated by the form. A fresh values_list() query
    # could silently drop a report deleted before we acquire its lock.
    report_ids = {report.pk for report in form.cleaned_data["reports"]}
    with transaction.atomic():
        group = _group_or_404(request.org, group.pk, lock=True)
        studio = get_object_or_404(
            Studio.objects.select_for_update(), org=request.org, pk=studio.pk
        )
        locked_reports = {
            report.pk: report for report in Report.objects.select_for_update()
            .filter(studio=studio, pk__in=report_ids)
            .only("pk", "studio_id", "audience")
        }
        locked_report_ids = set(locked_reports)
        if locked_report_ids != report_ids:
            form.add_error("reports", "Selected reports changed; review and try again.")
            return False
        report_ids = locked_report_ids
        grant = (
            PermissionGroupGrant.objects.select_for_update()
            .filter(group=group, studio=studio)
            .first()
        )
        if not role:
            if grant:
                grant.delete()
                audit(request, "group.ungrant", target=group, studio=studio.slug)
            return True

        if grant is None:
            grant = PermissionGroupGrant(group=group, studio=studio)
        # Hydrate the locked relations for assignment validation without a
        # foreign-key lookup per report. Their identity was checked above.
        grant.group = group
        grant.studio = studio
        grant.role = role
        grant.viewer_scope = scope if role == roles.VIEWER else "all"
        try:
            grant.full_clean()
            grant.save()
        except ValidationError as exc:
            form.add_error(None, exc)
            return False

        old_ids = set(
            ReportPermissionGrant.objects.filter(grant=grant).values_list(
                "report_id", flat=True
            )
        )
        selected_ids = report_ids if grant.viewer_scope == "selected" else set()
        if grant.role == roles.VIEWER and grant.viewer_scope == "all":
            # Whole-studio Viewer access does not replace explicit Private assignments.
            selected_ids = set(
                ReportPermissionGrant.objects.filter(
                    grant=grant, report__audience=Report.AUDIENCE_PRIVATE
                ).values_list("report_id", flat=True)
            )
        ReportPermissionGrant.objects.filter(grant=grant).exclude(
            report_id__in=selected_ids
        ).delete()
        try:
            additions = []
            for report_id in sorted(selected_ids - old_ids):
                report = locked_reports[report_id]
                report.studio = studio
                assignment = ReportPermissionGrant(grant=grant, report=report)
                # Both foreign keys are validated under locks, and database
                # constraints still enforce uniqueness. Keep the model's
                # domain checks (including readiness and Private audience).
                assignment.clean()
                additions.append(assignment)
            ReportPermissionGrant.objects.bulk_create(additions, batch_size=250)
        except (ValidationError, IntegrityError) as exc:
            message = exc if isinstance(exc, ValidationError) else "Selected reports changed; review and try again."
            form.add_error(None, message)
            transaction.set_rollback(True)
            return False
        audit(
            request,
            "group.grant",
            target=group,
            studio=studio.slug,
            role=grant.role,
            viewer_scope=grant.viewer_scope,
            report_count=len(selected_ids),
        )
        audit_many(
            request, "group.report_grant",
            (Report(pk=pk) for pk in sorted(selected_ids - old_ids)), group_id=group.pk,
        )
        audit_many(
            request, "group.report_ungrant",
            (Report(pk=pk) for pk in sorted(old_ids - selected_ids)), group_id=group.pk,
        )
    return True


def _effective_member(request, group):
    raw_id = request.GET.get("member")
    membership = (
        OrgMembership.objects.filter(
            org=request.org,
            user_id=raw_id,
            user__permission_group_memberships__group=group,
        )
        .select_related("user")
        .first()
        if str(raw_id or "").isdecimal()
        else None
    )
    if membership is None:
        membership = (
            OrgMembership.objects.filter(
                org=request.org, user__permission_group_memberships__group=group
            )
            .select_related("user")
            .order_by("user__email")
            .first()
        )
    if membership is None:
        return None, []

    user = membership.user
    er = effective_roles(user, request.org)
    studios = Studio.objects.filter(org=request.org)
    if not er.is_org_admin and not er.default_studio_role:
        # Only studios with an explicit grant can carry a role.
        studios = studios.filter(pk__in=er.studio_roles)
    studios = [s for s in studios.order_by("slug", "pk")[: present.STUDIO_LIMIT] if er.role_for(s)]
    studio_ids = [studio.pk for studio in studios]
    direct_roles = dict(
        StudioMembership.objects.filter(user=user, studio__org=request.org).values_list(
            "studio_id", "role"
        )
    )
    member_groups = list(
        PermissionGroup.objects.filter(org=request.org, memberships__user=user).order_by("name", "pk")[: present.CHOICE_LIMIT]
    )
    grants = {
        (grant.group_id, grant.studio_id): grant
        for grant in PermissionGroupGrant.objects.filter(
            group__in=member_groups, studio_id__in=studio_ids
        ).annotate(report_count=Count("report_grants"))
    }
    rows = []
    for studio in studios:
        role = er.role_for(studio)
        full = roles.at_least(role, roles.DEVELOPER)
        sources = []
        if membership.role == roles.ORG_ADMIN:
            sources.append("Direct organization admin: Admin with all reports")
        if direct_roles.get(studio.pk):
            access_label = (
                "Studio audience" if direct_roles[studio.pk] == roles.VIEWER else "all reports"
            )
            sources.append(
                "Direct studio role: "
                f"{direct_roles[studio.pk].title()} with {access_label}"
            )
        for source_group in member_groups:
            if source_group.org_role == roles.ORG_ADMIN:
                sources.append(
                    f"{source_group.name}: organization admin with all reports"
                )
            if source_group.default_studio_role:
                sources.append(
                    f"{source_group.name}: default "
                    f"{source_group.default_studio_role.title()} on every studio"
                )
            grant = grants.get((source_group.pk, studio.pk))
            if grant:
                label = grant.role.title()
                if grant.role == roles.VIEWER and grant.viewer_scope == "selected":
                    count = grant.report_count
                    label += f" for {count} selected report{'s' if count != 1 else ''}"
                else:
                    label += " with Studio audience" if grant.role == roles.VIEWER else " with all reports"
                    if grant.role == roles.VIEWER and grant.report_count:
                        label += f" plus {grant.report_count} explicit Private assignments"
                sources.append(f"{source_group.name}: {label}")
        reports, reports_count = [], 0
        if not full:
            visible = visible_reports(user, Report.objects.filter(studio=studio)).only(
                "pk", "studio_id", "name", "slug", "kind"
            ).order_by("name", "slug")
            reports, more = present.limited(visible)
            reports_count = visible.count() if more else len(reports)
        rows.append(
            {
                "studio": studio,
                "role": role,
                "full": full,
                "reports": reports,
                "reports_count": reports_count,
                "sources": sources,
            }
        )
    return membership, rows


@require_org_role(roles.ORG_ADMIN)
def groups(request, org_slug):  # noqa: ARG001
    form = GroupForm(request.POST or None, org=request.org)
    if request.method == "POST":
        if form.is_valid():
            group = form.save(commit=False)
            group.org = request.org
            group.created_by = request.user
            group.save()
            audit(request, "group.create", target=group, name=group.name)
            messages.success(request, f"Group “{group.name}” created.")
            return redirect(f"/orgs/{request.org.slug}/settings/groups/{group.pk}")

    group_qs = present.group_summary_counts(request.org.permission_groups.all()).order_by("name", "pk")
    q = (request.GET.get("q") or "").strip()
    if q:
        group_qs = group_qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    total = group_qs.count()
    page = paginate(request, group_qs, PAGE_SIZE)
    group_list = page.object_list
    return render(
        request,
        "orgs/groups.html",
        {
            "org": request.org,
            "groups": group_list,
            "form": form,
            "page": page,
            "q": q,
            "total": total,
            "can_manage": True,
        },
    )


@require_org_role(roles.ORG_ADMIN)
def group_new(request, org_slug):  # noqa: ARG001
    form = GroupForm(request.POST or None, org=request.org)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            group = form.save(commit=False)
            group.org = request.org
            group.created_by = request.user
            group.save()
            audit(request, "group.create", target=group, name=group.name)
        messages.success(request, f"Group “{group.name}” created.")
        return redirect(_group_url(request.org, group))
    return render(
        request,
        "orgs/group_new.html",
        {
            "org": request.org,
            "form": form,
            "can_manage": True,
        },
    )


@require_org_role(roles.ORG_ADMIN)
def group_detail(request, org_slug, group_id):  # noqa: ARG001
    group = _group_or_404(request.org, group_id)
    tab = request.GET.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    form = GroupForm(instance=group, org=request.org)
    access_form = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "edit":
            with transaction.atomic():
                group = _group_or_404(request.org, group_id, lock=True)
                form = GroupForm(request.POST, instance=group, org=request.org)
                if form.is_valid():
                    form.save()
                    audit(request, "group.update", target=group)
                    if is_settings_request(request):
                        return settings_success(request, "Group updated.", values=form.cleaned_data, refresh=["group-heading", "group-summary"])
                    messages.success(request, "Group updated.")
                    return redirect(_group_url(request.org, group))
            if is_settings_request(request):
                return settings_error(request, form=form)
        elif action == "studio_access":
            access_form = StudioAccessForm(request.POST, org=request.org)
            if _save_group_access(request, group, access_form):
                if is_settings_request(request):
                    return settings_success(request, "Studio access updated.", updates={f"group-grant-{access_form.cleaned_data['studio'].pk}": "explicit grant" if access_form.cleaned_data["role"] else "no explicit grant"})
                messages.success(request, "Studio access updated.")
                return redirect(_group_url(request.org, group, "access"))
            if is_settings_request(request):
                return settings_error(request, form=access_form)
            tab = "access"
        elif action == "grant":  # legacy form compatibility
            studio_id = request.POST.get("studio_id")
            if not str(studio_id or "").isdecimal():
                from django.http import Http404

                raise Http404
            studio = get_object_or_404(Studio, org=request.org, pk=studio_id)
            access_form = StudioAccessForm(
                {
                    **request.POST.dict(),
                    "studio": studio.pk,
                    "viewer_scope": "all",
                },
                org=request.org,
                studio=studio,
            )
            if _save_group_access(request, group, access_form):
                return redirect(_group_url(request.org, group, "access"))
        elif action == "ungrant":
            studio_id = request.POST.get("studio_id")
            if not str(studio_id or "").isdecimal():
                return redirect(_group_url(request.org, group, "access"))
            with transaction.atomic():
                group = _group_or_404(request.org, group_id, lock=True)
                grant = (
                    PermissionGroupGrant.objects.select_for_update()
                    .filter(group=group, studio__org=request.org, studio_id=studio_id)
                    .first()
                )
                if grant:
                    studio_slug = grant.studio.slug
                    grant.delete()
                    audit(request, "group.ungrant", target=group, studio=studio_slug)
            return redirect(_group_url(request.org, group, "access"))
        elif action == "add_user":
            user_id = request.POST.get("user_id")
            if not str(user_id or "").isdecimal():
                return redirect(_group_url(request.org, group, "members"))
            with transaction.atomic():
                group = _group_or_404(request.org, group_id, lock=True)
                m = OrgMembership.objects.filter(
                    org=request.org, user_id=user_id
                ).first()
                if m:
                    _, created = PermissionGroupMembership.objects.get_or_create(
                        user=m.user, group=group, defaults={"added_by": request.user}
                    )
                    if created:
                        audit(
                            request, "group.add_user", target=group, email=m.user.email
                        )
            return redirect(_group_url(request.org, group, "members"))
        elif action == "remove_user":
            user_id = request.POST.get("user_id")
            if not str(user_id or "").isdecimal():
                return redirect(_group_url(request.org, group, "members"))
            with transaction.atomic():
                group = _group_or_404(request.org, group_id, lock=True)
                membership = (
                    PermissionGroupMembership.objects.select_for_update()
                    .filter(group=group, user_id=user_id)
                    .first()
                )
                if membership:
                    user_id = membership.user_id
                    membership.delete()
                    audit(request, "group.remove_user", target=group, user_id=user_id)
            return redirect(_group_url(request.org, group, "members"))

    context = {
        "org": request.org, "group": group, "console_page_title": group.name,
        "form": form, "tabs": TABS, "tab": tab, "can_manage": True,
        "q": present.text(request, "q"), "selected_block_reason": selected_report_access_block_reason(),
        "access_rows": [], "grants": [],
    }
    if tab == "overview":
        context["grants"] = list(group.grants.select_related("studio").order_by("studio__slug", "pk")[: present.CHOICE_LIMIT])
    elif tab == "members":
        members = group.memberships.select_related("user").order_by("user__email", "pk")
        if context["q"]:
            members = members.filter(Q(user__email__icontains=context["q"]) | Q(user__name__icontains=context["q"]))
        member_page = paginate(request, members, PAGE_SIZE)
        addable, addable_remote = present.limited(present.member_choices(request.org, group, "addable"))
        context.update(member_page=member_page, member_total=member_page.paginator.count,
                       addable_members=addable, addable_remote=addable_remote)
    elif tab == "access":
        rows, studios_more = present.access_rows(
            request.org, group,
            lambda studio, initial: StudioAccessForm(org=request.org, studio=studio, initial=initial),
            access_form,
        )
        context.update(access_rows=rows, studios_more=studios_more, studio_limit=present.STUDIO_LIMIT)
    elif tab == "effective":
        selected_member, effective_rows = _effective_member(request, group)
        members, members_remote = present.limited(group.memberships.select_related("user").order_by("user__email", "pk"))
        if selected_member and all(m.user_id != selected_member.user_id for m in members):
            members.append(selected_member)
        context.update(selected_member=selected_member, effective_rows=effective_rows,
                       effective_members=members, effective_members_remote=members_remote,
                       report_limit=present.CHOICE_LIMIT)
    return render(request, "orgs/group_detail.html", context)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def group_delete(request, org_slug, group_id):  # noqa: ARG001
    with transaction.atomic():
        group = _group_or_404(request.org, group_id, lock=True)
        audit(request, "group.delete", target=group, name=group.name)
        group.delete()
    messages.success(request, "Group deleted.")
    return redirect(f"/orgs/{request.org.slug}/settings/groups")


@require_studio_role(roles.ADMIN)
@require_http_methods(["GET", "POST"])
def report_access(request, org_slug, studio_slug, slug):  # noqa: ARG001
    reports = Report.objects.only(*present.REPORT_FIELDS) if request.method == "GET" else Report.objects.all()
    report = get_object_or_404(reports, studio=request.studio, slug=slug)
    if request.method == "GET" and request.headers.get("Accept", "").startswith(
        "application/json"
    ):
        return JsonResponse({"can_manage": True, "url": request.path})

    if request.method == "POST":
        group_id = request.POST.get("group_id")
        action = request.POST.get("action")
        if action == "set_audience":
            audience = request.POST.get("audience")
            if audience not in dict(Report.AUDIENCE_CHOICES):
                if is_settings_request(request):
                    return settings_error(request, "Choose a valid audience.", errors={"audience": ["Choose a valid audience."]})
                messages.error(request, "Choose a valid audience.")
                return redirect(request.path)
            block_reason = selected_report_access_block_reason()
            if block_reason:
                if is_settings_request(request):
                    return settings_error(request, block_reason, errors={"audience": [block_reason]})
                messages.error(request, block_reason)
                return redirect(request.path)
            with transaction.atomic():
                report = get_object_or_404(
                    Report.objects.select_for_update(), pk=report.pk, studio=request.studio
                )
                prior = report.audience
                report.audience = audience
                report.save(update_fields=["audience"])
                if audience == Report.AUDIENCE_STUDIO:
                    # All-scope Viewer assignments are valid only for Private items.
                    ReportPermissionGrant.objects.filter(
                        report=report, grant__viewer_scope="all"
                    ).delete()
                audit(
                    request, "report.audience_set", target=report,
                    prior_audience=prior, audience=audience,
                )
            if is_settings_request(request):
                return settings_success(request, f"{report.get_kind_display()} audience saved.", audience=audience, refresh=["report-audience-description", "report-audience-assignments"])
            messages.success(request, f"{report.get_kind_display()} audience saved.")
            return redirect(request.path)
        with transaction.atomic():
            group = _group_or_404(request.org, group_id, lock=True)
            studio = get_object_or_404(
                Studio.objects.select_for_update(),
                org=request.org,
                pk=request.studio.pk,
            )
            report = get_object_or_404(
                Report.objects.select_for_update(), pk=report.pk, studio=studio
            )
            block_reason = selected_report_access_block_reason()
            if action in {"grant_report", "ungrant_report"} and block_reason:
                messages.error(request, block_reason)
                return redirect(request.path)
            grant = (
                PermissionGroupGrant.objects.select_for_update()
                .filter(group=group, studio=studio)
                .first()
            )
            broader = bool(
                group.org_role == roles.ORG_ADMIN
                or roles.at_least(group.default_studio_role, roles.DEVELOPER)
                or (report.audience == Report.AUDIENCE_STUDIO and group.default_studio_role)
                or (
                    grant
                    and (
                        grant.role != roles.VIEWER
                        or (report.audience == Report.AUDIENCE_STUDIO and grant.viewer_scope == "all")
                    )
                )
            )
            if broader:
                messages.info(
                    request,
                    f"{group.name} already has inherited access to this {report.get_kind_display().lower()}.",
                )
            elif action == "grant_report":
                try:
                    if grant is None:
                        grant = PermissionGroupGrant.objects.create(
                            group=group,
                            studio=studio,
                            role=roles.VIEWER,
                            viewer_scope="selected",
                        )
                        audit(
                            request,
                            "group.grant",
                            target=group,
                            studio=studio.slug,
                            role=roles.VIEWER,
                            viewer_scope="selected",
                        )
                    _, created = ReportPermissionGrant.objects.get_or_create(
                        grant=grant, report=report
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    transaction.set_rollback(True)
                    return redirect(request.path)
                if created:
                    audit(
                        request, "group.report_grant", target=report, group_id=group.pk
                    )
            elif action == "ungrant_report" and grant is not None:
                deleted, _ = ReportPermissionGrant.objects.filter(
                    grant=grant, report=report
                ).delete()
                if deleted:
                    audit(
                        request,
                        "group.report_ungrant",
                        target=report,
                        group_id=group.pk,
                    )
        return redirect(request.path)

    group_qs = PermissionGroup.objects.filter(org=request.org).prefetch_related(
        Prefetch("grants", queryset=PermissionGroupGrant.objects.filter(studio=request.studio))
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        group_qs = group_qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    group_qs = group_qs.order_by("name")
    total = group_qs.count()
    page = paginate(request, group_qs, PAGE_SIZE)
    selected_group_ids = set(ReportPermissionGrant.objects.filter(report=report, grant__group_id__in=[g.pk for g in page.object_list], grant__studio=request.studio).values_list("grant__group_id", flat=True))
    rows = []
    for group in page.object_list:
        grant = next(
            (g for g in group.grants.all() if g.studio_id == request.studio.pk), None
        )
        full_reason = ""
        if group.org_role == roles.ORG_ADMIN:
            full_reason = (
                "Organization admins have Admin access to every studio, report and analysis."
            )
        elif group.default_studio_role and (
            roles.at_least(group.default_studio_role, roles.DEVELOPER)
            or report.audience == Report.AUDIENCE_STUDIO
        ):
            full_reason = (
                f"The group default grants {group.default_studio_role.title()} "
                "access to every studio."
            )
        elif grant and grant.role != roles.VIEWER:
            full_reason = (
                f"This studio grant is {grant.role.title()}, "
                "which includes every report and analysis."
            )
        elif grant and grant.viewer_scope == "all" and report.audience == Report.AUDIENCE_STUDIO:
            full_reason = "This Viewer grant includes the Studio audience."
        selected = group.pk in selected_group_ids
        rows.append(
            {
                "group": group,
                "grant": grant,
                "full_reason": full_reason,
                "selected": selected,
            }
        )
    return render(
        request,
        "orgs/report_access.html",
        {
            "org": request.org,
            "studio": request.studio,
            "report": report,
            "audience_choices": Report.AUDIENCE_CHOICES,
            "console_page_title": f"{report.get_kind_display()} access",
            "rows": rows,
            "q": q,
            "total": total,
            "page": page,
            "link_total": ShareLink.objects.filter(report=report).count(),
            "can_manage": True,
            "selected_block_reason": selected_report_access_block_reason(),
        },
    )


@require_org_role(roles.ORG_ADMIN)
@require_GET
def group_member_choices(request, org_slug, group_id):
    group = _group_or_404(request.org, group_id)
    kind = request.GET.get("kind", "addable")
    if kind not in {"addable", "effective"}:
        return JsonResponse({"error": "Choose a valid member list."}, status=400)
    page = present.window(request, present.member_choices(request.org, group, kind, present.text(request, "q")), "page")
    raw = request.GET.get("selected")
    selected = present.member_choices(request.org, group, kind).filter(user_id=raw).first() if str(raw or "").isdecimal() else None
    def record(member):
        return {"value": member.user_id, "label": member.user.display_name, "hint": member.user.email}
    response = JsonResponse({"results": [record(m) for m in page.object_list], "selected": record(selected) if selected else None, "pagination": present.pagination(page)})
    response["Cache-Control"] = "private, no-store"
    return response


@require_org_role(roles.ORG_ADMIN)
@require_http_methods(["GET", "POST"])
def group_report_choices(request, org_slug, group_id, studio_id):
    _group_or_404(request.org, group_id)
    studio = get_object_or_404(Studio, org=request.org, pk=studio_id)
    try:
        parameters = choice_request_params(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    page = present.window(request, present.report_choices(studio, parameters.get("q", "").strip()), "page", parameters=parameters)
    ids = present.report_ids([v for raw in parameters.getlist("selected") for v in raw.split(",")])
    selected = Report.objects.filter(studio=studio, pk__in=ids).only(*present.REPORT_FIELDS).order_by("name", "slug")
    field = ReportChoiceField(queryset=Report.objects.none())
    def record(report):
        return {"value": str(report.pk), "label": field.label_from_instance(report)}
    response = JsonResponse({"results": [record(r) for r in page.object_list], "selected": [record(r) for r in selected], "pagination": present.pagination(page)})
    response["Cache-Control"] = "private, no-store"
    return response
