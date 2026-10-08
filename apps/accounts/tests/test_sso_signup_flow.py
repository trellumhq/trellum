"""Regression: allauth's auto-provision signup must work with our
email-only User model (it 500ed on KeyError 'username' in the first real
Entra round-trip until ACCOUNT_USER_MODEL_USERNAME_FIELD=None)."""
import pytest
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from apps.orgs.models import OrgMembership, OrgSSOConfig

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _domain_verification_is_covered_by_its_own_suite(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False


def _callback_request():
    request = RequestFactory().get("/accounts/oidc/oidc-demo/login/callback/")
    request.session = SessionStore()
    request.session.create()
    request.user = AnonymousUser()
    request._messages = FallbackStorage(request)
    return request


def test_settings_declare_email_only_user_model(settings):
    assert settings.ACCOUNT_USER_MODEL_USERNAME_FIELD is None
    assert settings.ACCOUNT_SIGNUP_FIELDS == ["email*"]


def test_auto_provision_signup_full_flow(org):
    """The exact live-failure path: nested Entra claims, mailbox-less user,
    auto-provision on → allauth signup must create the member, not 500."""
    OrgSSOConfig.objects.create(
        org=org, enabled=True, auto_provision=True,
        issuer_url="https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0",
        client_id="cid", client_secret="s",
        email_domains=["meijerapollogmail.onmicrosoft.com"],
    )
    email = "apollo@meijerapollogmail.onmicrosoft.com"
    request = _callback_request()

    # Build the SocialLogin the way the real callback does: through the
    # provider (which resolves our transient app + populate_user).
    from allauth.socialaccount.adapter import get_adapter

    provider = get_adapter().get_provider(request, "oidc-demo")
    sociallogin = provider.sociallogin_from_response(
        request,
        {
            "userinfo": {"sub": "entra-oid-123", "name": "Apollo Test"},
            "id_token": {
                "sub": "entra-oid-123",
                "tid": "11111111-2222-3333-4444-555555555555",
                "preferred_username": email,
                "name": "Apollo Test",
            },
        },
    )

    from allauth.core import context
    from allauth.socialaccount.helpers import complete_social_login

    # Production runs allauth's middleware, which sets this context; the
    # bare RequestFactory harness must set it explicitly.
    with context.request_context(request):
        response = complete_social_login(request, sociallogin)

    assert response.status_code in (200, 302)  # anything but a crash
    user = User.objects.get(email=email)
    assert user.name == "Apollo Test"
    assert not user.has_usable_password()
    assert OrgMembership.objects.filter(user=user, org=org, role="member").exists()
    assert SocialAccount.objects.filter(user=user, uid="entra-oid-123").exists()
