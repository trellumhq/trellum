"""Shared settings for every environment.

Environment-driven via django-environ. Instance-level configuration lives in
the process environment (or a ``.env`` file next to ``manage.py``); everything
tenant-level (SSO, assistant keys, repo tokens, data-source credentials) lives
encrypted in the database — see ``apps/core/crypto.py``.
"""
from pathlib import Path

import environ

# Repo root: trellum_portal/settings/base.py -> trellum_portal/settings -> trellum_portal -> root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# Signs session cookies and the one-time links in invitation and
# password-reset email. Named for what it does rather than for the web
# framework underneath, which is no concern of the operator setting it.
SECRET_KEY = env("SESSION_SECRET_KEY", default="")
DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Public base URL of this instance (used for absolute links: invites, SSO
# redirect URIs, the back-link injected into built reports).
PORTAL_BASE_URL = env("PORTAL_BASE_URL", default="http://localhost:8050").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # allauth is installed from day one so its migrations land once; it is
    # only actively used for the per-org OpenID Connect flow (M5).
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    # First-party apps
    "apps.core",
    "apps.accounts",
    "apps.orgs",
    "apps.studios",
    "apps.reports",
    "apps.datasources",
    "apps.runner",
    "apps.assistant",
    "apps.alerts",
    "apps.operator",
]

MIDDLEWARE = [
    # First, so everything downstream — including security middleware's own
    # rejections — logs under a request id.
    "apps.core.middleware.RequestIdMiddleware",
    "apps.core.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Enforces the session policy (idle timeout, absolute cap, instance-wide
    # and per-user revocation) and the forced-MFA-enrollment redirect-lock.
    # Immediately after AuthenticationMiddleware (needs request.user) and
    # before ImpersonationMiddleware (killing the session must also end any
    # impersonation riding on it) -- see apps.accounts.session_policy.
    "apps.accounts.session_policy.SessionSecurityMiddleware",
    # Bearer API keys (internal planning ticket #002). After SessionSecurityMiddleware, not
    # directly after AuthenticationMiddleware: that one adopts any
    # authenticated request into a session (stamps sp_*), which would write
    # a django_session row and a cookie for every API call. A bearer request
    # is anonymous to it and has no session to police.
    "apps.accounts.api_keys.ApiKeyMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # Ends a time-boxed operator impersonation. After AuthenticationMiddleware,
    # since it acts on request.user.
    "apps.core.impersonation.ImpersonationMiddleware",
]

ROOT_URLCONF = "trellum_portal.urls"
WSGI_APPLICATION = "trellum_portal.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.shell",
            ],
        },
    },
]

DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://trellum:trellum@127.0.0.1:5433/trellum_portal"),
}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/"
SITE_ID = 1

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# allauth: local accounts are handled by our own views; allauth only drives
# the per-org OpenID Connect flow. Never let it open self-signup.
# Our User model is email-only — without these two lines allauth's SSO
# signup 500s trying to clean a 'username' field that does not exist.
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_ADAPTER = "apps.accounts.adapters.NoSignupAccountAdapter"
# allauth must never offer a credential surface of its own.
#
# `include("allauth.urls")` otherwise mounts allauth's own /accounts/login/,
# /accounts/password/set/ and /accounts/password/reset/. That login form calls
# django authenticate(email=..., password=...), ModelBackend resolves it through
# USERNAME_FIELD ("email"), and the resulting session has never passed
# `sso.enforced_config_for_user()` or `allow_password_login` in
# apps/accounts/views.login_view — an org that mandates SSO could still be
# entered with a password. /accounts/password/set/ was the matching escalation:
# an SSO-provisioned user has an unusable password until something lets them set
# one.
#
# SOCIALACCOUNT_ONLY is allauth's own switch for "this project has no local
# account UI": it drops signup/email/password URLs from allauth.account.urls
# entirely and makes its LoginView refuse every non-GET (PermissionDenied).
# Requires ACCOUNT_EMAIL_VERIFICATION="none" (above), no allauth.mfa and no
# login-by-code — all true here; `manage.py check` enforces it.
SOCIALACCOUNT_ONLY = True

