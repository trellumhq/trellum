"""Alerts and scheduled email delivery.

Two independent things feed this module (see ``apps.reports.models``):

- Build-status *transition* mail (success -> failure, failure -> success).
  Every studio member with role ``developer`` or ``admin`` (see
  ``apps.core.roles``) hears about a report of theirs breaking automatically
  — see :func:`alert_recipients`. There is no per-report subscription, no
  toggle, no opt-out: this is operational duty mail, not something anyone
  signs up for. Driven by :func:`run_finished`, called from
  ``apps.runner.executor`` after every build. Broken-transition mail is
  buffered and coalesced across a studio — see :func:`flush_broken_buffers`
  — recovered mail is not.
- ``EmailSchedule`` — a personal cadence ("send me this report every
  weekday at 8am"). Driven by :func:`send_scheduled`, called from an
  APScheduler job the coordinator keeps in sync with the schedule rows (see
  ``apps.runner.management.commands.runworker._SchedulerManager``). This is
  the only subscription in this module, and the only mail that carries an
  unsubscribe link.

Nothing here may ever escape as an exception into its caller: a notification
bug must never break a build or wedge the scheduler, so every public
function catches broadly, logs, and returns.
"""
from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as _dt_timezone

from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.safestring import mark_safe

from apps.core.instance import base_url, instance_name
from apps.core.mail import resolve_delivery_connection

logger = logging.getLogger(__name__)

#: Namespaces the signed token so it can't be replayed against some other
#: use of django.core.signing elsewhere in the app.
UNSUBSCRIBE_SALT = "apps.reports.notify.unsubscribe"

#: Statuses that count as "broken" for alert purposes. STOPPED (a user
#: deliberately cancelling a run) is not one of them — that is not a signal
#: anything is wrong with the report.
FAILURE_STATUSES = ("error", "timeout", "oom_killed")

#: How long a broken-build transition waits in the buffer (see
#: BrokenReportPending / flush_broken_buffers) before it is mailed out, so
#: several reports in one studio breaking together (a git-sync outage, a
#: shared data source dying) coalesce into one summary mail per recipient
#: instead of one mail per report. Flushing rides the coordinator's existing
#: ~60s scheduler-refresh cadence (see runworker._SchedulerManager.refresh),
#: so worst-case single-failure latency is this window plus one refresh
#: cycle — comfortably under the ~3 minute budget product asked for.
COALESCE_WINDOW_SECONDS = 90


# ── alert-audience resolution ───────────────────────────────────────────────

def alert_recipients(report, kind: str) -> list:  # noqa: ARG001 - kind kept for call-site clarity
    """Who should hear about ``report``: every user with *effective*
    developer-or-admin access to its studio (see
    ``apps.core.permissions.effective_roles`` / ``bulk_role_for_studio``) —
    a direct ``StudioMembership`` row, an org admin, or a permission-group
    grant/default role, same as everywhere else "does this user currently
    have access to this studio" is decided (see
    ``apps.reports.notify.intersect_with_studio``). Deactivated users never
    count, however they'd otherwise qualify. Viewers hear nothing. There is
    no per-report or per-user override of this — it's operational duty
    mail, delivered with zero configuration.

    ``kind`` is accepted but not consulted: it used to select between
    audiences under the old watch-level system, and the one live caller
    (failure/recovery transitions) always resolves the same audience, so the
    parameter is kept only so call sites stay self-describing.
    """
    from apps.core import roles
    from apps.core.permissions import bulk_role_for_studio
    from apps.orgs.models import OrgMembership

    studio = report.studio
    org = studio.org
    members = [
        om.user
        for om in OrgMembership.objects.filter(org=org, user__is_active=True).select_related("user")
    ]
    role_by_id = bulk_role_for_studio(members, studio)
    recipients = [
        user for user in members if roles.at_least(role_by_id.get(user.pk), roles.DEVELOPER)
    ]
    if report.audience == report.AUDIENCE_PRIVATE and recipients:
        from apps.core.report_access import bulk_can_view_report

        allowed = bulk_can_view_report(recipients, report)
        recipients = [user for user in recipients if allowed.get(user.pk, False)]
    return sorted(recipients, key=lambda u: u.email)


# ── schedule-audience resolution ────────────────────────────────────────────
#
# A schedule's recipient list is not the frozen ``recipients`` M2M any more:
# it can also carry role chips ("admins"/"developers"/"everyone") and
# permission-group chips (apps.orgs.models.PermissionGroup). Both are
# references, expanded fresh at every send, so a joiner starts hearing from
# the schedule and a leaver stops without anyone editing it — see
# :func:`resolve_recipients`, the one place that expands them.

