"""Credential scope, enrollment enforcement and single-use authentication factors."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pyotp
import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.core.cache import cache
from django.db import connection, connections
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts import mfa, throttle
from apps.accounts.adapters import OrgSSOAdapter
from apps.accounts.models import ApiKey, RecoveryCode, TotpDevice, User
from apps.core import roles
from apps.core.models import InstanceConfig
from apps.orgs.models import OrgMembership, OrgSecurityPolicy, OrgSSOConfig

pytestmark = pytest.mark.django_db
PASSWORD = "pw-Str0ng-pw"
TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


def _device(user):
    device = mfa.begin_enrollment(user)
    codes = mfa.confirm_enrollment(device, pyotp.TOTP(device.secret).now())
    TotpDevice.objects.filter(pk=device.pk).update(last_used_step=0)
    device.refresh_from_db()
    return device, codes


@pytest.mark.parametrize("with_session", [False, True])
@pytest.mark.parametrize("url", ["/me/api-keys", "/account", "/system", "/api/system/health", "/operator/"])
def test_bearer_cannot_access_session_management(client, superuser, org, url, with_session):
    OrgMembership.objects.create(user=superuser, org=org)
    _, secret = ApiKey.mint(user=superuser, org=org, name="limited", scopes=ApiKey.READ_WRITE)
    if with_session:
        client.force_login(superuser)
    response = client.get(url, HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert response.status_code == 403
    assert response.json()["error"] == "session_required"


@pytest.mark.parametrize("target_org", ["same", "other"])
def test_bearer_cannot_mint_or_extend_keys(client, member, org, other_org, target_org):
    OrgMembership.objects.create(user=member, org=other_org)
    key, secret = ApiKey.mint(
        user=member, org=org, name="limited", scopes=ApiKey.READ_WRITE,
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )
    response = client.post(
        "/me/api-keys", {"name": "expanded", "org": (org if target_org == "same" else other_org).pk,
                         "scopes": ApiKey.READ_WRITE}, HTTP_AUTHORIZATION=f"Bearer {secret}",
    )
    assert response.status_code == 403
    assert list(ApiKey.objects.filter(user=member)) == [key]
    assert "new_api_key" not in client.session


def test_scoped_bearer_preserves_read_write_and_org_ceiling(client, member, org, studio, other_org,
                                                          other_studio, grant_studio):
    OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
    grant_studio(member, studio, roles.DEVELOPER)
    grant_studio(member, other_studio, roles.DEVELOPER)
    OrgMembership.objects.create(user=member, org=other_org)
    _, read = ApiKey.mint(user=member, org=org, name="read")
    _, write = ApiKey.mint(user=member, org=org, name="write", scopes=ApiKey.READ_WRITE)
    own = f"/s/{org.slug}/{studio.slug}/api/system/cache/clear"
    other = f"/s/{other_org.slug}/{other_studio.slug}/api/system/cache/clear"
    assert client.post(own, HTTP_AUTHORIZATION=f"Bearer {read}").json()["error"] == "write_scope_required"
    assert client.post(own, HTTP_AUTHORIZATION=f"Bearer {write}").status_code == 200
    assert client.post(other, HTTP_AUTHORIZATION=f"Bearer {write}").status_code == 404
    assert client.get(f"/s/{org.slug}/{studio.slug}/", HTTP_AUTHORIZATION=f"Bearer {write}").status_code == 403


@pytest.mark.parametrize("url", ["/account", "/api/me", "/me/api-keys", "/account/security/mfa/disable"])
def test_required_enrollment_denies_json_sessions(client, member, org, url):
    OrgSecurityPolicy.objects.create(org=org, require_mfa=True, mfa_grace_days=0)
    client.force_login(member)
    response = client.post(url, HTTP_ACCEPT="application/json")
    assert response.status_code == 403
    assert response.json()["error"] == "mfa_enrollment_required"
    assert client.get("/account/security/mfa/setup", HTTP_ACCEPT="application/json").status_code == 200
    assert client.post("/logout").status_code == 302


def test_operator_enrollment_denies_json(client, superuser):
    cfg = InstanceConfig.load()
    cfg.require_mfa_operators = True
    cfg.save()
    client.force_login(superuser)
    assert client.get("/system", HTTP_ACCEPT="application/json").json()["error"] == "mfa_enrollment_required"


def test_correct_password_cannot_reset_mfa_guess_limit(client, member):
    cfg = InstanceConfig.load()
    cfg.lockout_account_threshold = 2
    cfg.save()
    _device(member)
    for _ in range(2):
        assert client.post("/login", {"email": member.email, "password": PASSWORD}).url == "/login/verify"
        assert client.post("/login/verify", {"code": "wrong-code"}).status_code == 200
    request = RequestFactory().get("/login", REMOTE_ADDR="127.0.0.1")
    assert throttle.is_throttled(member.email, request)
    assert client.post("/login", {"email": member.email, "password": PASSWORD}).status_code == 200
    assert "_auth_user_id" not in client.session


@pytest.mark.parametrize("factor", ["totp", "recovery"])
def test_complete_mfa_login_clears_failure_state(client, member, factor):
    device, codes = _device(member)
    request = RequestFactory().get("/login", REMOTE_ADDR="127.0.0.1")
    throttle.record_failure(member.email, request)
    client.post("/login", {"email": member.email, "password": PASSWORD})
    code = pyotp.TOTP(device.secret).now() if factor == "totp" else codes[0]
    assert client.post("/login/verify", {"code": code}).status_code == 302
    assert cache.get(throttle._scopes(member.email, request)[0].counter_key) is None
    assert "_auth_user_id" in client.session


def test_expired_pending_mfa_does_not_clear_failure_state(client, member):
    _device(member)
    request = RequestFactory().get("/login", REMOTE_ADDR="127.0.0.1")
    throttle.record_failure(member.email, request)
    client.post("/login", {"email": member.email, "password": PASSWORD})
    session = client.session
    session["mfa_started"] = timezone.now().timestamp() - 301
    session.save()
    assert client.post("/login/verify", {"code": "wrong-code"}).url == "/login"
    assert cache.get(throttle._scopes(member.email, request)[0].counter_key) == 1


@pytest.mark.parametrize("superuser_target", [False, True])
def test_org_admin_cannot_reset_operator_factor(client, org_admin, member, org, superuser_target):
    member.is_operator_flag = not superuser_target
    member.is_superuser = superuser_target
    member.save()
    device, _ = _device(member)
    before_codes = list(RecoveryCode.objects.filter(user=member).values_list("pk", "code_hash", "used_at"))
    before_epoch = member.auth_epoch
    client.force_login(org_admin)
    assert client.post(f"/orgs/{org.slug}/settings/members/{member.pk}/reset-mfa").status_code == 302
    member.refresh_from_db()
    assert member.auth_epoch == before_epoch
    assert TotpDevice.objects.filter(pk=device.pk).exists()
    assert list(RecoveryCode.objects.filter(user=member).values_list("pk", "code_hash", "used_at")) == before_codes


def test_stale_totp_device_cannot_replay_a_code(member):
    device, _ = _device(member)
    stale = TotpDevice.objects.get(pk=device.pk)
    code = pyotp.TOTP(device.secret).now()
    assert mfa.verify_login_code(device, code)
    assert not mfa.verify_login_code(stale, code)


def test_enrollment_code_cannot_be_reused_or_regenerate_recovery_codes(member):
    device = mfa.begin_enrollment(member)
    stale = TotpDevice.objects.get(pk=device.pk)
    code = pyotp.TOTP(device.secret).now()
    assert mfa.confirm_enrollment(device, code)
    original = list(RecoveryCode.objects.filter(user=member).values_list("pk", flat=True))
    assert mfa.confirm_enrollment(stale, code) is None
    assert not mfa.verify_login_code(device, code)
    assert list(RecoveryCode.objects.filter(user=member).values_list("pk", flat=True)) == original


def test_stale_recovery_row_cannot_be_consumed_twice(member, monkeypatch):
    from django.contrib.auth.hashers import check_password

    _, codes = _device(member)
    stale = next(row for row in RecoveryCode.objects.filter(user=member) if check_password(codes[0], row.code_hash))
    monkeypatch.setattr(type(member.recovery_codes), "filter", lambda self, **kw: [stale])
    assert mfa.verify_recovery_code(member, codes[0]) is not None
    assert mfa.verify_recovery_code(member, codes[0]) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("factor", ["totp", "recovery"])
def test_concurrent_factor_consumption_has_one_winner(member, factor):
    if connection.vendor != "postgresql":
        pytest.skip("Concurrent row-lock verification requires PostgreSQL")
    device, codes = _device(member)
    code = pyotp.TOTP(device.secret).now() if factor == "totp" else codes[0]
    ready = Barrier(2)

    def consume():
        try:
            current = TotpDevice.objects.get(pk=device.pk)
            user = User.objects.get(pk=member.pk)
            ready.wait(timeout=10)
            if factor == "totp":
                return mfa.verify_login_code(current, code)
            return mfa.verify_recovery_code(user, code) is not None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(consume) for _ in range(2)]
        assert sum(future.result(timeout=15) for future in futures) == 1


@pytest.mark.parametrize("next_url,expected", [("https://outside.invalid", "/"),
                                               ("//outside.invalid/path", "/"),
                                               ("/account?tab=profile", "/account?tab=profile")])
def test_password_login_uses_only_safe_redirects(client, member, next_url, expected):
    response = client.post("/login", {"email": member.email, "password": PASSWORD}, QUERY_STRING=f"next={next_url}")
    assert response.url == expected


@pytest.mark.parametrize("verified", [False, "false", None])
@pytest.mark.parametrize("preloaded_user", [False, True])
def test_new_subject_cannot_link_unverified_member_email(member, org, rf, settings, verified, preloaded_user):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    OrgSSOConfig.objects.create(org=org, enabled=True, issuer_url="https://idp.example", client_id="cid",
                                email_domains=["demo.example"])
    claims = {"email": member.email}
    if verified is not None:
        claims["email_verified"] = verified
    sociallogin = SocialLogin(user=member if preloaded_user else User(email=member.email), account=SocialAccount(
        provider=f"oidc-{org.slug}", uid="new-subject", extra_data=claims,
    ))
    request = rf.get("/callback")
    request.session = {}
    with pytest.raises(ImmediateHttpResponse) as exc:
        OrgSSOAdapter().pre_social_login(request, sociallogin)
    assert exc.value.response.status_code == 403
    assert not SocialAccount.objects.filter(user=member).exists()


@pytest.mark.parametrize("issuer,tid", [("https://idp.example/realms/demo", TENANT),
                                        (f"https://login.microsoftonline.com.attacker.invalid/{TENANT}/v2.0", TENANT),
                                        ("https://login.microsoftonline.com/common/v2.0", TENANT),
                                        (f"https://login.microsoftonline.com/{TENANT}/v2.0", "other-tenant")])
def test_upn_fallback_cannot_bypass_email_verification(member, org, rf, settings, issuer, tid):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    OrgSSOConfig.objects.create(org=org, enabled=True, issuer_url=issuer, client_id="cid",
                                email_domains=["demo.example"])
    sociallogin = SocialLogin(user=User(email=member.email), account=SocialAccount(
        provider=f"oidc-{org.slug}", uid="new-subject",
        extra_data={"id_token": {"preferred_username": member.email, "tid": tid}},
    ))
    request = rf.get("/callback")
    request.session = {}
    with pytest.raises(ImmediateHttpResponse):
        OrgSSOAdapter().pre_social_login(request, sociallogin)
    assert not SocialAccount.objects.filter(user=member).exists()


def test_verified_identity_and_trusted_entra_upn_can_link(member, org, rf, settings, monkeypatch):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    cfg = OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url=f"https://login.microsoftonline.com/{TENANT}/v2.0", client_id="cid",
        email_domains=["demo.example"],
    )
    connected = []
    monkeypatch.setattr(SocialLogin, "connect", lambda self, req, user: connected.append(user.pk))
    request = rf.get("/callback")
    request.session = {}
    for claims in ({"email": member.email, "email_verified": True},
                   {"id_token": {"preferred_username": member.email, "tid": TENANT}}):
        login = SocialLogin(user=User(email=member.email), account=SocialAccount(
            provider=f"oidc-{org.slug}", uid="new-subject", extra_data=claims,
        ))
        OrgSSOAdapter().pre_social_login(request, login)
    assert connected == [member.pk, member.pk]
    cfg.org.is_active = False
    cfg.org.save()
    with pytest.raises(ImmediateHttpResponse):
        OrgSSOAdapter().pre_social_login(request, login)
    assert connected == [member.pk, member.pk]


def test_returning_subject_identity_does_not_require_new_email_verification(member, org, rf, settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False
    OrgSSOConfig.objects.create(org=org, enabled=True, issuer_url="https://idp.example", client_id="cid",
                                email_domains=["demo.example"])
    account = SocialAccount.objects.create(user=member, provider=f"oidc-{org.slug}", uid="existing-subject",
                                           extra_data={"email": member.email, "email_verified": False})
    login = SocialLogin(user=member, account=account)
    request = rf.get("/callback")
    request.session = {}
    OrgSSOAdapter().pre_social_login(request, login)
    assert SocialAccount.objects.filter(user=member).count() == 1


def test_bearer_headers_with_session_never_fall_back_for_invalid_trellum_key(client, member):
    client.force_login(member)
    assert client.get("/account", HTTP_AUTHORIZATION="Bearer trellum_pk_invalid").status_code == 401
    assert client.get("/account", HTTP_AUTHORIZATION="Bearer unrelated-credential").status_code == 200
