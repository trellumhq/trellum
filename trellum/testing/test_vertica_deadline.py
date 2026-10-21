"""Vertica deadlines, without a database or external network."""
from __future__ import annotations

import socket
import struct
import threading
import time
from unittest.mock import Mock

import pytest

from trellum.data import vertica_deadline as vd
from trellum.data.drivers.vertica import VerticaDriver
from trellum.data.fetch import execute_query


class Cancelled(Exception):
    sqlstate = "57014"


class Raw:
    def __init__(self, execute=lambda: None, fetch=lambda: [(1, "a"), (1, "a")]):
        self.socket = Mock(family=socket.AF_INET)
        self.socket.getpeername.return_value = ("127.0.0.1", 5433)
        self.socket_as_file = None
        self.backend_pid, self.backend_key = 123, 456
        self._trellum_query_timeout_seconds = 0.02
        self.close = Mock()
        self.cur = Mock(description=[("id",), ("name",)])
        self.cur.execute.side_effect = lambda sql: execute()
        self.cur.fetchall.side_effect = fetch
        self.cur.fetchmany = None

    def cursor(self):
        return self.cur


def no_watchers():
    assert not [t for t in threading.enumerate() if t.name == "vertica-query-deadline"]


@pytest.mark.parametrize("value", [0, -1, None, True, False, "120", float("inf"), float("nan"), 3601, 10**1000])
def test_invalid_options(value):
    with pytest.raises(ValueError, match="query_timeout_seconds"):
        VerticaDriver().connect({"query_timeout_seconds": value})


@pytest.mark.parametrize("value", [0.01, 120, 3600])
def test_valid_options(value):
    assert vd.query_timeout_seconds(value) == value


def test_driver_connect_and_established_socket_budgets(monkeypatch):
    raw = Raw()
    connect = Mock(return_value=raw)
    monkeypatch.setattr("vertica_python.connect", connect)
    assert VerticaDriver().connect(dict(host="db", user="u", password="p")) is raw
    assert connect.call_args.kwargs["connection_timeout"] == 10
    raw.socket.settimeout.assert_called_once_with(125)
    assert raw._trellum_query_timeout_seconds == 120


@pytest.mark.heavy
def test_healthy_no_late_cancel_and_fresh_deadline(monkeypatch):
    cancel = Mock()
    monkeypatch.setattr(vd, "_send_cancel", cancel)
    raw = Raw()
    for _ in range(3):
        frame = execute_query(raw, "select 1", compact_text=True)
        assert str(frame["name"].dtype) == "category"
        time.sleep(0.03)
    cancel.assert_not_called()
    raw.close.assert_not_called()
    no_watchers()


@pytest.mark.heavy
@pytest.mark.parametrize("phase", ["execute", "fetch"])
def test_stall_ack_disposes_and_new_connection_gets_fresh_budget(monkeypatch, phase):
    ack = threading.Event()

    def blocked():
        assert ack.wait(1)
        raise Cancelled("server confirmed")

    raw = Raw(**{phase: blocked})
    original = raw.socket
    cancel = Mock(side_effect=lambda *args: ack.set())
    monkeypatch.setattr(vd, "_send_cancel", cancel)
    with pytest.raises(vd.VerticaQueryTimeout) as caught:
        execute_query(raw, "select 1")
    assert caught.value.cancellation_confirmed is True
    assert isinstance(caught.value.__cause__, Cancelled)
    assert cancel.call_args.args[:4] == (socket.AF_INET, ("127.0.0.1", 5433), 123, 456)
    original.shutdown.assert_called()
    original.close.assert_called_once()
    raw.cur.close.assert_not_called()
    assert raw.socket is None
    with pytest.raises(vd.VerticaQueryTimeout):
        execute_query(raw, "select 2")
    fresh = Raw()
    assert len(execute_query(fresh, "select 3")) == 2
    assert cancel.call_count == 1
    no_watchers()


