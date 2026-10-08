"""Preset contracts are explicit and cannot inherit ambient Anymail settings."""
from types import SimpleNamespace
import base64
from io import BytesIO
import json
from urllib.parse import unquote_plus
from email.mime.image import MIMEImage

import pytest
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from requests import Response
import requests

from apps.core.email_errors import EmailDeliveryError
from apps.core import email_providers
from apps.core.mail import prepare_api_message
from apps.reports.notify import _DeliveryEmail
from apps.core.email_providers import (
    PROVIDER_CHOICES, build_api_backend, build_ses_backend, validate_api_profile,
)


def _snapshot(provider, config, credentials):
    return SimpleNamespace(provider=provider, provider_config=config,
                           custom_config={}, credentials=credentials)


class _RawBytes(BytesIO):
    def read(self, size=-1, decode_content=False):
        assert decode_content is False
        return super().read(size)


@pytest.mark.parametrize("provider,options,credentials", [
    ("sendgrid", {"region": "global"}, {"api_key": "key"}),
    ("amazon_ses", {"region": "eu-west-1", "credential_source": "deployment"}, {}),
    ("mailgun", {"region": "eu", "sender_domain": "mail.example.com"}, {"api_key": "key"}),
    ("postmark", {"message_stream": "outbound"}, {"server_token": "key"}),
    ("brevo", {}, {"api_key": "key"}),
    ("resend", {}, {"api_key": "key"}),
    ("mailjet", {}, {"api_key": "key", "secret_key": "secret"}),
    ("mailersend", {}, {"api_token": "key"}),
    ("mailtrap", {}, {"api_token": "key"}),
])
def test_catalog_validation_and_backend_options(provider, options, credentials, settings):
    assert provider in dict(PROVIDER_CHOICES)
    validate_api_profile(provider, options, {}, credentials)
    settings.ANYMAIL = {
        "SEND_DEFAULTS": {"esp_extra": {"unwanted": "from environment"}},
        "MAILTRAP_SANDBOX_ID": "999", "POSTMARK_USE_BULK_API": True,
    }
    backend = build_api_backend(_snapshot(provider, options, credentials))
    if provider not in {"sendgrid", "amazon_ses"}:
        assert backend.send_defaults == {}
        assert backend.ignore_unsupported_features is False
        assert backend.ignore_recipient_status is False
        assert backend.debug_api_requests is False
        assert backend.api_url.startswith("https://")
        assert backend.timeout == (5, 20)
    if provider == "mailtrap":
        assert backend.use_sandbox is False
    if provider == "postmark":
        assert backend.use_bulk_api is False and backend.message_stream == "outbound"
    if provider == "mailgun":
        assert backend.api_url == "https://api.eu.mailgun.net/v3/"


@pytest.mark.django_db
@pytest.mark.parametrize("provider,options,credentials", [
    ("sendgrid", {"region": "global"}, {"api_key": "key"}),
    ("amazon_ses", {"region": "eu-west-1", "credential_source": "deployment"}, {}),
    ("mailgun", {"region": "us", "sender_domain": "mail.example.com"}, {"api_key": "key"}),
    ("postmark", {}, {"server_token": "key"}),
    ("brevo", {}, {"api_key": "key"}),
    ("resend", {}, {"api_key": "key"}),
    ("mailjet", {}, {"api_key": "key", "secret_key": "secret"}),
    ("mailersend", {}, {"api_token": "key"}),
    ("mailtrap", {}, {"api_token": "key"}),
])
def test_every_preset_passes_model_validation(provider, options, credentials):
    from apps.core.models import EmailApiConnection

    profile = EmailApiConnection(name=provider, provider=provider,
                                 provider_config=options, credentials=credentials,
                                 from_email="verified@example.test")
    profile.full_clean()


