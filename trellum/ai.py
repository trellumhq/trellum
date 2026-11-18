"""Credential-free, portal-hosted single-shot completions for reports."""
from __future__ import annotations

import json
import math
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_TOKENS = 4096
MAX_TOKENS = 16384
MAX_PROMPT_BYTES = 256 * 1024
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 120
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ReportAIError(Exception):
    """A safe completion failure that a report can catch and publish around."""

    def __init__(self, code: str, message: str = "AI completion is unavailable."):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CompletionResult:
    text: str
    finish_reason: str
    model: str
    usage: dict[str, Any] | None = None
    cost_usd: str | float | None = None


def validate_request(*, system, user, max_tokens=DEFAULT_MAX_TOKENS,
                     response_format=None, model=None, timeout=DEFAULT_TIMEOUT):
    """Validate the public protocol before contacting a provider or broker."""
    if not all(isinstance(value, str) and value.strip() for value in (system, user)):
        raise ReportAIError("invalid_request", "system and user must be nonempty strings.")
    try:
        prompt_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
    except UnicodeError:
        raise ReportAIError("invalid_request", "Prompts must contain valid Unicode.") from None
    if prompt_bytes > MAX_PROMPT_BYTES:
        raise ReportAIError("invalid_request", "Prompts exceed the 256 KiB limit.")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS:
        raise ReportAIError("invalid_request", "max_tokens must be between 1 and 16384.")
    if response_format not in (None, "json"):
        raise ReportAIError("invalid_request", 'response_format must be omitted or "json".')
    if model is not None and (not isinstance(model, str) or not model.strip() or len(model) > 512):
        raise ReportAIError("invalid_request", "model must be a nonempty model identifier.")
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT):
        raise ReportAIError("invalid_request", "timeout must be greater than zero and at most 120 seconds.")
    return dict(system=system, user=user, max_tokens=max_tokens,
                response_format=response_format, model=model, timeout=timeout)


@contextmanager
def unix_socket_address(path: str):
    """Address long Linux paths through a held directory fd, without symlinks."""
    if len(os.fsencode(path)) < 104:
        yield path
        return
    directory, name = os.path.split(os.path.abspath(path))
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield f"/proc/self/fd/{descriptor}/{name}"
    finally:
        os.close(descriptor)


class ReportAI:
    """Blocking broker client. Provider configuration never enters this process."""

    def complete(self, *, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS,
                 response_format: str | None = None, model: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> CompletionResult:
        request = validate_request(system=system, user=user, max_tokens=max_tokens,
                                   response_format=response_format, model=model, timeout=timeout)
        path = os.environ.get("TRELLUM_AI_SOCKET")
        token = os.environ.get("TRELLUM_AI_TOKEN")
        if not path or not token or not hasattr(socket, "AF_UNIX"):
            raise ReportAIError("unavailable", "AI completions require a portal report build.")
        request["token"] = token
        deadline = time.monotonic() + timeout
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                with unix_socket_address(path) as address:
                    connection.connect(address)
                connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                data = bytearray()
                while b"\n" not in data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    connection.settimeout(remaining)
                    chunk = connection.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(data)))
                    if not chunk:
                        raise ReportAIError("invalid_response", "AI broker returned an incomplete response.")
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ReportAIError("invalid_response", "AI response exceeded the size limit.")
            response = json.loads(data.split(b"\n", 1)[0])
            if not isinstance(response, dict):
                raise ValueError
            if "error" in response:
                error = response["error"]
                if not isinstance(error, dict) or not all(isinstance(error.get(k), str) for k in ("code", "message")):
                    raise ValueError
                raise ReportAIError(error["code"], error["message"])
            result = response["result"]
            if not isinstance(result, dict) or not all(isinstance(result.get(k), str) for k in ("text", "finish_reason", "model")):
                raise ValueError
            return CompletionResult(**{k: result[k] for k in ("text", "finish_reason", "model", "usage", "cost_usd") if k in result})
        except ReportAIError:
            raise
        except (socket.timeout, TimeoutError):
            raise ReportAIError("timeout", "AI completion timed out.") from None
        except OSError:
            raise ReportAIError("unavailable", "AI completion broker is unavailable.") from None
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ReportAIError("invalid_response", "AI broker returned an invalid response.") from None
