"""Per-studio data sources, stored in the portal DB with encrypted
credentials. The framework never reads this table: before every run the
worker materializes ``data-sources/config.yaml`` + ``TRELLUM_DS_<id>_*`` env vars
that satisfy the framework's existing resolver contract exactly
(trellum/data/resolvers.py::LocalEnvResolver)."""
from __future__ import annotations

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from apps.core.crypto import EncryptedJSONField

name_validator = RegexValidator(
    r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$",
    "Source names are 1-64 chars: letters, digits, underscore, hyphen "
    "(reports reference this name in report.yaml).",
)

#: Which stored keys become which env-var suffixes for credentialed types.
#: Hand-kept, and held in step with the framework's private
#: ``resolvers._ENV_SUFFIXES`` by tests/test_materialize.py::TestFrameworkLockstep.
ENV_SUFFIXES = {
    "host": "HOST",
    "port": "PORT",
    "database": "DB",
    "user": "USER",
    "password": "PASS",
    "project": "PROJECT",
    "credentials_json": "CREDENTIALS_JSON",
    "credentials_path": "CREDENTIALS_PATH",
    "account": "ACCOUNT",
    "warehouse": "WAREHOUSE",
    "schema": "SCHEMA",
    "catalog": "CATALOG",
    "http_path": "HTTP_PATH",
    "access_token": "ACCESS_TOKEN",
    "secure": "SECURE",
    "tenant_id": "TENANT_ID",
    "client_id": "CLIENT_ID",
    "client_secret": "CLIENT_SECRET",
    "site_url": "SITE_URL",
    "path": "PATH",
    "ssh_host": "SSH_HOST",
    "ssh_port": "SSH_PORT",
    "ssh_user": "SSH_USER",
    "ssh_private_key": "SSH_PRIVATE_KEY",
    "ssh_password": "SSH_PASSWORD",
    "ssh_host_key": "SSH_HOST_KEY",
}


#: Non-secret keys live in ``config``; secret keys live encrypted in
#: ``credentials``. The materializer merges both into the child env.
CONFIG_KEYS = {
    "host", "port", "database", "path", "upload", "project", "account",
    "warehouse", "schema", "catalog", "http_path", "secure",
    "tenant_id", "client_id", "site_url", "credentials_path",
    "ssh_host", "ssh_port", "ssh_user", "ssh_host_key",
}
CREDENTIAL_KEYS = {
    "user", "password", "client_secret", "credentials_json", "access_token",
    "ssh_private_key", "ssh_password",
}

#: An SSH tunnel is available to every host/port type; the framework opens it
#: when ``ssh_host`` is set (trellum/data/ssh_tunnel.py). ``ssh_host_key`` is
#: the bastion's public key: pinned when given, unverified otherwise.
SSH_FIELDS = ["ssh_host", "ssh_port", "ssh_user", "ssh_private_key", "ssh_password", "ssh_host_key"]


#: Fields that mean "this source can connect" per type.
REQUIRED_FIELDS = {
    "vertica": ["host", "user", "password"],
    "postgres": ["host", "user", "password"],
    "mysql": ["host", "user", "password"],
    "clickhouse": ["host", "user", "password"],
    "sqlserver": ["host", "user", "password"],
    "redshift": ["host", "user", "password"],
    # A password is optional: without one Trino connects unauthenticated.
    "trino": ["host", "user"],
    "databricks": ["host", "http_path", "access_token"],
    "snowflake": ["account", "user", "password"],
    "bigquery": ["project"],
    # Both credentials are optional: without one the framework falls back to
    # Application Default Credentials on the worker.
    "google_sheets": ["path"],
    "onedrive": ["tenant_id", "client_id", "client_secret", "site_url"],
    "sqlite": ["path"],
    "duckdb": ["path"],
    "file": ["path"],
    "image": ["path"],
}

