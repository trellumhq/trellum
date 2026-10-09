"""Instance-wide models: the audit trail and the instance's own configuration."""
from django.conf import settings
from django.db import models

from apps.core.crypto import EncryptedJSONField, EncryptedTextField


def _default_lockout_account_threshold() -> int:
    """Seeds a fresh install's DB threshold from the env-level
    ``LOGIN_MAX_ATTEMPTS`` fallback, so a customer who already tuned that
    env var keeps the same effective limit the day this becomes DB-governed
    -- see apps/accounts/throttle.py's module docstring."""
    return settings.LOGIN_MAX_ATTEMPTS


def _default_lockout_window_minutes() -> int:
    return max(1, settings.LOGIN_ATTEMPT_WINDOW_SECONDS // 60)


class InstanceConfig(models.Model):
    """Singleton (pk=1) holding the instance settings an operator can change
    without editing ``.env`` and restarting.

    The split is deliberate. Anything Django reads at import time — secrets,
    ``DATABASE_URL``, ``ALLOWED_HOSTS``, and the ``PORTAL_BASE_URL`` that
    ``settings/prod.py`` turns into ``CSRF_TRUSTED_ORIGINS`` and the secure-cookie
    flags — stays in the environment, because a row in a table cannot influence
    a module that was imported before the database was reachable. What lives
    here is everything consulted at *request* time: the display name, the base
    URL used to build outbound links, and SMTP.
    """

    instance_name = models.CharField(max_length=100, default="trellum")
    public_base_url = models.URLField(
        blank=True,
        default="",
        help_text=(
            "Absolute URL of this portal, used for invitation links, SSO redirect "
            "URIs and report back-links. Blank = use PORTAL_BASE_URL from the "
            "environment."
        ),
    )
    email_host = models.CharField(max_length=255, blank=True, default="")
    email_port = models.PositiveIntegerField(default=587)
    email_use_tls = models.BooleanField(default=True)
    email_host_user = models.CharField(max_length=255, blank=True, default="")
    email_host_password = EncryptedTextField(blank=True, default="")
    email_from = models.EmailField(blank=True, default="")
    active_email_api_connection = models.ForeignKey(
        "EmailApiConnection", null=True, blank=True, on_delete=models.PROTECT,
        related_name="active_instances",
    )
    max_upload_mb = models.PositiveIntegerField(
        default=512,
        help_text=(
            "Largest data-source file a tenant may upload, in MB. 0 = no limit. "
            "Your reverse proxy must allow at least this much too — nginx's "
            "client_max_body_size defaults to 1 MB."
        ),
    )

    # ── Session policy (apps.accounts.session_policy) ──────────────────────
    # Instance-scoped in v1 -- a session belongs to a user, and a user can
    # belong to several orgs, so there is no honest per-org answer yet (see
    # apps/accounts/session_policy.py's module docstring). Defaults reproduce
    # today's behaviour exactly except session_absolute_hours, which is new
    # but invisible to almost everyone (see CHANGELOG).
    session_idle_minutes = models.PositiveIntegerField(
        default=720,
        help_text="Sign out after this many minutes of inactivity. 0 = disabled.",
    )
    session_absolute_hours = models.PositiveIntegerField(
        default=720,
        help_text="Hard cap on a session's age from login, regardless of activity. 0 = disabled.",
    )
    session_expire_at_browser_close = models.BooleanField(
        default=False,
        help_text="Sessions become non-persistent cookies unless remember-me is ticked.",
    )
    # Remember-me is deliberately stubbed off in v1 (design defers the UI):
    # the field exists so the knob has a stable name and default, but no form
    # ever sets it True yet, and LoginForm carries no checkbox for it.
    session_remember_me_enabled = models.BooleanField(
        default=False,
        help_text='Show a "Keep me signed in" checkbox on the login form. (Not yet built.)',
    )
    session_remember_me_days = models.PositiveIntegerField(
        default=30,
        help_text="Idle window granted when remember-me is ticked. The absolute cap still applies.",
    )
    #: Set only by the "Sign everyone out" action on /system -- never edited
    #: directly. Any session whose login predates this timestamp is dead on
    #: its next request. NULL = nobody has ever pressed the button.
    sessions_invalidated_at = models.DateTimeField(null=True, blank=True)

    # ── Login lockout (apps.accounts.throttle) ──────────────────────────────
    # DB-configured thresholds on top of the existing dual-key (email + IP)
    # counters; LOGIN_MAX_ATTEMPTS/LOGIN_ATTEMPT_WINDOW_SECONDS (env) remain
    # the defaults a fresh install starts from. See throttle.py's docstring
    # for why this is an escalating cooloff, never a permanent hard lock.
    lockout_account_threshold = models.PositiveIntegerField(
        default=_default_lockout_account_threshold,
        help_text="Failed attempts against one account before it cools off. 0 = disabled.",
    )
    lockout_ip_threshold = models.PositiveIntegerField(
        default=30,
        help_text="Failed attempts from one client address before it cools off (higher: shared NAT). 0 = disabled.",
    )
    lockout_window_minutes = models.PositiveIntegerField(
        default=_default_lockout_window_minutes,
        help_text="Rolling window the two thresholds above are counted over.",
    )
    lockout_cooloff_minutes = models.PositiveIntegerField(
        default=15,
        help_text="Cooloff after crossing a threshold. Doubles on repeat lockouts of the same key within 24h, capped at 240.",
    )

    # ── MFA (apps.accounts.mfa) ──────────────────────────────────────────────
    # This knob is instance-scoped: operators are exempt from org SSO
    # enforcement by design (apps/accounts/sso.py -- lockout safety), which
    # makes them the permanent password-login population and the reason this
    # compensating control exists.
    require_mfa_operators = models.BooleanField(
        default=False,
        help_text="Require instance operators (is_superuser / is_operator) to have MFA enrolled.",
    )

    setup_completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "instance configuration"
        verbose_name_plural = "instance configuration"

    def __str__(self) -> str:
        return self.instance_name

    def save(self, *args, **kwargs):
        self.pk = 1  # there is exactly one instance, by construction
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # noqa: ARG002
        raise NotImplementedError("The instance configuration row is permanent.")

    @classmethod
    def load(cls) -> "InstanceConfig":
        return cls.objects.get_or_create(pk=1)[0]


class EmailApiConnection(models.Model):
    """Named, encrypted outbound API configuration for this installation."""

    PROVIDER_CHOICES = (
        ("sendgrid", "SendGrid"),
        ("amazon_ses", "Amazon SES"),
        ("mailgun", "Mailgun"),
        ("postmark", "Postmark"),
        ("brevo", "Brevo"),
        ("resend", "Resend"),
        ("mailjet", "Mailjet"),
        ("mailersend", "MailerSend"),
        ("mailtrap", "Mailtrap Email Sending"),
        ("custom_https", "Custom HTTPS"),
    )
    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=24, choices=PROVIDER_CHOICES)
    credentials = EncryptedJSONField(default=dict, blank=True)
    provider_config = models.JSONField(default=dict, blank=True)
    custom_config = models.JSONField(default=dict, blank=True)
    from_email = models.EmailField()
    config_revision = models.PositiveIntegerField(default=1)
    last_test_results = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        from apps.core.email_providers import validate_api_profile

        validate_api_profile(
            self.provider, self.provider_config, self.custom_config, self.credentials,
        )

    def __str__(self):
        return self.name


