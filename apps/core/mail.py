"""Bind one sender and one delivery route for each mail operation."""
from __future__ import annotations

import copy
import binascii
import json
import struct
import zlib
from dataclasses import dataclass, field

from django.conf import settings
from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend
from django.views.decorators.debug import sensitive_variables

from apps.core.email_errors import EmailDeliveryError
from apps.core.email_providers import build_api_backend, mime_mode, screenshot_mode, validate_api_profile


@dataclass(frozen=True)
class DeliverySnapshot:
    provider: str
    profile_id: int | None
    config_revision: int | None
    from_email: str
    screenshot_mode: str
    mime_mode: bool
    _provider_config: str = "{}"
    _custom_config: str = "{}"
    _credentials: str = field(default="{}", repr=False)

    @property
    def provider_config(self):
        return json.loads(self._provider_config)

    @property
    def custom_config(self):
        return json.loads(self._custom_config)

    @property
    def credentials(self):
        return json.loads(self._credentials)

    @property
    def configuration(self):
        return self.custom_config if self.provider == "custom_https" else self.provider_config


@sensitive_variables()
def _profile_snapshot(profile):
    validate_api_profile(profile.provider, profile.provider_config, profile.custom_config, profile.credentials)
    return DeliverySnapshot(
        provider=profile.provider, profile_id=profile.pk,
        config_revision=profile.config_revision, from_email=profile.from_email,
        screenshot_mode=screenshot_mode(profile.provider, profile.custom_config),
        mime_mode=mime_mode(profile.provider),
        _provider_config=json.dumps(profile.provider_config),
        _custom_config=json.dumps(profile.custom_config),
        _credentials=json.dumps(profile.credentials),
    )


@sensitive_variables()
def prepare_api_message(email, snapshot):
    """Keep SES's raw MIME, or make an independent standard structured message."""
    if snapshot.mime_mode:
        prepared = copy.copy(email)
        prepared.to = list(email.to)
        prepared.cc = list(email.cc)
        prepared.bcc = list(email.bcc)
        prepared.reply_to = list(email.reply_to)
        prepared.extra_headers = copy.deepcopy(email.extra_headers)
        prepared.alternatives = copy.deepcopy(getattr(email, "alternatives", []))
        prepared.attachments = copy.deepcopy(email.attachments)
        if hasattr(email, "_inline_image"):
            prepared._inline_image = copy.deepcopy(email._inline_image)
        prepared.connection = None
        prepared.from_email = snapshot.from_email
        return prepared
    prepared = EmailMultiAlternatives(
        subject=email.subject, body=email.body, from_email=snapshot.from_email,
        to=list(email.to), cc=list(email.cc), bcc=list(email.bcc),
        reply_to=list(email.reply_to), headers=copy.deepcopy(email.extra_headers),
    )
    prepared.encoding = email.encoding
    prepared.content_subtype = email.content_subtype
    prepared.alternatives = copy.deepcopy(getattr(email, "alternatives", []))
    prepared.attachments = copy.deepcopy(email.attachments)
    inline = getattr(email, "_inline_image", None)
    if inline is not None:
        if snapshot.screenshot_mode == "inline":
            prepared.attach(copy.deepcopy(inline))
        else:
            prepared.attach("snapshot.png", inline.get_payload(decode=True), "image/png")
    return prepared


def _safe_api_error(exc):
    if isinstance(exc, EmailDeliveryError):
        return exc
    from anymail.exceptions import (
        AnymailAPIError, AnymailInvalidAddress, AnymailRecipientsRefused,
        AnymailSerializationError, AnymailUnsupportedFeature,
    )
    from django.core.exceptions import ValidationError
    from requests.exceptions import ConnectionError, Timeout

    if isinstance(exc, (ValidationError, AnymailInvalidAddress, AnymailSerializationError, AnymailUnsupportedFeature)):
        return EmailDeliveryError("invalid_message")
    if isinstance(exc, Timeout):
        return EmailDeliveryError("timeout", outcome="unconfirmed")
    if isinstance(exc, ConnectionError):
        return EmailDeliveryError("connection_error", outcome="unconfirmed")
    if isinstance(exc, (AnymailAPIError, AnymailRecipientsRefused)):
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = (response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                      if isinstance(response, dict) else getattr(response, "status_code", None))
        if status in {400, 401, 403, 404, 405, 413, 415, 422, 429}:
            return EmailDeliveryError("http_rejected", outcome="rejected", status=status)
        return EmailDeliveryError("http_unconfirmed", outcome="unconfirmed", status=status)
    return EmailDeliveryError("http_unconfirmed", outcome="unconfirmed")