#: Human labels for the role chips that aren't "everyone" (which needs the
#: studio's name interpolated, so it isn't a static label) -- shared with
#: apps.reports.views._recipient_summary so the drawer/center summary string
#: and any other consumer describe the same audience the same way.
RECIPIENT_ROLE_LABELS = {"admins": "All admins", "developers": "All developers"}


def intersect_with_studio(users, studio) -> list:
    """Users narrowed to those with any current effective access to
    ``studio`` (org admin, direct membership, or a permission-group grant/
    default role -- see ``apps.core.permissions.effective_roles``) who are
    also active. This is the hard access rule every dynamically-resolved
    recipient must pass: a permission-group member with no access to this
    particular studio never receives the mail, and neither does an
    individually-picked user who has since left it.

    Computed with ``bulk_role_for_studio`` -- a handful of queries for the
    whole ``users`` iterable -- rather than calling ``effective_roles`` once
    per user: this is called with a schedule's whole picked-recipients list
    or a whole permission group's membership (``api_studio_members``), so a
    per-user query fan-out here scales with a studio's membership, not with
    a fixed request cost."""
    from apps.core.permissions import bulk_role_for_studio

    users = list(users)
    if not users:
        return []
    role_by_id = bulk_role_for_studio(users, studio)
    return [u for u in users if u.is_active and role_by_id.get(u.pk) is not None]


def user_in_schedule_audience(user, schedule, *, user_role: str | None, user_group_ids) -> bool:
    """Whether ``user`` is in ``schedule``'s resolved audience, using
    per-request-precomputed membership instead of the multi-query fan-out
    :func:`resolve_recipients` does to build the *entire* audience -- for
    call sites that only need a yes/no answer for one already-known user
    across potentially many schedules (``apps.reports.views
    .api_my_subscriptions``, the drawer's own schedule-list GET).

    ``user_role`` is that user's own current ``StudioMembership.role`` for
    the schedule's studio (``None`` if they have no explicit row) -- role
    chips ("admins"/"developers") match against this, same as
    :func:`resolve_recipients`. ``user_group_ids`` is the set of this org's
    ``PermissionGroup`` ids the user belongs to.

    Does *not* itself re-check whether ``user`` currently has access to the
    studio at all (see :func:`intersect_with_studio`) -- every caller today
    already knows that going in (the request passed a studio-role guard for
    this exact user), so re-deriving it per schedule would just be wasted
    work. A caller resolving membership for someone other than the current
    request's own user must apply that check itself.

    ``schedule.recipients``/``recipient_groups`` must already be
    prefetched by the caller (as every current caller does) -- this makes
    zero additional queries per schedule.
    """
    from apps.core import roles as role_consts

    if any(u.pk == user.pk for u in schedule.recipients.all()):
        return True
    role_list = schedule.recipient_roles or []
    if role_list:
        if "everyone" in role_list:
            return True
        if user_role == role_consts.ADMIN and "admins" in role_list:
            return True
        if user_role == role_consts.DEVELOPER and "developers" in role_list:
            return True
    if user_group_ids and any(g.pk in user_group_ids for g in schedule.recipient_groups.all()):
        return True
    return False


def resolve_recipients(schedule) -> list:
    """Everyone ``schedule`` should mail right now: the union of its
    individually-picked ``recipients``, its role chips (expanded from the
    studio's *current* ``StudioMembership`` rows -- the same audience
    :func:`alert_recipients` draws its developer/admin audience from, so
    "All admins"/"All developers" describes configured studio roles, not
    every implicit path to access) and its permission-group chips (expanded
    from ``PermissionGroupMembership``, org-scoped) -- deduped and always
    intersected with the studio's current membership (see
    :func:`intersect_with_studio`).

    Called by :func:`_send_scheduled` for the real send, and by
    ``apps.reports.views.api_my_subscriptions``/``_recipient_summary`` so a
    user reached only through a role or group chip still shows up as
    subscribed."""
    from apps.core import roles as role_consts
    from apps.orgs.models import PermissionGroupMembership
    from apps.studios.models import StudioMembership

    studio = schedule.report.studio

    union: dict[int, object] = {u.pk: u for u in schedule.recipients.all()}

    role_list = schedule.recipient_roles or []
    if role_list:
        everyone = "everyone" in role_list
        wanted = set()
        if "admins" in role_list:
            wanted.add(role_consts.ADMIN)
        if "developers" in role_list:
            wanted.add(role_consts.DEVELOPER)
        for membership in StudioMembership.objects.filter(studio=studio).select_related("user"):
            if everyone or membership.role in wanted:
                union[membership.user_id] = membership.user

    group_ids = list(schedule.recipient_groups.values_list("pk", flat=True))
    if group_ids:
        for membership in PermissionGroupMembership.objects.filter(
            group_id__in=group_ids
        ).select_related("user"):
            union[membership.user_id] = membership.user

    if not union:
        return []
    recipients = intersect_with_studio(union.values(), studio)
    from apps.core.report_access import bulk_can_view_report

    allowed = bulk_can_view_report(recipients, schedule.report)
    return sorted((u for u in recipients if allowed.get(u.pk, False)), key=lambda u: u.email)


