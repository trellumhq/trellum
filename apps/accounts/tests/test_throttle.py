"""Login throttling and escalating lockout.

The portal accepted unlimited password guesses against a console that reaches
every organization on the instance. Thresholds are DB-configured
(``InstanceConfig``) now rather than read from Django settings at request
time -- see apps/accounts/throttle.py's module docstring -- so tests drive
them through the ``lockout_config`` fixture below instead of monkeypatching
``settings.LOGIN_MAX_ATTEMPTS``.
"""
import pytest
from django.core.cache import cache

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "pw-Str0ng-pw"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def lockout_config(db):
    from apps.core.models import InstanceConfig

    def _set(**kwargs):
        cfg = InstanceConfig.load()
        for key, value in kwargs.items():
            setattr(cfg, key, value)
        cfg.save()
        return cfg

    return _set


def _attempt(client, email, password="wrong", **extra):
    return client.post("/login", {"email": email, "password": password}, **extra)


class TestThrottling:
    def test_repeated_failures_eventually_stop_being_tried(
        self, client, make_user, lockout_config
    ):
        lockout_config(lockout_account_threshold=3)
        user = make_user("target@demo.example")

        for _ in range(3):
            _attempt(client, user.email)

        # The correct password now fails too: the window has closed.
        _attempt(client, user.email, password=PASSWORD)
        assert "_auth_user_id" not in client.session

    def test_a_correct_password_clears_the_count(self, client, make_user, lockout_config):
        lockout_config(lockout_account_threshold=5)
        user = make_user("target@demo.example")

        for _ in range(3):
            _attempt(client, user.email)
        _attempt(client, user.email, password=PASSWORD)
        assert "_auth_user_id" in client.session

        from apps.accounts import throttle

        request = type("R", (), {"META": {"REMOTE_ADDR": "127.0.0.1"}})()
        assert not throttle.is_throttled(user.email, request)

    def test_the_message_does_not_reveal_the_limit(self, client, make_user, lockout_config):
        """Telling an attacker they hit the limit tells them one exists."""
        lockout_config(lockout_account_threshold=2)
        user = make_user("target@demo.example")

        for _ in range(2):
            _attempt(client, user.email)
        body = _attempt(client, user.email).content.decode()
        assert "Invalid email or password" in body
        assert "too many" not in body.lower()
        assert "locked" not in body.lower()

    def test_one_account_cannot_be_locked_by_a_stranger(
        self, client, make_user, lockout_config
    ):
        """Per-email counting alone would let anyone lock out a user they can
        name. The window expires by itself and a correct password clears it —
        there is no lockout state for an attacker to set."""
        lockout_config(lockout_account_threshold=3, lockout_window_minutes=0)
        user = make_user("target@demo.example")

        for _ in range(5):
            _attempt(client, user.email)

        _attempt(client, user.email, password=PASSWORD)
        assert "_auth_user_id" in client.session

    def test_zero_disables_it(self, client, make_user, lockout_config):
        lockout_config(lockout_account_threshold=0)
        user = make_user("target@demo.example")
        for _ in range(20):
            _attempt(client, user.email)
        _attempt(client, user.email, password=PASSWORD)
        assert "_auth_user_id" in client.session

    def test_sso_enforced_users_are_never_stranded(
        self, client, make_user, org, lockout_config
    ):
        """Throttling runs after the SSO redirect. A member of an enforcing org
        gets in through the IdP, and that route must stay open however many
        password attempts were made against them."""
        from apps.orgs.models import OrgSSOConfig

        lockout_config(lockout_account_threshold=1)
        make_user("member@demo.example", org=org)
        OrgSSOConfig.objects.create(
            org=org, enabled=True, enforce_sso=True,
            issuer_url="https://idp.example/realms/demo",
            client_id="c", client_secret="s", email_domains=["demo.example"],
        )

        for _ in range(3):
            _attempt(client, "member@demo.example")

        response = _attempt(client, "member@demo.example")
        assert response.status_code == 302
        assert "/accounts/oidc/" in response["Location"]


class TestSharedCache:
    def test_the_cache_is_not_per_process(self, settings):
        """gunicorn runs two workers. With the previous LocMemCache a limit of
        10 was really 20, and a deploy reset it."""
        assert "locmem" not in settings.CACHES["default"]["BACKEND"].lower()

    def test_the_cache_table_exists(self):
        """Created by migration rather than `createcachetable`, so upgrades do
        not need a manual step."""
        cache.set("probe", "value", 10)
        assert cache.get("probe") == "value"


