"""Bounded, typed JSON mapping for custom email APIs. No I/O occurs here."""

import json
import math
import re

from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables

from apps.core.email_http import validate_endpoint, validate_header_name

_ADDRESS = {"address", "name", "formatted"}
_ATTACHMENT = {"filename", "content_type", "content_base64", "disposition", "content_id"}
_COLLECTIONS = {
    "message.to": _ADDRESS, "message.cc": _ADDRESS, "message.bcc": _ADDRESS,
    "message.reply_to": _ADDRESS, "message.attachments": _ATTACHMENT,
}
_SCALARS = {"message.subject", "message.text", "message.html"}
_VALUES = _SCALARS | {"message.from", "message.from.address", "message.from.name", "message.from.formatted"} | set(_COLLECTIONS)
_REQUIRED = {"message.from", "message.to", "message.subject", "message.text", "message.html", "message.attachments"}
_OMIT = object()


def _error(location, reason):
    raise ValidationError(f"{location}: {reason}")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _error("configuration", "duplicate JSON key")
        result[key] = value
    return result


@sensitive_variables()
def _json_bytes(value, location):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _error(location, "must contain finite JSON values")


def _pointer(pointer, location):
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")) or len(pointer) > 256:
        _error(location, "must be an RFC 6901 JSON pointer")
    if re.search(r"~(?![01])", pointer):
        _error(location, "has an invalid JSON pointer escape")


def _inspect(node, location, *, depth=1, each=None, parent="root", inline=False, count=None, bindings=None, collections=None):
    if depth > 12:
        _error(location, "mapping is too deep")
    count[0] += 1
    if count[0] > 500:
        _error(location, "mapping has too many nodes")
    if isinstance(node, dict):
        special = {key for key in node if isinstance(key, str) and key.startswith("$")}
        if special:
            if "$value" in special and set(node) in ({"$value"}, {"$value", "$omit_if_empty"}):
                if "$omit_if_empty" in node and parent != "object":
                    _error(location, "omission is only allowed in an object property")
                path = node["$value"]
                allowed = _VALUES if each is None else _VALUES | {"item"} | {f"item.{field}" for field in _COLLECTIONS[each]}
                if not isinstance(path, str) or path not in allowed:
                    _error(location, "unknown value path")
                if path.startswith("item") and each is None:
                    _error(location, "item is unavailable here")
                if node.get("$omit_if_empty", True) is not True:
                    _error(location, "omit_if_empty must be true")
                bindings.add(each if path == "item" else path if not path.startswith("item.") else f"{each}.{path[5:]}")
            elif "$each" in special and set(node) in ({"$each", "$template"}, {"$each", "$template", "$omit_if_empty"}):
                if "$omit_if_empty" in node and parent != "object":
                    _error(location, "omission is only allowed in an object property")
                path = node["$each"]
                if each is not None or not isinstance(path, str) or path not in _COLLECTIONS:
                    _error(location, "unknown or nested collection")
                if node.get("$omit_if_empty", True) is not True:
                    _error(location, "omit_if_empty must be true")
                collections.add(path)
                item_bindings = set()
                _inspect(node["$template"], location + ".$template", depth=depth+1, each=path, parent="root",
                         inline=inline, count=count, bindings=item_bindings, collections=collections)
                if path in {"message.to", "message.cc", "message.bcc", "message.reply_to"}:
                    if not {path, f"{path}.address", f"{path}.formatted"} & item_bindings:
                        _error(location, "collection template must preserve address")
                elif path not in item_bindings:
                    required = {f"{path}.{field}" for field in ("filename", "content_type", "content_base64")}
                    if inline:
                        required |= {f"{path}.content_id", f"{path}.disposition"}
                    if not required <= item_bindings:
                        _error(location, "collection template must preserve attachment fields")
                bindings.update(item_bindings)
            else:
                _error(location, "invalid mapping operator")
        else:
            for key, value in node.items():
                if not isinstance(key, str):
                    _error(location, "object keys must be strings")
                _inspect(value, location + "." + key, depth=depth+1, each=each, parent="object", inline=inline,
                         count=count, bindings=bindings, collections=collections)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _inspect(value, f"{location}[{index}]", depth=depth+1, each=each, parent="array", inline=inline,
                     count=count, bindings=bindings, collections=collections)
    elif not (node is None or isinstance(node, (str, bool, int)) or isinstance(node, float) and math.isfinite(node)):
        _error(location, "must contain finite JSON values")


