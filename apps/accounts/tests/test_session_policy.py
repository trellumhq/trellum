"""Session lifetime enforcement: idle timeout, absolute cap, retroactive
policy changes, instance-wide and per-user revocation, legacy-session
adoption. See .lavish/session-security-design.md §3 and
apps/accounts/session_policy.py.

Time is controlled by monkeypatching ``session_policy._now`` (a seam built
for exactly this — the suite carries no freezegun dependency).
"""
from __future__ import annotations

import pytest

from apps.accounts import session_policy as sp
from apps.core.models import AuditLog, InstanceConfig

pytestmark = pytest.mark.django_db

PASSWORD = "pw-Str0ng-pw"


@pytest.fixture(autouse=True)
def _clean_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


class _Clock:
    """A controllable clock for session_policy._now, advanced explicitly.

    Starts near the REAL wall clock (not an arbitrary fixed epoch) because
    sessions_invalidated_at (apps.core.models.InstanceConfig) is stamped with
    the genuine django.utils.timezone.now(), never the mocked seam -- an
    arbitrary past start would make every login compare as "before" any
    real invalidation timestamp, which is a test artifact, not the
    behaviour under test.
    """

    def __init__(self, start: int | None = None):
        import time

        self.t = start if start is not None else int(time.time())

    def __call__(self) -> int:
        return self.t

    def advance(self, seconds: int) -> None:
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(sp, "_now", c)
    return c


def _login(client, user, password=PASSWORD):
    resp = client.post("/login", {"email": user.email, "password": password})
    assert resp.status_code == 302
    return resp


class TestLoginStamping:
    def test_login_writes_the_three_stamps(self, client, member, clock):
        _login(client, member)
        session = client.session
        assert session["sp_login_at"] == clock.t
        assert session["sp_last_seen"] == clock.t
        assert session["sp_epoch"] == member.auth_epoch
        assert session["sp_remember"] is False
        assert session["sp_via_sso"] is False

    def test_login_creates_a_usersession_row(self, client, member, clock):
        _login(client, member)
        from apps.accounts.models import UserSession

        row = UserSession.objects.get(user=member)
        assert row.session_key == client.session.session_key
        assert row.remember_me is False


