"""Studios: the sharing boundary. Reports, data sources, memberships, and the
reports git repo all hang off a studio. A studio's durable files live under
``<DATA_DIR>/studios/<org_slug>/<studio_slug>/``.
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import models

from apps.core import roles
from apps.core.crypto import EncryptedTextField
from apps.orgs.models import ImmutableSlugMixin, Organization, slug_validator

#: Runner pools. A runner serves the pools named in TRELLUM_RUNNER_POOLS (empty =
#: all), so a single box runs one process serving everything while a fleet can
#: dedicate high-memory nodes to the "large" pool. Studios, not reports, carry
#: the assignment: a studio's git checkout lives on the node that builds it.
POOL_SMALL = "small"
POOL_STANDARD = "standard"
POOL_LARGE = "large"
POOL_CHOICES = [
    (POOL_SMALL, "Small"),
    (POOL_STANDARD, "Standard"),
    (POOL_LARGE, "Large"),
]


class Studio(ImmutableSlugMixin):
    AUDIENCE_STUDIO = "studio"
    AUDIENCE_PRIVATE = "private"
    AUDIENCE_CHOICES = ((AUDIENCE_STUDIO, "Studio audience"), (AUDIENCE_PRIVATE, "Private"))

    org = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="studios")
    slug = models.SlugField(max_length=64, validators=[slug_validator])
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=400, blank=True)
    default_report_audience = models.CharField(
        max_length=16, choices=AUDIENCE_CHOICES, default=AUDIENCE_STUDIO, db_default=AUDIENCE_STUDIO
    )
    default_analysis_audience = models.CharField(
        max_length=16, choices=AUDIENCE_CHOICES, default=AUDIENCE_STUDIO, db_default=AUDIENCE_STUDIO
    )
    pool = models.CharField(max_length=32, choices=POOL_CHOICES, default=POOL_STANDARD)
    #: The studio's own default look -- recolors the studio's whole chrome
    #: (management pages AND report content, see apps.core.themes.
    #: resolve_studio_theme) for every member who has not picked a personal
    #: override. "" = inherit the organization's default. Set by a studio
    #: admin (the tab-rail chip); not a model ``choices`` constraint because
    #: ``trellum.themes.THEME_REGISTRY`` is extensible at runtime -- validity
    #: is checked at request time instead (the setter view and the resolver).
    theme = models.CharField(max_length=64, blank=True, default="")
    #: The studio's theme as DECLARED BY ITS OWN REPOSITORY (project-root
    #: config.yaml `theme:`), stamped by apps.reports.scan.sync_studio_registry
    #: from the file apps.runner.gitsync mirrors in alongside events.yaml/
    #: metrics.yaml. Absent/empty file -> "". Outranks `theme` above and
    #: every viewer/org pick (apps.core.themes.explicit_studio_theme rung 0)
    #: -- once a repo declares one, nothing in the portal UI can override it,
    #: only the repo's own config.yaml can. NOT validated against
    #: THEME_REGISTRY here: it may name a theme the repo registers itself
    #: (trellum.themes.register_theme) that only exists inside that repo's
    #: own build process. A name outside the registry still pins the report
    #: CONTENT (it was built with real CSS for it) but the management
    #: CHROME cannot render it and falls back to Trellum -- see
    #: apps.core.themes.explicit_studio_theme and
    #: apps.reports.views._inject_report_chrome.
    repo_theme = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["org", "slug"], name="uniq_studio_slug_per_org")
        ]

    def __str__(self) -> str:
        return f"{self.org.slug}/{self.slug}"

    # ── Disk layout (single source of truth for every path) ──────────────
    @property
    def data_root(self) -> Path:
        return Path(settings.DATA_DIR) / "studios" / self.org.slug / self.slug

    @property
    def repo_dir(self) -> Path:
        """Git checkout of the studio's reports repository."""
        return self.data_root / "repo"

    @property
    def project_root(self) -> Path:
        """FW_PROJECT_ROOT for this studio's report builds."""
        return self.data_root / "project"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    @property
    def output_dir(self) -> Path:
        return self.project_root / "output"

    @property
    def datasources_dir(self) -> Path:
        return self.project_root / "data-sources"

    def ensure_dirs(self) -> None:
        for p in (self.reports_dir, self.output_dir, self.datasources_dir / "files"):
            p.mkdir(parents=True, exist_ok=True)


class StudioMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="studio_memberships"
    )
    studio = models.ForeignKey(Studio, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=roles.STUDIO_ROLE_CHOICES)
    # Retained for compatibility with the previous release's ORM queries.
    # Active theme preferences are stored in StudioPreference.
    theme = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "studio"], name="uniq_studio_membership")
        ]

    def __str__(self) -> str:
        return f"{self.user} @ {self.studio} ({self.role})"


