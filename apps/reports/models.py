"""The per-studio report index.

``report.yaml`` in the studio's git repository stays the source of truth for
configuration — these rows are a synced index that gives reports a stable
identity for foreign keys (runs, favorites), feeds the scheduler, and answers
list queries without touching disk. Run state (last_status, validation, ...)
is read live from ``output/<slug>/_meta.json``, which the framework owns.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.hashers import check_password as _check_password
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.db.models import F
from django.utils import timezone
from django.utils import timezone as dj_tz  # alias: EmailSchedule has its own "timezone" field

logger = logging.getLogger(__name__)


class Report(models.Model):
    KIND_REPORT = "report"
    KIND_ANALYSIS = "analysis"
    KIND_CHOICES = [(KIND_REPORT, "Report"), (KIND_ANALYSIS, "Analysis")]
    AUDIENCE_STUDIO = "studio"
    AUDIENCE_PRIVATE = "private"
    AUDIENCE_CHOICES = ((AUDIENCE_STUDIO, "Studio audience"), (AUDIENCE_PRIVATE, "Private"))

    studio = models.ForeignKey("studios.Studio", on_delete=models.CASCADE, related_name="reports")
    slug = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, default=KIND_REPORT, db_default=KIND_REPORT)
    audience = models.CharField(
        max_length=16, choices=AUDIENCE_CHOICES, default=AUDIENCE_STUDIO, db_default=AUDIENCE_STUDIO
    )
    name = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=200, blank=True, default="Uncategorized")
    tags = models.JSONField(default=list, blank=True)
    schedule_cron = models.CharField(max_length=100, blank=True, default="")
    schedule_timezone = models.CharField(max_length=64, blank=True, default="UTC")
    disabled = models.BooleanField(default=False)
    #: ``display.priority`` from report.yaml: lower sorts first, 99 is unranked (last).
    priority = models.IntegerField(default=99)
    config = models.JSONField(default=dict, blank=True)  # last-scanned report.yaml
    present_in_scan = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_scanned_at = models.DateTimeField(default=timezone.now)
    #: When this report last built SUCCESSFULLY -- the clock the built-data
    #: retention window runs on (apps.core.retention.purge_built_data).
    #:
    #: A column rather than a query for two reasons. ``_meta.json``'s
    #: ``last_run`` is bumped by failed runs too, so a report failing nightly
    #: would look freshly built forever and never expire -- exempting exactly
    #: the reports most likely to be serving stale data. And ``Run`` rows are
    #: themselves purged, on a window an operator may set SHORTER than this
    #: one, so the sweep cannot depend on run history still existing.
    #:
    #: NULL means "never built, or built before this column existed", and a
    #: NULL is never expired: unknown age must not authorise deletion.
    last_built_at = models.DateTimeField(null=True, blank=True)
    #: When retention deleted this report's built output; NULL while the
    #: output is intact. Set by apps.core.retention.purge_built_data, cleared
    #: by the next successful build.
    #:
    #: Recorded alongside ``last_built_at`` rather than by clearing it, which
    #: is what a sweep would naively do. Three things need the pair:
    #: the expired-report page has to say "built on X, deleted on Y" and both
    #: dates are gone the moment one is erased; "expired" and "never built"
    #: are different states owed different pages, and a single NULL clock
    #: collapses them; and the sweep needs something to exclude on, or it
    #: re-walks and re-deletes every purged report every night.
    data_expired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["studio", "slug"], name="uniq_report_per_studio")
        ]
        ordering = ["priority", "slug"]

    def __str__(self) -> str:
        return f"{self.studio}/{self.slug}"


class ReportPermissionGrant(models.Model):
    """One explicit report assignment for a permission group's Viewer grant."""

    grant = models.ForeignKey(
        "orgs.PermissionGroupGrant", on_delete=models.CASCADE, related_name="report_grants"
    )
    report = models.ForeignKey(
        Report, on_delete=models.CASCADE, related_name="permission_group_grants"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["grant", "report"], name="uniq_group_report_grant")
        ]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.grant_id and self.report_id:
            from apps.core.report_access import selected_report_access_block_reason

            grant = self.grant
            if grant.studio_id != self.report.studio_id:
                errors["report"] = "The report must belong to the grant's studio."
            if grant.role != "viewer" or not (
                grant.viewer_scope == "selected"
                or (grant.viewer_scope == "all" and self.report.audience == Report.AUDIENCE_PRIVATE)
            ):
                errors["grant"] = "Report assignments require a selected Viewer grant or a private report."
            if grant.group.org_id != self.report.studio.org_id:
                errors["grant"] = "The group and report must belong to the same organization."
            reason = selected_report_access_block_reason()
            if reason:
                errors["grant"] = f"Selected report access is unavailable: {reason}."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class MetricDefinition(models.Model):
    """One ``metrics.yaml`` entry, synced per-studio (semantic-layer Phase 2).

    Read-only from the portal's point of view: ``metrics.yaml`` in the
    studio's repository is the source of truth, and this row is a browsable
    index of it -- the same relationship ``Report`` has to ``report.yaml``.
    Editing happens by pull request against the file; nothing here is ever
    written back to git. Synced in the same pass as the report registry
    (``apps.reports.scan.sync_studio_metrics``, called from
    ``sync_studio_registry``), from ``trellum.metrics.load_metrics_result``.

    ``name`` is the metric's id (``metrics.yaml``'s ``name:`` key, matching
    ``trellum.metrics.Metric.name`` and every claim's ``{"metric": id}``) --
    not called ``slug`` like ``Report.slug`` so a claim in report.yaml and a
    row here read as obviously the same key without a rename in between.
    """

    studio = models.ForeignKey(
        "studios.Studio", on_delete=models.CASCADE, related_name="metric_definitions"
    )
    name = models.CharField(max_length=200)
    label = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=300, blank=True)
    format = models.CharField(max_length=32, default="number")
    version = models.PositiveIntegerField(default=1)
    #: The executable spec, when the metric is executable (empty/blank
    #: otherwise -- a *descriptive* metric per trellum.metrics.Metric).
    agg = models.CharField(max_length=32, blank=True, default="")
    column = models.CharField(max_length=200, blank=True, default="")
    numerator = models.CharField(max_length=200, blank=True, default="")
    denominator = models.CharField(max_length=200, blank=True, default="")
    #: Canonical derivation, informational only (never executed by the portal).
    sql = models.TextField(blank=True, default="")
    dimensions = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    #: trellum.metrics.compute_definition_hash's output for this definition --
    #: the identity a built report's _meta.json/`_metrics` claim is compared
    #: against to decide "current" vs. "built against an older version".
    definition_hash = models.CharField(max_length=64, blank=True, default="")
    present_in_scan = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_scanned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["studio", "name"], name="uniq_metric_per_studio")
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.studio}/{self.name}"

    def to_metric(self):
        """This row as a :class:`trellum.metrics.Metric` -- so display logic
        (``spec_text()``, ``executable``, ``agg_spec()``) lives in exactly one
        place, the framework's own dataclass, rather than being re-derived
        here from the stored fields."""
        from trellum.metrics import Metric

        return Metric(
            name=self.name,
            label=self.label,
            description=self.description,
            owner=self.owner,
            format=self.format,
            version=self.version,
            agg=self.agg or None,
            column=self.column or None,
            numerator=self.numerator or None,
            denominator=self.denominator or None,
            sql=self.sql,
            dimensions=tuple(self.dimensions or ()),
            tags=tuple(self.tags or ()),
            definition_hash=self.definition_hash,
        )

    def spec_text(self) -> str:
        return self.to_metric().spec_text()