def _analyze(mapping, *, inline=False):
    if not isinstance(mapping, dict):
        _error("payload", "root must be a JSON object")
    bindings, collections = set(), set()
    _inspect(mapping, "payload", inline=inline, count=[0], bindings=bindings, collections=collections)
    def covered(path):
        return path in bindings or path in collections or any(b.startswith(path + ".") for b in bindings)
    for path in _REQUIRED:
        if not covered(path):
            _error("payload", f"missing required binding for {path}")
    if not {"message.from", "message.from.address", "message.from.formatted"} & bindings:
        _error("payload", "missing sender address binding")
    for path in ("message.to", "message.cc", "message.bcc", "message.reply_to"):
        if path in collections and path not in bindings and f"{path}.address" not in bindings and f"{path}.formatted" not in bindings:
            _error("payload", f"missing address binding for {path}")
    return bindings, collections


@sensitive_variables()
def validate_custom_config(config):
    """Return a validated, non-secret configuration or raise value-free ValidationError."""
    if isinstance(config, str):
        try:
            source_size = len(config.encode("utf-8"))
        except UnicodeError:
            _error("configuration", "invalid JSON encoding")
        if source_size > 65536:
            _error("configuration", "exceeds 64 KiB")
        try:
            config = json.loads(config, object_pairs_hook=_pairs,
                                parse_constant=lambda _: _error("configuration", "non-finite number"))
        except (ValueError, UnicodeError, RecursionError):
            _error("configuration", "invalid JSON")
    if not isinstance(config, dict) or set(config) != {
        "schema_version", "endpoint", "auth", "header_names", "payload", "response", "attachments", "max_request_bytes"
    }:
        _error("configuration", "unexpected or missing fields")
    if len(_json_bytes(config, "configuration")) > 65536:
        _error("configuration", "exceeds 64 KiB")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        _error("schema_version", "unsupported version")
    validate_endpoint(config["endpoint"])
    auth = config["auth"]
    if not isinstance(auth, dict) or not isinstance(auth.get("type"), str) or auth["type"] not in {"bearer", "api_key_header", "basic"}:
        _error("auth", "invalid authentication mode")
    expected = {"type", "header_name"} if auth["type"] == "api_key_header" else {"type"}
    if set(auth) != expected:
        _error("auth", "unexpected or missing fields")
    auth_header = "authorization"
    if auth["type"] == "api_key_header":
        validate_header_name(auth["header_name"])
        auth_header = auth["header_name"].lower()
    names = config["header_names"]
    if not isinstance(names, list) or len(names) > 10:
        _error("header_names", "must be a list of at most 10 names")
    seen = {auth_header}
    for name in names:
        validate_header_name(name)
        if name.lower() in seen:
            _error("header_names", "duplicate or authentication header collision")
        seen.add(name.lower())
    response = config["response"]
    if not isinstance(response, dict) or not set(response) <= {"accepted_statuses", "condition", "message_id_pointer"} or "accepted_statuses" not in response:
        _error("response", "invalid response rules")
    statuses = response["accepted_statuses"]
    if not isinstance(statuses, list) or not statuses or len(statuses) > 100 or len(set(map(str, statuses))) != len(statuses) or any(type(s) is not int or not 200 <= s <= 299 for s in statuses):
        _error("response.accepted_statuses", "must contain unique 2xx statuses")
    if "condition" in response:
        condition = response["condition"]
        if not isinstance(condition, dict) or set(condition) != {"pointer", "equals"} or not (condition["equals"] is None or type(condition["equals"]) in {str, bool, int, float}):
            _error("response.condition", "must contain pointer and scalar equality")
        _json_bytes(condition["equals"], "response.condition")
        _pointer(condition["pointer"], "response.condition.pointer")
    if "message_id_pointer" in response:
        _pointer(response["message_id_pointer"], "response.message_id_pointer")
    if not isinstance(config["attachments"], dict) or set(config["attachments"]) != {"snapshot_mode"} or not isinstance(config["attachments"]["snapshot_mode"], str) or config["attachments"]["snapshot_mode"] not in {"attachment", "inline"}:
        _error("attachments", "invalid snapshot mode")
    size = config["max_request_bytes"]
    if type(size) is not int or not 1 <= size <= 25 * 1024 * 1024:
        _error("max_request_bytes", "must be between 1 byte and 25 MiB")
    _analyze(config["payload"], inline=config["attachments"]["snapshot_mode"] == "inline")
    return config


def _empty(value):
    return value is None or value == "" or value == []


