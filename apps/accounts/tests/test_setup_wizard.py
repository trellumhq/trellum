"""The first-run wizard, step by step.

The contract this file defends: a fresh instance ends up with exactly one
operator, exactly one organization, and **no studios** — and no step of the
wizard can trap the operator on a machine that is only partly healthy.
"""
import pytest
from django.contrib.auth import get_user_model

from apps.core.models import InstanceConfig
from apps.orgs.models import Organization, OrgMembership
from apps.studios.models import Studio

User = get_user_model()

pytestmark = pytest.mark.django_db

ORG = {"org_name": "Demo", "org_slug": "demo"}
ACCOUNT = {
    "email": "boss@demo.example",
    "name": "Boss",
    "password1": "Sup3r-secret-pw",
    "password2": "Sup3r-secret-pw",
}


def walk_to_operator(client):
    """Steps 3 and 4 — everything up to and including the commit."""
    client.post("/setup/organization", ORG)
    return client.post("/setup/account", ACCOUNT)


class TestHappyPath:
    def test_welcome_greets_a_fresh_instance(self, client):
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert b"set up your portal" in resp.content

    def test_full_walk_ends_with_an_operator_an_org_and_no_studios(self, client):
        assert client.get("/setup").status_code == 200
        assert client.get("/setup/checks").status_code == 200
        assert client.get("/setup/organization").status_code == 200

        resp = walk_to_operator(client)
        assert resp.status_code == 302 and resp.url == "/setup/settings"

        # Skipping the optional step is a plain GET of the last one.
        assert client.get("/setup/settings").status_code == 200
        assert client.get("/setup/done").status_code == 200
        done = client.post("/setup/done")
        assert done.status_code == 302 and done.url == "/"

        assert User.objects.count() == 1
        operator = User.objects.get()
        assert operator.email == "boss@demo.example"
        assert operator.is_superuser and operator.is_staff

        assert Organization.objects.count() == 1
        org = Organization.objects.get()
        assert org.slug == "demo"
        assert OrgMembership.objects.get(user=operator, org=org).role == "admin"

        # The whole point of the end state: an empty organization.
        assert Studio.objects.count() == 0

        assert InstanceConfig.load().setup_completed_at is not None

    def test_account_step_logs_the_operator_in(self, client):
        walk_to_operator(client)
        assert client.get("/api/me").json()["email"] == "boss@demo.example"


class TestReadinessIsAdvisory:
    def test_a_failing_check_still_lets_you_continue(self, client, monkeypatch):
        monkeypatch.setattr(
            "apps.core.health.run_checks",
            lambda: [{"label": "worker", "ok": False, "detail": "no live worker heartbeat"}],
        )
        resp = client.get("/setup/checks")
        assert resp.status_code == 200
        assert b"Continue anyway" in resp.content
        # The hint for that check is shown rather than a bare failure.
        assert b"Reports will queue until one starts" in resp.content
        # And the next step is genuinely reachable.
        assert client.get("/setup/organization").status_code == 200


class TestGates:
    @pytest.mark.parametrize(
        "path", ["/setup", "/setup/checks", "/setup/organization", "/setup/account"]
    )
    def test_pre_commit_steps_close_once_a_user_exists(self, client, superuser, path):
        resp = client.get(path)
        assert resp.status_code == 302 and resp.url == "/login"

    def test_account_step_needs_the_organization_step_first(self, client):
        resp = client.get("/setup/account")
        assert resp.status_code == 302 and resp.url == "/setup/organization"

    @pytest.mark.parametrize("path", ["/setup/settings", "/setup/done"])
    def test_post_commit_steps_need_an_operator(self, client, path):
        assert client.get(path).status_code == 302

    @pytest.mark.parametrize("path", ["/setup/settings", "/setup/done"])
    def test_post_commit_steps_refuse_a_plain_member(self, login, member, path):
        assert login(member).get(path).status_code == 302

    @pytest.mark.parametrize("path", ["/setup/settings", "/setup/done"])
    def test_post_commit_steps_close_once_setup_is_complete(self, login, superuser, path):
        row = InstanceConfig.load()
        row.setup_completed_at = "2026-01-01T00:00:00Z"
        row.save()
        resp = login(superuser).get(path)
        assert resp.status_code == 302 and resp.url == "/"


class TestValidation:
    def test_duplicate_org_slug_is_refused_at_the_org_step(self, client, org):
        # `org` fixture also creates no users, so the wizard is still open.
        resp = client.post("/setup/organization", ORG)
        assert resp.status_code == 200
        assert b"already exists" in resp.content

    def test_organization_details_survive_to_the_account_step(self, client):
        client.post("/setup/organization", ORG)
        assert b"Demo" in client.get("/setup/account").content

    def test_nothing_is_created_when_the_account_step_fails(self, client):
        client.post("/setup/organization", ORG)
        resp = client.post(
            "/setup/account", {**ACCOUNT, "password2": "does-not-match-Xy1"}
        )
        assert resp.status_code == 200
        assert not User.objects.exists()
        assert not Organization.objects.exists()


class TestSettingsStep:
    def test_settings_are_saved_and_advance_to_done(self, client):
        walk_to_operator(client)
        resp = client.post(
            "/setup/settings",
            {
                "instance_name": "Demo BI",
                "public_base_url": "https://bi.demo.example",
                "email_host": "mail.demo.example",
                "email_port": "587",
                "email_use_tls": "on",
                "email_host_user": "portal",
                "email_host_password": "smtp-secret",
                "email_from": "bi@demo.example",
            },
        )
        assert resp.status_code == 302 and resp.url == "/setup/done"
        row = InstanceConfig.load()
        assert row.instance_name == "Demo BI"
        assert row.public_base_url == "https://bi.demo.example"
        assert row.email_host_password == "smtp-secret"

    def test_smtp_host_without_a_from_address_is_refused(self, client):
        walk_to_operator(client)
        resp = client.post(
            "/setup/settings",
            {"instance_name": "Demo BI", "email_host": "mail.demo.example", "email_port": "587"},
        )
        assert resp.status_code == 200
        assert not InstanceConfig.load().email_host

    def test_done_page_reports_mail_as_unconfigured_by_default(self, client, settings):
        settings.EMAIL_URL_CONFIGURED = False
        walk_to_operator(client)
        body = client.get("/setup/done").content
        assert b"not configured" in body

    def test_test_email_reports_back(self, client, mailoutbox):
        walk_to_operator(client)
        resp = client.post(
            "/setup/settings",
            {
                "instance_name": "Demo BI",
                "email_port": "587",
                "test_email": "1",
            },
        )
        assert resp.status_code == 302 and resp.url == "/setup/settings"
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["boss@demo.example"]
