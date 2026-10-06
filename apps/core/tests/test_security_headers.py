"""Production security settings, and the CSP boundary.

prod.py had gone untouched since the portal was built: no HSTS, no referrer
policy, no CSP, and cookie-secure derived from a string prefix with no way to
correct it. These assert the settings module rather than a live response,
because the settings module is the artifact that ships.
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from django.conf import settings as django_settings

#: Settings are read from the environment at import time, so an in-process
#: reload cannot answer "what would prod.py do with this .env" — base.py has
#: already captured the old values. A subprocess imports it cleanly, which is
#: also exactly how it is evaluated in production.
_PROBE = (
    "import json, django.conf;"
    "s = django.conf.settings;"
    "print(json.dumps({k: getattr(s, k, None) for k in ("
    "'SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE','SECURE_HSTS_SECONDS',"
    "'SECURE_HSTS_INCLUDE_SUBDOMAINS','SECURE_HSTS_PRELOAD',"
    "'SECURE_REFERRER_POLICY','SECURE_REDIRECT_EXEMPT','SECURE_SSL_REDIRECT',"
    "'SESSION_COOKIE_AGE','SESSION_SAVE_EVERY_REQUEST')}))"
)


def _prod(**env):
    child = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "trellum_portal.settings.prod",
        "SESSION_SECRET_KEY": "x" * 50,
        "SECRET_ENCRYPTION_KEY": "5oyYd0zpeq5F1zqLBTXCzCUJ9WQ0P84qBcNPXuMUsUE=",
        **env,
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=child,
        cwd=str(django_settings.BASE_DIR),
    )
    if result.returncode != 0:
        pytest.fail(f"prod settings failed to import:\n{result.stderr[-2000:]}")
    return SimpleNamespace(**json.loads(result.stdout))


class TestHttpsDeployment:
    @staticmethod
    @pytest.fixture(scope="class")
    def prod():
        return _prod(PORTAL_BASE_URL="https://bi.example.com")

    def test_cookies_are_secure(self, prod):
        assert prod.SESSION_COOKIE_SECURE is True
        assert prod.CSRF_COOKIE_SECURE is True

    def test_hsts_is_on_for_a_year(self, prod):
        assert prod.SECURE_HSTS_SECONDS == 31536000

    def test_subdomains_and_preload_stay_opt_in(self, prod):
        """The portal may share a parent domain with hosts we know nothing
        about, and preload is close to irreversible."""
        assert prod.SECURE_HSTS_INCLUDE_SUBDOMAINS is False
        assert prod.SECURE_HSTS_PRELOAD is False

    def test_referrer_policy_does_not_leak_slugs(self, prod):
        """Report URLs contain org and studio slugs, and describe what a
        customer measures."""
        assert prod.SECURE_REFERRER_POLICY == "same-origin"

    def test_healthz_is_exempt_from_ssl_redirect(self, prod):
        """The compose healthcheck hits http://127.0.0.1:8050/healthz inside
        the container; redirecting it would fail the container."""
        assert r"^healthz$" in prod.SECURE_REDIRECT_EXEMPT

    def test_sessions_are_not_two_weeks(self, prod):
        assert prod.SESSION_COOKIE_AGE <= 60 * 60 * 24
        assert prod.SESSION_SAVE_EVERY_REQUEST is True


class TestPlainHttpDeployment:
    @staticmethod
    @pytest.fixture(scope="class")
    def prod():
        return _prod(PORTAL_BASE_URL="http://bi.internal")

    def test_hsts_is_off(self, prod):
        """Sending HSTS over plain HTTP is meaningless, and sending it from a
        host that must keep serving HTTP locks it out of browsers."""
        assert prod.SECURE_HSTS_SECONDS == 0

    def test_cookies_are_not_marked_secure(self, prod):
        assert prod.SESSION_COOKIE_SECURE is False

    def test_an_operator_can_override_the_derived_value(self):
        """TLS may terminate in front in a way the base URL does not describe.
        Previously this was a string prefix check with no escape hatch."""
        prod = _prod(
            PORTAL_BASE_URL="http://bi.internal",
            SESSION_COOKIE_SECURE="true",
        )
        assert prod.SESSION_COOKIE_SECURE is True


@pytest.mark.django_db
class TestContentSecurityPolicy:
    def test_the_management_surface_gets_a_policy(self, client, make_user):
        client.force_login(make_user("someone@demo.example"))
        response = client.get("/")
        assert response["Content-Security-Policy-Report-Only"]

    def test_it_is_report_only_by_default(self, client):
        """A policy that silently breaks a customer's console is worse than one
        that reports first."""
        response = client.get("/login")
        assert "Content-Security-Policy-Report-Only" in response
        assert "Content-Security-Policy" not in response

    def test_enforcing_mode_switches_the_header(self, client, settings):
        settings.CSP_REPORT_ONLY = False
        response = client.get("/login")
        assert response["Content-Security-Policy"]

    def test_scripts_are_not_allowed_inline(self, client):
        """Everything the management surface runs is a static file. Styles keep
        unsafe-inline because the theme system writes custom properties."""
        policy = client.get("/login")["Content-Security-Policy-Report-Only"]
        assert "script-src 'self'" in policy
        assert "script-src 'self' 'unsafe-inline'" not in policy

    def test_framing_is_denied(self, client):
        policy = client.get("/login")["Content-Security-Policy-Report-Only"]
        assert "frame-ancestors 'none'" in policy

    def test_report_content_is_exempt_by_url_name(self, client, make_user, org, studio):
        """Built report pages are tenant-authored HTML and JavaScript; a policy
        strict enough to be worth having would break them.

        Matched on the resolved URL name, not a path substring — a substring
        test would exempt anything containing "/r/", and would drift silently
        the next time a URL shape changed.
        """
        from apps.core.middleware import ContentSecurityPolicyMiddleware

        assert ContentSecurityPolicyMiddleware._EXEMPT == {
            "report-page", "report-asset", "vendor-asset", "share-entry", "share-asset",
        }

    def test_share_routes_are_exempt(self, client, db):
        """The public share routes serve the same tenant-authored output
        (internal planning ticket #92). An unknown token still resolves to the share view, so
        the 404 is enough to prove the exemption is by URL name."""
        response = client.get("/share/no-such-token/")
        assert response.status_code == 404
        assert "Content-Security-Policy-Report-Only" not in response

    def test_a_lookalike_path_is_still_covered(self, client, make_user):
        """`/orgs/<slug>/...` contains no report route, and a path-substring
        exemption is exactly the kind of thing that would have let one slip."""
        client.force_login(make_user("someone@demo.example"))
        response = client.get("/account")
        assert response["Content-Security-Policy-Report-Only"]


class TestSandboxEgress:
    """Report code is arbitrary tenant Python with decrypted warehouse
    credentials injected into it."""

    def test_egress_is_denied_by_default(self, settings):
        assert settings.TRELLUM_SANDBOX_EGRESS == "deny"

    def test_an_internal_network_reports_denied(self, settings, monkeypatch):
        import apps.core.health as health

        settings.TRELLUM_SANDBOX_EGRESS = "deny"
        monkeypatch.setattr(
            health, "_inspect_sandbox_network", lambda: {"Internal": True}
        )
        assert "denied (internal network)" in health._sandbox_egress_state()

    def test_a_non_internal_network_is_called_misconfigured(self, settings, monkeypatch):
        """Docker cannot flip `internal` in place, so a network created by an
        older release stays open however the setting reads. Saying 'denied'
        there would be a lie the operator acts on."""
        import apps.core.health as health

        settings.TRELLUM_SANDBOX_EGRESS = "deny"
        monkeypatch.setattr(
            health, "_inspect_sandbox_network", lambda: {"Internal": False}
        )
        assert "MISCONFIGURED" in health._sandbox_egress_state()

    def test_no_docker_socket_is_not_a_failure(self, settings, monkeypatch):
        """The web container has no socket, which is the normal case."""
        import apps.core.health as health

        settings.TRELLUM_SANDBOX_EGRESS = "deny"
        monkeypatch.setattr(health, "_inspect_sandbox_network", lambda: None)
        assert "not verifiable from here" in health._sandbox_egress_state()

    def test_opening_egress_is_reported_loudly(self, settings):
        """It is a legitimate choice, but never an invisible one."""
        from apps.core.health import _sandbox_egress_state

        settings.TRELLUM_SANDBOX_EGRESS = "open"
        assert "OPEN" in _sandbox_egress_state()
