"""Operator route changes and write-only email connection editor."""
from unittest.mock import patch
from types import SimpleNamespace
import json

import pytest
from django.test import Client
from django.test import RequestFactory
from django.views.debug import SafeExceptionReporterFilter

from apps.core.models import EmailApiConnection, InstanceConfig

pytestmark = pytest.mark.django_db


def _sendgrid(name="Primary", key="a-secret"):
    return {"name": name, "provider": "sendgrid", "from_email": "sender@example.com",
            "region": "global", "api_key": key}


def _create(client, name="Primary", key="a-secret"):
    response = client.post("/system/email-connections/new", _sendgrid(name, key))
    assert response.status_code == 302, response.content.decode()[:500]
    return EmailApiConnection.objects.get(name=name)


@pytest.mark.parametrize("provider, options, secrets", [
    ("sendgrid", {"region": "eu"}, {"api_key": "sendgrid-secret"}),
    ("amazon_ses", {"region": "eu-west-1", "credential_source": "deployment"}, {}),
    ("mailgun", {"region": "us", "sender_domain": "mail.example.com"}, {"api_key": "mailgun-secret"}),
    ("postmark", {"message_stream": "outbound"}, {"server_token": "postmark-secret"}),
    ("brevo", {}, {"api_key": "brevo-secret"}),
    ("resend", {}, {"api_key": "resend-secret"}),
    ("mailjet", {}, {"api_key": "mailjet-key", "secret_key": "mailjet-secret"}),
    ("mailersend", {}, {"api_token": "mailersend-secret"}),
    ("mailtrap", {}, {"api_token": "mailtrap-secret"}),
])
def test_preset_create_get_and_blank_edit_preserve_each_secret(login, superuser, provider, options, secrets):
    client = login(superuser)
    data = {"name": f"Route {provider}", "provider": provider,
            "from_email": "sender@example.com", **options, **secrets}
    response = client.post("/system/email-connections/new", data)
    assert response.status_code == 302, response.context["form"].errors if response.context else ""
    profile = EmailApiConnection.objects.get(name=data["name"])
    assert profile.credentials == secrets
    page = client.get(f"/system/email-connections/{profile.pk}")
    assert page.status_code == 200
    for secret in secrets.values():
        assert secret.encode() not in page.content
    preserved = {**data, **{key: "" for key in secrets}}
    assert client.post(f"/system/email-connections/{profile.pk}", preserved).status_code == 302
    profile.refresh_from_db()
    assert profile.credentials == secrets
    assert profile.config_revision == 1


def test_ses_access_keys_and_session_token_clear(login, superuser):
    client = login(superuser)
    data = {
        "name": "SES keys", "provider": "amazon_ses", "from_email": "sender@example.com",
        "region": "eu-west-1", "credential_source": "access_keys",
        "aws_access_key_id": "AKIAEXAMPLE", "aws_secret_access_key": "aws-secret",
        "aws_session_token": "session-secret",
    }
    assert client.post("/system/email-connections/new", data).status_code == 302
    profile = EmailApiConnection.objects.get(name="SES keys")
    assert profile.credentials["aws_session_token"] == "session-secret"
    for key in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
        data[key] = ""
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 302
    profile.refresh_from_db()
    assert profile.credentials["aws_session_token"] == "session-secret"
    data["clear_aws_session_token"] = "on"
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 302
    profile.refresh_from_db()
    assert "aws_session_token" not in profile.credentials
    data["credential_source"] = "deployment"
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 302
    profile.refresh_from_db()
    assert profile.credentials == {}


def test_blank_custom_auth_is_form_error(login, superuser):
    client = login(superuser)
    data = _custom_data()
    data["auth_type"] = ""
    response = client.post("/system/email-connections/new", data)
    assert response.status_code == 200
    assert b"Choose an authentication method" in response.content
    assert EmailApiConnection.objects.count() == 0


