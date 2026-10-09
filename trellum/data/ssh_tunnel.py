"""SSH port forwarding for host/port data sources.

A source whose ``conn_info`` names an ``ssh_host`` reaches its database
through that bastion -- an in-process ``ssh -L``. The driver connects to a
port on 127.0.0.1, and every byte is carried over an SSH ``direct-tcpip``
channel to the database's own host and port, which only the bastion can
reach. ``drivers.connect`` is the sole caller; nothing here knows about
drivers.

The contract, as conn_info keys (and their ``.env`` suffixes)::

    ssh_host         SSH_HOST         the bastion; its presence turns the tunnel on
    ssh_port         SSH_PORT         default 22
    ssh_user         SSH_USER
    ssh_key_path     SSH_KEY_PATH     a private key file (local use)
    ssh_private_key  SSH_PRIVATE_KEY  the private key's text (what a portal stores)
    ssh_password     SSH_PASSWORD     also the private key's passphrase, if it has one
    ssh_host_key     SSH_HOST_KEY     the bastion's public host key, as
                                      ``ssh-keyscan`` prints it

With ``ssh_host_key`` set, a bastion presenting any other key is refused
before a password or key signature is sent to it. Without it, whatever key
the bastion presents is accepted, and a warning says so.

paramiko is an optional dependency (requirements-drivers.txt), imported
only when a tunnel is opened.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import select
import socketserver
import sys
import threading
from typing import Any

logger = logging.getLogger(__name__)


def _private_key(text: str, password: str | None = None):
    """A paramiko key from the text of a private key file, as ssh-keygen
    writes it (OpenSSH format, or PKCS#1 PEM with ``-m PEM``)."""
    import paramiko

    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=password)
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError(
        "ssh_private_key must be an OpenSSH-format (ssh-keygen) or PKCS#1 PEM private "
        "key; if it has a passphrase, put it in ssh_password"
    )


def _public_key(line: str):
    """A paramiko key from a host-key line: ``[host] type base64 [comment]``."""
    import paramiko

    fields = line.split()
    try:
        i = next(
            i for i, f in enumerate(fields)
            if f in ("ssh-ed25519", "ssh-rsa") or f.startswith("ecdsa-sha2-")
        )
        return paramiko.PKey.from_type_string(fields[i], base64.b64decode(fields[i + 1]))
    except Exception as exc:  # noqa: BLE001 -- every malformed line is the same mistake
        raise ValueError(
            "ssh_host_key must be a public host key line, as `ssh-keyscan` prints it"
        ) from exc


class _Handler(socketserver.BaseRequestHandler):
    """One accepted local connection <-> one direct-tcpip channel."""

    def handle(self) -> None:
        chan = self.server.transport.open_channel(
            "direct-tcpip", self.server.remote, self.request.getpeername()
        )
        sources = [self.request, chan]
        try:
            while chan in sources:
                readable, _, _ = select.select(sources, [], [])
                if self.request in readable:
                    data = self.request.recv(32768)
                    if data:
                        chan.sendall(data)
                    else:  # the driver is done sending; its reply may still be on the way
                        chan.shutdown_write()
                        sources.remove(self.request)
                if chan in readable:
                    data = chan.recv(32768)
                    if data:
                        self.request.sendall(data)
                    else:
                        sources.remove(chan)
        finally:
            chan.close()


class _Forwarder(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, transport, remote: tuple[str, int]):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.transport, self.remote = transport, remote

    def handle_error(self, request, client_address) -> None:  # noqa: ARG002
        # The driver only ever sees its socket close; the reason is here.
        logger.warning(
            "SSH tunnel: forwarding to %s:%s failed: %s", *self.remote, sys.exc_info()[1]
        )


class SSHTunnel:
    """A running forward: ``127.0.0.1:local_port`` -> *remote*, via the
    bastion *conn_info* describes. ``close()`` tears it down."""

    def __init__(self, conn_info: dict, remote: tuple[str, int]):
        import paramiko

        host, port = conn_info["ssh_host"], int(conn_info.get("ssh_port") or 22)
        password = conn_info.get("ssh_password") or None
        pkey = _private_key(conn_info["ssh_private_key"], password) if conn_info.get("ssh_private_key") else None
        self._client = client = paramiko.SSHClient()
        if conn_info.get("ssh_host_key"):
            key = _public_key(conn_info["ssh_host_key"])
            client.get_host_keys().add(host if port == 22 else f"[{host}]:{port}", key.get_name(), key)
            # RejectPolicy, the default: any other key fails before authentication.
        else:
            logger.warning("SSH tunnel to %s: host key not verified; set ssh_host_key to pin it", host)
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                host, port, username=conn_info.get("ssh_user"), password=password, pkey=pkey,
                key_filename=os.path.expanduser(conn_info["ssh_key_path"]) if conn_info.get("ssh_key_path") else None,
                allow_agent=False, look_for_keys=False, timeout=30,
            )
        except Exception as exc:
            client.close()
            raise ConnectionError(f"SSH tunnel to {host}:{port} could not be opened: {exc}") from exc
        self._server = _Forwarder(client.get_transport(), remote)
        self.local_port: int = self._server.server_address[1]
        threading.Thread(
            target=self._server.serve_forever, name=f"ssh-tunnel:{host}", daemon=True
        ).start()

    def close(self) -> None:
        self._client.close()  # transport down: every channel EOFs, handlers return
        self._server.shutdown()
        self._server.server_close()


class TunnelledConnection:
    """A driver connection plus the tunnel it runs through: closing one closes
    both. Everything else is delegated, so callers see the driver's own
    object; :func:`unwrap` is for code that sniffs the connection's type."""

    def __init__(self, inner: Any, tunnel: SSHTunnel):
        self._inner, self._tunnel = inner, tunnel

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._tunnel.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._inner.__exit__(*exc)
        finally:
            # Some drivers close on exit (pymysql, vertica), others only end the
            # transaction (psycopg2); the tunnel follows the connection either way.
            closed = getattr(self._inner, "closed", None)
            if (closed() if callable(closed) else closed) or getattr(self._inner, "open", True) is False:
                self._tunnel.close()


def unwrap(conn: Any) -> Any:
    """The driver's own connection object, managed or tunnelled or not."""
    from trellum.data.retry import ManagedConnection

    while isinstance(conn, (ManagedConnection, TunnelledConnection)):
        conn = conn._inner
    return conn
