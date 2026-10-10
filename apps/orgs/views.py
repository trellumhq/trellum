"""Org-level pages: home/switcher, studios list, members, invites."""
from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Invitation
from apps.core import roles
from apps.core.audit import audit
from apps.core.form_responses import is_settings_request, settings_error, settings_success
from apps.core.permissions import (
    get_effective,
    require_org_role,
    visible_studios,
)
from apps.core.report_access import visible_reports
from apps.studios.models import Studio, StudioMembership

from .forms import InviteForm, StudioCreateForm
from .models import Organization, OrgMembership, PermissionGroupMembership

User = get_user_model()

#: Rows per page on the management list screens. These tables were unbounded —
#: an org with hundreds of members rendered every one of them, with a nested
#: form per member per studio.
PAGE_SIZE = 50


def paginate(request, qs, per_page: int = PAGE_SIZE):
    """One pager for every list screen (the audit log had the only one)."""
    from django.core.paginator import Paginator

    return Paginator(qs, per_page).get_page(request.GET.get("page"))


def home(request):
    """THE hub: the active organization's studios, plus entry points to
    everything else. There is no separate org page — `?org=<slug>` switches
    the hub (remembered in the session)."""
    if not request.user.is_authenticated:
        return redirect("login")

    if request.user.is_superuser:
        orgs = list(Organization.objects.filter(is_active=True).order_by("slug"))
    else:
        orgs = list(
            Organization.objects.filter(
                is_active=True, memberships__user=request.user
            ).order_by("slug")
        )

    active = None
    wanted = request.GET.get("org") or request.session.get("active_org")
    if wanted:
        active = next((o for o in orgs if o.slug == wanted), None)
    if active is None and orgs:
        active = orgs[0]
    if active is not None:
        request.session["active_org"] = active.slug
        er = get_effective(request, active)
        # Decorator-style attributes so the shell renders consistently.
        request.org = active
        request.org_roles = er
        studios = list(visible_studios(request.user, active))
    else:
        er = None
        studios = []

    # Cheap DB-only health hints per studio card (no disk reads).
    from apps.runner.models import Run

    cards = []
    for studio in studios:
        reports = visible_reports(
            request.user, studio.reports.filter(present_in_scan=True)
        )
        last_run = (
            Run.objects.filter(report__in=reports)
            .exclude(status__in=Run.ACTIVE_STATUSES)
            .order_by("-created_at")
            .first()
        )
        cards.append(
            {
                "studio": studio,
                "report_count": reports.count(),
                "last_run": last_run,
            }
        )

    is_org_admin = bool(er and er.is_org_admin)
    return render(
        request,
        "orgs/home.html",
        {
            "orgs": orgs,
            "org": active,
            "cards": cards,
            "studios": studios,
            "is_org_admin": is_org_admin,
            "getting_started": _getting_started(active) if is_org_admin else None,
        },
    )


def may_create_org(user) -> bool:
    """Who may create an organization.

    Operators always. Everyone else only when the deployment has
    ``TRELLUM_ORG_SELF_SIGNUP`` on. The form page and the POST
    must apply the *same* rule, or a user is shown a form they cannot submit.
    """
    if not user.is_authenticated:
        return False
    return bool(getattr(user, "is_operator", False)) or settings.TRELLUM_ORG_SELF_SIGNUP


def _getting_started(org):
    """The post-setup checklist shown on the hub.

    Returns None once it has nothing left to say — dismissed by an admin, or
    all four steps done. The steps are independent and unordered; each one
    reports its own state from the database, so the card is always truthful
    even when the work was done from somewhere else (the CLI, a fixture, an
    invitation accepted last week).
    """
    if org is None or org.onboarding_dismissed_at is not None:
        return None

    from apps.datasources.models import DataSource
    from apps.studios.models import StudioRepo

    first_studio = org.studios.order_by("pk").first()
    has_repo = StudioRepo.objects.filter(
        studio__org=org, repo_url__gt=""
    ).exists()
    has_source = DataSource.objects.filter(
        models.Q(org=org) | models.Q(studio__org=org)
    ).exists()
    has_people = (
        org.memberships.count() > 1
        or Invitation.objects.filter(org=org).exists()
    )

    from django.urls import reverse

    studios_url = reverse("org-studios", args=[org.slug])
    # Steps 2 and 3 need a studio to point at. Until one exists they fall back
    # to the studios screen, which is where step 1 sends you anyway.
    if first_studio is not None:
        repo_url = reverse("studio-repo", args=[org.slug, first_studio.slug])
        sources_url = reverse("studio-datasources", args=[org.slug, first_studio.slug])
    else:
        repo_url = sources_url = studios_url

    # With a repository connected, the sources are declared in git and the
    # step is about credentials: done once declared sources exist and none
    # waits for any (a portal-only source still counts, as before), pointing
    # at the first studio that still has one waiting.
    sources_item = {
        "label": "Add a data source",
        "help": "Credentials are encrypted and injected only into that studio's builds.",
        "href": sources_url,
        "done": has_source,
    }
    if has_repo:
        from apps.datasources.status import SourceState, source_states

        # Only studios with a declaration have states worth computing.
        declared = [
            (studio, s)
            for studio in org.studios.filter(repo_sources__present=True).distinct().order_by("pk")
            for s in source_states(studio)
            if s.declared
        ]
        waiting = [
            (studio, s) for studio, s in declared
            if s.state in (SourceState.NEEDS_CREDENTIALS, SourceState.NEEDS_UPLOAD)
        ]
        n = len(waiting)
        sources_item = {
            "label": (
                f"{n} data source{'s' if n != 1 else ''} need{'s' if n == 1 else ''} credentials"
                if n else "Configure data sources"
            ),
            "help": "Sources are declared in the repository; the portal holds their credentials.",
            "href": (
                reverse("studio-datasources", args=[org.slug, waiting[0][0].slug])
                if n else sources_url
            ),
            "done": n == 0 and (has_source or bool(declared)),
        }

    items = [
        {
            "label": "Create your first studio",
            "help": "A studio is where a set of reports lives.",
            "href": studios_url,
            "done": first_studio is not None,
        },
        {
            "label": "Point it at a reports repository",
            "help": "Reports come from a git repo; pushes sync and rebuild them.",
            "href": repo_url,
            "done": has_repo,
        },
        sources_item,
        {
            "label": "Invite your team",
            "help": "Invitations carry org role, groups and per-studio access.",
            "href": reverse("org-invites", args=[org.slug]),
            "done": has_people,
        },
    ]
    if all(i["done"] for i in items):
        return None
    return {"items": items, "remaining": sum(1 for i in items if not i["done"])}


