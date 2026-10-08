"""Allowlisted outbound email providers and per-operation backend construction."""
from __future__ import annotations

import importlib
import re

import requests

from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

from apps.core.email_errors import EmailDeliveryError


# These caps apply to the fully encoded request, including multipart boundaries and
# base64 attachment expansion. They are deliberately below some ESP maximums.
PRESET_REQUEST_LIMIT = 10_000_000
PRESET_RESPONSE_LIMIT = 64 * 1024
SES_RAW_MIME_LIMIT = 28 * 1024 * 1024


class _SizeLimitedSESClient:
    """Check the exact raw MIME passed to the region-bound boto3 client."""

    def __init__(self, client):
        self._client = client

    @sensitive_variables()
    def send_email(self, **params):
        raw = params.get("Content", {}).get("Raw", {}).get("Data")
        if not isinstance(raw, bytes) or len(raw) > SES_RAW_MIME_LIMIT:
            raise EmailDeliveryError("request_too_large" if isinstance(raw, bytes) else "invalid_message")
        return self._client.send_email(**params)

    def send_bulk_email(self, **params):
        # The configured route supports raw MIME only; a template send must not
        # bypass the raw-message size and attachment checks.
        raise EmailDeliveryError("invalid_message")

    def __getattr__(self, name):
        return getattr(self._client, name)


@sensitive_variables()
def _bounded_preset_request(session, method, url, **kwargs):
    """Prepare once with Requests, then send only those measured bytes."""
    timeout = kwargs.pop("timeout", (5, 20))
    kwargs.pop("allow_redirects", None)
    kwargs.pop("stream", None)
    allowed = {"params", "data", "headers", "files", "auth", "cookies", "hooks", "json"}
    if set(kwargs) - allowed:
        raise EmailDeliveryError("invalid_config")
    headers = dict(kwargs.pop("headers") or {})
    headers["Accept-Encoding"] = "identity"
    prepared = session.prepare_request(requests.Request(method, url, headers=headers, **kwargs))
    body = prepared.body
    if body is not None and not isinstance(body, (bytes, str)):
        raise EmailDeliveryError("invalid_message")
    if isinstance(body, str):
        body = body.encode("utf-8")
        prepared.body = body
        prepared.headers["Content-Length"] = str(len(body))
    body_size = len(body) if body is not None else 0
    if body_size > PRESET_REQUEST_LIMIT:
        raise EmailDeliveryError("request_too_large")

    response = session.send(prepared, timeout=timeout, allow_redirects=False, stream=True,
                            proxies={})
    try:
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=response.status_code)
        # Never ask Requests to materialize or decompress an unbounded body.
        if response.raw is None:
            content = response.content  # already buffered by a test/adapter
            if len(content) > PRESET_RESPONSE_LIMIT:
                raise EmailDeliveryError("response_too_large", outcome="unconfirmed", status=response.status_code)
        else:
            chunks = []
            size = 0
            while size <= PRESET_RESPONSE_LIMIT:
                chunk = response.raw.read(min(8192, PRESET_RESPONSE_LIMIT + 1 - size), decode_content=False)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > PRESET_RESPONSE_LIMIT:
                raise EmailDeliveryError("response_too_large", outcome="unconfirmed", status=response.status_code)
            content = b"".join(chunks)
        response._content = content
        response._content_consumed = True
        return response
    finally:
        if response.raw is not None:
            response.raw.close()
        response.close()


PROVIDER_CHOICES = (
    ("sendgrid", "SendGrid"), ("amazon_ses", "Amazon SES"),
    ("mailgun", "Mailgun"), ("postmark", "Postmark"),
    ("brevo", "Brevo"), ("resend", "Resend"),
    ("mailjet", "Mailjet"), ("mailersend", "MailerSend"),
    ("mailtrap", "Mailtrap Email Sending"), ("custom_https", "Custom HTTPS"),
)
_PRESET_MODULES = {
    "amazon_ses": "amazon_ses", "mailgun": "mailgun", "postmark": "postmark",
    "brevo": "brevo", "resend": "resend", "mailjet": "mailjet",
    "mailersend": "mailersend", "mailtrap": "mailtrap",
}
_SECRET_KEYS = {
    "sendgrid": {"api_key"}, "mailgun": {"api_key"},
    "postmark": {"server_token"}, "brevo": {"api_key"},
    "resend": {"api_key"}, "mailjet": {"api_key", "secret_key"},
    "mailersend": {"api_token"}, "mailtrap": {"api_token"},
}
_ENDPOINTS = {
    "brevo": "https://api.brevo.com/v3/",
    "resend": "https://api.resend.com/",
    "postmark": "https://api.postmarkapp.com/",
    "mailjet": "https://api.mailjet.com/v3.1/",
    "mailersend": "https://api.mailersend.com/v1/",
    "mailtrap": "https://send.api.mailtrap.io/api/",
}
_DOMAIN = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\Z")


