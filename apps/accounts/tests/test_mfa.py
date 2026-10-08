"""First-party TOTP MFA: enrollment, recovery codes, replay guard, the login
step-up, org/instance enforcement (grace and policy persistence),
and admin reset. See .lavish/session-security-design.md §4 and
apps/accounts/mfa.py.
"""
from __future__ import annotations

import re
from datetime import timedelta

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts import mfa
from apps.accounts.models import RecoveryCode, TotpDevice
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

PASSWORD = "pw-Str0ng-pw"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _confirmed_device(user):
    device = mfa.begin_enrollment(user)
    totp = pyotp.TOTP(device.secret)
    codes = mfa.confirm_enrollment(device, totp.now())
    device.refresh_from_db()
    # Confirmation itself consumes the "now" step under the replay guard
    # (correctly -- see TestReplayAndDrift). Reset the baseline so tests that
    # build on this fixture and then immediately generate another "now" or
    # "now - 1 step" code aren't tripped by confirmation's own consumption,
    # which is an implementation detail of THIS helper, not of the scenario
    # under test.
    device.last_used_step = 0
    device.save(update_fields=["last_used_step"])
    return device, codes


class TestEnrollment:
    def test_begin_enrollment_creates_an_unconfirmed_device(self, member):
        device = mfa.begin_enrollment(member)
        assert device.confirmed_at is None
        assert member.has_mfa is False

    def test_secret_is_encrypted_at_rest(self, member):
        device = mfa.begin_enrollment(member)
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT secret FROM accounts_totpdevice WHERE id = %s", [device.pk])
            stored = cur.fetchone()[0]
        assert stored.startswith("enc$1$")
        assert device.secret not in stored

    def test_re_beginning_replaces_the_secret(self, member):
        first = mfa.begin_enrollment(member)
        second = mfa.begin_enrollment(member)
        assert first.secret != second.secret
        assert TotpDevice.objects.filter(user=member).count() == 1

    def test_confirm_with_the_right_code_enables_it_and_mints_recovery_codes(self, member):
        device = mfa.begin_enrollment(member)
        totp = pyotp.TOTP(device.secret)
        codes = mfa.confirm_enrollment(device, totp.now())

        assert codes is not None and len(codes) == 10
        assert len(set(codes)) == 10  # all distinct
        for code in codes:
            assert re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$", code)
        member.refresh_from_db()
        assert member.has_mfa is True
        assert RecoveryCode.objects.filter(user=member).count() == 10

    def test_confirm_with_the_wrong_code_stays_unconfirmed(self, member):
        device = mfa.begin_enrollment(member)
        assert mfa.confirm_enrollment(device, "000000") is None
        device.refresh_from_db()
        assert device.confirmed_at is None
        assert member.has_mfa is False

    def test_qr_svg_is_inline_markup(self, member):
        device = mfa.begin_enrollment(member)
        uri = mfa.provisioning_uri(member, device, issuer_name="trellum")
        assert uri.startswith("otpauth://totp/")
        svg = mfa.qr_svg(uri)
        assert svg.strip().startswith("<svg")
        assert "<?xml" not in svg  # inline, not a standalone document

    def test_enrollment_view_confirms_and_audits(self, client, login, member):
        c = login(member)
        device = mfa.begin_enrollment(member)
        totp = pyotp.TOTP(device.secret)
        resp = c.post("/account/security/mfa/setup", {"code": totp.now()})
        assert resp.status_code == 302 and resp.url == "/account/security/mfa/recovery-codes"
        member.refresh_from_db()
        assert member.has_mfa is True
        assert AuditLog.objects.filter(action="auth.mfa_enrolled", target_id=str(member.pk)).exists()

    def test_recovery_codes_are_shown_exactly_once(self, client, login, member):
        c = login(member)
        device = mfa.begin_enrollment(member)
        totp = pyotp.TOTP(device.secret)
        c.post("/account/security/mfa/setup", {"code": totp.now()})

        first = c.get("/account/security/mfa/recovery-codes")
        assert first.status_code == 200
        second = c.get("/account/security/mfa/recovery-codes")
        assert second.status_code == 302  # nothing left to show; bounced to /account


