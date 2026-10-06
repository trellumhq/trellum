"""The backups health check.

The bundled database has no replication and no point-in-time recovery, so for
the default topology the nightly dump is the entire recovery story. The classic
way to discover a backup system has been dead for six weeks is to need it, so
these tests are mostly about the check being loud in each of the ways backups
fail quietly.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.core.health import run_checks

pytestmark = pytest.mark.django_db


def _row(label="backups"):
    return next(c for c in run_checks() if c["label"] == label)


def _write_status(directory, *, ok=True, age_hours=1.0, off_host=False, detail="verified"):
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    (directory / "status.json").write_text(json.dumps({
        "last_attempt_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "detail": detail,
        "keep_days": 14,
        "off_host_copy": off_host,
    }), encoding="utf-8")


@pytest.fixture
def backup_dir(tmp_path, settings):
    directory = tmp_path / "backups"
    directory.mkdir()
    settings.BACKUP_DIR = directory
    return directory


class TestBackupsCheck:
    def test_recent_verified_backup_passes(self, backup_dir):
        _write_status(backup_dir, age_hours=2)
        row = _row()
        assert row["ok"] is True
        assert "verified 2h ago" in row["detail"]

    def test_missing_directory_fails(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path / "nope"
        row = _row()
        assert row["ok"] is False
        assert "no backup directory" in row["detail"]

    def test_never_run_fails(self, backup_dir):
        """The directory exists but the loop has never completed — a stack that
        was brought up without its backup service."""
        row = _row()
        assert row["ok"] is False
        assert "not completed a run" in row["detail"]

    def test_stale_backup_fails(self, backup_dir, settings):
        """The failure this whole check exists for: backups stopped, and
        nothing said so."""
        settings.BACKUP_MAX_AGE_HOURS = 36
        _write_status(backup_dir, age_hours=72)
        row = _row()
        assert row["ok"] is False
        assert "72h old" in row["detail"]

    def test_last_run_failed_is_reported_even_if_recent(self, backup_dir):
        _write_status(backup_dir, ok=False, age_hours=2, detail="pg_dump failed")
        row = _row()
        assert row["ok"] is False
        assert "pg_dump failed" in row["detail"]

    def test_host_only_backups_pass_but_say_so(self, backup_dir):
        """Plenty of installs accept this. It must not be invisible: it is the
        difference between a backup and disaster recovery."""
        _write_status(backup_dir, off_host=False)
        row = _row()
        assert row["ok"] is True
        assert "this host only" in row["detail"]
        assert "BACKUP_REMOTE_TARGET" in row["detail"]

    def test_off_host_copy_is_reported(self, backup_dir):
        _write_status(backup_dir, off_host=True)
        row = _row()
        assert row["ok"] is True
        assert "off-host copy" in row["detail"]

    def test_unreadable_status_does_not_raise(self, backup_dir):
        """run_checks() promises never to raise; /system must not 500 because a
        file got truncated."""
        (backup_dir / "status.json").write_text("{ not json", encoding="utf-8")
        row = _row()
        assert row["ok"] is False


class TestRestoreDrillReporting:
    """`pg_restore --list` proves a file is a well-formed archive. Only a
    restore proves it contains a portal, so whether one has ever been run is
    part of the backup picture."""

    def test_never_drilled_is_said_out_loud(self, backup_dir):
        _write_status(backup_dir)
        row = _row()
        assert row["ok"] is True
        assert "never restore-tested" in row["detail"]

    def test_a_passing_drill_is_reported_with_its_age(self, backup_dir):
        _write_status(backup_dir)
        when = datetime.now(timezone.utc) - timedelta(days=5)
        (backup_dir / "drill.json").write_text(json.dumps({
            "last_drill_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ok": True, "dump": "db-x.dump",
        }), encoding="utf-8")
        row = _row()
        assert row["ok"] is True
        assert "restore-tested 5d ago" in row["detail"]

    def test_a_failed_drill_fails_the_check(self, backup_dir):
        """Backups that restore to an empty database are worse than none: they
        look like protection right up until the day they are needed."""
        _write_status(backup_dir)
        (backup_dir / "drill.json").write_text(json.dumps({
            "last_drill_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ok": False, "dump": "db-x.dump",
        }), encoding="utf-8")
        row = _row()
        assert row["ok"] is False
        assert "restore drill FAILED" in row["detail"]


class TestNewestDump:
    def test_picks_the_latest_by_name(self, backup_dir):
        from apps.core import backups

        for name in ["db-20260101-0300.dump", "db-20260315-0300.dump",
                     "db-20260210-0300.dump"]:
            (backup_dir / name).write_bytes(b"x")
        assert backups.newest_dump().name == "db-20260315-0300.dump"

    def test_none_when_empty(self, backup_dir):
        from apps.core import backups

        assert backups.newest_dump() is None


class TestProductionReadinessUsesBackups:
    """The bundled database is supported; running it with nothing to restore
    from is not."""

    @pytest.fixture(autouse=True)
    def _production(self, settings):
        # Otherwise the test settings' own ALLOWED_HOSTS=["*"] and http base URL
        # fail this check for unrelated reasons.
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["trellum.example.com"]
        settings.PORTAL_BASE_URL = "https://bi.example.com"
        settings.DATABASES = {
            **settings.DATABASES,
            "default": {**settings.DATABASES["default"], "HOST": "db"},
        }

    def test_bundled_db_without_any_backup_is_a_failure(self, backup_dir):
        row = _row("production readiness")
        assert row["ok"] is False
        assert "nothing to restore from" in row["detail"]

    def test_bundled_db_with_a_backup_is_only_a_note(self, backup_dir):
        _write_status(backup_dir, age_hours=2, off_host=True)
        row = _row("production readiness")
        assert row["ok"] is True
        assert "supported" in row["detail"]
