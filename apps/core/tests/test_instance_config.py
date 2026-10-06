"""The instance configuration singleton: base URL resolution, SMTP, mail backend."""
import pytest

from apps.core.forms import InstanceSettingsForm
from apps.core.instance import (
    base_url,
    mail_is_configured,
    max_upload_bytes,
    setup_is_complete,
)
from apps.core.mail import InstanceEmailBackend
from apps.core.models import InstanceConfig

pytestmark = pytest.mark.django_db


class TestSingleton:
    def test_load_is_idempotent(self):
        assert InstanceConfig.load().pk == InstanceConfig.load().pk == 1

    def test_saving_a_second_row_collapses_onto_the_first(self):
        InstanceConfig.load()
        InstanceConfig(instance_name="Other").save()
        assert InstanceConfig.objects.count() == 1
        assert InstanceConfig.load().instance_name == "Other"

    def test_the_row_cannot_be_deleted(self):
        with pytest.raises(NotImplementedError):
            InstanceConfig.load().delete()


class TestBaseUrl:
    def test_falls_back_to_the_setting(self, settings):
        settings.PORTAL_BASE_URL = "http://localhost:8050"
        assert base_url() == "http://localhost:8050"

    def test_database_value_wins(self, settings):
        settings.PORTAL_BASE_URL = "http://localhost:8050"
        row = InstanceConfig.load()
        row.public_base_url = "https://bi.demo.example"
        row.save()
        assert base_url() == "https://bi.demo.example"

    def test_trailing_slash_is_trimmed(self):
        row = InstanceConfig.load()
        row.public_base_url = "https://bi.demo.example/"
        row.save()
        assert base_url() == "https://bi.demo.example"

    def test_invitation_links_follow_it(self, org, settings):
        from apps.accounts.models import Invitation

        settings.PORTAL_BASE_URL = "http://localhost:8050"
        row = InstanceConfig.load()
        row.public_base_url = "https://bi.demo.example"
        row.save()
        invite = Invitation.objects.create(org=org, email="new@demo.example")
        assert invite.accept_url().startswith("https://bi.demo.example/invite/")


class TestSecretAtRest:
    def test_smtp_password_round_trips_and_is_encrypted_in_the_column(self):
        row = InstanceConfig.load()
        row.email_host_password = "smtp-secret"
        row.save()
        assert InstanceConfig.load().email_host_password == "smtp-secret"

        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT email_host_password FROM core_instanceconfig WHERE id = 1")
            stored = cur.fetchone()[0]
        assert stored.startswith("enc$1$")
        assert "smtp-secret" not in stored


class TestMailConfiguration:
    """Django defaults EMAIL_HOST to "localhost", never "". Every check below
    exists because "is EMAIL_HOST truthy" would answer yes on a box where
    nobody configured anything — claiming invitations get mailed when they are
    being written to the container log."""

    def test_unconfigured_instance_reports_no_mail(self, settings):
        settings.EMAIL_URL_CONFIGURED = False
        assert settings.EMAIL_HOST == "localhost"  # the trap this guards
        assert mail_is_configured() is False

    def test_database_smtp_counts_as_configured(self, settings):
        settings.EMAIL_URL_CONFIGURED = False
        row = InstanceConfig.load()
        row.email_host = "mail.demo.example"
        row.save()
        assert mail_is_configured() is True

    def test_env_smtp_still_counts(self, settings):
        settings.EMAIL_URL_CONFIGURED = True
        assert mail_is_configured() is True

    def test_backend_writes_to_the_console_with_nothing_configured(self, settings):
        settings.EMAIL_URL_CONFIGURED = False
        backend = InstanceEmailBackend()
        assert backend._console is not None

    def test_backend_uses_smtp_when_email_url_was_set(self, settings):
        settings.EMAIL_URL_CONFIGURED = True
        assert InstanceEmailBackend()._console is None

    def test_backend_prefers_the_database(self, settings):
        settings.EMAIL_URL_CONFIGURED = False
        settings.EMAIL_HOST = "env.example.com"
        row = InstanceConfig.load()
        row.email_host = "db.demo.example"
        row.email_port = 2525
        row.email_host_user = "portal"
        row.email_host_password = "smtp-secret"
        row.email_use_tls = True
        row.save()

        backend = InstanceEmailBackend()
        assert backend._console is None
        assert backend.host == "db.demo.example"
        assert backend.port == 2525
        assert backend.username == "portal"
        assert backend.password == "smtp-secret"
        assert backend.use_tls is True
        assert backend.use_ssl is False


