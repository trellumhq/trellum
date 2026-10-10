"""Root URL configuration."""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts import views as account_views
from apps.assistant import mcp as assistant_mcp
from apps.assistant import views as assistant_views
from apps.core import views as core_views
from apps.datasources import views as datasource_views
from apps.orgs import views as org_views
from apps.orgs.audit import views as org_audit
from apps.orgs.groups import views as org_groups
from apps.orgs.security import views as org_security
from apps.orgs.sso import views as org_sso
from apps.reports import livequery as report_livequery
from apps.reports import metrics_catalog
from apps.reports import views as report_views
from apps.reports.overviews import annotations, experiments
from apps.reports.vendor import vendor_asset
from apps.studios import views as studio_views
# Everything scoped to one studio: mounted at /s/<org_slug>/<studio_slug>/.
# The JSON endpoints mirror the legacy handlers.py response shapes so the
# adapted portal.js keeps working.
studio_patterns = [
    path("", report_views.dashboard, name="studio-dashboard"),
    path("analyses", report_views.analyses, name="studio-analyses"),
    # The Operations tab (studio tab bar): the dashboard template booted with
    # the ops surface active. Old ?view=ops/?view=health deep links redirect
    # here from the dashboard view.
    path("operations", report_views.operations, name="studio-operations"),
    path("analytics", report_views.analytics, name="studio-analytics"),
    path("metrics", metrics_catalog.metrics_page, name="studio-metrics"),
    path("experiments", experiments.experiments_page, name="studio-experiments"),
    path("api/registry", report_views.api_registry, name="api-registry"),
    path("api/system/status", report_views.api_system_status),
    path("api/system/run-stats", report_views.api_run_stats),
    path("api/system/log", report_views.api_system_log),
    path("api/system/run-all", report_views.api_run_all),
    path("api/system/stop-all", report_views.api_stop_all),
    path("api/system/cache/clear", report_views.api_cache_clear),
    path("api/system/registry/refresh", report_views.api_registry_refresh),
    path("api/system/git/status", report_views.api_git_status),
    path("api/system/git/sync", report_views.api_git_sync),
    path("api/system/git/check", report_views.api_git_check),
    path("api/system/git/publish", report_views.api_git_publish),
    path("api/system/git/history", report_views.api_git_history),
    path("api/git/webhook", report_views.api_git_webhook),
    path("api/members", report_views.api_studio_members),
    path("api/datasources", datasource_views.api_list),
    path("api/datasources/<str:name>/upload", datasource_views.api_upload),
    path("api/datasources/<str:name>/test", datasource_views.api_test),
    path("api/datasources/<str:name>/download", datasource_views.api_download),
    path("api/reports/<str:slug>/status", report_views.api_report_status),
    path("api/report-shell", report_views.report_shell, name="report-shell"),
    path("api/reports/<str:slug>/log/live", report_views.api_report_log_live),
    path("api/reports/<str:slug>/log", report_views.api_report_log),
    path("api/reports/<str:slug>/validation", report_views.api_report_validation),
    path("api/reports/<str:slug>/run", report_views.api_report_run),
    path("api/reports/<str:slug>/stop", report_views.api_report_stop),
    # Scheduled email delivery (personal, VIEWER-gated — see the views for
    # why this differs from the DEVELOPER-gated run controls above). Failure
    # /recovery alerts have no per-user API: every studio developer/admin
    # gets them automatically (apps.reports.notify.alert_recipients).
    path("api/my-subscriptions", report_views.api_my_subscriptions),
    path("api/reports/<str:slug>/schedules", report_views.api_email_schedules),
    path(
        "api/reports/<str:slug>/schedules/<int:schedule_id>",
        report_views.api_email_schedule_detail,
    ),
    path(
        "api/reports/<str:slug>/schedules/<int:schedule_id>/sample",
        report_views.api_email_schedule_sample,
    ),
    path("api/reports/<str:slug>/sample", report_views.api_report_sample),
    # Public share links (internal planning#6): studio developers/admins manage
    # them here; the public-facing /share/<token>/... routes are unauthed
    # and live at the top level below, next to notify/unsubscribe.
    path("api/reports/<str:slug>/share_links", report_views.api_share_links),
    # View analytics (internal planning#4): report page's "Activity" panel.
    path("api/reports/<str:slug>/views", report_views.api_report_views),
    # "Metrics in this report" panel (semantic-layer Phase 2): the build's
    # own claimed metrics vs. their current studio definitions.
    path("api/reports/<str:slug>/metrics", report_views.api_report_metrics, name="api-report-metrics"),
    path("api/reports/<str:slug>/recipients", report_views.api_report_recipients, name="api-report-recipients"),
    # Live queries (M1): built report pages re-run a query the build
    # declared in _live_queries.json. Session-authenticated viewers only —
    # the public /share/<token>/ routes deliberately get no equivalent.
    path("api/reports/<str:slug>/live-query", report_livequery.api_live_query),
    # AI assistant (studio-scoped: the assistant reads this studio's output)
    path("api/assistant/available", assistant_views.studio_available, name="studio-assistant-available"),
    path("api/assistant/budget", assistant_views.budget, name="studio-assistant-budget"),
    path("api/assistant/sessions", assistant_views.sessions, name="studio-assistant-sessions"),
    path(
        "api/assistant/sessions/<int:session_id>",
        assistant_views.session_detail,
        name="studio-assistant-session",
    ),
    path(
        "api/assistant/sessions/<int:session_id>/message",
        assistant_views.session_message,
        name="studio-assistant-message",
    ),
    path(
        "api/assistant/proposals/<int:proposal_id>/approve",
        assistant_views.proposal_approve,
        name="studio-assistant-proposal-approve",
    ),
    path(
        "api/assistant/proposals/<int:proposal_id>/reject",
        assistant_views.proposal_reject,
        name="studio-assistant-proposal-reject",
    ),
    # The agent door (internal planning ticket #062): the same toolbox over MCP, API-key only.
    path("mcp", assistant_mcp.mcp, name="studio-mcp"),
    # The alert email's "open in the assistant" link (a page, not JSON).
    path(
        "assistant/from-alert/<int:run_id>",
        assistant_views.from_alert,
        name="studio-assistant-from-alert",
    ),
    path("annotations", annotations.calendar_page, name="studio-annotations"),
    # Agentic alerts (internal planning ticket #147): rules, run log, Test now.
    path("alerts", include("apps.alerts.urls")),
    # Per-studio theming (studio owns its theme; a viewer keeps a personal
    # per-studio override) -- see apps.core.themes.resolve_studio_theme.
    # theme: any member's own override (the studio header's Appearance
    # picker, and the report page's own in-report picker, both post here).
    # theme/default: studio-admin-only, the studio's own default (the tab
    # rail's swatch chip).
    path("theme", studio_views.theme_set, name="studio-theme-set"),
    path("theme/default", studio_views.theme_set_default, name="studio-theme-set-default"),
    path("r/<str:slug>/", report_views.report_page, name="report-page"),
    path("r/<str:slug>/access", org_groups.report_access, name="report-access"),
    path("r/<str:slug>/<path:asset>", report_views.report_asset, name="report-asset"),
]

