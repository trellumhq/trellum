"""User accounts. Email is the login identifier; there is no username.

Org/studio roles never live on the user — they come from membership rows
and permission groups (see ``apps.orgs``). ``is_superuser`` marks the
instance operator (may create organizations, sees the Django admin).
"""
from __future__ import annotations

import hashlib
import secrets
import string
from datetime import timedelta

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.core.crypto import EncryptedTextField


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    #: Access to the cross-organization operator console at /operator/.
    #: Deliberately separate from is_superuser: support staff need the fleet
    #: view without unrestricted Django-admin access to every table.
    #: is_superuser implies it — see :meth:`is_operator`.
    is_operator_flag = models.BooleanField(
        "operator console access", default=False, db_column="is_operator"
    )
    date_joined = models.DateTimeField(default=timezone.now)
    #: Bumped to invalidate every session this user holds except (optionally)
    #: the one that triggered the bump -- "sign out my other sessions",
    #: admin MFA reset, account-compromise response. Checked per-request by
    #: apps.accounts.session_policy.SessionSecurityMiddleware against the
    #: sp_epoch stamp written into each session at login. A plain integer
    #: rather than a timestamp: no clock comparison, no "same second" edge.
    auth_epoch = models.PositiveIntegerField(default=0)
    #: SUPERSEDED -- theme is a per-studio property now (Studio.theme,
    #: StudioMembership.theme; see apps.core.themes.resolve_studio_theme --
    #: Organization.default_theme is itself retired from that chain too,
    #: same "keep the field" treatment). This column is kept only to
    #: avoid a destructive migration on an unreleased field; nothing reads
    #: it any more -- apps.core.context_processors.shell resolves
    #: studio_theme from the new per-studio chain instead, and the retired
    #: apps.accounts.views.theme_set that used to write this is gone.
    theme = models.CharField(max_length=64, blank=True, default="")
    #: When this account was erased under GDPR Art. 17 -- and the whole
    #: attributability mechanism. Erasure scrubs the row's identifying
    #: columns but KEEPS the row, so every SET_NULL reference that must
    #: outlive the person (audit rows, view events, run history, the LLM
    #: billing ledger) keeps pointing at the same primary key. That gives
    #: "how many actions did the erased person take" as an ordinary query,
    #: and keeps their past actions distinguishable from a genuinely NULL
    #: actor -- which is what audit_system() writes for the instance's own
    #: events. Deleting the row instead would collapse both into NULL, and
    #: a separate tombstone table would duplicate a key the row already has.
    erased_at = models.DateTimeField(null=True, blank=True)
    #: When the dormancy policy last emailed this account to say it was about
    #: to be closed -- and the only thing that starts the clocks for the two
    #: irreversible stages after it (see apps.core.retention). It is stamped
    #: when the mail is actually accepted for delivery, never before, so a
    #: broken mail server stalls the whole policy rather than silently
    #: disabling and erasing people who were never told.
    #:
    #: Nothing ever clears it: a warning older than ``last_login`` is spent,
    #: because the person did the one thing the mail asked them to do. That
    #: makes a sign-in reset every stage at once through the column Django
    #: already updates on every login, with no receiver to forget about when
    #: a new sign-in path lands.
    dormancy_warned_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    def __str__(self) -> str:
        return self.email

    @property
    def display_name(self) -> str:
        return self.name or self.email

    @property
    def is_operator(self) -> bool:
        return self.is_superuser or self.is_operator_flag

    @property
    def has_mfa(self) -> bool:
        device = getattr(self, "totp_device", None)
        return bool(device and device.confirmed_at is not None)

    def bump_auth_epoch(self) -> None:
        """Invalidate every session this user holds on its next request.

        A plain UPDATE, not save(): callers reach this from code paths that
        may be holding a stale in-memory copy of ``self`` (a form's
        ``instance``, an admin action's queryset row), and a blind
        ``self.save()`` there would silently clobber other fields with
        whatever that stale copy happened to have.
        """
        User.objects.filter(pk=self.pk).update(auth_epoch=models.F("auth_epoch") + 1)
        self.auth_epoch += 1


#: Every secret starts with this. The Bearer middleware keys on it, so any
#: other Authorization header passes through untouched.
API_KEY_PREFIX = "trellum_pk_"
_ALNUM = string.ascii_letters + string.digits
#: last_used_at is stamped at most this often -- one UPDATE per key per
#: minute, not one per request.
_TOUCH_INTERVAL = timedelta(seconds=60)


