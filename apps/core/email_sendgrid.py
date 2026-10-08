"""SendGrid Mail Send v3 adapter with fixed Global and EU destinations."""
from __future__ import annotations

import copy

from django.core.mail.backends.base import BaseEmailBackend
from django.views.decorators.debug import sensitive_variables

from apps.core.email_errors import EmailDeliveryError
from apps.core.email_custom import build_message_context
from apps.core.email_http import evaluate_response, post_json


_ENDPOINTS = {
    "global": "https://api.sendgrid.com/v3/mail/send",
    "eu": "https://api.eu.sendgrid.com/v3/mail/send",
}


def _address(value):
    result = {"email": value["address"]}
    if value.get("name"):
        result["name"] = value["name"]
    return result


@sensitive_variables()
def _payload(email):
    clean = copy.copy(email)
    clean.extra_headers = {}
    message = build_message_context(clean)["message"]
    personalization = {"to": [_address(value) for value in message["to"]]}
    for field in ("cc", "bcc"):
        if message[field]:
            personalization[field] = [_address(value) for value in message[field]]
    if not personalization["to"]:
        raise EmailDeliveryError("invalid_message")
    data = {
        "personalizations": [personalization],
        "from": _address(message["from"]),
        "subject": message["subject"],
        "content": [{"type": "text/plain", "value": message["text"]}],
    }
    if message["html"]:
        data["content"].append({"type": "text/html", "value": message["html"]})
    if message["reply_to"]:
        if len(message["reply_to"]) != 1:
            raise EmailDeliveryError("invalid_message")
        data["reply_to"] = _address(message["reply_to"][0])
    if message["attachments"]:
        data["attachments"] = []
        for item in message["attachments"]:
            attachment = {
                "filename": item["filename"], "type": item["content_type"],
                "content": item["content_base64"], "disposition": item["disposition"],
            }
            if item.get("content_id"):
                attachment["content_id"] = item["content_id"].strip("<>")
            data["attachments"].append(attachment)
    if email.extra_headers:
        headers = {}
        for key, value in email.extra_headers.items():
            if (not key.lower().startswith("x-") or not isinstance(value, str)
                    or "\r" in value or "\n" in value):
                raise EmailDeliveryError("invalid_message")
            headers[key] = value
        data["headers"] = headers
    return data


class EmailBackend(BaseEmailBackend):
    @sensitive_variables()
    def __init__(self, *, region, api_key, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        if region not in _ENDPOINTS or not api_key:
            raise EmailDeliveryError("invalid_config")
        self.endpoint = _ENDPOINTS[region]
        self.api_key = api_key

    def open(self):
        return False

    def close(self):
        return None

    @sensitive_variables()
    def send_messages(self, email_messages):
        count = 0
        for message in email_messages:
            try:
                response = post_json(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    payload=_payload(message),
                    max_request_bytes=25 * 1024 * 1024,
                )
                evaluate_response(response, accepted_statuses=(202,))
                count += 1
            except EmailDeliveryError:
                if not self.fail_silently:
                    raise
        return count
