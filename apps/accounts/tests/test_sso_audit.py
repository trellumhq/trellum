"""auth.sso_denied: the refusal paths in OrgSSOAdapter.pre_social_login
each write a row with the right reason_code/provider/org/asserted_email, and
member.provisioned fires when auto-provisioning actually creates an account.

internal planning#76's own acceptance criterion -- the cross-tenant guard
records the org an attacker tried to reach plus the email they asserted --
is checked literally in TestCrossTenantAcceptanceCriterion below.
"""
import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.accounts.adapters import (
    REASON_AUTO_PROVISION_OFF,
    REASON_EXISTING_NON_MEMBER,
    REASON_NO_EMAIL,
    REASON_NOT_ENABLED,
    REASON_OUTSIDE_DOMAINS,
    OrgSSOAdapter,
)
from apps.core.models import AuditLog
from apps.orgs.models import OrgSSOConfig

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _domain_verification_is_covered_by_its_own_suite(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False


@pytest.fixture
def sso_cfg(org):
    return OrgSSOConfig.objects.create(
        org=org,
        enabled=True,
        issuer_url="https://idp.example/realms/demo",
        client_id="client-abc",
        client_secret="entra-secret",
        email_domains=["demo.example"],
    )


@pytest.fixture
def rf_request():
    request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
    request.session = {}
    return request


def _sociallogin(provider="oidc-demo", email="sso.user@demo.example", uid="oid-1", **claims):
    account = SocialAccount(provider=provider, uid=uid, extra_data={"email": email, "email_verified": True, **claims})
    user = User(email=email)
    return SocialLogin(user=user, account=account)


def _gate(request, login_obj):
    return OrgSSOAdapter().pre_social_login(request, login_obj)


class TestDenialPaths:
    def test_sso_not_enabled(self, rf_request, org):
        """No OrgSSOConfig at all for this provider id -- but the org is
        still resolvable from the provider id itself (oidc-<slug>), so the
        denial lands on the right org's trail even though nothing was ever
        configured."""
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin(provider=f"oidc-{org.slug}"))

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.outcome == "denied"
        assert row.category == "auth"
        assert row.metadata["reason_code"] == REASON_NOT_ENABLED
        assert row.org_id == org.pk

    def test_sso_not_enabled_with_no_resolvable_org(self, rf_request):
        """A provider id that doesn't even map to a real org slug -- org
        stays None rather than guessing."""
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin(provider="oidc-does-not-exist"))

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_NOT_ENABLED
        assert row.org_id is None

    def test_no_email_claim(self, sso_cfg, rf_request, org):
        account = SocialAccount(provider="oidc-demo", uid="x", extra_data={})
        login_obj = SocialLogin(user=User(), account=account)
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, login_obj)

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_NO_EMAIL
        assert row.org_id == org.pk
        assert row.metadata.get("asserted_email", "") == ""

    def test_outside_verified_domains(self, sso_cfg, rf_request, org):
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin(email="attacker@rival.example"))

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_OUTSIDE_DOMAINS
        assert row.org_id == org.pk
        assert row.metadata["asserted_email"] == "attacker@rival.example"

    def test_existing_non_member_account(self, sso_cfg, rf_request, make_user, org):
        make_user("sso.user@demo.example")  # exists, no membership in this org
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin())

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_EXISTING_NON_MEMBER
        assert row.org_id == org.pk
        assert row.metadata["asserted_email"] == "sso.user@demo.example"

    def test_auto_provision_disabled(self, sso_cfg, rf_request, org):
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin())

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == REASON_AUTO_PROVISION_OFF
        assert row.org_id == org.pk

    def test_every_denial_carries_the_provider(self, sso_cfg, rf_request):
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin(email="attacker@rival.example"))
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["provider"] == "oidc-demo"


class TestCrossTenantAcceptanceCriterion:
    """internal planning#76's own acceptance criterion, checked literally: an
    IdP asserting an email outside the org's verified domains must be
    recorded with which org was targeted and which email was asserted."""

    def test_the_denial_names_the_targeted_org_and_the_asserted_email(
        self, sso_cfg, rf_request, org
    ):
        with pytest.raises(ImmediateHttpResponse):
            _gate(rf_request, _sociallogin(email="eve@rival.example"))

        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.org == org
        assert row.metadata["asserted_email"] == "eve@rival.example"
        assert row.metadata["reason_code"] == "outside_verified_domains"
        assert row.outcome == "denied"


class TestMemberProvisioned:
    def test_auto_provision_writes_a_provisioned_row(self, org):
        """Full allauth round-trip, same shape as
        test_sso_signup_flow.test_auto_provision_signup_full_flow --
        provisioning a member via SSO used to leave no authz trace at all."""
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore

        OrgSSOConfig.objects.create(
            org=org, enabled=True, auto_provision=True,
            issuer_url="https://idp.example/realms/demo", client_id="cid", client_secret="s",
            email_domains=["demo.example"],
        )
        email = "fresh@demo.example"
        request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
        request.session = SessionStore()
        request.session.create()
        request.user = AnonymousUser()
        request._messages = FallbackStorage(request)

        from allauth.core import context
        from allauth.socialaccount.adapter import get_adapter
        from allauth.socialaccount.helpers import complete_social_login

        provider = get_adapter().get_provider(request, "oidc-demo")
        sociallogin = provider.sociallogin_from_response(
            request,
            {
                "userinfo": {"sub": "entra-oid-9", "name": "Fresh User", "email": email, "email_verified": True},
                "id_token": {
                    "sub": "entra-oid-9", "preferred_username": email, "name": "Fresh User",
                },
            },
        )
        with context.request_context(request):
            complete_social_login(request, sociallogin)

        user = User.objects.get(email=email)
        row = AuditLog.objects.get(action="member.provisioned")
        assert row.actor_id == user.pk
        assert row.target_id == str(user.pk)
        assert row.org == org
        assert row.metadata["provider"] == "oidc-demo"
        assert row.metadata["via"] == "sso_auto_provision"
        assert row.category == "authz"
