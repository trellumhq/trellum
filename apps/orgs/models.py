"""Organizations, memberships, permission groups, and per-org configuration.

The tenancy model:

    Organization ── OrgMembership ── User          (belongs-to + base role)
         │        ── PermissionGroup ── Grant       (reusable role bundles)
         │                          ── Membership
         └── Studio (apps.studios) ── StudioMembership

Effective permissions are resolved in ``apps.core.permissions`` as the MAX of
direct memberships and group grants. Org/studio slugs are immutable: they key
directory trees under the data volume.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator
from django.db import models

from apps.core import roles
from apps.core.crypto import EncryptedTextField

slug_validator = RegexValidator(
    r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$",
    "Slugs are 1-64 chars of lowercase letters, digits and hyphens.",
)


class ImmutableSlugMixin(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
            if old is not None and old != self.slug:
                raise ValueError(
                    f"{type(self).__name__} slugs are immutable (they key data "
                    f"directories on disk); rename the display name instead."
                )
        super().save(*args, **kwargs)


class Organization(ImmutableSlugMixin):
    slug = models.SlugField(max_length=64, unique=True, validators=[slug_validator])
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    #: When an admin dismissed the getting-started checklist on the hub. Per-org
    #: rather than per-user: the checklist describes the organization's setup,
    #: not one person's progress through it.
    onboarding_dismissed_at = models.DateTimeField(null=True, blank=True)
    #: RETIRED as a resolver rung (apps.core.themes.explicit_studio_theme
    #: dropped it -- .lavish/theming-controls-design.html decision (2)):
    #: nothing anywhere set it any more even before that (see the
    #: theming-controls relocation, .lavish/theming-explained.html "The
    #: fix", decision (1)), which orphaned it as a rung nothing could aim
    #: at. Kept on the model, unused, same "keep the field, skip the
    #: migration" rule as User.theme -- a studio that hasn't picked its own
    #: palette now falls straight through to Trellum, full stop.
    default_theme = models.CharField(max_length=64, blank=True, default="")
    #: Light/Dark/Auto for the plain Trellum-looking org and settings
    #: surfaces (and any studio still on the Trellum default) before a
    #: viewer has set their own `mgmt-theme` preference -- the org-admin
    #: half of what used to be conflated with the palette dropdown. Set
    #: from Org settings -> Appearance; consumed by
    #: apps.core.context_processors.shell into `org_default_mode`, which
    #: templates/_theme_boot_mgmt.html uses as its pre-paint default.
    default_mode = models.CharField(
        max_length=8,
        choices=[("light", "Light"), ("dark", "Dark"), ("auto", "Auto")],
        default="dark",
    )
    #: When on, a studio's resolved theme (its own, or the org default) is
    #: final -- StudioMembership.theme personal overrides are ignored at
    #: resolution time and the setter refuses to write new ones. "Brand
    #: enforced": org admins can require every viewer to see the same look.
    #: Set from Org settings -> Appearance, alongside default_mode.
    lock_studio_theme = models.BooleanField(default=False)
    #: How long this organization's built report data and abandoned uploads
    #: live. None means "inherit the instance default". Stored rather than
    #: defaulted so raising RETENTION["built_data_days"] moves every org that
    #: never chose. Resolve them through apps.core.retention.built_window /
    #: upload_window, never by reading these directly -- built_days is capped
    #: by the instance ceiling and 0 does not mean the same thing on both.
    retention_built_days = models.PositiveSmallIntegerField(null=True, blank=True)
    retention_abandoned_upload_days = models.PositiveSmallIntegerField(null=True, blank=True)
    #: Whether members may authenticate with personal API keys
    #: (apps.accounts.models.ApiKey). Re-checked on every bearer request, so
    #: turning it off stops every key in the org at once without deleting
    #: any -- Org settings -> API keys.
    api_keys_enabled = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.slug

    # ── Disk layout ──────────────────────────────────────────────────────
    # Studios own ``<DATA_DIR>/studios/<org>/<studio>/``; an organization owns
    # this sibling tree for the files it shares ACROSS studios. Nothing here is
    # rebuildable from git, so it belongs in backups (docker/backup.sh).
    @property
    def data_root(self) -> Path:
        return Path(settings.DATA_DIR) / "orgs" / self.slug

    @property
    def datasources_dir(self) -> Path:
        """Root that an org-level file source's stored path resolves against."""
        return self.data_root / "data-sources"


class OrgMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="org_memberships"
    )
    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(
        max_length=16, choices=roles.ORG_ROLE_CHOICES, default=roles.ORG_MEMBER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "org"], name="uniq_org_membership")
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.org} ({self.role})"


class PermissionGroup(models.Model):
    """A reusable, org-scoped bundle of roles (e.g. "Demo BA", "Demo Admin").

    Attaching a group to a user grants, on top of their direct memberships:
      - ``org_role="admin"``: org-admin rights;
      - ``default_studio_role``: that role on EVERY current and future studio
        in the org;
      - per-studio :class:`PermissionGroupGrant` rows.
    """

    org = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="permission_groups"
    )
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=400, blank=True)
    org_role = models.CharField(
        max_length=16,
        blank=True,
        default="",
        choices=[("", "No org-level role"), (roles.ORG_ADMIN, "Org admin")],
    )
    default_studio_role = models.CharField(
        max_length=16,
        blank=True,
        default="",
        choices=[("", "No default"), *roles.STUDIO_ROLE_CHOICES],
        help_text="Applied to all current and future studios in the organization.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "name"], name="uniq_group_name_per_org")
        ]

    def __str__(self) -> str:
        return f"{self.org.slug}/{self.name}"


class PermissionGroupGrant(models.Model):
    REPORT_SCOPE_ALL = "all"
    REPORT_SCOPE_SELECTED = "selected"
    REPORT_SCOPE_CHOICES = (
        (REPORT_SCOPE_ALL, "All reports"),
        (REPORT_SCOPE_SELECTED, "Selected reports"),
    )

    group = models.ForeignKey(PermissionGroup, on_delete=models.CASCADE, related_name="grants")
    studio = models.ForeignKey(
        "studios.Studio", on_delete=models.CASCADE, related_name="group_grants"
    )
    role = models.CharField(max_length=16, choices=roles.STUDIO_ROLE_CHOICES)
    viewer_scope = models.CharField(
        max_length=16, choices=REPORT_SCOPE_CHOICES, default=REPORT_SCOPE_ALL
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "studio"], name="uniq_grant_per_studio"),
            models.CheckConstraint(
                condition=models.Q(viewer_scope="all") | models.Q(role=roles.VIEWER),
                name="selected_reports_require_viewer_role",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.viewer_scope != self.REPORT_SCOPE_SELECTED:
            return
        from apps.core.report_access import selected_report_access_block_reason

        reason = selected_report_access_block_reason()
        if reason:
            raise ValidationError({"viewer_scope": f"Selected report access is unavailable: {reason}."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PermissionGroupMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="permission_group_memberships",
    )
    group = models.ForeignKey(
        PermissionGroup, on_delete=models.CASCADE, related_name="memberships"
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], name="uniq_group_membership")
        ]


