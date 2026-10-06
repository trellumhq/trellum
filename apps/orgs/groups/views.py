"""Permission-group management for reusable role bundles."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.core import roles
from apps.core.audit import audit
from apps.core.permissions import effective_roles, require_org_role, require_studio_role
from apps.core.report_access import selected_report_access_block_reason
from apps.orgs.models import (
    OrgMembership,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)
from apps.orgs.views import PAGE_SIZE, paginate
from apps.reports.models import Report, ReportPermissionGrant, ShareLink
from apps.studios.models import Studio, StudioMembership

from .forms import GroupForm, StudioAccessForm

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
    report_ids = set(form.cleaned_data["reports"].values_list("pk", flat=True))
    with transaction.atomic():
        group = _group_or_404(request.org, group.pk, lock=True)
        studio = get_object_or_404(
            Studio.objects.select_for_update(), org=request.org, pk=studio.pk
        )
        locked_report_ids = set(
            Report.objects.select_for_update()
            .filter(studio=studio, pk__in=report_ids)
            .values_list("pk", flat=True)
        )
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
        ReportPermissionGrant.objects.filter(grant=grant).exclude(
            report_id__in=selected_ids
        ).delete()
        try:
            for report_id in selected_ids - old_ids:
                ReportPermissionGrant.objects.create(grant=grant, report_id=report_id)
        except ValidationError as exc:
            form.add_error(None, exc)
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
        for report_id in selected_ids - old_ids:
            audit(
                request,
                "group.report_grant",
                target=Report(pk=report_id),
                group_id=group.pk,
            )
        for report_id in old_ids - selected_ids:
            audit(
                request,
                "group.report_ungrant",
                target=Report(pk=report_id),
                group_id=group.pk,
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
    direct_roles = dict(
        StudioMembership.objects.filter(user=user, studio__org=request.org).values_list(
            "studio_id", "role"
        )
    )
    member_groups = list(
        PermissionGroup.objects.filter(org=request.org, memberships__user=user)
        .prefetch_related("grants__report_grants")
        .order_by("name")
    )
    rows = []
    for studio in Studio.objects.filter(org=request.org).order_by("slug"):
        role = er.role_for(studio)
        if role is None:
            continue
        scope = er.report_scope_for(studio)
        sources = []
        if membership.role == roles.ORG_ADMIN:
            sources.append("Direct organization admin: Admin with all reports")
        if direct_roles.get(studio.pk):
            sources.append(
                "Direct studio role: "
                f"{direct_roles[studio.pk].title()} with all reports"
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
            grant = next(
                (g for g in source_group.grants.all() if g.studio_id == studio.pk), None
            )
            if grant:
                label = grant.role.title()
                if grant.role == roles.VIEWER and grant.viewer_scope == "selected":
                    count = len(grant.report_grants.all())
                    label += f" for {count} selected report{'s' if count != 1 else ''}"
                else:
                    label += " with all reports"
                sources.append(f"{source_group.name}: {label}")
        rows.append(
            {
                "studio": studio,
                "role": role,
                "full": scope is None,
                "reports": list(
                    Report.objects.filter(studio=studio, pk__in=scope).order_by(
                        "name", "slug"
                    )
                )
                if scope is not None
                else [],
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

    group_qs = request.org.permission_groups.prefetch_related(
        "grants__studio", "memberships"
    ).order_by("name")
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
                    messages.success(request, "Group updated.")
                    return redirect(_group_url(request.org, group))
        elif action == "studio_access":
            access_form = StudioAccessForm(request.POST, org=request.org)
            if _save_group_access(request, group, access_form):
                messages.success(request, "Studio access updated.")
                return redirect(_group_url(request.org, group, "access"))
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

    grants = list(group.grants.select_related("studio").order_by("studio__slug"))
    q = (request.GET.get("q") or "").strip()
    member_page = None
    member_total = 0
    addable_members = None
    if tab == "members":
        member_qs = group.memberships.select_related("user").order_by("user__email")
        if q:
            member_qs = member_qs.filter(
                Q(user__email__icontains=q) | Q(user__name__icontains=q)
            )
        member_total = member_qs.count()
        member_page = paginate(request, member_qs, PAGE_SIZE)
        addable_members = (
            OrgMembership.objects.filter(org=request.org)
            .exclude(user__permission_group_memberships__group=group)
            .select_related("user")
            .order_by("user__email")
        )
    access_rows = []
    selected_block_reason = selected_report_access_block_reason()
    if tab == "access":
        selected_by_grant = {}
        for grant_id, report_id in ReportPermissionGrant.objects.filter(
            grant__group=group
        ).values_list("grant_id", "report_id"):
            selected_by_grant.setdefault(grant_id, []).append(report_id)
        for studio in Studio.objects.filter(org=request.org).order_by("slug"):
            grant = next((g for g in grants if g.studio_id == studio.pk), None)
            initial = {
                "studio": studio,
                "role": grant.role if grant else "",
                "viewer_scope": grant.viewer_scope if grant else "all",
                "reports": selected_by_grant.get(grant.pk, []) if grant else [],
            }
            row_form = (
                access_form
                if access_form and str(access_form.data.get("studio")) == str(studio.pk)
                else StudioAccessForm(org=request.org, studio=studio, initial=initial)
            )
            access_rows.append({"studio": studio, "grant": grant, "form": row_form})
    selected_member, effective_rows = (
        _effective_member(request, group) if tab == "effective" else (None, [])
    )
    effective_members = (
        group.memberships.select_related("user").order_by("user__email")
        if tab == "effective"
        else None
    )
    return render(
        request,
        "orgs/group_detail.html",
        {
            "org": request.org,
            "group": group,
            "console_page_title": group.name,
            "form": form,
            "grants": grants,
            "tabs": TABS,
            "tab": tab,
            "can_manage": True,
            "member_page": member_page,
            "member_total": member_total,
            "q": q,
            "addable_members": addable_members,
            "access_rows": access_rows,
            "selected_block_reason": selected_block_reason,
            "selected_member": selected_member,
            "effective_rows": effective_rows,
            "effective_members": effective_members,
            "studio_role_choices": roles.STUDIO_ROLE_CHOICES,
        },
    )


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
    report = get_object_or_404(Report, studio=request.studio, slug=slug)
    if request.method == "GET" and request.headers.get("Accept", "").startswith(
        "application/json"
    ):
        return JsonResponse({"can_manage": True, "url": request.path})

    if request.method == "POST":
        group_id = request.POST.get("group_id")
        action = request.POST.get("action")
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
                or group.default_studio_role
                or (
                    grant
                    and (grant.role != roles.VIEWER or grant.viewer_scope == "all")
                )
            )
            if broader:
                messages.info(
                    request,
                    f"{group.name} already has access to every report in this studio.",
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
        "grants"
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        group_qs = group_qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    group_qs = group_qs.order_by("name")
    total = group_qs.count()
    page = paginate(request, group_qs, PAGE_SIZE)
    rows = []
    for group in page.object_list:
        grant = next(
            (g for g in group.grants.all() if g.studio_id == request.studio.pk), None
        )
        full_reason = ""
        if group.org_role == roles.ORG_ADMIN:
            full_reason = (
                "Organization admins have Admin access to every studio and report."
            )
        elif group.default_studio_role:
            full_reason = (
                f"The group default grants {group.default_studio_role.title()} "
                "access to every studio."
            )
        elif grant and grant.role != roles.VIEWER:
            full_reason = (
                f"This studio grant is {grant.role.title()}, "
                "which includes every report."
            )
        elif grant and grant.viewer_scope == "all":
            full_reason = "This Viewer grant includes every report in the studio."
        selected = bool(
            grant
            and ReportPermissionGrant.objects.filter(
                grant=grant, report=report
            ).exists()
        )
        rows.append(
            {
                "group": group,
                "grant": grant,
                "full_reason": full_reason,
                "selected": selected,
            }
        )
    public_links = ShareLink.objects.filter(report=report).defer(
        "token", "password_hash"
    )
    return render(
        request,
        "orgs/report_access.html",
        {
            "org": request.org,
            "studio": request.studio,
            "report": report,
            "rows": rows,
            "q": q,
            "total": total,
            "page": page,
            "public_links": public_links,
            "can_manage": True,
            "selected_block_reason": selected_report_access_block_reason(),
        },
    )
