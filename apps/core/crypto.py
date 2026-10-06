"""Encryption-at-rest for tenant secrets.

Values are encrypted with Fernet before they hit the database. The key comes
from the ``SECRET_ENCRYPTION_KEY`` setting: a comma-separated list of Fernet
keys where the FIRST key encrypts and ALL keys decrypt. Rotation:

    1. Prepend a fresh key: ``SECRET_ENCRYPTION_KEY=new,old``
    2. Run ``manage.py rotate_encryption`` (re-encrypts every stored secret)
    3. Drop the old key.

Stored format is ``enc$1$<fernet token>`` so a plaintext value can never be
mistaken for an encrypted one. Encrypted columns cannot be filtered on —
Fernet output differs on every call. Look rows up by their other columns.
"""
from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

_PREFIX = "enc$1$"


def _fernet() -> MultiFernet:
    raw = getattr(settings, "SECRET_ENCRYPTION_KEY", "") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ImproperlyConfigured(
            "SECRET_ENCRYPTION_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(f"SECRET_ENCRYPTION_KEY contains an invalid Fernet key: {exc}") from exc


def encrypt_str(value: str) -> str:
    if value is None:
        return value
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(stored: str) -> str:
    if stored is None or stored == "":
        return stored
    if not stored.startswith(_PREFIX):
        # Pre-encryption legacy value (or a fixture); pass through unchanged
        # so imports of plaintext config remain loadable.
        return stored
    token = stored[len(_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "Cannot decrypt a stored secret: SECRET_ENCRYPTION_KEY does not "
            "contain the key that encrypted it."
        ) from exc


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(_PREFIX)


class EncryptedTextField(models.TextField):
    """TextField whose value is Fernet-encrypted at rest.

    Python value: str (or None/""). DB value: ``enc$1$<token>``.
    """

    description = "Text, encrypted at rest"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        if isinstance(value, str) and value.startswith(_PREFIX):
            return value  # already encrypted (e.g. rotation resave path)
        return encrypt_str(str(value))

    def from_db_value(self, value, expression, connection):  # noqa: ARG002
        if value is None or value == "":
            return value
        return decrypt_str(value)


class EncryptedJSONField(models.TextField):
    """JSON (dict/list) serialized then Fernet-encrypted at rest.

    Python value: dict/list (or None). DB value: ``enc$1$<token>``.
    """

    description = "JSON, encrypted at rest"

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, str) and value.startswith(_PREFIX):
            return value
        return encrypt_str(json.dumps(value, sort_keys=True))

    def from_db_value(self, value, expression, connection):  # noqa: ARG002
        if value is None or value == "":
            return None
        return json.loads(decrypt_str(value))

    def to_python(self, value):
        if value is None or isinstance(value, (dict, list)):
            return value
        return json.loads(decrypt_str(value))


def encrypted_field_targets():
    """Yield (model, [field names]) for every model that stores secrets."""
    from django.apps import apps as django_apps

    for model in django_apps.get_models():
        names = [
            f.name
            for f in model._meta.get_fields()
            if isinstance(f, (EncryptedTextField, EncryptedJSONField))
        ]
        if names:
            yield model, names
