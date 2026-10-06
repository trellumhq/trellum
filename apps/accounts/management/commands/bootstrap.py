"""Create the first admin account + organization from the command line.

    manage.py bootstrap --email a@b.c --password ... --org-name "Demo" --org-slug demo

The web equivalent is the /setup first-run wizard. Refuses to run when any
user already exists (this is a bootstrap, not a user-management tool).
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.orgs.provisioning import (
    OrgSlugTakenError,
    UserExistsError,
    provision_org_with_owner,
)


class Command(BaseCommand):
    help = "First-run bootstrap: create the instance operator and the first organization."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--name", default="")
        parser.add_argument("--org-name", required=True)
        parser.add_argument("--org-slug", required=True)

    def handle(self, *args, **opts):  # noqa: ARG002
        if User.objects.exists():
            raise CommandError("Users already exist; bootstrap is first-run only.")
        try:
            user, org = provision_org_with_owner(
                email=opts["email"],
                name=opts["name"],
                password=opts["password"],
                org_name=opts["org_name"],
                org_slug=opts["org_slug"],
                instance_operator=True,
            )
        except OrgSlugTakenError as exc:
            raise CommandError(f"Organization slug '{exc}' already exists.") from exc
        except UserExistsError as exc:
            raise CommandError(f"A user with email '{exc}' already exists.") from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Created operator {user.email} and organization {org.slug}."
            )
        )
