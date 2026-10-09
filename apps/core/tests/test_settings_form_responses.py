"""JSON enhancement keeps the native view validation and permissions."""
import pytest
from django import forms
from django.test import RequestFactory

from apps.core.form_responses import is_settings_request, settings_error
from apps.orgs.models import OrgMembership
from apps.studios.models import StudioRepo

HEADERS = {"HTTP_X_TRELLUM_FORM": "1", "HTTP_ACCEPT": "application/json"}


def test_form_errors_have_plain_field_and_non_field_messages():
    form = forms.Form(data={})
    form.is_valid()
    form.add_error(None, "Conflict.")
    request = RequestFactory().post("/", **HEADERS)
    assert is_settings_request(request)
    assert settings_error(request, form=form).content == b'{"ok": false, "message": "Please correct the highlighted fields.", "errors": {"__all__": ["Conflict."]}}'
    assert not is_settings_request(RequestFactory().post("/", HTTP_ACCEPT="application/json"))


@pytest.mark.django_db
@pytest.mark.parametrize("page,data", [
    ("appearance", {"default_mode": "light"}),
    ("retention", {"retention_built_days": "", "retention_abandoned_upload_days": "20"}),
    ("security", {"mfa_grace_days": "7"}),
    ("api-keys", {"action": "toggle", "api_keys_enabled": "1"}),
])
def test_simple_settings_json_and_native_save_parity(login, org_admin, member, org, page, data):
    url = f"/orgs/{org.slug}/settings/{page}"
    response = login(org_admin).post(url, data, **HEADERS)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert login(org_admin).post(url, data).status_code == 302
    assert login(member).post(url, data, **HEADERS).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("page,data,field", [
    ("appearance", {"default_mode": "invalid"}, "default_mode"),
    ("retention", {"retention_built_days": "tomorrow"}, "retention_built_days"),
    ("security", {"mfa_grace_days": "bad"}, "mfa_grace_days"),
    ("assistant", {"provider": "unknown"}, "provider"),
    ("sso", {"issuer_url": "http://example.test"}, "issuer_url"),
])
def test_invalid_settings_return_field_errors_without_redirect(login, org_admin, org, page, data, field):
    response = login(org_admin).post(f"/orgs/{org.slug}/settings/{page}", data, **HEADERS)
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert field in response.json()["errors"]


@pytest.mark.django_db
def test_member_last_admin_guard_unchanged_for_json(login, org_admin, org):
    url = f"/orgs/{org.slug}/settings/members/{org_admin.pk}/role"
    response = login(org_admin).post(url, {"role": "member"}, **HEADERS)
    assert response.status_code == 400
    assert "role" in response.json()["errors"]
    assert OrgMembership.objects.get(user=org_admin, org=org).role == "admin"


@pytest.mark.django_db
def test_group_validation_and_summary_refresh_contract(login, org_admin, org, make_group):
    group = make_group("Existing")
    url = f"/orgs/{org.slug}/settings/groups/{group.pk}"
    response = login(org_admin).post(url, {"action": "edit", "name": ""}, **HEADERS)
    assert response.status_code == 400
    assert "name" in response.json()["errors"]
    response = login(org_admin).post(url, {"action": "edit", "name": "Renamed"}, **HEADERS)
    assert response.status_code == 200
    assert response.json()["values"]["name"] == "Renamed"
    assert response.json()["refresh"] == ["group-heading", "group-summary"]