#: ALL fields that are relevant per type — the settings form shows exactly
#: these and the save path drops everything else, so switching a source's
#: type can never leave stale credentials behind.
#:
#: "path" and "upload" are INDEPENDENT for the file-ish types, and must stay
#: that way: "path" is where the bytes live, "upload" is whether the portal may
#: replace them. A file committed in the repo that an analyst also refreshes
#: by hand between pushes is a real and supported combination.
#:
#: Ports are left to the framework driver's default when blank (5432, 1433,
#: 5439, 8080, ...), so no type carries a default port here.
TYPE_FIELDS = {
    "postgres": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "mysql": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "vertica": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "clickhouse": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "sqlserver": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "redshift": ["host", "port", "database", "user", "password", *SSH_FIELDS],
    "trino": ["host", "port", "user", "password", "catalog", "schema", "secure", *SSH_FIELDS],

    "databricks": ["host", "http_path", "access_token", "catalog", "schema"],
    "snowflake": ["account", "warehouse", "schema", "database", "user", "password"],
    "bigquery": ["project", "credentials_json", "credentials_path"],
    # "path" is the spreadsheet URL or ID, not a file: the framework reads it
    # through the resolver like any other connection field. The service
    # account is either stored here (credentials_json) or a file on the
    # worker (credentials_path); the framework prefers the inline one.
    "google_sheets": ["path", "credentials_json", "credentials_path"],
    "sqlite": ["path", "upload"],
    "duckdb": ["path", "upload"],
    "file": ["path", "upload"],
    "image": ["path", "upload"],
    "onedrive": ["tenant_id", "client_id", "client_secret", "site_url", "path"],
}

#: Types whose config.yaml entry is inline-path only (no resolver involved).
#: "image" is a portal-side refinement of "file" (image uploads/previews);
#: the framework sees it as a plain file entry.
INLINE_TYPES = ("sqlite", "duckdb", "file", "image")

#: Paths under this prefix are rewritten by git sync (see gitsync._copy_reports),
#: so an upload there survives only until the next pull of that report.
GIT_MANAGED_PREFIX = "reports/"

#: Types that may be shared at ORGANIZATION level — every type. A file-ish
#: source at org scope is always portal-managed (``upload``): its bytes live in
#: ``Organization.datasources_dir`` and the materializer writes that ABSOLUTE
#: path into every studio's config.yaml, so one uploaded file serves the whole
#: organization. Only the repo-backed variant stays studio-only, since a
#: repo-relative path resolves against one studio's project root.
ORG_ALLOWED_TYPES = (
    "postgres", "mysql", "vertica", "clickhouse", "sqlserver", "redshift",
    "trino", "databricks", "snowflake", "bigquery", "google_sheets",
    "onedrive", "sqlite", "duckdb", "file", "image",
)


def missing_required(type_: str, fields: dict) -> list[str]:
    """``REQUIRED_FIELDS`` of ``type_`` that ``fields`` leaves blank."""
    return [
        key
        for key in REQUIRED_FIELDS.get(type_, [])
        if not str(fields.get(key) or "").strip()
    ]


