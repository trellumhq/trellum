"""The production-readiness check.

These are configuration choices that are correct for an evaluation and wrong
for production, so nothing else surfaces them. Each is otherwise discovered at
the worst possible moment: the bundled database when a disk fills, DEBUG when a
traceback reaches an end user.
"""
import pytest

from apps.core.health import run_checks

pytestmark = pytest.mark.django_db


def _row():
    return next(c for c in run_checks() if c["label"] == "production readiness")


@pytest.fixture
def verified_backup(tmp_path, settings):
    """A recent, verified backup — so the bundled-database branch reports what
    it is relying on rather than failing. The no-backup case lives in
    test_backups.py, which owns that behaviour."""
    import json
    from datetime import datetime, timedelta, timezone

    directory = tmp_path / "backups"
    directory.mkdir()
    when = datetime.now(timezone.utc) - timedelta(hours=1)
    (directory / "status.json").write_text(json.dumps({
        "last_success_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": True, "detail": "verified", "off_host_copy": True,
    }), encoding="utf-8")
    settings.BACKUP_DIR = directory
    return directory


@pytest.fixture
def production(settings):
    """A deployment that should pass."""
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["trellum.example.com"]
    settings.PORTAL_BASE_URL = "https://bi.example.com"
    settings.DATABASES = {
        **settings.DATABASES,
        "default": {**settings.DATABASES["default"], "HOST": "db.internal"},
    }
    return settings


class TestProductionReadiness:
    def test_a_well_configured_instance_passes(self, production):
        assert _row()["ok"] is True

    def test_development_is_skipped_not_failed(self, settings):
        settings.DEBUG = True
        row = _row()
        assert row["ok"] is True
        assert "skipped" in row["detail"]

    @pytest.mark.parametrize("host", ["db", "localhost", "127.0.0.1", ""])
    def test_the_bundled_database_is_noted_but_not_a_failure(
        self, production, settings, host, verified_backup
    ):
        """One VM running web, worker and db together is the supported default.

        Failing this check condemned the topology the product recommends — and
        the backup service only knows how to back up that same bundled
        database, so the advice pointed away from the one thing it could
        protect. The fault is not the bundled database; it is having nothing to
        restore from, which is covered in test_backups.py.
        """
        settings.DATABASES = {
            **settings.DATABASES,
            "default": {**settings.DATABASES["default"], "HOST": host},
        }
        row = _row()
        assert row["ok"] is True
        assert "bundled database" in row["detail"]
        assert "the whole recovery story" in row["detail"]

    def test_wildcard_allowed_hosts_is_flagged(self, production, settings):
        settings.ALLOWED_HOSTS = ["*"]
        row = _row()
        assert row["ok"] is False
        assert "ALLOWED_HOSTS" in row["detail"]

    def test_plain_http_base_url_is_flagged(self, production, settings):
        settings.PORTAL_BASE_URL = "http://bi.example.com"
        row = _row()
        assert row["ok"] is False
        assert "plain HTTP" in row["detail"]

    def test_localhost_over_http_is_not_flagged(self, production, settings):
        """A proxy terminating TLS in front of 127.0.0.1 is the documented
        deployment, not a mistake."""
        settings.PORTAL_BASE_URL = "http://localhost:8050"
        assert _row()["ok"] is True

    def test_every_problem_is_reported_at_once(self, production, settings):
        """Fixing one thing only to be told about the next is a bad loop."""
        settings.ALLOWED_HOSTS = ["*"]
        settings.PORTAL_BASE_URL = "http://bi.example.com"
        detail = _row()["detail"]
        assert "ALLOWED_HOSTS" in detail
        assert "plain HTTP" in detail

    def test_a_real_problem_still_fails_alongside_the_bundled_db_note(
        self, production, settings
    ):
        """The note must not swallow an actual fault."""
        settings.ALLOWED_HOSTS = ["*"]
        settings.DATABASES = {
            **settings.DATABASES,
            "default": {**settings.DATABASES["default"], "HOST": "db"},
        }
        row = _row()
        assert row["ok"] is False
        assert "ALLOWED_HOSTS" in row["detail"]