class ReportFavorite(models.Model):
    """Per-user favorite reports (replaces the browser-localStorage list)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_favorites"
    )
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "report"], name="uniq_favorite")
        ]


class EmailSchedule(models.Model):
    """A user's personal "send me this report" cadence.

    Independent of ``Report.schedule_cron`` (which drives when the report is
    *built*) — this drives when a snapshot of the latest successful build is
    *emailed*, and to whom. Synced into the coordinator's APScheduler exactly
    like the build schedule (see ``runworker._SchedulerManager``), and read by
    ``apps.reports.notify.send_scheduled`` when its job fires.
    """

    FREQ_DAILY = "daily"
    FREQ_WEEKDAYS = "weekdays"
    FREQ_WEEKLY = "weekly"
    FREQ_MONTHLY = "monthly"
    FREQ_CHOICES = [
        (FREQ_DAILY, "Daily"),
        (FREQ_WEEKDAYS, "Weekdays"),
        (FREQ_WEEKLY, "Weekly"),
        (FREQ_MONTHLY, "Monthly"),
    ]

    #: Dynamic role groups a schedule can be addressed to, resolved against
    #: the report's studio at send time (see ``apps.reports.notify.
    #: resolve_recipients``) rather than frozen at pick time — a joiner
    #: starts hearing from the schedule, a leaver stops, with no edit
    #: required. "everyone" means every current studio member regardless of
    #: role; "admins"/"developers" mirror the same StudioMembership roles
    #: ``alert_recipients`` already draws its audience from.
    RECIPIENT_ROLE_CHOICES = ["admins", "developers", "everyone"]

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="email_schedules")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    #: Individually-picked recipients — fixed users, not re-resolved.
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="email_schedule_subscriptions", blank=True
    )
    recipient_roles = models.JSONField(
        default=list, blank=True,
        help_text='Subset of ["admins", "developers", "everyone"], expanded at send time.',
    )
    #: Org permission groups (apps.orgs.models.PermissionGroup) whose current
    #: membership is expanded at send time, same as recipient_roles — a
    #: reference, not a frozen snapshot.
    recipient_groups = models.ManyToManyField(
        "orgs.PermissionGroup", related_name="email_schedules", blank=True
    )
    freq = models.CharField(max_length=16, choices=FREQ_CHOICES, default=FREQ_DAILY)
    send_hour = models.PositiveSmallIntegerField(default=8)
    send_minute = models.PositiveSmallIntegerField(default=0)
    #: 0=Monday..6=Sunday (APScheduler's ``day_of_week`` convention). Only
    #: consulted when freq == "weekly".
    weekday = models.PositiveSmallIntegerField(default=0)
    #: Day of month, clamped to 1-28 so every month has one (no "31st"
    #: schedules that silently skip February). Only consulted when
    #: freq == "monthly".
    month_day = models.PositiveSmallIntegerField(default=1)
    timezone = models.CharField(max_length=64, default="UTC")
    attach_pdf = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=dj_tz.now)

    def clean(self) -> None:
        errors = {}
        if not 0 <= self.send_hour <= 23:
            errors["send_hour"] = "Must be 0-23."
        if not 0 <= self.send_minute <= 59:
            errors["send_minute"] = "Must be 0-59."
        if self.freq == self.FREQ_WEEKLY and not 0 <= self.weekday <= 6:
            errors["weekday"] = "Must be 0 (Monday) through 6 (Sunday)."
        if self.freq == self.FREQ_MONTHLY and not 1 <= self.month_day <= 28:
            errors["month_day"] = "Must be 1-28 (stays valid in every month)."
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                errors["timezone"] = f"Unknown IANA timezone: {self.timezone!r}."
        if self.recipient_roles:
            invalid = sorted(set(self.recipient_roles) - set(self.RECIPIENT_ROLE_CHOICES))
            if invalid:
                errors["recipient_roles"] = f"Unknown role(s): {', '.join(invalid)}."
            else:
                # Canonicalize here too, so this is the one place that owns
                # both validity and a stable order for the recipient summary
                # string (apps.reports.views._recipient_summary).
                self.recipient_roles = sorted(
                    set(self.recipient_roles), key=self.RECIPIENT_ROLE_CHOICES.index
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"schedule({self.report}, {self.freq} @ {self.send_hour:02d}:{self.send_minute:02d} {self.timezone})"


class BrokenReportPending(models.Model):
    """A broken-build transition awaiting the coalescing window before it is
    mailed out (see ``apps.reports.notify.flush_broken_buffers``).

    When several reports in one studio break within a short span of each
    other — a shared data source dying, a git-sync outage — nobody wants one
    email per report. ``notify._run_finished`` writes a row here instead of
    sending immediately; the coordinator's existing periodic scheduler sync
    (``runworker._SchedulerManager.refresh``, already on a ~60s cadence)
    calls ``flush_broken_buffers`` on every pass, which delivers a studio's
    pending rows — as one summary mail per recipient if there is more than
    one, or the normal single-report mail if there's just one — once the
    oldest of them has been waiting at least
    ``notify.COALESCE_WINDOW_SECONDS``. Recovered-build mail is unaffected:
    it is never buffered, and keeps sending immediately.
    """

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="+")
    run = models.ForeignKey("runner.Run", on_delete=models.CASCADE, related_name="+")
    #: The previously-completed run this transition was measured against —
    #: kept only so the eventual mail can compute the same "days since last
    #: failure" context ``_send_broken`` always has. Nullable because a
    #: run's previous run can, in principle, be deleted out from under it.
    previous_run = models.ForeignKey(
        "runner.Run", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=dj_tz.now)

    def __str__(self) -> str:
        return f"broken-pending({self.report})"


def _new_share_token() -> str:
    """A fresh, never-reused share token.

    ``secrets.token_urlsafe(24)`` draws from 192 bits of randomness, so a
    collision is not a realistic event -- the retry loop below is cheap
    insurance, not a load-bearing guarantee.
    """
    token = secrets.token_urlsafe(24)
    while ShareLink.objects.filter(token=token).exists():
        token = secrets.token_urlsafe(24)
    return token


class ShareLink(models.Model):
    """A revocable, anonymous public link to one report (internal planning#6).

    The token *is* the credential -- anyone holding it can open the report
    with no portal account, subject to expiry/password/revocation below. View
    counting here is deliberately a running total on the row, not a per-view
    event log: a richer per-view audit trail is a later feature, and this
    keeps the v1 read path a single ``UPDATE ... SET view_count = view_count
    + 1`` rather than a write-amplifying event table.
    """

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="share_links")
    token = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    #: Who minted the link. SET_NULL (not CASCADE): the link and the audit
    #: trail behind it should outlive the creator's account.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(default=dj_tz.now)
    #: null = no expiry.
    expires_at = models.DateTimeField(null=True, blank=True)
    #: blank = open link, no password. Never store the plaintext -- only
    #: ``make_password``'s salted hash (see ``set_password``/``check_password``
    #: below).
    password_hash = models.CharField(max_length=128, blank=True, default="")
    allow_export = models.BooleanField(default=False)
    #: An embed link (internal planning ticket #12): indefinite, password-less, and framed only
    #: by the origins in ``embed_origins`` -- lowercase ``scheme://host[:port]``
    #: entries, or exactly ``["*"]``. Enforced via ``frame-ancestors`` at
    #: serve time; the org opts in with OrgSharePolicy.embed_links_enabled.
    embed = models.BooleanField(default=False)
    embed_origins = models.JSONField(default=list, blank=True)
    #: How that embed renders, chosen when the link is minted (internal planning ticket #12) and
    #: applied at serve time. ``embed_theme`` names one of the report's own
    #: registered themes -- blank means "whatever the studio resolves to";
    #: ``embed_hide_filters`` additionally hides the filter bar, for a host
    #: that wants a static view. Both are meaningless on an ordinary share
    #: link and are only ever set alongside ``embed``.
    embed_theme = models.CharField(max_length=64, blank=True, default="")
    embed_hide_filters = models.BooleanField(default=False)
    revoked_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    last_viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = _new_share_token()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """Not revoked, and not past its (optional) expiry.

        Ignores org policy entirely -- this is the link's own state, used
        where policy doesn't apply (e.g. the "revoked" badge in the manage
        panel). Serve-time callers use :meth:`is_active_under` instead, which
        additionally accounts for a currently-required password and a
        currently-capped lifetime.
        """
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    def effective_expires_at(self, policy: "OrgSharePolicy | None"):
        """``self.expires_at`` capped by ``policy.max_expiry_days`` (if set),
        whichever is sooner. A cap is measured from ``created_at`` -- it
        gracefully ages old links out rather than retroactively demanding
        they'd already set a qualifying expiry."""
        if self.embed:
            return None
        cap = None
        if policy is not None and policy.max_expiry_days:
            cap = self.created_at + timedelta(days=policy.max_expiry_days)
        if self.expires_at is not None and cap is not None:
            return min(self.expires_at, cap)
        return self.expires_at if self.expires_at is not None else cap

    def is_active_under(self, policy: "OrgSharePolicy | None") -> bool:
        """The serve-time check (``apps.reports.views.share_entry`` /
        ``share_asset``): not revoked, not past its policy-capped expiry,
        and -- when the org's policy currently requires one -- carrying a
        password. A link that fails only this (not :attr:`is_active`) is
        "blocked by org policy" rather than genuinely dead: it resumes
        serving the moment the policy relaxes, with nothing to recreate.
        """
        if self.revoked_at is not None:
            return False
        if self.embed:
            return policy is not None and policy.embed_links_enabled
        if policy is not None and policy.require_password and not self.password_hash:
            return False
        expires_at = self.effective_expires_at(policy)
        if expires_at is not None and expires_at <= timezone.now():
            return False
        return True

    def set_password(self, raw: str) -> None:
        """``raw`` empty clears the password (an open link)."""
        self.password_hash = make_password(raw) if raw else ""

    def check_password(self, raw: str) -> bool:
        """An unset password always passes -- the caller decides whether to
        ask for one at all (see ``apps.reports.views.share_entry``)."""
        if not self.password_hash:
            return True
        return _check_password(raw, self.password_hash)

    def __str__(self) -> str:
        return f"share-link({self.report}, ...{self.token[-6:]})"