@require_org_role(roles.ORG_ADMIN)
@require_POST
def onboarding_dismiss(request, org_slug):  # noqa: ARG001 - org comes from the decorator
    request.org.onboarding_dismissed_at = timezone.now()
    request.org.save(update_fields=["onboarding_dismissed_at"])
    return redirect("/")


def org_new(request):
    """The create-an-organization screen.

    This form used to sit at the bottom of the hub, where it took more vertical
    space than the studio cards above it and showed an instance-level action on
    a tenant's page — for the one user in the instance who can use it.
    """
    from apps.accounts.forms import OrgCreateForm

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    if not may_create_org(request.user):
        raise Http404  # don't advertise a page they cannot use
    return render(request, "orgs/new.html", {"form": OrgCreateForm()})


@require_POST
def org_create(request):
    """Create an organization.

    The superuser requirement moved out of the decorator and into
    :func:`may_create_org` so self-signup can open it without a code change.
    """
    from apps.accounts.forms import OrgCreateForm
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())

    if not may_create_org(request.user):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("organization creation is not open on this instance")

    form = OrgCreateForm(request.POST)
    if form.is_valid():
        org = Organization.objects.create(
            slug=form.cleaned_data["slug"], name=form.cleaned_data["name"]
        )
        OrgMembership.objects.get_or_create(
            user=request.user, org=org, defaults={"role": roles.ORG_ADMIN}
        )
        audit(request, "org.create", target=org, org=org)
        messages.success(request, f"Organization “{org.name}” created.")
        return redirect(f"/orgs/{org.slug}/")
    for err in form.errors.values():
        messages.error(request, "; ".join(err))
    return redirect("org-new")


def org_home(request, org_slug):
    """Legacy URL: the hub took over. Old links keep working."""
    return redirect(f"/?org={org_slug}")


