"""SSH tunnelling for host/port sources (trellum.data.ssh_tunnel, opened by
drivers.connect). No sockets to a bastion are ever opened here: paramiko's
SSHClient is replaced by a recorder, the tunnel by a stub. What is proven is
the contract -- which credentials reach paramiko, what the driver sees, and
that the tunnel's lifetime follows the connection's."""

import logging
import os
import socket
import threading
from unittest.mock import MagicMock, patch

import pytest

from trellum.data import ssh_tunnel
from trellum.data.drivers import connect, register_driver
from trellum.data.ssh_tunnel import SSHTunnel, TunnelledConnection, unwrap

paramiko = pytest.importorskip("paramiko")

# An ed25519 key pair generated once; the private half is what a portal
# stores inline, the public half doubles as a host-key line.
def _generate_key_text() -> str:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        ser.Encoding.PEM, ser.PrivateFormat.OpenSSH, ser.NoEncryption(),
    ).decode()


def _generate_key_text(fmt="OpenSSH", passphrase: bytes | None = None) -> str:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        ser.Encoding.PEM, getattr(ser.PrivateFormat, fmt),
        ser.BestAvailableEncryption(passphrase) if passphrase else ser.NoEncryption(),
    ).decode()


_PRIVATE_KEY_TEXT = _generate_key_text()
_PRIVATE_KEY = ssh_tunnel._private_key(_PRIVATE_KEY_TEXT)
_HOST_KEY_LINE = f"{_PRIVATE_KEY.get_name()} {_PRIVATE_KEY.get_base64()}"



# ── drivers.connect: the one place ───────────────────────────────────────


class _Driver:
    """Records what it was asked to connect with; returns a closable stub."""

    default_port = 5432

    def __init__(self, fail=False):
        self.seen = None
        self.fail = fail
        self.conn = MagicMock(name="driver-connection")

    def connect(self, conn_info):
        self.seen = dict(conn_info)
        if self.fail:
            raise RuntimeError("driver refused")
        return self.conn


class _Tunnel:
    """Stands in for SSHTunnel: records its arguments, hands out a port."""

    instances: list = []

    def __init__(self, conn_info, remote):
        self.conn_info, self.remote, self.closed = dict(conn_info), remote, False
        self.local_port = 54321
        _Tunnel.instances.append(self)

    def close(self):
        self.closed = True


@pytest.fixture
def stub_tunnel(monkeypatch):
    _Tunnel.instances = []
    monkeypatch.setattr(ssh_tunnel, "SSHTunnel", _Tunnel)
    return _Tunnel