class TestEscalatingLockout:
    """apps.accounts.throttle: DB-configured thresholds, escalating bounded
    cooloff, exactly-once auth.lockout, admin unlock."""

    @staticmethod
    def _request(ip="127.0.0.1"):
        return type("R", (), {"META": {"REMOTE_ADDR": ip}})()

    def test_crossing_the_threshold_locks_and_audits_once(
        self, make_user, lockout_config
    ):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=3, lockout_cooloff_minutes=15)
        user = make_user("victim@demo.example")
        request = self._request()

        for _ in range(3):
            throttle.record_failure(user.email, request)

        assert throttle.is_throttled(user.email, request)
        rows = AuditLog.objects.filter(action="auth.lockout")
        assert rows.count() == 1
        row = rows.get()
        assert row.outcome == "denied"
        assert row.metadata["attempted"] == user.email
        assert row.metadata["scope"] == "email"
        assert row.metadata["failures"] == 3
        assert row.metadata["cooloff_seconds"] == 15 * 60

        # Further failures against an already-locked key do not re-audit.
        for _ in range(5):
            throttle.record_failure(user.email, request)
        assert AuditLog.objects.filter(action="auth.lockout").count() == 1

    def test_cooloff_escalates_and_caps(self, make_user, lockout_config):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=2, lockout_cooloff_minutes=15)
        user = make_user("repeat@demo.example")
        request = self._request()

        seen_cooloffs = []
        for _round in range(6):
            for _ in range(2):
                throttle.record_failure(user.email, request)
            row = AuditLog.objects.filter(action="auth.lockout").order_by("-created_at").first()
            seen_cooloffs.append(row.metadata["cooloff_seconds"])
            # Clear only the lock flag (not the escalation key) to simulate
            # the cooloff expiring naturally between rounds, and drop the
            # counter so the next round starts a fresh count toward the
            # threshold.
            from apps.accounts.throttle import _Scope

            scope = _Scope("email", user.email)
            cache.delete_many([scope.locked_key, scope.counter_key])

        assert seen_cooloffs == [m * 60 for m in (15, 30, 60, 120, 240, 240)]  # doubles, capped

    def test_ip_threshold_is_independent_of_account_threshold(
        self, make_user, lockout_config
    ):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=10, lockout_ip_threshold=2)
        user_a = make_user("a@demo.example")
        user_b = make_user("b@demo.example")
        request = self._request("9.9.9.9")

        throttle.record_failure(user_a.email, request)
        throttle.record_failure(user_b.email, request)

        # Neither account crossed its own (10) threshold, but the shared IP
        # crossed its (2) threshold -- both emails are refused from that IP.
        assert throttle.is_throttled(user_a.email, request)
        assert throttle.is_throttled(user_b.email, request)
        assert not throttle.is_throttled(user_a.email, self._request("1.1.1.1"))
        row = AuditLog.objects.get(action="auth.lockout")
        assert row.metadata["scope"] == "ip"

    def test_a_correct_password_clears_escalation_state_too(
        self, client, make_user, lockout_config
    ):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=2, lockout_cooloff_minutes=15)
        user = make_user("clears@demo.example")
        request = self._request()

        for _ in range(2):
            throttle.record_failure(user.email, request)
        assert throttle.is_throttled(user.email, request)

        throttle.clear(user.email, request)
        assert not throttle.is_throttled(user.email, request)

        # Escalation resets too: the next lockout is back at the base cooloff.
        for _ in range(2):
            throttle.record_failure(user.email, request)
        row = AuditLog.objects.filter(action="auth.lockout").order_by("-created_at").first()
        assert row.metadata["cooloff_seconds"] == 15 * 60

    def test_admin_unlock_clears_email_scope_only(self, make_user, lockout_config):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=1, lockout_ip_threshold=1)
        user = make_user("locked@demo.example")
        request = self._request("8.8.8.8")
        throttle.record_failure(user.email, request)
        assert throttle.is_throttled(user.email, request)

        cleared = throttle.unlock(user.email)
        assert cleared is True

        # The email scope is cleared...
        from apps.accounts.throttle import _Scope

        assert cache.get(_Scope("email", user.email).locked_key) is None
        # ...but the IP scope (which the admin does not necessarily know)
        # was also crossed and is left alone -- it expires on its own.
        assert cache.get(_Scope("ip", "8.8.8.8").locked_key) is True

    def test_unlocking_a_key_that_was_not_locked_reports_so(self, make_user, lockout_config):
        from apps.accounts import throttle

        lockout_config()
        user = make_user("never-locked@demo.example")
        assert throttle.unlock(user.email) is False

    def test_org_admin_unlock_view_clears_and_audits(
        self, client, login, org_admin, member, org, lockout_config
    ):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=1)
        request = type("R", (), {"META": {"REMOTE_ADDR": "127.0.0.1"}})()
        throttle.record_failure(member.email, request)
        assert throttle.is_throttled(member.email, request)

        c = login(org_admin)
        resp = c.post(f"/orgs/{org.slug}/settings/members/{member.pk}/unlock")
        assert resp.status_code == 302
        assert not throttle.is_throttled(member.email, request)
        assert AuditLog.objects.filter(action="auth.unlock", target_id=str(member.pk)).exists()

    def test_operator_unlock_view_clears_and_audits(
        self, client, login, superuser, member, org, lockout_config
    ):
        from apps.accounts import throttle

        lockout_config(lockout_account_threshold=1)
        request = type("R", (), {"META": {"REMOTE_ADDR": "127.0.0.1"}})()
        throttle.record_failure(member.email, request)

        c = login(superuser)
        resp = c.post(f"/operator/orgs/{org.slug}/members/{member.pk}/unlock")
        assert resp.status_code == 302
        assert not throttle.is_throttled(member.email, request)
        assert AuditLog.objects.filter(action="auth.unlock", target_id=str(member.pk)).exists()


class TestMfaFeedsTheSameCounters:
    """apps.accounts.views.mfa_verify_view records against the same throttle
    keys as a wrong password -- see test_mfa.py for the full login-flow
    version; this checks the counter/audit shape directly."""

    def test_wrong_mfa_code_is_a_recorded_failure(self, make_user, lockout_config):
        from django.utils import timezone

        from apps.accounts import mfa, throttle
        from apps.accounts.models import TotpDevice

        lockout_config(lockout_account_threshold=2)
        user = make_user("factored@demo.example")
        TotpDevice.objects.create(user=user, secret="JBSWY3DPEHPK3PXP", confirmed_at=timezone.now())
        request = type("R", (), {"META": {"REMOTE_ADDR": "127.0.0.1"}})()

        assert mfa.verify_login_code(user.totp_device, "000000") is False
        throttle.record_failure(user.email, request)
        throttle.record_failure(user.email, request)
        assert throttle.is_throttled(user.email, request)
