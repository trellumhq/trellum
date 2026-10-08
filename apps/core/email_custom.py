"""Django backend for the bounded Custom HTTPS email contract."""

import base64
from email.headerregistry import Address
from email.utils import getaddresses, parseaddr

from django.core.exceptions import ValidationError
from django.core.mail.backends.base import BaseEmailBackend
from django.core.validators import validate_email
from django.views.decorators.debug import sensitive_variables

from apps.core.email_errors import EmailDeliveryError
from apps.core.email_http import evaluate_response, post_json, validate_header_name
from apps.core.email_mapping import _analyze, render_payload, validate_custom_config


@sensitive_variables()
def _address(raw):
    if not isinstance(raw, str) or any(c in raw for c in "\r\n"):
        raise EmailDeliveryError("invalid_message")
    try:
        parsed = getaddresses([raw], strict=True)
    except TypeError:  # Older Python releases do not expose strict parsing.
        parsed = getaddresses([raw])
    except ValueError:
        raise EmailDeliveryError("invalid_message") from None
    if len(parsed) != 1 or not parsed[0][1] or "@" not in parsed[0][1]:
        raise EmailDeliveryError("invalid_message")
    name, address = parsed[0]
    if parseaddr(address)[1] != address:
        raise EmailDeliveryError("invalid_message")
    try:
        validate_email(address)
        formatted = str(Address(display_name=name, addr_spec=address))
    except (ValidationError, ValueError):
        raise EmailDeliveryError("invalid_message") from None
    return {"address": address, "name": name, "formatted": formatted}


@sensitive_variables()
def _addresses(values):
    if not isinstance(values, (list, tuple)):
        raise EmailDeliveryError("invalid_message")
    return [_address(value) for value in values]


@sensitive_variables()
def _attachment(part, max_bytes):
    if isinstance(part, tuple) and len(part) == 3:
        filename, content, content_type = part
        disposition, content_id = "attachment", None
    elif hasattr(part, "get_content_type"):
        if part.is_multipart():
            raise EmailDeliveryError("invalid_message")
        filename = part.get_filename()
        content_type = part.get_content_type()
        content = part.get_payload(decode=True)
        disposition = part.get_content_disposition() or "attachment"
        content_id = part.get("Content-ID")
    else:
        raise EmailDeliveryError("invalid_message")
    if not isinstance(filename, str) or not filename or any(c in filename for c in "\r\n") or not isinstance(content_type, str) or "/" not in content_type or disposition not in {"attachment", "inline"}:
        raise EmailDeliveryError("invalid_message")
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not isinstance(content, bytes):
        raise EmailDeliveryError("invalid_message")
    if len(content) > max_bytes:
        raise EmailDeliveryError("request_too_large")
    if content_id is not None:
        if not isinstance(content_id, str) or any(c in content_id for c in "\r\n"):
            raise EmailDeliveryError("invalid_message")
        content_id = content_id.strip("<>")
    if disposition == "inline" and not content_id:
        raise EmailDeliveryError("invalid_message")
    return {"filename": filename, "content_type": content_type,
            "content_base64": base64.b64encode(content).decode("ascii"),
            "disposition": disposition, "content_id": content_id}