@require_org_role(roles.ORG_ADMIN)
def studios_settings(request, org_slug):  # noqa: ARG001
    """Org settings → Studios: create, rename (display name only), delete.

    The hub is a navigation surface; studio management lives here."""
    from django.db.models import Count

    if request.method == "POST" and request.POST.get("action") == "rename":
        studio = get_object_or_404(Studio, org=request.org, pk=request.POST.get("studio_id"))
        name = (request.POST.get("name") or "").strip()
        if name:
            studio.name = name
            studio.description = (request.POST.get("description") or "").strip()
            studio.save(update_fields=["name", "description"])
            audit(request, "studio.rename", target=studio, name=name)
            if is_settings_request(request):
                return settings_success(request, "Studio updated.", values={"name": studio.name, "description": studio.description})
            messages.success(request, "Studio updated.")
        if is_settings_request(request):
            return settings_error(request, errors={"name": ["Enter a studio name."]})
        return redirect(request.path)

    from django.db.models import Q

    qs = (
        Studio.objects.filter(org=request.org)
        .annotate(member_count=Count("memberships", distinct=True))
        .annotate(report_count=Count("reports", distinct=True))
        .order_by("slug")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    total = qs.count()
    page = paginate(request, qs)

    from apps.orgs import quotas

    return render(
        request,
        "orgs/studios.html",
        {
            "org": request.org,
            "rows": page.object_list,
            "studio_form": StudioCreateForm(org=request.org),
            "page": page,
            "q": q,
            "total": total,
            # Shown only when limits actually apply. Without this an admin
            # whose builds are queueing behind a concurrency cap has no way to
            # find out why from their own UI.
            "usage": quotas.usage_state(request.org),
        },
    )


@require_org_role(roles.ORG_ADMIN)
@require_POST
def studio_create(request, org_slug):  # noqa: ARG001
    from apps.orgs import quotas

    decision = quotas.check_studio_allowed(request.org)
    if not decision.allowed:
        messages.error(request, decision.reason)
        return redirect(f"/orgs/{request.org.slug}/settings/studios")

    form = StudioCreateForm(request.POST, org=request.org)
    if form.is_valid():
        studio = Studio.objects.create(
            org=request.org,
            slug=form.cleaned_data["slug"],
            name=form.cleaned_data["name"],
            description=form.cleaned_data.get("description", ""),
        )
        studio.ensure_dirs()
        audit(request, "studio.create", target=studio)
        messages.success(request, f"Studio “{studio.name}” created.")
    else:
        for err in form.errors.values():
            messages.error(request, "; ".join(err))
    return redirect(f"/orgs/{request.org.slug}/settings/studios")


@require_org_role(roles.ORG_ADMIN)
@require_POST
def org_delete(request, org_slug):  # noqa: ARG001
    """Delete the whole organization: rows only, files retained.

    This is the exit a one-person organization needs — its only admin cannot
    "hand the admin role to someone else first" (the self-deletion guard's
    advice) when there is nobody else, so without this they could never leave.

    Same contract as studio_delete, one level up: the audit row is written
    BEFORE the delete because its ``slug`` metadata is what
    apps.core.retention._deletion_times dates the orphaned tree by — the org
    row itself is about to stop existing, and the AuditLog.org FK goes NULL
    with it. Members lose this membership and keep their accounts.
    """
    org = request.org
    if request.POST.get("confirm_slug") != org.slug:
        messages.error(request, "Type the organization slug to confirm deletion.")
        return redirect(f"/orgs/{org.slug}/settings/studios")
    audit(request, "org.delete", target=org, slug=org.slug)
    org.delete()  # DB rows only; files under /data are kept until pruned
    messages.success(
        request,
        f"Organization “{org_slug}” deleted. Files on disk are retained until "
        "pruned, and members keep their accounts.",
    )
    return redirect("/")


@require_org_role(roles.ORG_ADMIN)
@require_POST
def studio_delete(request, org_slug, studio_slug):  # noqa: ARG001
    studio = get_object_or_404(Studio, org=request.org, slug=studio_slug)
    if request.POST.get("confirm_slug") != studio.slug:
        messages.error(request, "Type the studio slug to confirm deletion.")
        return redirect(f"/orgs/{request.org.slug}/settings/studios")
    audit(request, "studio.delete", target=studio, slug=studio.slug)
    studio.delete()  # DB rows only; files under /data are kept until pruned
    messages.success(request, f"Studio “{studio_slug}” deleted (files on disk retained).")
    return redirect(f"/orgs/{request.org.slug}/settings/studios")


# ── Members ──────────────────────────────────────────────────────────────────

def settings_home(request, org_slug):  # noqa: ARG001
    """/orgs/<org>/settings/ — the org settings hub is its first tab.

    A pure URL alias: the target enforces the org-admin requirement.
    """
    return redirect(f"/orgs/{org_slug}/settings/members")


@require_org_role(roles.ORG_ADMIN)
def appearance_settings(request, org_slug):  # noqa: ARG001
    """Org settings -> Appearance: the org-admin half of theming, moved off
    the personal Account page after live owner testing flagged an org-admin
    "Lock it" control sitting on a page titled "Your account" as nonsense
    (.lavish/theming-explained.html "The fix").

    Sets ``Organization.default_mode`` (Light/Dark/Auto for the plain
    Trellum-looking org and settings surfaces, and any studio still on the
    Trellum default -- apps.core.context_processors.shell threads it into
    org_default_mode for templates/_theme_boot_mgmt.html) and
    ``lock_studio_theme`` (whether viewers may override a studio's palette
    for themselves -- apps.core.themes.resolve_studio_theme rung 1).

    The org no longer picks a default *palette* here: ``Organization
    .default_theme`` stays on the model (unused -- apps.core.themes
    .explicit_studio_theme has since dropped it as a resolver rung
    entirely, .lavish/theming-controls-design.html decision (2)), but
    nothing in the UI reads or writes it any more -- a studio's own palette
    is a purely per-studio choice now, set from Studio settings ->
    Appearance.
    """
    if request.method == "POST":
        mode = (request.POST.get("default_mode") or "").strip()
        valid_modes = {choice for choice, _ in Organization._meta.get_field("default_mode").choices}
        if mode not in valid_modes:
            if is_settings_request(request):
                return settings_error(request, "That mode is not available.", errors={"default_mode": ["That mode is not available."]})
            messages.error(request, "That mode is not available.")
            return redirect(request.path)

        request.org.default_mode = mode
        request.org.lock_studio_theme = bool(request.POST.get("lock_studio_theme"))
        request.org.save(update_fields=["default_mode", "lock_studio_theme"])
        audit(
            request, "org.appearance_set", target=request.org,
            default_mode=mode, lock=request.org.lock_studio_theme,
        )
        if is_settings_request(request):
            return settings_success(request, "Organization appearance saved.", default_mode=mode)
        messages.success(request, "Organization appearance saved.")
        return redirect(request.path)

    return render(request, "orgs/appearance.html", {"org": request.org})


def _parse_retention_days(raw: str) -> tuple[bool, int | None]:
    """Blank -> ``(True, None)`` (inherit). Digits -> ``(True, int)``.
    Anything else, or a number the column cannot hold, -> ``(False, None)``.

    Form parsing, not policy -- kept out of apps.core.retention, which only
    resolves and sweeps windows that are already valid ints.
    """
    raw = (raw or "").strip()
    if not raw:
        return True, None
    if not raw.isdigit():
        return False, None
    value = int(raw)
    if value > 32767:  # PositiveSmallIntegerField's own ceiling
        return False, None
    return True, value


@require_org_role(roles.ORG_ADMIN)
def retention_settings(request, org_slug):  # noqa: ARG001
    """Org settings -> Data retention (internal planning ticket #110): the two per-org windows,
    what is about to expire under them, and a dry-run preview of tonight's
    sweep.

    Both fields accept blank (inherit the instance default) or a whole
    number of days. Built data additionally has a ceiling
    (``settings.RETENTION["built_data_days"]``) an organization may only
    tighten, never loosen -- ``apps.core.retention.built_window`` enforces
    that regardless of what reaches it, but this view refuses an over-ceiling
    submission itself, so the admin is told which number actually won rather
    than having it silently clamped underneath them.

    Submitting 0 for the built window is not "keep forever" -- see
    ``built_window``'s own zero-trap note -- so it is normalized to blank
    here rather than stored literally: the two already behave identically to
    the resolver, and leaving a literal 0 in the column would read as
    "disabled" to the next admin who opens this page. The upload window has
    no ceiling, so its 0 really does mean forever, and is stored as typed.
    """
    from apps.core import retention
    from apps.core.models import OpsState

    org = request.org
    ceiling = settings.RETENTION["built_data_days"]

    if request.method == "POST":
        built_ok, built_value = _parse_retention_days(
            request.POST.get("retention_built_days", "")
        )
        upload_ok, upload_value = _parse_retention_days(
            request.POST.get("retention_abandoned_upload_days", "")
        )
        if not built_ok or not upload_ok:
            if is_settings_request(request):
                return settings_error(request, errors={name: ["Enter a whole number of days, or leave blank."] for name, valid in [("retention_built_days", built_ok), ("retention_abandoned_upload_days", upload_ok)] if not valid})
            messages.error(
                request,
                "Enter a whole number of days for each window, or leave it "
                "blank to use the default.",
            )
            return redirect(request.path)

        if ceiling and built_value and built_value > ceiling:
            if is_settings_request(request):
                return settings_error(request, errors={"retention_built_days": [f"Enter {ceiling} days or fewer, or leave blank."]})
            messages.error(
                request,
                f"Built data cannot be kept longer than {ceiling} days on "
                f"this instance. Enter {ceiling} or fewer, or leave it blank "
                f"to use that default.",
            )
            return redirect(request.path)

        if built_value == 0 and ceiling:
            built_value = None
            if not is_settings_request(request):
                messages.info(
                    request,
                    f"0 means the {ceiling}-day instance default, not “keep "
                    f"forever” — saved as “use the default.”",
                )

        org.retention_built_days = built_value
        org.retention_abandoned_upload_days = upload_value
        org.save(update_fields=["retention_built_days", "retention_abandoned_upload_days"])
        audit(
            request, "org.retention_set", target=org,
            retention_built_days=built_value,
            retention_abandoned_upload_days=upload_value,
        )
        if is_settings_request(request):
            return settings_success(request, "Data retention settings saved.",
                values={"retention_built_days": built_value, "retention_abandoned_upload_days": upload_value},
                refresh=["retention-built", "retention-upload", "retention-expiring", "retention-preview"])
        messages.success(request, "Data retention settings saved.")
        return redirect(request.path)

    cleanup_state = OpsState.objects.filter(key="cleanup").first()
    context = {
        "org": org,
        "ceiling": ceiling,
        "built_days": org.retention_built_days,
        "upload_days": org.retention_abandoned_upload_days,
        # None and a stored 0 resolve identically for the built window in
        # every ceiling state (built_window's own "not chosen" check treats
        # them the same) -- so both display as "inherited" here too. Saving
        # already normalizes a submitted 0 to None (the zero trap below),
        # but a row written before this page existed could still hold a
        # literal 0, and it must never be shown as "chosen."
        "built_inherited": not org.retention_built_days,
        "upload_inherited": org.retention_abandoned_upload_days is None,
        "resolved_built": retention.built_window(org),
        "resolved_upload": retention.upload_window(org),
        "default_upload": settings.RETENTION["abandoned_upload_days"],
        "expiring": retention.expiring_soon(org),
        # Account dormancy is an instance policy, not one of this org's two
        # windows -- there is no field for it on this form. It is shown here
        # because this is the page that answers "what is about to be deleted
        # and when", and because the admin reading it is the person who can
        # tell a colleague on long leave to sign in once. Empty list when the
        # instance has dormancy switched off, which is the default.
        "dormant": retention.dormant_soon(org),
        "dormant_days": settings.RETENTION["dormant_days"],
        "last_purge": retention.last_purge_event(org),
        "cleanup_ran_at": cleanup_state.ran_at if cleanup_state else None,
    }
    if request.GET.get("preview"):
        context["preview"] = retention.org_preview(org)
    return render(request, "orgs/retention.html", context)


@require_org_role(roles.ORG_ADMIN)
def members(request, org_slug):  # noqa: ARG001
    """The reference screen: each member's EFFECTIVE access per studio
    (direct + group grants + org-admin implication, max-wins), with the
    source of each role made visible."""
    from apps.core.permissions import effective_roles

    from django.db.models import Q

    studios = list(Studio.objects.filter(org=request.org).order_by("slug"))
    qs = (
        OrgMembership.objects.filter(org=request.org)
        .select_related("user")
        .prefetch_related("user__permission_group_memberships__group")
        .order_by("user__email")
    )
    q = (request.GET.get("q") or "").strip()
    if q:
        # display_name is a property, not a column — search the stored name.
        qs = qs.filter(Q(user__email__icontains=q) | Q(user__name__icontains=q))
    total = qs.count()
    # Paginated because the per-row work below is O(members x studios): an
    # unbounded org rendered every member's effective access on one page.
    page = paginate(request, qs)

    member_rows = []
    for m in page.object_list:
        groups = [
            pgm.group
            for pgm in m.user.permission_group_memberships.all()
            if pgm.group.org_id == request.org.pk
        ]
        direct = {
            sm.studio_id: sm.role
            for sm in StudioMembership.objects.filter(user=m.user, studio__org=request.org)
        }
        er = effective_roles(m.user, request.org)
        effective = []
        for studio in studios:
            role = er.role_for(studio)
            if role is None:
                continue
            if er.is_org_admin:
                source = "org admin"
            elif direct.get(studio.pk) == role:
                source = "direct"
            else:
                source = "group"
            effective.append({"studio": studio, "role": role, "source": source})
        member_rows.append(
            {
                "m": m,
                "groups": groups,
                "effective": effective,
                "studio_editor": [
                    {"studio": s, "direct_role": direct.get(s.pk, "")} for s in studios
                ],
            }
        )
    return render(
        request,
        "orgs/members.html",
        {
            "org": request.org,
            "member_rows": member_rows,
            "org_roles_choices": roles.ORG_ROLE_CHOICES,
            "studio_role_choices": roles.STUDIO_ROLE_CHOICES,
            "studios": studios,
            "page": page,
            "q": q,
            "total": total,
        },
    )


def _members_redirect(request):
    """Back to the members list the admin was looking at.

    These handlers used to redirect to the bare list URL, so changing a role
    from a searched or paged view silently threw away the filter — one of the
    reasons a change looked like it had not happened.
    """
    base = f"/orgs/{request.org.slug}/settings/members"
    # Only echo back parameters this screen owns; the value arrives via POST.
    keep = {k: v for k, v in ((k, request.POST.get(k)) for k in ("q", "page")) if v}
    if keep.get("page") == "1":
        keep.pop("page")  # the default page is not worth carrying in the URL
    if not keep:
        return redirect(base)
    from urllib.parse import urlencode

    return redirect(f"{base}?{urlencode(keep)}")


def _target_membership(request, org, user_id) -> OrgMembership:
    m = OrgMembership.objects.filter(org=org, user_id=user_id).select_related("user").first()
    if m is None:
        raise Http404
    return m


@require_org_role(roles.ORG_ADMIN)
@require_POST
def member_set_role(request, org_slug, user_id):  # noqa: ARG001
    m = _target_membership(request, request.org, user_id)
    role = request.POST.get("role")
    if role not in dict(roles.ORG_ROLE_CHOICES):
        if is_settings_request(request):
            return settings_error(request, "Invalid role.", errors={"role": ["Invalid role."]})
        messages.error(request, "Invalid role.")
        return _members_redirect(request)
    if (
        m.role == roles.ORG_ADMIN
        and role != roles.ORG_ADMIN
        and not OrgMembership.objects.filter(org=request.org, role=roles.ORG_ADMIN)
        .exclude(pk=m.pk)
        .exists()
    ):
        if is_settings_request(request):
            return settings_error(request, "An organization needs at least one direct admin.", errors={"role": ["An organization needs at least one direct admin."]})
        messages.error(request, "An organization needs at least one direct admin.")
        return _members_redirect(request)
    m.role = role
    m.save(update_fields=["role"])
    audit(request, "member.set_role", target=m.user, role=role)
    if is_settings_request(request):
        return settings_success(request, f"{m.user.email} is now an organization {role}.", refresh=[f"member-effective-{m.user_id}"])
    messages.success(request, f"{m.user.email} is now an organization {role}.")
    return _members_redirect(request)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def member_remove(request, org_slug, user_id):  # noqa: ARG001
    m = _target_membership(request, request.org, user_id)
    if m.user_id == request.user.pk:
        messages.error(request, "You cannot remove yourself.")
        return _members_redirect(request)
    with transaction.atomic():
        StudioMembership.objects.filter(user=m.user, studio__org=request.org).delete()
        PermissionGroupMembership.objects.filter(
            user=m.user, group__org=request.org
        ).delete()
        audit(request, "member.remove", target=m.user, email=m.user.email)
        m.delete()
    messages.success(request, "Member removed.")
    return _members_redirect(request)


@require_org_role(roles.ORG_ADMIN)
def member_export(request, org_slug, user_id):  # noqa: ARG001
    """Download everything this installation holds about one member (GDPR
    Art. 15 access, Art. 20 portability). What is and is not in the file, and
    why, is in apps.orgs.personal_data.export."""
    from django.http import JsonResponse

    from apps.orgs import personal_data

    m = _target_membership(request, request.org, user_id)
    payload = personal_data.export(m.user, request.org)
    audit(request, "person.export", target=m.user)
    # Named by primary key, not by address: the file gets forwarded, and the
    # person it is about is stated inside it anyway.
    stamp = timezone.now().strftime("%Y%m%d")
    response = JsonResponse(payload, json_dumps_params={"indent": 2, "ensure_ascii": False})
    response["Content-Disposition"] = (
        f'attachment; filename="personal-data-{request.org.slug}-{m.user.pk}-{stamp}.json"'
    )
    return response


@require_org_role(roles.ORG_ADMIN)
def member_erase(request, org_slug, user_id):  # noqa: ARG001
    """Erase a person (GDPR Art. 17). GET is the confirmation screen an admin
    has to be able to show someone else; POST does it, and cannot be undone.

    Three refusals, all before the screen renders, because a confirmation
    screen for something that will be rejected is worse than no screen.
    """
    from apps.orgs import personal_data

    m = _target_membership(request, request.org, user_id)
    if m.user_id == request.user.pk:
        messages.error(request, "You cannot erase yourself.")
        return _members_redirect(request)
    if m.user.is_operator:
        # The operator console is an instance-wide role. One organization's
        # admin does not get to end the account that supports every other one.
        messages.error(request, "Instance operators cannot be erased from an organization.")
        return _members_redirect(request)
    if (
        m.role == roles.ORG_ADMIN
        and not OrgMembership.objects.filter(org=request.org, role=roles.ORG_ADMIN)
        .exclude(pk=m.pk)
        .exists()
    ):
        messages.error(request, "An organization needs at least one direct admin.")
        return _members_redirect(request)

    if request.method == "POST":
        if request.POST.get("confirm_email") != m.user.email:
            messages.error(request, "Type the member’s email address to confirm.")
            return redirect(request.path)
        result = personal_data.erase(request, m.user, request.org)
        if result["full"]:
            messages.success(
                request,
                f"Erased. {sum(result['removed'].values())} records were deleted and the "
                f"account no longer exists as a person.",
            )
        else:
            messages.success(
                request,
                f"Erased from {request.org.name}. {sum(result['removed'].values())} records "
                f"were deleted; the account itself belongs to other organizations and "
                f"was left standing.",
            )
        return redirect(f"/orgs/{request.org.slug}/settings/members")

    return render(
        request,
        "orgs/member_erase.html",
        {"org": request.org, "subject": m.user, "preview": personal_data.preview(m.user, request.org)},
    )


@require_org_role(roles.ORG_ADMIN)
@require_POST
def member_set_studio_role(request, org_slug, user_id):  # noqa: ARG001
    m = _target_membership(request, request.org, user_id)
    studio = get_object_or_404(Studio, org=request.org, pk=request.POST.get("studio_id"))
    role = request.POST.get("role", "")
    if role == "":
        StudioMembership.objects.filter(user=m.user, studio=studio).delete()
        audit(request, "member.revoke_studio", target=m.user, studio=studio.slug)
        if is_settings_request(request):
            return settings_success(request, f"{m.user.email} no longer has a direct role in {studio.name}.", refresh=[f"member-effective-{m.user_id}"])
        messages.success(request, f"{m.user.email} no longer has a direct role in {studio.name}.")
    elif role in dict(roles.STUDIO_ROLE_CHOICES):
        StudioMembership.objects.update_or_create(
            user=m.user, studio=studio, defaults={"role": role}
        )
        audit(request, "member.set_studio_role", target=m.user, studio=studio.slug, role=role)
        if is_settings_request(request):
            return settings_success(request, f"{m.user.email} is now {role} in {studio.name}.", refresh=[f"member-effective-{m.user_id}"])
        messages.success(request, f"{m.user.email} is now {role} in {studio.name}.")
    else:
        if is_settings_request(request):
            return settings_error(request, "Invalid role.", errors={"role": ["Invalid role."]})
        messages.error(request, "Invalid role.")
    return _members_redirect(request)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def member_unlock(request, org_slug, user_id):  # noqa: ARG001
    """Clear a login lockout for one of this org's own members. Email-scoped
    only -- see apps.accounts.throttle.unlock's docstring for why an admin
    unlocking a victim must not need to know an attacker's IP."""
    from apps.accounts import throttle

    m = _target_membership(request, request.org, user_id)
    cleared = throttle.unlock(m.user.email)
    audit(request, "auth.unlock", target=m.user)
    if cleared:
        messages.success(request, f"Login lockout cleared for {m.user.email}.")
    else:
        messages.info(request, f"{m.user.email} was not locked out.")
    return _members_redirect(request)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def member_reset_mfa(request, org_slug, user_id):  # noqa: ARG001
    """Lost-device recovery for one of this org's own members."""
    from apps.accounts import mfa

    m = _target_membership(request, request.org, user_id)
    if m.user.is_operator:
        messages.error(request, "Instance operators' MFA cannot be reset from an organization.")
        return _members_redirect(request)
    if not m.user.has_mfa:
        messages.error(request, f"{m.user.email} does not have MFA enrolled.")
        return _members_redirect(request)
    mfa.remove_device(m.user)
    m.user.bump_auth_epoch()
    audit(request, "auth.mfa_reset", target=m.user, by_admin=True)
    messages.success(
        request,
        f"MFA reset for {m.user.email}. Their sessions were ended; they will "
        f"be asked to re-enroll next time a policy requires it.",
    )
    return _members_redirect(request)


# ── Permission groups ───────────────────────────────────────────────────────

# ── AI settings ─────────────────────────────────────────────────────────────────

def _assistant_saved_connection(cfg):
    if not cfg:
        return None
    from apps.assistant.provider_registry import effective_base_url
    return {"provider": cfg.provider, "base_url": effective_base_url(cfg.provider, cfg.base_url),
            "auth_mode": cfg.auth_mode, "cloud_config": cfg.cloud_config, "model": cfg.model}


def _assistant_check_updates(cfg):
    stored = cfg.connection_check if cfg else {}
    checks = stored.get("checks", {}) if cfg and stored.get("revision") == cfg.config_revision else {}
    updates = {}
    for name, label in (("chat", "Chat"), ("alerts", "Alerts"), ("usage", "Cost metering")):
        check = checks.get(name)
        state = ("Verified" if check.get("ok") else "Unavailable") if check else "Not tested"
        updates["assistant-" + name + "-check"] = label + ": " + state + (" — " + check.get("message", "") if check else "")
    return updates


def _assistant_pricing_state(config):
    if config.price() is None:
        return False, "Cost estimates are unavailable without pricing. Prices are optional while both budgets are blank."
    if config.price_in is not None and config.price_out is not None:
        return True, "Manual prices are set; cost estimates are available."
    return True, "Built-in model rates are available; a manual override is optional."

@require_org_role(roles.ORG_ADMIN)
def assistant_settings(request, org_slug):  # noqa: ARG001
    """Bring-your-own-key configuration shared by chat and alert evaluations."""
    from .forms import AssistantConfigForm
    from .models import OrgAssistantConfig
    from apps.assistant.provider_registry import PROVIDERS

    cfg = OrgAssistantConfig.objects.filter(org=request.org).first()
    if request.method == "POST":
        form = AssistantConfigForm(request.POST, instance=cfg, org=request.org)
        valid = form.is_valid()
        if valid:
            from django.core.exceptions import ValidationError
            try:
                form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
                valid = False
        if valid:
            audit(
                request, "assistant.config.update", target=request.org,
                enabled=form.instance.enabled, provider=form.instance.provider,
                model=form.instance.model,
            )
            from apps.assistant import llm

            config = llm.LLMConfig.from_config(form.instance)
            hints = llm.mismatch_hints(config)
            readiness_ok, readiness_reason = llm.is_available(request.org)
            pricing_known, pricing_state = _assistant_pricing_state(config)
            open_pricing = (
                not pricing_known and (
                    form.instance.monthly_budget_usd is not None
                    or form.instance.per_user_budget_usd is not None
                )
            )
            message = "AI settings saved." + (" " + " ".join(hints) if hints else "")
            if is_settings_request(request):
                return settings_success(
                    request, message,
                    values={name: "" for name in ("api_key", "access_key_id", "secret_access_key", "session_token", "client_secret", "service_account")},
                    config_revision=form.instance.config_revision,
                    saved_connection=_assistant_saved_connection(form.instance),
                    checks=form.instance.connection_check.get("checks", {}),
                    has_key=bool(form.instance.api_key),
                    has_cloud_credentials=bool(form.instance.cloud_credentials),
                    assistant_ready=readiness_ok,
                    assistant_open_pricing=open_pricing,
                    updates={
                        "assistant-key-stored": "(a key is stored — blank keeps it)" if form.instance.api_key else "(none stored yet)",
                        "assistant-readiness-title": "AI is ready based on saved settings." if readiness_ok else "AI is unavailable based on saved settings.",
                        "assistant-readiness-reason": readiness_reason or "Use Test connection below to verify provider access.",
                        "assistant-pricing-state": pricing_state,
                        **_assistant_check_updates(form.instance),
                    },
                )
            messages.success(request, "AI settings saved.")
            for hint in hints:
                messages.info(request, hint)
            return redirect(request.path)
        if is_settings_request(request):
            return settings_error(request, form=form)
        # ModelForm validation mutates its instance; status describes saved settings.
        cfg = OrgAssistantConfig.objects.filter(org=request.org).first()
    else:
        form = AssistantConfigForm(instance=cfg, org=request.org)

    from apps.assistant import llm
    readiness_ok, readiness_reason = llm.is_available(request.org)
    if cfg:
        pricing_known, pricing_state = _assistant_pricing_state(llm.LLMConfig.from_config(cfg))
    else:
        pricing_known, pricing_state = False, "Cost estimates are unavailable without pricing. Prices are optional while both budgets are blank."
    pricing_blocks_budget = bool(
        cfg and not pricing_known and (
            cfg.monthly_budget_usd is not None or cfg.per_user_budget_usd is not None
        )
    )
    advanced_open = (form.advanced_open() or pricing_blocks_budget
                     or form["provider"].value() in {"azure", "litellm", "custom"}
                     or form["auth_mode"].value() == "none")

    usage = None
    if cfg is not None:
        from apps.assistant.models import LlmUsage, month_start

        usage = {
            "month": month_start(),
            "org_total": LlmUsage.month_total(request.org),
            "rows": list(
                LlmUsage.objects.filter(org=request.org, month=month_start())
                .select_related("user")
                .order_by("-cost_usd")[:20]
            ),
        }
    return render(
        request,
        "orgs/assistant.html",
        {
            "org": request.org,
            "form": form,
            "cfg": cfg,
            "provider_presets": PROVIDERS,
            "check_updates": _assistant_check_updates(cfg),
            "saved_connection": _assistant_saved_connection(cfg),
            "has_cloud_credentials": bool(cfg and cfg.cloud_credentials),
            "has_key": bool(cfg and cfg.api_key),
            "readiness_ok": readiness_ok,
            "readiness_reason": readiness_reason,
            "pricing_state": pricing_state,
            "advanced_open": advanced_open,
            "usage": usage,
        },
    )


@require_org_role(roles.ORG_ADMIN)
@require_POST
def assistant_test(request, org_slug):  # noqa: ARG001
    """One tiny model call through the config as SAVED, answered as JSON."""
    from apps.assistant import llm

    from .models import OrgAssistantConfig

    cfg = OrgAssistantConfig.objects.filter(org=request.org).first()
    if cfg is None:
        available, reason = llm.is_available(request.org)
        return JsonResponse({
            "ok": False, "message": "Save the settings first.", "latency_ms": 0,
            "assistant_available": available, "assistant_reason": reason,
        })
    result = llm.check_connection(llm.LLMConfig.from_config(cfg))
    checks = result.get("checks", {})
    # The network operation ran without a database lock. Its result belongs only
    # to the exact saved connection that was probed.
    stored = OrgAssistantConfig.objects.filter(pk=cfg.pk, config_revision=cfg.config_revision).update(
        connection_check={"revision": cfg.config_revision, "checks": checks},
    )
    result["config_revision"] = cfg.config_revision
    result["stale"] = not bool(stored)
    result["assistant_available"], result["assistant_reason"] = llm.is_available(request.org)
    return JsonResponse(result)


@require_org_role(roles.ORG_ADMIN)
@require_POST
def assistant_models(request, org_slug):  # noqa: ARG001
    """Optional catalog discovery through the saved organization credentials."""
    from apps.assistant import llm
    from .models import OrgAssistantConfig

    cfg = OrgAssistantConfig.objects.filter(org=request.org).first()
    if cfg is None:
        return JsonResponse({"ok": False, "message": "Save the settings first.", "models": []}, status=400)
    try:
        models = llm.list_models(llm.LLMConfig.from_config(cfg))
    except Exception:
        # SDK exceptions may contain credentials or signed request URLs.
        return JsonResponse({"ok": False, "message": "Model discovery is unavailable. Enter the model ID or deployment name manually.",
                             "models": [], "config_revision": cfg.config_revision})
    stale = not OrgAssistantConfig.objects.filter(pk=cfg.pk, config_revision=cfg.config_revision).exists()
    return JsonResponse({"ok": True, "models": models, "stale": stale, "config_revision": cfg.config_revision,
                         "message": "Azure catalogs contain model IDs; enter your deployment name manually." if cfg.provider == "azure" else "Available models loaded. Select or enter a model ID."})


# ── Invitations ─────────────────────────────────────────────────────────────

@require_org_role(roles.ORG_ADMIN)
def invites(request, org_slug):  # noqa: ARG001
    form = InviteForm(request.POST or None, org=request.org)
    new_invite = None
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        already = OrgMembership.objects.filter(
            org=request.org, user__email__iexact=email
        ).exists()
        if already:
            messages.error(request, f"{email} is already a member.")
        else:
            new_invite = Invitation.objects.create(
                org=request.org,
                email=email,
                org_role=form.cleaned_data["org_role"],
                studio_grants=form.studio_grants(),
                invited_by=request.user,
            )
            new_invite.groups.set(form.cleaned_data["groups"])
            audit(request, "member.invite", target=new_invite, email=email)
            _try_send_invite_mail(new_invite)
            form = InviteForm(org=request.org)
    pending_qs = request.org.invitations.filter(accepted_at__isnull=True).order_by("-created_at")
    q = (request.GET.get("q") or "").strip()
    if q:
        pending_qs = pending_qs.filter(email__icontains=q)
    total = pending_qs.count()
    page = paginate(request, pending_qs)
    return render(
        request,
        "orgs/invites.html",
        {
            "org": request.org,
            "form": form,
            "pending": page.object_list,
            "new_invite": new_invite,
            "page": page,
            "q": q,
            "total": total,
        },
    )


@require_org_role(roles.ORG_ADMIN)
@require_POST
def invite_revoke(request, org_slug, invite_id):  # noqa: ARG001
    invite = get_object_or_404(Invitation, org=request.org, pk=invite_id, accepted_at__isnull=True)
    audit(request, "member.invite_revoke", target=invite, email=invite.email)
    invite.delete()
    return redirect(f"/orgs/{request.org.slug}/settings/invites")


def _try_send_invite_mail(invite: Invitation) -> None:
    from apps.core.instance import mail_is_configured

    if not mail_is_configured():
        return
    from django.core.mail import send_mail

    try:
        send_mail(
            subject=f"You're invited to {invite.org.name} on trellum",
            message=(
                f"You have been invited to join {invite.org.name}.\n\n"
                f"Accept here: {invite.accept_url()}\n\n"
                f"This link expires {invite.expires_at:%Y-%m-%d}."
            ),
            from_email=None,
            recipient_list=[invite.email],
            fail_silently=True,
        )
    except Exception:  # pragma: no cover - best effort only
        pass