class OrgSharePolicy(models.Model):
    """Org-level opt-in for public share links (internal planning#6 follow-up).

    Disabled by default -- a fresh org has no row at all, and
    :func:`sharing_enabled` treats "no row" as "off". Lives in ``apps.reports``
    (not ``apps.orgs``, a different workspace's app) even though it is keyed
    by organization: the policy only ever governs a report-serving feature,
    so the model stays with the feature it gates rather than with the org
    app it references.
    """

    org = models.OneToOneField(
        "orgs.Organization", on_delete=models.CASCADE, related_name="share_policy"
    )
    share_links_enabled = models.BooleanField(default=False)
    #: When on, a link with no password no longer serves (existing links
    #: included -- see ShareLink.is_active_under). Enforced at create time
    #: too: a password becomes mandatory on every new link.
    require_password = models.BooleanField(default=False)
    #: Second opt-in for embed links (ShareLink.embed). Off: no new embed
    #: link can be created and existing ones stop serving until it's on again.
    embed_links_enabled = models.BooleanField(default=False)
    #: Caps how far out a link's expiry may be set, in days; null = no cap.
    #: Enforced going forward (new links must set a qualifying expiry) AND
    #: retroactively (an old link -- including one with no expiry at all --
    #: stops serving once it's older than this; see
    #: ShareLink.effective_expires_at).
    max_expiry_days = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"share-policy({self.org}, {'on' if self.share_links_enabled else 'off'})"


