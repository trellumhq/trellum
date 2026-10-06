"""Reading the state of the backup system.

The backup loop (``docker/backup.sh``) writes ``status.json`` after every run.
This module is the only reader, shared by the ``backups`` health check and
``manage.py restore_drill``.

Why the portal cares at all: the bundled database is the supported default
topology, and it has no replication and no point-in-time recovery. The nightly
dumps are the entire recovery story for those installs, and a backup system
nobody is watching is one that stopped weeks ago. So "when did a verified backup
last succeed" is a health question, not a filesystem detail.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class BackupState:
    configured: bool          # is there a backup directory at all?
    last_success_at: datetime | None
    ok: bool                  # did the last run succeed?
    detail: str
    off_host_copy: bool
    newest_dump: Path | None

    @property
    def age_hours(self) -> float | None:
        if self.last_success_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.last_success_at
        return delta.total_seconds() / 3600


def _parse_stamp(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def backup_dir() -> Path:
    return Path(str(settings.BACKUP_DIR))


def newest_dump() -> Path | None:
    """The most recent database dump, by filename.

    Names are ``db-YYYYMMDD-HHMM.dump``, so lexical order is chronological —
    and unlike mtime it survives a copy that did not preserve timestamps.
    """
    directory = backup_dir()
    if not directory.is_dir():
        return None
    dumps = sorted(directory.glob("db-*.dump"))
    return dumps[-1] if dumps else None


def last_drill() -> tuple[datetime | None, bool]:
    """``(when, passed)`` for the last restore drill, from ``drill.json``.

    Written by ``docker/restore_drill.sh``. Absent means nobody has ever proved
    these backups restore, which is worth saying out loud but is not a failure
    on its own — plenty of installs never run one.
    """
    drill_file = backup_dir() / "drill.json"
    if not drill_file.is_file():
        return None, False
    try:
        raw = json.loads(drill_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    return _parse_stamp(raw.get("last_drill_at")), bool(raw.get("ok"))


def state() -> BackupState:
    """Never raises. An unreadable backup directory is an answer."""
    directory = backup_dir()
    status_file = directory / "status.json"
    if not directory.is_dir():
        return BackupState(False, None, False, "no backup directory", False, None)

    if not status_file.is_file():
        # The directory exists but the loop has never completed a run — a
        # freshly started stack, or a backup service that died before its first
        # attempt. Distinguishable from "never configured".
        return BackupState(
            True, None, False, "the backup service has not completed a run yet",
            False, newest_dump(),
        )

    try:
        raw = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return BackupState(True, None, False, f"status.json unreadable: {exc}", False, None)

    return BackupState(
        configured=True,
        last_success_at=_parse_stamp(raw.get("last_success_at")),
        ok=bool(raw.get("ok")),
        detail=str(raw.get("detail") or ""),
        off_host_copy=bool(raw.get("off_host_copy")),
        newest_dump=newest_dump(),
    )
