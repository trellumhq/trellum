"""Versioned, bounded build evidence. No exception messages or SQL are emitted.

Hosts opt in with TRELLUM_DIAGNOSTICS_PATH. This is progress evidence, never
permission to replay arbitrary report Python or proof that a process stopped.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

MAX_BYTES = 16 * 1024
STAGES = frozenset({"connect", "execute", "fetch", "render", "publish"})
SOURCES = frozenset({"sqlite", "duckdb", "vertica", "postgres", "redshift", "mysql",
                     "sqlserver", "snowflake", "clickhouse", "bigquery", "trino", "databricks"})
_context = ContextVar("trellum_diagnostics", default={})
_lock = threading.RLock()
_path = None
_snapshot = {}
_timings = []
_failure = None
_retry_history = []
_retry_count = 0


def fingerprint(value: str) -> str:
    """An opaque identifier; callers pass unbound SQL, never SQL parameters."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _number(value, maximum):
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return min(maximum, max(0, value))
    return 0


def sanitize_diagnostics(value) -> dict:
    """Allowlist and bound an untrusted v1 snapshot; reject unknown versions."""
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != 1
            or not isinstance(value.get("stage"), str) or value.get("stage") not in STAGES):
        return {}
    state = value.get("state")
    if not isinstance(state, str) or state not in {"active", "completed", "failed"}:
        return {}
    result = {"version": 1, "stage": value["stage"], "state": state,
              "attempt": int(_number(value.get("attempt", 1), 3)) or 1,
              "transient": value.get("transient") is True,
              "query_retry_safe": value.get("query_retry_safe") is True,
              "unsafe_operation": value.get("unsafe_operation") is True,
              "cancellation_unconfirmed": value.get("cancellation_unconfirmed") is True,
              "query_timeout": value.get("query_timeout") is True,
              "stage_started_at": _number(value.get("stage_started_at"), 4_102_444_800)}
    result["source_type"] = value.get("source_type") if isinstance(value.get("source_type"), str) and value.get("source_type") in SOURCES else ""
    for key in ("source_id", "query_id"):
        item = value.get(key)
        result[key] = item if isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) else ""
    result["errors"] = []
    errors = value.get("errors", [])
    for error in errors[:5] if isinstance(errors, list) else []:
        if not isinstance(error, dict):
            continue
        name, code = error.get("class"), error.get("code")
        item = {"class": name if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name) else "UnknownError"}
        if isinstance(code, str) and re.fullmatch(r"(?:[0-9A-Z]{5}|[0-9]{1,9})", code):
            item["code"] = code
        item["frames"] = []
        frames = error.get("frames", [])
        for frame in frames[-3:] if isinstance(frames, list) else []:
            if (isinstance(frame, dict) and isinstance(frame.get("location_id"), str)
                    and re.fullmatch(r"[a-f0-9]{64}", frame["location_id"])):
                item["frames"].append({"location_id": frame["location_id"],
                                       "line": int(_number(frame.get("line"), 10_000_000))})
        result["errors"].append(item)
    result["timings"] = []
    timings = value.get("timings", [])
    for item in timings[-16:] if isinstance(timings, list) else []:
        if isinstance(item, dict) and isinstance(item.get("stage"), str) and item.get("stage") in STAGES:
            result["timings"].append({"stage": item["stage"], "elapsed_ms": int(_number(item.get("elapsed_ms"), 14_400_000))})
    result["retry_count"] = int(_number(value.get("retry_count", 0), 255))
    result["retry_history"] = []
    retries = value.get("retry_history", [])
    for item in retries[-8:] if isinstance(retries, list) else []:
        if not isinstance(item, dict) or item.get("reason") not in ("transient_connection", "query_timeout"):
            continue
        name = item.get("class")
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            result["retry_history"].append({
                "attempt": int(_number(item.get("attempt", 1), 3)) or 1,
                "reason": item["reason"], "class": name,
            })
    return result


def read_diagnostics(path) -> dict:
    """Read at most 16 KiB, tolerating missing, corrupt, or newer evidence."""
    try:
        # Report Python owns this path: a FIFO must not block the worker tick,
        # and a symlink must not redirect evidence reads outside the run area.
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
                return {}
            payload = stream.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            return {}
        return sanitize_diagnostics(json.loads(payload))
    except (OSError, ValueError, TypeError, RecursionError):
        return {}