class TestReplayAndDrift:
    def test_a_code_verifies_once_then_is_refused(self, member):
        device, _codes = _confirmed_device(member)
        totp = pyotp.TOTP(device.secret)
        code = totp.now()
        assert mfa.verify_login_code(device, code) is True
        device.refresh_from_db()
        assert mfa.verify_login_code(device, code) is False  # replay refused

    def test_one_step_of_drift_is_accepted(self, member):
        device, _codes = _confirmed_device(member)
        totp = pyotp.TOTP(device.secret)
        earlier = totp.at(timezone.now().timestamp() - 30)
        assert mfa.verify_login_code(device, earlier) is True

    def test_two_steps_of_drift_is_refused(self, member):
        device, _codes = _confirmed_device(member)
        totp = pyotp.TOTP(device.secret)
        too_old = totp.at(timezone.now().timestamp() - 90)
        assert mfa.verify_login_code(device, too_old) is False

    def test_an_unconfirmed_device_never_verifies(self, member):
        device = mfa.begin_enrollment(member)
        totp = pyotp.TOTP(device.secret)
        assert mfa.verify_login_code(device, totp.now()) is False


class TestRecoveryCodes:
    def test_a_code_works_once(self, member):
        _device, codes = _confirmed_device(member)
        code = codes[0]
        assert mfa.verify_recovery_code(member, code) is not None
        assert mfa.verify_recovery_code(member, code) is None  # already used

    def test_remaining_count_decreases(self, member):
        _device, codes = _confirmed_device(member)
        assert mfa.remaining_recovery_codes(member) == 10
        mfa.verify_recovery_code(member, codes[0])
        assert mfa.remaining_recovery_codes(member) == 9

    def test_regeneration_invalidates_the_old_set(self, member):
        _device, old_codes = _confirmed_device(member)
        new_codes = mfa.generate_recovery_codes(member)
        assert set(old_codes).isdisjoint(new_codes)
        assert mfa.verify_recovery_code(member, old_codes[0]) is None
        assert mfa.verify_recovery_code(member, new_codes[0]) is not None

    def test_regenerate_view_requires_the_current_password(self, client, login, member):
        _device, _codes = _confirmed_device(member)
        c = login(member)
        resp = c.post("/account/security/mfa/regenerate", {"password": "wrong"})
        assert resp.status_code == 302 and resp.url == "/account"
        # No new stash created.
        assert c.get("/account/security/mfa/recovery-codes").status_code == 302


class TestLoginStepUp:
    def test_password_only_no_device_logs_in_directly(self, client, member):
        resp = client.post("/login", {"email": member.email, "password": PASSWORD})
        assert resp.status_code == 302 and resp.url == "/"
        assert "_auth_user_id" in client.session

    def test_a_confirmed_device_stops_short_of_login(self, client, member):
        _confirmed_device(member)
        resp = client.post("/login", {"email": member.email, "password": PASSWORD})
        assert resp.status_code == 302 and resp.url == "/login/verify"
        assert "_auth_user_id" not in client.session  # not logged in yet

    def test_the_right_totp_code_completes_login(self, client, member):
        device, _codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        totp = pyotp.TOTP(device.secret)
        resp = client.post("/login/verify", {"code": totp.now()})
        assert resp.status_code == 302 and resp.url == "/"
        assert "_auth_user_id" in client.session

    def test_login_audits_the_mfa_method(self, client, member):
        device, _codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        totp = pyotp.TOTP(device.secret)
        client.post("/login/verify", {"code": totp.now()})
        row = AuditLog.objects.get(action="auth.login")
        assert row.metadata["mfa"] == "totp"

    def test_a_recovery_code_also_completes_login(self, client, member):
        _device, codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        resp = client.post("/login/verify", {"code": codes[0]})
        assert resp.status_code == 302 and resp.url == "/"
        assert "_auth_user_id" in client.session
        assert AuditLog.objects.filter(action="auth.mfa_recovery_used").exists()
        assert AuditLog.objects.get(action="auth.login").metadata["mfa"] == "recovery"

    def test_a_used_recovery_code_cannot_complete_a_second_login(self, client, member):
        _device, codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        client.post("/login/verify", {"code": codes[0]})
        client.post("/logout")

        client.post("/login", {"email": member.email, "password": PASSWORD})
        resp = client.post("/login/verify", {"code": codes[0]})
        assert resp.status_code == 200  # re-rendered with an error
        assert "_auth_user_id" not in client.session

    def test_a_wrong_code_is_refused_and_audited(self, client, member):
        _device, _codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        resp = client.post("/login/verify", {"code": "000000"})
        assert resp.status_code == 200
        assert "_auth_user_id" not in client.session
        row = AuditLog.objects.get(action="auth.mfa_failed")
        assert row.outcome == "denied"

    def test_repeated_wrong_codes_feed_the_login_throttle(self, client, member, settings):
        from apps.core.models import InstanceConfig

        cfg = InstanceConfig.load()
        cfg.lockout_account_threshold = 2
        cfg.save()
        _device, _codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})

        for _ in range(2):
            client.post("/login/verify", {"code": "000000"})

        device = member.totp_device
        totp = pyotp.TOTP(device.secret)
        resp = client.post("/login/verify", {"code": totp.now()})
        assert resp.status_code == 200  # refused even with a right code now
        assert "_auth_user_id" not in client.session

    def test_stash_expires_after_five_minutes(self, client, member):
        device, _codes = _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})

        # Backdate the stash directly rather than mocking the global clock
        # (django.utils.timezone.now is shared process-wide, and mocking it
        # would also perturb session_policy's own now() calls in this same
        # request cycle).
        session = client.session
        session["mfa_started"] = (timezone.now() - timedelta(minutes=6)).timestamp()
        session.save()

        totp = pyotp.TOTP(device.secret)
        resp = client.post("/login/verify", {"code": totp.now()})
        assert resp.status_code == 302 and resp.url == "/login"
        assert "_auth_user_id" not in client.session

    def test_the_stash_cycles_the_session_key_against_fixation(self, client, member):
        # The documented way to seed a test Client's session before any
        # request: build and save a SessionStore, then plant its key as the
        # client's cookie -- client.session alone does not persist one.
        from importlib import import_module

        from django.conf import settings as dj_settings

        engine = import_module(dj_settings.SESSION_ENGINE)
        store = engine.SessionStore()
        store.save()
        client.cookies[dj_settings.SESSION_COOKIE_NAME] = store.session_key
        before = store.session_key

        _confirmed_device(member)
        client.post("/login", {"email": member.email, "password": PASSWORD})
        after_stepup = client.session.session_key

        assert after_stepup is not None
        assert after_stepup != before


