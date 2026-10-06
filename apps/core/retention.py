"""Deleting the things that would otherwise grow forever.

Each target is a small function returning ``(label, count)`` and taking a
``dry_run`` flag, so `manage.py cleanup --dry-run` reports exactly what the real
run would remove. They are separate rather than one big sweep because they fail
independently — a locked table or a missing directory should not stop the rest.

Two rules run through all of them:

**Delete in bounded batches.** A single ``DELETE`` over a million rows holds
locks and bloats WAL. `Run` is the job queue as well as the history, so a long
lock there stops every build on the instance.

**Never touch the current billing month.** ``apps/orgs/quotas.py`` derives
monthly build minutes by summing ``finished_at - started_at`` over ``Run``
rather than from a ledger. Deleting a run inside the current month silently
reduces an organization's recorded usage, so retention would quietly hand back
quota. Anything older than the current month is safe, and the default 90-day
window is nowhere near it — but the guard is explicit because the coupling is
not obvious from either file alone.

One target does not delete anything that grew: the account dormancy policy
acts on people rather than on rows, so it is warn-then-disable-then-erase
rather than a sweep, and it is off by default. Its own rules are in the
"Dormant accounts" section below.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Rows per DELETE. Big enough to be efficient, small enough that the lock is
#: never interesting.
BATCH = 1000


def _cutoff(days: int):
    """``None`` when the window is 0 — the documented "keep forever"."""
    return None if not days else timezone.now() - timedelta(days=days)


def _delete_in_batches(queryset) -> int:
    """Delete by primary key in chunks. Returns the number removed."""
    model = queryset.model
    total = 0
    while True:
        pks = list(queryset.values_list("pk", flat=True)[:BATCH])
        if not pks:
            return total
        deleted, _ = model.objects.filter(pk__in=pks).delete()
        # `deleted` counts cascaded rows too; count what we asked for instead,
        # so the report says "500 runs" rather than "1500 objects".
        total += len(pks)
        if deleted == 0:  # nothing removable; avoid spinning
            return total


def purge_runs(dry_run: bool = False) -> tuple[str, int]:
    """Old terminal runs, keeping the newest N per report."""
    from apps.orgs.quotas import month_start
    from apps.runner.models import Run

    policy = settings.RETENTION
    cutoff = _cutoff(policy["run_days"])
    if cutoff is None:
        return "runs", 0

    # Never inside the current billing month — see the module docstring.
    cutoff = min(cutoff, month_start())

    candidates = Run.objects.filter(created_at__lt=cutoff).exclude(
        status__in=Run.ACTIVE_STATUSES
    )

    keep_n = policy["run_keep_per_report"]
    if not keep_n:
        if dry_run:
            return "runs", candidates.count()
        return "runs", _delete_in_batches(candidates)

    # The newest N per (studio, slug) survive at any age, so a quarterly report
    # does not lose its whole history to a 90-day window.
    #
    # Handled one report at a time rather than by collecting every protected id
    # first: on an instance with thousands of reports that set is tens of
    # thousands of UUIDs, held in memory and then sent back as a single IN
    # clause. Retention is the code that runs when data has grown, so it is the
    # last place to assume it hasn't.
    total = 0
    pairs = list(candidates.values_list("studio_id", "slug").distinct())
    for studio_id, slug in pairs:
        keep_ids = list(
            Run.objects.filter(studio_id=studio_id, slug=slug)
            .order_by("-created_at")
            .values_list("pk", flat=True)[:keep_n]
        )
        group = candidates.filter(studio_id=studio_id, slug=slug).exclude(
            pk__in=keep_ids
        )
        total += group.count() if dry_run else _delete_in_batches(group)
    return "runs", total


def blank_run_output(dry_run: bool = False) -> tuple[str, int]:
    """Drop the log tails from older runs but keep the rows.

    Most of the bytes in a Run row are the two ~50 KB tails; the row itself is
    what history and quota accounting need. This is the cheapest large win.
    """
    from apps.runner.models import Run

    cutoff = _cutoff(settings.RETENTION["run_output_days"])
    if cutoff is None:
        return "run log tails", 0

    stale = Run.objects.filter(created_at__lt=cutoff).exclude(
        stdout_tail="", stderr_tail=""
    ).exclude(status__in=Run.ACTIVE_STATUSES)

    if dry_run:
        return "run log tails", stale.count()
    return "run log tails", stale.update(stdout_tail="", stderr_tail="")


#: Categories governed by the shorter access-events window (RETENTION_AUDIT_ACCESS_DAYS,
#: default 90): auth + access events are 10-100x the volume of mutations
#: (internal planning#76 predicted exactly this) and their forensic half-life
#: is shorter. authz/admin/system stay under audit_days (default 365) -- that
#: is the compliance record. See apps.core.audit_actions for what a category
#: is and apps.core.models.AuditLog for why it is a column, not a scan.
_AUDIT_ACCESS_CATEGORIES = ("auth", "access")


def _archive_row(row) -> dict:
    """One AuditLog row as a plain-JSON dict for the NDJSON archive.

    Field names match the model, not the CSV export's column order -- this
    is a machine format for cold storage, read back by nothing in this
    product yet, so it optimizes for "every column, unambiguous" over any
    particular consumer.
    """
    return {
        "id": row.pk,
        "created_at": row.created_at.isoformat(),
        "org": row.org.slug if row.org_id else None,
        "actor_id": row.actor_id,
        "impersonator_id": row.impersonator_id,
        "action": row.action,
        "category": row.category,
        "outcome": row.outcome,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "metadata": row.metadata,
        "ip": row.ip,
        "user_agent": row.user_agent,
    }


def _write_audit_archive(rows) -> None:
    """Append ``rows`` to their (org, year-month) gzip NDJSON files under
    ``DATA_DIR/archive/audit/``.

    Local disk only for now. TODO(audit-archive-object-storage): route
    through ``apps.core.s3`` when the S3 storage backend is configured,
    the way report output does -- deferred rather than half-wired, because
    that module's key layout and Cache-Control table are built around report
    builds, not arbitrary archives, and reusing them correctly is its own
    change. Every self-host instance -- the default topology, and the one
    RETENTION_AUDIT_ARCHIVE exists for -- is served correctly by the local
    path in the meantime; DATA_DIR is durable there.

    Appends rather than overwrites: a later purge run in the same month adds
    more lines to the same file. Gzip supports concatenated members, so this
    reads back correctly with nothing more than ``gzip.open`` -- see
    apps/core/tests/test_retention.py's archive round-trip test.
    """
    import gzip
    import json

    if not rows:
        return
    root = settings.DATA_DIR / "archive" / "audit"
    buckets: dict[tuple[str, str], list] = {}
    for row in rows:
        org_slug = row.org.slug if row.org_id else "_none"
        month = row.created_at.strftime("%Y%m")
        buckets.setdefault((org_slug, month), []).append(row)
    for (org_slug, month), group in buckets.items():
        directory = root / org_slug
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{month}.ndjson.gz"
        with gzip.open(path, "at", encoding="utf-8") as fh:
            for row in group:
                fh.write(json.dumps(_archive_row(row), default=str) + "\n")


def _archive_and_delete_in_batches(queryset) -> int:
    """Like :func:`_delete_in_batches`, but archives each chunk immediately
    before deleting it -- bounded to ``BATCH`` rows in memory at a time, same
    as every other target here, so RETENTION_AUDIT_ARCHIVE stays safe to turn
    on even on an instance whose AuditLog has never been pruned before.

    Not exactly-once: a process killed between a chunk's archive write and its
    delete re-archives (appends duplicate lines for) that one chunk on the
    next run, before deleting it for real. Cheaper to accept than to build a
    two-phase commit between a gzip file and a DELETE for a nightly batch job.
    """
    model = queryset.model
    total = 0
    while True:
        pks = list(queryset.values_list("pk", flat=True)[:BATCH])
        if not pks:
            return total
        rows = list(model.objects.filter(pk__in=pks).select_related("org"))
        _write_audit_archive(rows)
        deleted, _ = model.objects.filter(pk__in=pks).delete()
        total += len(pks)
        if deleted == 0:
            return total


def purge_audit(dry_run: bool = False) -> tuple[str, int]:
    """Two category-filtered passes over AuditLog, each through the same
    batched delete (archive-before-prune, when RETENTION_AUDIT_ARCHIVE is on)
    as every other target in this module.
    """
    from apps.core.models import AuditLog

    policy = settings.RETENTION
    archive = bool(policy.get("audit_archive"))
    total = 0
    for categories, days_key in (
        (_AUDIT_ACCESS_CATEGORIES, "audit_access_days"),
        (None, "audit_days"),  # None = everything else: authz, admin, system
    ):
        cutoff = _cutoff(policy[days_key])
        if cutoff is None:
            continue
        qs = AuditLog.objects.filter(created_at__lt=cutoff)
        qs = (
            qs.filter(category__in=categories)
            if categories is not None
            else qs.exclude(category__in=_AUDIT_ACCESS_CATEGORIES)
        )
        if dry_run:
            total += qs.count()
        elif archive:
            total += _archive_and_delete_in_batches(qs)
        else:
            total += _delete_in_batches(qs)
    return "audit rows", total


def purge_assistant_sessions(dry_run: bool = False) -> tuple[str, int]:
    """Whole LLM transcripts live in a JSON column, so these rows are large.

    LlmUsage is deliberately untouched: it is the billing ledger.
    """
    from apps.assistant.models import AssistantSession

    cutoff = _cutoff(settings.RETENTION["assistant_session_days"])
    if cutoff is None:
        return "assistant sessions", 0
    old = AssistantSession.objects.filter(updated_at__lt=cutoff)
    if dry_run:
        return "assistant sessions", old.count()
    return "assistant sessions", _delete_in_batches(old)


def purge_invitations(dry_run: bool = False) -> tuple[str, int]:
    """Accepted or expired invitations. A pending, unexpired one is live."""
    from django.db.models import Q

    from apps.accounts.models import Invitation

    cutoff = _cutoff(settings.RETENTION["invitation_days"])
    if cutoff is None:
        return "invitations", 0
    now = timezone.now()
    done = Invitation.objects.filter(
        Q(accepted_at__isnull=False) | Q(expires_at__lt=now)
    ).filter(created_at__lt=cutoff)
    if dry_run:
        return "invitations", done.count()
    return "invitations", _delete_in_batches(done)


def purge_sessions(dry_run: bool = False) -> tuple[str, int]:
    """Expired django_session rows.

    Sessions are database-backed and nothing has ever run `clearsessions`, so
    this table grows with every login for the life of the instance — and
    impersonation writes to it too. Django's own `expire_date` is the policy;
    this only enforces it.
    """
    from django.contrib.sessions.models import Session

    expired = Session.objects.filter(expire_date__lt=timezone.now())
    if dry_run:
        return "expired sessions", expired.count()
    return "expired sessions", _delete_in_batches(expired)


def purge_user_sessions(dry_run: bool = False) -> tuple[str, int]:
    """``UserSession`` rows (the "where you're signed in" registry) whose
    ``django_session`` row is already gone -- expired, revoked, or replaced
    by a later login that reused the same key. Best-effort observability
    table, not the enforcement mechanism -- see
    ``apps.accounts.models.UserSession``'s docstring.
    """
    from apps.accounts.session_policy import dead_user_sessions

    dead = dead_user_sessions()
    if dry_run:
        return "stale session registry rows", dead.count()
    return "stale session registry rows", _delete_in_batches(dead)


def purge_tmp_runs(dry_run: bool = False) -> tuple[str, int]:
    """Sandbox scratch directories left behind under <DATA_DIR>/tmp/runs.

    The worker sweeps these at boot, which misses everything on a box that does
    not restart — exactly the well-behaved instance where they pile up.
    """
    from apps.runner.models import Run

    hours = settings.RETENTION["tmp_run_hours"]
    if not hours:
        return "sandbox scratch dirs", 0

    root = settings.DATA_DIR / "tmp" / "runs"
    if not root.is_dir():
        return "sandbox scratch dirs", 0

    cutoff = timezone.now() - timedelta(hours=hours)
    active = set(
        str(pk) for pk in Run.objects.filter(
            status__in=Run.ACTIVE_STATUSES
        ).values_list("pk", flat=True)
    )

    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name in active:
            continue
        try:
            mtime = timezone.datetime.fromtimestamp(
                entry.stat().st_mtime, tz=timezone.get_current_timezone()
            )
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        removed += 1
        if not dry_run:
            shutil.rmtree(entry, ignore_errors=True)
    return "sandbox scratch dirs", removed


def purge_report_view_events(dry_run: bool = False) -> tuple[str, int]:
    """Raw per-view events (internal planning#4), batch-deleted past their window.

    ``ReportViewDaily`` (the rollup they feed) is never touched here -- it
    is kept forever precisely so the dashboard's history survives the raw
    log being trimmed. See apps.reports.models.record_view for how the two
    are written.
    """
    from apps.reports.models import ReportViewEvent

    cutoff = _cutoff(settings.RETENTION["report_view_event_days"])
    if cutoff is None:
        return "report view events", 0
    old = ReportViewEvent.objects.filter(created_at__lt=cutoff)
    if dry_run:
        return "report view events", old.count()
    return "report view events", _delete_in_batches(old)




# ── Dormant accounts: warn, then disable, then erase ────────────────────────
#
# Everything else in this module deletes rows and bytes. This one acts on
# people, on a timer, so it is built to fail in the direction of keeping an
# account rather than closing one.
#
# **The clock is the account's, not a membership's.** ``last_login`` is what
# "dormant" means, and it is a column on the account, true for every
# organization at once. That is the whole reason the erase stage cannot just
# call ``personal_data.erase`` for one org -- see :func:`_erase_account`.
#
# **Each stage is a separate target**, so ``manage.py cleanup --target
# warn_dormant_accounts`` works, and so an SMTP outage that breaks the warning
# does not also stop an already-warned account from progressing. They are
# registered in TARGETS in stage order, but nothing depends on that: each
# stage's own window keeps an account out of the next one for a whole grace
# period, so the worst a different order could cost is a night of latency.
#
# **The warning is the gate.** ``User.dormancy_warned_at`` is stamped only
# once the mail has actually been accepted for delivery, and both later
# stages count from that stamp rather than from the threshold. A warning that
# was never sent leaves the stamp NULL, and an account with a NULL stamp is
# never disabled and never erased -- so a broken mail server stalls the
# policy instead of running it silently. That is the "a single missed email
# must never cost someone their account" rule, expressed as the only piece of
# state the policy has.


def _dormant_candidates():
    """Accounts the dormancy policy may act on at all, annotated with
    ``last_seen``: ``last_login``, or ``date_joined`` where the account has
    never been signed into.

    **Never signed in falls back to when the account was created.** A NULL
    ``last_login`` on a *User row* does not mean "invited and never
    accepted" -- accepting an invitation is what creates the row, and it
    signs the person in in the same request (``apps.accounts.views
    .invite_accept_view``), so an unaccepted invitation has no account here
    at all and ages out on ``Invitation.expires_at`` instead, swept by
    :func:`purge_invitations`. What a NULL actually means is an account
    created some other way and then never used: provisioned by the first-run
    wizard or by an operator for somebody who never showed up. Holding a name
    and an address for two years for a person who has never once signed in is
    exactly the case the policy is for, and reading NULL as "never dormant"
    would make it the one case that is exempt for ever.

    Two exclusions. **Operator accounts** (``is_superuser`` / ``is_staff`` /
    ``is_operator_flag``): the break-glass account on a self-hosted instance
    is precisely the one that legitimately goes years without a sign-in, and
    erasing the last superuser would lock an installation out of itself
    permanently, with nothing left that could undo it. **Already-erased
    accounts**: a tombstone's clock is frozen at the moment of erasure, so
    without this every erased row would be reconsidered every night for the
    rest of the instance's life.
    """
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from django.db.models.functions import Coalesce

    return (
        get_user_model()
        .objects.filter(erased_at__isnull=True)
        .exclude(Q(is_superuser=True) | Q(is_staff=True) | Q(is_operator_flag=True))
        .annotate(last_seen=Coalesce("last_login", "date_joined"))
    )


def _warning_is_live():
    """Q for "we warned this account and they have not signed in since".

    Nothing ever clears ``dormancy_warned_at``; a stamp older than the
    account's own clock is simply spent. So this one comparison against
    ``last_seen`` is what makes a sign-in reset all three stages at once, and
    it rides on the column Django already updates inside ``login()`` -- every
    sign-in path there is or ever will be, with no receiver to register and
    no new one to remember.
    """
    from django.db.models import F, Q

    return Q(dormancy_warned_at__isnull=False) & Q(dormancy_warned_at__gt=F("last_seen"))


def warn_dormant_accounts(dry_run: bool = False) -> tuple[str, int]:
    """Stage one: email the account to say it is about to be closed.

    The count is warnings *delivered*, not accounts found. A send that raises
    leaves the account unstamped and therefore still at stage one, to be
    tried again tomorrow.
    """
    from django.db.models import F, Q

    label = "dormancy warnings"
    days = settings.RETENTION["dormant_days"]
    cutoff = _cutoff(days)
    if cutoff is None:
        return label, 0

    # ``is_active=True``: an account somebody already switched off does not
    # get told to "sign in once to keep it", because it cannot. An account
    # deactivated by hand therefore never enters the policy at all -- which is
    # right, since a person made that decision and this is not the mechanism
    # for revisiting it.
    due = _dormant_candidates().filter(is_active=True, last_seen__lt=cutoff).filter(
        Q(dormancy_warned_at__isnull=True) | Q(dormancy_warned_at__lte=F("last_seen"))
    )
    if dry_run:
        return label, due.count()

    sent = 0
    for user in list(due):
        # One timestamp for the mail and the stamp, so the two deadlines the
        # person reads are the two the sweep will actually act on.
        warned_at = timezone.now()
        try:
            _send_dormancy_warning(user, warned_at=warned_at)
        except Exception:  # noqa: BLE001 - one bad address must not stop the rest
            logger.exception("retention: could not warn dormant account %s", user.pk)
            continue
        # Stamped after the send and never before it: this column is the only
        # authority the two irreversible stages have.
        type(user).objects.filter(pk=user.pk).update(dormancy_warned_at=warned_at)
        _audit_dormancy(user, "retention.dormancy_warned", dormant_days=days)
        sent += 1
    return label, sent


def disable_dormant_accounts(dry_run: bool = False) -> tuple[str, int]:
    """Stage two: switch sign-in off. Nothing is deleted, so this is still
    reversible -- but only through the Django admin, the one surface in the
    product that writes ``User.is_active``. Clearing ``dormancy_warned_at``
    there is part of putting an account back: leaving a live warning stamped
    means tomorrow's sweep disables it again (``apps.accounts.admin`` puts
    the two fields side by side for that reason).
    """
    label = "dormant sign-ins disabled"
    cutoff = _cutoff(settings.RETENTION["dormant_disable_days"])
    if cutoff is None or not settings.RETENTION["dormant_days"]:
        return label, 0

    due = _dormant_candidates().filter(
        _warning_is_live(), is_active=True, dormancy_warned_at__lt=cutoff
    )
    if dry_run:
        return label, due.count()

    count = 0
    for user in list(due):
        type(user).objects.filter(pk=user.pk).update(is_active=False)
        _audit_dormancy(user, "retention.dormancy_disabled")
        count += 1
    return label, count


def erase_dormant_accounts(dry_run: bool = False) -> tuple[str, int]:
    """Stage three, and the only irreversible one.

    Both earlier windows have to be on. Erasure may only follow a *disabled*
    account, because that dead sign-in is the last chance anyone has to
    notice -- a support ticket saying "I can't log in" is worth more than any
    email -- so switching stage two off switches this off with it, rather
    than quietly promoting erasure into stage two's place.
    """
    policy = settings.RETENTION
    label = "dormant accounts erased"
    grace = policy["dormant_disable_days"]
    if not policy["dormant_days"] or not grace or not policy["dormant_erase_days"]:
        return label, 0

    # From the warning, through the disable grace, then the erase grace: the
    # stamp is the only date anybody was actually told anything on.
    cutoff = _cutoff(grace + policy["dormant_erase_days"])
    due = _dormant_candidates().filter(
        _warning_is_live(), is_active=False, dormancy_warned_at__lt=cutoff
    )
    if dry_run:
        return label, due.count()

    count = 0
    for user in list(due):
        # Before the erase, not after: this names every organization the
        # account belongs to, and erasing is what deletes those memberships.
        _audit_dormancy(user, "retention.dormancy_erased", dormant_days=policy["dormant_days"])
        _erase_account(user)
        count += 1
    return label, count


def _erase_account(user) -> None:
    """Erase this person from every organization, and then the account.

    ``personal_data.erase`` is organization-scoped on purpose, and that
    scoping is a statement about *authority*: an org admin pressing "erase
    this person" may remove their own tenant's copy of someone, and may not
    reach an identity another tenant still employs. Dormancy is not that
    decision. It is a fact about the account -- nobody has signed in for two
    years, which is true in every organization simultaneously -- and it is
    the instance's policy rather than any one tenant's request. Stopping at
    one org would leave the identity, the address and the sign-in alive,
    which is the single thing the policy promised to remove: the org-scoped
    erase would have returned ``full=False`` and quietly done a fraction of
    the job while the audit trail recorded a completed erasure.

    So the account is erased everywhere, one ``erase()`` call per membership.
    Each call removes that organization's footprint and writes that
    organization's own ``person.erase`` row, and the last one -- by then the
    only organization left holding the account -- sees ``other_org_count()``
    reach zero and tombstones the account itself. Looping the existing
    function rather than writing a wider one keeps ``apps.orgs.personal_data``
    the only inventory of what "everything about this person" means; a second
    copy of that list living here is exactly how the promise on the
    confirmation screen and the thing that actually happens drift apart.

    Not wrapped in one transaction across the organizations. Each ``erase()``
    is atomic on its own, and a crash between two of them leaves the account
    with fewer memberships and the same dormancy stamp, so the next night's
    sweep finishes the job. A single transaction spanning every organization
    would only hold a wider lock to buy an atomicity nothing here needs.

    ``request=None``: there is no request behind a nightly job, and
    ``audit()`` reads an absent one as an absent actor -- "the instance did
    this", which is exactly true.
    """
    from apps.orgs.models import OrgMembership
    from apps.orgs.personal_data import erase

    orgs = [m.org for m in OrgMembership.objects.filter(user=user).select_related("org")]
    # ``[None]`` is not a fallback, it is the orgless account's real scope: a
    # user who belongs nowhere still holds credentials, sessions and an
    # address, and ``erase(user, None)`` finds no org footprint, counts zero
    # other organizations, and goes straight to the account itself.
    for org in orgs or [None]:
        erase(None, user, org)


def _audit_dormancy(user, action: str, **metadata) -> None:
    """One row per organization holding this account, plus an instance-wide
    one when it holds none.

    Same reasoning as :func:`_audit_org_purge`: a row with ``org=None``
    appears in no organization's own trail, and every tenant this person
    belongs to is about to lose a member. No email address goes in the
    metadata -- the target primary key identifies the account, and it is the
    handle that survives the tombstone, which is the point
    ``apps.orgs.personal_data.erase`` makes about its own row.
    """
    from apps.core.audit import audit
    from apps.orgs.models import OrgMembership

    orgs = [m.org for m in OrgMembership.objects.filter(user=user).select_related("org")]
    for org in orgs or [None]:
        audit(None, action, target=user, org=org, **metadata)


def _dormancy_dates(user, warned_at=None) -> tuple[object, object]:
    """``(disable_on, erase_on)`` for one annotated account, either being
    ``None`` where that stage is switched off.

    Both run from the warning: the live stamp where there is one, and the
    *projected* warning date where there is not, so the settings page can say
    what is going to happen to somebody nobody has emailed yet.

    ``warned_at`` overrides both, and exists for exactly one caller: the
    warning mail itself is composed before its own stamp is written (the
    stamp only lands if the send succeeds), so without it the mail would date
    its two deadlines from a projected warning that is happening *right now*
    -- and for an account long past the threshold, that projection is in the
    past. Telling somebody their account will be disabled last month is worse
    than not writing to them at all.
    """
    policy = settings.RETENTION
    warned = warned_at or user.dormancy_warned_at
    if not warned or warned <= user.last_seen:
        warned = user.last_seen + timedelta(days=policy["dormant_days"])
    grace = policy["dormant_disable_days"]
    if not grace:
        return None, None
    disable_on = warned + timedelta(days=grace)
    erase_days = policy["dormant_erase_days"]
    return disable_on, (disable_on + timedelta(days=erase_days) if erase_days else None)


def dormant_soon(org, *, within_days: int = 30) -> list[dict]:
    """One organization's members at or approaching dormancy, soonest first.

    The read-only twin of the three stages above, scoped to the org whose
    settings page is asking and never writing anything. ``within_days``
    reaches *back* from the threshold, so an admin sees a colleague before
    the first email rather than after it -- the point of showing this at all
    is that someone on long leave gets caught by a person who knows that,
    not only by a policy that does not.

    Each entry: ``user``, ``last_seen``, ``never_signed_in``, ``stage``
    ("approaching" / "warned" / "disabled"), ``next_at`` and ``days_left``
    for whatever happens next, and ``erase_on``.
    """
    days = settings.RETENTION["dormant_days"]
    if not days:
        return []

    now = timezone.now()
    horizon = now - timedelta(days=days - within_days)
    people = _dormant_candidates().filter(org_memberships__org=org, last_seen__lt=horizon)

    out = []
    for user in people:
        disable_on, erase_on = _dormancy_dates(user)
        warned = bool(user.dormancy_warned_at and user.dormancy_warned_at > user.last_seen)
        if warned and not user.is_active:
            stage, next_at = "disabled", erase_on
        elif warned:
            stage, next_at = "warned", disable_on
        else:
            stage, next_at = "approaching", user.last_seen + timedelta(days=days)
        out.append({
            "user": user,
            "last_seen": user.last_seen,
            "never_signed_in": user.last_login is None,
            "stage": stage,
            "next_at": next_at,
            "days_left": (next_at - now).days if next_at else None,
            "erase_on": erase_on,
        })
    # A stage that is switched off has no date, so those sort last rather
    # than crashing the comparison.
    out.sort(key=lambda item: (item["next_at"] is None, item["next_at"] or now))
    return out


def _send_dormancy_warning(user, warned_at=None) -> None:
    """The one email the policy sends. Raises on a failed send, and the
    caller reads that as "not warned yet" -- the whole safety property.

    Reuses the portal's existing text+HTML send path
    (``notify._send_simple``: the configured backend, the portal's From
    address, the shared email chrome) rather than introducing a second way to
    send mail.
    """
    from apps.core.instance import base_url, instance_name
    from apps.reports.notify import _send_simple

    disable_on, erase_on = _dormancy_dates(user, warned_at=warned_at or timezone.now())
    name = instance_name()
    _send_simple(
        f"Sign in to keep your {name} account",
        "email/dormant_warning",
        {
            "instance_name": name,
            "sign_in_url": f"{base_url()}/login",
            "last_seen": user.last_seen,
            "never_signed_in": user.last_login is None,
            "disable_on": disable_on,
            "erase_on": erase_on,
            "accent": "#B45309",
            "status_color": "#B45309",
            "status_label": "ACTION NEEDED",
            "preview_text": "Sign in once and nothing happens to your account.",
        },
        user,
    )


# ── An organization's own data: what Trellum built, and what it was given ──
#
# Everything above this line is instance housekeeping on one global window.
# These two are the customer-facing promise (internal planning ticket #110): built data has a hard
# cap an organization may tighten and nobody may loosen, uploaded files get a
# longer courtesy window that only starts once nothing references them. Two
# windows because they are two different claims — "we do not keep what we
# made" and "we tidy up after ourselves".


def built_window(org) -> int:
    """Days before an org's built report data expires. Never above the ceiling."""
    ceiling = settings.RETENTION["built_data_days"]
    chosen = getattr(org, "retention_built_days", None)
    if not ceiling:
        # The instance turned the cap off outright (legal hold). Only then can
        # an org's own 0 mean "keep forever".
        return chosen or 0
    if not chosen:
        # None (never chose) and 0 both inherit. 0 must NOT mean "forever"
        # while a ceiling exists, or one org could opt out of the promise.
        return ceiling
    return min(chosen, ceiling)


