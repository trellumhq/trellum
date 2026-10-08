"""Outgoing SendGrid request fixtures, including recipient isolation and files."""
import base64
from email.mime.image import MIMEImage

import pytest
from django.core.mail import EmailMultiAlternatives

from apps.core import email_sendgrid
from apps.core.email_errors import EmailDeliveryError
from apps.core.email_http import HttpResponse


def _message():
    message = EmailMultiAlternatives(
        subject="Résumé — 📈", body="Line one\nLine two",
        from_email="Sender <sender@example.test>",
        to=["Alice <alice@example.test>"], cc=["cc@example.test"],
        bcc=["hidden@example.test"], reply_to=["reply@example.test"],
    )
    message.attach_alternative("<p>Résumé</p><img src='cid:report-snapshot'>", "text/html")
    image = MIMEImage(b"PNG bytes", _subtype="png")
    image.add_header("Content-ID", "<report-snapshot>")
    image.add_header("Content-Disposition", "inline", filename="snapshot.png")
    message.attach(image)
    message.attach("report.pdf", b"PDF bytes", "application/pdf")
    return message


@pytest.mark.parametrize("region,host", [("global", "api.sendgrid.com"), ("eu", "api.eu.sendgrid.com")])
def test_sendgrid_payload_preserves_recipients_and_binary(monkeypatch, region, host):
    calls = []

    def post(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return HttpResponse(202, b"", 1, host)

    monkeypatch.setattr(email_sendgrid, "post_json", post)
    backend = email_sendgrid.EmailBackend(region=region, api_key="private-key")
    assert backend.send_messages([_message()]) == 1
    assert len(calls) == 1
    endpoint, call = calls[0]
    assert endpoint == f"https://{host}/v3/mail/send"
    assert call["headers"] == {"Authorization": "Bearer private-key"}
    data = call["payload"]
    assert data["from"] == {"email": "sender@example.test", "name": "Sender"}
    assert data["personalizations"] == [{
        "to": [{"email": "alice@example.test", "name": "Alice"}],
        "cc": [{"email": "cc@example.test"}],
        "bcc": [{"email": "hidden@example.test"}],
    }]
    assert data["reply_to"] == {"email": "reply@example.test"}
    assert data["subject"] == "Résumé — 📈"
    assert data["content"][0]["value"] == "Line one\nLine two"
    assert data["attachments"] == [
        {"filename": "snapshot.png", "type": "image/png", "content": base64.b64encode(b"PNG bytes").decode(),
         "disposition": "inline", "content_id": "report-snapshot"},
        {"filename": "report.pdf", "type": "application/pdf", "content": base64.b64encode(b"PDF bytes").decode(),
         "disposition": "attachment"},
    ]


@pytest.mark.parametrize("status,outcome", [(401, "rejected"), (429, "rejected"), (500, "unconfirmed"), (302, "unconfirmed")])
def test_sendgrid_failure_never_counts_or_retries(monkeypatch, status, outcome):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return HttpResponse(status, b"", 1, "api.sendgrid.com")

    monkeypatch.setattr(email_sendgrid, "post_json", post)
    backend = email_sendgrid.EmailBackend(region="global", api_key="private-key")
    with pytest.raises(EmailDeliveryError) as error:
        backend.send_messages([_message()])
    assert error.value.outcome == outcome and len(calls) == 1
    assert email_sendgrid.EmailBackend(region="global", api_key="private-key", fail_silently=True).send_messages([_message()]) == 0
