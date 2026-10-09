"""The audit event taxonomy: every action string, its category and its label.

One dict, one place. ``apps.core.audit.audit()`` resolves ``category`` through
this registry at write time — the column is denormalized here, never parsed
back out of the dotted verb at query time (see the AuditLog model docstring).
The dashboard (``apps.orgs.audit``) reads the same registry for its action
filter's labels and for grouping by category, rather than re-deriving either
from a distinct-values table scan.

Adding an event: add one line to ``ACTIONS`` in the right category. That is
the whole integration — the write path, the category pills and the action
filter all pick it up with no other change.

Reserved names (``reserved=True``): action strings this design registers but
does not itself emit. internal planning#78 (session security — MFA, lockout,
session revocation) landed and activated its six reserved names
(``auth.mfa_enrolled``, ``auth.mfa_removed``, ``auth.mfa_failed``,
``auth.lockout``, ``auth.unlock``, ``auth.session_revoked``); this list is
otherwise empty today.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Categories ────────────────────────────────────────────────────────────
#: Sign-in, sign-out, and the refusals in between.
CATEGORY_AUTH = "auth"
#: Who may do what: membership, roles, permission groups.
CATEGORY_AUTHZ = "authz"
#: Observing or pulling data: views, exports, downloads, live queries.
CATEGORY_ACCESS = "access"
#: Instance and organization configuration.
CATEGORY_ADMIN = "admin"
#: Background jobs and the audit trail's own lifecycle.
CATEGORY_SYSTEM = "system"

CATEGORIES = (
    CATEGORY_AUTH,
    CATEGORY_AUTHZ,
    CATEGORY_ACCESS,
    CATEGORY_ADMIN,
    CATEGORY_SYSTEM,
)

#: Human label for a category pill / stat tile.
CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_AUTH: "Authentication",
    CATEGORY_AUTHZ: "Authorization",
    CATEGORY_ACCESS: "Data access",
    CATEGORY_ADMIN: "Administration",
    CATEGORY_SYSTEM: "System",
}

# ── Outcomes ──────────────────────────────────────────────────────────────
#: The action happened as intended.
OUTCOME_SUCCESS = "success"
#: The actor was refused — a login, an SSO assertion, a permission check.
OUTCOME_DENIED = "denied"
#: The action was attempted and did not complete — an error, a timeout.
OUTCOME_FAILURE = "failure"

OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_FAILURE)


@dataclass(frozen=True)
class ActionSpec:
    category: str
    label: str
    #: True for an "access" verb (observes / attempts entry) in the design's
    #: M/A legend, False for a mutation. Informational — nothing gates on it.
    is_access: bool = False
    #: Registered but not (yet) emitted anywhere in this codebase — see the
    #: module docstring.
    reserved: bool = False


def _m(category: str, label: str) -> ActionSpec:
    return ActionSpec(category=category, label=label)


def _a(category: str, label: str) -> ActionSpec:
    return ActionSpec(category=category, label=label, is_access=True)


def _reserved(category: str, label: str, *, is_access: bool = False) -> ActionSpec:
    return ActionSpec(category=category, label=label, is_access=is_access, reserved=True)


# ── The registry ──────────────────────────────────────────────────────────
#
# Existing-action coverage is verified, not assumed: every action string this
# tree actually calls audit()/audit_system() with is grep-checked against this
# dict in apps/core/tests/test_audit_model.py, so a call site added without a
# registry entry fails the suite rather than silently degrading to the
# fallback category in production.
ACTIONS: dict[str, ActionSpec] = {
    # ── auth ────────────────────────────────────────────────────────────
    "auth.login": _a(CATEGORY_AUTH, "Signed in"),
    "auth.logout": _a(CATEGORY_AUTH, "Signed out"),
    "auth.login_failed": _a(CATEGORY_AUTH, "Sign-in failed"),
    "auth.sso_denied": _a(CATEGORY_AUTH, "SSO sign-in denied"),
    "account.password_change": _m(CATEGORY_AUTH, "Password changed"),
    # internal planning#78 (account security): the six names reserved by the
    # auditing feature, now emitted by apps.accounts.{mfa,throttle,session_policy}.
    "auth.mfa_enrolled": _m(CATEGORY_AUTH, "MFA enrolled"),
    "auth.mfa_removed": _m(CATEGORY_AUTH, "MFA removed"),
    "auth.mfa_failed": _a(CATEGORY_AUTH, "MFA challenge failed"),
    "auth.lockout": _m(CATEGORY_AUTH, "Account locked out"),
    "auth.unlock": _m(CATEGORY_AUTH, "Account unlocked"),
    "auth.session_revoked": _m(CATEGORY_AUTH, "Session revoked"),
    # Not reserved by #98 -- added here per that module's own convention (one
    # line, right category) because the design calls for events distinct from
    # the six above: an admin resetting someone else's MFA is a different
    # event from the user disabling their own, and logging in with a recovery
    # code is worth telling apart from a normal TOTP-factored login.
    "auth.mfa_reset": _m(CATEGORY_AUTH, "MFA reset by admin"),
    "auth.mfa_recovery_used": _m(CATEGORY_AUTH, "Recovery code used"),
    # Personal API keys (internal planning ticket #002): the key row is the target. Revoke is
    # the same event whether the owner or an org admin did it -- the actor
    # column already says which.
    "apikey.create": _m(CATEGORY_AUTH, "API key created"),
    "apikey.revoke": _m(CATEGORY_AUTH, "API key revoked"),

    # ── authz ───────────────────────────────────────────────────────────
    "member.invite": _m(CATEGORY_AUTHZ, "Member invited"),
    "member.invite_revoke": _m(CATEGORY_AUTHZ, "Invitation revoked"),
    "member.join": _m(CATEGORY_AUTHZ, "Member joined"),
    "member.set_role": _m(CATEGORY_AUTHZ, "Org role changed"),
    "member.remove": _m(CATEGORY_AUTHZ, "Member removed"),
    "member.revoke_studio": _m(CATEGORY_AUTHZ, "Studio access revoked"),
    "member.set_studio_role": _m(CATEGORY_AUTHZ, "Studio role changed"),
    "member.provisioned": _m(CATEGORY_AUTHZ, "Member auto-provisioned via SSO"),
    "studio.member_remove": _m(CATEGORY_AUTHZ, "Studio member removed"),
    "studio.member_set": _m(CATEGORY_AUTHZ, "Studio member role set"),
    "group.create": _m(CATEGORY_AUTHZ, "Permission group created"),
    "group.update": _m(CATEGORY_AUTHZ, "Permission group updated"),
    "group.delete": _m(CATEGORY_AUTHZ, "Permission group deleted"),
    "group.grant": _m(CATEGORY_AUTHZ, "Permission group grant added"),
    "group.ungrant": _m(CATEGORY_AUTHZ, "Permission group grant removed"),
    "group.report_grant": _m(CATEGORY_AUTHZ, "Report access granted to group"),
    "group.report_ungrant": _m(CATEGORY_AUTHZ, "Report access removed from group"),
    "studio.default_audiences_set": _m(CATEGORY_AUTHZ, "Default content audiences changed"),
    "report.audience_set": _m(CATEGORY_AUTHZ, "Content audience changed"),
    "group.add_user": _m(CATEGORY_AUTHZ, "Added to permission group"),
    "group.remove_user": _m(CATEGORY_AUTHZ, "Removed from permission group"),

    # ── access ──────────────────────────────────────────────────────────
    "report.view": _a(CATEGORY_ACCESS, "Report viewed"),
    "report.export_download": _a(CATEGORY_ACCESS, "Report export downloaded"),
    "report.live_query": _a(CATEGORY_ACCESS, "Live query run"),
    "share_link.create": _m(CATEGORY_ACCESS, "Share link created"),
    "share_link.revoke": _m(CATEGORY_ACCESS, "Share link revoked"),
    "assistant.session.delete": _m(CATEGORY_ACCESS, "AI assistant conversation deleted"),
    "share_link.export_download": _a(CATEGORY_ACCESS, "Share link export downloaded"),
    "datasource.upload": _m(CATEGORY_ACCESS, "Data source file uploaded"),
    "datasource.update": _m(CATEGORY_ACCESS, "Data source updated"),
    "datasource.delete": _m(CATEGORY_ACCESS, "Data source deleted"),
    "datasource.download": _a(CATEGORY_ACCESS, "Data source file downloaded"),
    "datasource.test": _a(CATEGORY_ACCESS, "Data source connection tested"),
    # A subject-access export is a bulk read of one person's data, so it sits
    # with the other download verbs rather than with the admin ones.
    "person.export": _a(CATEGORY_ACCESS, "Personal data exported"),

    # ── admin ───────────────────────────────────────────────────────────
    "instance.setup": _m(CATEGORY_ADMIN, "Instance set up"),
    "instance.settings_update": _m(CATEGORY_ADMIN, "Instance settings updated"),
    "instance.email_connection_create": _m(CATEGORY_ADMIN, "Email connection created"),
    "instance.email_connection_update": _m(CATEGORY_ADMIN, "Email connection updated"),
    "instance.email_connection_delete": _m(CATEGORY_ADMIN, "Email connection deleted"),
    "instance.email_connection_select": _m(CATEGORY_ADMIN, "Email delivery route selected"),
    "instance.email_connection_test": _a(CATEGORY_ADMIN, "Email connection tested"),
    "org.create": _m(CATEGORY_ADMIN, "Organization created"),
    "org.appearance_set": _m(CATEGORY_ADMIN, "Organization appearance (default mode / lock) updated"),
    "org.retention_set": _m(CATEGORY_ADMIN, "Organization data retention windows updated"),
    "org.api_keys_set": _m(CATEGORY_ADMIN, "Organization API keys enabled or disabled"),
    # Written BEFORE the delete, deliberately: its slug metadata is what
    # apps.core.retention._deletion_times dates the orphaned tree by, and the
    # row's own org FK goes NULL the moment the org it points at is gone.
    "org.delete": _m(CATEGORY_ADMIN, "Organization deleted"),
    "studio.create": _m(CATEGORY_ADMIN, "Studio created"),
    "studio.rename": _m(CATEGORY_ADMIN, "Studio renamed"),
    "studio.delete": _m(CATEGORY_ADMIN, "Studio deleted"),
    "studio.repo_update": _m(CATEGORY_ADMIN, "Studio repository updated"),
    "studio.theme_set": _m(CATEGORY_ADMIN, "Studio default theme updated"),
    "assistant.config.update": _m(CATEGORY_ADMIN, "AI settings updated"),
    # A proposed action carries its own audit row too (datasource.update,
    # run.enqueue, ...) with the proposal id; these record the decision.
    "assistant.proposal.approve": _m(CATEGORY_ADMIN, "AI assistant action approved"),
    "assistant.proposal.reject": _m(CATEGORY_ADMIN, "AI assistant action rejected"),
    # Pre-rename rows still carry the old action string. The trail is
    # append-only and must stay readable back to its first entry, so the
    # old key keeps its label rather than degrading to a raw slug.
    "buddy.config.update": _m(CATEGORY_ADMIN, "AI settings updated"),
    "sso.update": _m(CATEGORY_ADMIN, "SSO configuration updated"),
    "security_policy.update": _m(CATEGORY_ADMIN, "Organization security policy updated"),
    "live_query_policy.update": _m(CATEGORY_ADMIN, "Live-query rate limit updated"),
    "sso.domain.claim": _m(CATEGORY_ADMIN, "SSO domain claimed"),
    "sso.domain.verify": _m(CATEGORY_ADMIN, "SSO domain verification attempted"),
    "sso.domain.remove": _m(CATEGORY_ADMIN, "SSO domain removed"),
    "sso.ldap.test": _a(CATEGORY_ADMIN, "Directory connection tested"),
    "share_policy.enable": _m(CATEGORY_ADMIN, "Share links enabled"),
    "share_policy.disable": _m(CATEGORY_ADMIN, "Share links disabled"),
    "share_policy.update": _m(CATEGORY_ADMIN, "Share policy updated"),
    "operator.org.active": _m(CATEGORY_ADMIN, "Organization active flag changed"),
    "operator.org.quota": _m(CATEGORY_ADMIN, "Organization quota changed"),
    "operator.studio.pool": _m(CATEGORY_ADMIN, "Studio sandbox pool changed"),
    "operator.impersonate.start": _m(CATEGORY_ADMIN, "Impersonation started"),
    "operator.impersonate.stop": _m(CATEGORY_ADMIN, "Impersonation ended"),
    "operator.impersonate.expired": _m(CATEGORY_ADMIN, "Impersonation expired"),
    # The tombstone. This row is the only thing left that says an erasure
    # happened, so it identifies its subject by target_id (the primary key the
    # scrubbed User row keeps) and never by address -- see
    # apps.orgs.personal_data.erase.
    "person.erase": _m(CATEGORY_ADMIN, "Person erased"),

    # ── system ──────────────────────────────────────────────────────────
    "run.enqueue": _m(CATEGORY_SYSTEM, "Report run queued"),
    "run.stop": _m(CATEGORY_SYSTEM, "Report run stopped"),
    "run.enqueue_all": _m(CATEGORY_SYSTEM, "All reports queued"),
    "run.stop_all": _m(CATEGORY_SYSTEM, "All runs stopped"),
    "cache.clear": _m(CATEGORY_SYSTEM, "Cache cleared"),
    "git.sync_now": _m(CATEGORY_SYSTEM, "Repository sync requested"),
    "git.check": _m(CATEGORY_SYSTEM, "Repository check requested"),
    "git.publish": _m(CATEGORY_SYSTEM, "Repository publish requested"),
    "notify.schedule_create": _m(CATEGORY_SYSTEM, "Delivery schedule created"),
    "notify.schedule_update": _m(CATEGORY_SYSTEM, "Delivery schedule updated"),
    "notify.schedule_delete": _m(CATEGORY_SYSTEM, "Delivery schedule deleted"),
    "notify.sample_send": _m(CATEGORY_SYSTEM, "Sample delivery sent"),
    # Agentic alerts (internal planning ticket #146). create/update/delete are emitted by the
    # studio Alerts page (internal planning ticket #147); fire by the evaluator on delivery.
    "alert.create": _m(CATEGORY_SYSTEM, "Alert rule created"),
    "alert.update": _m(CATEGORY_SYSTEM, "Alert rule updated"),
    "alert.delete": _m(CATEGORY_SYSTEM, "Alert rule deleted"),
    "alert.fire": _m(CATEGORY_SYSTEM, "Alert fired"),
    "retention.purge": _m(CATEGORY_SYSTEM, "Retention purge ran"),
    # Per-organization companions to the instance-wide row above, which
    # carries org=None and so appears in nobody's own audit trail. An org
    # admin has to be able to see that their data was the data that went.
    "retention.built_data_purged": _m(CATEGORY_SYSTEM, "Built report data expired"),
    "retention.orphaned_data_purged": _m(CATEGORY_SYSTEM, "Orphaned data removed"),
    # The three dormancy stages, written per organization for the same
    # reason: every tenant this account belongs to is about to lose a member,
    # and the erasure row (person.erase) arrives after the memberships that
    # would have carried it are already gone. None of the three carries the
    # address -- the target pk is the whole identification, exactly as
    # apps.orgs.personal_data.erase argues for its own row.
    "retention.dormancy_warned": _m(CATEGORY_SYSTEM, "Dormant account warned"),
    "retention.dormancy_disabled": _m(CATEGORY_SYSTEM, "Dormant account sign-in disabled"),
    "retention.dormancy_erased": _m(CATEGORY_SYSTEM, "Dormant account erased"),
    "audit.export": _a(CATEGORY_SYSTEM, "Audit log exported"),
}

#: Prefix fallback for an action string that reaches ``audit()`` without a
#: registry entry — a hotfixed call site, most likely. Approximate on purpose:
#: this only has to degrade a row to *a* sensible category, never to crash the
#: request that is being recorded. The registry above is the source of truth;
#: this is the net under it.
_PREFIX_FALLBACK: dict[str, str] = {
    "auth": CATEGORY_AUTH,
    "account": CATEGORY_AUTH,
    "member": CATEGORY_AUTHZ,
    "group": CATEGORY_AUTHZ,
    "report": CATEGORY_ACCESS,
    "share_link": CATEGORY_ACCESS,
    "datasource": CATEGORY_ACCESS,
    "instance": CATEGORY_ADMIN,
    "org": CATEGORY_ADMIN,
    "studio": CATEGORY_ADMIN,
    "assistant": CATEGORY_ADMIN,
    "sso": CATEGORY_ADMIN,
    "share_policy": CATEGORY_ADMIN,
    "operator": CATEGORY_ADMIN,
    "person": CATEGORY_ADMIN,
    "run": CATEGORY_SYSTEM,
    "cache": CATEGORY_SYSTEM,
    "git": CATEGORY_SYSTEM,
    "notify": CATEGORY_SYSTEM,
    "alert": CATEGORY_SYSTEM,
    "retention": CATEGORY_SYSTEM,
    "audit": CATEGORY_SYSTEM,
}

#: What an action with no prefix match at all gets. "system" reads honestly
#: as "the instance did something we don't have a name for" rather than
#: guessing at a more specific category the fallback has no basis for.
_DEFAULT_CATEGORY = CATEGORY_SYSTEM


class UnknownAuditAction(Exception):
    """Raised only in DEBUG — see ``category_for``."""


def category_for(action: str, *, debug: bool = False) -> str:
    """The category for ``action``: the registry, then the prefix fallback.

    In DEBUG this raises on an unregistered action, so a new call site is
    caught the first time a developer exercises it rather than shipping a
    miscategorized row. In production it never raises — a hotfixed or
    third-party call site degrades to a fallback category, and the row still
    gets written; see the module docstring.
    """
    spec = ACTIONS.get(action)
    if spec is not None:
        return spec.category
    if debug:
        raise UnknownAuditAction(
            f"{action!r} is not registered in apps.core.audit_actions.ACTIONS — "
            f"add it there (category, label) before shipping this call site."
        )
    prefix = action.split(".", 1)[0]
    return _PREFIX_FALLBACK.get(prefix, _DEFAULT_CATEGORY)


def label_for(action: str) -> str:
    """Human label for the dashboard's action filter, or the raw verb."""
    spec = ACTIONS.get(action)
    return spec.label if spec is not None else action


def emittable_actions() -> dict[str, ActionSpec]:
    """Every registered action except the reserved ones — what the dashboard's
    action filter offers. Reserved names carry no rows yet by construction, so
    excluding them is cosmetic, not load-bearing; it just keeps the dropdown
    from listing events nothing has ever fired."""
    return {name: spec for name, spec in ACTIONS.items() if not spec.reserved}