# Declared rather than inherited. Django's default is exactly this list, but
# leaving it implicit hid the fact that allauth's login form authenticates
# through it. Our own login view is the only password path; it calls
# authenticate() directly, so allauth's AuthenticationBackend is not needed —
# the SSO flow logs users in via sociallogin.connect().
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
SOCIALACCOUNT_ADAPTER = "apps.accounts.adapters.OrgSSOAdapter"
# The login view redirects straight into the provider flow (standard SSO UX).
SOCIALACCOUNT_LOGIN_ON_GET = True
# Never persist provider tokens; the portal only needs the identity.
SOCIALACCOUNT_STORE_TOKENS = False
# Always show the IdP's account picker: without this, Microsoft silently
# reuses whatever session is active in the browser — the wrong-account trap.
SOCIALACCOUNT_PROVIDERS = {
    "openid_connect": {"AUTH_PARAMS": {"prompt": "select_account"}},
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# CSRF token must be readable by portal.js / assistant.js (X-CSRFToken header).
CSRF_COOKIE_HTTPONLY = False

# The env-level OUTER BOUND on a session's cookie age -- SessionSecurityMiddleware
# (apps.accounts.session_policy) is the actual enforcer of idle/absolute limits,
# reading InstanceConfig at request time; this is the belt-and-braces ceiling
# Django's own expiry falls back to for a session the middleware never sees
# again (see that module's docstring). Previously only set in prod.py, which
# meant dev/test silently ran Django's default two weeks -- moved here so every
# environment gets the same 12h default a real deployment has always had.
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=60 * 60 * 12)

# ---------------------------------------------------------------------------
# Portal-specific settings
# ---------------------------------------------------------------------------

# Root of all durable studio state (git checkouts, materialized project roots,
# report output). One volume in production (/data), a repo-local dir in dev.
DATA_DIR = Path(env("TRELLUM_DATA_DIR", default=str(BASE_DIR / ".data-dev")))

# Where built report output is READ from. See apps/core/storage.py.
#
#   local  runner and web share a filesystem — the default, and the only
#          supported single-VM shape. Nothing is copied.
#   s3     the runner publishes each build to a bucket and the web process
#          serves from a local read cache.
#
# Builds always write to the local project root first either way; with "s3"
# the runner uploads that directory afterwards under a per-tenant prefix. The
# framework's own BI_STORAGE_BACKEND is deliberately NOT what this sets — the
# portal owns the key layout because the framework's is single-project.
#
# Note the prefix difference, which is not an oversight: these are the
# PORTAL's settings and carry the portal's name. The framework keeps its own
# BI_STORAGE_* / BI_REPORTS_BUCKET contract, which is not ours to rename from
# here, so apps/core/storage.py translates at the boundary.
TRELLUM_STORAGE_BACKEND = env("TRELLUM_STORAGE_BACKEND", default="local")
TRELLUM_REPORTS_BUCKET = env("TRELLUM_REPORTS_BUCKET", default="")

# Object-store connection details for a non-AWS store (MinIO, Ceph, Wasabi).
# Declared here rather than left to flow straight through the process
# environment, because the operator sets TRELLUM_* and the framework reads
# BI_*: something has to know both names, and a setting is where an operator
# looks. apps/core/storage.py does the translation.
TRELLUM_STORAGE_ENDPOINT_URL = env("TRELLUM_STORAGE_ENDPOINT_URL", default="")
TRELLUM_STORAGE_ACCESS_KEY = env("TRELLUM_STORAGE_ACCESS_KEY", default="")
TRELLUM_STORAGE_SECRET_KEY = env("TRELLUM_STORAGE_SECRET_KEY", default="")
TRELLUM_STORAGE_REGION = env("TRELLUM_STORAGE_REGION", default="")