class InstanceEmailBackend(BaseEmailBackend):
    """Django's configured backend; constructor captures the route and sender."""

    @sensitive_variables()
    def __init__(self, *args, api_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._delegate = None
        self._resolution_error = None
        try:
            self._bind(api_profile)
        except Exception:
            self._resolution_error = EmailDeliveryError("invalid_config")
            if not self.fail_silently:
                raise self._resolution_error from None

    @sensitive_variables()
    def _bind(self, api_profile):
        from apps.core.models import EmailApiConnection, InstanceConfig

        row = None
        if api_profile is not None:
            profile = EmailApiConnection.objects.get(pk=api_profile.pk)
            snapshot = _profile_snapshot(profile)
        else:
            InstanceConfig.load()
            row = InstanceConfig.objects.select_related("active_email_api_connection").get(pk=1)
            profile = row.active_email_api_connection
            if profile is not None:
                snapshot = _profile_snapshot(profile)
            else:
                provider = "smtp" if row.email_host or settings.EMAIL_URL_CONFIGURED else "console"
                snapshot = DeliverySnapshot(provider, None, None, row.email_from or settings.DEFAULT_FROM_EMAIL, "inline", True)
        self.snapshot = snapshot
        self.from_email = snapshot.from_email
        self.configured = snapshot.provider != "console"
        if profile is not None:
            self._delegate = build_api_backend(snapshot)
            self._console = None
        elif snapshot.provider == "console":
            self._delegate = ConsoleBackend(fail_silently=self.fail_silently)
            self._console = self._delegate
        else:
            params = {"fail_silently": self.fail_silently}
            if row.email_host:
                params.update(host=row.email_host, port=row.email_port or 587,
                              username=row.email_host_user, password=row.email_host_password,
                              use_tls=bool(row.email_use_tls), use_ssl=False)
            self._delegate = SmtpBackend(**params)
            self._console = None
            for key in ("host", "port", "username", "password", "use_tls", "use_ssl"):
                setattr(self, key, getattr(self._delegate, key))

    @sensitive_variables()
    def open(self):
        if self._resolution_error:
            if self.fail_silently:
                return False
            raise self._resolution_error
        try:
            return self._delegate.open()
        except Exception as exc:
            if self.snapshot.profile_id is None:
                if self.fail_silently:
                    return False
                raise
            if not self.fail_silently:
                raise _safe_api_error(exc) from None
            return False

    @sensitive_variables()
    def close(self):
        if self._delegate is not None:
            try:
                return self._delegate.close()
            except Exception as exc:
                if not self.fail_silently:
                    if self.snapshot.profile_id is not None:
                        raise _safe_api_error(exc) from None
                    raise

    @sensitive_variables()
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if self._resolution_error:
            if self.fail_silently:
                return 0
            raise self._resolution_error
        accepted = 0
        for email in email_messages:
            try:
                prepared = prepare_api_message(email, self.snapshot) if self.snapshot.profile_id else email
                sent = self._delegate.send_messages([prepared]) or 0
                if sent and self.snapshot.provider not in {"sendgrid", "custom_https", "smtp", "console"}:
                    statuses = getattr(getattr(prepared, "anymail_status", None), "recipients", None)
                    if not statuses or any(item.status not in {"sent", "queued"} for item in statuses.values()):
                        raise EmailDeliveryError("http_unconfirmed", outcome="unconfirmed")
                accepted += sent
            except Exception as exc:
                if self.snapshot.profile_id is None:
                    if self.fail_silently:
                        continue
                    raise
                if not self.fail_silently:
                    raise _safe_api_error(exc) from None
        return accepted


def resolve_delivery_connection(*, api_profile=None):
    """Resolve a fresh route, honoring an explicit Django test backend."""
    if api_profile is None and settings.EMAIL_BACKEND != "apps.core.mail.InstanceEmailBackend":
        from apps.core.models import InstanceConfig

        try:
            from_email = InstanceConfig.load().email_from or settings.DEFAULT_FROM_EMAIL
        except Exception:
            from_email = settings.DEFAULT_FROM_EMAIL
        backend = get_connection()
        backend.from_email = from_email
        backend.snapshot = DeliverySnapshot("legacy_override", None, None, from_email, "inline", True)
        backend.configured = True
        return backend
    return InstanceEmailBackend(api_profile=api_profile)


def default_from_email() -> str:
    return resolve_delivery_connection().from_email


def send_test_email(recipient: str, *, connection=None) -> int:
    from apps.core.instance import base_url, instance_name

    connection = connection or resolve_delivery_connection()
    count = EmailMessage(
        subject=f"{instance_name()} — test email",
        body=(f"This is a test message from {instance_name()} ({base_url()}).\n\n"
              "If you are reading it, outbound mail is configured correctly: "
              "invitations and password resets will be delivered."),
        from_email=connection.from_email, to=[recipient], connection=connection,
    ).send(fail_silently=False)
    if count != 1:
        raise EmailDeliveryError("http_unconfirmed", outcome="unconfirmed")
    return count


def _sample_report_files():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", binascii.crc32(name + data))

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\x0d\x94\x88\xff")) + chunk(b"IEND", b""))
    content = b"BT /F1 12 Tf 20 50 Td (Trellum report sample) Tj ET"
    objects = [
        b"<</Type /Catalog /Pages 2 0 R>>",
        b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Resources <</Font <</F1 5 0 R>>>> /Contents 4 0 R>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
        b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    startxref = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010} 00000 n \n".encode())
    pdf.extend(f"trailer\n<</Size {len(offsets)} /Root 1 0 R>>\nstartxref\n{startxref}\n%%EOF\n".encode())
    return png, bytes(pdf)


def send_report_test_email(recipient: str, *, connection) -> int:
    """Send a valid synthetic PNG/PDF through the selected report capability."""
    from email.mime.image import MIMEImage

    png, pdf = _sample_report_files()
    inline = MIMEImage(png, _subtype="png")
    inline.add_header("Content-ID", "<report-snapshot>")
    inline.add_header("Content-Disposition", "inline", filename="snapshot.png")
    from apps.reports.notify import _DeliveryEmail

    is_file = connection.snapshot.screenshot_mode == "attachment"
    message = _DeliveryEmail(
        subject="Trellum report sample", body="Synthetic report sample. Snapshot attached." if is_file else "Synthetic report sample.",
        from_email=connection.from_email, to=[recipient], connection=connection,
        inline_image=inline,
    )
    message.attach_alternative(
        "<p>Snapshot attached</p>" if is_file else '<img src="cid:report-snapshot" alt="Report snapshot">',
        "text/html",
    )
    message.attach("sample.pdf", pdf, "application/pdf")
    count = message.send(fail_silently=False)
    if count != 1:
        raise EmailDeliveryError("http_unconfirmed", outcome="unconfirmed")
    return count