@pytest.mark.heavy
def test_completed_at_deadline_is_confirmed(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(vd, "_send_cancel", lambda *args: release.set())
    raw = Raw(execute=lambda: release.wait(1))
    with pytest.raises(vd.VerticaQueryTimeout) as caught:
        execute_query(raw, "select 1")
    assert caught.value.cancellation_confirmed
    no_watchers()


def test_manual_cancel_and_other_errors_unchanged(monkeypatch):
    cancel = Mock()
    monkeypatch.setattr(vd, "_send_cancel", cancel)
    for error in [Cancelled("manual"), ValueError("query"), KeyboardInterrupt()]:
        def fail():
            raise error
        raw = Raw(execute=fail)
        raw._trellum_query_timeout_seconds = 30
        raw.cur.close.side_effect = RuntimeError("cleanup")
        with pytest.raises(type(error)) as caught:
            execute_query(raw, "select 1")
        assert caught.value is error
        raw.cur.close.assert_called_once()
        no_watchers()
    cancel.assert_not_called()


@pytest.mark.heavy
@pytest.mark.parametrize("transport_fails", [False, True])
def test_real_socket_blocked_read_wakes_and_no_watcher(monkeypatch, transport_fails):
    # Both the original session and CancelRequest use real loopback TCP sockets.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(1)
    client = socket.create_connection(listener.getsockname(), timeout=1)
    server, _ = listener.accept()
    client.settimeout(2)
    def blocked_read():
        if not raw.socket_as_file.read(1):
            raise EOFError("connection closed")

    raw = Raw(execute=blocked_read)
    raw.socket = client
    raw.socket_as_file = client.makefile("rb")
    monkeypatch.setattr(vd, "CANCEL_GRACE_SECONDS", 0.08)
    if transport_fails:
        monkeypatch.setattr(vd, "_send_cancel", Mock(side_effect=TimeoutError()))
    try:
        started = time.monotonic()
        with pytest.raises(vd.VerticaQueryTimeout) as caught:
            execute_query(raw, "select 1")
        assert caught.value.cancellation_confirmed is False
        # EOF after local shutdown is not successful DB operation completion.
        assert time.monotonic() - started < 1
        if not transport_fails:
            transport, _ = listener.accept()
            with transport:
                transport.settimeout(1)
                assert transport.recv(16) == struct.pack("!4I", 16, 80877102, 123, 456)
        no_watchers()
        assert raw.socket is None
    finally:
        client.close()
        server.close()
        listener.close()


def test_tunnel_cleanup_preserved_after_discard():
    from trellum.data.ssh_tunnel import TunnelledConnection
    raw = Raw()
    tunnel = Mock()
    wrapped = TunnelledConnection(raw, tunnel)
    vd._discard(raw, raw.socket)
    wrapped.close()
    tunnel.close.assert_called_once()


@pytest.mark.heavy
def test_compact_fetch_batches_share_execute_deadline(monkeypatch):
    ack = threading.Event()
    raw = Raw()
    batches = iter([[(1, "a"), (1, "a")]])

    def fetchmany(size):
        batch = next(batches, None)
        if batch is not None:
            return batch
        assert ack.wait(1)
        raise Cancelled("fetch cancelled")

    raw.cur.fetchmany = fetchmany
    monkeypatch.setattr(vd, "_send_cancel", lambda *args: ack.set())
    with pytest.raises(vd.VerticaQueryTimeout) as caught:
        execute_query(raw, "select 1", compact_text=True)
    assert caught.value.cancellation_confirmed
    no_watchers()


@pytest.mark.heavy
def test_manual_cancellation_before_deadline_survives_slow_cleanup(monkeypatch):
    cancel = Mock()
    monkeypatch.setattr(vd, "_send_cancel", cancel)
    error = Cancelled("user cancelled")

    def fail():
        raise error

    raw = Raw(execute=fail)
    raw.cur.close.side_effect = lambda: time.sleep(0.04)
    with pytest.raises(Cancelled) as caught:
        execute_query(raw, "select 1")
    assert caught.value is error
    cancel.assert_not_called()
    no_watchers()
