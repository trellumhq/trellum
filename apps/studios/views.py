"""Studio-scoped settings pages (members for now; repo and data sources
land with their milestones)."""
from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import roles
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import require_studio_role
from apps.core.report_access import selected_report_access_block_reason
from apps.orgs.models import OrgMembership
from apps.reports.models import Report

from .forms import DefaultAudiencesForm, StudioRepoForm
from .models import RepoPublish, StudioMembership, StudioRepo


def settings_home(request, org_slug, studio_slug):  # noqa: ARG001
    """/s/<org>/<studio>/settings/ — the studio settings hub is its first tab.

    A pure URL alias: the target enforces the admin requirement.
    """
    return redirect(f"/s/{org_slug}/{studio_slug}/settings/repo")


@require_studio_role(roles.ADMIN)
def members(request, org_slug, studio_slug):  # noqa: ARG001
    rows = (
        StudioMembership.objects.filter(studio=request.studio)
        .select_related("user")
        .order_by("user__email")
    )
    org_users = (
        OrgMembership.objects.filter(org=request.org)
        .exclude(user__studio_memberships__studio=request.studio)
        .select_related("user")
        .order_by("user__email")
    )
    return render(
        request,
        "studios/members.html",
        {
            "org": request.org,
            "studio": request.studio,
            "rows": rows,
            "org_users": org_users,
            "studio_role_choices": roles.STUDIO_ROLE_CHOICES,
        },
    )


