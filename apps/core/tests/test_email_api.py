"""Bound route selection, sender, counts and encrypted profile storage."""
import pytest
from django.core.mail import EmailMessage
from django.db import connection

from apps.core import mail
from apps.core.email_errors import EmailDeliveryError
from apps.core.models import EmailApiConnection, InstanceConfig


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def use_router(settings):
    settings.EMAIL_BACKEND = "apps.core.mail.InstanceEmailBackend"


def _profile(name="one", key="secret"):
    return EmailApiConnection.objects.create(
        name=name, provider="sendgrid", provider_config={"region": "global"},
        credentials={"api_key": key}, from_email="api@example.test",
    )


class FakeBackend:
    def __init__(self, accepted=1, error=None):
        self.accepted = accepted
        self.error = error
        self.messages = []

    def send_messages(self, messages):
        self.messages.extend(messages)
        if self.error:
            raise self.error
        return self.accepted

    def open(self):
        return True

    def close(self):
        return None


def test_profile_credentials_are_encrypted_and_selected_over_smtp(monkeypatch, settings):
    settings.EMAIL_URL_CONFIGURED = True
    profile = _profile()
    row = InstanceConfig.load()
    row.email_host = "smtp.example.test"
    row.active_email_api_connection = profile
    row.save()
    with connection.cursor() as cursor:
        cursor.execute("SELECT credentials FROM core_emailapiconnection WHERE id = %s", [profile.pk])
        stored = cursor.fetchone()[0]
    assert stored.startswith("enc$1$") and "secret" not in stored
    fake = FakeBackend()
    monkeypatch.setattr(mail, "build_api_backend", lambda snapshot: fake)
    backend = mail.resolve_delivery_connection()
    assert backend.configured and backend.snapshot.provider == "sendgrid"
    assert backend.snapshot.credentials == {"api_key": "secret"}
    message = EmailMessage("Subject", "Body", "wrong@example.test", ["to@example.test"])
    assert backend.send_messages([message]) == 1
    assert fake.messages[0].from_email == "api@example.test"
    assert message.from_email == "wrong@example.test"


def test_two_same_provider_profiles_do_not_share_credentials(monkeypatch):
    a = _profile("one", "first")
    b = _profile("two", "second")
    snapshots = []
    monkeypatch.setattr(mail, "build_api_backend", lambda snapshot: snapshots.append(snapshot) or FakeBackend())
    first = mail.resolve_delivery_connection(api_profile=a)
    second = mail.resolve_delivery_connection(api_profile=b)
    assert first.snapshot.credentials == {"api_key": "first"}
    assert second.snapshot.credentials == {"api_key": "second"}
    assert [item.profile_id for item in snapshots] == [a.pk, b.pk]
    changed = first.snapshot.credentials
    changed["api_key"] = "mutated"
    assert first.snapshot.credentials["api_key"] == "first"


def test_unselected_smtp_and_console_follow_legacy_chain(settings):
    settings.EMAIL_URL_CONFIGURED = False
    assert mail.resolve_delivery_connection().snapshot.provider == "console"
    row = InstanceConfig.load()
    row.email_host = "smtp.example.test"
    row.email_host_user = "user"
    row.email_host_password = "password"
    row.save()
    backend = mail.resolve_delivery_connection()
    assert backend.snapshot.provider == "smtp"
    assert backend.host == "smtp.example.test" and backend.password == "password"


def test_database_failure_never_assumes_legacy_route(monkeypatch):
    monkeypatch.setattr(InstanceConfig, "load", classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("private db message"))))
    with pytest.raises(EmailDeliveryError) as error:
        mail.resolve_delivery_connection()
    assert "private db message" not in str(error.value)
    assert mail.InstanceEmailBackend(fail_silently=True).send_messages([EmailMessage("a", "b", to=["a@example.test"])]) == 0


