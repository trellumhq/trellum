"""Claim extraction from allauth's NESTED extra_data ({'userinfo': …,
'id_token': …} since 65.11) — the exact shape Entra produced in the live
test, where a mailbox-less onmicrosoft.com user has no userinfo email and
the identity lives in the id_token's preferred_username."""
import pytest
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model

from apps.accounts import sso
from apps.orgs.models import OrgSSOConfig, PermissionGroupMembership

User = get_user_model()

pytestmark = pytest.mark.django_db


def _login_with(extra_data):
    account = SocialAccount(provider="oidc-demo", uid="oid-1", extra_data=extra_data)
    return SocialLogin(user=User(), account=account)


class TestClaims:
    def test_mailboxless_entra_user_email_from_id_token(self):
        login_obj = _login_with(
            {
                "userinfo": {"sub": "x", "name": "Apollo", "picture": None},
                "id_token": {
                    "sub": "x",
                    "preferred_username": "apollo@meijerapollogmail.onmicrosoft.com",
                    "name": "Apollo",
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@meijerapollogmail.onmicrosoft.com"

    def test_userinfo_email_wins_when_present(self):
        login_obj = _login_with(
            {
                "userinfo": {"email": "real@demo.example"},
                "id_token": {"preferred_username": "upn@demo.example"},
            }
        )
        assert sso.extract_email(login_obj) == "real@demo.example"

    def test_flat_legacy_shape_still_works(self):
        login_obj = _login_with({"email": "flat@demo.example"})
        assert sso.extract_email(login_obj) == "flat@demo.example"

    def test_groups_claim_survives_from_id_token(self):
        login_obj = _login_with(
            {
                "userinfo": {"sub": "x"},
                "id_token": {"groups": ["gid-1", "gid-2"], "preferred_username": "a@b.c"},
            }
        )
        assert sso.claims(login_obj).get("groups") == ["gid-1", "gid-2"]

    def test_name_merged_for_populate_user(self):
        login_obj = _login_with(
            {"userinfo": {"name": "Userinfo Name"}, "id_token": {"name": "Token Name"}}
        )
        assert sso.claims(login_obj)["name"] == "Userinfo Name"  # userinfo overlays

    def test_empty_extra_data(self):
        assert sso.extract_email(_login_with({})) == ""


def _cfg(org, **kw):
    """Unsaved config: sync_groups only reads org, groups_claim and group_map."""
    return OrgSSOConfig(org=org, **kw)


def _member_of(user, group) -> bool:
    return PermissionGroupMembership.objects.filter(user=user, group=group).exists()


class TestProviderShapes:
    """One realistic token per identity provider, shaped from its docs, run
    through extract_email() and sync_groups() with the groups claim and
    mapping the SSO docs tell an admin to use."""

    @pytest.fixture
    def groups(self, make_group):
        return make_group("Analysts"), make_group("Admins")

    @pytest.fixture
    def user(self, make_user, org):
        return make_user("apollo@demo.example", org=org)

    def _assert_analyst_only(self, user, groups):
        analysts, admins = groups
        assert _member_of(user, analysts)
        assert not _member_of(user, admins)

    def test_entra_group_object_ids(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {"sub": "AAAAAAAAAAAAAAAAAAAAAG9v", "name": "Apollo", "picture": None},
                "id_token": {
                    "iss": "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/v2.0",
                    "oid": "9f1c2a6e-0d3b-4f41-9c3e-8c1c8a0d2b11",
                    "tid": "11111111-2222-3333-4444-555555555555",
                    "preferred_username": "apollo@demo.example",  # no mailbox: no email
                    "name": "Apollo",
                    "groups": ["3c2f4c5a-1c7a-4d9e-a3b3-2a1f0c9d8e7f"],
                    "ver": "2.0",
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(
            org,
            group_map={
                "3c2f4c5a-1c7a-4d9e-a3b3-2a1f0c9d8e7f": analysts.pk,
                "0d0d0d0d-0000-0000-0000-000000000000": admins.pk,
            },
        )
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_entra_group_overage_leaves_memberships_alone(self, org, user, groups, attach_group):
        analysts, _ = groups
        attach_group(user, analysts)
        claims = {
            "preferred_username": "apollo@demo.example",
            "_claim_names": {"groups": "src1"},
            "_claim_sources": {
                "src1": {"endpoint": "https://graph.microsoft.com/v1.0/users/x/getMemberObjects"}
            },
        }
        sso.sync_groups(user, _cfg(org, group_map={"any": analysts.pk}), claims)
        assert _member_of(user, analysts)

    def test_google_has_no_groups_and_touches_nothing(self, org, user, groups, attach_group):
        analysts, _ = groups
        attach_group(user, analysts)
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "109876543210987654321",
                    "email": "apollo@demo.example",
                    "email_verified": True,
                    "hd": "demo.example",
                    "name": "Apollo",
                    "picture": "https://lh3.googleusercontent.com/a/photo",
                },
                "id_token": {
                    "iss": "https://accounts.google.com",
                    "sub": "109876543210987654321",
                    "email": "apollo@demo.example",
                    "email_verified": True,
                    "hd": "demo.example",
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        sso.sync_groups(user, _cfg(org, group_map={}), sso.claims(login_obj))
        assert _member_of(user, analysts)

    def test_okta_group_names(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "00u1abcdefghijklmn5d7",
                    "email": "apollo@demo.example",
                    "email_verified": True,
                    "preferred_username": "apollo@demo.example",
                    "groups": ["Everyone", "Analysts"],
                },
                "id_token": {
                    "iss": "https://acme.okta.com",
                    "sub": "00u1abcdefghijklmn5d7",
                    "email": "apollo@demo.example",
                    "preferred_username": "apollo@demo.example",
                    "groups": ["Everyone", "Analysts"],
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_pingone_group_names_from_attribute_mapping(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "7a1b2c3d-1111-2222-3333-444455556666",
                    "email": "apollo@demo.example",
                },
                "id_token": {
                    "iss": "https://auth.pingone.eu/0a1b2c3d-1111-2222-3333-444455556666/as",
                    "sub": "7a1b2c3d-1111-2222-3333-444455556666",
                    "env": "0a1b2c3d-1111-2222-3333-444455556666",
                    "email": "apollo@demo.example",
                    "groups": ["Analysts"],  # memberOfGroupNames mapped to `groups`
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_onelogin_roles_in_groups_claim(self, org, user, groups, attach_group):
        analysts, admins = groups
        attach_group(user, admins)  # revoked below: that role is gone
        login_obj = _login_with(
            {
                "userinfo": {"sub": "12345678", "email": "apollo@demo.example"},
                "id_token": {
                    "iss": "https://acme.onelogin.com/oidc/2",
                    "sub": "12345678",
                    "email": "apollo@demo.example",
                    "preferred_username": "apollo@demo.example",
                    "groups": ["Analysts"],  # User Roles, multi-value output
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_jumpcloud_connected_group_names(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "5f0e1d2c3b4a59687776655",
                    "email": "apollo@demo.example",
                    "email_verified": True,
                    "given_name": "Apollo",
                    "family_name": "M",
                    "groups": ["Analysts"],
                },
                "id_token": {
                    "iss": "https://oauth.id.jumpcloud.com/",
                    "sub": "5f0e1d2c3b4a59687776655",
                    "email": "apollo@demo.example",
                    "groups": ["Analysts"],
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_auth0_namespaced_claim(self, org, user, groups):
        analysts, admins = groups
        claim = "https://example.com/groups"
        assert len(claim) <= OrgSSOConfig._meta.get_field("groups_claim").max_length
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "auth0|64f1c2a6e0d3b4f419c3e8c1",
                    "nickname": "apollo",
                    "email": "apollo@demo.example",
                    "email_verified": True,
                    claim: ["Analysts"],
                },
                "id_token": {
                    "iss": "https://acme.eu.auth0.com/",
                    "sub": "auth0|64f1c2a6e0d3b4f419c3e8c1",
                    "email": "apollo@demo.example",
                    claim: ["Analysts"],  # Post Login Action, setCustomClaim
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(
            org, groups_claim=claim, group_map={"Analysts": analysts.pk, "Admins": admins.pk}
        )
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_keycloak_group_membership_mapper(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {
                    "sub": "c1d2e3f4-5555-6666-7777-888899990000",
                    "email_verified": True,
                    "preferred_username": "apollo",
                    "email": "apollo@demo.example",
                    "groups": ["Analysts"],  # Full group path off
                },
                "id_token": {
                    "iss": "https://keycloak.internal/realms/acme",
                    "sub": "c1d2e3f4-5555-6666-7777-888899990000",
                    "preferred_username": "apollo",
                    "email": "apollo@demo.example",
                    "groups": ["Analysts"],
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_adfs_upn_fallback_and_single_group_string(self, org, user, groups):
        analysts, admins = groups
        login_obj = _login_with(
            {
                "userinfo": {"sub": "q1w2e3r4t5y6u7i8o9p0"},  # AD FS userinfo: sub only
                "id_token": {
                    "iss": "https://fs.demo.example/adfs",
                    "sub": "q1w2e3r4t5y6u7i8o9p0",
                    "upn": "apollo@demo.example",  # no mail attribute: no email claim
                    "unique_name": "DEMO\\apollo",
                    "groups": "Analysts",  # one group: a bare string, not a list
                },
            }
        )
        assert sso.extract_email(login_obj) == "apollo@demo.example"
        cfg = _cfg(org, group_map={"Analysts": analysts.pk, "Admins": admins.pk})
        sso.sync_groups(user, cfg, sso.claims(login_obj))
        self._assert_analyst_only(user, groups)

    def test_extra_scopes_extend_allauth_default(self, org):
        base = {"org": org, "issuer_url": "https://fs.demo.example/adfs", "client_id": "c"}
        app = sso.build_transient_app(OrgSSOConfig(**base, extra_scopes="allatclaims"))
        assert app.settings["scope"] == ["openid", "profile", "email", "allatclaims"]
        assert "scope" not in sso.build_transient_app(OrgSSOConfig(**base)).settings