def upload_window(org) -> int:
    """Days before an abandoned upload is swept. No ceiling; 0 keeps forever."""
    chosen = getattr(org, "retention_abandoned_upload_days", None)
    return settings.RETENTION["abandoned_upload_days"] if chosen is None else chosen


# ``getattr`` rather than ``org.retention_*`` in both, and ``org`` may be None:
# these windows also govern trees whose organization row is gone, where the
# honest answer is the instance default. One lookup covers "never chose" and
# "nobody left to ask".


def purge_built_data(dry_run: bool = False) -> tuple[str, int]:
    """Built report output past its organization's window.

    Per organization rather than one global query, because the window is per
    organization now. The clock is ``Report.last_built_at`` — see that field
    for why neither ``_meta.json`` nor directory mtime can serve.
    """
    count, freed = _sweep_built_data(delete=not dry_run)
    _log_sweep("built report output", count, freed, dry_run)
    return "built report output", count


def purge_orphaned_data(dry_run: bool = False) -> tuple[str, int]:
    """Bytes on the data volume that no row owns any more.

    Deleting a studio deletes rows, not files (``apps.orgs.views.studio_delete``:
    "DB rows only; files under /data are kept until pruned"). So
    :func:`purge_built_data`, which walks ``Report`` rows, cannot see a deleted
    studio's output at all — and without this pass that output would outlive the
    hard cap precisely because its rows are gone.

    Two shapes, one window. Both are bytes nothing owns any more:

    * a rowless studio's or organization's built output;
    * an uploaded file no ``DataSource`` row resolves to.

    Both age on the abandoned window. Built output that still *has* a report is
    a different question entirely and is not swept here — it stays until
    someone deletes the report (see ``built_data_days``, off by default).

    A file a live data source still points at is never swept, at any age. That
    is the invariant the design rests on: the clock starts when nothing
    references the file, not when the file was written.
    """
    count, freed, basis = _sweep_orphans(delete=not dry_run)
    _log_sweep("orphaned data", count, freed, dry_run, basis=basis)
    return "orphaned data", count