class TestConnect:
    def test_no_ssh_host_is_a_plain_driver_connect(self, stub_tunnel):
        driver = _Driver()
        register_driver("t_plain", driver)
        info = {"host": "db.internal", "port": 5433, "user": "u", "password": "p"}
        assert connect("t_plain", info) is driver.conn  # not wrapped
        assert driver.seen == info
        assert stub_tunnel.instances == []

    def test_tunnel_rewrites_host_and_port_and_strips_ssh_keys(self, stub_tunnel):
        driver = _Driver()
        register_driver("t_ssh", driver)
        conn = connect("t_ssh", {
            "host": "db.internal", "port": 5433, "user": "u", "password": "p",
            "ssh_host": "bastion", "ssh_port": 2222, "ssh_user": "ops",
            "ssh_key_path": "/keys/id_ed25519",
        })
        (tunnel,) = stub_tunnel.instances
        assert tunnel.remote == ("db.internal", 5433)
        assert tunnel.conn_info["ssh_host"] == "bastion"
        assert driver.seen == {"host": "127.0.0.1", "port": 54321, "user": "u", "password": "p"}
        assert not any(k.startswith("ssh_") for k in driver.seen)
        assert isinstance(conn, TunnelledConnection)

    def test_remote_port_defaults_to_the_drivers_port(self, stub_tunnel):
        register_driver("t_ssh_default", _Driver())
        connect("t_ssh_default", {"host": "db", "user": "u", "password": "p", "ssh_host": "b"})
        assert stub_tunnel.instances[0].remote == ("db", 5432)

    def test_closing_the_connection_closes_the_tunnel(self, stub_tunnel):
        driver = _Driver()
        register_driver("t_ssh_close", driver)
        conn = connect("t_ssh_close", {"host": "db", "user": "u", "password": "p", "ssh_host": "b"})
        conn.close()
        driver.conn.close.assert_called_once_with()
        assert stub_tunnel.instances[0].closed is True

    def test_a_failing_driver_connect_closes_the_tunnel(self, stub_tunnel):
        register_driver("t_ssh_fail", _Driver(fail=True))
        with pytest.raises(RuntimeError, match="driver refused"):
            connect("t_ssh_fail", {"host": "db", "user": "u", "password": "p", "ssh_host": "b"})
        assert stub_tunnel.instances[0].closed is True

    def test_ssh_host_without_host_is_a_clear_error(self, stub_tunnel):
        register_driver("t_ssh_nohost", _Driver())
        with pytest.raises(ValueError, match="ssh_host needs host"):
            connect("t_ssh_nohost", {"user": "u", "password": "p", "ssh_host": "b"})
        assert stub_tunnel.instances == []

    def test_with_block_delegates_and_follows_an_inner_close(self):
        class ClosesOnExit:  # pymysql-style
            open = True

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.open = False

        class Transactional:  # psycopg2-style: exit ends the transaction, not the connection
            closed = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

        tunnel = _Tunnel({}, ("h", 1))
        with TunnelledConnection(ClosesOnExit(), tunnel) as conn:
            assert isinstance(conn, TunnelledConnection)
        assert tunnel.closed is True
        tunnel = _Tunnel({}, ("h", 1))
        with TunnelledConnection(Transactional(), tunnel):
            pass
        assert tunnel.closed is False

    def test_everything_else_is_delegated(self, stub_tunnel):
        driver = _Driver()
        register_driver("t_ssh_delegate", driver)
        conn = connect("t_ssh_delegate", {"host": "db", "user": "u", "password": "p", "ssh_host": "b"})
        conn.cursor().execute("SELECT 1")
        driver.conn.cursor.return_value.execute.assert_called_once_with("SELECT 1")
        assert unwrap(conn) is driver.conn


class TestQueryDispatchSeesThroughTheWrapper:
    def test_execute_query_dispatches_on_the_inner_connection(self):
        import pandas as pd

        from trellum.data.query import _execute_query

        client = MagicMock()
        client.__class__.__module__ = "clickhouse_connect.driver.client"
        client.__class__.__qualname__ = "Client"
        client.query_df.return_value = pd.DataFrame({"x": [1]})
        result = _execute_query(TunnelledConnection(client, _Tunnel({}, ("h", 1))), "SELECT 1")
        client.query_df.assert_called_once_with("SELECT 1")
        assert list(result["x"]) == [1]

    def test_sql_dialect_sees_the_inner_connection(self):
        from trellum.data.query import _sql_dialect

        dbx = MagicMock()
        dbx.__class__.__module__ = "databricks.sql.client"
        dbx.__class__.__qualname__ = "Connection"
        assert _sql_dialect(TunnelledConnection(dbx, _Tunnel({}, ("h", 1)))) == "googlesql"

    def test_unwrap_leaves_a_plain_connection_alone(self):
        conn = MagicMock()
        assert unwrap(conn) is conn


# ── The resolver knows the keys ──────────────────────────────────────────


class TestResolver:
    def test_env_suffixes_map_into_conn_info(self):
        from trellum.data.resolvers import LocalEnvResolver

        env = {
            "MYSRC_HOST": "db.internal", "MYSRC_USER": "u", "MYSRC_PASS": "p",
            "MYSRC_SSH_HOST": "bastion", "MYSRC_SSH_PORT": "2222", "MYSRC_SSH_USER": "ops",
            "MYSRC_SSH_PRIVATE_KEY": _PRIVATE_KEY_TEXT, "MYSRC_SSH_PASSWORD": "pw",
            "MYSRC_SSH_HOST_KEY": _HOST_KEY_LINE, "MYSRC_SSH_KEY_PATH": "/k",
        }
        with patch.dict(os.environ, env, clear=False):
            info = LocalEnvResolver().resolve({"local_env": "MYSRC"})
        assert info["ssh_host"] == "bastion"
        assert info["ssh_port"] == 2222  # int, like port
        assert info["ssh_user"] == "ops"
        assert info["ssh_private_key"] == _PRIVATE_KEY_TEXT
        assert info["ssh_password"] == "pw"
        assert info["ssh_host_key"] == _HOST_KEY_LINE
        assert info["ssh_key_path"] == "/k"

    def test_inline_ssh_fields_in_config_are_kept(self):
        from trellum.data.resolvers import LocalEnvResolver

        source = {
            "local_env": "MYSRC2", "type": "postgres", "host": "db",
            "ssh_host": "bastion", "ssh_user": "ops", "ssh_host_key": _HOST_KEY_LINE,
        }
        with patch.dict(os.environ, {"MYSRC2_USER": "u", "MYSRC2_PASS": "p"}, clear=False):
            info = LocalEnvResolver().resolve(source)
        assert (info["ssh_host"], info["ssh_user"], info["ssh_host_key"]) == (
            "bastion", "ops", _HOST_KEY_LINE,
        )