class DataSource(models.Model):
    TYPES = [
        ("postgres", "PostgreSQL"),
        ("mysql", "MySQL"),
        ("vertica", "Vertica"),
        ("clickhouse", "ClickHouse"),
        ("sqlserver", "SQL Server"),
        ("redshift", "Redshift"),
        ("trino", "Trino"),
        ("databricks", "Databricks"),
        ("snowflake", "Snowflake"),
        ("bigquery", "BigQuery"),
        ("google_sheets", "Google Sheets"),
        ("sqlite", "SQLite file"),
        ("duckdb", "DuckDB file"),
        ("file", "Data file (CSV/Excel)"),
        ("image", "Image (logo, background, asset)"),
        ("onedrive", "OneDrive / SharePoint"),
    ]

    # Exactly one of (studio, org) is set. Studio sources belong to one
    # studio; org sources are shared by every studio in the organization
    # (a studio source with the same name shadows the org one).
    studio = models.ForeignKey(
        "studios.Studio", on_delete=models.CASCADE, related_name="data_sources",
        null=True, blank=True,
    )
    org = models.ForeignKey(
        "orgs.Organization", on_delete=models.CASCADE, related_name="data_sources",
        null=True, blank=True,
    )
    name = models.CharField(max_length=64, validators=[name_validator])
    type = models.CharField(max_length=20, choices=TYPES)
    description = models.CharField(max_length=400, blank=True)
    config = models.JSONField(default=dict, blank=True)  # non-secret fields
    credentials = EncryptedJSONField(blank=True, null=True)  # secret fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    #: Outcome of the last connection check (apps.datasources.testing.run_check).
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_check_ok = models.BooleanField(null=True, blank=True)
    last_check_error = models.TextField(blank=True, default="")
    #: Set on rows that predate declaration sync; cleared by the studio's first
    #: sync so an undeclared row reads as "not in repo" only once that is known.
    awaiting_first_sync = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["studio", "name"], name="uniq_source_per_studio"),
            models.UniqueConstraint(fields=["org", "name"], name="uniq_source_per_org"),
            models.CheckConstraint(
                name="source_has_exactly_one_scope",
                condition=(
                    models.Q(studio__isnull=False, org__isnull=True)
                    | models.Q(studio__isnull=True, org__isnull=False)
                ),
            ),
        ]
        ordering = ["name"]

    @property
    def scope(self) -> str:
        return "studio" if self.studio_id else "org"

    @property
    def owner_org(self):
        return self.org if self.org_id else self.studio.org

    def __str__(self) -> str:
        owner = self.studio if self.studio_id else self.org
        return f"{owner}/{self.name} ({self.type})"

    @property
    def env_prefix(self) -> str:
        return f"TRELLUM_DS_{self.pk}"

    def all_fields(self) -> dict:
        return {**(self.config or {}), **(self.credentials or {})}

    def missing_fields(self) -> list[str]:
        return missing_required(self.type, self.all_fields())

    def scrub(self, text: str) -> str:
        """``text`` with every stored secret replaced -- for driver errors,
        which may echo their connection arguments. The user name stays."""
        for key, secret in (self.credentials or {}).items():
            if key != "user" and secret:
                text = text.replace(str(secret), "***")
        return text

    @property
    def is_configured(self) -> bool:
        return not self.missing_fields()

    @property
    def is_uploaded(self) -> bool:
        """True when the portal may write this source's file.

        Not "the file is not in git" — a repo-backed path can be uploadable
        too. This only answers whether the upload endpoint will accept a write.
        """
        return self.type in INLINE_TYPES and bool((self.config or {}).get("upload"))

    @property
    def path_is_git_managed(self) -> bool:
        """True when git sync rewrites this path, so an upload is temporary."""
        rel = ((self.config or {}).get("path") or "").replace("\\", "/")
        return self.studio_id is not None and rel.startswith(GIT_MANAGED_PREFIX)


def sources_for_studio(studio) -> list["DataSource"]:
    """What a studio's report builds can use: the organization's shared
    sources plus the studio's own — a studio source SHADOWS an org source
    with the same name. Single resolution rule for the materializer, the
    dashboard listing, and assistant."""
    by_name: dict[str, DataSource] = {}
    for ds in DataSource.objects.filter(org=studio.org).order_by("name"):
        by_name[ds.name] = ds
    for ds in DataSource.objects.filter(studio=studio).order_by("name"):
        by_name[ds.name] = ds
    return [by_name[name] for name in sorted(by_name)]


class RepoDataSource(models.Model):
    """A data source the studio's repository declares, mirrored from
    ``data-sources/config.yaml`` and from inline ``data_sources`` entries in
    ``report.yaml`` by git sync -- exactly as ``Report`` mirrors ``report.yaml``.
    Credentials never live here: secret keys are stripped before the row is
    written, and connecting stays the job of ``DataSource``.
    """

    studio = models.ForeignKey(
        "studios.Studio", on_delete=models.CASCADE, related_name="repo_sources"
    )
    name = models.CharField(max_length=64, validators=[name_validator])
    #: Free string, not ``DataSource.TYPES``: the framework registers types the
    #: portal has no form for, and the declaration is mirrored as written.
    type = models.CharField(max_length=64)
    config = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    #: Project-relative file the declaration was read from.
    source_file = models.CharField(max_length=300)
    #: Project-relative path git sync last copied out of the repository for
    #: this source (a file/sqlite source whose file is committed). Kept so a
    #: declaration that moves, or disappears, takes its file with it instead
    #: of leaving an orphan in the project dir. Empty when there is nothing to
    #: ship -- including when the portal owns the bytes (an upload).
    shipped_path = models.CharField(max_length=300, blank=True, default="")
    #: Why the declared file did NOT reach the project dir (a path escaping
    #: the project, a file over the instance's size cap). Blocks the build
    #: with that reason (see apps.datasources.status) instead of letting it
    #: fail mid-run on a file nobody delivered.
    file_error = models.CharField(max_length=300, blank=True, default="")
    present = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["studio", "name"], name="uniq_repo_source_per_studio"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.studio}/{self.name} ({self.type}, {self.source_file})"
