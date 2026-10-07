"""Template context for the global shell: which orgs/studios the user can
switch to, and what's active. Cheap (two indexed queries) and cached on the
request so the shell include never re-queries."""
from __future__ import annotations

# Shared mobile header titles.  Keeping these against named routes makes the
# header deterministic without parsing a page's <title> or hiding an unknown
# page's own heading.
CONSOLE_PAGE_TITLES = {
    "home": "Studios",
    "org-home": "Studios",
    "org-new": "Create an organization",
    "org-settings": "Studios",
    "org-studios": "Manage studios",
    "org-appearance-settings": "Console theme",
    "org-members": "Members",
    "org-invites": "Invitations",
    "org-api-keys": "API keys",
    "org-datasources": "Data sources",
    "org-assistant": "AI Assistant",
    "org-sharing": "Report sharing",
    "org-live-queries": "Live queries",
    "org-groups": "Groups",
    "org-group-detail": "Group",
    "org-group-new": "New group",
    "report-access": "Report access",
    "org-sso": "Single sign-on",
    "org-security": "Security",
    "org-audit": "Audit log",
    "studio-dashboard": "Reports",
    "studio-operations": "Operations",
    "studio-analytics": "Report Analytics",
    "studio-metrics": "Metrics",
    "studio-experiments": "Experiments",
    "studio-annotations": "Annotations",
    "studio-alerts": "Alerts",
    "studio-alert": "Alert rule",
    "studio-alert-new": "New alert",
    "studio-alert-edit": "Edit alert",
    "studio-settings": "Repository",
    "studio-repo": "Repository",
    "studio-datasources": "Data sources",
    "studio-members": "Members",
    "studio-appearance-settings": "Report theme",
    "account": "Your account",
    "mfa-setup": "Set up MFA",
    "mfa-recovery-codes": "Recovery codes",
    "my-deliveries": "My deliveries",
    "my-api-keys": "API keys",
    "system": "System health",
    "operator-fleet": "Fleet",
    "operator-orgs": "Organizations",
    "operator-org-detail": "Organization",
}

_ACTIVE_STUDIOS_SESSION_KEY = "active_studio_by_org"


def console_page_title(request) -> str:
    match = getattr(request, "resolver_match", None)
    return CONSOLE_PAGE_TITLES.get(getattr(match, "url_name", None), "")