def get_share_policy(org) -> OrgSharePolicy:
    """The org's share policy, real or a default (unsaved) stand-in.

    Every call site -- create-time validation, serve-time enforcement, the
    settings page, the management API -- wants the same "no row yet" answer:
    disabled, no password requirement, no expiry cap. Returning an in-memory
    default here means every one of those reads ``policy.share_links_enabled``
    /``policy.require_password``/``policy.max_expiry_days`` without a None
    check of its own, and there is exactly one query per policy lookup
    (never zero-vs-one branches scattered across callers).
    """
    return OrgSharePolicy.objects.filter(org=org).first() or OrgSharePolicy(org=org)


def sharing_enabled(org) -> bool:
    """Whether ``org`` may create/serve public share links.

    No row = disabled: the feature is opt-in, so an org that has never
    touched the setting reads as off, not on. Callers that only need this
    one flag call this instead of reaching for the row directly; callers
    that also need ``require_password``/``max_expiry_days`` should call
    :func:`get_share_policy` once and read ``share_links_enabled`` off it,
    rather than paying for two separate queries.
    """
    return get_share_policy(org).share_links_enabled


# ── Org-level live-query rate limit ──────────────────────────────────────────
#
# The framework's live-query endpoint (apps.reports.livequery) enforces a
# per-user executions/minute cap. It used to be one process-wide constant;
# the live-query filter redesign turns one filter change into N linked
# queries (LiveDataSource.propagate_to), so the right budget for a given org
# depends on how its reports are built -- an org running several linked live
# queries per page wants a higher ceiling than the historical default.
#
# Same posture as OrgSharePolicy just above: disabled row = the default
# (DEFAULT_LIVE_QUERY_RATE_LIMIT), an org-admin self-service setting rather
# than an operator-only OrgQuota field (apps.orgs.models) -- this governs a
# per-request throttle every org has access to, separate from operator-managed
# resource quotas.

