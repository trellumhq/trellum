"""There is exactly one way into this portal with a password.

`include("allauth.urls")` used to mount allauth's own credential surface next to
ours: a login form, plus password set/change/reset, signup and email-management
views. None of it consulted `sso.enforced_config_for_user()` or
`allow_password_login` in apps/accounts/views.login_view.

What the login form actually did, measured rather than assumed: it **accepted**
valid credentials (ModelBackend resolves allauth's `email=` kwarg through our
USERNAME_FIELD) and returned no form errors, then routed into allauth's
login-by-code stage — HTTP 302 to /accounts/login/code/confirm/ with an
`account_login` stage in the session, rather than an authenticated session. So it
was not a one-request bypass; completing it needed the emailed code. That is
still a bypass of the org's SSO mandate for anyone who can read their own
mailbox, which is precisely the user an org enables `enforce_sso` over.

`/accounts/password/set/` was the sharper edge and needed no email at all: it
served 200 to a logged-in SSO-provisioned user, whose password is unusable by
design, letting them mint one.

These tests pin all of it shut. The assertions target the stage machinery, not
just "is the session authenticated" — the latter held true even in the vulnerable
state and would have proved nothing. The OIDC paths are asserted alongside,
because the fix edits the URL tree that live identity-provider app registrations point at.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import NoReverseMatch, resolve, reverse

from apps.core import roles
from apps.orgs.models import OrgMembership, OrgSSOConfig

User = get_user_model()

pytestmark = pytest.mark.django_db

PASSWORD = "pw-Str0ng-pw"


@pytest.fixture
def sso_user(org):
    """A member of an org that mandates SSO, who still has a usable password.

    This is not a contrived state: it is what every user looks like the moment
    an admin turns `enforce_sso` on for an org whose members signed up with a
    password.
    """
    user = User.objects.create_user(email="member@demo.example", password=PASSWORD)
    OrgMembership.objects.create(user=user, org=org, role=roles.ORG_MEMBER)
    OrgSSOConfig.objects.create(
        org=org,
        enabled=True,
        enforce_sso=True,
        issuer_url="https://idp.example/realms/demo",
        client_id="client-abc",
        client_secret="entra-secret",
        email_domains=["demo.example"],
    )
    return user


class TestAllauthLoginIsNotACredentialSurface:
    def test_posting_credentials_to_allauth_login_starts_no_login_flow(
        self, client, sso_user
    ):
        """The bypass itself: a valid password, posted to allauth's login view.

        Asserting only `not authenticated` would pass in the vulnerable state
        too, because allauth deferred the session to an emailed code. What marks
        the door shut is that the credentials never reach allauth's stage
        machinery at all: no `account_login` stage, and no hand-off to
        /accounts/login/code/confirm/.
        """
        response = client.post(
            "/accounts/login/",
            {"login": sso_user.email, "email": sso_user.email, "password": PASSWORD},
        )
        assert "_auth_user_id" not in client.session
        assert "account_login" not in client.session
        assert "code/confirm" not in (response.get("Location") or "")
        assert response.status_code == 302
        assert response["Location"].startswith("/login")

    def test_the_same_credentials_are_genuinely_valid(self, client, sso_user):
        """Guards the test above from passing for the wrong reason.

        If the password were simply wrong, the assertion would hold no matter
        what allauth did. Django's own authenticate() must accept it.
        """
        from django.contrib.auth import authenticate

        assert authenticate(username=sso_user.email, password=PASSWORD) == sso_user

    def test_enforced_sso_still_blocks_the_portal_login_view(self, client, sso_user):
        """The rule the bypass was circumventing, still enforced on our own view."""
        response = client.post(
            "/login", {"email": sso_user.email, "password": PASSWORD}
        )
        assert "_auth_user_id" not in client.session
        assert response.status_code == 302
        assert "/accounts/oidc/oidc-demo/login/" in response["Location"]

    def test_allauth_login_get_redirects_to_the_one_login_page(self, client):
        response = client.get("/accounts/login/")
        assert response.status_code == 302
        assert response["Location"].startswith("/login")

    def test_next_survives_the_redirect(self, client):
        """allauth sends users here mid-flow; dropping ?next= would strand them."""
        response = client.get("/accounts/login/?next=/orgs/demo/")
        assert "next=" in response["Location"]


class TestAllauthPasswordUrlsAreGone:
    """SOCIALACCOUNT_ONLY drops these from allauth.account.urls entirely."""

    @pytest.mark.parametrize(
        "url_name",
        ["account_set_password", "account_change_password", "account_reset_password",
         "account_signup", "account_email"],
    )
    def test_url_is_not_registered(self, url_name):
        with pytest.raises(NoReverseMatch):
            reverse(url_name)

    @pytest.mark.parametrize(
        "path",
        ["/accounts/password/set/", "/accounts/password/change/",
         "/accounts/password/reset/", "/accounts/signup/", "/accounts/email/"],
    )
    def test_path_is_not_served(self, client, path):
        assert client.get(path).status_code == 404

    def test_an_sso_user_cannot_give_themselves_a_password(self, client, org):
        """The escalation route: unusable password -> set one -> log in with it."""
        user = User.objects.create_user(email="sso-only@demo.example", password=None)
        user.set_unusable_password()
        user.save()
        OrgMembership.objects.create(user=user, org=org, role=roles.ORG_MEMBER)
        client.force_login(user)

        assert client.get("/accounts/password/set/").status_code == 404
        user.refresh_from_db()
        assert not user.has_usable_password()


class TestTheOIDCFlowIsUntouched:
    """The fix edits the URL tree identity-provider app registrations point at. If these
    break, every configured tenant's redirect URI breaks with them."""

    def test_the_oidc_callback_path_still_resolves(self):
        match = resolve("/accounts/oidc/oidc-demo/login/callback/")
        assert match.url_name == "openid_connect_callback"
        assert match.kwargs["provider_id"] == "oidc-demo"

    def test_the_oidc_login_path_still_resolves(self):
        match = resolve("/accounts/oidc/oidc-demo/login/")
        assert match.url_name == "openid_connect_login"

    def test_account_login_still_reverses(self):
        """allauth reverses this internally (socialaccount signup with no
        pending login, and AccountMiddleware on reauthentication)."""
        assert reverse("account_login") == "/accounts/login/"
