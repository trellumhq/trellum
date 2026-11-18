"""Reversible file changes for a data-source database transaction."""
from __future__ import annotations

import os
import uuid
from pathlib import Path


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
                os.replace(self.backup, self.original)
                self.backup = None
            if self.created is not None:
                self.created.unlink(missing_ok=True)
        if self.backup is not None:
            self.backup.unlink(missing_ok=True)
