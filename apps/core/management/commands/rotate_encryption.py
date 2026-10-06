"""Re-encrypt every stored secret with the current primary Fernet key.

Usage (rotation):
    1. Set SECRET_ENCRYPTION_KEY=<new-key>,<old-key>
    2. python manage.py rotate_encryption
    3. Set SECRET_ENCRYPTION_KEY=<new-key>

Idempotent: values already encrypted with the primary key are simply
re-encrypted; a run with a single key is a no-op in effect.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.crypto import encrypted_field_targets


class Command(BaseCommand):
    help = "Re-encrypt all encrypted columns with the primary SECRET_ENCRYPTION_KEY"

    def handle(self, *args, **options):  # noqa: ARG002
        total = 0
        for model, field_names in encrypted_field_targets():
            with transaction.atomic():
                for obj in model.objects.all().iterator():
                    # Reading decrypted values and saving re-encrypts with
                    # the primary key via the field's get_prep_value.
                    obj.save(update_fields=field_names)
                    total += 1
            self.stdout.write(f"{model._meta.label}: rotated {field_names}")
        self.stdout.write(self.style.SUCCESS(f"Re-encrypted {total} row(s)."))
