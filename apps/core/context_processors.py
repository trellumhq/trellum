"""Template context for the global shell: which orgs/studios the user can
switch to, and what's active. Role and theme resolution share the request
cache with view guards, so shell rendering does not repeat their work."""
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
    "org-delivery-settings": "Delivery settings",
    "org-members": "Members",
    "org-invites": "Invitations",
    "org-api-keys": "API keys",
    "org-datasources": "Data sources",
    "org-datasource-edit": "Edit data source",
    "org-assistant": "AI settings",
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
    "studio-analyses": "Analyses",
    "studio-deliveries": "Deliveries",
    "studio-delivery-new": "Add delivery",
    "studio-delivery-edit": "Edit delivery",
    "manage-delivery-edit": "Edit delivery",
    "studio-operations": "Operations",
    "studio-analytics": "Report Analytics",
    "studio-metrics": "Metrics",
    "studio-metrics-comparison": "Metrics",
    "metric-comparison": "Metrics",
    "studio-experiments": "Experiments",
    "studio-annotations": "Annotations",
    "studio-annotations-browse": "Annotations",
    "studio-alerts": "Alerts",
    "studio-alert": "Alert rule",
    "studio-alert-new": "New alert",
    "studio-alert-edit": "Edit alert",
    "studio-settings": "Repository",
    "studio-repo": "Repository",
    "studio-datasources": "Data sources",
    "studio-datasource-edit": "Edit data source",
    "studio-members": "Members",
    "studio-appearance-settings": "Report theme",
    "account": "Your account",
    "mfa-setup": "Set up MFA",
    "mfa-recovery-codes": "Recovery codes",
    "my-deliveries": "Deliveries",
    "my-api-keys": "API keys",
    "system": "System health",
    "instance-settings": "Instance settings",
    "operator-fleet": "Fleet",
    "operator-orgs": "Organizations",
    # operator-org-detail is deliberately absent: its own h1 is the organization's
    # name, and a generic "Organization" shell title would hide that h1 on narrow
    # screens, leaving the operator without the page identity.
    "operator-logs": "Server logs",
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
    from apps.core.request_context import actor_key

    requested_org = getattr(request, "org", None)
    requested_studio = getattr(request, "studio", None)
    identity = (
        actor_key(request),
        getattr(requested_org, "pk", None),
        getattr(requested_org, "is_active", None),
        getattr(requested_studio, "pk", None),
        getattr(requested_studio, "org_id", None),
    )
    cached = getattr(request, "_shell_ctx", None)
    if cached is not None and getattr(request, "_shell_identity", None) == identity:
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

    from apps.core.shell_scope import (
        can_manage_any_org, initial_choices, organization_choices, selected_choice,
        studio_choices, studio_facts, with_selected,
    )

    org_rows = organization_choices(request)
    orgs, orgs_more = initial_choices(org_rows)
    session = request.session if hasattr(request, "session") else None
    raw_remembered = session.get(_ACTIVE_STUDIOS_SESSION_KEY, {}) if session is not None else {}
    remembered_studios = raw_remembered if isinstance(raw_remembered, dict) else {}
    if remembered_studios:
        # Query only session keys: never enumerate every accessible organization.
        page_slugs = {org.slug for org in orgs}
        missing = set(remembered_studios) - page_slugs
        valid_slugs = page_slugs | set(org_rows.filter(slug__in=missing).values_list("slug", flat=True)) if missing else page_slugs
        remembered_studios = {slug: pk for slug, pk in remembered_studios.items() if slug in valid_slugs}
    if session is not None and remembered_studios != raw_remembered:
        session[_ACTIVE_STUDIOS_SESSION_KEY] = remembered_studios

    active_org = selected_choice(org_rows, orgs, pk=getattr(requested_org, "pk", None))
    if active_org is not None and session is not None:
        if session.get("active_org") != active_org.slug:
            session["active_org"] = active_org.slug
    if active_org is None:
        remembered_org = session.get("active_org") if session is not None else None
        active_org = selected_choice(org_rows, orgs, slug=remembered_org) if isinstance(remembered_org, str) else None
        active_org = active_org or (orgs[0] if orgs else None)
    orgs = with_selected(orgs, active_org)

    studio_rows = studio_choices(request, active_org) if active_org else None
    studios, studios_more = initial_choices(studio_rows) if active_org else ([], False)
    active_studio = None
    if requested_studio is not None and active_org and requested_studio.org_id == active_org.pk:
        active_studio = selected_choice(studio_rows, studios, pk=requested_studio.pk)
    nav_studio = active_studio
    if active_org:
        remembered_id = remembered_studios.get(active_org.slug)
        if active_studio is not None:
            if session is not None and remembered_id != active_studio.pk:
                remembered_studios = {**remembered_studios, active_org.slug: active_studio.pk}
                session[_ACTIVE_STUDIOS_SESSION_KEY] = remembered_studios
        elif remembered_id is not None:
            # Session values are untrusted; malformed values cannot reach ORM coercion.
            if isinstance(remembered_id, int) and not isinstance(remembered_id, bool) and 0 < remembered_id <= 9223372036854775807:
                nav_studio = selected_choice(studio_rows, studios, pk=remembered_id)
            if nav_studio is None and session is not None:
                remembered_studios = {**remembered_studios}
                remembered_studios.pop(active_org.slug, None)
                session[_ACTIVE_STUDIOS_SESSION_KEY] = remembered_studios
    studios = with_selected(studios, nav_studio)
    for studio in studios:
        studio.org = active_org
    active_role, _ = studio_facts(request, active_studio)
    nav_role, nav_full = studio_facts(request, nav_studio)
    can_manage_deliveries = can_manage_any_org(request, orgs, has_more=orgs_more)

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
    studio_theme = explicit_studio_theme(user, active_studio, request=request) if active_studio else ""
    # What THIS viewer personally chose, for the picker's `selected` state --
    # not the resolved `studio_theme` above, which may be a studio/org
    # default nobody at this row actually picked (see viewer_theme_override's
    # docstring).
    viewer_studio_theme = viewer_theme_override(user, active_studio, request=request) if active_studio else ""
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
        "shell_orgs_more": orgs_more,
        "shell_studios": studios,
        "shell_studios_more": studios_more,
        "shell_org": active_org,
        "shell_studio": active_studio,
        "shell_nav_studio": nav_studio,
        "shell_is_org_admin": bool(active_org and active_org.shell_admin),
        "shell_can_manage_deliveries": bool(can_manage_deliveries),
        "shell_studio_role": active_role,
        "shell_nav_studio_role": nav_role,
        "shell_nav_full_studio_visibility": nav_full,
        "console_page_title": console_page_title(request),
        "theme_registry": list(THEME_REGISTRY),
        "studio_theme": studio_theme,
        "viewer_studio_theme": viewer_studio_theme,
        "studio_theme_locked": studio_theme_locked,
        "org_default_mode": org_default_mode,
        "trellum_source_url": source_url(),
        **impersonation_ctx,
    }
    request._shell_identity = identity
    request._shell_ctx = ctx
    return ctx
