"""Permission-group management for reusable role bundles."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import BooleanField, Case, Prefetch, Q, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core import roles
from apps.core.audit import audit
from apps.core.choice_requests import choice_request_params
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_org_role, require_studio_role
from apps.core.report_access import selected_report_access_block_reason
from apps.orgs.models import (
    OrgMembership,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)
from apps.orgs.views import PAGE_SIZE, paginate
from apps.reports.models import OrgSharePolicy, Report, ReportPermissionGrant, ShareLink
from apps.studios.models import Studio

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
        if action in {"browse_access", "browse_member"}:
            if action == "browse_access":
                access_form = StudioAccessForm(request.POST, org=request.org)
                tab = "access"
            else:
                tab = "members"
        elif action == "edit":
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
                return redirect(_group_url(request.org, group, "access") + f"&studio={access_form.cleaned_data['studio'].pk}")
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
        "access_rows": [], "grants": [], "access_form": access_form,
    }
    if tab == "overview":
        grant_q = present.text(request, "grant_q")
        grants = group.grants.select_related("studio").order_by("studio__slug", "pk")
        grant_total = grants.count()
        if grant_q:
            grants = grants.filter(Q(studio__name__icontains=grant_q) | Q(studio__slug__icontains=grant_q))
        page = present.window(request, grants, "grant_page")
        context.update(grants=page.object_list, grant_total=grant_total, grant_q=grant_q,
                       grant_pager=present.pager(request, page, "grant_page"))
    elif tab == "members":
        members = group.memberships.select_related("user").order_by("user__email", "pk")
        if context["q"]:
            members = members.filter(Q(user__email__icontains=context["q"]) | Q(user__name__icontains=context["q"]))
        member_page = paginate(request, members, PAGE_SIZE)
        add_q = present.text(request, "member_q")
        add_page = present.window(request, present.member_choices(request.org, group, "addable", add_q), "member_page")
        choices = list(add_page.object_list)
        selected_id = request.POST.get("user_id", request.GET.get("user_id"))
        selected = present.member_choices(request.org, group, "addable").filter(user_id=selected_id).first() if str(selected_id or "").isdecimal() else None
        if selected and all(m.user_id != selected.user_id for m in choices):
            choices.append(selected)
        context.update(member_page=member_page, member_total=member_page.paginator.count,
                       addable_members=choices, addable_page=add_page, selected_addable=selected,
                       member_q=add_q, addable_pager=present.pager(request, add_page, "member_page"))
    elif tab == "access":
        studio_q = present.text(request, "studio_q")
        studios = Studio.objects.filter(org=request.org).order_by("slug", "pk")
        if studio_q:
            studios = studios.filter(Q(name__icontains=studio_q) | Q(slug__icontains=studio_q))
        page = present.window(request, studios, "studio_page")
        page_studios = list(page.object_list)
        raw = access_form.data.get("studio") if access_form else request.GET.get("studio")
        selected_studio = Studio.objects.filter(org=request.org, pk=raw).first() if str(raw or "").isdecimal() else (page_studios[0] if page_studios else None)
        ids = [s.pk for s in page_studios]
        if selected_studio:
            ids.append(selected_studio.pk)
        grants = {g.studio_id: g for g in group.grants.filter(studio_id__in=ids)}
        context.update(studio_page=page, studio_pager=present.pager(request, page, "studio_page"), studio_q=studio_q,
                       studio_rows=[{"studio": studio, "grant": grants.get(studio.pk)} for studio in page_studios])
        if selected_studio:
            grant = grants.get(selected_studio.pk)
            selected_ids = list(ReportPermissionGrant.objects.filter(grant=grant).values_list("report_id", flat=True)) if grant else []
            row_form = access_form or StudioAccessForm(org=request.org, studio=selected_studio, initial={
                "studio": selected_studio, "role": grant.role if grant else "",
                "viewer_scope": grant.viewer_scope if grant else "all", "reports": selected_ids})
            if access_form:
                selected_ids = access_form.data.getlist("reports")
            valid_selected = Report.objects.filter(studio=selected_studio, pk__in=present.report_ids(selected_ids)).order_by("name", "slug")
            report_q = present.text(request, "report_q")
            report_page = present.window(request, present.report_choices(selected_studio, report_q), "report_page")
            catalog = {r.pk: r for r in report_page.object_list}
            catalog.update({r.pk: r for r in valid_selected})
            catalog_ids = {str(pk) for pk in catalog}
            row_form.present_reports(catalog.values(), [value for value in selected_ids if str(value) not in catalog_ids])
            private = Report.objects.filter(studio=selected_studio, audience=Report.AUDIENCE_PRIVATE,
                permission_group_grants__grant=grant).distinct().order_by("name", "slug") if grant else Report.objects.none()
            private_page = present.window(request, private, "private_page")
            context.update(access_rows=[{"studio": selected_studio, "grant": grant, "form": row_form, "private_reports": private_page.object_list}],
                report_page=report_page, report_q=report_q, selected_report_count=len(selected_ids),
                private_page=private_page, private_pager=present.pager(request, private_page, "private_page"))
    elif tab == "effective":
        context.update(present.effective_context(request, group))
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
    report = get_object_or_404(Report, studio=request.studio, slug=slug)
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
    public_links = ShareLink.objects.filter(report=report).select_related("created_by").defer("token", "password_hash").annotate(
        has_password=Case(When(password_hash="", then=Value(False)), default=Value(True), output_field=BooleanField())
    ).order_by("-created_at", "-pk")
    link_total = public_links.count()
    link_q = present.text(request, "link_q")
    if link_q:
        public_links = public_links.filter(Q(created_by__email__icontains=link_q) | Q(created_by__name__icontains=link_q))
    link_page = present.window(request, public_links, "link_page")
    policy = OrgSharePolicy.objects.filter(org=request.org).first()
    link_rows = list(link_page.object_list)
    now = timezone.now()
    for link in link_rows:
        link.preview_expiry = link.effective_expires_at(policy)
        link.preview_state = "Active"
        if link.revoked_at:
            link.preview_state = "Revoked"
        elif link.preview_expiry and link.preview_expiry <= now:
            link.preview_state = "Expired"
        elif not policy or not policy.share_links_enabled or (link.embed and not policy.embed_links_enabled) or (
            not link.embed and policy and policy.require_password and not link.has_password
        ):
            link.preview_state = "Blocked by organization policy"
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
            "public_links": link_rows,
            "link_total": link_total, "link_page": link_page, "link_q": link_q, "link_pager": present.pager(request, link_page, "link_page"),
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
    selected = Report.objects.filter(studio=studio, pk__in=ids).order_by("name", "slug")
    field = ReportChoiceField(queryset=Report.objects.none())
    def record(report):
        return {"value": str(report.pk), "label": field.label_from_instance(report)}
    response = JsonResponse({"results": [record(r) for r in page.object_list], "selected": [record(r) for r in selected], "pagination": present.pagination(page)})
    response["Cache-Control"] = "private, no-store"
    return response