class OpsState(models.Model):
    """What a background maintenance job last did.

    Small key/JSON table, one row per job. It exists so "what did cleanup
    delete last night" is answerable without reading container logs — which on
    the default deployment are an unaggregated stream on one box, and are the
    first thing lost when the disk fills.
    """

    key = models.CharField(max_length=64, primary_key=True)  # e.g. "cleanup"
    ran_at = models.DateTimeField()
    ok = models.BooleanField(default=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "ops state"
        verbose_name_plural = "ops state"

    def __str__(self) -> str:
        return f"{self.key} @ {self.ran_at:%Y-%m-%d %H:%M}"

    @classmethod
    def record(cls, key: str, *, ok: bool = True, **payload) -> "OpsState":
        from django.utils import timezone

        return cls.objects.update_or_create(
            key=key,
            defaults={"ran_at": timezone.now(), "ok": ok, "payload": payload},
        )[0]


class AuditLog(models.Model):
    """Append-only record of what happened in the portal: mutations
    (member invited, role changed, SSO configured), access (report viewed,
    export downloaded, live query run), and the handful of system events
    that have no request behind them (for example, retention's nightly purge).

    Written via ``apps.core.audit.audit()`` for anything tied to a request,
    or ``apps.core.audit.audit_system()`` for the request-less system rows
    -- never construct rows by hand in views, so the shape (and the
    category/outcome/redaction those two funnels apply) stays uniform. See
    ``apps.core.audit_actions`` for the full action taxonomy.
    """

    org = models.ForeignKey(
        "orgs.Organization", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # The operator behind an "act as user" session, when there is one. `actor`
    # deliberately stays the impersonated user — the action really was taken
    # with their permissions, and a trail that hid that would misrepresent what
    # the account could do. This column is what stops the trail claiming the
    # customer did it themselves.
    # db_index=False deliberately: nothing queries by impersonator yet, and on a
    # table that only ever grows an index build takes a lock this migration does
    # not need. Add it concurrently if a "what did this operator touch" view
    # ever lands.
    impersonator = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", db_index=False,
    )
    action = models.CharField(max_length=64)  # dotted verb: "member.invite", "run.enqueue"
    # Denormalized from apps.core.audit_actions.ACTIONS at write time -- never
    # parsed back out of `action` at query time. See that module for the five
    # values (auth/authz/access/admin/system) and why a column beats a scan.
    category = models.CharField(max_length=16, default="", blank=True)
    # success | denied | failure (apps.core.audit_actions.OUTCOMES). Column,
    # not a metadata key -- "who was refused" is the whole point of the SSO
    # denial trail, and it needs to be indexable.
    outcome = models.CharField(max_length=8, default="success")
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    # Truncated HTTP_USER_AGENT. Blank for request-less rows (audit_system())
    # and for legacy rows recorded before this column existed.
    user_agent = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["org", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            # Category pills on the dashboard, and purge_audit's per-category
            # cutoff scan (apps.core.retention).
            models.Index(fields=["org", "category", "-created_at"]),
            # The dashboard's actor filter.
            models.Index(fields=["org", "actor", "-created_at"]),
            # "History of this object", reached by clicking a target cell.
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            # Denials/failures tile and panel. Partial: tiny relative to the
            # table (most rows succeed), so it stays cheap forever regardless
            # of how large AuditLog itself grows.
            models.Index(
                fields=["org", "-created_at"],
                condition=~models.Q(outcome="success"),
                name="idx_audit_non_success",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        who = str(self.actor)
        if self.impersonator_id:
            who = f"{self.actor} (impersonated by {self.impersonator})"
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} by {who}"


class ServerLogEvent(models.Model):
    """Bounded diagnostic events, independent of report history retention."""

    id = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField()
    level = models.CharField(max_length=16)
    service = models.CharField(max_length=32)
    host = models.CharField(max_length=255)
    process = models.PositiveIntegerField()
    logger = models.CharField(max_length=200)
    message = models.TextField()
    exception = models.TextField(blank=True, default="")
    context = models.JSONField(default=dict)
    worker_id = models.CharField(max_length=100, blank=True, default="")
    run_id = models.CharField(max_length=36, blank=True, default="")
    report_slug = models.CharField(max_length=200, blank=True, default="")
    request_id = models.CharField(max_length=200, blank=True, default="")
    trigger = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["timestamp"], name="server_log_time_idx"),
            models.Index(fields=["run_id", "id"], name="server_log_run_idx"),
            models.Index(fields=["worker_id", "id"], name="server_log_worker_idx"),
        ]
