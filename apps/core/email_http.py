"""One bounded HTTPS POST to a DNS-validated, pinned public address."""

import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import urllib3
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError, SSLError

from apps.core.email_errors import EmailDeliveryError

_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED = {
    "host", "content-length", "transfer-encoding", "connection", "trailer", "upgrade",
    "cookie", "accept-encoding", "content-type", "accept", "authorization", "proxy-authorization",
    "proxy-authenticate", "keep-alive", "te",
}
_REJECTED = {400, 401, 403, 404, 405, 413, 415, 422, 429}
MAX_RESPONSE_BYTES = 65536


def validate_header_name(name):
    if not isinstance(name, str) or len(name) > 128 or not _TOKEN.fullmatch(name) or name.lower().startswith("proxy-") or name.lower() in _RESERVED:
        raise ValidationError("header name is invalid or reserved")
    return name


def validate_endpoint(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in endpoint):
        raise ValidationError("endpoint must be a public HTTPS URL")
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
        if parsed.scheme.lower() != "https" or not host or port not in (None, 443) or parsed.username is not None or parsed.password is not None or "?" in endpoint or "#" in endpoint:
            raise ValueError
        if "@" in parsed.netloc or "\\" in endpoint or "{" in endpoint or "}" in endpoint:
            raise ValueError
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if len(host) > 253 or len(host.split(".")) < 2 or not all(_LABEL.fullmatch(label) for label in host.split(".")):
            raise ValueError
        if host.split(".")[-1].isdigit() or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
        if re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9.]+)", host):
            raise ValueError
        return host, parsed.path or "/"
    except (ValueError, UnicodeError):
        raise ValidationError("endpoint must be a public HTTPS URL") from None


def _public_addresses(host):
    try:
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise EmailDeliveryError("dns_error") from None
    addresses = []
    for answer in answers:
        try:
            ip = ipaddress.ip_address(answer[4][0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local or ip.is_private:
                raise ValueError
            addresses.append(str(ip))
        except (ValueError, IndexError, TypeError):
            raise EmailDeliveryError("network_policy") from None
    if not addresses:
        raise EmailDeliveryError("dns_error")
    return addresses


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    elapsed_ms: int
    hostname: str


@sensitive_variables()
def post_json(endpoint, *, headers, payload, max_request_bytes=25 * 1024 * 1024):
    """Return bounded HTTP response; never follow redirects or replay a request."""
    try:
        host, path = validate_endpoint(endpoint)
    except ValidationError:
        raise EmailDeliveryError("invalid_config") from None
    if type(max_request_bytes) is not int or not 1 <= max_request_bytes <= 25 * 1024 * 1024:
        raise EmailDeliveryError("invalid_config")
    if not isinstance(payload, dict):
        raise EmailDeliveryError("invalid_message")
    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise EmailDeliveryError("invalid_message") from None
    if len(body) > max_request_bytes:
        raise EmailDeliveryError("request_too_large")
    if not isinstance(headers, dict):
        raise EmailDeliveryError("invalid_config")
    outgoing = {"Host": host, "Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"}
    seen = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not _TOKEN.fullmatch(name) or name.lower() in seen or name.lower() in _RESERVED - {"authorization"} or name.lower().startswith("proxy-"):
            raise EmailDeliveryError("invalid_config")
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise EmailDeliveryError("invalid_config")
        seen.add(name.lower())
        outgoing[name] = value
    address = _public_addresses(host)[0]
    started = time.monotonic()
    pool = urllib3.HTTPSConnectionPool(
        address, port=443, server_hostname=host, assert_hostname=host,
        cert_reqs=ssl.CERT_REQUIRED, maxsize=1,
        timeout=urllib3.Timeout(connect=5, read=20), retries=False,
    )
    response = None
    try:
        # Establish and verify TLS before the request can submit bytes.
        connection = pool._get_conn()
        try:
            connection.connect()
        except (ConnectTimeoutError, NewConnectionError):
            connection.close()
            raise EmailDeliveryError("connection_error") from None
        except SSLError:
            connection.close()
            raise EmailDeliveryError("tls_error") from None
        except Exception:
            connection.close()
            raise EmailDeliveryError("connection_error") from None
        pool._put_conn(connection)
        response = pool.request("POST", path, body=body, headers=outgoing,
                                retries=False, redirect=False, assert_same_host=False,
                                preload_content=False, decode_content=False)
        status = response.status
        encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
        if encoding not in ("", "identity"):
            raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=status)
        data = response.read(MAX_RESPONSE_BYTES + 1, decode_content=False)
        if len(data) > MAX_RESPONSE_BYTES:
            raise EmailDeliveryError("response_too_large", outcome="unconfirmed", status=status)
        return HttpResponse(status, data, round((time.monotonic() - started) * 1000), host)
    except EmailDeliveryError:
        raise
    except (ConnectTimeoutError, NewConnectionError):
        raise EmailDeliveryError("connection_error") from None
    except SSLError:
        raise EmailDeliveryError("tls_error", outcome="unconfirmed") from None
    except (TimeoutError, urllib3.exceptions.TimeoutError):
        raise EmailDeliveryError("timeout", outcome="unconfirmed") from None
    except Exception:
        # A protocol/write failure may occur after submission; never claim rejection.
        raise EmailDeliveryError("http_unconfirmed", outcome="unconfirmed") from None
    finally:
        if response is not None:
            response.close()
        pool.close()


def _json_pointer(value, pointer):
    if pointer == "":
        return value
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isascii() and (token == "0" or token.isdigit() and not token.startswith("0")) and int(token) < len(value):
            value = value[int(token)]
        else:
            return _MISSING
    return value


_MISSING = object()


def _response_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("non-finite response")


@sensitive_variables()
def evaluate_response(response, *, accepted_statuses, condition=None, message_id_pointer=None):
    """Return a bounded provider ID on acceptance, or raise a classified error."""
    if response.status not in accepted_statuses:
        rejected = response.status in _REJECTED
        raise EmailDeliveryError("http_rejected" if rejected else "http_unconfirmed",
                                 outcome="rejected" if rejected else "unconfirmed", status=response.status)
    if condition is None and message_id_pointer is None:
        return None
    try:
        document = json.loads(response.body, object_pairs_hook=_response_pairs,
                              parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=response.status) from None
    if condition is not None:
        actual = _json_pointer(document, condition["pointer"])
        expected = condition["equals"]
        if type(actual) is not type(expected) or actual != expected:
            raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=response.status)
    if message_id_pointer is None:
        return None
    identifier = _json_pointer(document, message_id_pointer)
    if type(identifier) not in (str, int, float) or not str(identifier).strip():
        raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=response.status)
    identifier = str(identifier)
    if len(identifier) > 128 or not re.fullmatch(r"[A-Za-z0-9._:@/-]+", identifier):
        raise EmailDeliveryError("response_invalid", outcome="unconfirmed", status=response.status)
    return identifier