class TestSsoUsersAreNeverPrompted:
    def test_enrollment_required_is_false_for_an_sso_established_session(self, rf, member):
        request = rf.get("/")
        request.session = {"sp_via_sso": True}
        assert mfa.enrollment_required(request, member) is False

    def test_sso_login_never_reaches_the_totp_step_up(self, client, org, make_user):
        """SSO logins are handled entirely by apps.accounts.adapters /
        allauth, never by login_view's password branch -- the step-up code
        (has_mfa check) is simply never on that call path. This documents
        the invariant rather than driving a full OIDC round-trip (covered by
        apps/accounts/tests/test_sso*.py)."""
        import inspect

        from apps.accounts import views as account_views

        source = inspect.getsource(account_views.login_view)
        # The step-up only fires after authenticate() succeeds on the
        # PASSWORD branch; the SSO branches return before reaching it.
        assert "has_mfa" in source
        assert source.index("provider_login_url(request, cfg)") < source.index("has_mfa")


class TestOrgPolicyEnforcement:
    def test_no_applicable_policy_never_requires_mfa(self, rf, member):
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is False

    def test_a_hard_policy_requires_mfa_immediately(self, rf, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is True

    def test_within_grace_enrollment_is_not_yet_required(self, rf, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=7)
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is False

    def test_after_grace_enrollment_is_required(self, rf, member, org):
        # Both anchors (policy turned on, user joined) must predate the
        # grace window for it to have expired -- back-date the member too,
        # or the "policy turned on" anchor loses to "just joined" and grace
        # never runs out. date_joined is a plain default=, not auto_now, but
        # OrgSecurityPolicy.updated_at IS auto_now=True: a normal .save()
        # would re-stamp it to "now" regardless of what was assigned, even
        # with update_fields, so both back-dates go through .update() to
        # bypass that.
        from apps.accounts.models import User
        from apps.orgs.models import OrgSecurityPolicy

        User.objects.filter(pk=member.pk).update(date_joined=timezone.now() - timedelta(days=30))
        member.refresh_from_db()  # .update() bypasses the in-memory instance
        policy = OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=7)
        OrgSecurityPolicy.objects.filter(pk=policy.pk).update(
            updated_at=timezone.now() - timedelta(days=8)
        )
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is True

    def test_an_already_enrolled_user_is_never_locked(self, rf, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        _confirmed_device(member)
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is False

    def test_redirect_lock_forces_enrollment_on_html_pages(self, client, login, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        c = login(member)
        resp = c.get("/account")
        assert resp.status_code == 302
        assert resp.url == "/account/security/mfa/setup"

    def test_enrollment_lock_denies_json_api_calls(self, client, login, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        c = login(member)
        resp = c.get("/api/me")
        assert resp.status_code == 403
        assert resp.json()["error"] == "mfa_enrollment_required"

    def test_the_enrollment_page_itself_stays_reachable(self, client, login, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        c = login(member)
        assert c.get("/account/security/mfa/setup").status_code == 200

    def test_self_disable_is_refused_while_policy_requires_it(self, client, login, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        device, _codes = _confirmed_device(member)
        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        c = login(member)
        totp = pyotp.TOTP(device.secret)
        c.post("/account/security/mfa/disable", {"password": PASSWORD, "code": totp.now()})
        member.refresh_from_db()
        assert member.has_mfa is True


class TestRequireMfaOperators:
    def test_off_by_default_operators_are_not_locked(self, client, login, superuser):
        c = login(superuser)
        assert c.get("/system").status_code == 200

    def test_when_enabled_an_unenrolled_operator_is_redirect_locked(self, client, login, superuser):
        from apps.core.models import InstanceConfig

        cfg = InstanceConfig.load()
        cfg.require_mfa_operators = True
        cfg.save()
        c = login(superuser)
        resp = c.get("/account")
        assert resp.status_code == 302
        assert resp.url == "/account/security/mfa/setup"


class TestOrganizationPolicy:
    def test_admin_can_enable_the_policy(self, client, login, org_admin, org):
        from apps.orgs.models import OrgSecurityPolicy

        response = login(org_admin).post(
            "/orgs/demo/settings/security",
            {"require_mfa": "on", "mfa_grace_days": "7"},
        )
        assert response.status_code == 302
        assert OrgSecurityPolicy.objects.get(org=org).require_mfa is True

    def test_policy_enforcement_reads_the_saved_row(self, rf, member, org):
        from apps.orgs.models import OrgSecurityPolicy

        OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
        request = rf.get("/")
        request.session = {}
        assert mfa.enrollment_required(request, member) is True


class TestAdminReset:
    def test_org_admin_reset_clears_the_device_and_bumps_epoch(self, client, login, org_admin, member, org):
        _confirmed_device(member)
        before_epoch = member.auth_epoch
        c = login(org_admin)
        resp = c.post(f"/orgs/{org.slug}/settings/members/{member.pk}/reset-mfa")
        assert resp.status_code == 302
        member.refresh_from_db()
        assert member.has_mfa is False
        assert member.auth_epoch == before_epoch + 1
        assert AuditLog.objects.filter(action="auth.mfa_reset", target_id=str(member.pk)).exists()

    def test_operator_reset_clears_the_device(self, client, login, superuser, member, org):
        _confirmed_device(member)
        c = login(superuser)
        resp = c.post(f"/operator/orgs/{org.slug}/members/{member.pk}/reset-mfa")
        assert resp.status_code == 302
        member.refresh_from_db()
        assert member.has_mfa is False
        assert AuditLog.objects.filter(action="auth.mfa_reset", target_id=str(member.pk)).exists()

    def test_reset_ends_the_users_sessions(self, member, org, org_admin, client):
        from django.test import Client

        _confirmed_device(member)
        member_client = Client()
        assert member_client.login(username=member.email, password=PASSWORD)
        totp = pyotp.TOTP(member.totp_device.secret)
        member_client.post("/login/verify", {"code": totp.now()})

        admin_client = Client()
        assert admin_client.login(username=org_admin.email, password=PASSWORD)
        admin_client.post(f"/orgs/{org.slug}/settings/members/{member.pk}/reset-mfa")

        resp = member_client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json()["reason"] == "revoked"

    def test_manage_py_mfa_reset_command(self, member):
        from django.core.management import call_command

        _confirmed_device(member)
        call_command("mfa_reset", member.email)
        member.refresh_from_db()
        assert member.has_mfa is False
        assert AuditLog.objects.filter(action="auth.mfa_reset").exists()

    def test_manage_py_mfa_reset_unknown_email(self):
        from django.core.management import CommandError, call_command

        with pytest.raises(CommandError):
            call_command("mfa_reset", "nobody@nowhere.example")
