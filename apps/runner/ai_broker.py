"""Per-run capability socket. Provider credentials stay in a killable worker."""
from __future__ import annotations

from dataclasses import asdict
import hmac
import json
import logging
import multiprocessing
import os
from pathlib import Path
import secrets
import socket
import threading
import time

from trellum.ai import ReportAIError, unix_socket_address

logger = logging.getLogger("trellum.runner.ai")
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _error(code, message):
    return {"error": {"code": code, "message": message}}


def _complete(org_id, request):
    from apps.assistant import budget
    from apps.assistant.llm import LLMConfig, complete_once, is_available
    from apps.orgs.models import Organization

    org = Organization.objects.get(pk=org_id)
    config = LLMConfig.for_org(org)
    if config is None:
        raise ReportAIError("disabled", "AI is disabled or not configured for this organization.")
    ok, _ = is_available(org, purpose="report", model=request.get("model"))
    if not ok:
        # Readiness can contain administrator-controlled values; never relay it.
        raise ReportAIError("unavailable", "Check the saved AI configuration and model prices in AI settings.")
    ok, reason = budget.report_precheck(org)
    if not ok:
        raise ReportAIError("budget", reason)
    try:
        result = complete_once(config, **request)
    except ReportAIError as error:
        # A rejected answer can still carry valid usage from the provider.
        cost = getattr(error, "cost_usd", None)
        if cost is not None:
            budget.report_record(org, cost)
        raise
    if result.cost_usd is not None:
        budget.report_record(org, result.cost_usd)
    return {"result": asdict(result)}


def _completion_process(sender, org_id, request, database_name):
    """Spawn entry point: independent DB/SDK state; no secrets cross the pipe."""
    try:
        import django
        from django.conf import settings
        settings.DATABASES["default"]["NAME"] = database_name
        django.setup()
        response = _complete(org_id, request)
        if len(json.dumps(response, allow_nan=False).encode()) > MAX_RESPONSE_BYTES:
            response = _error("invalid_response", "The AI response exceeds the size limit.")
    except ReportAIError as error:
        # Use our bounded vocabulary, never exception strings from an SDK.
        messages = {
            "disabled": "AI is disabled or not configured for this organization.",
            "budget": "The organization AI budget cap has been reached.",
            "invalid_request": "Invalid AI completion request.",
            "invalid_response": "The AI provider returned an invalid response.",
            "timeout": "The AI completion timed out.",
            "unavailable": "The saved AI connection is unavailable. Check AI settings.",
        }
        response = _error(error.code, messages.get(error.code, "The AI provider could not complete the request."))
        logger.info("Report AI completion failed: %s", error.code)
    except Exception:  # SDK/DB internals must never reach tenant code or logs.
        response = _error("provider", "The AI provider could not complete the request.")
        logger.info("Report AI completion failed: provider")
    try:
        sender.send(response)
    finally:
        sender.close()
        from django.db import connections
        connections.close_all()