# ── unsubscribe tokens ──────────────────────────────────────────────────────

def sign_unsubscribe_token(kind: str, object_id: int, user_id: int) -> str:
    """A signed, tamper-proof token identifying one (subscription kind,
    object, user) triple. ``kind`` is "schedule" or "alert"."""
    return signing.dumps(
        {"kind": kind, "id": object_id, "uid": user_id}, salt=UNSUBSCRIBE_SALT
    )


def read_unsubscribe_token(token: str, max_age: int = 60 * 60 * 24 * 90) -> dict | None:
    """Decode a token minted by :func:`sign_unsubscribe_token`. None if it is
    missing, malformed, or older than ``max_age`` seconds (default 90 days —
    an unsubscribe link should stay usable for as long as we might mail
    someone, but not forever)."""
    try:
        return signing.loads(token, salt=UNSUBSCRIBE_SALT, max_age=max_age)
    except signing.BadSignature:
        return None


def unsubscribe_url(kind: str, object_id: int, user_id: int) -> str:
    token = sign_unsubscribe_token(kind, object_id, user_id)
    return f"{base_url()}/notify/unsubscribe/{token}"


def _report_url(report) -> str:
    studio = report.studio
    return f"{base_url()}/s/{studio.org.slug}/{studio.slug}/r/{report.slug}/"


# ── mail-body timestamp/cadence formatting ──────────────────────────────────

