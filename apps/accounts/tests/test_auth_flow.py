"""Login, logout, first-run setup, bootstrap command, /api/me."""
import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from apps.orgs.models import Organization, OrgMembership

User = get_user_model()

pytestmark = pytest.mark.django_db


class TestSetupWizard:
    """The entry conditions. The step-by-step walk lives in test_setup_wizard.py."""

    def test_fresh_instance_redirects_login_to_setup(self, client):
        resp = client.get("/login")
        assert resp.status_code == 302 and resp.url == "/setup"

    def test_setup_creates_operator_and_org(self, client):
        assert client.post(
            "/setup/organization", {"org_name": "Demo", "org_slug": "demo"}
        ).status_code == 302
        resp = client.post(
            "/setup/account",
            {
                "email": "boss@demo.example",
                "name": "Boss",
                "password1": "Sup3r-secret-pw",
                "password2": "Sup3r-secret-pw",
            },
        )
        assert resp.status_code == 302 and resp.url == "/setup/settings"
        user = User.objects.get(email="boss@demo.example")
        assert user.is_superuser
        org = Organization.objects.get(slug="demo")
        m = OrgMembership.objects.get(user=user, org=org)
        assert m.role == "admin"
        # And the wizard logged us in.
        assert client.get("/orgs/demo/", follow=True).status_code == 200

    def test_setup_locked_once_users_exist(self, client, superuser):
        assert client.get("/setup").status_code == 302

    def test_password_rules_enforced(self, client):
        client.post("/setup/organization", {"org_name": "Demo", "org_slug": "demo"})
        resp = client.post(
            "/setup/account",
            {"email": "boss@demo.example", "password1": "short", "password2": "short"},
        )
        assert resp.status_code == 200  # re-rendered with errors
        assert not User.objects.exists()


class TestBootstrapCommand:
    def test_bootstrap(self):
        call_command(
            "bootstrap",
            email="ops@demo.example",
            password="Sup3r-secret-pw",
            org_name="Demo",
            org_slug="demo",
        )
        user = User.objects.get(email="ops@demo.example")
        assert user.is_superuser
        assert OrgMembership.objects.filter(user=user, org__slug="demo", role="admin").exists()

    def test_bootstrap_refuses_second_run(self, superuser):
        with pytest.raises(CommandError):
            call_command(
                "bootstrap",
                email="x@y.z",
                password="Sup3r-secret-pw",
                org_name="X",
                org_slug="x",
            )


class TestLogin:
    def test_login_ok(self, client, member):
        resp = client.post("/login", {"email": member.email, "password": "pw-Str0ng-pw"})
        assert resp.status_code == 302
        assert client.get("/api/me").json()["email"] == member.email

    def test_login_bad_password(self, client, member):
        resp = client.post("/login", {"email": member.email, "password": "wrong"})
        assert resp.status_code == 200
        assert client.get("/api/me").status_code == 401

    def test_inactive_user_cannot_login(self, client, member):
        member.is_active = False
        member.save()
        resp = client.post("/login", {"email": member.email, "password": "pw-Str0ng-pw"})
        assert resp.status_code == 200
        assert client.get("/api/me").status_code == 401

    def test_logout_requires_post(self, login, member):
        c = login(member)
        assert c.get("/logout").status_code == 405
        assert c.post("/logout").status_code == 302
        assert c.get("/api/me").status_code == 401


class TestMeApi:
    def test_me_shape(self, login, org_admin, org):
        body = login(org_admin).get("/api/me").json()
        assert body["authenticated"] is True
        assert body["email"] == org_admin.email
        assert body["orgs"] == [{"slug": "demo", "name": "Demo", "org_admin": True}]