@pytest.mark.parametrize("provider,options,credentials", [
    ([], {}, {}),
    ("sendgrid", {"region": []}, {"api_key": "key"}),
    ("sendgrid", {}, {"api_key": {"bad": "shape"}}),
    ("amazon_ses", {"region": [], "credential_source": "deployment"}, {}),
    ("amazon_ses", {"region": "eu-west-1", "credential_source": []}, {}),
    ("amazon_ses", {"region": "eu-west-1", "credential_source": "access_keys"}, {"aws_access_key_id": "only-one"}),
    ("mailgun", {"region": [], "sender_domain": "mail.example.com"}, {"api_key": "key"}),
    ("postmark", {"message_stream": []}, {"server_token": "key"}),
    ("brevo", {"unknown": "value"}, {"api_key": "key"}),
    ("mailjet", {}, {"api_key": "key"}),
])
def test_malformed_preset_shapes_raise_safe_validation(provider, options, credentials):
    with pytest.raises(ValidationError):
        validate_api_profile(provider, options, {}, credentials)


def test_ses_backend_is_region_bound_and_lazily_built(monkeypatch):
    calls = []

    class FakeSession:
        def __init__(self, **kwargs):
            calls.append(("session", kwargs))

        def client(self, service, **kwargs):
            calls.append(("client", service, kwargs))
            return SimpleNamespace(close=lambda: calls.append(("close",)))

    monkeypatch.setattr("boto3.session.Session", FakeSession)
    snapshot = _snapshot("amazon_ses", {"region": "eu-west-1", "credential_source": "access_keys"},
                         {"aws_access_key_id": "id", "aws_secret_access_key": "secret", "aws_session_token": "token"})
    backend = build_ses_backend(snapshot)
    assert calls == []
    assert backend.open() is True
    assert calls[0] == ("session", snapshot.credentials)
    assert calls[1][1] == "sesv2"
    assert calls[1][2]["region_name"] == "eu-west-1"
    config = calls[1][2]["config"]
    assert config.connect_timeout == 5 and config.read_timeout == 20
    assert config.retries["total_max_attempts"] == 1
    backend.close()
    assert calls[-1] == ("close",)


_REQUEST_FIXTURES = [
    ("mailgun", {"region": "eu", "sender_domain": "mail.example.com"}, {"api_key": "key"},
     200, {"id": "id", "message": "Queued. Thank you."}, {}),
    ("postmark", {"message_stream": "outbound"}, {"server_token": "key"},
     200, {"ErrorCode": 0, "Message": "OK", "MessageID": "id", "To": "to@example.test"}, {}),
    ("brevo", {}, {"api_key": "key"}, 201, {"messageId": "id"}, {}),
    ("resend", {}, {"api_key": "key"}, 200, {"id": "id"}, {}),
    ("mailjet", {}, {"api_key": "key", "secret_key": "secret"}, 200,
     {"Messages": [{"Status": "success", "To": [{"Email": "to@example.test", "MessageID": 1}],
                    "Cc": [{"Email": "cc@example.test", "MessageID": 1}],
                    "Bcc": [{"Email": "hidden@example.test", "MessageID": 1}]}]}, {}),
    ("mailersend", {}, {"api_token": "key"}, 202, {},
     {"Content-Type": "text/html", "X-Message-Id": "id"}),
    ("mailtrap", {}, {"api_token": "key"}, 200,
     {"success": True, "message_ids": ["a", "b", "c"]}, {}),
]