def test_read_and_preview_never_decrypt_saved_credentials(login, superuser):
    from apps.core.crypto import EncryptedJSONField

    client = login(superuser)
    data = _custom_data()
    assert client.post("/system/email-connections/new", data).status_code == 302
    profile = EmailApiConnection.objects.get(name="Custom")
    data["action"] = "preview"
    data["token"] = ""
    with patch.object(EncryptedJSONField, "from_db_value", side_effect=AssertionError("credential read")):
        editor = client.get(f"/system/email-connections/{profile.pk}")
        system = client.get("/system")
        preview = client.post(f"/system/email-connections/{profile.pk}", data)
    assert editor.status_code == system.status_code == preview.status_code == 200
    assert b"Preview" in preview.content


def test_create_edit_preserves_secret_and_revision(login, superuser):
    client = login(superuser)
    first = _create(client)
    assert first.credentials["api_key"] == "a-secret"
    page = client.get(f"/system/email-connections/{first.pk}")
    assert b"a-secret" not in page.content
    data = _sendgrid("Renamed", "")
    response = client.post(f"/system/email-connections/{first.pk}", data)
    assert response.status_code == 302
    first.refresh_from_db()
    assert first.name == "Renamed"
    assert first.credentials["api_key"] == "a-secret"
    assert first.config_revision == 1
    data["api_key"] = "new-secret"
    assert client.post(f"/system/email-connections/{first.pk}", data).status_code == 302
    first.refresh_from_db()
    assert first.credentials["api_key"] == "new-secret"
    assert first.config_revision == 2


def test_old_result_is_marked_stale_after_secret_rotation(login, superuser):
    client = login(superuser)
    profile = _create(client)
    profile.last_test_results = {"simple": {
        "revision": 1, "checked_at": "2026-10-08T12:00:00+00:00", "outcome": "accepted",
    }}
    profile.save(update_fields=["last_test_results"])
    assert client.post(f"/system/email-connections/{profile.pk}",
                       _sendgrid(key="rotated-secret")).status_code == 302
    assert b"Stale: configuration changed" in client.get("/system").content


def test_select_test_inactive_and_delete_guard(login, superuser):
    client = login(superuser)
    first = _create(client)
    other = _create(client, "Other", "other-secret")
    config = InstanceConfig.load()
    assert config.active_email_api_connection_id is None
    assert client.post(f"/system/email-connections/{first.pk}/use").status_code == 302
    config.refresh_from_db()
    assert config.active_email_api_connection_id == first.pk
    connection = SimpleNamespace(snapshot=SimpleNamespace(config_revision=1))
    with patch("apps.core.mail.resolve_delivery_connection", return_value=connection) as resolve, \
         patch("apps.core.mail.send_test_email") as send:
        assert client.post(f"/system/email-connections/{other.pk}/test", {"kind": "simple"}).status_code == 302
        resolve.assert_called_once()
        assert resolve.call_args.kwargs["api_profile"].pk == other.pk
        send.assert_called_once()
        assert send.call_args.args[0] == superuser.email
    config.refresh_from_db()
    assert config.active_email_api_connection_id == first.pk
    other.refresh_from_db()
    assert other.last_test_results["simple"]["revision"] == 1
    assert other.last_test_results["simple"]["outcome"] == "accepted"
    client.post(f"/system/email-connections/{first.pk}/delete")
    assert EmailApiConnection.objects.filter(pk=first.pk).exists()
    client.post("/system/email-connections/use-smtp")
    client.post(f"/system/email-connections/{first.pk}/delete")
    assert not EmailApiConnection.objects.filter(pk=first.pk).exists()


