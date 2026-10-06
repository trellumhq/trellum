"""Export and erase one person: GDPR Art. 15/20 (access, portability) and
Art. 17 (erasure). Reached from Organization settings -> Members; see
apps.orgs.views.member_export / member_erase, and vault ticket #077 for why
"remove from the org" was never deprovisioning.

**One inventory, two readers.** :func:`preview` is what the confirmation
screen promises and :func:`erase` is what actually happens; they read the
same :func:`footprint`, because a second hand-maintained list is exactly how
a promise on a confirmation screen turns into a lie.

**Erasure never deletes the User row.** It scrubs the identifying columns and
stamps ``User.erased_at`` -- see that field for why the tombstone *is* the
attributability mechanism. A consequence worth stating: no CASCADE ever
fires, so the LLM billing ledger and delivery schedules that happen to hang
off a user FK are never collateral damage of an erasure.

**Erasure is org-scoped; the account is installation-wide.** An org admin may
erase this organization's copy of a person. They may not erase an identity a
different tenant still employs, and they are never told which other
organizations those are. So:

* member of this org only -> the org footprint goes *and* the account is
  tombstoned installation-wide.
* member of other organizations too -> only this org's footprint goes. The
  account, the sign-in and the other tenants' records are untouched, and this
  org's audit trail keeps naming them, because the account still exists.

The confirmation screen states which of the two is about to happen.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Invitation, RecoveryCode, TotpDevice, UserSession
from apps.assistant.models import AssistantSession, LlmUsage
from apps.core.audit import audit
from apps.core.models import AuditLog
from apps.datasources.models import DataSource
from apps.reports.models import (
    EmailSchedule,
    ReportFavorite,
    ReportViewEvent,
    ShareLink,
)
from apps.runner.models import Run
from apps.studios.models import StudioMembership

from .models import OrgMembership, PermissionGroup, PermissionGroupMembership

#: Reserved by RFC 2606: never resolvable, never registrable, so a tombstoned
#: address can neither be mailed nor re-claimed by the person signing up again.
ERASED_EMAIL_DOMAIN = "erased.invalid"

#: Audit metadata keys that call sites are known to put a raw email address
#: in. Erasure has to reach these or the account's identifier survives in the
#: very table erasure is careful to keep. Kept in step with the call sites by
#: ``apps/orgs/tests/test_personal_data.py::test_every_email_bearing_audit_...``.
EMAIL_METADATA_KEYS = ("email", "attempted", "target_email", "asserted_email")

#: The actions that carry one of the keys above. Filtering by action uses
#: AuditLog's ``(action, -created_at)`` index and stays portable, where a
#: JSON-value query would be neither.
EMAIL_BEARING_ACTIONS = (
    "auth.lockout",
    "auth.login_failed",
    "auth.mfa_reset",
    "auth.sso_denied",
    "group.add_user",
    "member.invite",
    "member.invite_revoke",
    "member.remove",
    "operator.impersonate.expired",
    "operator.impersonate.start",
    "operator.impersonate.stop",
)


def _dt(value):
    return value.isoformat() if value else None


def other_org_count(user, org) -> int:
    """How many *other* organizations this account belongs to.

    A count, never the names: which tenants employ this person is those
    tenants' data, not something an admin of this one gets to read.
    """
    return OrgMembership.objects.filter(user=user).exclude(org=org).count()


# ── The inventory ────────────────────────────────────────────────────────────

def footprint(user, org, *, full: bool = False) -> dict[str, object]:
    """This organization's personal data about *user*, as deletable querysets.

    Ordered the way the confirmation screen reads it. Delivery subscriptions
    are absent on purpose -- they are an m2m link, not a row we may delete;
    see :func:`_unsubscribe`.
    """
    # A pending invitation is an address sitting in a table waiting to
    # re-create the account. When the account itself is ending, every one of
    # them goes, wherever it was sent from -- and the admin is never told
    # where that was, which is why this is one line and not a second bucket.
    invitations = Invitation.objects.filter(email__iexact=user.email)
    if not full:
        invitations = invitations.filter(org=org)
    return {
        "Organization membership": OrgMembership.objects.filter(user=user, org=org),
        "Studio roles": StudioMembership.objects.filter(user=user, studio__org=org),
        "Permission group memberships": PermissionGroupMembership.objects.filter(
            user=user, group__org=org
        ),
        "Favourite reports": ReportFavorite.objects.filter(
            user=user, report__studio__org=org
        ),
        "Assistant conversations (with their transcripts)": AssistantSession.objects.filter(
            user=user, org=org
        ),
        "Pending invitations to this address": invitations,
    }


def account_footprint(user) -> dict[str, object]:
    """Installation-wide credentials and sign-in records. Only erased when
    the account itself is going -- a person who still works for another
    tenant has to keep being able to log in."""
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    return {
        "Sign-in sessions": UserSession.objects.filter(user=user),
        "Second factor (TOTP device)": TotpDevice.objects.filter(user=user),
        "MFA recovery codes": RecoveryCode.objects.filter(user=user),
        "Linked SSO identities": SocialAccount.objects.filter(user=user),
        "Verified email addresses": EmailAddress.objects.filter(user=user),
    }


def _subscriptions(user, org):
    """Delivery schedules this person is an individually-named recipient of."""
    return EmailSchedule.objects.filter(recipients=user, report__studio__org=org)


_SUBSCRIPTIONS_LABEL = (
    "Report delivery subscriptions (the schedules keep running for everyone "
    "else on them)"
)


def _rows(pairs) -> list[dict]:
    return [{"label": label, "count": count} for label, count in pairs]


def preview(user, org) -> dict:
    """Exactly what the button is about to do, for the confirmation screen."""
    others = other_org_count(user, org)
    full = others == 0

    deleted = _rows(
        [(label, qs.count()) for label, qs in footprint(user, org, full=full).items()]
        + [(_SUBSCRIPTIONS_LABEL, _subscriptions(user, org).count())]
    )
    if full:
        deleted += _rows(
            (label, qs.count()) for label, qs in account_footprint(user).items()
        )

    # These survive by design: SET_NULL was already the schema's answer to
    # "this record must outlive the person", and the tombstone keeps those
    # references pointing at something countable instead of at NULL.
    anonymised = _rows([
        ("Audit trail entries they are the actor of",
         AuditLog.objects.filter(actor=user, org=org).count()),
        ("Report views",
         ReportViewEvent.objects.filter(user=user, report__studio__org=org).count()),
        ("Report runs they requested",
         Run.objects.filter(requested_by=user, studio__org=org).count()),
        ("Share links they created",
         ShareLink.objects.filter(created_by=user, report__studio__org=org).count()),
        ("Data sources they last edited",
         DataSource.objects.filter(updated_by=user).filter(_org_scope_q(org)).count()),
        ("LLM spend records — a billing ledger, never purged",
         LlmUsage.objects.filter(user=user, org=org).count()),
    ])

    # Organization property that merely happens to name them. Deleting any of
    # it would be an outage for people who are still here.
    kept = _rows([
        ("Report delivery schedules they set up",
         EmailSchedule.objects.filter(created_by=user, report__studio__org=org).count()),
        ("Permission groups they created",
         PermissionGroup.objects.filter(created_by=user, org=org).count()),
    ])

    return {
        "full": full,
        "other_org_count": others,
        "deleted": deleted,
        "anonymised": anonymised,
        "kept": kept,
    }


def _org_scope_q(org):
    """DataSource lives at either org or studio scope; both belong to *org*."""
    from django.db.models import Q

    return Q(org=org) | Q(studio__org=org)


# ── Export (Art. 15 access, Art. 20 portability) ─────────────────────────────

def export(user, org) -> dict:
    """Everything this installation holds about *user* that this organization's
    admin is entitled to hand them.

    Two things are deliberately absent. **Credentials** -- password hash, TOTP
    secret, recovery-code hashes, share-link tokens, session keys -- because
    they are not facts about the person, they are keys to their account, and a
    downloadable file is the worst place for them. **Other people** -- audit
    metadata routinely names a third party (the member an admin removed, the
    address an invitation went to), so audit rows are exported without their
    metadata and target, and other organizations appear only as a count.
    """
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    return {
        "format": "trellum.personal-data-export",
        "format_version": 1,
        "generated_at": _dt(timezone.now()),
        "scope": {
            "organization": org.slug,
            "other_organizations": other_org_count(user, org),
            "note": (
                "Account-level records are installation-wide; everything else is "
                "limited to this organization. Where this person belongs to other "
                "organizations, those organizations' records are not included and "
                "are not named — they belong to a different tenant."
            ),
            "excluded": (
                "Passwords, MFA secrets, recovery codes, session keys and "
                "share-link tokens are credentials and are never exported. Audit "
                "entries carry no metadata or target, because those name other "
                "people."
            ),
        },
        "account": {
            "id": user.pk,
            "email": user.email,
            "name": user.name,
            "date_joined": _dt(user.date_joined),
            "last_login": _dt(user.last_login),
            "is_active": user.is_active,
            "erased_at": _dt(user.erased_at),
            # A decision this installation has taken about them, on their own
            # data, that they were told about by email -- so it belongs in the
            # Art. 15 answer beside the dates it was taken from.
            "dormancy_warned_at": _dt(user.dormancy_warned_at),
            "mfa_enrolled": user.has_mfa,
            "recovery_codes": {
                "issued": RecoveryCode.objects.filter(user=user).count(),
                "used": RecoveryCode.objects.filter(
                    user=user, used_at__isnull=False
                ).count(),
            },
            "email_addresses": [
                {"email": e.email, "verified": e.verified, "primary": e.primary}
                for e in EmailAddress.objects.filter(user=user)
            ],
            "sso_identities": [
                {
                    "provider": s.provider,
                    "uid": s.uid,
                    "connected_at": _dt(s.date_joined),
                    "claims": s.extra_data,
                }
                for s in SocialAccount.objects.filter(user=user)
            ],
            "sign_in_sessions": [
                {
                    "created_at": _dt(s.created_at),
                    "last_seen": _dt(s.last_seen),
                    "ip": s.ip,
                    "user_agent": s.user_agent,
                    "remember_me": s.remember_me,
                }
                for s in UserSession.objects.filter(user=user)
            ],
        },
        "access": _export_access(user, org),
        "activity": _export_activity(user, org),
    }


def _export_access(user, org) -> dict:
    membership = OrgMembership.objects.filter(user=user, org=org).first()
    return {
        "organization_role": membership.role if membership else None,
        "joined_at": _dt(membership.created_at) if membership else None,
        "studios": [
            {"studio": sm.studio.slug, "role": sm.role, "granted_at": _dt(sm.created_at)}
            for sm in StudioMembership.objects.filter(
                user=user, studio__org=org
            ).select_related("studio")
        ],
        "permission_groups": [
            # added_by is another member; the group name is all that is about
            # this person.
            {"group": pgm.group.name, "added_at": _dt(pgm.created_at)}
            for pgm in PermissionGroupMembership.objects.filter(
                user=user, group__org=org
            ).select_related("group")
        ],
    }


def _export_activity(user, org) -> dict:
    return {
        "favourite_reports": [
            {
                "studio": f.report.studio.slug,
                "report": f.report.slug,
                "since": _dt(f.created_at),
            }
            for f in ReportFavorite.objects.filter(
                user=user, report__studio__org=org
            ).select_related("report__studio")
        ],
        "assistant_conversations": [
            {
                "title": s.title,
                "created_at": _dt(s.created_at),
                "updated_at": _dt(s.updated_at),
                "transcript": s.transcript,
                "usage": s.usage,
            }
            for s in AssistantSession.objects.filter(user=user, org=org)
        ],
        "llm_spend": [
            {"month": u.month.isoformat(), "cost_usd": str(u.cost_usd)}
            for u in LlmUsage.objects.filter(user=user, org=org)
        ],
        "report_views": [
            {
                "studio": v.report.studio.slug,
                "report": v.report.slug,
                "hour": _dt(v.bucket),
            }
            for v in ReportViewEvent.objects.filter(
                user=user, report__studio__org=org
            ).select_related("report__studio")
        ],
        "runs_requested": [
            {
                "studio": r.studio.slug,
                "report": r.slug,
                "status": r.status,
                "requested_at": _dt(r.created_at),
            }
            for r in Run.objects.filter(
                requested_by=user, studio__org=org
            ).select_related("studio")
        ],
        "share_links_created": [
            # No token: it is the credential that opens the report.
            {
                "studio": link.report.studio.slug,
                "report": link.report.slug,
                "created_at": _dt(link.created_at),
                "expires_at": _dt(link.expires_at),
                "revoked_at": _dt(link.revoked_at),
            }
            for link in ShareLink.objects.filter(
                created_by=user, report__studio__org=org
            ).select_related("report__studio")
        ],
        "delivery_subscriptions": [
            {
                "studio": s.report.studio.slug,
                "report": s.report.slug,
                "frequency": s.freq,
                "enabled": s.enabled,
            }
            for s in _subscriptions(user, org).select_related("report__studio")
        ],
        "audit_trail": [
            {
                "at": _dt(row.created_at),
                "action": row.action,
                "category": row.category,
                "outcome": row.outcome,
                "ip": row.ip,
                "user_agent": row.user_agent,
                "role": "actor" if row.actor_id == user.pk else "subject",
            }
            for row in _audit_about(user, org)
        ],
    }


def _audit_about(user, org):
    """Rows where this person acted, plus rows where they were acted upon."""
    from django.db.models import Q

    return AuditLog.objects.filter(org=org).filter(
        Q(actor=user)
        | Q(target_type="accounts.user", target_id=str(user.pk))
    ).order_by("created_at")


# ── Erasure (Art. 17) ────────────────────────────────────────────────────────

def _unsubscribe(user, org) -> int:
    """Drop the person from delivery schedules without touching the schedules.

    They are the organization's configuration and are addressed to other
    people too; removing the m2m link is the erasure, deleting the row would
    be someone else's outage.
    """
    schedules = list(_subscriptions(user, org))
    for schedule in schedules:
        schedule.recipients.remove(user)
    return len(schedules)


def _scrub_audit_metadata(user) -> int:
    """Replace the erased address wherever a call site stored it as metadata.

    Without this the account's own identifier survives in the one table
    erasure deliberately keeps, and the erasure is cosmetic.
    """
    email = (user.email or "").lower()
    if not email:
        return 0
    scrubbed = 0
    # ponytail: one pass over the email-bearing actions, no JSON index. Erasure
    # is an admin-triggered one-off; add a GIN index on metadata if this ever
    # runs often enough to matter.
    for row in AuditLog.objects.filter(action__in=EMAIL_BEARING_ACTIONS).iterator():
        metadata = row.metadata or {}
        hits = [
            key
            for key in EMAIL_METADATA_KEYS
            if str(metadata.get(key, "")).lower() == email
        ]
        if not hits:
            continue
        for key in hits:
            metadata[key] = "[erased]"
        row.metadata = metadata
        row.save(update_fields=["metadata"])
        scrubbed += 1
    return scrubbed


def _tombstone(user) -> None:
    """Strip the identity, keep the row. See User.erased_at."""
    user.email = f"erased-{user.pk}@{ERASED_EMAIL_DOMAIN}"
    user.name = ""
    user.theme = ""
    user.is_active = False
    user.is_staff = False
    user.is_operator_flag = False
    user.is_superuser = False
    user.erased_at = timezone.now()
    user.set_unusable_password()
    # Every live session dies on its next request -- SessionSecurityMiddleware
    # compares this against the stamp written into the session at login. The
    # django_session rows themselves are not addressable by user and are swept
    # by apps.core.retention.purge_sessions once expired.
    user.auth_epoch += 1
    user.save()
    user.groups.clear()
    user.user_permissions.clear()


@transaction.atomic
def erase(request, user, org) -> dict:
    """Erase *user* from *org*, and the account too when no other org holds it.

    Returns the counts the caller reports back to the admin. Audited as
    ``person.erase`` -- and the row deliberately carries no email address:
    it identifies the erased person by primary key, which is exactly the
    pseudonymous handle the tombstone leaves behind. Recording the address
    "for the record" would undo the erasure in the record.
    """
    full = other_org_count(user, org) == 0

    removed = {
        label: qs.delete()[0] for label, qs in footprint(user, org, full=full).items()
    }
    removed["Report delivery subscriptions"] = _unsubscribe(user, org)

    scrubbed = 0
    if full:
        for label, qs in account_footprint(user).items():
            removed[label] = qs.delete()[0]
        scrubbed = _scrub_audit_metadata(user)
        _tombstone(user)

    audit(
        request,
        "person.erase",
        target=user,
        org=org,
        scope="account" if full else "organization",
        rows_deleted=sum(removed.values()),
        audit_rows_scrubbed=scrubbed,
    )
    return {"full": full, "removed": removed, "audit_rows_scrubbed": scrubbed}