# How built report content reaches a viewer's browser. See apps/core/cdn.py.
#
#   proxy          the portal serves report content itself, behind its own
#                  permission check. The bucket (if any) is never exposed.
#                  The default and the only self-safe-by-construction shape.
#   edge-signed    a CDN/edge worker in front of the bucket serves the bytes;
#                  the portal issues a short-lived Ed25519 grant per viewer
#                  after its permission check, and the edge verifies it. The
#                  hosted (Cloudflare R2 + Worker) posture.
#   edge-external  the customer fronts the public bucket with their OWN
#                  identity layer (Azure AD / IAP); the portal emits content
#                  URLs and issues no grant. The portal cannot see that layer,
#                  so it PROVES a stranger is refused before serving (below).
TRELLUM_REPORT_ACCESS_MODEL = env("TRELLUM_REPORT_ACCESS_MODEL", default="proxy")
# Public origin report content is served from in an edge posture, e.g.
# https://reports.example.com — used to build content URLs and to probe that a
# stranger cannot read them.
TRELLUM_CDN_BASE_URL = env("TRELLUM_CDN_BASE_URL", default="").rstrip("/")
# Ed25519 private key (PEM) the portal signs grants with; the edge worker holds
# only the matching public key. File wins over inline. edge-signed only.
TRELLUM_CDN_SIGNING_KEY = env("TRELLUM_CDN_SIGNING_KEY", default="")
TRELLUM_CDN_SIGNING_KEY_FILE = env("TRELLUM_CDN_SIGNING_KEY_FILE", default="")
# Grant lifetime, and therefore the revocation latency: a viewer whose access
# is removed can keep fetching already-granted content for at most this long.
TRELLUM_CDN_COOKIE_TTL_SECONDS = env.int("TRELLUM_CDN_COOKIE_TTL_SECONDS", default=3600)
# Enables selected-report grants after every web/worker process understands
# them. Edge-signed operators must additionally wait one full cookie TTL after
# deploying report-scoped grants, so no legacy studio-wide cookie remains.
TRELLUM_REPORT_SCOPED_ACCESS_READY = env.bool(
    "TRELLUM_REPORT_SCOPED_ACCESS_READY", default=False
)

# Comma-separated Fernet keys; first encrypts, all decrypt (rotation).
SECRET_ENCRYPTION_KEY = env("SECRET_ENCRYPTION_KEY", default="")

# Wall-clock ceiling on one AI assistant turn (all model calls and tool
# iterations for a single user message). Past it the stream ends with a
# `deadline` error and the partial transcript is kept.
ASSISTANT_WORKLOAD_IDENTITY_ORGS = env.json("ASSISTANT_WORKLOAD_IDENTITY_ORGS", default={})

ASSISTANT_TURN_DEADLINE_S = env.int("ASSISTANT_TURN_DEADLINE_S", default=120)

# Operational policy switches. Product features are available in every
# installation; these settings control deployment policy rather than a tier.
TRELLUM_ORG_SELF_SIGNUP = env.bool("TRELLUM_ORG_SELF_SIGNUP", default=False)
TRELLUM_QUOTAS_ENABLED = env.bool("TRELLUM_QUOTAS_ENABLED", default=False)
TRELLUM_IMPERSONATION_ENABLED = env.bool("TRELLUM_IMPERSONATION_ENABLED", default=True)
TRELLUM_SSO_DOMAIN_VERIFICATION = env.bool(
    "TRELLUM_SSO_DOMAIN_VERIFICATION", default=True
)

