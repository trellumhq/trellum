"""Crypto round-trip, prefix, and rotation behavior."""
import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core import crypto


def test_round_trip():
    assert crypto.decrypt_str(crypto.encrypt_str("s3cret")) == "s3cret"


def test_unicode_round_trip():
    val = "wachtwoord-ünïcode-密码"
    assert crypto.decrypt_str(crypto.encrypt_str(val)) == val


def test_encrypted_value_is_prefixed_and_opaque():
    stored = crypto.encrypt_str("hunter2")
    assert stored.startswith("enc$1$")
    assert "hunter2" not in stored
    assert crypto.is_encrypted(stored)


def test_two_encryptions_differ():
    # Fernet is randomized: equality lookups on encrypted columns can never
    # work, which is exactly why the fields document that restriction.
    assert crypto.encrypt_str("x") != crypto.encrypt_str("x")


def test_plaintext_passthrough():
    # Legacy/imported plaintext (no prefix) is returned unchanged.
    assert crypto.decrypt_str("plain-old-value") == "plain-old-value"
    assert not crypto.is_encrypted("plain-old-value")


def test_empty_and_none():
    assert crypto.decrypt_str("") == ""
    assert crypto.decrypt_str(None) is None


def test_missing_key_raises():
    with override_settings(SECRET_ENCRYPTION_KEY=""):
        with pytest.raises(ImproperlyConfigured):
            crypto.encrypt_str("x")


def test_rotation_old_key_still_decrypts():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    with override_settings(SECRET_ENCRYPTION_KEY=old_key):
        stored = crypto.encrypt_str("rotate-me")
    # New primary key prepended, old key retained: decryption still works.
    with override_settings(SECRET_ENCRYPTION_KEY=f"{new_key},{old_key}"):
        assert crypto.decrypt_str(stored) == "rotate-me"
        restored = crypto.encrypt_str(crypto.decrypt_str(stored))
    # After re-encryption the old key is no longer needed.
    with override_settings(SECRET_ENCRYPTION_KEY=new_key):
        assert crypto.decrypt_str(restored) == "rotate-me"


def test_wrong_key_raises():
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    with override_settings(SECRET_ENCRYPTION_KEY=key_a):
        stored = crypto.encrypt_str("secret")
    with override_settings(SECRET_ENCRYPTION_KEY=key_b):
        with pytest.raises(ImproperlyConfigured):
            crypto.decrypt_str(stored)


@pytest.mark.django_db
def test_api_credentials_rotate_with_all_encrypted_fields(settings):
    from django.core.management import call_command
    from django.db import connection

    from apps.core.models import EmailApiConnection

    old_key = settings.SECRET_ENCRYPTION_KEY
    new_key = Fernet.generate_key().decode()
    profiles = [
        EmailApiConnection.objects.create(
            name="ses", provider="amazon_ses", from_email="from@example.test",
            provider_config={"region": "eu-west-1", "credential_source": "access_keys"},
            credentials={"aws_access_key_id": "id", "aws_secret_access_key": "secret", "aws_session_token": "session"},
        ),
        EmailApiConnection.objects.create(
            name="custom", provider="custom_https", from_email="from@example.test",
            credentials={"token": "token", "headers": {"X-Test": "extra"}},
        ),
    ]
    assert any(model is EmailApiConnection and "credentials" in names
               for model, names in crypto.encrypted_field_targets())
    with connection.cursor() as cursor:
        cursor.execute("SELECT credentials FROM core_emailapiconnection ORDER BY id")
        before = [row[0] for row in cursor.fetchall()]
    with override_settings(SECRET_ENCRYPTION_KEY=f"{new_key},{old_key}"):
        call_command("rotate_encryption", verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SELECT credentials FROM core_emailapiconnection ORDER BY id")
        after = [row[0] for row in cursor.fetchall()]
    assert all(a.startswith("enc$1$") and a != b for a, b in zip(after, before))
    with override_settings(SECRET_ENCRYPTION_KEY=new_key):
        assert EmailApiConnection.objects.get(pk=profiles[0].pk).credentials["aws_session_token"] == "session"
        assert EmailApiConnection.objects.get(pk=profiles[1].pk).credentials["headers"] == {"X-Test": "extra"}
