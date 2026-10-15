"""Opt-in action observations; execution and authorization belong to callers."""
from urllib.parse import urlsplit

STATES = frozenset({"accepted", "running", "succeeded", "failed", "unconfirmed"})


def _text(value, name, limit, *, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid {name}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"Invalid {name}")
    return value


def action_payload(*, action_id, state, message, status_url=None, revision=None):
    """Build an observation, without admitting work or authorizing its lookup.

    Status URLs are root-relative; the authorized endpoint remains responsible
    for checking the current actor, tenant and operation on every request.
    Provider acceptance belongs in separate domain fields, not this state.
    """
    action_id = _text(action_id, "action ID", 128)
    state = _text(state, "state", 32)
    if state not in STATES:
        raise ValueError("Invalid state")
    message = _text(message, "message", 1000)
    revision = _text(revision, "revision", 256, nullable=True)
    if status_url is not None:
        status_url = _text(status_url, "status URL", 2048)
        parsed = urlsplit(status_url)
        if (not status_url.startswith("/") or status_url.startswith("//")
                or "\\" in status_url or parsed.scheme or parsed.netloc or parsed.fragment):
            raise ValueError("Status URL must be root-relative")
    return {"action": {"version": 1, "id": action_id, "state": state,
                       "message": message, "status_url": status_url, "revision": revision}}
