"""Authentication in the audit trail, and the client IP that makes it useful.

The trail used to describe every consequence of an intrusion and nothing about
the intrusion: no login, no logout, no failed attempt, and an `ip` column that
recorded the reverse proxy for every user alike.
"""
import pytest

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "pw-Str0ng-pw"


class TestAuthEvents:
    def test_a_successful_login_is_recorded(self, client, make_user):
        user = make_user("someone@demo.example")
        client.post("/login", {"email": user.email, "password": PASSWORD})

        row = AuditLog.objects.get(action="auth.login")
        assert row.actor_id == user.pk
        assert row.outcome == "success"
        assert row.category == "auth"

    def test_a_password_login_carries_its_method_and_no_provider(self, client, make_user):
        user = make_user("someone@demo.example")
        client.post("/login", {"email": user.email, "password": PASSWORD})

        row = AuditLog.objects.get(action="auth.login")
        assert row.metadata["method"] == "password"
        assert "provider" not in row.metadata

    def test_an_sso_login_carries_its_method_and_provider(self, rf, make_user, org):
        """auth_events._logged_in reads a request attribute
        OrgSSOAdapter.pre_social_login stamps before allauth's own login()
        call reaches the signal -- a full SSO round-trip is exercised
        elsewhere (apps/accounts/tests/test_sso*.py); this is the narrower
        unit test for the signal receiver's own translation of that flag."""
        from django.contrib.auth import signals as auth_signals
        from django.contrib.sessions.backends.db import SessionStore

        user = make_user("sso.user@demo.example", org=org)
        request = rf.get("/accounts/oidc/oidc-demo/login/callback/")
        # A real session, not a bare dict: apps.accounts.session_policy also
        # listens on this signal and calls session.set_expiry(), same as it
        # would on a genuine request.
        request.session = SessionStore()
        request._trellum_sso_provider = "oidc-demo"

        auth_signals.user_logged_in.send(sender=user.__class__, request=request, user=user)

        row = AuditLog.objects.get(action="auth.login")
        assert row.metadata["method"] == "sso"
        assert row.metadata["provider"] == "oidc-demo"

    def test_a_failed_login_records_what_was_tried(self, client, make_user):
        """The interesting case is an address that does not exist — someone
        guessing — so the attempted identifier is the payload, not a user."""
        make_user("someone@demo.example")
        client.post("/login", {"email": "someone@demo.example", "password": "wrong"})

        row = AuditLog.objects.get(action="auth.login_failed")
        assert row.metadata["attempted"] == "someone@demo.example"
        assert row.actor_id is None
        assert row.outcome == "failure"
        assert row.category == "auth"

    def test_repeated_failures_each_leave_a_row(self, client, make_user):
        """Brute force is a pattern over rows; collapsing them hides it."""
        make_user("someone@demo.example")
        for _ in range(3):
            client.post("/login", {"email": "someone@demo.example", "password": "no"})
        assert AuditLog.objects.filter(action="auth.login_failed").count() == 3

    def test_logout_is_recorded(self, client, make_user):
        user = make_user("someone@demo.example")
        client.force_login(user)
        client.post("/logout")

        row = AuditLog.objects.get(action="auth.logout")
        assert row.actor_id == user.pk

    def test_impersonation_does_not_also_log_a_login(self, client, make_user, org):
        """impersonation.start() calls login(), which would otherwise write a
        bare auth.login by the target next to operator.impersonate.start and
        read as if they signed in themselves."""
        from django.test import override_settings

        operator = make_user("ops@portal.local")
        operator.is_operator_flag = True
        operator.save(update_fields=["is_operator_flag"])
        member = make_user("member@demo.example", org=org)

        client.force_login(operator)
        AuditLog.objects.all().delete()
        with override_settings(TRELLUM_IMPERSONATION_ENABLED=True):
            client.post(f"/operator/orgs/{org.slug}/impersonate/{member.pk}")

        assert not AuditLog.objects.filter(action="auth.login").exists()
        assert AuditLog.objects.filter(action="operator.impersonate.start").exists()


class TestClientIp:
    def test_untrusted_by_default_uses_the_connection(self, rf, settings):
        """X-Forwarded-For is client-supplied. Trusting it by default would let
        anyone write whatever address they liked into the audit trail, which is
        worse than a consistently wrong one."""
        from apps.core.net import client_ip

        settings.TRELLUM_TRUSTED_PROXIES = 0
        request = rf.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.9")
        assert client_ip(request) == "10.0.0.9"

    def test_one_trusted_proxy_reads_through_it(self, rf, settings):
        from apps.core.net import client_ip

        settings.TRELLUM_TRUSTED_PROXIES = 1
        request = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.7", REMOTE_ADDR="10.0.0.9")
        assert client_ip(request) == "203.0.113.7"

    def test_a_spoofed_prefix_is_ignored(self, rf, settings):
        """The client can prepend anything; only the hops our own proxy appended
        mean anything."""
        from apps.core.net import client_ip

        settings.TRELLUM_TRUSTED_PROXIES = 1
        request = rf.get(
            "/",
            HTTP_X_FORWARDED_FOR="9.9.9.9, 203.0.113.7",
            REMOTE_ADDR="10.0.0.9",
        )
        assert client_ip(request) == "203.0.113.7"

    def test_garbage_falls_back_to_the_connection(self, rf, settings):
        from apps.core.net import client_ip

        settings.TRELLUM_TRUSTED_PROXIES = 1
        request = rf.get("/", HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="10.0.0.9")
        assert client_ip(request) == "10.0.0.9"

    def test_audit_rows_carry_it(self, client, make_user, settings):
        settings.TRELLUM_TRUSTED_PROXIES = 1
        user = make_user("someone@demo.example")
        client.post(
            "/login", {"email": user.email, "password": PASSWORD},
            HTTP_X_FORWARDED_FOR="203.0.113.7",
        )
        assert AuditLog.objects.get(action="auth.login").ip == "203.0.113.7"
