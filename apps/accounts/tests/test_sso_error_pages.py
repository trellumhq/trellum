"""The failure pages carry OUR design and a support reference, for both the
gate's 403 (reason codes) and allauth's identity-provider error page."""
import html
import re

import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from allauth.socialaccount.providers.base import AuthError
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.accounts import adapters
from apps.core.models import AuditLog
from apps.orgs.models import OrgSSOConfig

pytestmark = pytest.mark.django_db
User = get_user_model()
CALLBACK = "/accounts/oidc/oidc-demo/login/callback/"


class _Provider:
    id = sub_id = "oidc-demo"


@pytest.fixture(autouse=True)
def _no_domain_verification(settings):
    settings.TRELLUM_SSO_DOMAIN_VERIFICATION = False


@pytest.fixture
def cfg(org):
    return OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url="https://idp.example/realms/demo", client_id="cid",
        client_secret="s", email_domains=["demo.example"],
    )


def _request():
    request = RequestFactory().get(CALLBACK)
    request.session = {}
    return request


def _gate(email="sso.user@demo.example", provider="oidc-demo", **claims):
    account = SocialAccount(
        provider=provider, uid="u1", extra_data={"email": email, "email_verified": True, **claims},
    )
    login = SocialLogin(user=User(email=email), account=account)
    with pytest.raises(ImmediateHttpResponse) as exc:
        adapters.OrgSSOAdapter().pre_social_login(_request(), login)
    resp = exc.value.response
    return resp, html.unescape(resp.content.decode())


def _reference(page):
    return re.search(r"data-sso-reference>([A-Z2-7]{8})<", page).group(1)


class TestGateDenials:
    def test_non_member(self, cfg, make_user):
        make_user("sso.user@demo.example")
        resp, page = _gate()
        assert resp.status_code == 403
        assert "We couldn't sign you in" in page
        assert "Your account isn't a member of Demo yet. Ask an organization admin to invite you." in page
        assert "auth-card" in page and "auth-shell" in page

    def test_auto_provision_disabled(self, cfg):
        _, page = _gate()
        assert "Demo doesn't create accounts automatically. Ask an organization admin to invite you." in page

    def test_outside_domains_names_only_the_asserted_email(self, cfg):
        _, page = _gate(email="x@rival.example")
        assert "x@rival.example isn't in an email domain Demo uses for single sign-on." in page

    def test_unverified_email(self, cfg):
        _, page = _gate(email_verified=False)
        assert "Your identity provider didn't confirm your email address." in page

    def test_no_email(self, cfg):
        login = SocialLogin(
            user=User(), account=SocialAccount(provider="oidc-demo", uid="x", extra_data={}),
        )
        with pytest.raises(ImmediateHttpResponse) as exc:
            adapters.OrgSSOAdapter().pre_social_login(_request(), login)
        assert "didn't send an email address" in html.unescape(exc.value.response.content.decode())

    def test_not_enabled(self, org):
        resp, page = _gate(provider="oidc-demo")
        assert resp.status_code == 403
        assert "Single sign-on isn't turned on for Demo." in page

    def test_unknown_reason_falls_back(self, org):
        resp = adapters.denied(_request(), "", reason_code="mystery", org=org)
        assert resp.status_code == 403
        assert "Something went wrong while signing you in." in resp.content.decode()

    def test_reference_on_page_and_in_audit_row(self, cfg):
        _, page = _gate()
        ref = _reference(page)
        assert "mention" in page and "reference" in page
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reference"] == ref
        assert row.metadata["reason_code"] == adapters.REASON_AUTO_PROVISION_OFF

    def test_references_differ_between_failures(self, cfg):
        assert _reference(_gate()[1]) != _reference(_gate()[1])

    def test_reference_is_searchable_in_org_audit_log(self, cfg, org, make_user, client):
        from apps.core import roles

        admin = make_user("admin@demo.example", org=org, org_role=roles.ORG_ADMIN)
        ref = _reference(_gate()[1])
        client.force_login(admin)
        resp = client.get(f"/orgs/{org.slug}/settings/audit", {"q": ref})
        assert resp.status_code == 200 and ref in resp.content.decode()

    def test_policy_refusal_retry_goes_to_login(self, cfg):
        _, page = _gate()
        assert 'class="ui-btn primary" href="/login"' in page
        assert "Back to sign-in" in page

    def test_no_claims_or_traces_leak(self, cfg):
        _, page = _gate(name="Secret Name", groups=["g1"])
        assert "Secret Name" not in page and "Traceback" not in page


class TestAuthenticationErrorPage:
    def test_callback_failure_uses_new_template(self, client, cfg):
        # No state: allauth reports a state error through the adapter.
        resp = client.get(CALLBACK)
        page = html.unescape(resp.content.decode())
        assert resp.status_code in (401, 403)
        assert "We couldn't sign you in" in page
        assert "Your sign-in session expired or was already used." in page
        assert "auth-card" in page
        assert "Third-Party Login Failure" not in page and "Sign Up" not in page

    def test_retry_goes_back_to_org_login_and_back_link_to_login(self, client, cfg):
        page = client.get(CALLBACK).content.decode()
        assert 'class="ui-btn primary" href="/accounts/oidc/oidc-demo/login/"' in page
        assert 'href="/login"' in page

    def test_reference_on_page_and_in_audit_row(self, client, cfg, org):
        page = client.get(CALLBACK).content.decode()
        ref = _reference(page)
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reference"] == ref
        assert row.metadata["reason_code"] == adapters.REASON_IDP_STATE
        assert row.org_id == org.pk and row.outcome == "failure"

    def test_provider_error_never_reaches_audit_or_page(self, cfg):
        extra = {}
        adapters.OrgSSOAdapter().on_authentication_error(
            _request(), _Provider(), error=AuthError.UNKNOWN,
            exception=ValueError("AADSTS50011 x"), extra_context=extra,
        )
        assert extra["failure"]["lead"] == "Your identity provider reported a problem."
        assert extra["failure"]["retry_url"].endswith("/oidc-demo/login/")
        assert "AADSTS" not in str(AuditLog.objects.get(action="auth.sso_denied").metadata)

    def test_cancelled_renders_our_page(self, cfg):
        with pytest.raises(ImmediateHttpResponse) as exc:
            adapters.OrgSSOAdapter().on_authentication_error(
                _request(), _Provider(), error=AuthError.CANCELLED, extra_context={},
            )
        page = exc.value.response.content.decode()
        assert "Sign-in was cancelled." in page
        assert '/oidc-demo/login/"' in page
        row = AuditLog.objects.get(action="auth.sso_denied")
        assert row.metadata["reason_code"] == adapters.REASON_IDP_CANCELLED

    def test_unknown_org_provider_still_renders(self, org):
        class Other:
            id = sub_id = "other"

        extra = {}
        adapters.OrgSSOAdapter().on_authentication_error(_request(), Other(), extra_context=extra)
        assert extra["failure"]["retry_url"] == "/login"