@require_studio_role(roles.ADMIN)
def repo_settings(request, org_slug, studio_slug):  # noqa: ARG001
    repo = StudioRepo.objects.filter(studio=request.studio).first()
    audience_form = DefaultAudiencesForm(instance=request.studio)
    if request.method == "POST" and "set_default_audiences" in request.POST:
        fields = DefaultAudiencesForm.Meta.fields
        prior = {field: getattr(request.studio, field) for field in fields}
        audience_form = DefaultAudiencesForm(request.POST, instance=request.studio)
        if audience_form.is_valid():
            new = {field: audience_form.cleaned_data[field] for field in fields}
            block_reason = selected_report_access_block_reason()
            if block_reason and any(
                new[field] == Report.AUDIENCE_PRIVATE and prior[field] != new[field]
                for field in fields
            ):
                audience_form.add_error(None, block_reason)
            else:
                audience_form.save(commit=False).save(update_fields=fields)
                audit(
                    request, "studio.default_audiences_set", target=request.studio,
                    prior=prior, new=new,
                )
                if is_settings_request(request):
                    return settings_success(request, "Default audiences saved for new reports and analyses.", values=new)
                messages.success(request, "Default audiences saved for new reports and analyses.")
                return redirect(request.path + "#default-audiences")
        if is_settings_request(request):
            return settings_error(request, form=audience_form)
    if request.method == "POST" and "sync_now" in request.POST:
        # The "Sync now" button: flag the repo and let the worker's git thread
        # pull. Enhanced requests stay on the settings page; native forms redirect.
        if repo and repo.repo_url:
            repo.sync_requested = True
            repo.sync_reason = "manual"
            repo.save(update_fields=["sync_requested", "sync_reason"])
            audit(request, "git.sync_now", target=request.studio)
            if is_settings_request(request):
                return settings_success(request, "Sync scheduled — the worker pulls within seconds.", sync_requested=True)
            messages.success(request, "Sync scheduled — the worker pulls within seconds.")
        else:
            if is_settings_request(request):
                return settings_error(request, "Configure a repository first.")
            messages.error(request, "Configure a repository first.")
        return redirect(request.path)
    if request.method == "POST" and "set_mode" in request.POST:
        # The segmented Publishing control: one field, same rules as the form.
        mode = request.POST.get("publish_mode")
        if not (repo and repo.repo_url) or mode not in dict(StudioRepo.PUBLISH_CHOICES):
            if is_settings_request(request):
                return settings_error(request, "Configure a repository first.", errors={"publish_mode": ["Configure a repository first."]})
            messages.error(request, "Configure a repository first.")
            return redirect(request.path)
        if repo.publish_mode == "manual" and mode == "auto":
            # Whatever was held back publishes on the next tick.
            repo.publish_requested = True
            repo.publish_requested_by = request.user
            repo.publish_requested_to = ""
            repo.sync_requested = True
            repo.sync_reason = "manual"
        repo.publish_mode = mode
        # Only these columns: the runner writes the rest between our read and this save.
        repo.save(update_fields=[
            "publish_mode", "publish_requested", "publish_requested_by",
            "publish_requested_to", "sync_requested", "sync_reason", "updated_at",
        ])
        audit(
            request, "studio.repo_update", target=request.studio,
            repo=repo.repo_url, publish_mode=mode,
        )
        if is_settings_request(request):
            return settings_success(request, "Publishing mode saved.", publish_mode=mode, repo_configured=True)
        messages.success(
            request,
            "Every push now goes live." if mode == "auto"
            else "Pushes now wait for you to publish them.",
        )
        return redirect(request.path)
    if request.method == "POST" and "set_default_audiences" not in request.POST:
        # Captured before the form binds: is_valid() writes onto ``repo``.
        was_manual = repo is not None and repo.publish_mode == "manual"
        form = StudioRepoForm(request.POST, instance=repo)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.studio = request.studio
            obj.sync_requested = True  # sync right away with the new config
            obj.sync_reason = "manual"
            if was_manual and obj.publish_mode == "auto":
                # Whatever was held back publishes on the next tick.
                obj.publish_requested = True
                obj.publish_requested_by = request.user
            obj.save()
            audit(
                request, "studio.repo_update", target=request.studio,
                repo=obj.repo_url, publish_mode=obj.publish_mode,
            )
            if is_settings_request(request):
                return settings_success(request, "Repository settings saved — sync scheduled.", repo_configured=bool(obj.repo_url), publish_mode=obj.publish_mode, values={"token": "", "webhook_secret": ""})
            messages.success(request, "Repository settings saved — sync scheduled.")
            return redirect(request.path)
        if is_settings_request(request):
            return settings_error(request, form=form)
    else:
        form = StudioRepoForm(instance=repo)
    from apps.core.instance import base_url

    webhook_url = (
        f"{base_url()}/s/{request.org.slug}/{request.studio.slug}/api/git/webhook"
    )
    try:
        history_limit = max(1, min(int(request.GET.get("history", 20)), 200))
    except ValueError:
        history_limit = 20
    history = RepoPublish.objects.filter(studio=request.studio).select_related("published_by")
    publishes = list(history[: history_limit + 1])
    history_more = len(publishes) > history_limit
    publishes = publishes[:history_limit]
    for row in publishes:
        reports = (row.summary or {}).get("reports") or {}
        row.reports_changed = sum(len(v) for v in reports.values())
    pending = (repo.pending_changes or {}) if repo else {}
    commits = pending.get("commits") or ()
    for commit in commits:
        # git's %aI is a string; the template wants a datetime for timesince.
        try:
            commit["when"] = datetime.fromisoformat(commit.get("date") or "")
        except (TypeError, ValueError, AttributeError):
            pass  # malformed entry: rendered without a relative time
    reports = pending.get("reports") or {}
    # The runner has missed at least two polls: the remote column is stale.
    stale = bool(
        repo and repo.remote_checked_at and repo.sync_interval_minutes
        and repo.remote_checked_at
        < timezone.now() - timedelta(minutes=2 * repo.sync_interval_minutes)
    )
    return render(
        request,
        "studios/repo.html",
        {
            "org": request.org,
            "studio": request.studio,
            "form": form,
            "audience_form": audience_form,
            "audience_block_reason": selected_report_access_block_reason(),
            "repo": repo,
            "webhook_url": webhook_url,
            "pending": pending,
            "ahead": pending.get("commits_total") or len(commits),
            "rebuild_count": len(reports.get("added") or ()) + len(reports.get("modified") or ()),
            "remote_stale": stale,
            "last_publish": history.filter(status="ok").first(),
            "publishes": publishes,
            "history_limit": history_limit,
            "history_more": history_more,
        },
    )


def _theme_redirect(request):
    """Same-site-only ``next``, defaulting back to the studio dashboard --
    mirrors the safety check the retired global
    ``apps.accounts.views.theme_set`` used to do (a leading "//" is
    protocol-relative and would leave the site)."""
    fallback = f"/s/{request.org.slug}/{request.studio.slug}/"
    next_url = request.POST.get("next") or fallback
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = fallback
    return redirect(next_url)