# Worker (report runner) tuning.
WORKER_MAX_CONCURRENT = env.int("WORKER_MAX_CONCURRENT", default=3)
WORKER_CATCHUP = env.bool("WORKER_CATCHUP", default=False)
WORKER_HEARTBEAT_SECONDS = 15
# How often to reclaim runs whose worker stopped beating. Must comfortably
# exceed the heartbeat interval so a slow beat is never mistaken for death.
WORKER_REAP_SECONDS = env.int("WORKER_REAP_SECONDS", default=60)

# Memory accounting for report builds. See apps/runner/executor.py.
#
# Sizing a host: give the runner what is left after the rest of the stack.
#   TRELLUM_RUNNER_MEMORY_BUDGET_MB = total_ram_mb - 1024 (postgres)
#                                             - 1024 (web)
#                                             -  512 (headroom)
# On an 8 GB VM that is roughly 5600. Left at 0, only WORKER_MAX_CONCURRENT
# limits how many builds run at once — the pre-existing behaviour.
TRELLUM_RUNNER_MEMORY_BUDGET_MB = env.int("TRELLUM_RUNNER_MEMORY_BUDGET_MB", default=0)
# Memory limit for every report build (MB). report.yaml carries no sandbox
# limits; this and the budget above are the operator's.
TRELLUM_DEFAULT_JOB_MEMORY_MB = env.int("TRELLUM_DEFAULT_JOB_MEMORY_MB", default=1024)
# The hard RLIMIT_AS cap is this multiple of that figure. Address space
# legitimately exceeds resident memory, so the cap sits above the number used
# for packing and acts as a runaway guard rather than a precise ceiling.
TRELLUM_JOB_MEMORY_HEADROOM = env.float("TRELLUM_JOB_MEMORY_HEADROOM", default=1.5)
TRELLUM_JOB_MEMORY_ENFORCE = env.bool("TRELLUM_JOB_MEMORY_ENFORCE", default=True)

# Wall-clock limit for every report build (seconds): SIGTERM at this point,
# SIGKILL 5 s later. Like the memory limit, an operator setting, not a
# report.yaml key.
TRELLUM_RUN_TIMEOUT = env.int("TRELLUM_RUN_TIMEOUT", default=1800)

# Live queries (apps/reports/livequery.py): simultaneous in-process executions
# per web process. Acquired non-blocking — a full pool answers 429 with
# Retry-After rather than queueing a gunicorn thread, which is what keeps the
# endpoint from eating the web tier's headroom under load.
TRELLUM_LIVEQUERY_MAX_CONCURRENT = env.int("TRELLUM_LIVEQUERY_MAX_CONCURRENT", default=4)

# Which half of the worker this process runs: "all" (default — one process
# doing both, the single-node topology), "coordinator" (cron + reaper, exactly
# one), or "runner" (claims and builds, scale to N).
TRELLUM_RUNNER_ROLE = env("TRELLUM_RUNNER_ROLE", default="all")

# Which studio pools this runner serves. Empty (the default) means all of them,
# which is what a single-node install wants. A fleet can dedicate high-memory
# nodes with e.g. TRELLUM_RUNNER_POOLS=large.
TRELLUM_RUNNER_POOLS = env.list("TRELLUM_RUNNER_POOLS", default=[])

