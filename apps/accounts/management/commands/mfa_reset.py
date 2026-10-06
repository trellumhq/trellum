"""Shell-trust MFA recovery for the lost-device-plus-lost-recovery-codes case.

    manage.py mfa_reset someone@example.com

Removes a user's TOTP device and recovery codes and bumps their auth_epoch
(ending their current sessions), the same as the web admin reset in the
operator console / org members page -- this is the no-web-access escape
hatch for it. Shell access is the root of trust for a self-host instance,
same as `manage.py createsuperuser` today; there is deliberately no web
backdoor for this.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Clear a user's MFA device and recovery codes, and end their sessions."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **opts):
        from apps.accounts import mfa
        from apps.core.audit import audit_system

        email = opts["email"]
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            raise CommandError(f"No user with email '{email}'.")
        if not user.has_mfa:
            self.stdout.write(self.style.WARNING(f"{user.email} does not have MFA enrolled."))
            return

        mfa.remove_device(user)
        user.bump_auth_epoch()
        audit_system("auth.mfa_reset", target_email=user.email, by_admin=True, via="manage.py")
        self.stdout.write(
            self.style.SUCCESS(
                f"MFA cleared for {user.email}; their sessions have been ended."
            )
        )
