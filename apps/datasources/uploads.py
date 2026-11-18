"""Reversible file changes for a data-source database transaction."""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from django.db import connection

logger = logging.getLogger(__name__)


class UploadRecoveryError(OSError):
    """Rollback could not restore the original path; its recovery link remains."""


@contextmanager
def file_scope_lock(*, studio=None, org=None):
    """Nonblocking cross-process guard, held through commit and file rollback.

    Share the worker's studio lock so repository declaration mirroring cannot
    introduce an alias during mutation. All portal file-setting writers use
    this guard too. The second lock serializes the actual storage root,
    including organization sources, without locking every source row.
    """
    from apps.runner.gitsync import studio_sync_lock

    with ExitStack() as stack:
        if studio is not None and not stack.enter_context(studio_sync_lock(studio.pk)):
            yield False
            return
        root = studio.project_root if studio is not None else org.datasources_dir
        key = int.from_bytes(hashlib.sha256(os.fsencode(root.resolve())).digest()[:8], "big", signed=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
            acquired = cursor.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


class UploadChange:
    """Retain the previous inode under a private name until the commit finishes.

    A same-directory hard link preserves bytes without allocating a second copy.
    Rollback restores that link by rename, including when the disk is full.
    This compensates for request/commit errors, not abrupt process or machine loss.
    """

    def __init__(self):
        self.original: Path | None = None
        self.backup: Path | None = None
        self.original_changed = False
        self.created: Path | None = None

    def __enter__(self):
        return self

    def _remember(self, current):
        self.original = current
        if current is not None:
            backup = current.with_name(f".{current.name}.{uuid.uuid4().hex}.previous")
            try:
                os.link(current, backup)
            except FileNotFoundError:
                pass
            else:
                self.backup = backup

    def remove(self, current):
        self._remember(current)
        if current is not None:
            current.unlink(missing_ok=True)
            self.original_changed = self.backup is not None

    def replace(self, staged, target, current):
        self._remember(current)
        if target == current and self.backup is not None:
            os.replace(staged, target)
            self.original_changed = True
        else:
            # Atomic publication refuses an unrelated destination, including
            # one created after validation by another upload.
            os.link(staged, target)
            self.created = target
            if current is not None and current != target:
                current.unlink(missing_ok=True)
                self.original_changed = self.backup is not None

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            if self.original_changed:
                # If restoration itself fails, retain the recovery link.
                try:
                    os.replace(self.backup, self.original)
                except OSError as exc:
                    logger.exception("Upload rollback failed; original contents retained at %s", self.backup)
                    raise UploadRecoveryError("Upload rollback needs administrator recovery") from exc
                self.backup = None
            if self.created is not None:
                self.created.unlink(missing_ok=True)
        if self.backup is not None:
            try:
                self.backup.unlink(missing_ok=True)
            except OSError:
                # Cleanup failure does not undo a completed commit or prevent
                # a truthful response after a successful restoration.
                logger.exception("Could not clean up upload recovery link %s", self.backup)
