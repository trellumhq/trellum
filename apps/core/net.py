"""Working out who is actually on the other end of a request.

``REMOTE_ADDR`` is the address of whatever opened the TCP connection. This
portal is documented as running behind a reverse proxy terminating TLS
(`prod.py` sets SECURE_PROXY_SSL_HEADER for exactly that reason), so
REMOTE_ADDR is the proxy — the same value for every user, forever. Every
`AuditLog.ip` written in production so far records the proxy.

The fix is not simply to trust ``X-Forwarded-For``: any client can send that
header, so trusting it blindly lets anyone write whatever address they like into
the audit trail, which is worse than a useless column. The header is only
meaningful for the hops your own proxy appended, so the number of trusted
proxies has to be stated rather than guessed.
"""
from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid(candidate: str) -> str | None:
    candidate = candidate.strip()
    if not candidate:
        return None
    # Strip a port if one came along (some proxies append :port for IPv6).
    if candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.split(":")[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request) -> str | None:
    """The client's address, or None when it cannot be established honestly.

    With ``TRELLUM_TRUSTED_PROXIES = 0`` (the default) the connection address is
    used, which is right for a directly-exposed instance and for anyone who has
    not thought about it — a wrong-but-consistent proxy address beats a
    forgeable one.

    With N > 0, the Nth-from-last entry of ``X-Forwarded-For`` is taken: the
    last N were appended by proxies you control, so the one before them is the
    furthest hop you can still vouch for. Anything a client puts at the front of
    the header is ignored.
    """
    meta = getattr(request, "META", None)
    if not meta:
        return None

    trusted = getattr(settings, "TRELLUM_TRUSTED_PROXIES", 0)
    if trusted:
        forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            hops = [h for h in (p.strip() for p in forwarded.split(",")) if h]
            # hops[-1] was added by the closest proxy; walking back `trusted`
            # entries lands on the last address we did not have to take on
            # faith.
            index = len(hops) - trusted
            if 0 <= index < len(hops):
                resolved = _valid(hops[index])
                if resolved:
                    return resolved

    return _valid(meta.get("REMOTE_ADDR", "") or "")
