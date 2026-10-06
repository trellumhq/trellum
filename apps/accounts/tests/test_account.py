"""Account page (name/password) and the password-reset flow."""
import re

import pytest
from django.core import mail

pytestmark = pytest.mark.django_db


class TestAccountPage:
    def test_requires_login(self, client, db):
        assert client.get("/account").status_code == 302

    def test_change_display_name(self, login, member):
        c = login(member)
        resp = c.post("/account", {"action": "profile", "name": "Apollo M."})
        assert resp.status_code == 302
        member.refresh_from_db()
        assert member.name == "Apollo M."

    def test_change_password_keeps_session(self, login, member):
        c = login(member)
        resp = c.post(
            "/account",
            {
                "action": "password",
                "old_password": "pw-Str0ng-pw",
                "new_password1": "Fresh-new-pw-9",
                "new_password2": "Fresh-new-pw-9",
            },
        )
        assert resp.status_code == 302
        assert c.get("/api/me").status_code == 200  # still logged in
        c.post("/logout")
        assert c.login(username=member.email, password="Fresh-new-pw-9")

    def test_wrong_old_password_rejected(self, login, member):
        c = login(member)
        resp = c.post(
            "/account",
            {
                "action": "password",
                "old_password": "wrong",
                "new_password1": "Fresh-new-pw-9",
                "new_password2": "Fresh-new-pw-9",
            },
        )
        assert resp.status_code == 200  # re-rendered with errors
        c.post("/logout")
        assert not c.login(username=member.email, password="Fresh-new-pw-9")

    def test_account_link_in_user_menu(self, login, member):
        assert '/account' in login(member).get("/").content.decode()


class TestPasswordReset:
    def test_full_reset_flow(self, client, member):
        assert client.get("/password-reset/").status_code == 200
        resp = client.post("/password-reset/", {"email": member.email})
        assert resp.status_code == 302
        assert len(mail.outbox) == 1
        link = re.search(r"http://[^\s]+/password-reset/[^\s]+", mail.outbox[0].body).group(0)
        path = "/" + link.split("/", 3)[3]
        # Django redirects the tokened URL to a session-backed set-password URL.
        resp = client.get(path, follow=True)
        assert resp.status_code == 200
        set_url = resp.redirect_chain[-1][0] if resp.redirect_chain else path
        resp = client.post(
            set_url,
            {"new_password1": "Reset-pw-77x", "new_password2": "Reset-pw-77x"},
        )
        assert resp.status_code == 302
        assert client.login(username=member.email, password="Reset-pw-77x")

    def test_unknown_email_no_leak_no_mail(self, client, member):
        resp = client.post("/password-reset/", {"email": "ghost@nowhere.io"})
        assert resp.status_code == 302  # same response either way
        assert len(mail.outbox) == 0

    def test_login_page_links_reset(self, client, member):
        assert "/password-reset/" in client.get("/login").content.decode()


class TestDeleteAccount:
    """Self-service erasure from /account. The org-admin path is covered in
    apps/orgs/tests/test_personal_data.py; these tests cover only what the
    self-service wrapper adds: its guards, its confirmations, and the
    erase-everywhere loop."""

    def _delete(self, c, user, **extra):
        data = {"confirm_email": user.email, "password": "pw-Str0ng-pw"}
        data.update(extra)
        return c.post("/account/delete", data)

    def test_requires_login(self, client, db):
        assert client.post("/account/delete").status_code == 302

    def test_get_deletes_nothing(self, login, member):
        c = login(member)
        assert c.get("/account/delete").status_code == 302
        member.refresh_from_db()
        assert member.erased_at is None

    def test_wrong_email_deletes_nothing(self, login, member):
        c = login(member)
        self._delete(c, member, confirm_email="someone-else@demo.example")
        member.refresh_from_db()
        assert member.erased_at is None

    def test_wrong_password_deletes_nothing(self, login, member):
        c = login(member)
        self._delete(c, member, password="wrong")
        member.refresh_from_db()
        assert member.erased_at is None

    def test_happy_path_tombstones_ends_session_and_says_so(self, login, member):
        c = login(member)
        resp = self._delete(c, member)
        assert resp.status_code == 302 and resp.url == "/login"
        member.refresh_from_db()
        assert member.erased_at is not None
        assert "member@demo.example" not in member.email
        # The session died with the account, and the banner survived the
        # logout (cookie-backed message storage).
        assert c.get("/account").status_code == 302
        assert b"Your account has been deleted" in c.get("/login").content

    def test_sso_style_account_needs_no_password(self, client, make_user, org):
        # SSO-provisioned accounts have unusable passwords; the typed email
        # is their whole confirmation.
        user = make_user("sso@demo.example", org=org)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        client.force_login(user)
        client.post("/account/delete", {"confirm_email": user.email})
        user.refresh_from_db()
        assert user.erased_at is not None

    def test_sole_admin_is_refused_and_the_org_is_named(self, login, org_admin):
        c = login(org_admin)
        resp = self._delete(c, org_admin)
        assert resp.status_code == 302
        org_admin.refresh_from_db()
        assert org_admin.erased_at is None
        assert b"only admin of Demo" in c.get("/account").content

    def test_admin_beside_another_admin_may_leave(self, login, org_admin, make_user, org):
        from apps.core import roles as r

        make_user("second-admin@demo.example", org=org, org_role=r.ORG_ADMIN)
        c = login(org_admin)
        self._delete(c, org_admin)
        org_admin.refresh_from_db()
        assert org_admin.erased_at is not None

    @pytest.mark.parametrize("flag", ["is_superuser", "is_operator_flag"])
    def test_operator_accounts_are_refused(self, login, make_user, org, flag):
        user = make_user(f"op-{flag}@demo.example", org=org)
        setattr(user, flag, True)
        user.save(update_fields=[flag])
        c = login(user)
        self._delete(c, user)
        user.refresh_from_db()
        assert user.erased_at is None

    def test_an_impersonator_cannot_delete_the_account(self, login, member, superuser):
        from apps.core.impersonation import SESSION_OPERATOR_ID

        c = login(member)
        session = c.session
        session[SESSION_OPERATOR_ID] = superuser.pk
        session.save()
        self._delete(c, member)
        member.refresh_from_db()
        assert member.erased_at is None

    def test_two_org_member_is_erased_everywhere(self, login, make_user, org, other_org):
        from apps.core.models import AuditLog
        from apps.orgs.models import OrgMembership as OM

        user = make_user("both@demo.example", org=org)
        OM.objects.create(user=user, org=other_org)
        c = login(user)
        self._delete(c, user)
        user.refresh_from_db()
        assert user.erased_at is not None
        assert OM.objects.filter(user=user).count() == 0
        rows = AuditLog.objects.filter(action="person.erase", target_id=str(user.pk))
        assert rows.count() == 2
        for row in rows:
            # actor == target is the self-service marker, and the row must
            # not undo the erasure by recording the address.
            assert row.actor_id == user.pk
            assert "both@demo.example" not in str(row.metadata)
