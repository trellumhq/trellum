"""Amazon SES v2 signs a region-bound raw MIME message without losing report files."""
from email import policy
from email.mime.image import MIMEImage
from email.parser import BytesParser

import pytest

from apps.core.models import EmailApiConnection, InstanceConfig
from apps.core.email_errors import EmailDeliveryError
from apps.core import email_providers
from apps.reports.notify import _DeliveryEmail


pytestmark = pytest.mark.django_db


def test_ses_raw_mime_has_one_png_one_pdf_and_private_bcc(monkeypatch, settings):
    settings.EMAIL_BACKEND = "apps.core.mail.InstanceEmailBackend"
    calls = {}

    class Client:
        def send_email(self, **kwargs):
            calls["send"] = kwargs
            return {"MessageId": "abc-123"}

        def close(self):
            calls["closed"] = True

    class Session:
        def __init__(self, **kwargs):
            calls["session"] = kwargs

        def client(self, service, **kwargs):
            calls["service"] = service
            calls["client"] = kwargs
            return Client()

    monkeypatch.setattr("boto3.session.Session", Session)
    profile = EmailApiConnection.objects.create(
        name="ses-eu", provider="amazon_ses", from_email="verified@example.test",
        provider_config={"region": "eu-west-1", "credential_source": "access_keys"},
        credentials={"aws_access_key_id": "id", "aws_secret_access_key": "secret"},
    )
    row = InstanceConfig.load()
    row.active_email_api_connection = profile
    row.save()
    image = MIMEImage(b"exact PNG bytes", _subtype="png")
    image.add_header("Content-ID", "<report-snapshot>")
    image.add_header("Content-Disposition", "inline", filename="snapshot.png")
    message = _DeliveryEmail(
        subject="Rapport — 📈", body="Text report", from_email="old@example.test",
        to=["to@example.test"], bcc=["hidden@example.test"], inline_image=image,
    )
    message.attach_alternative('<img src="cid:report-snapshot">', "text/html")
    message.attach("report.pdf", b"exact PDF bytes", "application/pdf")
    assert message.send() == 1
    assert calls["session"] == {"aws_access_key_id": "id", "aws_secret_access_key": "secret"}
    assert calls["service"] == "sesv2"
    assert calls["client"]["region_name"] == "eu-west-1"
    assert calls["client"]["config"].retries["total_max_attempts"] == 1
    assert calls["closed"] is True
    params = calls["send"]
    assert params["Destination"]["BccAddresses"] == ["hidden@example.test"]
    raw = params["Content"]["Raw"]["Data"]
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert mime["From"] == "verified@example.test" and mime["Bcc"] is None
    parts = list(mime.walk())
    images = [part for part in parts if part.get_content_type() == "image/png"]
    pdfs = [part for part in parts if part.get_content_type() == "application/pdf"]
    assert len(images) == len(pdfs) == 1
    assert images[0]["Content-ID"] == "<report-snapshot>"
    assert images[0].get_payload(decode=True) == b"exact PNG bytes"
    assert pdfs[0].get_payload(decode=True) == b"exact PDF bytes"
    assert any('cid:report-snapshot' in part.get_content() for part in parts if part.get_content_type() == "text/html")


def test_ses_checks_final_raw_mime_before_sdk_send(monkeypatch, settings):
    settings.EMAIL_BACKEND = "apps.core.mail.InstanceEmailBackend"
    monkeypatch.setattr(email_providers, "SES_RAW_MIME_LIMIT", 1024)
    sent = []

    class Client:
        def send_email(self, **kwargs):
            sent.append(kwargs)
            return {"MessageId": "should-not-send"}

        def close(self):
            pass

    class Session:
        def __init__(self, **kwargs):
            pass

        def client(self, service, **kwargs):
            return Client()

    monkeypatch.setattr("boto3.session.Session", Session)
    profile = EmailApiConnection.objects.create(
        name="ses-limited", provider="amazon_ses", from_email="verified@example.test",
        provider_config={"region": "eu-west-1", "credential_source": "access_keys"},
        credentials={"aws_access_key_id": "id", "aws_secret_access_key": "secret"},
    )
    row = InstanceConfig.load()
    row.active_email_api_connection = profile
    row.save()
    message = _DeliveryEmail("Large", "body", "old@example.test", ["to@example.test"])
    message.attach("report.pdf", b"secret-pdf" * 300, "application/pdf")
    with pytest.raises(EmailDeliveryError) as caught:
        message.send()
    assert (caught.value.category, caught.value.outcome) == ("request_too_large", "not_submitted")
    assert sent == []
    assert "secret-pdf" not in str(caught.value)