def shell(request):
    from apps.core.version import source_url

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"trellum_source_url": source_url()}
    cached = getattr(request, "_shell_ctx", None)
    if cached is not None:
        return cached

    from apps.core import impersonation

    impersonating = impersonation.is_impersonating(request)
    is_operator = bool(getattr(user, "is_operator", False)) and not impersonating
    impersonation_ctx = {
        "is_impersonating": impersonating,
        "impersonation_operator": impersonation.operator_of(request) if impersonating else None,
        "impersonation_expires_at": impersonation.expires_at(request) if impersonating else None,
        # An impersonated session belongs to the target user, so the console
        # link must disappear while it lasts.
        "is_operator": is_operator,
        "shell_health": None,
    }
    if is_operator:
        from apps.core.health import quick_health_summary

        impersonation_ctx["shell_health"] = quick_health_summary()

    from apps.core.permissions import visible_studios
    from apps.orgs.models import Organization

    if user.is_superuser:
        orgs = list(Organization.objects.filter(is_active=True).order_by("slug"))
    else:
        orgs = list(
            Organization.objects.filter(
                is_active=True, memberships__user=user
            ).order_by("slug")
        )

    session = request.session if hasattr(request, "session") else None
    raw_remembered_studios = (
        session.get(_ACTIVE_STUDIOS_SESSION_KEY, {}) if session is not None else {}
    )
    remembered_studios = (
        raw_remembered_studios if isinstance(raw_remembered_studios, dict) else {}
    )
    visible_org_slugs = {org.slug for org in orgs}
    valid_remembered_studios = {
        slug: studio_id
        for slug, studio_id in remembered_studios.items()
        if slug in visible_org_slugs
    }
    if session is not None and valid_remembered_studios != raw_remembered_studios:
        session[_ACTIVE_STUDIOS_SESSION_KEY] = valid_remembered_studios
    remembered_studios = valid_remembered_studios

    from apps.core.permissions import effective_roles

    active_org = getattr(request, "org", None)
    if active_org is not None and hasattr(request, "session"):
        # Remember the last org the user actually worked in.
        if request.session.get("active_org") != active_org.slug:
            request.session["active_org"] = active_org.slug
    if active_org is None:
        # Session-remembered choice, else the user's only/first org — the
        # shell must work on pages without a decorator-resolved org (home!).
        session_slug = request.session.get("active_org") if hasattr(request, "session") else None
        if session_slug:
            active_org = next((o for o in orgs if o.slug == session_slug), None)
        if active_org is None and orgs:
            active_org = orgs[0]

    studios = list(visible_studios(user, active_org)) if active_org else []

    # Admin flags computed HERE (not from request.org_roles, which only
    # exists behind the permission decorators).
    er = effective_roles(user, active_org) if active_org else None
    # Keep request scope separate from the last studio used for navigation:
    # org pages must never acquire a request.studio through shell state.
    active_studio = getattr(request, "studio", None)
    nav_studio = None
    if active_org:
        visible_by_id = {studio.pk: studio for studio in studios}

        if active_studio is not None:
            nav_studio = visible_by_id.get(active_studio.pk)
            if session is not None and nav_studio is not None:
                if remembered_studios.get(active_org.slug) != nav_studio.pk:
                    remembered_studios = {
                        **remembered_studios,
                        active_org.slug: nav_studio.pk,
                    }
                    session[_ACTIVE_STUDIOS_SESSION_KEY] = remembered_studios
        elif active_org.slug in remembered_studios:
            nav_studio = visible_by_id.get(remembered_studios[active_org.slug])
            if nav_studio is None and session is not None:
                remembered_studios = {**remembered_studios}
                remembered_studios.pop(active_org.slug)
                session[_ACTIVE_STUDIOS_SESSION_KEY] = remembered_studios

    from apps.core.themes import explicit_studio_theme, viewer_theme_override
    from trellum.themes import THEME_REGISTRY

    # Theme is a per-studio property now, not a per-viewer global (see
    # apps.core.themes.resolve_studio_theme) — `studio_theme` is non-empty
    # only when this request is inside a studio (request.studio set by the
    # permission decorators) AND something in the chain (viewer override,
    # studio, org) explicitly names a theme; empty means "nothing picked
    # anywhere", which renders as plain Trellum, governed by the separate
    # light/dark/auto preference (explicit_studio_theme's docstring). This
    # is deliberately the rungs-1-3-only helper, not resolve_studio_theme
    # (which apps.reports.views._inject_report_chrome calls for report
    # content, and which always returns a concrete key) — see that
    # function's docstring for why chrome and report content need different
    # answers to "nothing was picked". Org/product pages never see
    # active_studio set, so they cost zero conditional logic here beyond
    # this one attribute and always render the Trellum brand look.
    studio_theme = explicit_studio_theme(user, active_studio) if active_studio else ""
    # What THIS viewer personally chose, for the picker's `selected` state --
    # not the resolved `studio_theme` above, which may be a studio/org
    # default nobody at this row actually picked (see viewer_theme_override's
    # docstring).
    viewer_studio_theme = viewer_theme_override(user, active_studio) if active_studio else ""
    # Whether THIS viewer may change their per-studio override — the studio
    # header's Appearance picker hides/disables itself when the org has
    # locked overrides off (organization.lock_studio_theme). Meaningless
    # (and left False) outside a studio.
    studio_theme_locked = bool(active_studio and active_org and active_org.lock_studio_theme)
    # Pre-paint default for the management-surface mode boot
    # (_theme_boot_mgmt.html): the organization's own Light/Dark/Auto
    # setting (Organization.default_mode, Org settings -> Appearance), not
    # a mode derived from a palette — the org no longer picks one (see
    # .lavish/theming-explained.html "The fix", decision (1)). "dark"
    # outside any org context (pages this context processor skips, e.g.
    # before login resolves one), matching the field's own default.
    org_default_mode = active_org.default_mode if active_org else "dark"

    ctx = {
        "shell_orgs": orgs,
        "shell_studios": studios,
        "shell_org": active_org,
        "shell_studio": active_studio,
        "shell_nav_studio": nav_studio,
        "shell_is_org_admin": bool(er and er.is_org_admin),
        "shell_studio_role": er.role_for(active_studio) if er and active_studio else None,
        "shell_nav_studio_role": er.role_for(nav_studio) if er and nav_studio else None,
        "shell_nav_full_studio_visibility": bool(
            er and nav_studio and er.has_full_studio_visibility(nav_studio)
        ),
        "console_page_title": console_page_title(request),
        "theme_registry": list(THEME_REGISTRY),
        "studio_theme": studio_theme,
        "viewer_studio_theme": viewer_studio_theme,
        "studio_theme_locked": studio_theme_locked,
        "org_default_mode": org_default_mode,
        "trellum_source_url": source_url(),
        **impersonation_ctx,
    }
    request._shell_ctx = ctx
    return ctx