class TestIdleTimeout:
    def test_within_the_window_stays_authenticated(self, client, member, clock):
        InstanceConfig.load()  # default idle: 720 minutes
        _login(client, member)
        clock.advance(600)  # 10 minutes
        assert client.get("/api/me").json()["authenticated"] is True

    def test_past_the_window_is_expired_with_reason_idle(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 5
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        clock.advance(6 * 60)

        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json() == {"error": "session_expired", "reason": "idle"}
        assert "_auth_user_id" not in client.session

    def test_html_request_redirects_to_login_with_reason_and_next(
        self, client, member, clock
    ):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 5
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        clock.advance(6 * 60)

        resp = client.get("/account")
        assert resp.status_code == 302
        assert resp.url.startswith("/login?")
        assert "reason=idle" in resp.url
        assert "next=%2Faccount" in resp.url

    def test_expiry_writes_a_logout_audit_row(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 5
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        AuditLog.objects.filter(action="auth.login").delete()
        clock.advance(6 * 60)
        client.get("/api/me")
        assert AuditLog.objects.filter(action="auth.logout").exists()

    def test_zero_disables_idle_enforcement(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 0
        cfg.session_absolute_hours = 0
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        clock.advance(10_000_000)  # a very long time
        assert client.get("/api/me").json()["authenticated"] is True

    def test_activity_keeps_the_session_alive_within_a_shorter_window(
        self, client, member, clock
    ):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 5
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        for _ in range(5):
            clock.advance(4 * 60)  # under the 5-minute idle window each time
            assert client.get("/api/me").json()["authenticated"] is True


class TestAbsoluteCap:
    def test_continuous_activity_still_expires_at_the_absolute_cap(
        self, client, member, clock
    ):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 60  # generous idle window
        cfg.session_absolute_hours = 1
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)

        # Stay active well within the idle window, but cross the 1h absolute cap.
        for _ in range(3):
            clock.advance(20 * 60)
            assert client.get("/api/me").json()["authenticated"] is True

        clock.advance(20 * 60)  # total elapsed now > 1h
        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json()["reason"] == "expired"

    def test_zero_disables_the_absolute_cap(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 0
        cfg.session_absolute_hours = 0
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        clock.advance(1_000_000_000)
        assert client.get("/api/me").json()["authenticated"] is True


class TestRetroactiveEnforcement:
    def test_tightening_the_policy_applies_on_the_very_next_request(
        self, client, member, clock
    ):
        """No sweep is ever run: the middleware checks stamps against the
        CURRENT policy every request, so a session that was fine a moment ago
        is refused the instant the operator saves a tighter setting."""
        _login(client, member)  # default idle: 720 minutes
        clock.advance(120)
        assert client.get("/api/me").json()["authenticated"] is True

        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 1
        cfg.save()
        sp.invalidate_policy_cache()
        clock.advance(120)  # 2 minutes since last_seen, over the new 1-minute cap

        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json()["reason"] == "idle"


class TestInstanceWideInvalidation:
    def test_sign_everyone_out_kills_every_stamped_session(self, client, member, clock):
        _login(client, member)
        clock.advance(5)
        sp.sign_everyone_out()

        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json()["reason"] == "revoked"

    def test_a_session_that_logs_in_after_the_invalidation_survives_it(
        self, client, member, clock
    ):
        sp.sign_everyone_out()
        clock.advance(5)
        _login(client, member)
        clock.advance(5)
        assert client.get("/api/me").json()["authenticated"] is True

    def test_the_system_page_action_invalidates_every_session(
        self, client, member, superuser, clock
    ):
        from django.test import Client

        _login(client, member)
        clock.advance(5)

        # A separate client: the `login` fixture reuses the same underlying
        # `client`, which would silently swap out member's session for the
        # admin's in-place rather than exercising two independent sessions.
        admin_client = Client()
        assert admin_client.login(username=superuser.email, password=PASSWORD)
        resp = admin_client.post("/system", {"action": "sign_everyone_out"})
        assert resp.status_code == 302

        assert client.get("/api/me").status_code == 401


class TestPerUserRevocation:
    def test_sign_out_others_spares_the_initiating_session(self, member, clock):
        from django.test import Client

        client_a = Client()
        client_b = Client()
        assert client_a.login(username=member.email, password=PASSWORD)
        clock.advance(1)
        assert client_b.login(username=member.email, password=PASSWORD)
        clock.advance(1)

        resp = client_a.post("/account", {"action": "sign_out_others"})
        assert resp.status_code == 302

        assert client_a.get("/api/me").json()["authenticated"] is True
        resp_b = client_b.get("/api/me")
        assert resp_b.status_code == 401
        assert resp_b.json()["reason"] == "revoked"

    def test_sign_out_others_audits_and_bumps_the_epoch(self, client, member, clock):
        _login(client, member)
        before = member.auth_epoch
        client.post("/account", {"action": "sign_out_others"})
        member.refresh_from_db()
        assert member.auth_epoch == before + 1
        assert AuditLog.objects.filter(action="auth.session_revoked", target_id=str(member.pk)).exists()

    def test_revoke_one_session_from_the_account_page(self, member, clock):
        from django.test import Client

        from apps.accounts.models import UserSession

        client_a = Client()
        client_b = Client()
        assert client_a.login(username=member.email, password=PASSWORD)
        clock.advance(1)
        assert client_b.login(username=member.email, password=PASSWORD)
        clock.advance(1)

        b_key = client_b.session.session_key
        resp = client_a.post("/account", {"action": "revoke_session", "session_key": b_key})
        assert resp.status_code == 302
        assert not UserSession.objects.filter(session_key=b_key).exists()
        assert client_b.get("/api/me").status_code == 401
        # A's own session is untouched.
        assert client_a.get("/api/me").json()["authenticated"] is True

    def test_cannot_revoke_someone_elses_session(self, member, org_admin, clock):
        from django.test import Client

        client_member = Client()
        client_admin = Client()
        assert client_member.login(username=member.email, password=PASSWORD)
        assert client_admin.login(username=org_admin.email, password=PASSWORD)

        member_key = client_member.session.session_key
        resp = client_admin.post(
            "/account", {"action": "revoke_session", "session_key": member_key}
        )
        assert resp.status_code == 302
        # Still alive -- an admin cannot revoke another user's session from
        # their own account page (that is the operator/org-member surfaces'
        # job, keyed by explicit admin privilege, not this endpoint).
        assert client_member.get("/api/me").json()["authenticated"] is True


class TestLegacySessionAdoption:
    def test_a_stampless_session_is_adopted_not_killed(self, client, member, clock):
        """A session predating this feature (or created by a path that
        bypassed the login signal) must not be force-logged-out on deploy."""
        session = client.session
        session["_auth_user_id"] = str(member.pk)
        session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        session["_auth_user_hash"] = member.get_session_auth_hash()
        session.save()
        assert "sp_login_at" not in client.session

        resp = client.get("/api/me")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is True
        assert client.session["sp_login_at"] == clock.t
        assert client.session["sp_last_seen"] == clock.t


class TestRememberMe:
    def test_remember_me_uses_its_own_idle_window(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 1
        cfg.session_remember_me_enabled = True
        cfg.session_remember_me_days = 1  # 24h
        cfg.save()
        sp.invalidate_policy_cache()

        resp = client.post(
            "/login", {"email": member.email, "password": PASSWORD, "remember_me": "1"}
        )
        assert resp.status_code == 302
        assert client.session["sp_remember"] is True

        clock.advance(30 * 60)  # past the 1-minute idle window, well under 24h
        assert client.get("/api/me").json()["authenticated"] is True

    def test_remember_me_is_ignored_when_the_instance_has_not_enabled_it(
        self, client, member, clock
    ):
        """The checkbox is deferred v1 UI; even a raw POST of the field name
        must not grant the longer window unless an operator turned it on."""
        cfg = InstanceConfig.load()
        cfg.session_idle_minutes = 1
        cfg.save()
        sp.invalidate_policy_cache()

        client.post(
            "/login", {"email": member.email, "password": PASSWORD, "remember_me": "1"}
        )
        assert client.session["sp_remember"] is False
        clock.advance(2 * 60)
        assert client.get("/api/me").status_code == 401


class TestBrowserClose:
    def test_expire_at_browser_close_sets_a_session_cookie(self, client, member, clock):
        cfg = InstanceConfig.load()
        cfg.session_expire_at_browser_close = True
        cfg.save()
        sp.invalidate_policy_cache()
        _login(client, member)
        assert client.session.get_expire_at_browser_close() is True


class TestImpersonationInteraction:
    def test_impersonation_gets_its_own_fresh_stamps(
        self, client, member, superuser, org, clock
    ):
        client.login(username=superuser.email, password=PASSWORD)
        clock.advance(500)

        from django.test import override_settings

        with override_settings(TRELLUM_IMPERSONATION_ENABLED=True):
            resp = client.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")
        assert resp.status_code == 302
        # The impersonated session is a NEW session, stamped at the moment
        # impersonation started -- not inherited from the operator's login.
        assert client.session["sp_login_at"] == clock.t
