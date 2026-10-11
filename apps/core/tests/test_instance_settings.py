"""Moved settings preserve native and enhanced persistence and access rules."""
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog, InstanceConfig

pytestmark = pytest.mark.django_db
ENHANCED = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}
INSTANCE = {"instance_name": "Saved instance", "email_port": "587",
            "public_base_url": "https://portal.example.com/", "max_upload_mb": "42"}
SECURITY = {"action": "security_settings", "session_idle_minutes": "60",
            "session_absolute_hours": "24", "lockout_account_threshold": "5",
            "lockout_ip_threshold": "20", "lockout_window_minutes": "10",
            "lockout_cooloff_minutes": "20"}


@pytest.mark.parametrize("path", ["/system", "/instance/settings"])
@pytest.mark.parametrize("enhanced", [False, True])
def test_instance_save_and_legacy_post_preserve_secrets(login, superuser, path, enhanced):
    config = InstanceConfig.load()
    config.email_host_password = "stored-secret"
    config.save()
    response = login(superuser).post(path, INSTANCE, **(ENHANCED if enhanced else {}))
    config.refresh_from_db()
    assert config.instance_name == "Saved instance"
    assert config.public_base_url == "https://portal.example.com"
    assert config.max_upload_mb == 42
    assert config.email_host_password == "stored-secret"
    assert AuditLog.objects.filter(action="instance.settings_update").count() == 1
    if enhanced:
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["values"]["public_base_url"] == config.public_base_url
        assert "email_host_password" not in response.json()["values"]
        assert "redirect" not in response.json()
    else:
        assert response.status_code == 302
        assert response.url == reverse("instance-settings")


@pytest.mark.parametrize("path", ["/system", "/instance/settings"])
@pytest.mark.parametrize("enhanced", [False, True])
def test_invalid_instance_draft_is_retained(login, superuser, path, enhanced):
    response = login(superuser).post(path, {**INSTANCE, "max_upload_mb": "-1"},
                                     **(ENHANCED if enhanced else {}))
    assert InstanceConfig.load().instance_name != INSTANCE["instance_name"]
    assert not AuditLog.objects.filter(action="instance.settings_update").exists()
    if enhanced:
        assert response.status_code == 400
        assert response.json()["ok"] is False
        assert "max_upload_mb" in response.json()["errors"]
    else:
        assert response.status_code == 200
        assert response.context["form"].is_bound
        assert response.context["form"]["instance_name"].value() == INSTANCE["instance_name"]
        assert "max_upload_mb" in response.context["form"].errors
        assert b"Instance settings" in response.content


@pytest.mark.parametrize("path", ["/system", "/instance/settings"])
def test_security_save_invalidates_cache_and_retains_invalid_draft(login, superuser, path):
    client = login(superuser)
    with patch("apps.accounts.session_policy.invalidate_policy_cache") as invalidate:
        response = client.post(path, SECURITY, **ENHANCED)
    assert response.json()["ok"] is True
    invalidate.assert_called_once()
    assert InstanceConfig.load().session_idle_minutes == 60
    response = client.post(path, {**SECURITY, "session_idle_minutes": "-2"})
    assert response.status_code == 200
    assert response.context["security_form"]["session_idle_minutes"].value() == "-2"
    assert "session_idle_minutes" in response.context["security_form"].errors
    assert InstanceConfig.load().session_idle_minutes == 60


def test_health_and_settings_have_distinct_content_and_titles(login, superuser):
    client = login(superuser)
    with patch("apps.core.health.run_checks", side_effect=AssertionError("settings ran diagnostics")):
        response = client.get(reverse("instance-settings"))
    assert response.context["console_page_title"] == "Instance settings"
    assert b'data-settings-save' in response.content
    assert b'Send a test email using saved settings' in response.content
    assert b'Save and send a test email' not in response.content
    health = client.get("/system")
    assert health.status_code == 200
    assert "form" not in health.context
    assert "recent_runs" not in health.context
    assert "workers" not in health.context
    assert b'Open Fleet' in health.content


def test_explicit_test_does_not_save_and_legacy_save_reports_both_outcomes(login, superuser):
    client = login(superuser)
    with patch("apps.core.mail.send_test_email", side_effect=RuntimeError("private provider detail")) as send:
        response = client.post("/instance/settings", {**INSTANCE, "action": "test_email"}, follow=True)
        assert InstanceConfig.load().instance_name != INSTANCE["instance_name"]
        assert b"Test email failed" in response.content
        response = client.post("/system", {**INSTANCE, "test_email": "1"}, follow=True)
    assert send.call_count == 2
    assert InstanceConfig.load().instance_name == INSTANCE["instance_name"]
    assert b"Instance settings saved." in response.content
    assert b"Test email failed" in response.content
    assert b"private provider detail" not in response.content
    assert response.redirect_chain[0][0] == reverse("instance-settings") + "#email-delivery"


def test_settings_operator_gate_nav_and_csrf(login, make_user, member, superuser):
    support = make_user("settings-support@example.com")
    support.is_operator_flag = True
    support.save()
    response = login(support).get(reverse("instance-settings"))
    assert response.status_code == 200
    assert b'href="/instance/settings"' in response.content
    assert b'href="/orgs/new"' not in response.content
    client = login(member)
    assert client.get(reverse("instance-settings")).status_code == 404
    assert client.post(reverse("instance-settings"), INSTANCE, **ENHANCED).status_code == 404
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)
    assert client.post(reverse("instance-settings"), INSTANCE, **ENHANCED).status_code == 403


def test_native_invalid_smtp_draft_does_not_change_saved_route_summary(login, superuser, settings):
    settings.EMAIL_URL_CONFIGURED = False
    response = login(superuser).post("/system", {**INSTANCE, "email_host": "draft.example.com"})
    assert response.status_code == 200
    assert "email_from" in response.context["form"].errors
    assert response.context["form"]["email_host"].value() == "draft.example.com"
    assert response.context["legacy_email_route"] == "Console only"
    assert InstanceConfig.load().email_host == ""