#: Historical constant, kept as the default for an org with no row.
DEFAULT_LIVE_QUERY_RATE_LIMIT = 30


class OrgLiveQueryPolicy(models.Model):
    """Per-org override of the live-query endpoint's per-user rate limit."""

    org = models.OneToOneField(
        "orgs.Organization", on_delete=models.CASCADE, related_name="live_query_policy"
    )
    #: None = use DEFAULT_LIVE_QUERY_RATE_LIMIT. Stored nullable (not
    #: defaulted to 30 on the column) so "an admin explicitly set 30" and
    #: "nobody has touched this" stay distinguishable in the row itself,
    #: matching OrgSharePolicy.max_expiry_days's blank-means-default idiom.
    rate_limit_per_minute = models.PositiveIntegerField(null=True, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"live-query-policy({self.org}, {self.rate_limit_per_minute or DEFAULT_LIVE_QUERY_RATE_LIMIT}/min)"


def live_query_rate_limit(org) -> int:
    """The effective per-user executions/minute cap for *org*.

    No row, or a row with the field left blank, reads as the historical
    default -- an org that has never touched the setting behaves exactly as
    it always did. Read by apps.reports.livequery._rate_limited(); the
    client-side live-query binder cannot know this value (no round trip
    exists to hand it over) and paces itself against a fixed heuristic
    instead -- see docs/COMPATIBILITY.md's `window._fwShareLink` row's
    sibling note on the endpoint contract.
    """
    policy = OrgLiveQueryPolicy.objects.filter(org=org).first()
    if policy is None or policy.rate_limit_per_minute is None:
        return DEFAULT_LIVE_QUERY_RATE_LIMIT
    return policy.rate_limit_per_minute


# ── View analytics (internal planning#4) ──────────────────────────────────────────
#
# Who actually looks at which report. Two capture surfaces feed this: the
# authenticated report page and the public share links above -- see
# apps.reports.views.report_asset / share_entry, which are the only two
# call sites that invoke record_view(), each exactly once per page view (the
# entry HTML, never a non-entry asset).


class ReportViewEvent(models.Model):
    """One (report, viewer, hour-bucket) view.

    Deduped at the DB level to an hourly bucket per viewer -- a refresh or a
    re-navigation within the same hour does not inflate the count. Exactly
    one of ``user``/``share_link`` is set (an authenticated view or an
    anonymous share view); the two ``UniqueConstraint``s below need a
    ``condition`` because plain multi-column uniqueness would let every NULL
    combination collide (or, per SQL's normal NULL handling, never collide at
    all -- either way, the wrong answer for a nullable dedup key).
    """

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="view_events")
    #: SET_NULL, not CASCADE: a deleted account should not take the
    #: historical view event (and the aggregates built from it) down with it.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: Anonymous share visitor. SET_NULL for the same reason -- revoking or
    #: deleting a link must not erase that someone viewed it.
    share_link = models.ForeignKey(
        ShareLink, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: The view's hour, with minutes/seconds/microseconds zeroed -- the
    #: dedup granularity. Set by record_view(); never assigned directly.
    bucket = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["report", "user", "bucket"],
                condition=models.Q(user__isnull=False),
                name="uniq_view_event_user_bucket",
            ),
            models.UniqueConstraint(
                fields=["report", "share_link", "bucket"],
                condition=models.Q(share_link__isnull=False),
                name="uniq_view_event_share_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["report", "created_at"], name="idx_view_event_report_created"),
        ]

    def __str__(self) -> str:
        return f"view-event({self.report}, {self.bucket.isoformat()})"


