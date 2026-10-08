"""CI-only cleanup for a previous app registry testing an expanded schema."""
import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def cascade_previous_schema_flush(django_db_setup):
    from django.db import connection

    expected = os.environ.get("TRELLUM_PREVIOUS_SCHEMA_TEST_DB", "")
    if (
        not expected.startswith("test_")
        or connection.vendor != "postgresql"
        or os.environ.get("DJANGO_SETTINGS_MODULE") != "trellum_portal.settings.test"
        or connection.settings_dict["NAME"] != expected
    ):
        raise pytest.UsageError("Previous-schema cleanup requires an explicit PostgreSQL test database")
    original = connection.ops.sql_flush

    def sql_flush(style, tables, *, reset_sequences=False, allow_cascade=False):
        if connection.settings_dict["NAME"] != expected:
            raise pytest.UsageError("Refusing previous-schema cleanup outside its configured test database")
        return original(style, tables, reset_sequences=reset_sequences, allow_cascade=True)

    connection.ops.sql_flush = sql_flush
    try:
        yield
    finally:
        connection.ops.sql_flush = original