@pytest.mark.parametrize("provider,options,credentials,status,body,response_headers", _REQUEST_FIXTURES)
@pytest.mark.parametrize("kind", ["simple", "report"])
def test_presets_send_real_adapter_requests_without_losing_message_parts(
        monkeypatch, provider, options, credentials, status, body, response_headers, kind):
    calls = []

    def fake_send(self, prepared, **kwargs):
        calls.append((prepared, kwargs))
        response = Response()
        response.status_code = status
        response.raw = _RawBytes(json.dumps(body).encode())
        response.headers.update(response_headers)
        response.url = prepared.url
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)
    backend = build_api_backend(_snapshot(provider, options, credentials))
    if kind == "simple":
        original = EmailMultiAlternatives(
            "Résumé — 📈", "Line one\nLine two", "other@example.test",
            ["to@example.test"], cc=["cc@example.test"],
            bcc=["hidden@example.test"], reply_to=["reply@example.test"],
        )
        original.attach_alternative("<p>Résumé</p>", "text/html")
    else:
        image = MIMEImage(b"exact PNG bytes", _subtype="png")
        image.add_header("Content-ID", "<report-snapshot>")
        image.add_header("Content-Disposition", "inline", filename="snapshot.png")
        original = _DeliveryEmail(
            "Résumé — 📈", "Line one\nLine two", "other@example.test",
            ["to@example.test"], cc=["cc@example.test"],
            bcc=["hidden@example.test"], reply_to=["reply@example.test"],
            inline_image=image,
        )
        original.attach_alternative(
            "<p>Snapshot attached</p>" if provider == "brevo" else '<img src="cid:report-snapshot">',
            "text/html",
        )
        original.attach("report.pdf", b"exact PDF bytes", "application/pdf")
    snapshot = SimpleNamespace(from_email="verified@example.test", mime_mode=False,
                               screenshot_mode="attachment" if provider == "brevo" else "inline")
    prepared = prepare_api_message(original, snapshot)
    assert prepared.from_email == "verified@example.test"
    assert backend.send_messages([prepared]) == 1
    assert len(calls) == 1
    request, send_options = calls[0]
    assert request.method == "POST" and request.url.startswith("https://")
    assert send_options["allow_redirects"] is False
    assert send_options["timeout"] == (5, 20)
    assert send_options["stream"] is True and send_options["proxies"] == {}
    assert request.headers["Content-Length"] == str(len(request.body))
    wire = request.body.decode("utf-8", "replace") if isinstance(request.body, bytes) else request.body
    wire = unquote_plus(wire)
    assert "Résumé" in wire or "R\\u00e9sum\\u00e9" in wire or "R%C3%A9sum%C3%A9" in wire
    assert "verified@example.test" in wire
    assert "to@example.test" in wire and "cc@example.test" in wire and "hidden@example.test" in wire
    if kind == "report":
        assert base64.b64encode(b"exact PNG bytes").decode() in wire or "exact PNG bytes" in wire
        assert base64.b64encode(b"exact PDF bytes").decode() in wire or "exact PDF bytes" in wire
        if provider == "brevo":
            assert "cid:report-snapshot" not in wire


@pytest.mark.parametrize("provider,options,credentials", [
    ("mailgun", {"region": "us", "sender_domain": "mail.example.com"}, {"api_key": "key"}),
    ("postmark", {}, {"server_token": "key"}),
    ("mailersend", {}, {"api_token": "key"}),
])
def test_preset_encoded_request_over_limit_never_reaches_network(
        monkeypatch, provider, options, credentials):
    monkeypatch.setattr(email_providers, "PRESET_REQUEST_LIMIT", 1024)
    calls = []
    monkeypatch.setattr(requests.Session, "send", lambda *args, **kwargs: calls.append(args))
    backend = build_api_backend(_snapshot(provider, options, credentials))
    message = EmailMultiAlternatives("oversize", "body", "from@example.test", ["to@example.test"])
    message.attach("report.pdf", b"private-pdf-bytes" * 100, "application/pdf")

    with pytest.raises(EmailDeliveryError) as caught:
        backend.send_messages([message])
    assert (caught.value.category, caught.value.outcome) == ("request_too_large", "not_submitted")
    assert calls == []
    assert "private-pdf-bytes" not in str(caught.value)


@pytest.mark.parametrize("size,category", [
    (64 * 1024, None),
    (64 * 1024 + 1, "response_too_large"),
])
def test_preset_response_is_bounded_without_truncating(monkeypatch, size, category):
    # Mailgun's response parser tolerates a trailing JSON whitespace body.
    prefix = b'{"id":"id","message":"Queued. Thank you."}'
    response_bytes = prefix + b" " * (size - len(prefix))
    calls = []

    def fake_send(self, prepared, **kwargs):
        response = Response()
        response.status_code = 200
        response.raw = _RawBytes(response_bytes)
        response.url = prepared.url
        calls.append(response)
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)
    backend = build_api_backend(_snapshot("mailgun", {"region": "us", "sender_domain": "mail.example.com"},
                                          {"api_key": "key"}))
    message = EmailMultiAlternatives("subject", "body", "from@example.test", ["to@example.test"])
    if category is None:
        assert backend.send_messages([message]) == 1
    else:
        with pytest.raises(EmailDeliveryError) as caught:
            backend.send_messages([message])
        assert (caught.value.category, caught.value.outcome, caught.value.status) == (
            "response_too_large", "unconfirmed", 200)
    assert len(calls) == 1 and calls[0].raw.closed