# ── Report sandboxing ──────────────────────────────────────────────────────
# How tenant report code is isolated when it builds. Report code is arbitrary
# Python authored outside the portal's trust boundary, so in production each
# build runs in a throwaway sibling container (non-root, read-only rootfs,
# resource-limited, only that run's directories mounted, on a bridge network
# that cannot reach the compose-internal network).
#
#   "docker"  container-per-run (production default, fail-closed at boot)
#   "off"     legacy in-container subprocess — DEV ONLY (see settings/dev.py)
TRELLUM_SANDBOX = env("TRELLUM_SANDBOX", default="docker")
# The runner image the sandbox containers use. Built from the `runner` target
# of the Dockerfile; carries the bundled framework and its deps, nothing else.
TRELLUM_SANDBOX_IMAGE = env("TRELLUM_SANDBOX_IMAGE", default="trellum-runner:dev")
# The user-defined bridge sandbox containers join. Created by the runner if
# absent; segregated from the compose network so tenant code cannot reach
# Postgres/web even with stolen credentials.
TRELLUM_SANDBOX_NETWORK = env("TRELLUM_SANDBOX_NETWORK", default="trellum-sandbox")
# "deny" (default) puts sandboxes on an internal Docker network: report code can
# reach the data sources materialized for it and nothing else. "open" restores
# unrestricted outbound access, which is what a report calling a third-party API
# needs today — and also what lets decrypted warehouse credentials leave the
# host, so it is a deliberate choice rather than a default.
TRELLUM_SANDBOX_EGRESS = env("TRELLUM_SANDBOX_EGRESS", default="deny")
# The Docker volume (or host path) backing /data. Empty = self-discover by
# inspecting the runner's own container; set only for non-standard mounts.
TRELLUM_DATA_VOLUME = env("TRELLUM_DATA_VOLUME", default="")
# Mount the studio project root read-write into the sandbox. Off by default;
# a compat valve for reports that write outside output/. Discouraged.
TRELLUM_SANDBOX_PROJECT_RW = env.bool("TRELLUM_SANDBOX_PROJECT_RW", default=False)
# CPU allowance per sandboxed build (fractional cores).
TRELLUM_SANDBOX_CPUS = env.float("TRELLUM_SANDBOX_CPUS", default=1.0)

# How long an operator may act as another user before the session ends itself.
TRELLUM_IMPERSONATION_MINUTES = env.int("TRELLUM_IMPERSONATION_MINUTES", default=30)

# Optional SMTP for invitation + password-reset mails; invites always show a
# copyable link. EMAIL_URL fills in EMAIL_HOST/PORT/USER/PASSWORD/TLS here; the
# operator can instead configure SMTP in the portal (InstanceConfig), which
# InstanceEmailBackend layers on top of whatever these end up as. With neither,
# that backend writes to the console log, so a reset request can never hang on
# a nonexistent SMTP server.
# NB: Django's default EMAIL_HOST is "localhost", not "" — so "is EMAIL_HOST
# truthy" is NOT a test for "mail is configured". This flag is, and it is what
# InstanceEmailBackend and apps.core.instance.mail_is_configured() key off.
EMAIL_URL_CONFIGURED = bool(env("EMAIL_URL", default=""))
if EMAIL_URL_CONFIGURED:
    vars().update(env.email_url("EMAIL_URL"))
EMAIL_BACKEND = "apps.core.mail.InstanceEmailBackend"

# Build metadata (baked in by the Docker build).
TRELLUM_VERSION_SHA = env("TRELLUM_VERSION_SHA", default="dev")
TRELLUM_VERSION_BUILD_TIME = env("TRELLUM_VERSION_BUILD_TIME", default="")
TRELLUM_VERSION_BRANCH = env("TRELLUM_VERSION_BRANCH", default="")
# Exact Corresponding Source for this build. Official images bake an immutable
# commit URL; forks set their own HTTP(S) location.
TRELLUM_SOURCE_URL = env("TRELLUM_SOURCE_URL", default="")

# ── Documentation links ────────────────────────────────────────────────────
# Where the product sends someone who needs the docs. Empty means the public
# site. An air-gapped install has no route to it and gets the pages as files
# in its bundle instead, so point this at wherever those files are served:
#
#   TRELLUM_DOCS_BASE_URL=https://intranet.example.com/trellum-docs
#
# The version is appended to whatever this is, so a mirror keeps the same
# shape as the public site. TRELLUM_DOCS_VERSION overrides which version is
# linked; leave it unset and the instance links its own release.
TRELLUM_DOCS_BASE_URL = env("TRELLUM_DOCS_BASE_URL", default="")
TRELLUM_DOCS_VERSION = env("TRELLUM_DOCS_VERSION", default="")

