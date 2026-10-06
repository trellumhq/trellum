"""seed_synthetic_datasource: schema mode against the test DB."""
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import connection

from apps.datasources.models import DataSource

pytestmark = pytest.mark.django_db


def _seed(**opts):
    out = StringIO()
    call_command("seed_synthetic_datasource", stdout=out, **opts)
    return out.getvalue()


def _count_events() -> int:
    with connection.cursor() as cur:
        cur.execute('SELECT count(*) FROM "synth".events')
        return cur.fetchone()[0]


class TestGate:
    def test_refuses_outside_debug(self, settings):
        settings.DEBUG = False
        with pytest.raises(CommandError, match="DEBUG is off"):
            call_command("seed_synthetic_datasource")


class TestSchemaMode:
    @pytest.fixture(autouse=True)
    def _debug(self, settings):
        settings.DEBUG = True

    def test_fills_registers_and_is_idempotent(self, org):
        _seed(org=org.slug, reuse_portal_db=True, gb=0.001, batch_rows=1000)
        first = _count_events()
        assert first >= 1000

        source = DataSource.objects.get(org=org, name="synthetic")
        assert source.type == "postgres"
        assert source.config["database"]  # portal DB name
        assert source.all_fields()["user"]  # credentials decrypt

        # Re-run resumes from max(id): count never shrinks, no PK violations.
        _seed(org=org.slug, reuse_portal_db=True, gb=0.001, batch_rows=1000)
        assert _count_events() >= first

    def test_drop_removes_schema_and_source(self, org):
        _seed(org=org.slug, reuse_portal_db=True, gb=0.001, batch_rows=1000)
        _seed(org=org.slug, reuse_portal_db=True, drop=True)
        assert not DataSource.objects.filter(org=org, name="synthetic").exists()
        with connection.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'synth'"
            )
            assert cur.fetchone() is None
