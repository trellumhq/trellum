"""Email backend that prefers SMTP configured in the portal over the environment.

Three layers, most specific first:

1. ``InstanceConfig`` — set by the first-run wizard or ``/system``. Lets an
   operator turn on mail without editing ``.env`` and restarting the stack.
2. ``EMAIL_URL`` in the environment — the pre-existing path, untouched.
3. Neither: messages are written to the container log, exactly as before, so
   a password reset never hangs on a box with no mail server.
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpBackend


class InstanceEmailBackend(SmtpBackend):
    """SMTP backend whose connection settings come from the database when set."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        row = self._instance_config()
        configured = False
        if row is not None and row.email_host:
            self.host = row.email_host
            self.port = row.email_port or 587
            self.username = row.email_host_user
            self.password = row.email_host_password
            self.use_tls = bool(row.email_use_tls)
            # Django refuses a backend with both flags on; TLS is the field we
            # expose, so implicit SSL is only reachable through EMAIL_URL.
            self.use_ssl = False
            configured = True
        elif getattr(settings, "EMAIL_URL_CONFIGURED", False):
            configured = True  # host/port/credentials already came from EMAIL_URL

        # `not self.host` would be the obvious test and is wrong: Django
        # defaults EMAIL_HOST to "localhost", so an instance nobody configured
        # would silently try to deliver over SMTP to itself instead of logging.
        self._console = None if configured else ConsoleBackend(fail_silently=self.fail_silently)

    @staticmethod
    def _instance_config():
        try:
            from apps.core.models import InstanceConfig

            return InstanceConfig.load()
        except Exception:  # noqa: BLE001 - unmigrated DB, or DB down
            return None

    def open(self):
        if self._console is not None:
            return self._console.open()
        return super().open()

    def close(self):
        if self._console is not None:
            return self._console.close()
        return super().close()

    def send_messages(self, email_messages):
        if self._console is not None:
            return self._console.send_messages(email_messages)
        return super().send_messages(email_messages)


def default_from_email() -> str:
    """The From: address — portal setting, then env, then Django's default."""
    try:
        from apps.core.models import InstanceConfig

        configured = InstanceConfig.load().email_from
    except Exception:  # noqa: BLE001
        configured = ""
    return configured or settings.DEFAULT_FROM_EMAIL


def send_test_email(recipient: str) -> None:
    """Deliver a 'your mail works' message. Raises on failure — the caller is a
    button whose whole purpose is to surface the error."""
    from django.core.mail import EmailMessage

    from apps.core.instance import base_url, instance_name

    EmailMessage(
        subject=f"{instance_name()} — test email",
        body=(
            f"This is a test message from {instance_name()} ({base_url()}).\n\n"
            "If you are reading it, outbound mail is configured correctly: "
            "invitations and password resets will be delivered."
        ),
        from_email=default_from_email(),
        to=[recipient],
    ).send(fail_silently=False)
