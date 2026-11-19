"""The standalone broker client never needs a provider SDK or credentials."""
import json
import socket
import threading

import pytest

from trellum.ai import (
    MAX_RESPONSE_BYTES,
    ReportAI,
    ReportAIError,
    unix_socket_address,
    validate_request,
)
from trellum.report import ReportContext


def test_context_always_has_ai(tmp_path, monkeypatch):
    monkeypatch.delenv("TRELLUM_AI_SOCKET", raising=False)
    monkeypatch.delenv("TRELLUM_AI_TOKEN", raising=False)
    ctx = ReportContext({}, "example", str(tmp_path))
    with pytest.raises(ReportAIError) as error:
        ctx.ai.complete(system="system", user="question")
    assert error.value.code == "unavailable"


@pytest.mark.parametrize("kwargs", [
    {"system": ""}, {"user": None}, {"user": "x" * (256 * 1024)},
    {"system": "\ud800"}, {"max_tokens": 0}, {"max_tokens": 16385},
    {"max_tokens": True}, {"response_format": "xml"}, {"model": ""},
    {"timeout": 0}, {"timeout": 121}, {"timeout": float("nan")},
])
def test_invalid_request(kwargs):
    with pytest.raises(ReportAIError) as error:
        validate_request(**({"system": "system", "user": "user"} | kwargs))
    assert error.value.code == "invalid_request"


@pytest.fixture
def broker(tmp_path, monkeypatch):
    path = str(tmp_path / "ai.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    monkeypatch.setenv("TRELLUM_AI_SOCKET", path)
    monkeypatch.setenv("TRELLUM_AI_TOKEN", "private-capability")
    threads = []

    def serve(response):
        captured = []

        def run():
            with listener.accept()[0] as connection:
                data = bytearray()
                while b"\n" not in data:
                    data.extend(connection.recv(65536))
                captured.append(json.loads(data))
                try:
                    connection.sendall(response)
                except BrokenPipeError:
                    pass

        thread = threading.Thread(target=run)
        threads.append(thread)
        thread.start()
        return captured

    yield serve
    listener.close()
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_completion_protocol_and_no_token_in_result(broker):
    captured = broker(json.dumps({"result": {"text": "diagnosis", "finish_reason": "stop", "model": "saved-model", "cost_usd": "0.01"}}).encode() + b"\n")
    result = ReportAI().complete(system="system", user="facts", max_tokens=8000)
    assert result.text == "diagnosis"
    assert result.model == "saved-model"
    assert "private-capability" not in repr(result)
    assert captured[0]["token"] == "private-capability"
    assert captured[0]["max_tokens"] == 8000


@pytest.mark.parametrize("response,code", [
    (b'{"error":{"code":"budget","message":"Organization cap reached."}}\n', "budget"),
    (b'not json\n', "invalid_response"),
    (b'{"result":{"text":3}}\n', "invalid_response"),
    (b'[]\n', "invalid_response"),
    (b'x' * (MAX_RESPONSE_BYTES + 1), "invalid_response"),
])
def test_structured_failure(broker, response, code):
    broker(response)
    with pytest.raises(ReportAIError) as error:
        ReportAI().complete(system="system", user="facts")
    assert error.value.code == code
    assert "private-capability" not in str(error.value)


def test_deeply_nested_response_is_a_catchable_failure(broker):
    depth = 10000
    response = b'[' * depth + b'0' + b']' * depth + b'\n'
    assert len(response) < MAX_RESPONSE_BYTES
    broker(response)
    with pytest.raises(ReportAIError) as error:
        ReportAI().complete(system="system", user="facts")
    assert error.value.code == "invalid_response"


def test_missing_listener_is_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("TRELLUM_AI_SOCKET", str(tmp_path / "absent.sock"))
    monkeypatch.setenv("TRELLUM_AI_TOKEN", "private-capability")
    with pytest.raises(ReportAIError) as error:
        ReportAI().complete(system="system", user="facts")
    assert error.value.code == "unavailable"
    assert "private-capability" not in repr(error.value)


def test_long_socket_directory(tmp_path, monkeypatch):
    directory = tmp_path / ("long-" * 30)
    directory.mkdir()
    path = str(directory / "ai.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        with unix_socket_address(path) as address:
            listener.bind(address)
        listener.listen(1)
        monkeypatch.setenv("TRELLUM_AI_SOCKET", path)
        monkeypatch.setenv("TRELLUM_AI_TOKEN", "private-capability")

        def respond():
            with listener.accept()[0] as connection:
                connection.recv(65536)
                connection.sendall(b'{"result":{"text":"ok","finish_reason":"stop","model":"saved"}}\n')

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            assert ReportAI().complete(system="s", user="u").text == "ok"
        finally:
            thread.join(timeout=2)
        assert not thread.is_alive()
