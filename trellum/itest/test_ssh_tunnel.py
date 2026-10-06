"""What the contract tests cannot say about the postgres_ssh lane: that a
pinned ``ssh_host_key`` is honoured both ways, and that closing the
connection takes the tunnel down with it. Collected only when
``postgres_ssh`` is selected (conftest.py's _CONDITIONAL_TESTS)."""

from __future__ import annotations

import socket

import pytest
from engines import ENGINES


def _bastion_host_key_line() -> str:
    """The bastion's real host key, as ssh-keyscan would print it."""
    import paramiko

    info = ENGINES["postgres_ssh"].conn_info()
    transport = paramiko.Transport((info["ssh_host"], info["ssh_port"]))
    try:
        transport.start_client()
        key = transport.get_remote_server_key()
    finally:
        transport.close()
    return f"{key.get_name()} {key.get_base64()}"


def _connect(**overrides):
    from trellum.data.drivers import connect

    return connect("postgres", {**ENGINES["postgres_ssh"].conn_info(), **overrides})


def test_pinned_host_key_connects():
    conn = _connect(ssh_host_key=_bastion_host_key_line())
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_wrong_host_key_is_refused():
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519

    impostor = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH,
    ).decode()
    with pytest.raises(ConnectionError, match="SSH tunnel to"):
        _connect(ssh_host_key=impostor)


def test_close_stops_the_tunnel():
    conn = _connect()
    port = conn._tunnel.local_port
    conn.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