# ── Release check ──────────────────────────────────────────────────────────
# Off by default: an outbound call is an audit finding on an air-gapped
# install. When on, the
# coordinator asks the public releases feed once a day and /system lists the
# latest releases; a feed it cannot reach changes nothing and logs nothing.
# The feed itself is a constant (trellum/update_check.py): this is the switch.
TRELLUM_UPDATE_CHECK = env.bool("TRELLUM_UPDATE_CHECK", default=False)

# ── Backups ────────────────────────────────────────────────────────────────
# Where the backup service writes, mounted read-only into the app so the
# `backups` health check and `manage.py restore_drill` can see it. Deliberately
# NOT under TRELLUM_DATA_DIR: a backup on the volume it protects does not survive the
# failure it exists for.
BACKUP_DIR = environ.Path(env("BACKUP_DIR", default="/backups"))
# How stale the newest verified backup may be before the health check fails.
# Default 36h gives a daily backup one missed run of slack before it shouts.
BACKUP_MAX_AGE_HOURS = env.int("BACKUP_MAX_AGE_HOURS", default=36)

# ── Retention ──────────────────────────────────────────────────────────────
# Nothing in this product used to delete anything. Run rows (which are the job
# queue AND the permanent history, and carry two ~50 KB log tails), audit rows,
# assistant transcripts, sessions and expired invitations all grew forever — on a
# single VM whose disk is the thing that fills.
#
# Every window is in days, and every one accepts 0 meaning keep forever: a
# customer under a compliance hold needs that, and a retention policy you
# cannot turn off is one people work around.
RETENTION = {
    # Terminal runs only; an active run is never touched at any age.
    "run_days": env.int("RETENTION_RUN_DAYS", default=90),
    # Newest N per report survive regardless of age, so a quarterly report does
    # not lose its whole history to a 90-day window.
    "run_keep_per_report": env.int("RETENTION_RUN_KEEP_PER_REPORT", default=10),
    # Blank the stdout/stderr tails but keep the row. This is the big win: the
    # tails are most of the bytes, while the row is what history and quota
    # accounting need.
    "run_output_days": env.int("RETENTION_RUN_OUTPUT_DAYS", default=14),
    "audit_days": env.int("RETENTION_AUDIT_DAYS", default=365),
    # Shorter window for the higher-volume categories (auth + access):
    # sign-ins, report views, exports, live queries. authz/admin/system stay
    # under audit_days above -- that is the compliance record. See
    # apps.core.retention.purge_audit and apps.core.audit_actions.
    "audit_access_days": env.int("RETENTION_AUDIT_ACCESS_DAYS", default=90),
    # On by default for a compliance-safe posture: export the doomed window
    # to gzip NDJSON under DATA_DIR/archive/audit/ before purge_audit deletes
    # it, so pruning a retention window never destroys the record outright.
    # DATA_DIR is a durable volume on a self-host install, so the archive
    # survives. (Object-storage-backed archives are a follow-up — S3 has no
    # append, so it needs per-run objects rather than the gzip-append file
    # here; see _write_audit_archive.) Operators who genuinely want hard
    # delete set RETENTION_AUDIT_ARCHIVE=false.
    "audit_archive": env.bool("RETENTION_AUDIT_ARCHIVE", default=True),
    # LlmUsage is never purged — it is a billing ledger, not a log.
    "assistant_session_days": env.int("RETENTION_ASSISTANT_SESSION_DAYS", default=180),
    "invitation_days": env.int("RETENTION_INVITATION_DAYS", default=30),
    "tmp_run_hours": env.int("RETENTION_TMP_RUN_HOURS", default=48),
    # Raw per-view events (internal planning#4); the daily rollup (ReportViewDaily)
    # they feed is never purged -- see apps.core.retention.purge_report_view_events.
    "report_view_event_days": env.int("RETENTION_REPORT_VIEW_EVENT_DAYS", default=90),
    # Built report output. OFF by default: a report's data belongs to whoever
    # made it and stays until they delete the report, the same way a document
    # stays in a document editor. Expiring it on a clock is data loss with a
    # schedule, not retention -- a report opened every week would lose its
    # data because the *build* aged, which is not what "no longer needed"
    # means (GDPR Art. 5(1)(e) is about purpose, and Art. 13(2)(a) accepts
    # criteria rather than a fixed period).
    #
    # Set a non-zero value to opt in: it then acts as BOTH the default and the
    # ceiling, and an organization may choose a shorter window but never a
    # longer one. That is for operators under a retention obligation of their
    # own; nothing in the product needs it.
    #
    # Abandoned data is a different question and is NOT governed here -- a
    # deleted studio's leftovers age out on RETENTION_ABANDONED_UPLOAD_DAYS
    # whatever this is set to, so turning this off never means bytes leak
    # forever. See apps.core.retention.purge_orphaned_data.
    "built_data_days": env.int("RETENTION_BUILT_DATA_DAYS", default=0),
    # Files uploaded as data sources, counted from the day nothing references
    # them any more. A file still attached to a data source never ages. No
    # ceiling: this is housekeeping, not a promise.
    "abandoned_upload_days": env.int("RETENTION_ABANDONED_UPLOAD_DAYS", default=90),
    # Dormant accounts: nobody has signed in for this long, so warn by email,
    # then switch sign-in off, and stop there. The threshold the policy is
    # written around is 730 days (24 months) -- set RETENTION_DORMANT_DAYS=730
    # to run it. OFF by default, and not because 24 months is in doubt: an
    # upgrade that started locking a customer's staff accounts on a timer
    # nobody had been told about would be indistinguishable from a bug, and
    # the accounts it went for first are exactly the ones with the longest
    # history. An operator who owes someone this turns it on deliberately.
    "dormant_days": env.int("RETENTION_DORMANT_DAYS", default=0),
    # Stage two, counted from the day the warning was actually sent (not from
    # the threshold): sign-in is switched off. Nothing is deleted, so this is
    # still reversible, and by default it is the END of the pipeline. 0 stops
    # the policy after the warning -- and because erasure may only follow a
    # disabled account, 0 here turns stage three off with it. See
    # apps.core.retention.disable_dormant_accounts.
    "dormant_disable_days": env.int("RETENTION_DORMANT_DISABLE_DAYS", default=30),
    # Stage three ships OFF: erasing a disabled dormant account is a human
    # decision, made on the org admin's own "erase this person" screen, not a
    # cron job's. A non-zero value opts in to automation -- the account is
    # then erased that many days after stage two, through the same
    # apps.orgs.personal_data.erase the admin's button calls. Irreversible,
    # which is why automating it is the deliberate choice rather than the
    # default.
    "dormant_erase_days": env.int("RETENTION_DORMANT_ERASE_DAYS", default=0),
}
CLEANUP_ENABLED = env.bool("CLEANUP_ENABLED", default=True)
# An odd minute off the top of the hour, so it does not pile up with every
# other cron on the box.
CLEANUP_CRON = env("CLEANUP_CRON", default="17 3 * * *")
# How stale the last cleanup may be before the health check complains.
CLEANUP_MAX_AGE_HOURS = env.int("CLEANUP_MAX_AGE_HOURS", default=48)