urlpatterns = [
    # Instance-level (paths are contracts: healthchecks + built report pages)
    path("healthz", core_views.healthz, name="healthz"),
    path("api/version", core_views.version, name="api-version"),
    # Read by trellum/static/js/data_loader.js's filter-health badge check on
    # every already-built report page carrying a live FilterBar -- same
    # fixed/unprefixed contract as healthz/version above.
    path("api/auth/me", core_views.auth_me, name="api-auth-me"),
    path("_vendor/<path:path>", vendor_asset, name="vendor-asset"),
    path("api/assistant/widget.js", assistant_views.widget_js, name="assistant-widget"),
    path("api/assistant/available", assistant_views.available, name="assistant-available"),
    # Report-page "Options" menu host -- listed before the three widget
    # routes below since apps.runner.executor.portal_extensions() relies on
    # this loading first (see that function's own comment).
    path(
        "api/reports/menu-widget.js",
        report_views.menu_widget_js,
        name="reports-menu-widget",
    ),
    # Email & alerts widget, injected into every built report page — the
    # frozen-URL sibling of the assistant widget above (see the view's docstring).
    path(
        "api/reports/delivery-widget.js",
        report_views.delivery_widget_js,
        name="reports-delivery-widget",
    ),
    # Theming redesign: wires the report page's own theme picker to POST its
    # choice to the per-studio setter, same frozen-URL delivery as the
    # widget above (see report_views.theme_widget_js's docstring).
    path(
        "api/reports/theme-widget.js",
        report_views.theme_widget_js,
        name="reports-theme-widget",
    ),
    path(
        "api/reports/share-widget.js",
        report_views.share_widget_js,
        name="reports-share-widget",
    ),
    path(
        "api/reports/embed-widget.js",
        report_views.embed_widget_js,
        name="reports-embed-widget",
    ),
    # The <trellum-report> element, loaded by the *host* page rather than
    # injected into the frame — a customer types this URL into their own
    # markup from the docs, so it is frozen like the widgets above.
    path(
        "api/reports/embed-element.js",
        report_views.embed_element_js,
        name="reports-embed-element",
    ),
    path(
        "api/reports/views-widget.js",
        report_views.views_widget_js,
        name="reports-views-widget",
    ),
    # "Metrics in this report" panel (semantic-layer Phase 2), same
    # frozen-URL delivery as the three widgets above.
    path(
        "api/reports/metrics-widget.js",
        report_views.metrics_widget_js,
        name="reports-metrics-widget",
    ),
    path("api/share_links/<int:link_id>/revoke", report_views.api_share_link_revoke, name="api-share-link-revoke"),
    path("admin/", admin.site.urls),
    # Per-user favorites (cross-studio)
    path("api/me/favorites", report_views.api_favorites, name="api-favorites"),
    path("api/me/favorites/import", report_views.api_favorites_import),
    path("api/me/favorites/<int:report_id>", report_views.api_favorite_toggle),
    # Per-user deliveries & alerts, across every studio (cross-studio, like favorites)
    path("me/deliveries", report_views.my_deliveries, name="my-deliveries"),
    # Personal API keys (internal planning ticket #002): cross-org like deliveries; the org-admin
    # list sits with the other org settings tabs further down.
    path("me/api-keys", account_views.api_keys_view, name="my-api-keys"),
    # No-login unsubscribe link mailed at the bottom of every alert/delivery
    # mail — the signed token carries everything the view needs.
    path("notify/unsubscribe/<str:token>", report_views.notify_unsubscribe, name="notify-unsubscribe"),
    # Public share links (internal planning#6): the token is the credential, no
    # login required. Same entry/asset split as the authenticated
    # r/<slug>/ + r/<slug>/<asset> pair above (studio_patterns, near the
    # bottom of this file).
    path("share/<str:token>/", report_views.share_entry, name="share-entry"),
    path("share/<str:token>/<path:asset>", report_views.share_asset, name="share-asset"),
    # Studio-scoped dashboard + API + report content
    path("s/<slug:org_slug>/<slug:studio_slug>/", include(studio_patterns)),
    # Auth / account lifecycle
    path("login", account_views.login_view, name="login"),
    path("login/verify", account_views.mfa_verify_view, name="mfa-verify"),
    path("logout", account_views.logout_view, name="logout"),
    path("account", account_views.account_view, name="account"),
    path("account/delete", account_views.account_delete_view, name="account-delete"),
    path("account/security/mfa/setup", account_views.mfa_setup_view, name="mfa-setup"),
    path(
        "account/security/mfa/recovery-codes",
        account_views.mfa_recovery_codes_view,
        name="mfa-recovery-codes",
    ),
    path("account/security/mfa/disable", account_views.mfa_disable_view, name="mfa-disable"),
    path(
        "account/security/mfa/regenerate",
        account_views.mfa_regenerate_view,
        name="mfa-regenerate",
    ),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(success_url="/password-reset/sent/"),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(success_url="/password-reset/complete/"),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # First-run wizard. Steps 1-4 are open only while the instance has no
    # users; 5-6 run as the operator it just created.
    path("setup", account_views.setup_view, name="setup"),
    path("setup/checks", account_views.setup_checks_view, name="setup-checks"),
    path("setup/organization", account_views.setup_organization_view, name="setup-organization"),
    path("setup/account", account_views.setup_account_view, name="setup-account"),
    path("setup/settings", account_views.setup_settings_view, name="setup-settings"),
    path("setup/done", account_views.setup_done_view, name="setup-done"),
    path("invite/<str:token>", account_views.invite_accept_view, name="invite-accept"),
    path("api/me", account_views.me_api, name="api-me"),
    # SOCIALACCOUNT_ONLY (see settings/base.py) already refuses every non-GET on
    # allauth's login view and drops its signup/email/password URLs. This last
    # redirect stops the GET rendering a second, unbranded login page: allauth
    # itself sends users to `account_login` (e.g. socialaccount signup with no
    # pending login), and there is only one login page in this product. Must
    # stay ABOVE the include — URL resolution takes the first match.
    path("accounts/login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    path("accounts/", include("allauth.urls")),
    # Org switcher / management
    path("", org_views.home, name="home"),
    path("orgs/new", org_views.org_new, name="org-new"),
    path("orgs/create", org_views.org_create, name="org-create"),
    path("orgs/<slug:org_slug>/", org_views.org_home, name="org-home"),
    path("orgs/<slug:org_slug>/delete", org_views.org_delete, name="org-delete"),
    path("orgs/<slug:org_slug>/studios/new", org_views.studio_create, name="studio-create"),
    path(
        "orgs/<slug:org_slug>/studios/<slug:studio_slug>/delete",
        org_views.studio_delete,
        name="studio-delete",
    ),
    # Org-level share-links opt-in (internal planning#6 follow-up): owned by
    # apps.reports, not apps.orgs -- it only ever governs report sharing,
    # but the URL sits with its /orgs/<org_slug>/... siblings.
    path("orgs/<slug:org_slug>/api/share_policy", report_views.api_share_policy, name="api-org-share-policy"),
    # Org-level live-query rate limit (live-query filter redesign): same
    # ownership rationale as share_policy above.
    path(
        "orgs/<slug:org_slug>/api/live_query_policy",
        report_views.api_live_query_policy,
        name="api-org-live-query-policy",
    ),
    # Org settings -> Appearance: the org's default mode + the per-studio-
    # override lock -- see apps.orgs.views.appearance_settings and
    # apps.core.themes.resolve_studio_theme rungs 1/3.
    path("orgs/<slug:org_slug>/settings/appearance", org_views.appearance_settings, name="org-appearance-settings"),
    # Org settings -> Data retention: the two per-org data windows (vault
    # #110) -- see apps.orgs.views.retention_settings.
    path("orgs/<slug:org_slug>/settings/retention", org_views.retention_settings, name="org-retention-settings"),
    # Settings hubs: bare /settings/ lands on the section's first tab.
    path("orgs/<slug:org_slug>/settings/", org_views.settings_home, name="org-settings"),
    path("orgs/<slug:org_slug>/settings/studios", org_views.studios_settings, name="org-studios"),
    path("orgs/<slug:org_slug>/settings/audit", org_audit.audit_log, name="org-audit"),
    path(
        "orgs/<slug:org_slug>/settings/audit/export.csv",
        org_audit.audit_export_csv,
        name="org-audit-export",
    ),
    path(
        "orgs/<slug:org_slug>/settings/datasources",
        datasource_views.org_settings_page,
        name="org-datasources",
    ),
    path(
        "orgs/<slug:org_slug>/settings/datasources/<str:name>/test",
        datasource_views.api_org_test,
        name="org-datasource-test",
    ),
    path(
        "orgs/<slug:org_slug>/settings/datasources/<str:name>/upload",
        datasource_views.api_org_upload,
        name="org-datasource-upload",
    ),
    path(
        "orgs/<slug:org_slug>/settings/datasources/<str:name>/download",
        datasource_views.api_org_download,
        name="org-datasource-download",
    ),
    path("system", core_views.system_page, name="system"),
    path("system/email-connections/new", core_views.email_connection_editor, name="email-connection-new"),
    path("system/email-connections/<int:connection_id>", core_views.email_connection_editor,
         name="email-connection-edit"),
    path("system/email-connections/use-smtp", core_views.email_connection_action,
         {"action": "use-smtp"}, name="email-connection-use-smtp"),
    path("system/email-connections/<int:connection_id>/<str:action>", core_views.email_connection_action,
         name="email-connection-action"),
    path("api/system/health", core_views.system_health, name="system-health"),
    # Cross-organization operator console (404s for non-operators).
    path("operator/", include("apps.operator.urls")),
    path("orgs/<slug:org_slug>/settings/members", org_views.members, name="org-members"),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/role",
        org_views.member_set_role,
        name="org-member-role",
    ),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/remove",
        org_views.member_remove,
        name="org-member-remove",
    ),
    # Subject access / portability (Art. 15/20) and erasure (Art. 17) --
    # apps.orgs.personal_data owns both, and the second is irreversible.
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/export.json",
        org_views.member_export,
        name="org-member-export",
    ),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/erase",
        org_views.member_erase,
        name="org-member-erase",
    ),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/studio-role",
        org_views.member_set_studio_role,
        name="org-member-studio-role",
    ),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/unlock",
        org_views.member_unlock,
        name="org-member-unlock",
    ),
    path(
        "orgs/<slug:org_slug>/settings/members/<int:user_id>/reset-mfa",
        org_views.member_reset_mfa,
        name="org-member-reset-mfa",
    ),
    path("orgs/<slug:org_slug>/settings/groups", org_groups.groups, name="org-groups"),
    path("orgs/<slug:org_slug>/settings/groups/new", org_groups.group_new, name="org-group-new"),
    path(
        "orgs/<slug:org_slug>/settings/groups/<int:group_id>",
        org_groups.group_detail,
        name="org-group-detail",
    ),
    path(
        "orgs/<slug:org_slug>/settings/groups/<int:group_id>/delete",
        org_groups.group_delete,
        name="org-group-delete",
    ),
    path("orgs/<slug:org_slug>/settings/sso", org_sso.sso_settings, name="org-sso"),
    path(
        "orgs/<slug:org_slug>/settings/security",
        org_security.security_settings,
        name="org-security",
    ),
    path("orgs/<slug:org_slug>/settings/assistant", org_views.assistant_settings, name="org-assistant"),
    path("orgs/<slug:org_slug>/settings/assistant/models", org_views.assistant_models, name="org-assistant-models"),
    path(
        "orgs/<slug:org_slug>/settings/assistant/test",
        org_views.assistant_test,
        name="org-assistant-test",
    ),
    # Report sharing (internal planning#6 follow-up): owned by apps.reports, same
    # as the toggle endpoint above -- named org-* like every sibling tab.
    path("orgs/<slug:org_slug>/settings/sharing", report_views.org_share_settings, name="org-sharing"),
    # Live-query rate limit (live-query filter redesign): same ownership
    # rationale as Report sharing above.
    path(
        "orgs/<slug:org_slug>/settings/live-queries",
        report_views.org_live_query_settings,
        name="org-live-queries",
    ),
    path("orgs/<slug:org_slug>/settings/invites", org_views.invites, name="org-invites"),
    path("orgs/<slug:org_slug>/settings/api-keys", account_views.org_api_keys_view, name="org-api-keys"),
    path(
        "orgs/<slug:org_slug>/onboarding/dismiss",
        org_views.onboarding_dismiss,
        name="onboarding-dismiss",
    ),
    path(
        "orgs/<slug:org_slug>/settings/invites/<int:invite_id>/revoke",
        org_views.invite_revoke,
        name="org-invite-revoke",
    ),
    # Studio-scoped settings
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/",
        studio_views.settings_home,
        name="studio-settings",
    ),
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/members",
        studio_views.members,
        name="studio-members",
    ),
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/members/set",
        studio_views.member_set,
        name="studio-member-set",
    ),
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/repo",
        studio_views.repo_settings,
        name="studio-repo",
    ),
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/datasources",
        datasource_views.settings_page,
        name="studio-datasources",
    ),
    # The studio admin's own default-palette page (apps.core.themes
    # .resolve_studio_theme rung 2) -- moved off the tab bar's admin-only
    # chip into the studio's own settings, mirroring org-appearance one
    # level down (see .lavish/theming-controls-design.html). GET renders
    # the picker; POST reuses studio-theme-set-default above.
    path(
        "s/<slug:org_slug>/<slug:studio_slug>/settings/appearance",
        studio_views.appearance_settings,
        name="studio-appearance-settings",
    ),
]

# Dev-only instant login (the view re-checks DEBUG too; double gate).
if settings.DEBUG:
    from apps.accounts import dev as accounts_dev

    urlpatterns += [path("__dev__/login/", accounts_dev.dev_login, name="dev-login")]
