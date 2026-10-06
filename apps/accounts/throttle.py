"""Login throttling and escalating lockout.

The portal had none: a console that reaches every organization on the
instance accepted unlimited password guesses, and the audit log did not even
record that they were happening.

Counted on two keys, because the two attacks look different and closing only
one window leaves the other open:

* **per email** — many passwords against one account,
* **per client address** — one common password against many accounts, which
  no per-account counter ever sees. The IP threshold is deliberately higher
  than the account one: shared NAT means one office is one address, and
  collateral-locking a whole org out is the failure mode to avoid.

Backed by the shared database cache, so the count is the same in both
gunicorn workers and survives a restart.

Deliberately *not a permanent lock*: a counter that permanently locks an
account on failures lets anyone lock out a user they can name forever. What
crossing a threshold buys instead is an **escalating, bounded cooloff** --
the DB-configured base minutes, doubling on each consecutive lockout of the
same key within 24h, capped at 240. A correct password, or an admin's
"clear login lockout", clears everything for that key including the
escalation state.

Thresholds and windows are DB-configured (``InstanceConfig``) rather than
Django settings, so an operator can change them from ``/system`` without a
restart. ``LOGIN_MAX_ATTEMPTS``/``LOGIN_ATTEMPT_WINDOW_SECONDS`` (env) seed
the field defaults a fresh install starts from (see
``apps/core/models.py``); once the row exists, the DB value is what governs.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.cache import cache

from apps.core.net import client_ip

_PREFIX = "login-attempts"
#: How long a lockout's "how many times in a row" memory lasts. A key that
#: goes 24h without a fresh lockout starts back at the base cooloff.
_ESCALATION_TTL_SECONDS = 24 * 60 * 60
_COOLOFF_CAP_MINUTES = 240


@dataclass(frozen=True)
class _Scope:
    kind: str  # "email" | "ip"
    value: str

    @property
    def counter_key(self) -> str:
        return f"{_PREFIX}:{self.kind}:{self.value}"

    @property
    def locked_key(self) -> str:
        return f"{_PREFIX}:locked:{self.kind}:{self.value}"

    @property
    def escalation_key(self) -> str:
        return f"{_PREFIX}:esc:{self.kind}:{self.value}"


def _scopes(email: str, request) -> list[_Scope]:
    scopes = []
    if email:
        scopes.append(_Scope("email", email.strip().lower()[:150]))
    address = client_ip(request)
    if address:
        scopes.append(_Scope("ip", address))
    return scopes


def _config():
    from apps.core.models import InstanceConfig

    return InstanceConfig.load()


def _threshold(cfg, kind: str) -> int:
    return cfg.lockout_account_threshold if kind == "email" else cfg.lockout_ip_threshold


def _cooloff_seconds(cfg, scope: _Scope) -> int:
    escalation = cache.get(scope.escalation_key, 0)
    minutes = min(cfg.lockout_cooloff_minutes * (2**escalation), _COOLOFF_CAP_MINUTES)
    return int(minutes * 60)


def is_throttled(email: str, request) -> bool:
    cfg = _config()
    for scope in _scopes(email, request):
        if cache.get(scope.locked_key):
            return True
        threshold = _threshold(cfg, scope.kind)
        if threshold and cache.get(scope.counter_key, 0) >= threshold:
            return True
    return False


def record_failure(email: str, request) -> None:
    """Count one failed attempt against every applicable scope. A scope that
    crosses its threshold on this call gets locked (escalating cooloff) and
    emits exactly one ``auth.lockout`` row -- the edge is "the locked flag
    did not exist a moment ago and does now", so a burst of attempts after
    the lockout is already active never re-emits.
    """
    cfg = _config()
    window = cfg.lockout_window_minutes * 60
    for scope in _scopes(email, request):
        if cache.get(scope.locked_key):
            continue  # already locked; do not re-count or re-audit
        try:
            # add() only sets when absent, so the first failure starts the
            # window and later ones extend the count without resetting it --
            # otherwise an attacker could keep the window rolling forever.
            cache.add(scope.counter_key, 0, window)
            count = cache.incr(scope.counter_key)
        except ValueError:
            # The entry expired between add() and incr(). The next attempt
            # starts a fresh window, which is the correct outcome.
            cache.add(scope.counter_key, 1, window)
            count = 1

        threshold = _threshold(cfg, scope.kind)
        if threshold and count >= threshold:
            _lock(request, cfg, scope, email, count)


def _lock(request, cfg, scope: _Scope, email: str, failures: int) -> None:
    from apps.core.audit import audit

    cooloff_seconds = _cooloff_seconds(cfg, scope)
    escalation = cache.get(scope.escalation_key, 0)
    cache.set(scope.locked_key, True, cooloff_seconds)
    cache.set(scope.escalation_key, escalation + 1, _ESCALATION_TTL_SECONDS)
    audit(
        request, "auth.lockout", outcome="denied",
        attempted=email, scope=scope.kind, failures=failures,
        cooloff_seconds=cooloff_seconds,
    )


def clear(email: str, request) -> None:
    """A correct password means this was not an attack: clear counters,
    lock flags and escalation state for every applicable scope."""
    for scope in _scopes(email, request):
        cache.delete_many([scope.counter_key, scope.locked_key, scope.escalation_key])


def unlock(email: str) -> bool:
    """Admin "clear login lockout": deletes the EMAIL-scoped counter, lock
    flag and escalation state. Deliberately email-only -- an admin unlocking
    a victim must not need to know the attacker's IP, and IP-scoped state
    expires on its own (see the module docstring). Returns whether anything
    was actually cleared, so a caller can tell "cleared" from "was not
    locked to begin with".
    """
    scope = _Scope("email", email.strip().lower()[:150])
    keys = [scope.counter_key, scope.locked_key, scope.escalation_key]
    existed = any(cache.get(k) is not None for k in keys)
    cache.delete_many(keys)
    return existed