# How many reverse proxies sit in front of this instance. 0 (the default) means
# trust nothing: the connection address is used for audit logging, which is
# right for a directly-exposed instance and safe for everyone else, because
# X-Forwarded-For is client-supplied and forgeable. Set it to the number of
# proxies YOU operate — 1 for the documented single Caddy/nginx in front.
TRELLUM_TRUSTED_PROXIES = env.int("TRELLUM_TRUSTED_PROXIES", default=0)

# ── Content Security Policy ────────────────────────────────────────────────
# Applies to the management surface only; built report pages are tenant-authored
# and exempt (see apps/core/middleware.ContentSecurityPolicyMiddleware).
#
# 'unsafe-inline' for styles is load-bearing: the theme system writes inline
# custom properties per page. Scripts do NOT get it — everything the management
# surface runs is a static file.
CONTENT_SECURITY_POLICY = env(
    "CONTENT_SECURITY_POLICY",
    default=(
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
)
# Report-only until an operator has watched their own console for violations. A
# policy that silently breaks the UI is worse than one that reports first.
CSP_REPORT_ONLY = env.bool("CSP_REPORT_ONLY", default=True)

# ── Cache ──────────────────────────────────────────────────────────────────
# Postgres, not Redis. There was no CACHES setting at all, which meant Django's
# per-process LocMemCache: gunicorn runs two workers, so anything cached was a
# coin flip on which one served the request, and login throttling counted
# attempts twice as generously as configured and reset on restart.
#
# A database cache table is slower than Redis and entirely fast enough for what
# this caches, and it keeps the promise that a self-hosted install needs exactly
# one service to operate. Create the table with:
#   manage.py createcachetable
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "trellum_cache",
        "TIMEOUT": 300,
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}