# ── SSHTunnel: what reaches paramiko ─────────────────────────────────────


class _Client:
    """A paramiko.SSHClient that records connect() and never touches the network."""

    instances: list = []

    def __init__(self):
        self.connect_kwargs = None
        self.policy = None
        self.host_keys = paramiko.HostKeys()
        self.closed = False
        _Client.instances.append(self)

    def get_host_keys(self):
        return self.host_keys

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, port, **kwargs):
        self.connect_kwargs = {"host": host, "port": port, **kwargs}

    def get_transport(self):
        return MagicMock(name="transport")

    def close(self):
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch):
    _Client.instances = []
    monkeypatch.setattr(paramiko, "SSHClient", _Client)
    return _Client


def _open(conn_info, remote=("db.internal", 5432)):
    tunnel = SSHTunnel(conn_info, remote)
    tunnel.close()
    return _Client.instances[-1]


class _Chan:
    """One end of a socketpair posing as a paramiko Channel."""

    def __init__(self, sock):
        self.sock = sock

    def fileno(self):
        return self.sock.fileno()

    def recv(self, n):
        return self.sock.recv(n)

    def sendall(self, data):
        self.sock.sendall(data)

    def shutdown_write(self):
        self.sock.shutdown(socket.SHUT_WR)

    def close(self):
        self.sock.close()


class _ReplyAfterEofTransport:
    """open_channel() hands out one end of a socketpair; the remote end waits
    for the sender's EOF before replying -- a shape only a half-close survives."""

    def open_channel(self, kind, remote, peer):
        ours, theirs = socket.socketpair()

        def remote_side():
            got = b""
            while chunk := theirs.recv(65536):
                got += chunk
            theirs.sendall(b"reply:" + got)
            theirs.close()

        threading.Thread(target=remote_side, daemon=True).start()
        return _Chan(ours)


