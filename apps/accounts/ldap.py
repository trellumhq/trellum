"""Directory (LDAP / Active Directory) authentication for an org's login.

The password typed at the login form is checked against the org's own
directory: a service bind finds the user's entry (search), then a second
bind *as that entry* with the typed password is the credential check.
Every connection is TLS: ``ldaps://`` from the start, ``ldap://`` upgraded
with StartTLS before anything else is sent. Verification is always on -- a
private CA is pasted into the config as PEM, there is no "skip" switch --
and referrals are never followed (ldap3 would otherwise re-bind with the
same credentials to whatever host the directory names, minus the CA).

Everything that touches the network goes through ``_open`` so tests point
it at ldap3's in-memory mock. Nothing here logs, audits or raises a message
that contains a password.
"""
from __future__ import annotations

import contextlib
import ssl

import ldap3
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPException,
    LDAPInvalidCredentialsResult,
)
from ldap3.utils.conv import escape_filter_chars

#: What ``auth.login`` / ``auth.sso_denied`` rows carry as ``provider``.
PROVIDER = "ldap"


class LDAPError(Exception):
    """The directory itself is the problem: unreachable, refused the service
    bind, bad search base. The message is safe to show and to audit."""


def _server(cfg) -> ldap3.Server:
    tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_data=cfg.ldap_ca_cert or None)
    return ldap3.Server(cfg.ldap_server_uri, tls=tls, get_info=ldap3.NONE, connect_timeout=5)


def _open(cfg, user: str, password: str) -> ldap3.Connection:
    """Connect and bind. The only function that opens a socket; a blank
    user binds anonymously."""
    server = _server(cfg)
    conn = ldap3.Connection(
        server, user=user or None, password=password or None,
        raise_exceptions=True, receive_timeout=10, auto_referrals=False,
    )
    conn.open()
    if not server.ssl:
        conn.start_tls()  # plain ldap://: no bind before the upgrade
    conn.bind()
    return conn


def _filter(cfg, login: str) -> str:
    return cfg.ldap_user_filter.replace("{login}", escape_filter_chars(login))


def _safe(exc: BaseException, *secrets: str) -> str:
    """ldap3 never echoes credentials in its messages; belt and braces."""
    text = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _one(value) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def _many(value) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)] if value else []


def _search(conn, cfg, search_filter: str, *, attributes=None, scope=ldap3.SUBTREE) -> list[dict]:
    conn.search(cfg.ldap_user_search_base, search_filter, search_scope=scope, attributes=attributes)
    return [r for r in conn.response or [] if r.get("type") == "searchResEntry"]


def authenticate(cfg, login: str, password: str) -> dict | None:
    """Check ``login`` / ``password`` against the org's directory.

    Returns ``{"dn", "email", "name", "groups"}`` for a verified user --
    ``groups`` are the DN strings from ``cfg.ldap_group_attr``. Returns
    ``None`` when the directory rejects the credentials: no entry, several
    entries, or a wrong password, deliberately indistinguishable to the
    caller. Raises :class:`LDAPError` when the directory is the problem.
    """
    if not password:
        return None  # an empty simple bind is an anonymous bind, and succeeds
    secrets = (password, cfg.ldap_bind_password)
    try:
        service = _open(cfg, cfg.ldap_bind_dn, cfg.ldap_bind_password)
    except LDAPException as exc:
        raise LDAPError(_safe(exc, *secrets)) from exc
    try:
        entries = _search(
            service, cfg, _filter(cfg, login),
            attributes=[cfg.ldap_email_attr, cfg.ldap_name_attr, cfg.ldap_group_attr],
        )
    except LDAPException as exc:
        raise LDAPError(_safe(exc, *secrets)) from exc
    finally:
        with contextlib.suppress(LDAPException):
            service.unbind()
    if len(entries) != 1:
        return None
    entry = entries[0]
    try:
        _open(cfg, entry["dn"], password).unbind()
    except (LDAPInvalidCredentialsResult, LDAPBindError):
        return None
    except LDAPException as exc:
        raise LDAPError(_safe(exc, *secrets)) from exc
    attrs = entry.get("attributes") or {}
    return {
        "dn": entry["dn"],
        "email": _one(attrs.get(cfg.ldap_email_attr)).strip(),
        "name": _one(attrs.get(cfg.ldap_name_attr)),
        "groups": _many(attrs.get(cfg.ldap_group_attr)),
    }


def test_connection(cfg) -> str:
    """Service bind plus a base-scope read of the search base -- what the
    settings page's button runs. Raises :class:`LDAPError` on any problem."""
    try:
        conn = _open(cfg, cfg.ldap_bind_dn, cfg.ldap_bind_password)
        try:
            found = _search(conn, cfg, "(objectClass=*)", scope=ldap3.BASE)
        finally:
            with contextlib.suppress(LDAPException):
                conn.unbind()
    except LDAPException as exc:
        raise LDAPError(_safe(exc, cfg.ldap_bind_password)) from exc
    if not found:
        raise LDAPError(f"Bound, but the search base {cfg.ldap_user_search_base} was not found.")
    return (
        f"Connected to {cfg.ldap_server_uri} as {cfg.ldap_bind_dn or 'anonymous'}; "
        f"search base {cfg.ldap_user_search_base} found."
    )