@sensitive_variables()
def render_payload(mapping, context):
    """Bind allowlisted values without interpolation or object introspection."""
    bindings, collections = _analyze(mapping)
    if not isinstance(context, dict) or not isinstance(context.get("message"), dict):
        _error("context", "invalid message")
    message = context["message"]
    if any(not isinstance(message.get(path[8:]), list) for path in _COLLECTIONS):
        _error("context", "invalid message collections")
    if not isinstance(message.get("from"), dict):
        _error("context", "invalid sender")
    if sum(len(message.get(path[8:], [])) for path in _COLLECTIONS if path != "message.attachments") > 100:
        _error("context", "too many recipients")
    for path in ("message.cc", "message.bcc", "message.reply_to"):
        if message.get(path[8:]) and not (path in collections or path in bindings):
            _error("payload", f"missing binding for {path}")
    if message.get("attachments") and "message.attachments" not in collections and "message.attachments" not in bindings:
        _error("payload", "missing attachment binding")

    @sensitive_variables()
    def bind(node, location, item=None, collection=None, parent="root"):
        if isinstance(node, dict) and "$value" in node:
            path = node["$value"]
            if path == "item":
                value = item
            elif path.startswith("item."):
                value = item.get(path[5:]) if isinstance(item, dict) else None
            elif path in _SCALARS or path in _COLLECTIONS:
                value = message.get(path[8:])
            elif path == "message.from":
                value = message.get("from")
            else:
                value = message.get("from", {}).get(path[13:])
            if node.get("$omit_if_empty") and _empty(value):
                if parent != "object":
                    _error(location, "omission is only allowed in an object property")
                return _OMIT
            return value
        if isinstance(node, dict) and "$each" in node:
            path = node["$each"]
            values = message.get(path[8:]) or []
            result = [bind(node["$template"], f"{location}[{index}]", value, path, "array") for index, value in enumerate(values)]
            if node.get("$omit_if_empty") and not result:
                if parent != "object":
                    _error(location, "omission is only allowed in an object property")
                return _OMIT
            return result
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                bound = bind(value, location + "." + key, item, collection, "object")
                if bound is not _OMIT:
                    result[key] = bound
            return result
        if isinstance(node, list):
            return [bind(value, f"{location}[{index}]", item, collection, "array") for index, value in enumerate(node)]
        return node

    result = bind(mapping, "payload")
    _json_bytes(result, "payload")
    return result


def default_custom_config():
    return {
        "schema_version": 1,
        "endpoint": "https://api.example.com/send",
        "auth": {"type": "bearer"},
        "header_names": [],
        "payload": {
            "sender": {"email": {"$value": "message.from.address"}},
            "to": {"$each": "message.to", "$template": {"email": {"$value": "item.address"}}},
            "cc": {"$each": "message.cc", "$template": {"email": {"$value": "item.address"}}, "$omit_if_empty": True},
            "bcc": {"$each": "message.bcc", "$template": {"email": {"$value": "item.address"}}, "$omit_if_empty": True},
            "reply_to": {"$each": "message.reply_to", "$template": {"email": {"$value": "item.address"}}, "$omit_if_empty": True},
            "subject": {"$value": "message.subject"},
            "text": {"$value": "message.text"},
            "html": {"$value": "message.html", "$omit_if_empty": True},
            "attachments": {"$each": "message.attachments", "$template": {
                "name": {"$value": "item.filename"}, "type": {"$value": "item.content_type"},
                "content": {"$value": "item.content_base64"}}, "$omit_if_empty": True},
        },
        "response": {"accepted_statuses": [200, 201, 202]},
        "attachments": {"snapshot_mode": "attachment"},
        "max_request_bytes": 10 * 1024 * 1024,
    }


def synthetic_message_context(kind="simple"):
    if kind not in {"simple", "report"}:
        _error("preview", "unknown sample kind")
    import base64

    sender = {"address": "sender@example.com", "name": "Sample Sender", "formatted": "Sample Sender <sender@example.com>"}
    recipient = {"address": "recipient@example.net", "name": "Sample Recipient", "formatted": "Sample Recipient <recipient@example.net>"}
    attachments = []
    if kind == "report":
        attachments = [
            {"filename": "snapshot.png", "content_type": "image/png", "content_base64": base64.b64encode(b"\x89PNG\r\n\x1a\npreview").decode(), "disposition": "attachment", "content_id": None},
            {"filename": "report.pdf", "content_type": "application/pdf", "content_base64": base64.b64encode(b"%PDF-1.4\npreview").decode(), "disposition": "attachment", "content_id": None},
        ]
    return {"message": {"from": sender, "to": [recipient], "cc": [], "bcc": [], "reply_to": [],
                        "subject": "Sample report" if kind == "report" else "Sample email",
                        "text": "A sample email.\nNo real recipients.", "html": "<p>A sample email.</p>", "attachments": attachments}}