class TestSettingsForm:
    def test_blank_password_keeps_the_stored_one(self):
        row = InstanceConfig.load()
        row.email_host_password = "smtp-secret"
        row.save()

        form = InstanceSettingsForm(
            {
                "instance_name": "Demo BI",
                "email_host": "mail.demo.example",
                "email_port": "587",
                "email_from": "bi@demo.example",
                "email_host_password": "",
            },
            instance=InstanceConfig.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        assert InstanceConfig.load().email_host_password == "smtp-secret"

    def test_max_upload_mb_round_trips(self):
        form = InstanceSettingsForm(
            {"instance_name": "Demo BI", "email_port": "587", "max_upload_mb": "2048"},
            instance=InstanceConfig.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        assert InstanceConfig.load().max_upload_mb == 2048
        assert max_upload_bytes() == 2048 * 1024 * 1024

    def test_omitting_max_upload_mb_keeps_the_stored_one(self):
        """The first-run wizard posts this form without knowing about the
        limit; that must not silently reset it."""
        row = InstanceConfig.load()
        row.max_upload_mb = 100
        row.save()
        form = InstanceSettingsForm(
            {"instance_name": "Demo BI", "email_port": "587"},
            instance=InstanceConfig.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        assert InstanceConfig.load().max_upload_mb == 100

    def test_zero_means_no_limit(self):
        form = InstanceSettingsForm(
            {"instance_name": "Demo BI", "email_port": "587", "max_upload_mb": "0"},
            instance=InstanceConfig.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        assert max_upload_bytes() == 0

    def test_stored_password_is_never_rendered(self):
        row = InstanceConfig.load()
        row.email_host_password = "smtp-secret"
        row.save()
        assert "smtp-secret" not in str(InstanceSettingsForm(instance=InstanceConfig.load()))


def test_setup_is_complete_tracks_the_stamp():
    assert setup_is_complete() is False
    row = InstanceConfig.load()
    row.setup_completed_at = "2026-01-01T00:00:00Z"
    row.save()
    assert setup_is_complete() is True


class TestSecurityDefaults:
    """internal planning#78: out-of-the-box defaults must reproduce today's
    behaviour, with the one documented exception (the 30-day absolute cap —
    see apps/accounts/session_policy.py)."""

    def test_session_defaults_reproduce_todays_behaviour(self):
        row = InstanceConfig.load()
        assert row.session_idle_minutes == 720  # 12h — prod's existing SESSION_COOKIE_AGE
        assert row.session_absolute_hours == 720  # 30d — the one behavioural change
        assert row.session_expire_at_browser_close is False
        assert row.session_remember_me_enabled is False
        assert row.sessions_invalidated_at is None

    def test_lockout_thresholds_seed_from_the_env_fallback(self, settings):
        """LOGIN_MAX_ATTEMPTS / LOGIN_ATTEMPT_WINDOW_SECONDS become the DB
        defaults for a fresh install -- an operator who already tuned the env
        var keeps the same effective limit the day this becomes DB-governed."""
        settings.LOGIN_MAX_ATTEMPTS = 7
        settings.LOGIN_ATTEMPT_WINDOW_SECONDS = 600
        row = InstanceConfig()
        row.save()
        assert row.lockout_account_threshold == 7
        assert row.lockout_window_minutes == 10

    def test_require_mfa_operators_defaults_off(self):
        assert InstanceConfig.load().require_mfa_operators is False


class TestSecuritySettingsForm:
    def test_security_settings_round_trip(self):
        from apps.core.forms import SecuritySettingsForm

        form = SecuritySettingsForm(
            {
                "session_idle_minutes": "60",
                "session_absolute_hours": "24",
                "session_expire_at_browser_close": "on",
                "lockout_account_threshold": "5",
                "lockout_ip_threshold": "20",
                "lockout_window_minutes": "10",
                "lockout_cooloff_minutes": "20",
                "require_mfa_operators": "on",
            },
            instance=InstanceConfig.load(),
        )
        assert form.is_valid(), form.errors
        form.save()
        row = InstanceConfig.load()
        assert row.session_idle_minutes == 60
        assert row.session_absolute_hours == 24
        assert row.session_expire_at_browser_close is True
        assert row.lockout_account_threshold == 5
        assert row.require_mfa_operators is True
