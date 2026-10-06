"""Runtime access to the instance configuration.

Call these instead of reading ``settings.PORTAL_BASE_URL`` directly: the
operator can change the public URL from the portal after install, and a link
built from the setting would keep pointing at the old host.

The one place the *setting* is still authoritative is ``settings/prod.py``
(``CSRF_TRUSTED_ORIGINS``, ``SESSION_COOKIE_SECURE``) — read at import time,
before the database exists.
"""
from __future__ import annotations

from django.conf import settings


def config():
    """The singleton row. Imported lazily so this module is safe to import
    from ``settings``-adjacent code and from migrations."""
    from apps.core.models import InstanceConfig

    return InstanceConfig.load()


def base_url() -> str:
    """Absolute base URL for outbound links, without a trailing slash."""
    try:
        configured = config().public_base_url
    except Exception:  # noqa: BLE001 - pre-migrate, or DB briefly unavailable
        configured = ""
    return (configured or settings.PORTAL_BASE_URL).rstrip("/")


def instance_name() -> str:
    try:
        return config().instance_name or "trellum"
    except Exception:  # noqa: BLE001
        return "trellum"


def mail_is_configured() -> bool:
    """True when outbound mail will actually leave the building.

    Either the operator filled in SMTP in the portal, or ``EMAIL_URL`` was set
    in the environment. When neither is true, mail goes to the container log
    and the UI must keep offering copyable links instead.

    Deliberately not a test of ``settings.EMAIL_HOST``: Django defaults that to
    ``"localhost"``, so an unconfigured instance would claim it could send mail.
    """
    try:
        if config().email_host:
            return True
    except Exception:  # noqa: BLE001
        pass
    return bool(getattr(settings, "EMAIL_URL_CONFIGURED", False))


def max_upload_bytes() -> int:
    """Largest data-source upload this instance accepts. 0 = no limit."""
    try:
        return int(config().max_upload_mb or 0) * 1024 * 1024
    except Exception:  # noqa: BLE001
        return 512 * 1024 * 1024


def setup_is_complete() -> bool:
    try:
        return config().setup_completed_at is not None
    except Exception:  # noqa: BLE001
        return False