def reclaimable_bytes() -> int:
    """What the two data windows would free if they ran right now.

    A directory walk, deliberately: there is no counter to read and one would
    drift the first time an operator cleaned up by hand. Bounded by what is
    *already* expired, which is close to nothing on an instance whose cleanup
    runs nightly — and large exactly when the health check needs to say so.
    """
    return _sweep_built_data(delete=False)[1] + _sweep_orphans(delete=False)[1]


# ── Read-only previews for the org settings page ────────────────────────────
#
# Same clocks the two sweeps above use -- built_window/upload_window,
# Report.last_built_at, a file's mtime or the audit row that explains its
# abandonment -- scoped to one organization and never deleting anything.
# purge_built_data/purge_orphaned_data stay the only code that removes a
# byte; these only describe, ahead of time, what they would find.


def _org_built_reports(org):
    """Reports with live output, per organization -- the same rows
    :func:`_sweep_built_data` would fetch for this org, before its own
    ``last_built_at`` cutoff is applied.
    """
    from apps.reports.models import Report

    return list(
        Report.objects.filter(
            studio__org=org, last_built_at__isnull=False, data_expired_at__isnull=True
        ).select_related("studio")
    )


def _org_uploads(org):
    """Every unreferenced uploaded file under one organization's studios and
    its own shared data-sources directory, with when it was abandoned.

    Deleted-studio output trees (the "rowless studio" half of
    :func:`_sweep_orphans`) are not walked here -- that is a consequence of
    deleting a whole studio, told to the admin at delete time
    (``apps.orgs.views.studio_delete``), not of this org's day-to-day upload
    window. ``manage.py cleanup --dry-run`` remains the complete preview.
    """
    from apps.studios.models import Studio

    referenced = _referenced_files()
    _tree_deletes, source_deletes = _deletion_times()
    roots = [(s.slug, s.datasources_dir / "files") for s in Studio.objects.filter(org=org)]
    roots.append((None, org.datasources_dir / "files"))

    out = []
    for studio_slug, files_dir in roots:
        for path in _files_under(files_dir):
            if _norm(path) in referenced:
                continue  # a live DataSource still resolves to this file
            try:
                stat = path.stat()
            except OSError:
                continue
            when, _basis = _abandoned_at(
                _as_datetime(stat.st_mtime), source_deletes.get(path.stem)
            )
            out.append({
                "name": f"{studio_slug}/{path.name}" if studio_slug else path.name,
                "when": when,
                "size": stat.st_size,
            })
    return out