class AIBroker:
    """One authenticated request at a time, with no per-build call count cap."""

    def __init__(self, run, directory):
        self.org_id = run.studio.org_id
        self.run_id = run.pk
        self.requested_by_id = run.requested_by_id  # diagnostic context only
        self.path = str(Path(directory) / "ai.sock")
        self._token = secrets.token_urlsafe(32)
        self._stop = threading.Event()
        self._listener = None
        self._thread = None
        self._active = None
        self._connection = None
        self._handler = None
        self._busy = threading.Event()

    @property
    def child_env(self):
        return {"TRELLUM_AI_SOCKET": self.path, "TRELLUM_AI_TOKEN": self._token}

    def start(self):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        try:
            with unix_socket_address(self.path) as address:
                listener.bind(address)
            # Only this run's directory is mounted; UID 10001 must connect.
            os.chmod(self.path, 0o666)
            listener.listen(1)
            listener.settimeout(0.2)
            self._thread = threading.Thread(target=self._serve, daemon=True, name="report-ai")
            self._thread.start()
        except Exception:
            self.close()
            raise
        return self

    def close(self):
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._thread is not None and self._thread.ident is not None and self._thread is not threading.current_thread():
            self._thread.join(2)
        if self._handler is not None and self._handler.ident is not None and self._handler is not threading.current_thread():
            self._handler.join(2)
        try:
            # The report can replace this entry in its writable mount. Never
            # follow it or recursively remove a replacement directory.
            Path(self.path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Report AI socket cleanup failed")

    def _serve(self):
        while not self._stop.is_set():
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if self._busy.is_set():
                with connection:
                    try:
                        # Closing with unread request bytes can reset the socket
                        # before the client receives the rejection. Drain one
                        # bounded frame without dispatching another completion.
                        deadline = time.monotonic() + 0.2
                        remaining = MAX_REQUEST_BYTES + 1
                        while remaining:
                            timeout = deadline - time.monotonic()
                            if timeout <= 0:
                                raise TimeoutError
                            connection.settimeout(timeout)
                            part = connection.recv(min(65536, remaining))
                            remaining -= len(part)
                            if not part or not remaining:
                                raise ValueError
                            if b"\n" in part:
                                break
                        connection.settimeout(0.2)
                        connection.sendall(json.dumps(_error("invalid_request", "An AI completion is already in progress for this build.")).encode() + b"\n")
                    except (OSError, ValueError):
                        pass
                continue
            self._busy.set()
            self._connection = connection
            self._handler = threading.Thread(target=self._handle, args=(connection,), daemon=True)
            self._handler.start()

    def _handle(self, connection):
        with connection:
            connection.settimeout(2)
            try:
                response = self._request(connection)
            except Exception:
                logger.info("Report AI completion failed: provider")
                response = _error("provider", "The AI provider could not complete the request.")
            self._busy.clear()
            try:
                wire = json.dumps(response, allow_nan=False).encode() + b"\n"
                if len(wire) > MAX_RESPONSE_BYTES:
                    wire = json.dumps(_error("invalid_response", "AI response exceeds the size limit.")).encode() + b"\n"
                connection.sendall(wire)
            except (OSError, ValueError, TypeError):
                pass
        if self._connection is connection:
            self._connection = None
            self._busy.clear()

    def _request(self, connection):
        payload = bytearray()
        deadline = time.monotonic() + 2
        while b"\n" not in payload and len(payload) <= MAX_REQUEST_BYTES:
            if time.monotonic() >= deadline:
                return _error("invalid_request", "Invalid AI completion request.")
            part = connection.recv(min(65536, MAX_REQUEST_BYTES + 1 - len(payload)))
            if not part:
                break
            payload.extend(part)
        try:
            if len(payload) > MAX_REQUEST_BYTES:
                raise ValueError
            request = json.loads(payload)
            if not isinstance(request, dict):
                raise ValueError
            token = request.pop("token", None)
            if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
                return _error("unavailable", "AI completion is unavailable.")
            # Validate before process creation; the helper repeats validation.
            from trellum.ai import validate_request
            request = validate_request(**request)
        except (TypeError, ValueError, RecursionError, ReportAIError):
            return _error("invalid_request", "Invalid AI completion request.")
        return self._dispatch(request)

    def _dispatch(self, request):
        # Spawn, rather than fork a threaded Django worker with live DB sockets.
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        from django.db import connection
        process = context.Process(target=_completion_process, args=(sender, self.org_id, request, connection.settings_dict["NAME"]), daemon=True)
        deadline = time.monotonic() + request["timeout"]
        try:
            process.start()
            self._active = process
            sender.close()
            while not self._stop.is_set() and time.monotonic() < deadline:
                if receiver.poll(min(0.1, max(0, deadline - time.monotonic()))):
                    try:
                        return receiver.recv()
                    except EOFError:
                        return _error("provider", "The AI provider could not complete the request.")
                if not process.is_alive():
                    return _error("provider", "The AI provider could not complete the request.")
            return _error("timeout", "The AI completion timed out.")
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(0.5)
                if process.is_alive():
                    process.kill()
                    process.join()
                process.close()
            self._active = None
            receiver.close()
            sender.close()