def test_test_failure_is_bounded_and_does_not_switch_routes(login, superuser):
    from apps.core.email_errors import EmailDeliveryError

    client = login(superuser)
    profile = _create(client)
    connection = SimpleNamespace(snapshot=SimpleNamespace(config_revision=1))
    with patch("apps.core.mail.resolve_delivery_connection", return_value=connection), \
         patch("apps.core.mail.send_test_email",
               side_effect=EmailDeliveryError("http_rejected", outcome="rejected", status=401)):
        response = client.post(f"/system/email-connections/{profile.pk}/test",
                               {"kind": "simple"}, follow=True)
    assert response.status_code == 200
    assert b"http_rejected" in response.content
    assert b"a-secret" not in response.content
    profile.refresh_from_db()
    assert profile.last_test_results["simple"]["outcome"] == "rejected"
    assert profile.last_test_results["simple"]["status"] == 401
    assert InstanceConfig.load().active_email_api_connection_id is None


@pytest.mark.parametrize("path", [
    "/system/email-connections/new", "/system/email-connections/1",
    "/system/email-connections/1/use", "/system/email-connections/1/test",
    "/system/email-connections/use-smtp",
])
def test_non_operator_cannot_manage_connections(login, member, path):
    client = login(member)
    assert client.get(path).status_code == 404
    assert client.post(path).status_code == 404


def test_csrf_is_required_for_connection_actions(superuser):
    client = Client(enforce_csrf_checks=True)
    client.force_login(superuser)
    assert client.post("/system/email-connections/new", _sendgrid()).status_code == 403
    profile = EmailApiConnection.objects.create(
        name="Existing", provider="sendgrid", from_email="sender@example.com",
        provider_config={"region": "global"}, credentials={"api_key": "secret"},
    )
    for path, data in (
        (f"/system/email-connections/{profile.pk}", _sendgrid("Changed")),
        (f"/system/email-connections/{profile.pk}/use", {}),
        (f"/system/email-connections/{profile.pk}/test", {"kind": "simple"}),
        (f"/system/email-connections/{profile.pk}/delete", {}),
        ("/system/email-connections/use-smtp", {}),
    ):
        assert client.post(path, data).status_code == 403
    assert EmailApiConnection.objects.count() == 1


def test_old_instance_settings_post_keeps_api_route(login, superuser):
    client = login(superuser)
    profile = _create(client)
    client.post(f"/system/email-connections/{profile.pk}/use")
    assert client.post("/system", {"instance_name": "Updated", "email_port": "587"}).status_code == 302
    assert InstanceConfig.load().active_email_api_connection_id == profile.pk


def test_preview_is_synthetic_and_does_not_send_or_change_selection(login, superuser):
    from apps.core.email_mapping import default_custom_config

    client = login(superuser)
    sample = default_custom_config()
    data = {
        "action": "preview", "kind": "report", "name": "Custom", "provider": "custom_https",
        "from_email": "sender@example.com", "endpoint": sample["endpoint"],
        "auth_type": sample["auth"]["type"],
        "payload": json.dumps(sample["payload"]), "accepted_statuses": "200,201,202",
        "snapshot_mode": sample["attachments"]["snapshot_mode"],
        "max_request_bytes": sample["max_request_bytes"],
    }
    with patch("apps.core.mail.send_test_email") as send, patch("apps.core.mail.send_report_test_email") as report:
        response = client.post("/system/email-connections/new", data)
    assert response.status_code == 200
    assert b"Preview" in response.content
    assert EmailApiConnection.objects.count() == 0
    assert InstanceConfig.load().active_email_api_connection_id is None
    send.assert_not_called()
    report.assert_not_called()


def test_editor_marks_extra_header_values_sensitive(superuser):
    from apps.core.views import email_connection_editor

    request = RequestFactory().post("/system/email-connections/new",
                                    {**_sendgrid(), "header_value_0": "must-be-hidden"})
    request.user = superuser
    with patch("apps.core.views._require_email_operator", return_value=None), \
         patch("apps.core.forms.EmailApiConnectionForm.build_profile", side_effect=RuntimeError("db fail")), \
         pytest.raises(RuntimeError):
        email_connection_editor(request)
    reporter_filter = SafeExceptionReporterFilter()
    reporter_filter.is_active = lambda *_: True
    assert "must-be-hidden" not in str(reporter_filter.get_post_parameters(request))