def test_api_failure_is_sanitized_and_silent_count_is_zero(monkeypatch):
    profile = _profile()
    monkeypatch.setattr(mail, "build_api_backend", lambda snapshot: FakeBackend(error=RuntimeError("secret and recipient")))
    message = EmailMessage("Subject", "Body", to=["to@example.test"])
    with pytest.raises(EmailDeliveryError) as error:
        mail.resolve_delivery_connection(api_profile=profile).send_messages([message])
    assert "secret" not in str(error.value) and "recipient" not in str(error.value)
    assert mail.InstanceEmailBackend(api_profile=profile, fail_silently=True).send_messages([message]) == 0


def test_test_helper_requires_one_accepted_message(monkeypatch):
    fake = FakeBackend(accepted=0)
    monkeypatch.setattr(mail, "build_api_backend", lambda snapshot: fake)
    backend = mail.resolve_delivery_connection(api_profile=_profile())
    with pytest.raises(EmailDeliveryError):
        mail.send_test_email("operator@example.test", connection=backend)
    assert fake.messages[0].to == ["operator@example.test"]


def test_deployment_ses_and_custom_empty_headers_pass_model_validation():
    from apps.core.email_mapping import default_custom_config

    ses = EmailApiConnection(
        name="ses-role", provider="amazon_ses", from_email="sender@example.test",
        provider_config={"region": "eu-west-1", "credential_source": "deployment"},
        credentials={},
    )
    ses.full_clean()
    custom = EmailApiConnection(
        name="custom", provider="custom_https", from_email="sender@example.test",
        custom_config=default_custom_config(),
        credentials={"token": "private", "headers": {}},
    )
    custom.full_clean()


def test_report_test_files_are_parseable_and_preserved():
    from io import BytesIO
    from PIL import Image

    png, pdf = mail._sample_report_files()
    with Image.open(BytesIO(png)) as image:
        image.verify()
    assert pdf.startswith(b"%PDF-1.4\n")
    assert b"/Type /Page " in pdf and b"/Root 1 0 R" in pdf
    xref_offset = int(pdf.split(b"startxref\n", 1)[1].splitlines()[0])
    assert pdf[xref_offset:xref_offset + 4] == b"xref"


@pytest.mark.parametrize("statuses", [["error"], ["success", "error"]])
def test_mailjet_partial_or_total_recipient_failure_is_not_accepted(monkeypatch, statuses):
    import json
    import requests
    from requests import Response

    profile = EmailApiConnection.objects.create(
        name="mailjet", provider="mailjet", from_email="sender@example.test",
        credentials={"api_key": "key", "secret_key": "secret"},
    )
    calls = []

    def send(self, prepared, **kwargs):
        calls.append(prepared.url)
        response = Response()
        response.status_code = 200
        response._content = json.dumps({"Messages": [
            {"Status": status, "To": [{"Email": address, "MessageID": index + 1}]}
            for index, (status, address) in enumerate(zip(statuses, ["to@example.test", "cc@example.test"]))
        ]}).encode()
        response.url = prepared.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    message = EmailMessage("subject", "body", "ignored@example.test", ["to@example.test"],
                           cc=["cc@example.test"] if len(statuses) > 1 else [])
    with pytest.raises(EmailDeliveryError) as error:
        mail.resolve_delivery_connection(api_profile=profile).send_messages([message])
    assert error.value.outcome == "unconfirmed" and len(calls) == 1


def test_unknown_anymail_recipient_status_is_unconfirmed(monkeypatch):
    from types import SimpleNamespace

    profile = EmailApiConnection.objects.create(
        name="postmark", provider="postmark", from_email="sender@example.test",
        credentials={"server_token": "key"},
    )

    class UnknownBackend(FakeBackend):
        def send_messages(self, messages):
            messages[0].anymail_status = SimpleNamespace(
                recipients={"to@example.test": SimpleNamespace(status="unknown")},
            )
            return 1

    monkeypatch.setattr(mail, "build_api_backend", lambda snapshot: UnknownBackend())
    with pytest.raises(EmailDeliveryError) as error:
        mail.resolve_delivery_connection(api_profile=profile).send_messages(
            [EmailMessage("subject", "body", to=["to@example.test"])],
        )
    assert error.value.outcome == "unconfirmed"
