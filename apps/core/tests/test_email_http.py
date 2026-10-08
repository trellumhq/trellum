import socket

import pytest
from django.core.exceptions import ValidationError
from urllib3.exceptions import ReadTimeoutError, SSLError

from apps.core.email_errors import EmailDeliveryError
from apps.core.email_http import HttpResponse, _public_addresses, evaluate_response, post_json, validate_endpoint


def _dns(*addresses):
    return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0", "240.0.0.1", "::1", "ff02::1", "::ffff:127.0.0.1"])
def test_rejects_nonpublic_dns_and_mixed_answers(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8", address))
    with pytest.raises(EmailDeliveryError) as error:
        _public_addresses("api.example.com")
    assert error.value.category == "network_policy"
    assert error.value.outcome == "not_submitted"


@pytest.mark.parametrize("endpoint", [
    "http://api.example.com/send", "https://localhost/send", "https://127.0.0.1/send",
    "https://2130706433/send", "https://0x7f000001/send", "https://api.example.com:8443/send",
    "https://user:pass@api.example.com/send", "https://api.example.com/send?", "https://api.example.com/send#",
    "https://api.example.com\\@127.0.0.1/send",
])
def test_endpoint_validation(endpoint):
    with pytest.raises(ValidationError):
        validate_endpoint(endpoint)


class FakeConnection:
    def __init__(self):
        self.connected = False

    def connect(self):
        self.connected = True

    def close(self):
        pass


class FakeResponse:
    def __init__(self, status=202, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.closed = False

    def read(self, amount, **kwargs):
        assert amount == 65537 and kwargs == {"decode_content": False}
        return self.body[:amount]

    def close(self):
        self.closed = True


def test_pinned_ip_tls_host_no_retry_redirect_proxy_and_close(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8"))
    calls = []

    class FakePool:
        def __init__(self, ip, **kwargs):
            calls.append(("pool", ip, kwargs))
            self.connection = FakeConnection()

        def _get_conn(self):
            return self.connection

        def _put_conn(self, connection):
            assert connection.connected

        def request(self, method, path, **kwargs):
            calls.append(("request", method, path, kwargs))
            assert self.connection.connected
            return FakeResponse()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr("apps.core.email_http.urllib3.HTTPSConnectionPool", FakePool)
    response = post_json("https://api.example.com/send", headers={"Authorization": "Bearer token"}, payload={"subject": "é"})
    assert response.status == 202 and response.hostname == "api.example.com"
    assert calls[0][1] == "8.8.8.8"
    assert calls[0][2]["server_hostname"] == calls[0][2]["assert_hostname"] == "api.example.com"
    assert calls[1][3]["headers"]["Host"] == "api.example.com"
    assert calls[1][3]["headers"]["Accept-Encoding"] == "identity"
    assert calls[1][3]["retries"] is False and calls[1][3]["redirect"] is False
    assert calls[-1] == ("close",)
    assert calls[1][3]["body"] == b'{"subject":"\xc3\xa9"}'


def test_request_size_and_response_size_before_any_unbounded_read(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8"))
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload={"x": "é"}, max_request_bytes=1)
    assert error.value.category == "request_too_large"

    class FakePool:
        def __init__(self, *args, **kwargs):
            self.connection = FakeConnection()
            self.response = FakeResponse(body=b"a" * 65537)

        def _get_conn(self):
            return self.connection

        def _put_conn(self, connection):
            pass

        def request(self, *args, **kwargs):
            return self.response

        def close(self):
            pass

    monkeypatch.setattr("apps.core.email_http.urllib3.HTTPSConnectionPool", FakePool)
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload={})
    assert error.value.category == "response_too_large" and error.value.outcome == "unconfirmed"


def test_preflight_tls_failure_is_not_submitted_and_post_failure_is_unconfirmed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8"))
    requests = []

    class FakePool:
        def __init__(self, *a, **k):
            self.connection = FakeConnection()

        def _get_conn(self):
            return self.connection

        def _put_conn(self, connection):
            pass

        def request(self, *a, **k):
            requests.append(1)
            raise SSLError("secret server text")

        def close(self):
            pass

    monkeypatch.setattr("apps.core.email_http.urllib3.HTTPSConnectionPool", FakePool)
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload={})
    assert error.value.outcome == "unconfirmed" and len(requests) == 1
    assert "secret" not in str(error.value)

    def fail_connect(self):
        raise SSLError("secret server text")

    monkeypatch.setattr(FakeConnection, "connect", fail_connect)
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload={})
    assert error.value.outcome == "not_submitted" and len(requests) == 1