def _custom_data(auth_type="bearer"):
    from apps.core.email_mapping import default_custom_config

    config = default_custom_config()
    return {
        "name": "Custom", "provider": "custom_https", "from_email": "sender@example.com",
        "endpoint": config["endpoint"], "auth_type": auth_type,
        "auth_header_name": "X-Api-Key", "token": "bearer-secret", "api_key": "key-secret",
        "username": "basic-user", "password": "basic-secret",
        "payload": json.dumps(config["payload"]), "accepted_statuses": "200,201,202",
        "snapshot_mode": "attachment", "max_request_bytes": str(config["max_request_bytes"]),
    }


@pytest.mark.parametrize("auth_type, expected", [
    ("bearer", {"token": "bearer-secret"}),
    ("api_key_header", {"api_key": "key-secret"}),
    ("basic", {"username": "basic-user", "password": "basic-secret"}),
])
def test_custom_auth_modes_write_only(login, superuser, auth_type, expected):
    client = login(superuser)
    response = client.post("/system/email-connections/new", _custom_data(auth_type))
    assert response.status_code == 302, response.content.decode()[:500]
    profile = EmailApiConnection.objects.get(name="Custom")
    assert {key: profile.credentials[key] for key in expected} == expected
    assert not any(key in profile.credentials for key in {"token", "api_key", "username", "password"} - set(expected))
    page = client.get(f"/system/email-connections/{profile.pk}")
    for secret in ("bearer-secret", "key-secret", "basic-secret", "basic-user"):
        assert secret.encode() not in page.content


def test_custom_header_preserve_explicit_clear_and_active_contract_guard(login, superuser):
    client = login(superuser)
    data = _custom_data()
    data.update({"header_name_0": "X-Route", "header_value_0": "secret-header"})
    assert client.post("/system/email-connections/new", data).status_code == 302
    profile = EmailApiConnection.objects.get(name="Custom")
    assert profile.credentials["headers"] == {"X-Route": "secret-header"}
    data["header_value_0"] = ""
    data["token"] = ""
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 302
    profile.refresh_from_db()
    assert profile.credentials["headers"] == {"X-Route": "secret-header"}
    assert profile.credentials["token"] == "bearer-secret"
    client.post(f"/system/email-connections/{profile.pk}/use")
    data["endpoint"] = "https://other.example.com/send"
    data["token"] = "replacement"
    data["header_value_0"] = "replacement-header"
    response = client.post(f"/system/email-connections/{profile.pk}", data)
    assert response.status_code == 200
    assert b"inactive copy" in response.content
    profile.refresh_from_db()
    assert profile.custom_config["endpoint"] != data["endpoint"]
    copied = client.get(f"/system/email-connections/new?copy={profile.pk}")
    assert copied.status_code == 200
    assert b"secret-header" not in copied.content
    assert b"bearer-secret" not in copied.content
    data["endpoint"] = profile.custom_config["endpoint"]
    data["token"] = ""
    data["header_clear_0"] = "on"
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 200
    # Removing a header changes the active contract; the inactive copy must be selected first.


def test_custom_new_origin_requires_fresh_credentials(login, superuser):
    client = login(superuser)
    data = _custom_data()
    assert client.post("/system/email-connections/new", data).status_code == 302
    profile = EmailApiConnection.objects.get(name="Custom")
    data["token"] = ""
    data["endpoint"] = "https://api.example.com/another-path"
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 302
    profile.refresh_from_db()
    assert profile.credentials["token"] == "bearer-secret"
    data["endpoint"] = "https://different.example.com/send"
    assert client.post(f"/system/email-connections/{profile.pk}", data).status_code == 200
    profile.refresh_from_db()
    assert profile.custom_config["endpoint"] == "https://api.example.com/another-path"