def _write(**updates):
    global _path, _snapshot, _timings, _failure, _retry_history, _retry_count
    path = os.environ.get("TRELLUM_DIAGNOSTICS_PATH")
    if not path:
        return
    with _lock:
        if path != _path:
            _path, _snapshot, _timings, _failure, _retry_history, _retry_count = path, {}, [], None, [], 0
        unsafe = _snapshot.get("unsafe_operation", False) or updates.get("unsafe_operation", False)
        unconfirmed = _snapshot.get("cancellation_unconfirmed", False) or updates.get("cancellation_unconfirmed", False)
        _snapshot.update(updates)
        _snapshot["unsafe_operation"] = unsafe
        _snapshot["cancellation_unconfirmed"] = unconfirmed
        _snapshot.update(version=1, timings=_timings[-16:])
        payload = json.dumps(sanitize_diagnostics(_snapshot), separators=(",", ":")).encode()
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".diagnostics-", dir=Path(path).parent)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        except OSError:
            pass  # Optional evidence must never replace the original outcome.
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


@contextmanager
def diagnostic_context(**metadata):
    """Attach safe metadata to nested stages; identifiers must already be hashed."""
    token = _context.set({**_context.get(), **metadata})
    try:
        yield
    finally:
        _context.reset(token)


def record_failure(exc, *, source_type=None, query_retry_safe=None, attempt=None):
    """Record classified causes, preserving the innermost failing stage."""
    global _failure
    from trellum.data.retry_errors import _exception_chain, is_retryable_connection_error
    from trellum.data.vertica_deadline import VerticaQueryTimeout
    with _lock:
        metadata = dict(_context.get())
        if source_type is not None:
            metadata["source_type"] = source_type
        if query_retry_safe is not None:
            metadata["query_retry_safe"] = query_retry_safe
        if attempt is not None:
            metadata["attempt"] = attempt
        deadline = isinstance(exc, VerticaQueryTimeout)
        metadata["query_timeout"] = deadline
        if deadline and not exc.cancellation_confirmed:
            metadata["cancellation_unconfirmed"] = True
        errors = []
        # A locally requested, acknowledged cancel is a timeout outcome, not
        # an unsolicited permanent SQLSTATE 57014 failure in the host policy.
        for current in ((exc,) if deadline else _exception_chain(exc)):
            item = {"class": type(current).__name__}
            for key in ("sqlstate", "pgcode", "errno", "errorcode", "code"):
                try:
                    code = getattr(current, key, None)
                except Exception:
                    continue
                if type(code) in (str, int) and re.fullmatch(r"(?:[0-9A-Z]{5}|[0-9]{1,9})", str(code)):
                    item["code"] = str(code)
                    break
            frames = []
            traceback = current.__traceback__
            while traceback is not None:
                code = traceback.tb_frame.f_code
                frames.append({"location_id": fingerprint(code.co_filename + "\x00" + code.co_name),
                               "line": traceback.tb_lineno})
                del frames[:-3]
                traceback = traceback.tb_next
            item["frames"] = frames
            errors.append(item)
            if len(errors) == 5:
                break
        _failure = id(exc)
        try:
            transient = is_retryable_connection_error(exc, metadata.get("source_type", ""))
        except Exception:
            transient = False
        _write(**metadata, state="failed", errors=errors, transient=transient)


def record_retry(exc, *, attempt):
    """Keep the reasons for retries even after the next stage clears its error."""
    global _retry_count
    from trellum.data.vertica_deadline import VerticaQueryTimeout
    with _lock:
        _retry_count = min(255, _retry_count + 1)
        reason = "query_timeout" if isinstance(exc, VerticaQueryTimeout) else "transient_connection"
        _retry_history.append({"attempt": attempt, "reason": reason,
                               "class": type(exc).__name__})
        del _retry_history[:-8]
        _write(retry_count=_retry_count, retry_history=_retry_history)


def progress(name, **metadata):
    """Persist entry for driver APIs whose result is owned by report code."""
    if name not in STAGES:
        raise ValueError("Unknown diagnostic stage")
    _write(**{**_context.get(), **metadata}, stage=name, state="active", stage_started_at=time.time(), transient=False, errors=[], query_timeout=False)


@contextmanager
def stage(name, **metadata):
    """Persist stage entry before blocking work so a hard kill leaves evidence."""
    global _failure
    if name not in STAGES:
        raise ValueError("Unknown diagnostic stage")
    with diagnostic_context(**metadata):
        started = time.monotonic()
        _failure = None
        _write(**_context.get(), stage=name, state="active", stage_started_at=time.time(), transient=False, errors=[], query_timeout=False)
        try:
            yield
        except BaseException as exc:
            with _lock:
                _timings.append({"stage": name, "elapsed_ms": int((time.monotonic() - started) * 1000)})
                del _timings[:-16]
            if _failure != id(exc):
                record_failure(exc)
            raise
        else:
            with _lock:
                _timings.append({"stage": name, "elapsed_ms": int((time.monotonic() - started) * 1000)})
                del _timings[:-16]
                # A nested stage is more precise than its enclosing boundary.
                _write(state="completed", transient=False, errors=[])


def instrument(name):
    """Instrument a complete callable while preserving its public signature."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with stage(name, query_retry_safe=False, source_type="", source_id="", query_id="", attempt=1):
                return function(*args, **kwargs)
        return wrapped
    return decorate