def screenshot_mode(provider: str, custom_config: dict | None = None) -> str:
    if provider == "brevo":
        return "attachment"
    if provider == "custom_https":
        return (custom_config or {}).get("attachments", {}).get("snapshot_mode", "attachment")
    return "inline"


def mime_mode(provider: str) -> bool:
    return provider == "amazon_ses"


@sensitive_variables()
def validate_api_profile(provider, provider_config, custom_config, credentials):
    """Reject incomplete or surprising profile data without echoing secrets."""
    if not isinstance(provider, str) or provider not in dict(PROVIDER_CHOICES):
        raise ValidationError("Unknown email provider.")
    if not isinstance(provider_config, dict) or not isinstance(custom_config, dict) or not isinstance(credentials, dict):
        raise ValidationError("Invalid email connection configuration.")
    if provider == "custom_https":
        if provider_config:
            raise ValidationError("Custom connections cannot set preset options.")
        from apps.core.email_mapping import validate_custom_config

        validate_custom_config(custom_config)
        auth = custom_config["auth"]
        mode = auth["type"]
        required = {"bearer": {"token"}, "api_key_header": {"api_key"}, "basic": {"username", "password"}}.get(mode)
        if required is None or not required <= credentials.keys():
            raise ValidationError("Custom authentication credentials are incomplete.")
        names = custom_config.get("header_names", [])
        if set(credentials) not in (required, required | {"headers"}) or (names and "headers" not in credentials):
            raise ValidationError("Unexpected custom credential fields.")
        headers = credentials.get("headers", {})
        if (not isinstance(headers, dict)
                or not all(isinstance(name, str) for name in headers)
                or {name.lower() for name in headers} != {name.lower() for name in names}):
            raise ValidationError("Custom header credentials are incomplete.")
        values = [credentials[key] for key in required]
        if mode == "basic" and isinstance(credentials["username"], str) and ":" in credentials["username"]:
            raise ValidationError("Basic authentication username is invalid.")
        if not all(isinstance(value, str) and value and all(32 <= ord(c) != 127 for c in value) for value in values):
            raise ValidationError("Custom authentication credentials are invalid.")
        if mode == "bearer" and any(ord(c) <= 32 for c in credentials["token"]):
            raise ValidationError("Custom authentication credentials are invalid.")
        if not all(isinstance(value, str) and all(32 <= ord(c) != 127 for c in value) for value in headers.values()):
            raise ValidationError("Custom header credentials are invalid.")
        return
    if custom_config:
        raise ValidationError("Preset connections cannot set a custom contract.")
    allowed = {
        "sendgrid": {"region"}, "amazon_ses": {"region", "credential_source"},
        "mailgun": {"region", "sender_domain"}, "postmark": {"message_stream"},
    }.get(provider, set())
    if set(provider_config) - allowed:
        raise ValidationError("Unexpected provider options.")
    if provider == "sendgrid" and (not isinstance(provider_config.get("region", "global"), str) or provider_config.get("region", "global") not in {"global", "eu"}):
        raise ValidationError("Invalid SendGrid region.")
    if provider == "amazon_ses":
        from botocore.session import get_session

        regions = set(get_session().get_available_regions("sesv2", partition_name="aws"))
        if not isinstance(provider_config.get("region"), str) or provider_config.get("region") not in regions:
            raise ValidationError("Select a supported Amazon SES region.")
        source = provider_config.get("credential_source")
        if not isinstance(source, str) or source not in {"deployment", "access_keys"}:
            raise ValidationError("Select an Amazon SES credential source.")
        if source == "deployment":
            if credentials:
                raise ValidationError("Deployment credentials must not be stored here.")
            return
        required = {"aws_access_key_id", "aws_secret_access_key"}
        if not required <= set(credentials) or set(credentials) - required - {"aws_session_token"}:
            raise ValidationError("Amazon SES credentials are incomplete.")
    elif provider == "mailgun":
        if (not isinstance(provider_config.get("region"), str) or provider_config.get("region") not in {"us", "eu"}
                or not isinstance(provider_config.get("sender_domain"), str)
                or not _DOMAIN.fullmatch(provider_config["sender_domain"])):
            raise ValidationError("Mailgun region or sending domain is invalid.")
        required = _SECRET_KEYS[provider]
    else:
        required = _SECRET_KEYS[provider]
    if provider == "postmark" and "message_stream" in provider_config and provider_config["message_stream"] != "":
        stream = provider_config["message_stream"]
        if not isinstance(stream, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", stream):
            raise ValidationError("Invalid Postmark message stream.")
    if not required <= set(credentials) or set(credentials) - required - ({"aws_session_token"} if provider == "amazon_ses" else set()):
        raise ValidationError("Provider credentials are incomplete.")
    if not all(isinstance(v, str) and v and all(32 <= ord(c) != 127 for c in v) for v in credentials.values()):
        raise ValidationError("Provider credentials are invalid.")


@sensitive_variables()
def build_ses_backend(snapshot):
    from botocore.config import Config
    from anymail.backends.amazon_ses import EmailBackend

    options = snapshot.provider_config
    credentials = snapshot.credentials
    session_params = {}
    if options["credential_source"] == "access_keys":
        session_params = {
            "aws_access_key_id": credentials["aws_access_key_id"],
            "aws_secret_access_key": credentials["aws_secret_access_key"],
        }
        if credentials.get("aws_session_token"):
            session_params["aws_session_token"] = credentials["aws_session_token"]
    backend = EmailBackend(
        session_params=session_params,
        client_params={
            "region_name": options["region"],
            "config": Config(connect_timeout=5, read_timeout=20,
                             retries={"total_max_attempts": 1}),
        },
        configuration_set_name=None, message_tag_name=None,
        fail_silently=False, ignore_unsupported_features=False,
        ignore_recipient_status=False, debug_api_requests=False,
    )
    backend.send_defaults = {}
    original_open = backend.open

    @sensitive_variables()
    def open_limited():
        result = original_open()
        if backend.client is not None and not isinstance(backend.client, _SizeLimitedSESClient):
            backend.client = _SizeLimitedSESClient(backend.client)
        return result

    backend.open = open_limited
    return backend


@sensitive_variables()
def build_api_backend(snapshot):
    """Build a backend only from fixed code paths and the captured snapshot."""
    provider = snapshot.provider
    if provider == "custom_https":
        from apps.core.email_custom import EmailBackend

        return EmailBackend(config=snapshot.custom_config, credentials=snapshot.credentials,
                            fail_silently=False)
    if provider == "sendgrid":
        from apps.core.email_sendgrid import EmailBackend

        return EmailBackend(region=snapshot.provider_config.get("region", "global"),
                            api_key=snapshot.credentials["api_key"], fail_silently=False)
    if provider == "amazon_ses":
        return build_ses_backend(snapshot)
    module = importlib.import_module(f"anymail.backends.{_PRESET_MODULES[provider]}")
    options = dict(snapshot.credentials)
    options.update(api_url=_ENDPOINTS.get(provider), timeout=(5, 20),
                   fail_silently=False, ignore_unsupported_features=False,
                   ignore_recipient_status=False, debug_api_requests=False)
    if provider == "mailgun":
        options["api_url"] = "https://api.mailgun.net/v3/" if snapshot.provider_config["region"] == "us" else "https://api.eu.mailgun.net/v3/"
        options["sender_domain"] = snapshot.provider_config["sender_domain"]
    elif provider == "postmark":
        options["message_stream"] = snapshot.provider_config.get("message_stream") or None
        options["use_bulk_api"] = False
    elif provider == "mailersend":
        options["batch_send_mode"] = None
    elif provider == "mailtrap":
        options["sandbox_id"] = None
    backend = module.EmailBackend(**options)
    backend.send_defaults = {}
    if hasattr(backend, "create_session"):
        original_create = backend.create_session

        def create_session():
            session = original_create()
            session.trust_env = False
            session.max_redirects = 0
            session.request = lambda method, url, **kwargs: _bounded_preset_request(
                session, method, url, **kwargs)
            return session

        backend.create_session = create_session
    return backend