@sensitive_variables()
def build_message_context(prepared_email):
    """Project only supported message data; never expose a Django object to mapping."""
    if getattr(prepared_email, "extra_headers", None) or getattr(prepared_email, "attachments", None) is None:
        raise EmailDeliveryError("invalid_message")
    if getattr(prepared_email, "_inline_image", None) is not None:
        raise EmailDeliveryError("invalid_message")
    if getattr(prepared_email, "content_subtype", "plain") not in {"plain", "html"}:
        raise EmailDeliveryError("invalid_message")
    sender = _address(prepared_email.from_email)
    to = _addresses(prepared_email.to)
    cc = _addresses(prepared_email.cc)
    bcc = _addresses(prepared_email.bcc)
    reply_to = _addresses(prepared_email.reply_to)
    if not to or sum(map(len, (to, cc, bcc, reply_to))) > 100:
        raise EmailDeliveryError("invalid_message")
    if not isinstance(prepared_email.subject, str) or not isinstance(prepared_email.body, str):
        raise EmailDeliveryError("invalid_message")
    text = prepared_email.body if prepared_email.content_subtype == "plain" else ""
    html = prepared_email.body if prepared_email.content_subtype == "html" else None
    for alternative in getattr(prepared_email, "alternatives", []):
        if len(alternative) != 2 or alternative[1] != "text/html" or html is not None or not isinstance(alternative[0], str):
            raise EmailDeliveryError("invalid_message")
        html = alternative[0]
    if len(prepared_email.attachments) > 100:
        raise EmailDeliveryError("invalid_message")
    attachments = []
    total_attachment_bytes = 0
    for part in prepared_email.attachments:
        attachment = _attachment(part, 25 * 1024 * 1024 - total_attachment_bytes)
        total_attachment_bytes += len(attachment["content_base64"])
        if total_attachment_bytes > 25 * 1024 * 1024:
            raise EmailDeliveryError("request_too_large")
        attachments.append(attachment)
    return {"message": {"from": sender, "to": to, "cc": cc, "bcc": bcc,
                        "reply_to": reply_to, "subject": prepared_email.subject,
                        "text": text, "html": html, "attachments": attachments}}


@sensitive_variables()
def _headers(config, credentials):
    if not isinstance(credentials, dict):
        raise EmailDeliveryError("invalid_config")
    auth = config["auth"]
    mode = auth["type"]
    if mode == "bearer":
        expected, secret = {"token", "headers"}, credentials.get("token")
        if not isinstance(secret, str) or not secret or any(ord(c) < 33 or ord(c) == 127 for c in secret):
            raise EmailDeliveryError("invalid_config")
        headers = {"Authorization": "Bearer " + secret}
    elif mode == "api_key_header":
        expected, secret = {"api_key", "headers"}, credentials.get("api_key")
        if not isinstance(secret, str) or not secret or any(ord(c) < 32 or ord(c) == 127 for c in secret):
            raise EmailDeliveryError("invalid_config")
        headers = {auth["header_name"]: secret}
    else:
        expected = {"username", "password", "headers"}
        username, password = credentials.get("username"), credentials.get("password")
        if not isinstance(username, str) or not username or ":" in username or not isinstance(password, str) or not password or any(ord(c) < 32 or ord(c) == 127 for c in username + password):
            raise EmailDeliveryError("invalid_config")
        token = base64.b64encode((username + ":" + password).encode("utf-8")).decode("ascii")
        headers = {"Authorization": "Basic " + token}
    if set(credentials) not in (expected, expected - {"headers"}):
        raise EmailDeliveryError("invalid_config")
    extra = credentials.get("headers", {})
    if not isinstance(extra, dict) or {name.lower() for name in extra} != {name.lower() for name in config["header_names"]}:
        raise EmailDeliveryError("invalid_config")
    for name, value in extra.items():
        try:
            validate_header_name(name)
        except ValidationError:
            raise EmailDeliveryError("invalid_config") from None
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise EmailDeliveryError("invalid_config")
        headers[name] = value
    return headers


class EmailBackend(BaseEmailBackend):
    @sensitive_variables()
    def __init__(self, *, config, credentials, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.config = config
        self.credentials = credentials

    def open(self):
        return False

    def close(self):
        pass

    @sensitive_variables()
    def send_messages(self, email_messages):
        accepted = 0
        for email in email_messages:
            try:
                try:
                    config = validate_custom_config(self.config)
                    context = build_message_context(email)
                    bindings, _ = _analyze(config["payload"])
                    if any(part["disposition"] == "inline" for part in context["message"]["attachments"]):
                        if config["attachments"]["snapshot_mode"] != "inline":
                            raise EmailDeliveryError("invalid_message")
                        if "message.attachments" not in bindings and not {"message.attachments.content_id", "message.attachments.disposition"} <= bindings:
                            raise EmailDeliveryError("invalid_config")
                    payload = render_payload(config["payload"], context)
                except ValidationError:
                    raise EmailDeliveryError("invalid_config") from None
                response = post_json(config["endpoint"], headers=_headers(config, self.credentials),
                                     payload=payload, max_request_bytes=config["max_request_bytes"])
                evaluate_response(response, **config["response"])
                accepted += 1
            except EmailDeliveryError:
                if not self.fail_silently:
                    raise
        return accepted