class ReportViewDaily(models.Model):
    """Per-day view rollup, kept forever.

    Raw ``ReportViewEvent`` rows are purged after 90 days (see
    ``apps.core.retention.purge_report_view_events``); this is what any
    window wider than that -- and the dashboard's 30-day sum, comfortably
    inside it today -- actually reads, so the daily total survives the raw
    log being trimmed.
    """

    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name="view_daily")
    date = models.DateField()
    views = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["report", "date"], name="uniq_view_daily")
        ]

    def __str__(self) -> str:
        return f"view-daily({self.report}, {self.date}, {self.views})"


def record_view(report, *, user=None, share_link=None, request=None) -> None:
    """Capture one page view.

    Called exactly once per page load, at the point the entry HTML is served
    (``apps.reports.views.report_asset`` for an authenticated view,
    ``share_entry`` for an anonymous one) -- never for a non-entry asset, so
    one page load never becomes more than one row.

    Must never raise into the serving path: a bug or a lost race here is not
    worth a broken report page over. ``ReportViewEvent``'s unique
    constraints make the dedup race-safe -- two concurrent requests for the
    same (report, viewer, hour) both attempt the insert, one wins, and the
    other's ``IntegrityError`` is caught below and treated as "already
    counted" rather than surfaced.

    When ``request`` is given, a hit that actually creates the dedup event
    also mirrors into the audit trail as ``report.view`` -- inheriting the
    hourly dedup (at most one audit row per viewer/report/hour) and this same
    try/except for free, so the audit trail's "who saw what" is exactly as
    bounded and as safe as the analytics it rides on. ``request`` is optional
    so this stays callable from anywhere that has no request in hand; such a
    caller simply gets the dedup counting with no audit mirror.
    """
    try:
        now = timezone.now()
        bucket = now.replace(minute=0, second=0, microsecond=0)
        try:
            _event, created = ReportViewEvent.objects.get_or_create(
                report=report, user=user, share_link=share_link, bucket=bucket,
            )
        except IntegrityError:
            return  # lost the race -- the winner already counted this view
        if not created:
            return
        # Only bump the daily rollup when this call actually created the
        # event -- a deduped (already-counted) view must not double-count
        # the day either.
        daily, daily_created = ReportViewDaily.objects.get_or_create(
            report=report, date=now.date(), defaults={"views": 1}
        )
        if not daily_created:
            ReportViewDaily.objects.filter(pk=daily.pk).update(views=F("views") + 1)

        if request is not None:
            from apps.core.audit import audit

            audit_kwargs = {"via": "share" if share_link is not None else "portal"}
            if share_link is not None:
                audit_kwargs["token_suffix"] = share_link.token[-6:]
            # org resolved from the report itself, not request.org -- the
            # share_entry call site never sets request.org (there is no
            # studio/org role to resolve for an anonymous visitor).
            audit(
                request, "report.view", target=report,
                org=report.studio.org, actor=user, **audit_kwargs,
            )
    except Exception:  # noqa: BLE001 - capture must never break report serving
        logger.exception("record_view failed for report=%s", getattr(report, "pk", None))