def _alnum(n: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(n))


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class ApiKey(models.Model):
    """A personal bearer token for the studio JSON endpoints.

    Belongs to one user *and* one org: the membership is re-checked on every
    request, so a leaver's keys die with their membership. The secret
    (``trellum_pk_<8 prefix>_<32 random>``) is shown once at creation and
    stored only as a sha256 hash; ``prefix`` is the plaintext lookup column,
    since a hash cannot be searched by the secret it was made from.
    Rotation is create-new-then-revoke-old. Design: internal documentation
    ``internal design notes`` §4.
    """

    READ = "read"
    READ_WRITE = "read,write"
    SCOPE_CHOICES = [(READ, "Read"), (READ_WRITE, "Read and write")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    org = models.ForeignKey(
        "orgs.Organization", on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=8, db_index=True)
    key_hash = models.CharField(max_length=64)
    scopes = models.CharField(max_length=16, choices=SCOPE_CHOICES, default=READ)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"apikey({self.user.email}, {self.prefix})"

    @classmethod
    def mint(cls, *, user, org, name: str, scopes: str = READ, expires_at=None):
        """Create a key; returns ``(instance, secret)``. The secret is never
        stored and never shown again."""
        secret = f"{API_KEY_PREFIX}{_alnum(8)}_{_alnum(32)}"
        key = cls.objects.create(
            user=user, org=org, name=name, scopes=scopes, expires_at=expires_at,
            prefix=secret[len(API_KEY_PREFIX):][:8], key_hash=_hash(secret),
        )
        return key, secret

    @classmethod
    def authenticate(cls, secret: str) -> ApiKey | None:
        """The live key ``secret`` proves, or None. Membership and the org
        toggle are checked here rather than at creation -- policy is
        re-checked at use, the same way share links do it."""
        from apps.orgs.models import OrgMembership

        prefix = secret[len(API_KEY_PREFIX):][:8]
        key = cls.objects.filter(prefix=prefix).select_related("user", "org").first()
        if key is None or not constant_time_compare(key.key_hash, _hash(secret)):
            return None
        ok = (
            key.is_active
            and key.user.is_active
            and key.org.api_keys_enabled
            and OrgMembership.objects.filter(user=key.user, org=key.org).exists()
        )
        return key if ok else None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and (
            self.expires_at is None or self.expires_at > timezone.now()
        )

    @property
    def can_write(self) -> bool:
        return "write" in self.scopes.split(",")

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    def touch(self) -> None:
        """Stamp ``last_used_at``, at most once a minute. A blind UPDATE, for
        the same reason as :meth:`User.bump_auth_epoch`."""
        now = timezone.now()
        if self.last_used_at is None or now - self.last_used_at >= _TOUCH_INTERVAL:
            type(self).objects.filter(pk=self.pk).update(last_used_at=now)
            self.last_used_at = now


def new_invite_token() -> str:
    return secrets.token_urlsafe(32)


def default_invite_expiry():
    return timezone.now() + timezone.timedelta(days=14)


class Invitation(models.Model):
    """An email invited into an organization, with the roles it will get.

    Works with or without SMTP: the create response always shows the
    copyable accept link.
    """

    org = models.ForeignKey(
        "orgs.Organization", on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField()
    org_role = models.CharField(max_length=16, default="member")
    groups = models.ManyToManyField("orgs.PermissionGroup", blank=True, related_name="+")
    studio_grants = models.JSONField(
        default=list, blank=True,
        help_text='[{"studio_id": 1, "role": "viewer"}, ...]',
    )
    token = models.CharField(max_length=64, unique=True, default=new_invite_token)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_invite_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_accepted(self) -> bool:
        return self.accepted_at is not None

    def accept_url(self) -> str:
        from apps.core.instance import base_url

        return f"{base_url()}/invite/{self.token}"

    def __str__(self) -> str:
        return f"invite {self.email} -> {self.org.slug}"


class UserSession(models.Model):
    """A live login, for the "where you're signed in" list and revocation.

    Best-effort observability, not the enforcement mechanism: the truth an
    attacker cannot route around is the sp_* stamps
    ``SessionSecurityMiddleware`` checks against ``django_session`` on every
    request (apps.accounts.session_policy). This table exists so a human can
    *see* and *end* one session without waiting for a policy window to close
    it, and so "sign out other sessions" has something to delete rows from.
    Rows for dead sessions are pruned by the retention cleanup job
    (apps.core.retention) -- see ``prune_dead_sessions``.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    #: django_session's primary key. Unique: the same session_key can only
    #: ever belong to one login at a time (Django cycles the key on every
    #: login()), so a stale row from a previous occupant would be a bug, not
    #: a legitimate second owner.
    session_key = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    #: Refreshed with the same 60s coalescing granularity as the sp_last_seen
    #: session stamp, piggybacked on the same request rather than a second
    #: write -- see SessionSecurityMiddleware.
    last_seen = models.DateTimeField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True, default="")
    remember_me = models.BooleanField(default=False)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self) -> str:
        return f"session({self.user.email}, {self.session_key[:8]}…)"


class TotpDevice(models.Model):
    """A user's TOTP (RFC 6238) second factor. One per user in v1 -- the
    model leaves room for more (WebAuthn later) by keying on the user rather
    than assuming a singleton, but nothing today creates a second row.

    ``secret`` is Fernet-encrypted at rest via the same EncryptedTextField
    every other tenant secret uses (apps.core.crypto) -- key rotation and the
    ``enc$1$`` marker come for free. An unconfirmed device (``confirmed_at``
    is None) is enrollment-in-progress: it is never consulted by the login
    step-up or by ``User.has_mfa``, and re-beginning enrollment replaces it.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret = EncryptedTextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    #: Replay guard: a TOTP counter step may verify successfully once. Stored
    #: as the raw step counter (time // 30), not a timestamp, so drift-window
    #: comparisons stay integer arithmetic.
    last_used_step = models.BigIntegerField(default=0)

    def __str__(self) -> str:
        state = "confirmed" if self.confirmed_at else "pending"
        return f"totp({self.user.email}, {state})"


class RecoveryCode(models.Model):
    """One single-use MFA recovery code. Ten are minted per (re)generation;
    shown to the user exactly once at creation time and stored only as a
    salted hash (Django's default password hasher) -- there is no "show me
    my codes again" surface, by design, same as a password.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        state = "used" if self.used_at else "unused"
        return f"recovery-code({self.user.email}, {state})"
