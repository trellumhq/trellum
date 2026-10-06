"""Invitation lifecycle: create, accept (new + existing user), expiry, revoke."""
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import Invitation
from apps.core import roles
from apps.orgs.models import OrgMembership, PermissionGroupMembership
from apps.studios.models import StudioMembership

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def invite(org, org_admin, make_group, studio):
    inv = Invitation.objects.create(
        org=org,
        email="newbie@demo.example",
        org_role=roles.ORG_MEMBER,
        invited_by=org_admin,
        studio_grants=[{"studio_id": studio.pk, "role": roles.DEVELOPER}],
    )
    inv.groups.add(make_group("Demo BA", default_studio_role=roles.VIEWER))
    return inv


class TestCreate:
    def test_admin_creates_invite_and_sees_link(self, login, org_admin, org):
        c = login(org_admin)
        resp = c.post(
            f"/orgs/{org.slug}/settings/invites",
            {"email": "new@demo.example", "org_role": "member"},
        )
        assert resp.status_code == 200
        inv = Invitation.objects.get(email="new@demo.example")
        assert inv.token in resp.content.decode()

    def test_invite_carries_studio_grants_from_form(self, login, org_admin, org, studio):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites",
            {
                "email": "granted@demo.example",
                "org_role": "member",
                f"studio_role_{studio.pk}": "developer",
            },
        )
        inv = Invitation.objects.get(email="granted@demo.example")
        assert inv.studio_grants == [{"studio_id": studio.pk, "role": "developer"}]

    def test_blank_studio_roles_produce_no_grants(self, login, org_admin, org, studio):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites",
            {"email": "plain@demo.example", "org_role": "member", f"studio_role_{studio.pk}": ""},
        )
        assert Invitation.objects.get(email="plain@demo.example").studio_grants == []

    def test_cannot_invite_existing_member(self, login, org_admin, org, member):
        login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites",
            {"email": member.email, "org_role": "member"},
        )
        assert not Invitation.objects.filter(email=member.email).exists()

    def test_member_cannot_create_invites(self, login, member, org):
        resp = login(member).post(
            f"/orgs/{org.slug}/settings/invites",
            {"email": "x@y.z", "org_role": "member"},
        )
        assert resp.status_code == 403


class TestAccept:
    def test_new_user_accept_applies_everything(self, client, invite, org, studio):
        resp = client.get(f"/invite/{invite.token}")
        assert resp.status_code == 200
        resp = client.post(
            f"/invite/{invite.token}",
            {"name": "Newbie", "password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"},
        )
        assert resp.status_code == 302
        user = User.objects.get(email="newbie@demo.example")
        assert OrgMembership.objects.filter(user=user, org=org, role="member").exists()
        assert PermissionGroupMembership.objects.filter(
            user=user, group__name="Demo BA"
        ).exists()
        assert StudioMembership.objects.filter(
            user=user, studio=studio, role=roles.DEVELOPER
        ).exists()
        invite.refresh_from_db()
        assert invite.is_accepted and invite.accepted_by == user
        # Logged in and can see the org.
        assert client.get(f"/orgs/{org.slug}/", follow=True).status_code == 200

    def test_expired_invite_410(self, client, invite):
        invite.expires_at = timezone.now() - timezone.timedelta(days=1)
        invite.save()
        assert client.get(f"/invite/{invite.token}").status_code == 410

    def test_accepted_invite_cannot_be_reused(self, client, invite):
        client.post(
            f"/invite/{invite.token}",
            {"password1": "Sup3r-secret-pw", "password2": "Sup3r-secret-pw"},
        )
        client.post("/logout")
        assert client.get(f"/invite/{invite.token}").status_code == 410

    def test_bogus_token_410(self, client, db):
        assert client.get("/invite/not-a-real-token").status_code == 410

    def test_logged_in_wrong_email_403(self, login, member, invite):
        resp = login(member).get(f"/invite/{invite.token}")
        assert resp.status_code == 403

    def test_logged_in_matching_email_attaches(self, make_user, login, invite, org):
        user = make_user("newbie@demo.example")  # exists, not yet a member
        resp = login(user).get(f"/invite/{invite.token}")
        assert resp.status_code == 302
        assert OrgMembership.objects.filter(user=user, org=org).exists()

    def test_existing_account_logged_out_redirects_to_login(self, client, make_user, invite):
        make_user("newbie@demo.example")
        resp = client.get(f"/invite/{invite.token}")
        assert resp.status_code == 302
        assert resp.url == f"/login?next=/invite/{invite.token}"


class TestRevoke:
    def test_revoke(self, login, org_admin, org, invite):
        resp = login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites/{invite.pk}/revoke"
        )
        assert resp.status_code == 302
        assert not Invitation.objects.filter(pk=invite.pk).exists()
