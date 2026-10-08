import base64
import copy
from email.mime.image import MIMEImage

import pytest
from django.core.mail import EmailMultiAlternatives

from apps.core.email_custom import EmailBackend, _headers, build_message_context
from apps.core.email_errors import EmailDeliveryError
from apps.core.email_http import HttpResponse
from apps.core.email_mapping import default_custom_config, render_payload, synthetic_message_context


def _message():
    email = EmailMultiAlternatives("É report", "First\nsecond", "Sender <sender@example.com>",
                                  ["One <one@example.net>"], cc=["cc@example.net"],
                                  bcc=["blind@example.net"], reply_to=["reply@example.com"])
    email.attach_alternative('<p title="x">É report</p>', "text/html")
    image = MIMEImage(b"\x89PNG\r\n\x1a\nbytes", _subtype="png")
    image.add_header("Content-Disposition", "inline", filename="snapshot.png")
    image.add_header("Content-ID", "<report-snapshot>")
    email.attach(image)
    email.attach("report.pdf", b"%PDF-1.4\nbytes", "application/pdf")
    return email


def test_message_context_preserves_recipients_png_pdf_and_cid():
    context = build_message_context(_message())["message"]
    assert context["to"][0]["address"] == "one@example.net"
    assert context["bcc"][0]["address"] == "blind@example.net"
    assert context["reply_to"][0]["address"] == "reply@example.com"
    assert context["html"] == '<p title="x">É report</p>'
    assert base64.b64decode(context["attachments"][0]["content_base64"]) == b"\x89PNG\r\n\x1a\nbytes"
    assert context["attachments"][0]["content_id"] == "report-snapshot"
    assert base64.b64decode(context["attachments"][1]["content_base64"]) == b"%PDF-1.4\nbytes"


def test_unsupported_headers_or_html_alternative_fail_before_send():
    email = _message()
    email.extra_headers = {"X-Tracking": "1"}
    with pytest.raises(EmailDeliveryError):
        build_message_context(email)


def test_oversized_attachment_rejected_before_base64_projection():
    email = EmailMultiAlternatives("subject", "body", "sender@example.com", ["to@example.net"])
    email.attach("large.pdf", b"x" * (25 * 1024 * 1024 + 1), "application/pdf")
    with pytest.raises(EmailDeliveryError) as error:
        build_message_context(email)
    assert error.value.category == "request_too_large"
    email.extra_headers = {}
    email.attach_alternative("hi", "text/calendar")
    with pytest.raises(EmailDeliveryError):
        build_message_context(email)


@pytest.mark.parametrize("auth,credentials,header,value", [
    ({"type": "bearer"}, {"token": "abc"}, "Authorization", "Bearer abc"),
    ({"type": "api_key_header", "header_name": "X-Key"}, {"api_key": "abc"}, "X-Key", "abc"),
    ({"type": "basic"}, {"username": "name", "password": "pass"}, "Authorization", "Basic " + base64.b64encode(b"name:pass").decode()),
])
def test_auth_modes(auth, credentials, header, value):
    config = default_custom_config()
    config["auth"] = auth
    config["header_names"] = ["X-Extra"]
    credentials["headers"] = {"X-Extra": "encrypted value"}
    headers = _headers(config, credentials)
    assert headers[header] == value and headers["X-Extra"] == "encrypted value"


@pytest.mark.parametrize("credentials", [
    {"token": "abc\r\nInjected"}, {"token": "abc", "headers": {"X-Extra": "x\nInjection"}},
    {"token": "abc", "headers": {"Wrong": "x"}},
])
def test_bad_credential_values_rejected(credentials):
    config = default_custom_config()
    config["header_names"] = ["X-Extra"]
    with pytest.raises(EmailDeliveryError):
        _headers(config, credentials)


def test_backend_sends_once_and_requires_inline_mapping(monkeypatch):
    config = default_custom_config()
    config["attachments"]["snapshot_mode"] = "inline"
    config["payload"]["attachments"]["$template"]["disposition"] = {"$value": "item.disposition"}
    config["payload"]["attachments"]["$template"]["cid"] = {"$value": "item.content_id"}
    calls = []

    def fake_post(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return HttpResponse(202, b"", 2, "api.example.com")

    monkeypatch.setattr("apps.core.email_custom.post_json", fake_post)
    backend = EmailBackend(config=config, credentials={"token": "secret"})
    assert backend.send_messages([_message()]) == 1
    assert len(calls) == 1
    payload = calls[0][1]["payload"]
    assert payload["bcc"] == [{"email": "blind@example.net"}]
    assert payload["attachments"][0]["cid"] == "report-snapshot"
    assert base64.b64decode(payload["attachments"][1]["content"]) == b"%PDF-1.4\nbytes"

    bad = copy.deepcopy(config)
    del bad["payload"]["attachments"]["$template"]["cid"]
    with pytest.raises(EmailDeliveryError) as error:
        EmailBackend(config=bad, credentials={"token": "secret"}).send_messages([_message()])
    assert error.value.outcome == "not_submitted" and len(calls) == 1


def test_fail_silently_does_not_claim_acceptance(monkeypatch):
    attempts = []

    def fake_post(*args, **kwargs):
        attempts.append((args, kwargs))
        return HttpResponse(500, b"provider secret text", 1, "api.example.com")

    monkeypatch.setattr("apps.core.email_custom.post_json", fake_post)
    config = default_custom_config()
    backend = EmailBackend(config=config, credentials={"token": "secret"}, fail_silently=True)
    email = EmailMultiAlternatives("subject", "body", "sender@example.com", ["to@example.net"])
    assert backend.send_messages([email]) == 0
    assert len(attempts) == 1
    assert attempts[0][1]["headers"]["Authorization"] == "Bearer secret"
    with pytest.raises(EmailDeliveryError) as error:
        EmailBackend(config=config, credentials={"token": "secret"}).send_messages([email])
    assert error.value.outcome == "unconfirmed"
    assert "provider secret text" not in str(error.value)
    assert len(attempts) == 2


def test_sensitive_frames_cover_credentials_message_and_payload():
    config = default_custom_config()
    _headers(config, {"token": "secret"})
    build_message_context(EmailMultiAlternatives("subject", "body", "sender@example.com", ["to@example.net"]))
    render_payload(config["payload"], synthetic_message_context())
    EmailBackend(config=config, credentials={"token": "secret"}).send_messages([])
    assert _headers.sensitive_variables == "__ALL__"
    assert build_message_context.sensitive_variables == "__ALL__"
    assert render_payload.sensitive_variables == "__ALL__"
    assert EmailBackend.__init__.sensitive_variables == "__ALL__"
    assert EmailBackend.send_messages.sensitive_variables == "__ALL__"