def org_preview(org) -> dict:
    """What tonight's sweep would remove for one organization, right now.

    The read-only twin of ``purge_built_data``/``purge_orphaned_data``'s
    dry-run mode, scoped to the org whose settings page is asking rather
    than walking the whole instance.
    """
    from apps.core import storage

    now = timezone.now()
    built_count = built_freed = 0
    days = built_window(org)
    if days:
        for report in _org_built_reports(org):
            if _expired(now, report.last_built_at, days):
                built_count += 1
                built_freed += storage.output_bytes(report.studio, report.slug)

    upload_count = upload_freed = 0
    days = upload_window(org)
    if days:
        for upload in _org_uploads(org):
            if _expired(now, upload["when"], days):
                upload_count += 1
                upload_freed += upload["size"]

    return {
        "built_count": built_count, "built_freed": built_freed,
        "upload_count": upload_count, "upload_freed": upload_freed,
        "total_count": built_count + upload_count,
        "total_freed": built_freed + upload_freed,
    }


def expiring_soon(org, *, within_days: int = 7) -> list[dict]:
    """Reports and uploads whose window closes within ``within_days``, not
    already expired -- the settings page's early warning, on the same clocks
    the sweep will use when it actually runs.

    Each entry: ``kind`` ("built"/"upload"), ``name``, ``when`` (built or
    abandoned), ``expires_at``, ``days_left``, ``size``.
    """
    from apps.core import storage

    now = timezone.now()
    out = []

    days = built_window(org)
    if days:
        for report in _org_built_reports(org):
            expires_at = report.last_built_at + timedelta(days=days)
            left = expires_at - now
            if timedelta(0) <= left <= timedelta(days=within_days):
                out.append({
                    "kind": "built",
                    "name": report.name or report.slug,
                    "when": report.last_built_at,
                    "expires_at": expires_at,
                    "days_left": left.days,
                    "size": storage.output_bytes(report.studio, report.slug),
                })

    days = upload_window(org)
    if days:
        for upload in _org_uploads(org):
            expires_at = upload["when"] + timedelta(days=days)
            left = expires_at - now
            if timedelta(0) <= left <= timedelta(days=within_days):
                out.append({
                    "kind": "upload",
                    "name": upload["name"],
                    "when": upload["when"],
                    "expires_at": expires_at,
                    "days_left": left.days,
                    "size": upload["size"],
                })

    out.sort(key=lambda item: item["expires_at"])
    return out