class StudioPreference(models.Model):
    """Appearance preference, stored independently from access grants."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="studio_preferences"
    )
    studio = models.ForeignKey(Studio, on_delete=models.CASCADE, related_name="preferences")
    theme = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "studio"], name="uniq_studio_preference")
        ]


class StudioRepo(models.Model):
    """Where this studio's reports come from: a git repository.

    This is the sacred pipeline's configuration: push to the repo, the worker
    pulls, re-scans, and (optionally) rebuilds what changed.
    """

    AUTH_CHOICES = [("https_token", "HTTPS + token"), ("none", "Public / no auth")]
    PUBLISH_CHOICES = [("auto", "Publish every push"), ("manual", "Review, then publish")]
    SYNC_REASONS = [("schedule", "schedule"), ("manual", "manual"), ("webhook", "webhook")]

    studio = models.OneToOneField(Studio, on_delete=models.CASCADE, related_name="repo")
    repo_url = models.URLField(help_text="HTTPS clone URL of the reports repository.")
    branch = models.CharField(max_length=100, default="main")
    path = models.CharField(
        max_length=200, default="reports",
        help_text="Directory inside the repository that holds the report folders.",
    )
    auth_method = models.CharField(max_length=16, choices=AUTH_CHOICES, default="https_token")
    token = EncryptedTextField(blank=True, default="")
    sync_interval_minutes = models.PositiveIntegerField(
        default=5, help_text="0 disables polling (manual / webhook sync only)."
    )
    auto_run_changed = models.BooleanField(
        default=False, help_text="Rebuild reports whose files changed in a pulled commit."
    )
    webhook_secret = EncryptedTextField(blank=True, default="")
    # Set by the web process ("Sync now" button, push webhook); the worker's
    # git thread picks it up within seconds and clears it.
    sync_requested = models.BooleanField(default=False)
    #: What set sync_requested; the worker stamps it on the publish it causes.
    sync_reason = models.CharField(max_length=16, choices=SYNC_REASONS, default="schedule")
    #: ``auto``: every fetched change is published. ``manual``: fetches only
    #: record what is pending until someone requests a publish.
    publish_mode = models.CharField(max_length=8, choices=PUBLISH_CHOICES, default="auto")
    #: Head of origin/<branch> at the last fetch, and what publishing it would
    #: change relative to last_synced_sha (see gitsync.StudioGitSync.fetch).
    remote_sha = models.CharField(max_length=64, blank=True)
    remote_checked_at = models.DateTimeField(null=True, blank=True)
    pending_changes = models.JSONField(default=dict, blank=True)
    publish_requested = models.BooleanField(default=False)
    publish_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", db_index=False,
    )
    #: The remote head the request reviewed; a fetch that finds another one
    #: drops the request instead of publishing what nobody looked at.
    publish_requested_to = models.CharField(max_length=64, blank=True)
    #: One-shot replacement for auto_run_changed on the next publish.
    publish_rebuild_override = models.BooleanField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    #: The commit the project dir was last published from.
    last_synced_sha = models.CharField(max_length=64, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self) -> None:
        """Block path/branch values that could inject git arguments before
        they are ever stored — the settings form calls full_clean()."""
        from django.core.exceptions import ValidationError

        from apps.runner.gitsync import validate_repo_fields

        try:
            validate_repo_fields(self.path, self.branch)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def __str__(self) -> str:
        return f"repo({self.studio}, {self.repo_url})"


class RepoPublish(models.Model):
    """One attempt to bring the project dir up to a commit of the studio's
    repository. ``summary`` is the pending-changes structure the publish
    consumed; an ``error`` row leaves the project dir where it was."""

    TRIGGER_CHOICES = [
        ("auto", "auto"), ("manual", "manual"), ("webhook", "webhook"), ("initial", "initial"),
    ]
    STATUS_CHOICES = [("ok", "ok"), ("error", "error")]

    studio = models.ForeignKey(Studio, on_delete=models.CASCADE, related_name="publishes")
    from_sha = models.CharField(max_length=64, blank=True)
    to_sha = models.CharField(max_length=64)
    published_at = models.DateTimeField(auto_now_add=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", db_index=False,
    )
    trigger = models.CharField(max_length=8, choices=TRIGGER_CHOICES)
    summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default="ok")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-published_at"]
        indexes = [models.Index(fields=["studio", "published_at"], name="repopublish_studio_at")]

    def __str__(self) -> str:
        return f"publish({self.studio}, {self.to_sha[:10]}, {self.status})"
