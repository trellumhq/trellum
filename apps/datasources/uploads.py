"""Reversible file changes for a data-source database transaction."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


class UploadChange:
    """Keep the previous inode open until the database transaction completes.

    Unlinking/replacing its name does not discard its bytes while this handle
    is open. A failed request restores it using bounded streaming; a successful
    request closes it without leaving a backup in the studio project. This
    compensates for request/commit errors, not abrupt process or machine loss.
    """

    def __init__(self):
        self.original: Path | None = None
        self.previous = None
        self.original_changed = False
        self.created: Path | None = None

    def __enter__(self):
        return self

    def _remember(self, current):
        self.original = current
        if current is not None:
            try:
                self.previous = current.open("rb")
            except FileNotFoundError:
                pass

    def remove(self, current):
        self._remember(current)
        if current is not None:
            current.unlink(missing_ok=True)
            self.original_changed = self.previous is not None

    def replace(self, staged, target, current):
        self._remember(current)
        if target == current and self.previous is not None:
            os.replace(staged, target)
            self.original_changed = True
        else:
            # A link publishes the complete staged file atomically and refuses
            # an existing destination, including one created by another source
            # after validation. Never overwrite an unrelated file on disk.
            os.link(staged, target)
            self.created = target
            if current is not None and current != target:
                current.unlink(missing_ok=True)
                self.original_changed = self.previous is not None

    def __exit__(self, kind, value, traceback):
        try:
            if kind is not None:
                if self.original_changed:
                    # Restore by rename so readers never see a partial copy.
                    with tempfile.NamedTemporaryFile(dir=self.original.parent, delete=False) as restored:
                        temporary = Path(restored.name)
                        try:
                            self.previous.seek(0)
                            shutil.copyfileobj(self.previous, restored, length=1024 * 1024)
                            os.fchmod(restored.fileno(), os.fstat(self.previous.fileno()).st_mode)
                            restored.flush()
                            os.replace(temporary, self.original)
                        finally:
                            temporary.unlink(missing_ok=True)
                if self.created is not None:
                    self.created.unlink(missing_ok=True)
        finally:
            if self.previous is not None:
                self.previous.close()