# ── Login throttling ───────────────────────────────────────────────────────
# There was none. A console that reaches every organization on the instance
# accepted unlimited password guesses.
#
# Counted per email AND per client address, because the two attacks look
# different: many passwords against one account, or one password against many
# accounts. Both windows have to close.
LOGIN_MAX_ATTEMPTS = env.int("LOGIN_MAX_ATTEMPTS", default=10)
LOGIN_ATTEMPT_WINDOW_SECONDS = env.int("LOGIN_ATTEMPT_WINDOW_SECONDS", default=900)

# Text by default: the single-VM install reads its logs with `docker compose
# logs`, and JSON there is worse for the human it is for. Set LOG_FORMAT=json
# when shipping them somewhere that parses them.
LOG_FORMAT = env("LOG_FORMAT", default="text")
LOG_LEVEL = env("LOG_LEVEL", default="INFO")
SERVER_LOG_CAPTURE_ENABLED = env.bool("SERVER_LOG_CAPTURE_ENABLED", default=True)
SERVER_LOG_RETENTION_DAYS = env.int("SERVER_LOG_RETENTION_DAYS", default=7)
SERVER_LOG_MAX_ROWS = env.int("SERVER_LOG_MAX_ROWS", default=20_000)
SERVER_LOG_SERVICE = env("SERVER_LOG_SERVICE", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # Adds the current request id to web records; cross-process runs use run_id.
        "request_id": {"()": "apps.core.logging.RequestIdFilter"},
    },
    "formatters": {
        "simple": {
            "format": "{asctime} {levelname} {name} [{request_id}] {message}",
            "style": "{",
        },
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "handlers": {
        "server_logs": {
            "class": "apps.core.server_logs.DatabaseLogHandler",
            "filters": ["request_id"],
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "simple",
            "filters": ["request_id"],
        },
    },
    "root": {"handlers": ["console", "server_logs"], "level": LOG_LEVEL},
    "loggers": {
        # SSO failures must name themselves in the web log (token exchange
        # errors, discovery problems) instead of dying as a bare 401.
        "allauth": {"handlers": ["console", "server_logs"], "level": "DEBUG", "propagate": False},
        # Django logs 4xx at WARNING and 5xx at ERROR here. Without it, a 500 in
        # production reaches the user and appears in no log at all.
        "django.request": {"handlers": ["console", "server_logs"], "level": "WARNING", "propagate": False},
        # Host-header rejections and suspicious operations. These are the first
        # sign of someone probing, and they were previously silent.
        "django.security": {"handlers": ["console", "server_logs"], "level": "WARNING", "propagate": False},
    },
}