def _parse_meta_dt(value) -> datetime | None:
    """Parse a ``_meta.json`` timestamp (``last_run``, an ISO-8601 string) --
    None if missing or unparseable, so a malformed/absent value degrades to
    "no built_at" in the mail rather than raising."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _format_dt(dt: datetime | None, tz_name: str | None = None) -> str:
    """``dt`` as ``19 Aug 2026, 07:58``, converted to ``tz_name`` (an IANA
    zone) when given and valid, else UTC (the server's own TIME_ZONE) --
    "" when ``dt`` is None so a template's ``{% if built_at %}`` cleanly
    omits the whole meta-line segment."""
    if dt is None:
        return ""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, _dt_timezone.utc)
    zone = _dt_timezone.utc
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            zone = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 - a bad/unknown zone falls back to UTC
            zone = _dt_timezone.utc
    dt = dt.astimezone(zone)
    return f"{dt.day} {dt:%b %Y, %H:%M}"


def _cadence_phrase(schedule) -> str:
    """Human cadence phrase for a delivery mail's meta line, e.g. ``weekly,
    Mondays 08:00 (Europe/Amsterdam)``, ``daily at 07:30 (UTC)``, ``monthly
    on day 3 at 08:00 (Europe/Amsterdam)``. Distinct from
    ``apps.reports.views._schedule_cadence_words`` (the deliveries-list
    label) -- this one is prose meant to sit inline in a sentence, not a
    standalone label."""
    from apps.reports.models import EmailSchedule

    when = f"{schedule.send_hour:02d}:{schedule.send_minute:02d}"
    tz = schedule.timezone or "UTC"
    if schedule.freq == EmailSchedule.FREQ_WEEKLY:
        import calendar

        day = calendar.day_name[schedule.weekday] if 0 <= schedule.weekday <= 6 else ""
        return f"weekly, {day}s {when} ({tz})"
    if schedule.freq == EmailSchedule.FREQ_MONTHLY:
        return f"monthly on day {schedule.month_day} at {when} ({tz})"
    if schedule.freq == EmailSchedule.FREQ_WEEKDAYS:
        return f"weekdays at {when} ({tz})"
    return f"daily at {when} ({tz})"


# ── build-status alerts ──────────────────────────────────────────────────────

def run_finished(run) -> None:
    """Called after every build completes, whatever its outcome.

    Mails this report's alert audience (see :func:`alert_recipients`) only
    on a state *transition* — never on a repeated failure or a repeated
    success — so a report that has been down for a week doesn't re-alert
    its watchers on every retry.
    """
    try:
        _run_finished(run)
    except Exception:  # noqa: BLE001 - a notification bug must never break a build
        logger.exception("notify.run_finished failed for run %s", getattr(run, "pk", None))


def _run_finished(run) -> None:
    from apps.runner.models import Run

    report = run.report
    # The previous *completed* build for this report. Active statuses are
    # excluded defensively — enqueue() already refuses a second concurrent
    # run per report, but a stale in-flight row must never be read as "the
    # last known state".
    previous = (
        Run.objects.filter(report=report)
        .exclude(pk=run.pk)
        .exclude(status__in=Run.ACTIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if previous is None:
        return  # first build this report has ever finished: no transition yet

    # STOPPED counts as neither a failure nor a success here: a user
    # deliberately cancelling a build is not a signal anything is broken, and
    # cancelling out of a prior failure is not a "recovery" either — the
    # report still hasn't proven it builds.
    was_broken = previous.status in FAILURE_STATUSES
    is_broken = run.status in FAILURE_STATUSES
    is_ok = run.status == Run.SUCCESS
    if is_broken == was_broken or (is_ok and not was_broken):
        return  # no state change worth mailing about

    if is_broken:
        # Buffered, not sent immediately — see flush_broken_buffers, which
        # the coordinator's periodic scheduler sync calls. This lets several
        # reports in the same studio breaking together coalesce into one
        # summary mail instead of one per report.
        _buffer_broken(report, run, previous)
    elif is_ok:
        # A recovery inside the coalescing window must drop any of this
        # report's still-pending "broken" rows -- otherwise the next flush
        # mails a stale Broken notice for a report that has since recovered
        # (the recipient would see Recovered followed by a confusing Broken
        # for the same incident). Recovered mail itself is never buffered
        # (see the module docstring), so this is safe to do unconditionally
        # before sending it.
        from apps.reports.models import BrokenReportPending

        BrokenReportPending.objects.filter(report=report).delete()
        recipients = alert_recipients(report, "failure")
        if recipients:
            _send_recovered(report, recipients)


def _buffer_broken(report, run, previous) -> None:
    from apps.reports.models import BrokenReportPending

    BrokenReportPending.objects.create(report=report, run=run, previous_run=previous)


def _error_excerpt(report, run) -> str:
    """Best-effort error text for a broken-build mail. _meta.json (the
    framework's own record) wins when readable; the Run row's captured
    stderr is the fallback so a disk hiccup never leaves the mail empty."""
    excerpt = (run.stderr_tail or "").strip()[-500:]
    try:
        from trellum.meta import read_meta

        meta = read_meta(str(report.studio.output_dir / run.slug))
        excerpt = (meta.get("last_error") or excerpt).strip()
    except Exception:  # noqa: BLE001 - disk/format issues must not block the mail
        pass
    return excerpt or "(no error detail captured)"


def _days_since_last_failure(report, exclude_pks) -> int | None:
    """How long ago this report last failed, excluding the failure(s) that
    prompted this mail — i.e. "this isn't the first time", for context.
    None when there is no earlier failure to report."""
    from apps.runner.models import Run

    prior = (
        Run.objects.filter(report=report, status__in=FAILURE_STATUSES)
        .exclude(pk__in=exclude_pks)
        .order_by("-created_at")
        .first()
    )
    if prior is None:
        return None
    stamp = prior.finished_at or prior.created_at
    if not stamp:
        return None
    return max((timezone.now() - stamp).days, 0)


def _send_broken(report, run, recipients, exclude_pks) -> None:
    error_excerpt = _error_excerpt(report, run)
    ctx = {
        "report": report,
        "report_url": _report_url(report),
        "instance_name": instance_name(),
        "error_excerpt": error_excerpt,
        "days_since_last_failure": _days_since_last_failure(report, exclude_pks),
        "failed_at": _format_dt(run.finished_at or run.created_at),
        "accent": "#dc2626",
        "status_color": "#dc2626",
        "status_label": mark_safe("&#9888; BROKEN"),
        "duty_footer": True,
        "preview_text": error_excerpt.splitlines()[0][:140] if error_excerpt else "",
    }
    subject = f"⚠ Broken · {report.name or report.slug}"
    for user in recipients:
        _send_alert_mail(subject, "email/broken", ctx, report, user)


def _send_recovered(report, recipients) -> None:
    ctx = {
        "report": report,
        "report_url": _report_url(report),
        "instance_name": instance_name(),
        "accent": "#16a34a",
        "status_color": "#16a34a",
        "status_label": mark_safe("&#10003; RECOVERED"),
        "duty_footer": True,
        "preview_text": "Building again after the earlier failure.",
    }
    subject = f"✓ Recovered · {report.name or report.slug}"
    for user in recipients:
        _send_alert_mail(subject, "email/recovered", ctx, report, user)


# ── broken-build coalescing ──────────────────────────────────────────────

def flush_broken_buffers(*, force: bool = False) -> None:
    """Deliver any buffered broken-build transitions whose coalescing
    window has elapsed (see ``BrokenReportPending`` and
    ``COALESCE_WINDOW_SECONDS``).

    Call this on every pass of the coordinator's periodic scheduler sync
    (``runworker._SchedulerManager.refresh``, already on a ~60s cadence) —
    this module owns no timer of its own, since a runner and the coordinator
    can be different processes and only the coordinator's refresh loop is
    guaranteed to be running exactly once. ``force=True`` (tests only)
    ignores the window and flushes every studio with a pending row right
    now, so tests don't need to fake the clock.

    Never raises — mirrors every other public entry point in this module.
    """
    try:
        _flush_broken_buffers(force=force)
    except Exception:  # noqa: BLE001 - must never wedge the scheduler
        logger.exception("notify.flush_broken_buffers failed")


def _flush_broken_buffers(*, force: bool) -> None:
    from apps.reports.models import BrokenReportPending

    studio_ids = (
        BrokenReportPending.objects.order_by()
        .values_list("report__studio_id", flat=True)
        .distinct()
    )
    for studio_id in studio_ids:
        pending = list(
            BrokenReportPending.objects.filter(report__studio_id=studio_id)
            .select_related("report", "report__studio", "report__studio__org", "run", "previous_run")
            .order_by("created_at")
        )
        if not pending:
            continue
        oldest_age = (timezone.now() - pending[0].created_at).total_seconds()
        if not force and oldest_age < COALESCE_WINDOW_SECONDS:
            continue
        _flush_studio_buffer(pending)


def _flush_studio_buffer(pending) -> None:
    from apps.reports.models import BrokenReportPending

    # One row per broken-build TRANSITION, not per distinct report -- a
    # report can rack up more than one within the same coalescing window
    # (e.g. break, get cancelled by a user, then break again before the
    # window elapses: a STOPPED run counts as neither broken nor recovered,
    # see _run_finished, so the next failure is a fresh transition). Dedupe
    # to one row per report -- keeping the newest, since `pending` arrives
    # ordered oldest-first (see _flush_broken_buffers) and a later
    # duplicate simply overwrites the earlier one in the dict below -- so a
    # report breaking twice still takes the single-report path instead of
    # wrongly tripping the multi-report summary on itself.
    by_report: dict[int, object] = {}
    for item in pending:
        by_report[item.report_id] = item
    deduped = list(by_report.values())

    if len(deduped) == 1:
        item = deduped[0]
        recipients = alert_recipients(item.report, "failure")
        if recipients:
            exclude_pks = {item.run_id}
            if item.previous_run_id:
                exclude_pks.add(item.previous_run_id)
            _send_broken(item.report, item.run, recipients, exclude_pks)
    else:
        _send_broken_summary(deduped)
    BrokenReportPending.objects.filter(pk__in=[p.pk for p in pending]).delete()


def _send_broken_summary(pending) -> None:
    """One mail per recipient, listing every distinct report of theirs that
    broke in this coalescing window — the replacement for N separate broken
    mails when several reports in one studio break together. ``pending`` is
    expected to already carry at most one row per report (see
    _flush_studio_buffer, its only caller).

    Each recipient's subject counts only *their own* reports, never a
    studio-wide total -- a developer who only has access to one of several
    reports that broke together must see "1 report broken", not the count
    of everything that broke studio-wide."""
    studio = pending[0].report.studio

    reports_meta: dict[int, dict] = {}
    recipients_by_report: dict[int, list] = {}
    for item in pending:
        error_excerpt = _error_excerpt(item.report, item.run)
        reports_meta[item.report_id] = {
            "report": item.report,
            "report_url": _report_url(item.report),
            "error_excerpt": error_excerpt,
            "error_first_line": error_excerpt.splitlines()[0] if error_excerpt else "",
        }
        recipients_by_report[item.report_id] = alert_recipients(item.report, "failure")

    per_user: dict[int, list[int]] = {}
    users_by_id: dict[int, object] = {}
    for report_pk, recipients in recipients_by_report.items():
        for user in recipients:
            per_user.setdefault(user.pk, []).append(report_pk)
            users_by_id[user.pk] = user

    for user_id, report_pks in per_user.items():
        user = users_by_id[user_id]
        items = [reports_meta[pk] for pk in report_pks]
        n = len(report_pks)
        subject = f"⚠ {n} broken · {studio.name}"
        preview_text = ", ".join(item["report"].name or item["report"].slug for item in items)
        ctx = {
            "studio": studio,
            "instance_name": instance_name(),
            "items": items,
            "accent": "#dc2626",
            "status_color": "#dc2626",
            "status_label": mark_safe(f"&#9888; {n} BROKEN"),
            "duty_footer": True,
            "preview_text": preview_text,
        }
        try:
            _send_simple(subject, "email/broken_summary", ctx, user)
        except Exception:  # noqa: BLE001
            logger.exception(
                "notify: failed to send broken_summary for studio %s", studio.pk
            )


def _send_alert_mail(subject, template_base, ctx, report, user) -> None:
    """One alert recipient's mail. Guarded per-recipient so a single bad
    address can't stop the rest of the list from being notified. No
    unsubscribe link — this is operational duty mail to a studio's
    developers and admins, not a subscription (see :func:`alert_recipients`)."""
    try:
        _send_simple(subject, template_base, ctx, user)
    except Exception:  # noqa: BLE001
        logger.exception(
            "notify: failed to send %s for report %s", template_base, report.pk
        )


def _send_simple(subject: str, template_base: str, ctx: dict, user) -> None:
    """Text + HTML mail with no attachments, addressed to one user."""
    text_body = render_to_string(f"{template_base}.txt", ctx)
    html_body = render_to_string(f"{template_base}.html", ctx)
    connection = resolve_delivery_connection()
    msg = EmailMultiAlternatives(
        subject=subject, body=text_body, from_email=connection.from_email,
        to=[user.email], connection=connection,
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


# ── agentic alerts (apps.alerts) ─────────────────────────────────────────────

def send_alert(rule, run) -> list[str]:
    """Mail one alert decision to the rule's audience -- ``resolve_recipients``
    reads an ``AlertRule`` exactly as it reads an ``EmailSchedule``, studio
    intersection included. Returns the addresses actually sent; never raises."""
    try:
        recipients = resolve_recipients(rule)
    except Exception:  # noqa: BLE001
        logger.exception("notify.send_alert: recipients failed for rule %s", rule.pk)
        return []
    report = rule.report
    ctx = {
        "report": report,
        "rule": rule,
        "run": run,
        "evidence": list((run.evidence or {}).get("cited") or []),
        "report_url": _report_url(report),
        # Seeds a conversation with this run (apps.assistant.views.from_alert).
        "assistant_url": (
            f"{base_url()}/s/{report.studio.org.slug}/{report.studio.slug}"
            f"/assistant/from-alert/{run.pk}"
        ),
        "instance_name": instance_name(),
        "accent": "#d97706",
        "status_color": "#d97706",
        "status_label": mark_safe("&#9888; ALERT"),
        "preview_text": run.title,
    }
    subject = f"⚠ {run.title or rule.name} · {report.name or report.slug}"
    sent: list[str] = []
    for user in recipients:
        try:
            _send_simple(subject, "email/alert", ctx, user)
            sent.append(user.email)
        except Exception:  # noqa: BLE001
            logger.exception("notify: failed to send alert for rule %s", rule.pk)
    return sent


# ── scheduled delivery ───────────────────────────────────────────────────────

def send_scheduled(schedule_id) -> None:
    """APScheduler job target: deliver one EmailSchedule's cadence."""
    try:
        _send_scheduled(schedule_id)
    except Exception:  # noqa: BLE001 - must never wedge the scheduler thread
        logger.exception("notify.send_scheduled failed for schedule %s", schedule_id)


def _send_scheduled(schedule_id) -> None:
    from apps.reports.models import EmailSchedule

    schedule = (
        EmailSchedule.objects.filter(pk=schedule_id, enabled=True)
        .select_related("created_by", "report", "report__studio", "report__studio__org")
        .first()
    )
    if schedule is None:
        return  # deleted/disabled between the job firing and now
    from apps.core.report_access import can_view_report

    if not schedule.created_by.is_active or not can_view_report(schedule.created_by, schedule.report):
        return
    recipients = resolve_recipients(schedule)
    if not recipients:
        return
    _deliver(schedule.report, recipients, attach_pdf=schedule.attach_pdf, schedule=schedule)
    EmailSchedule.objects.filter(pk=schedule.pk).update(last_sent_at=timezone.now())


def send_sample(schedule_or_report, user) -> None:
    """Immediate one-off delivery of one report to one user — the "send me a
    sample now" button. Accepts either an EmailSchedule (its attach_pdf
    setting is honoured) or a bare Report (sent as a plain snapshot)."""
    try:
        _send_sample(schedule_or_report, user)
    except Exception:  # noqa: BLE001
        logger.exception("notify.send_sample failed for object %s", getattr(schedule_or_report, "pk", None))


def send_sample_or_raise(schedule_or_report, user) -> None:
    """Like :func:`send_sample`, but propagates a failure instead of
    swallowing it. For a caller that wants to log the failure itself with
    its own context (e.g. ``apps.reports.views``' sample-now endpoints,
    which run this off the request thread and log with the user/report/
    schedule identifiers the request already has, rather than this
    module's generic ``%r``)."""
    _send_sample(schedule_or_report, user)


def _send_sample(schedule_or_report, user) -> None:
    from apps.reports.models import EmailSchedule, Report

    if isinstance(schedule_or_report, EmailSchedule):
        report = schedule_or_report.report
        attach_pdf = schedule_or_report.attach_pdf
    elif isinstance(schedule_or_report, Report):
        report = schedule_or_report
        attach_pdf = False
    else:  # pragma: no cover - defensive; callers pass one of the above
        raise TypeError(f"expected EmailSchedule or Report, got {type(schedule_or_report)!r}")
    from apps.core.report_access import can_view_report

    if not user.is_active or not can_view_report(user, report):
        return
    _deliver(report, [user], attach_pdf=attach_pdf, schedule=None, sample=True)


def _deliver(report, recipients, *, attach_pdf: bool, schedule=None, sample: bool = False) -> None:
    """Shared delivery path for both the cadence job and the sample button."""
    from trellum.meta import read_meta

    output_dir = report.studio.output_dir / report.slug
    try:
        meta = read_meta(str(output_dir))
    except Exception:  # noqa: BLE001
        meta = {}

    if meta.get("last_status") != "success" or not output_dir.is_dir():
        _send_not_sent(report, recipients, meta, schedule=schedule)
        return

    render_result = _try_render(output_dir)
    for user in recipients:
        try:
            _send_delivery_mail(
                report, user, render_result, attach_pdf=attach_pdf, schedule=schedule, sample=sample,
                meta=meta,
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify: failed to send delivery mail for report %s", report.pk)


def _try_render(output_dir):
    """Render a preview image/pdf for the delivery mail. The rendering module
    is owned by another workstream; its absence (or any failure inside it) is
    an expected, non-fatal path — the mail just falls back to text+HTML."""
    try:
        from apps.reports.snapshot import render_report
    except ImportError:
        return None
    try:
        return render_report(output_dir)
    except Exception:  # noqa: BLE001
        logger.exception("notify: render_report failed for %s", output_dir)
        return None


class _DeliveryEmail(EmailMultiAlternatives):
    """An ``EmailMultiAlternatives`` whose optional inline CID image nests
    inside its own ``multipart/related`` wrapper around the text/html
    alternative part, with any *other* attachment (the PDF) staying a
    sibling of that whole blob under the outer ``multipart/mixed``:

        mixed
        +-- related          (only when there's an inline image)
        |   +-- alternative  (text, html)
        |   +-- image (cid)
        +-- pdf

    Django's ``EmailMultiAlternatives`` only exposes one ``mixed_subtype``
    knob, which wraps the alternative part *and every attachment* in the
    same container -- setting it to "related" (needed at all for an inline
    CID image to render) used to sweep a PDF attachment into
    ``multipart/related`` right alongside it too. Several mail clients
    (Outlook among them) don't treat an attachment nested that way as a
    real, downloadable attachment and simply never show it. Passing the
    inline image via ``inline_image=`` instead of ``.attach()`` keeps it
    out of ``self.attachments`` entirely, so ``mixed_subtype`` never needs
    touching and a PDF passed to ``.attach()`` lands as a plain sibling
    under the (default) ``multipart/mixed`` root.
    """

    def __init__(self, *args, inline_image=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._inline_image = inline_image

    def _create_message(self, msg):
        from django.conf import settings as dj_settings
        from django.core.mail.message import SafeMIMEMultipart

        body = self._create_alternatives(msg)
        if self._inline_image is not None:
            related = SafeMIMEMultipart(
                _subtype="related", encoding=self.encoding or dj_settings.DEFAULT_CHARSET
            )
            related.attach(body)
            related.attach(self._inline_image)
            body = related
        return self._create_attachments(body)


def _send_delivery_mail(report, user, render_result, *, attach_pdf, schedule, sample, meta=None) -> None:
    connection = resolve_delivery_connection()
    meta = meta or {}
    png = getattr(render_result, "png", None) if render_result else None
    pdf = getattr(render_result, "pdf", None) if render_result else None
    label = "Sample" if sample else "Fresh"
    subject = f"{label} · {report.name or report.slug}"
    studio_name = report.studio.name
    cadence = _cadence_phrase(schedule) if schedule is not None else ""
    built_at = _format_dt(
        _parse_meta_dt(meta.get("last_run")), schedule.timezone if schedule is not None else None
    )
    preview_text = f"{studio_name} · {cadence}" if cadence else studio_name
    ctx = {
        "report": report,
        "report_url": _report_url(report),
        "instance_name": instance_name(),
        "has_image": bool(png),
        "snapshot_unavailable": not png,
        "snapshot_attached": bool(png) and connection.snapshot.screenshot_mode == "attachment",
        "sample": sample,
        "cadence": cadence,
        "built_at": built_at,
        "accent": "#0D9488",
        "status_color": "#0D9488",
        "status_label": "SAMPLE" if sample else "FRESH",
        "preview_text": preview_text,
    }
    if schedule is not None:
        ctx["unsubscribe_url"] = unsubscribe_url("schedule", schedule.pk, user.pk)

    text_body = render_to_string("email/delivery.txt", ctx)
    html_body = render_to_string("email/delivery.html", ctx)

    inline_image = None
    if png:
        from email.mime.image import MIMEImage

        # _subtype is explicit rather than sniffed: the render_report
        # contract promises PNG bytes, and imghdr-based guessing raises on
        # anything it doesn't recognize (seen with test fixture bytes).
        inline_image = MIMEImage(png, _subtype="png")
        inline_image.add_header("Content-ID", "<report-snapshot>")
        inline_image.add_header("Content-Disposition", "inline", filename="snapshot.png")

    msg = _DeliveryEmail(
        subject=subject,
        body=text_body,
        from_email=connection.from_email,
        to=[user.email],
        inline_image=inline_image,
        connection=connection,
    )
    msg.attach_alternative(html_body, "text/html")
    if attach_pdf and pdf:
        msg.attach(f"{report.slug}.pdf", pdf, "application/pdf")
    msg.send(fail_silently=False)


def _send_not_sent(report, recipients, meta, schedule=None) -> None:
    """The report's last build failed or never ran: say so plainly instead
    of silently mailing stale numbers (or nothing at all)."""
    subject = f"Not sent · {report.name or report.slug}"
    last_status = meta.get("last_status") or "not_run"
    preview_text = (
        "This report hasn't built yet."
        if last_status == "not_run"
        else f"Last build status: {last_status}."
    )
    ctx = {
        "report": report,
        "report_url": _report_url(report),
        "instance_name": instance_name(),
        "last_status": last_status,
        "last_error": meta.get("last_error") or "",
        "accent": "#d97706",
        "status_color": "#d97706",
        "status_label": "NOT SENT",
        "preview_text": preview_text,
    }
    for user in recipients:
        try:
            local_ctx = dict(ctx)
            if schedule is not None:
                local_ctx["unsubscribe_url"] = unsubscribe_url("schedule", schedule.pk, user.pk)
            _send_simple(subject, "email/not_sent", local_ctx, user)
        except Exception:  # noqa: BLE001
            logger.exception("notify: failed to send not-sent notice for report %s", report.pk)