@pytest.mark.django_db
def test_repo_settings_json_configuration_mode_sync_and_validation(login, org_admin, org, studio):
    url = f"/s/{org.slug}/{studio.slug}/settings/repo"
    data = {"repo_url": "https://example.test/repo.git", "branch": "main", "auth_method": "https_token", "path": "reports", "sync_interval_minutes": "5", "publish_mode": "manual"}
    response = login(org_admin).post(url, data, **HEADERS)
    assert response.status_code == 200, response.content
    assert response.json()["repo_configured"] is True
    assert response.json()["values"] == {"token": "", "webhook_secret": ""}
    repo = StudioRepo.objects.get(studio=studio)
    assert repo.publish_mode == "manual"
    response = login(org_admin).post(url, {"set_mode": "1", "publish_mode": "auto"}, **HEADERS)
    assert response.json()["publish_mode"] == "auto"
    response = login(org_admin).post(url, {"sync_now": "1"}, **HEADERS)
    assert response.json()["sync_requested"] is True
    response = login(org_admin).post(url, {"set_default_audiences": "1", "default_report_audience": "wrong", "default_analysis_audience": "studio"}, **HEADERS)
    assert response.status_code == 400
    assert "default_report_audience" in response.json()["errors"]


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint,data,page", [
    ("share_policy", {"enabled": "1", "max_expiry_days": "2"}, "sharing"),
    ("live_query_policy", {"rate_limit_per_minute": "12"}, "live-queries"),
])
def test_policy_forms_support_shared_driver_and_native_fallback(login, org_admin, member, org, endpoint, data, page):
    url = f"/orgs/{org.slug}/api/{endpoint}"
    response = login(org_admin).post(url, data, **HEADERS)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    response = login(org_admin).post(url, data)
    assert response.status_code == 302
    assert response["Location"] == f"/orgs/{org.slug}/settings/{page}"
    assert login(member).post(url, data, **HEADERS).status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["share_policy", "live_query_policy"])
@pytest.mark.parametrize("content_type", ["application/octet-stream", "text/plain", "application/json", "multipart/form-data"])
def test_empty_or_unsupported_policy_body_does_not_save(login, org_admin, org, endpoint, content_type):
    from apps.reports.models import OrgSharePolicy, OrgLiveQueryPolicy
    url = f"/orgs/{org.slug}/api/{endpoint}"
    payload = {} if content_type == "multipart/form-data" else b""
    if content_type == "multipart/form-data":
        response = login(org_admin).post(url, payload, **HEADERS)
    else:
        response = login(org_admin).post(url, payload, content_type=content_type, **HEADERS)
    assert response.status_code == 400
    assert not OrgSharePolicy.objects.filter(org=org).exists()
    assert not OrgLiveQueryPolicy.objects.filter(org=org).exists()


@pytest.mark.django_db
def test_successful_assistant_and_sso_configuration_returns_no_secrets(login, org_admin, org):
    assistant = {"provider": "openai", "model": "gpt-example", "api_key": "synthetic-test-key", "monthly_budget_usd": "1", "per_user_budget_usd": "1", "price_in_per_mtok": "0", "price_out_per_mtok": "0"}
    response = login(org_admin).post(f"/orgs/{org.slug}/settings/assistant", assistant, **HEADERS)
    assert response.status_code == 200, response.content
    assert response.json()["values"]["api_key"] == ""
    assert b"synthetic-test-key" not in response.content
    sso = {"auth_method": "oidc", "issuer_url": "", "client_id": "", "client_secret": "synthetic-secret", "default_org_role": "member", "groups_claim": "groups"}
    response = login(org_admin).post(f"/orgs/{org.slug}/settings/sso", sso, **HEADERS)
    assert response.status_code == 200, response.content
    assert response.json()["values"]["client_secret"] == ""
    assert b"synthetic-secret" not in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("scope,title", [("org", "Console theme"), ("studio", "Report theme"), ("report", "Access to")])
def test_settings_page_title_has_no_script_content(login, org_admin, org, studio, report_row, scope, title):
    import re
    report = report_row
    paths = {"org": f"/orgs/{org.slug}/settings/appearance", "studio": f"/s/{org.slug}/{studio.slug}/settings/appearance", "report": f"/s/{org.slug}/{studio.slug}/r/{report.slug}/access"}
    response = login(org_admin).get(paths[scope])
    assert response.status_code == 200
    rendered_title = re.search(r"<title>(.*?)</title>", response.content.decode(), re.S).group(1)
    assert title in rendered_title
    assert "<script>" not in rendered_title
    assert "addEventListener" not in rendered_title