class OrgQuota(models.Model):
    """Per-organization limits.

    Blank/zero means "no limit" everywhere, so an install that never creates a
    quota row behaves exactly as before — which is what a single-tenant on-prem
    deployment wants. ``TRELLUM_QUOTAS_ENABLED`` lets an operator opt into
    enforcing the recorded limits.

    The shape deliberately mirrors ``apps.assistant.budget``: a ``precheck`` before
    the work, usage measured after it. See ``apps.orgs.quotas``.
    """

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="quota")
    #: How many builds this org may have in flight at once, across all runners.
    #: The lever that stops one tenant filling the fleet.
    max_concurrent_runs = models.PositiveIntegerField(default=0)
    #: Build minutes per calendar month, summed over finished runs.
    monthly_build_minutes = models.PositiveIntegerField(default=0)
    max_studios = models.PositiveIntegerField(default=0)
    max_reports = models.PositiveIntegerField(default=0)
    #: Largest memory class this org's studios may request.
    max_memory_mb = models.PositiveIntegerField(default=0)
    #: Total MB of uploaded data-source files this org may hold. Uploads are
    #: the one tenant-controlled consumer of the data volume that nothing else
    #: bounds — reports and output are rebuildable, uploaded bytes are not.
    max_storage_mb = models.PositiveIntegerField(default=0)
    notes = models.CharField(max_length=400, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"quota({self.org.slug})"


class OrgSSOConfig(models.Model):
    """Per-org single sign-on configuration: OpenID Connect or a directory.

    ``auth_method`` picks the identity source. For OpenID Connect any
    provider works (Keycloak, Okta, Google, Microsoft Entra ID, ...): the
    issuer URL is what the flow needs, and the allauth adapter builds a
    transient provider app from this row so secrets never land in allauth's
    tables. For LDAP / Active Directory the login form's password is checked
    against the directory (``apps.accounts.ldap``). Routing by email domain,
    the domain allowlist, group mapping, provisioning and enforcement are
    shared.

    Edited from the portal UI by org admins; secrets are encrypted at rest.
    """

    AUTH_OIDC = "oidc"
    AUTH_LDAP = "ldap"
    AUTH_METHOD_CHOICES = (
        (AUTH_OIDC, "OpenID Connect"),
        (AUTH_LDAP, "LDAP / Active Directory"),
    )

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="sso_config")
    enabled = models.BooleanField(default=False)
    # ponytail: one org = one identity source. An org wanting OIDC for some
    # users and a directory for others needs a second config row per org.
    auth_method = models.CharField(max_length=8, choices=AUTH_METHOD_CHOICES, default=AUTH_OIDC)
    issuer_url = models.URLField(
        max_length=500, blank=True, default="", validators=[URLValidator(schemes=["https"])],
        help_text="OpenID Connect issuer, e.g. https://keycloak.internal/realms/acme.",
    )
    client_id = models.CharField(max_length=64, blank=True)
    client_secret = EncryptedTextField(blank=True, default="")
    # ── LDAP / Active Directory ──────────────────────────────────────────
    ldap_server_uri = models.CharField(
        max_length=300, blank=True, default="",
        help_text="ldap://host:389 (upgraded with StartTLS) or ldaps://host:636.",
    )
    ldap_ca_cert = models.TextField(
        blank=True, default="",
        help_text="PEM certificate(s) of the CA that signed the directory's certificate, for a private CA.",
    )
    ldap_bind_dn = models.CharField(
        max_length=300, blank=True, default="",
        help_text="Service account that searches for users; blank binds anonymously.",
    )
    ldap_bind_password = EncryptedTextField(blank=True, default="")
    ldap_user_search_base = models.CharField(
        max_length=300, blank=True, default="", help_text="e.g. ou=people,dc=corp,dc=example.",
    )
    ldap_user_filter = models.CharField(
        max_length=500, default="(|(mail={login})(userPrincipalName={login}))",
        help_text="{login} is the email typed at the login form (escaped).",
    )
    ldap_email_attr = models.CharField(max_length=100, default="mail")
    ldap_name_attr = models.CharField(max_length=100, default="displayName")
    ldap_group_attr = models.CharField(
        max_length=100, default="memberOf",
        help_text="Attribute listing the user's group DNs; the group mapping keys on those DNs.",
    )
    email_domains = models.JSONField(
        default=list, blank=True,
        help_text='Login emails at these domains are routed to single sign-on (e.g. ["demo.example"]).',
    )
    auto_provision = models.BooleanField(
        default=False,
        help_text="Create portal accounts on first successful SSO login.",
    )
    default_org_role = models.CharField(
        max_length=16, choices=roles.ORG_ROLE_CHOICES, default=roles.ORG_MEMBER
    )
    default_groups = models.ManyToManyField(
        PermissionGroup, blank=True, related_name="+",
        help_text="Permission groups attached to auto-provisioned users.",
    )
    groups_claim = models.CharField(
        max_length=100, default="groups",
        help_text="Token claim that lists the user's groups (groups, roles, or a namespaced claim).",
    )
    group_map = models.JSONField(
        default=dict, blank=True,
        help_text="Identity provider group -> permission group id; synced on every login.",
    )
    extra_scopes = models.CharField(
        max_length=200, blank=True, default="",
        help_text=(
            "Scopes requested besides openid, profile and email, space-separated. "
            "Only for providers that emit the groups claim on request (see the SSO docs)."
        ),
    )
    allow_password_login = models.BooleanField(
        default=True,
        help_text="If off, users whose email matches this org's domains must use SSO.",
    )
    enforce_sso = models.BooleanField(
        default=False,
        help_text=(
            "Require SSO for every member of this organization, regardless of "
            "email domain — password login is refused (instance operators are "
            "exempt to prevent lockout)."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SSO({self.org.slug}, enabled={self.enabled})"

    @property
    def is_ldap(self) -> bool:
        return self.auth_method == self.AUTH_LDAP


class OrgSecurityPolicy(models.Model):
    """Per-org MFA requirement for members who authenticate with a password.

    OneToOne like ``OrgSSOConfig``, but deliberately a separate model rather
    than fields bolted onto it: MFA is about local-account strength, SSO is
    about identity routing, and conflating the two rows would make "does this
    org require MFA" a question you can only answer by first checking whether
    SSO happens to be configured at all.

    Enforcement (``apps.accounts.views.login_view`` and the redirect-lock in
    ``apps.accounts.session_policy``) reads this row directly.
    """

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="security_policy")
    require_mfa = models.BooleanField(
        default=False,
        help_text="Every member who signs in with a password must have MFA enrolled.",
    )
    mfa_grace_days = models.PositiveIntegerField(
        default=7,
        help_text=(
            "Members without a device may still sign in for this many days after the "
            "policy turns on (or after their account is created), with a nag banner. "
            "0 = enrollment required immediately."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"SecurityPolicy({self.org.slug}, require_mfa={self.require_mfa})"


class OrgDomain(models.Model):
    """An email domain an organization claims, and whether it proved it.

    Without proof, ``OrgSSOConfig.email_domains`` is an unverified assertion:
    on a shared instance, any org admin could claim ``victim.com`` and every
    login at that domain would be routed to *their* identity provider. That is
    a tenant-takeover vector, so on a multi-tenant instance a domain only
    routes once it is verified.

    Operators can disable enforcement explicitly for a trusted deployment with
    ``TRELLUM_SSO_DOMAIN_VERIFICATION``.
    """

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="domains")
    domain = models.CharField(max_length=253, db_index=True)
    #: Random per-claim. Published by the claimant as a DNS TXT record.
    verification_token = models.CharField(max_length=64)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=400, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One org per domain, globally: two orgs verifying the same domain
            # would reintroduce the ambiguity this model exists to remove.
            models.UniqueConstraint(fields=["domain"], name="uniq_domain_claim"),
        ]

    def __str__(self) -> str:
        state = "verified" if self.verified_at else "pending"
        return f"{self.domain} ({self.org.slug}, {state})"

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def dns_record_name(self) -> str:
        return f"_trellum-verification.{self.domain}"

    @property
    def dns_record_value(self) -> str:
        return f"trellum-verification={self.verification_token}"


class OrgAssistantConfig(models.Model):
    """Per-org AI assistant configuration — the org brings
    its own LLM API key."""

    PROVIDERS = [("anthropic", "Anthropic"), ("openai", "OpenAI")]

    org = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="assistant_config")
    enabled = models.BooleanField(default=False)
    provider = models.CharField(max_length=16, choices=PROVIDERS, default="anthropic")
    api_key = EncryptedTextField(blank=True, default="")
    model = models.CharField(max_length=100, blank=True)
    base_url = models.URLField(
        blank=True, help_text="Optional OpenAI-compatible gateway base URL."
    )
    monthly_budget_usd = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    per_user_budget_usd = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    # USD per million tokens. Required for a model the built-in price list
    # does not know (spend would otherwise be booked as zero); overrides the
    # list when set. Both or neither.
    price_in_per_mtok = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text="Input price in USD per million tokens. Required for models not in the built-in price list; overrides it otherwise.",
    )
    price_out_per_mtok = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text="Output price in USD per million tokens.",
    )
    share_report_source = models.BooleanField(
        default=True,
        help_text="Allow read_doc to send report source files (report.yaml, queries) to the model provider.",
    )
    actions_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Let the assistant propose portal actions: configure or test a data source, "
            "build a report, publish repository changes. Nothing runs until the user "
            "approves the proposal in the panel."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"assistant({self.org.slug}, enabled={self.enabled})"
