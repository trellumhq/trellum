"""Inspect this portal instance's studio files after Django has initialized."""
from __future__ import annotations

import logging

from django.db import DatabaseError

logger = logging.getLogger("trellum.studios")

_FILE_STATUS_PREFIXES = ("Studio files missing.", "Studio files need restoring.")


def check_studio_files_at_startup() -> None:
    """Record missing published files without fetching or publishing a repository.

    Run at the web entry point, not AppConfig.ready(): migrations and other
    management commands must remain usable before the schema exists. A failed
    database connection must not prevent the web process from starting.
    """
    from apps.runner.gitsync import studio_sync_lock
    from apps.runner.studio_files import check_studio_files
    from apps.studios.models import StudioRepo

    try:
        repos = (
            StudioRepo.objects.exclude(repo_url="").exclude(last_synced_sha="")
            .select_related("studio", "studio__org")
        )
        for repo in repos.iterator(chunk_size=100):
            # Sharing the sync mutex prevents a partial copy from becoming a
            # persistent startup warning after a same-commit restore finishes.
            with studio_sync_lock(repo.studio_id) as acquired:
                if not acquired:
                    continue
                repo.refresh_from_db(fields=["last_synced_sha", "last_error"])
                health = check_studio_files(repo)
                if health["state"] in {"missing", "mismatch"}:
                    logger.warning("%s: %s", repo.studio, health["message"])
                    # Keep a failed sync's explanation. Live status independently
                    # exposes missing files alongside any operational error.
                    if repo.last_error and not repo.last_error.startswith(_FILE_STATUS_PREFIXES):
                        continue
                    message = health["message"]
                elif health["ready"] and repo.last_error.startswith(_FILE_STATUS_PREFIXES):
                    message = ""
                else:
                    continue
                # A publish may have completed since inspection; never overwrite
                # its newer result with an observation of the previous checkout.
                StudioRepo.objects.filter(
                    pk=repo.pk, last_synced_sha=repo.last_synced_sha, last_error=repo.last_error,
                ).update(last_error=message)
    except DatabaseError:
        logger.warning("Studio file startup check deferred: database unavailable.")