@require_studio_role(roles.VIEWER)
@require_POST
def theme_set(request, org_slug, studio_slug):  # noqa: ARG001
    """Set the calling viewer's personal theme override for THIS studio --
    apps.core.themes.resolve_studio_theme rung 1. Both the studio header's
    Appearance picker and the report page's own in-report picker post here.
    A personal appearance choice, not an administrative act: unlike
    ``theme_set_default`` below, this is not audited (same as the retired
    global ``apps.accounts.views.theme_set``).
    """
    from trellum.themes import THEME_REGISTRY

    if request.org.lock_studio_theme:
        messages.error(request, "Your organization has locked this studio's theme.")
        return _theme_redirect(request)

    theme = (request.POST.get("theme") or "").strip()
    if theme and theme not in THEME_REGISTRY:
        messages.error(request, "That theme is not available.")
        return _theme_redirect(request)

    from apps.studios.models import StudioPreference

    if theme:
        StudioPreference.objects.update_or_create(
            user=request.user, studio=request.studio, defaults={"theme": theme}
        )
    else:
        StudioPreference.objects.filter(user=request.user, studio=request.studio).delete()
    return _theme_redirect(request)


@require_studio_role(roles.ADMIN)
@require_POST
def theme_set_default(request, org_slug, studio_slug):  # noqa: ARG001
    """Studio admin: set this studio's own default theme --
    apps.core.themes.resolve_studio_theme rung 2 (falls back straight to
    the Trellum default when left blank; the org no longer has a palette
    rung of its own to fall back to first, see explicit_studio_theme's
    docstring). The Studio settings -> Appearance page (appearance_settings
    above) posts here; so did the tab bar's admin-only chip before it moved
    off the rail. An administrative act, unlike the personal override
    above: audited.
    """
    from trellum.themes import THEME_REGISTRY

    theme = (request.POST.get("theme") or "").strip()
    if theme and theme not in THEME_REGISTRY:
        if is_settings_request(request):
            return settings_error(request, "That theme is not available.", errors={"theme": ["That theme is not available."]})
        messages.error(request, "That theme is not available.")
        return _theme_redirect(request)

    request.studio.theme = theme
    request.studio.save(update_fields=["theme"])
    audit(
        request, "studio.theme_set", target=request.studio,
        theme=theme or "(default)",
    )
    if is_settings_request(request):
        return settings_success(request, "Studio theme updated.", theme=theme)
    messages.success(request, "Studio theme updated.")
    return _theme_redirect(request)


@require_studio_role(roles.ADMIN)
def appearance_settings(request, org_slug, studio_slug):  # noqa: ARG001
    """Studio settings -> Appearance: the studio admin's own default-palette
    control (apps.core.themes.resolve_studio_theme rung 2), moved off the
    tab bar after live owner testing found it sitting right beside the
    viewer's personal picker as two identical unlabeled dropdowns
    (.lavish/theming-controls-design.html). Same admin gate as every other
    studio-settings page (Repository, Data sources, Members).

    A pure GET render -- the form on this page posts straight to
    ``theme_set_default`` above (same POST endpoint the old tab-bar chip
    used), just with ``next`` pointed back at this page instead of the
    dashboard, so saving reloads the settings page rather than bouncing the
    admin out of settings.
    """
    from trellum.themes import DEFAULT_THEME_NAME

    return render(
        request,
        "studios/appearance.html",
        {
            "org": request.org,
            "studio": request.studio,
            "default_theme_name": DEFAULT_THEME_NAME,
        },
    )


@require_studio_role(roles.ADMIN)
@require_POST
def member_set(request, org_slug, studio_slug):  # noqa: ARG001
    m = OrgMembership.objects.filter(
        org=request.org, user_id=request.POST.get("user_id")
    ).first()
    if m is None:
        raise Http404
    role = request.POST.get("role", "")
    if role == "":
        StudioMembership.objects.filter(user=m.user, studio=request.studio).delete()
        audit(request, "studio.member_remove", target=m.user, studio=request.studio.slug)
    elif role in dict(roles.STUDIO_ROLE_CHOICES):
        StudioMembership.objects.update_or_create(
            user=m.user, studio=request.studio, defaults={"role": role}
        )
        audit(
            request,
            "studio.member_set",
            target=m.user,
            studio=request.studio.slug,
            role=role,
        )
    else:
        if is_settings_request(request):
            return settings_error(request, "Invalid role.", errors={"role": ["Invalid role."]})
        messages.error(request, "Invalid role.")
    if is_settings_request(request):
        return settings_success(request, "Studio member role saved.")
    return redirect(f"/s/{request.org.slug}/{request.studio.slug}/settings/members")
