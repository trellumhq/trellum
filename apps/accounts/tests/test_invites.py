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


class TestInvitationEmail:
    @pytest.fixture(autouse=True)
    def mail_route(self, monkeypatch):
        from unittest.mock import Mock
        self.configured = Mock(return_value=True)
        self.send = Mock(return_value=1)
        monkeypatch.setattr("apps.core.instance.mail_is_configured", self.configured)
        monkeypatch.setattr("django.core.mail.send_mail", self.send)

    def test_creation_default_and_opt_out(self, login, org_admin, org):
        c = login(org_admin)
        url = f"/orgs/{org.slug}/settings/invites"
        form = c.get(url).context["form"]
        assert form["send_email"].value() is True
        response = c.post(url, {"email": "email@demo.example", "org_role": "member", "send_email": "on"})
        assert "submitted to the email provider" in response.content.decode()
        self.send.assert_called_once()
        assert self.send.call_args.kwargs["fail_silently"] is False
        invitation = Invitation.objects.get(email="email@demo.example")
        assert invitation.accept_url() in self.send.call_args.kwargs["message"]
        assert invitation.token in response.content.decode()
        self.send.reset_mock()
        response = c.post(url, {"email": "manual@demo.example", "org_role": "member"})
        self.send.assert_not_called()
        assert "Email was not requested" in response.content.decode()

    @pytest.mark.parametrize("failure", [0, RuntimeError("private-provider-secret")])
    def test_failure_keeps_created_link(self, failure, login, org_admin, org):
        if isinstance(failure, Exception):
            self.send.side_effect = failure
        else:
            self.send.return_value = failure
        response = login(org_admin).post(f"/orgs/{org.slug}/settings/invites", {
            "email": "failed@demo.example", "org_role": "member", "send_email": "on",
        })
        text = response.content.decode()
        assert "could not be confirmed" in text
        assert "private-provider-secret" not in text
        assert Invitation.objects.get(email="failed@demo.example").token in text

    def test_unconfigured_disables_send_and_ignores_forged_option(self, login, org_admin, org):
        self.configured.return_value = False
        c = login(org_admin)
        url = f"/orgs/{org.slug}/settings/invites"
        response = c.get(url)
        assert response.context["form"].fields["send_email"].disabled
        assert response.context["form"]["send_email"].value() is False
        assert "Email delivery is not configured" in response.content.decode()
        c.post(url, {"email": "manual@demo.example", "org_role": "member", "send_email": "on"})
        self.send.assert_not_called()

    def test_validation_retains_email_choice(self, login, org_admin, org):
        response = login(org_admin).post(f"/orgs/{org.slug}/settings/invites", {
            "email": "invalid", "org_role": "member", "send_email": "on",
        })
        assert response.context["form"]["send_email"].value() is True
        self.send.assert_not_called()

    def test_resend_same_link_native_and_enhanced(self, login, org_admin, org, invite):
        c = login(org_admin)
        url = f"/orgs/{org.slug}/settings/invites/{invite.pk}/resend"
        token, expiry, grants = invite.token, invite.expires_at, invite.studio_grants
        assert c.get(url).status_code == 405
        assert c.post(url).status_code == 302
        response = c.post(url, HTTP_X_TRELLUM_FORM="1", HTTP_ACCEPT="application/json")
        assert response.json()["ok"] is True
        assert "Inbox delivery is not confirmed" in response.json()["message"]
        invite.refresh_from_db()
        assert (invite.token, invite.expires_at, invite.studio_grants) == (token, expiry, grants)

    @pytest.mark.parametrize("state", ["expired", "accepted", "unconfigured", "failed", "revoked", "other-org"])
    def test_resend_rejects_unavailable(self, state, login, org_admin, org, invite, other_org):
        url = f"/orgs/{org.slug}/settings/invites/{invite.pk}/resend"
        if state == "expired":
            invite.expires_at = timezone.now() - timezone.timedelta(seconds=1)
            invite.save()
        elif state == "accepted":
            invite.accepted_at = timezone.now()
            invite.save()
        elif state == "unconfigured":
            self.configured.return_value = False
        elif state == "failed":
            self.send.return_value = 0
        elif state == "revoked":
            invite.delete()
        elif state == "other-org":
            invite.org = other_org
            invite.save()
        response = login(org_admin).post(url, HTTP_X_TRELLUM_FORM="1", HTTP_ACCEPT="application/json")
        assert response.status_code >= 400
        if state not in {"accepted", "revoked", "other-org"}:
            assert response.json()["ok"] is False
        if state != "failed":
            self.send.assert_not_called()

    def test_resend_preserves_admin_and_csrf_checks(self, login, member, org_admin, org, invite):
        from django.test import Client
        url = f"/orgs/{org.slug}/settings/invites/{invite.pk}/resend"
        assert login(member).post(url).status_code == 403
        c = Client(enforce_csrf_checks=True)
        c.force_login(org_admin)
        assert c.post(url).status_code == 403
        self.send.assert_not_called()

    @pytest.mark.parametrize("enhanced", [False, True])
    @pytest.mark.parametrize("delivery_status, audit_outcome", [
        ("accepted", "success"), ("failed", "failure"),
        ("unconfigured", "denied"), ("expired", "denied"),
    ])
    def test_resend_persists_canonical_audit_outcome(
        self, enhanced, delivery_status, audit_outcome, login, org_admin, org, invite,
    ):
        from apps.core.models import AuditLog

        if delivery_status == "unconfigured":
            self.configured.return_value = False
        elif delivery_status == "failed":
            self.send.side_effect = RuntimeError("private-provider-secret")
        elif delivery_status == "expired":
            invite.expires_at = timezone.now() - timezone.timedelta(seconds=1)
            invite.save(update_fields=["expires_at"])
        headers = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"} if enhanced else {}
        response = login(org_admin).post(
            f"/orgs/{org.slug}/settings/invites/{invite.pk}/resend", **headers,
        )
        expected_status = (200 if delivery_status == "accepted" else 400) if enhanced else 302
        assert response.status_code == expected_status
        rows = AuditLog.objects.filter(action="member.invite_resend", target_id=str(invite.pk))
        assert rows.count() == 1
        row = rows.get()
        assert row.org_id == org.pk
        assert row.actor_id == org_admin.pk
        assert row.outcome == audit_outcome
        assert row.metadata["delivery_status"] == delivery_status
        assert row.metadata["email"] == invite.email
        assert "private-provider-secret" not in str(row.metadata)
        assert invite.token not in str(row.metadata)
        assert self.send.call_count == int(delivery_status in {"accepted", "failed"})
