"""Wall-clock bounds for Vertica execute and result consumption.

The worker stays on the calling thread. The watchdog only sends a cancellation
packet and shuts down the original socket; it is joined before leaving scope.
"""
from __future__ import annotations

import math
import socket
import threading
import time
from contextlib import contextmanager

DEFAULT_QUERY_TIMEOUT_SECONDS = 120.0
MAX_QUERY_TIMEOUT_SECONDS = 3600.0
CANCEL_GRACE_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 10


class VerticaQueryTimeout(TimeoutError):
    """Local deadline expired; confirmation is required before replay."""

    def __init__(self, cancellation_confirmed: bool):
        self.cancellation_confirmed = cancellation_confirmed
        outcome = "operation ended" if cancellation_confirmed else "database outcome unconfirmed"
        super().__init__(f"Vertica query deadline exceeded ({outcome})")


def query_timeout_seconds(value=DEFAULT_QUERY_TIMEOUT_SECONDS) -> float:
    """Accept numeric seconds only, finite and in (0, 3600]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("query_timeout_seconds must be a number in (0, 3600]")
    try:
        valid = math.isfinite(value) and 0 < value <= MAX_QUERY_TIMEOUT_SECONDS
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("query_timeout_seconds must be a number in (0, 3600]")
    return float(value)


def _discard(raw, original_socket) -> None:
    # close() writes Terminate through _socket(), which can RECONNECT after EOF.
    # Replacing that method also makes later SSH-wrapper cleanup safe.
    raw.close = lambda: None
    try:
        original_socket.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    original_socket.close()
    file = raw.socket_as_file
    raw.socket = None
    raw.socket_as_file = None
    if file is not None:
        file.close()


def _send_cancel(family, peer, pid, key, deadline) -> None:
    from vertica_python.vertica.messages.frontend_messages import CancelRequest

    # Numeric peer, original address family: no DNS, failover, or reconnect.
    with socket.socket(family, socket.SOCK_STREAM) as transport:
        transport.settimeout(max(0.001, deadline - time.monotonic()))
        transport.connect(peer)
        transport.settimeout(max(0.001, deadline - time.monotonic()))
        transport.sendall(CancelRequest(pid, key).get_message())


def deadline_expired(raw) -> bool:
    return getattr(raw, "__dict__", {}).get("_trellum_deadline_expired", False)


@contextmanager
def query_deadline(raw):
    budget = getattr(raw, "__dict__", {}).get("_trellum_query_timeout_seconds")
    if budget is None:
        yield
        return
    if deadline_expired(raw):
        raise VerticaQueryTimeout(False)
    original_socket = raw.socket
    # Capture before the operation: driver error handling may clear these.
    peer = original_socket.getpeername()
    family = original_socket.family
    pid, key = raw.backend_pid, raw.backend_key
    done = threading.Event()
    expired = threading.Event()
    end = time.monotonic() + budget

    def watch():
        if done.wait(max(0, end - time.monotonic())):
            return
        expired.set()
        raw._trellum_deadline_expired = True
        stop_by = end + CANCEL_GRACE_SECONDS
        try:
            _send_cancel(family, peer, pid, key, stop_by)
        except Exception:
            # Sending a packet is never evidence that the backend stopped.
            pass
        finally:
            done.wait(max(0, stop_by - time.monotonic()))
            try:
                original_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watcher = threading.Thread(target=watch, name="vertica-query-deadline")
    watcher.start()
    error = None
    completed = False
    try:
        yield
        completed = True
    except BaseException as exc:
        error = exc
        raise
    finally:
        # Account for completion after the budget even if the watchdog was
        # delayed by scheduling. A completed operation needs no cancel packet.
        if time.monotonic() >= end:
            expired.set()
            raw._trellum_deadline_expired = True
        done.set()
        watcher.join()
        if expired.is_set():
            # Only a server cancellation response or successful completion is
            # confirmation. EOF/socket failure alone leaves the outcome unknown.
            confirmed = completed or getattr(error, "sqlstate", None) == "57014"
            try:
                _discard(raw, original_socket)
            finally:
                # Preserve process/user interrupts even if they race the deadline.
                if error is None or isinstance(error, Exception):
                    raise VerticaQueryTimeout(confirmed) from error
