"""The allauth failure pages carry OUR design, not the barebones defaults."""
import pytest

pytestmark = pytest.mark.django_db


def test_callback_failure_renders_branded_page(client, org, member):
    from apps.orgs.models import OrgSSOConfig

    OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url="https://idp.example/realms/demo", client_id="cid",
        client_secret="s", email_domains=["demo.example"],
    )
    # A callback with no code/state can't complete: allauth renders the
    # authentication-error template — which must be ours.
    resp = client.get("/accounts/oidc/oidc-demo/login/callback/")
    html = resp.content.decode()
    assert "Single sign-on didn't complete" in html
    assert "auth-card" in html
    assert "Usual causes" in html
    # The default allauth chrome must be gone.
    assert "Third-Party Login Failure" not in html
    assert "Sign Up" not in html


def test_error_page_links_back_to_login(client, org, member):
    from apps.orgs.models import OrgSSOConfig

    OrgSSOConfig.objects.create(
        org=org, enabled=True, issuer_url="https://idp.example/realms/demo", client_id="cid",
        client_secret="s", email_domains=["demo.example"],
    )
    html = client.get("/accounts/oidc/oidc-demo/login/callback/").content.decode()
    assert 'href="/login"' in html