class TestSSHTunnel:
    def test_a_reply_sent_after_the_drivers_eof_still_arrives(self, fake_client, monkeypatch):
        monkeypatch.setattr(_Client, "get_transport", lambda self: _ReplyAfterEofTransport())
        tunnel = SSHTunnel({"ssh_host": "b", "ssh_password": "pw"}, ("db", 1))
        try:
            client = socket.create_connection(("127.0.0.1", tunnel.local_port), timeout=5)
            client.sendall(b"query")
            client.shutdown(socket.SHUT_WR)
            got = b""
            while chunk := client.recv(65536):
                got += chunk
            client.close()
        finally:
            tunnel.close()
        assert got == b"reply:query"

    def test_key_path_auth(self, fake_client, caplog):
        with caplog.at_level(logging.WARNING, logger="trellum.data.ssh_tunnel"):
            client = _open({"ssh_host": "bastion", "ssh_user": "ops", "ssh_key_path": "/keys/id"})
        kw = client.connect_kwargs
        assert (kw["host"], kw["port"], kw["username"]) == ("bastion", 22, "ops")
        assert kw["key_filename"] == "/keys/id"
        assert _open({"ssh_host": "b", "ssh_key_path": "~/k"}).connect_kwargs["key_filename"] == os.path.expanduser("~/k")

        assert kw["pkey"] is None and kw["password"] is None
        assert kw["allow_agent"] is False and kw["look_for_keys"] is False
        assert kw["timeout"] == 30
        # No pin: accepted with a warning that says so.
        assert isinstance(client.policy, paramiko.AutoAddPolicy)
        assert "host key not verified" in caplog.text and "bastion" in caplog.text

    def test_inline_private_key_auth(self, fake_client):
        client = _open({
            "ssh_host": "bastion", "ssh_port": "2222", "ssh_user": "ops",
            "ssh_private_key": _PRIVATE_KEY_TEXT,
        })
        kw = client.connect_kwargs
        assert kw["port"] == 2222
        assert kw["pkey"].get_fingerprint() == _PRIVATE_KEY.get_fingerprint()
        assert kw["key_filename"] is None and kw["password"] is None

    def test_ssh_password_is_also_the_key_passphrase(self, fake_client):
        encrypted = _generate_key_text(passphrase=b"pp")
        with pytest.raises(ValueError, match="passphrase"):
            ssh_tunnel._private_key(encrypted)
        kw = _open({"ssh_host": "b", "ssh_private_key": encrypted, "ssh_password": "pp"}).connect_kwargs
        assert kw["pkey"].get_name() == "ssh-ed25519" and kw["password"] == "pp"

    def test_pkcs8_is_refused_with_the_accepted_formats_named(self):
        with pytest.raises(ValueError, match="OpenSSH-format .* or PKCS#1"):
            ssh_tunnel._private_key(_generate_key_text("PKCS8"))

    def test_host_key_line_with_a_hostname_starting_with_ssh(self):
        key = ssh_tunnel._public_key(f"ssh-gw.corp {_HOST_KEY_LINE}")
        assert key.get_fingerprint() == _PRIVATE_KEY.get_fingerprint()

    def test_password_auth(self, fake_client):
        kw = _open({"ssh_host": "bastion", "ssh_user": "ops", "ssh_password": "pw"}).connect_kwargs
        assert kw["password"] == "pw"
        assert kw["pkey"] is None and kw["key_filename"] is None

    def test_pinned_host_key_is_registered_and_others_rejected(self, fake_client, caplog):
        with caplog.at_level(logging.WARNING, logger="trellum.data.ssh_tunnel"):
            client = _open({
                "ssh_host": "bastion", "ssh_port": 2222, "ssh_user": "ops",
                "ssh_password": "pw", "ssh_host_key": f"[bastion]:2222 {_HOST_KEY_LINE}",
            })
        pinned = client.host_keys.lookup("[bastion]:2222")
        assert pinned is not None
        assert pinned["ssh-ed25519"].get_fingerprint() == _PRIVATE_KEY.get_fingerprint()
        assert client.policy is None  # paramiko's RejectPolicy stays: mismatch raises
        assert "not verified" not in caplog.text

    def test_the_driver_talks_to_the_local_port(self, fake_client):
        import socket

        tunnel = SSHTunnel({"ssh_host": "bastion", "ssh_user": "ops", "ssh_password": "pw"}, ("db", 1))
        try:
            assert 0 < tunnel.local_port < 65536
            socket.create_connection(("127.0.0.1", tunnel.local_port), timeout=2).close()
        finally:
            tunnel.close()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", tunnel.local_port), timeout=2).close()

    def test_failure_names_the_bastion_not_the_secret(self, fake_client, monkeypatch):
        def refuse(self, host, port, **kwargs):
            raise paramiko.AuthenticationException("Authentication failed.")

        monkeypatch.setattr(_Client, "connect", refuse)
        with pytest.raises(ConnectionError) as exc:
            SSHTunnel({"ssh_host": "bastion", "ssh_user": "ops", "ssh_password": "hunter2"}, ("db", 1))
        assert "SSH tunnel to bastion:22" in str(exc.value)
        assert "hunter2" not in str(exc.value)
        assert _Client.instances[-1].closed is True

    def test_malformed_keys_are_config_errors(self, fake_client):
        with pytest.raises(ValueError, match="ssh_host_key"):
            SSHTunnel({"ssh_host": "b", "ssh_password": "pw", "ssh_host_key": "nonsense"}, ("db", 1))
        with pytest.raises(ValueError, match="ssh_private_key"):
            SSHTunnel({"ssh_host": "b", "ssh_private_key": "not a key"}, ("db", 1))
