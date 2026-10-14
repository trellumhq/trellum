"""Bounded, terminable connection probes.

The probe runs in a child interpreter owned by the portal. On the deadline the
child (and, on POSIX, its whole process group, since drivers can spawn SSH
tunnels or helpers) is killed and reaped before the caller gets its answer, so
a reported timeout never leaves a probe running. Credentials travel only over
the child's stdin pipe -- never argv, environment additions, files or logs --
and the child never touches the portal database.

Concurrency is bounded per binding and per studio/organization (per web
process). A refused admission raises :class:`ProbeBusy`; it is not a
connection outcome and callers must not store it as check evidence.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

#: Parent-side cap on what is read back from the child.
RESULT_MAX_BYTES = 16 * 1024
#: Time allowed to kill and reap after the deadline.
KILL_GRACE_SECONDS = 1.0
MAX_PER_BINDING = 2
MAX_PER_SCOPE = 4
ADMISSION_WAIT_SECONDS = 2.0

_CHILD = Path(__file__).with_name("probe_child.py")
_cond = threading.Condition()
_active_binding: dict = {}
_active_scope: dict = {}


class ProbeBusy(Exception):
    """Too many probes already running for this binding or studio."""


class ProbeResult:
    __slots__ = ("ok", "detail", "timed_out", "elapsed", "cleanup_seconds")

    def __init__(self, ok, detail, timed_out=False, elapsed=0.0, cleanup_seconds=0.0):
        self.ok, self.detail, self.timed_out = ok, detail, timed_out
        self.elapsed, self.cleanup_seconds = elapsed, cleanup_seconds


@contextlib.contextmanager
def admission(binding_key, scope_key, wait=ADMISSION_WAIT_SECONDS):
    end = time.monotonic() + wait
    with _cond:
        while (_active_binding.get(binding_key, 0) >= MAX_PER_BINDING
               or _active_scope.get(scope_key, 0) >= MAX_PER_SCOPE):
            left = end - time.monotonic()
            if left <= 0:
                raise ProbeBusy("Another connection test for this source is still running. Try again in a moment.")
            _cond.wait(left)
        _active_binding[binding_key] = _active_binding.get(binding_key, 0) + 1
        _active_scope[scope_key] = _active_scope.get(scope_key, 0) + 1
    try:
        yield
    finally:
        with _cond:
            for table, key in ((_active_binding, binding_key), (_active_scope, scope_key)):
                table[key] -= 1
                if table[key] <= 0:
                    del table[key]
            _cond.notify_all()


def _terminate(proc: subprocess.Popen) -> None:
    """Kill the child (group on POSIX) and reap it within the grace period."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(OSError):
            proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(KILL_GRACE_SECONDS)


def _read_bounded(stream, sink: list) -> None:
    try:
        sink.append(stream.read(RESULT_MAX_BYTES + 1))
    except (OSError, ValueError):
        sink.append(b"")


def run_probe(kind: str, info: dict, *, timeout: float, argv=None) -> ProbeResult:
    """Run one probe. Always returns; never leaves the child running."""
    started = time.monotonic()
    payload = json.dumps({"type": kind, "info": info, "socket_timeout": max(timeout - 1, 1)}).encode()
    cmd = argv or [sys.executable, "-I", str(_CHILD)]
    kwargs = {"start_new_session": True} if os.name == "posix" else {}
    if os.name != "posix":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, **kwargs,
        )
    except OSError as exc:
        return ProbeResult(False, f"Could not start the connection test ({type(exc).__name__}).")
    sink: list = []
    oversized = False
    reader = threading.Thread(target=_read_bounded, args=(proc.stdout, sink), daemon=True)
    reader.start()
    try:
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        reader.join(max(timeout - (time.monotonic() - started), 0))
        finished = not reader.is_alive()
        oversized = finished and bool(sink) and len(sink[0]) > RESULT_MAX_BYTES
        if finished and not oversized:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(max(timeout - (time.monotonic() - started), 0.05))
    finally:
        deadline_hit = not oversized and (proc.poll() is None or reader.is_alive())
        cleanup_start = time.monotonic()
        # Even after a clean exit, descendants in the group must not outlive us.
        _terminate(proc)
        reader.join(KILL_GRACE_SECONDS)
        cleanup = time.monotonic() - cleanup_start
    elapsed = time.monotonic() - started
    if deadline_hit:
        return ProbeResult(False, "", timed_out=True, elapsed=elapsed, cleanup_seconds=cleanup)
    raw = sink[0] if sink else b""
    if len(raw) > RESULT_MAX_BYTES:
        return ProbeResult(False, "The connection test returned an oversized answer.", elapsed=elapsed, cleanup_seconds=cleanup)
    try:
        data = json.loads(raw.decode("utf-8", "replace").strip().splitlines()[-1])
        return ProbeResult(bool(data["ok"]), str(data["detail"]), elapsed=elapsed, cleanup_seconds=cleanup)
    except (ValueError, KeyError, IndexError, TypeError):
        return ProbeResult(False, "The connection test ended without an answer.", elapsed=elapsed, cleanup_seconds=cleanup)