def test_read_timeout_after_submission_is_unconfirmed_without_retry(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8"))
    attempts = []

    class FakePool:
        def __init__(self, *a, **k):
            self.connection = FakeConnection()

        def _get_conn(self):
            return self.connection

        def _put_conn(self, connection):
            pass

        def request(self, *a, **k):
            attempts.append(1)
            raise ReadTimeoutError(None, "/send", "secret remote detail")

        def close(self):
            pass

    monkeypatch.setattr("apps.core.email_http.urllib3.HTTPSConnectionPool", FakePool)
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={"Authorization": "Bearer secret"}, payload={})
    assert error.value.category == "timeout" and error.value.outcome == "unconfirmed"
    assert error.value.__cause__ is None and "secret" not in str(error.value)
    assert attempts == [1]


def test_deep_payload_and_required_response_are_sanitized(monkeypatch):
    nested = []
    payload = {"value": nested}
    for _ in range(5000):
        child = []
        nested.append(child)
        nested = child
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload=payload)
    assert error.value.category == "invalid_message" and error.value.__cause__ is None

    response = HttpResponse(200, b"[" * 5000 + b"0" + b"]" * 5000, 1, "api.example.com")
    with pytest.raises(EmailDeliveryError) as error:
        evaluate_response(response, accepted_statuses=[200], condition={"pointer": "", "equals": True})
    assert error.value.category == "response_invalid" and error.value.outcome == "unconfirmed"
    assert error.value.__cause__ is None
    assert post_json.sensitive_variables == "__ALL__"
    assert evaluate_response.sensitive_variables == "__ALL__"


def test_compressed_response_is_unconfirmed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns("8.8.8.8"))

    class FakePool:
        def __init__(self, *a, **k):
            self.connection = FakeConnection()

        def _get_conn(self):
            return self.connection

        def _put_conn(self, connection):
            pass

        def request(self, *a, **k):
            return FakeResponse(headers={"Content-Encoding": "gzip"})

        def close(self):
            pass

    monkeypatch.setattr("apps.core.email_http.urllib3.HTTPSConnectionPool", FakePool)
    with pytest.raises(EmailDeliveryError) as error:
        post_json("https://api.example.com/send", headers={}, payload={})
    assert error.value.category == "response_invalid" and error.value.outcome == "unconfirmed"


@pytest.mark.parametrize("status,outcome", [(400, "rejected"), (401, "rejected"), (429, "rejected"), (302, "unconfirmed"), (408, "unconfirmed"), (500, "unconfirmed")])
def test_status_outcomes(status, outcome):
    with pytest.raises(EmailDeliveryError) as error:
        evaluate_response(HttpResponse(status, b"", 2, "api.example.com"), accepted_statuses=[202])
    assert error.value.outcome == outcome and error.value.status == status


def test_status_only_and_json_pointer_condition_type_and_id():
    assert evaluate_response(HttpResponse(202, b"", 1, "api.example.com"), accepted_statuses=[202]) is None
    response = HttpResponse(200, b'{"a/b":{"~key":true},"id":"safe-123"}', 1, "api.example.com")
    assert evaluate_response(response, accepted_statuses=[200], condition={"pointer": "/a~1b/~0key", "equals": True}, message_id_pointer="/id") == "safe-123"
    for condition in ({"pointer": "/a~1b/~0key", "equals": 1}, {"pointer": "/missing", "equals": True}):
        with pytest.raises(EmailDeliveryError) as error:
            evaluate_response(response, accepted_statuses=[200], condition=condition)
        assert error.value.outcome == "unconfirmed"
    for body in (b"", b"not json", b'{"id":"bad value"}', b'{"id":"one","id":"two"}', b'{"id":NaN}'):
        with pytest.raises(EmailDeliveryError) as error:
            evaluate_response(HttpResponse(200, body, 1, "api.example.com"), accepted_statuses=[200], message_id_pointer="/id")
        assert error.value.outcome == "unconfirmed"