def last_purge_event(org) -> dict | None:
    """The most recent time retention actually removed something for this
    organization -- the audit rows :func:`_audit_org_purge` writes -- or
    ``None`` if it never has.
    """
    from apps.core.models import AuditLog

    row = (
        AuditLog.objects.filter(
            org=org,
            action__in=("retention.built_data_purged", "retention.orphaned_data_purged"),
        )
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    return {"when": row.created_at, "freed_bytes": (row.metadata or {}).get("freed_bytes", 0)}


# ── The two sweeps, and the shared question underneath them ────────────────


def _sweep_built_data(*, delete: bool) -> tuple[int, int]:
    """``(items, bytes)`` for reports past their organization's built window."""
    from apps.core import storage
    from apps.orgs.models import Organization
    from apps.reports.models import Report

    now = timezone.now()
    count = freed = 0
    for org in Organization.objects.iterator():
        days = built_window(org)
        if not days:
            continue
        # ``__lt`` on a nullable column already excludes NULL, and that is the
        # rule rather than an accident: a report whose last successful build is
        # unknown is never expired. Unknown age must not authorise deletion.
        #
        # ``data_expired_at`` is what keeps an already-purged report out: its
        # ``last_built_at`` deliberately survives the sweep (the expired-report
        # page needs both dates), so without this exclusion every purged report
        # stays a candidate for ever and the sweep re-walks it nightly.
        expired = Report.objects.filter(
            studio__org=org,
            last_built_at__lt=now - timedelta(days=days),
            data_expired_at__isnull=True,
        ).select_related("studio", "studio__org")

        org_count = org_freed = 0
        if not delete:
            for report in expired.iterator():
                org_count += 1
                org_freed += storage.output_bytes(report.studio, report.slug)
        else:
            # Batched like every other target here. Stamping data_expired_at
            # shrinks the queryset, so this terminates without a cursor, and a
            # report whose output was already gone simply frees 0 bytes.
            while True:
                batch = list(expired[:BATCH])
                if not batch:
                    break
                for report in batch:
                    org_freed += storage.delete_output(report.studio, report.slug)
                Report.objects.filter(pk__in=[r.pk for r in batch]).update(
                    data_expired_at=now
                )
                org_count += len(batch)
            if org_count:
                _audit_org_purge(
                    org, "retention.built_data_purged",
                    reports=org_count, freed_bytes=org_freed,
                )
        count += org_count
        freed += org_freed
    return count, freed


def _sweep_orphans(*, delete: bool) -> tuple[int, int, str]:
    """``(items, bytes, basis)`` for data on the volume that no row owns."""
    from apps.core import storage
    from apps.orgs.models import Organization
    from apps.studios.models import Studio

    root = Path(settings.DATA_DIR)
    orgs = {org.slug: org for org in Organization.objects.all()}
    live = {(s.org.slug, s.slug) for s in Studio.objects.select_related("org")}
    referenced = _referenced_files()
    tree_deletes, source_deletes = _deletion_times()
    now = timezone.now()

    count = freed = 0
    bases: dict[str, int] = {}
    per_org: dict[str, list[int]] = {}

    def took(org_slug: str, size: int, basis: str) -> None:
        nonlocal count, freed
        count += 1
        freed += size
        bases[basis] = bases.get(basis, 0) + 1
        tally = per_org.setdefault(org_slug, [0, 0])
        tally[0] += 1
        tally[1] += size

    # Walking directories rather than tables is the whole point: a deleted
    # studio has no row to iterate from, and its two slugs are still legible in
    # the path (Studio.data_root is DATA_DIR/studios/<org>/<studio>).
    for org_dir in _subdirs(root / "studios"):
        org = orgs.get(org_dir.name)
        for studio_dir in _subdirs(org_dir):
            rowless = (org_dir.name, studio_dir.name) not in live
            studio = _ghost_studio(
                org if org is not None else Organization(slug=org_dir.name),
                studio_dir.name,
            )
            # A rowless studio's own deletion is what orphaned everything under
            # it, uploads included: no per-source delete was ever recorded for
            # rows that went by cascade.
            explained = tree_deletes.get(studio_dir.name) if rowless else None

            if rowless:
                when, basis = _abandoned_at(_newest_mtime(studio.output_dir), explained)
                # The ABANDONED window, not the built one. A live report's data
                # is kept until someone deletes the report, so built_window is
                # off by default -- dating this by it would mean a deleted
                # studio's tree never went at all, which is the leak this whole
                # target exists to close. What makes these bytes sweepable is
                # that nothing owns them any more, which is the same condition,
                # and the same clock, as an abandoned upload beside them.
                if _expired(now, when, upload_window(org)):
                    took(
                        org_dir.name,
                        storage.delete_output(studio) if delete
                        else storage.output_bytes(studio),
                        basis,
                    )

            for size, basis in _sweep_uploads(
                studio.datasources_dir / "files", now, upload_window(org),
                referenced, source_deletes, explained, delete=delete,
            ):
                took(org_dir.name, size, basis)

    # Organization-level shared uploads live outside every studio tree.
    for org_dir in _subdirs(root / "orgs"):
        org = orgs.get(org_dir.name)
        explained = tree_deletes.get(org_dir.name) if org is None else None
        for size, basis in _sweep_uploads(
            org_dir / "data-sources" / "files", now, upload_window(org),
            referenced, source_deletes, explained, delete=delete,
        ):
            took(org_dir.name, size, basis)

    if delete:
        for org_slug, (items, size) in per_org.items():
            _audit_org_purge(
                orgs.get(org_slug), "retention.orphaned_data_purged",
                items=items, freed_bytes=size,
            )
    return count, freed, ", ".join(f"{n} from {name}" for name, n in sorted(bases.items()))


def _sweep_uploads(
    files_dir: Path, now, days: int, referenced: set[str],
    source_deletes: dict, explained, *, delete: bool,
):
    """Yield ``(bytes, basis)`` per abandoned file under one upload root.

    Only ``files/`` is walked, not the whole data-sources directory: that is
    where the portal *puts* uploads, while everything beside it is either
    portal-managed (``config.yaml``, rewritten before every run) or a path an
    admin chose inside the studio's git checkout. Sweeping those would delete
    repository content on a housekeeping window.
    """
    if not days:
        return
    for path in _files_under(files_dir):
        if _norm(path) in referenced:
            # A live DataSource still resolves to this file. Age is irrelevant
            # and always will be — this is the invariant, not an optimisation.
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        when, basis = _abandoned_at(_as_datetime(stat.st_mtime), explained or source_deletes.get(path.stem))
        if not _expired(now, when, days):
            continue
        if delete:
            try:
                path.unlink()
            except OSError:
                continue
        yield stat.st_size, basis


def _abandoned_at(mtime, deleted_at) -> tuple[object, str]:
    """When something stopped being referenced, and on what evidence.

    The audit row that explains the orphaning is the real answer; mtime is a
    floor under it rather than a competitor, because nothing can have been
    abandoned before it was last written. Taking the later of the two only ever
    delays a deletion, which is the direction to be wrong in.
    """
    if mtime is None:
        return None, "mtime"  # nothing there to age
    if deleted_at is None:
        return mtime, "mtime"
    return max(mtime, deleted_at), "audit"


def _expired(now, when, days: int) -> bool:
    return bool(days) and when is not None and when < now - timedelta(days=days)


def _referenced_files() -> set[str]:
    """Every path a ``DataSource`` row currently resolves to.

    Every row with a path, not only the uploadable ones: a source that reads a
    file it may not overwrite still references it, and "no row resolves to it"
    is the entire definition of abandoned.
    """
    from apps.datasources.materialize import stored_file
    from apps.datasources.models import DataSource

    rows = DataSource.objects.select_related("org", "studio", "studio__org")
    referenced = set()
    for ds in rows.iterator():
        path = stored_file(ds)
        if path is not None:
            referenced.add(_norm(path))
    return referenced


def _deletion_times() -> tuple[dict, dict]:
    """The audit rows that explain an orphan: ``({slug: when}, {name: when})``.

    ``org.delete`` is queried beside ``studio.delete`` although nothing emits
    it today — there is no org-delete path in the portal yet. When one lands,
    this sweep must date an orphaned org tree from it rather than quietly
    falling back to mtime, and one string in an ``IN`` clause is a cheaper way
    to guarantee that than remembering to come back here.

    Keyed on the slug or name alone, so two organizations that both deleted a
    studio called "sales" share an entry. That is deliberately left: the result
    only ever feeds a ``max()`` against the file's own mtime, so a wrong match
    can delay a deletion and never bring one forward, and the alternative is
    keying on an ``org`` column that is SET_NULL precisely when the
    organization is the thing that went.
    """
    from apps.core.models import AuditLog

    trees: dict[str, object] = {}
    sources: dict[str, object] = {}
    rows = AuditLog.objects.filter(
        action__in=("studio.delete", "org.delete", "datasource.delete")
    ).values_list("action", "metadata", "created_at")
    for action, metadata, when in rows.iterator():
        meta = metadata or {}
        if action == "datasource.delete":
            key, target = str(meta.get("name") or ""), sources
        else:
            key, target = str(meta.get("slug") or ""), trees
        if key and when >= target.get(key, when):
            target[key] = when
    return trees, sources


def _ghost_studio(org, studio_slug: str):
    """A ``Studio``-shaped stand-in for a studio whose row is gone.

    Every path this sweep needs — the output directory, the read cache, the
    bucket prefix — is arithmetic on two slugs, and both are still readable off
    the directory being stood in. Reconstructing the object keeps
    ``apps.studios.models.Studio`` the single source of truth for the layout
    instead of a second copy of it living here. Nothing is ever saved.
    """
    from apps.studios.models import Studio

    return Studio(org=org, slug=studio_slug)


def _subdirs(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []


def _files_under(path: Path) -> list[Path]:
    try:
        return [p for p in path.rglob("*") if p.is_file()]
    except OSError:
        return []


def _as_datetime(stamp: float):
    return timezone.datetime.fromtimestamp(stamp, tz=timezone.get_current_timezone())


def _newest_mtime(path: Path):
    """When anything in a tree was last written, or ``None`` if it holds nothing."""
    stamps = []
    for entry in _files_under(path):
        try:
            stamps.append(entry.stat().st_mtime)
        except OSError:
            continue
    return _as_datetime(max(stamps)) if stamps else None


def _norm(path: Path) -> str:
    """Comparable form of a path: resolved, and case-folded where the
    filesystem is. Both sides of the comparison go through this — a stored
    data-source path and a walked one reach it by different routes.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return os.path.normcase(str(resolved))


def _log_sweep(label: str, count: int, freed: int, dry_run: bool, basis: str = "") -> None:
    """The byte figure the ``(label, count)`` target contract has no room for.

    Widening that tuple would touch every target and every reader of one to
    serve two. Operators reach the number through :func:`reclaimable_bytes`
    (`doctor`, /system); this line puts it in the cleanup run's own log beside
    what was removed, which is where a dry run is read.
    """
    if not count:
        return
    detail = f", dated from {basis}" if basis else ""
    logger.info(
        f"retention: {'would free' if dry_run else 'freed'} "
        f"{freed / (1024 ** 2):.1f} MB of {label} ({count} item(s)){detail}"
    )


def _audit_org_purge(org, action: str, **metadata) -> None:
    """One row per organization whose data was actually removed.

    cleanup.py's instance-wide ``retention.purge`` row has ``org=None``, so an
    org admin reading their own trail would never learn that their data was
    deleted. This is the row they can see. Skipped when the organization row
    itself is gone — there is nobody left to tell.
    """
    from apps.core.audit import audit_system

    if org is not None:
        audit_system(action, org=org, **metadata)


#: Run in this order: rows first (cheap, frees the most), disk last.
#:
#: The two data windows go last of all: they are the most expensive (directory
#: walks, and object-store listings on the remote backend) and the least
#: urgent, since disk is what they free rather than table space.
TARGETS = (
    purge_runs,
    blank_run_output,
    purge_audit,
    purge_assistant_sessions,
    purge_invitations,
    purge_sessions,
    purge_user_sessions,
    purge_report_view_events,
    # The three dormancy stages, listed in the order they happen so a cleanup
    # run reads as the policy does. Off unless RETENTION_DORMANT_DAYS is set.
    warn_dormant_accounts,
    disable_dormant_accounts,
    erase_dormant_accounts,
    purge_tmp_runs,
    purge_built_data,
    purge_orphaned_data,
)
