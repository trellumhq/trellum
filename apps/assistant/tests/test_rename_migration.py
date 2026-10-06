"""The BA Buddy -> AI assistant table rename carries the rows across.

The migration's ordinary path is a fresh database, where
``carry_over_from_buddy`` does nothing -- which is exactly why the other
branch needs a test of its own. It runs only against a database that predates
the rename, so if it were broken nobody would find out until a developer had
already lost their transcripts and their spend ledger.
"""
import importlib
from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db

# The module name starts with a digit, so it cannot be imported with `from`.
_migration = importlib.import_module("apps.assistant.migrations.0001_initial")
carry_over = _migration.carry_over_from_buddy


class _SchemaEditor:
    """The one attribute the migration functions actually use."""

    connection = connection


free_names = _migration.free_the_legacy_names


def _make_legacy_tables(cur):
    # Column types match what Django would have created for the old models --
    # jsonb, not text. A looser fixture would pass here and the real migration
    # would still fail on a real database.
    cur.execute("""
        CREATE TABLE buddy_buddysession (
            id bigint PRIMARY KEY, title varchar(200), state jsonb,
            created_at timestamptz, updated_at timestamptz,
            org_id bigint, report_id bigint, studio_id bigint, user_id bigint
        )""")
    # The real legacy table owns this schema-global constraint name. Running
    # at the historical migration state lets this fixture reproduce the name
    # collision that 0001 must resolve before it creates the new table.
    cur.execute("""
        CREATE TABLE buddy_llmusage (
            id bigint PRIMARY KEY, month date, cost_usd numeric(9,4),
            updated_at timestamptz, org_id bigint, user_id bigint,
            CONSTRAINT uniq_llm_usage_month UNIQUE (org_id, user_id, month)
        )""")


def test_a_fresh_database_is_untouched():
    """No old tables, nothing to move, no error."""
    carry_over(None, _SchemaEditor())
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM assistant_assistantsession")
        assert cur.fetchone()[0] == 0


@pytest.mark.django_db(transaction=True)
def test_real_upgrade_moves_rows_and_defaults_them_to_full_scope(org, viewer, studio_tree):
    """Exercise the migration at its historical schema, then restore HEAD."""
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    initial = ("assistant", "0001_initial")
    restored = False
    try:
        executor.migrate([("assistant", None)])
        with connection.cursor() as cur:
            _make_legacy_tables(cur)
            cur.execute(
                "INSERT INTO buddy_buddysession "
                "(id, title, state, created_at, updated_at, org_id, report_id, studio_id, user_id) "
                "VALUES (1, 'why did revenue drop', '{}'::jsonb, '2026-08-01', "
                "'2026-08-01', %s, NULL, %s, %s)",
                [org.pk, studio_tree.pk, viewer.pk],
            )
            cur.execute(
                "INSERT INTO buddy_llmusage "
                "(id, month, cost_usd, updated_at, org_id, user_id) "
                "VALUES (1, '2026-08-01', 4.2500, '2026-08-01', %s, %s)",
                [org.pk, viewer.pk],
            )
            cur.execute(
                "INSERT INTO django_migrations (app, name, applied) "
                "VALUES ('buddy', '0001_initial', '2026-08-12')"
            )

        executor = MigrationExecutor(connection)
        executor.migrate([initial])
        old_apps = executor.loader.project_state([initial]).apps
        HistoricalSession = old_apps.get_model("assistant", "AssistantSession")
        HistoricalUsage = old_apps.get_model("assistant", "LlmUsage")
        session = HistoricalSession.objects.get(pk=1)
        assert session.title == "why did revenue drop"
        assert session.org_id == org.pk and session.user_id == viewer.pk
        assert HistoricalUsage.objects.get(pk=1).cost_usd == Decimal("4.25")
        remaining = set(connection.introspection.table_names())
        assert "buddy_buddysession" not in remaining
        assert "buddy_llmusage" not in remaining
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM django_migrations WHERE app = 'buddy'")
            assert cur.fetchone()[0] == 0

        MigrationExecutor(connection).migrate(leaves)
        restored = True
        from apps.assistant.models import AssistantSession

        assert AssistantSession.objects.get(pk=1).scope == AssistantSession.SCOPE_FULL
    finally:
        if not restored:
            with connection.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS buddy_buddysession CASCADE")
                cur.execute("DROP TABLE IF EXISTS buddy_llmusage CASCADE")
                cur.execute("DELETE FROM django_migrations WHERE app = 'buddy'")
            MigrationExecutor(connection).migrate(leaves)


class TestNameCollisions:
    """Every hand-named database object the new tables want must be free.

    This is the bug the first version of this suite missed, and it cost a
    failed migration against a real pre-rename database. PostgreSQL holds
    constraint and index names in the schema, not on the table, so
    ``CreateModel`` for ``assistant_llmusage`` fails outright while
    ``buddy_llmusage`` still owns ``uniq_llm_usage_month`` -- and it fails
    BEFORE the data carry-over that would have dropped the old table.

    Auto-named indexes are safe: Postgres builds their names from the table
    name, so the old and new sets never overlap. Only hand-named objects can
    collide, which makes this checkable without a database.
    """

    #: What the pre-rename models named their constraints and indexes. A
    #: historical fact, so it is written down rather than derived.
    LEGACY_NAMES = {"buddy_user_recent_idx", "uniq_llm_usage_month"}

    def _declared_names(self):
        from apps.assistant.models import AssistantSession, LlmUsage

        names = set()
        for model in (AssistantSession, LlmUsage):
            names |= {c.name for c in model._meta.constraints}
            names |= {i.name for i in model._meta.indexes if i.name}
        return names

    def test_a_name_kept_from_the_old_models_is_freed_first(self):
        reused = self._declared_names() & self.LEGACY_NAMES
        # uniq_llm_usage_month was kept; buddy_user_recent_idx was renamed.
        assert reused == {"uniq_llm_usage_month"}
        assert reused <= set(_migration._COLLIDING_NAMES), (
            "a constraint name kept from the pre-rename models must be listed "
            "in the migration's _COLLIDING_NAMES, or CreateModel will fail on "
            "any database that still has the old tables"
        )

    def test_the_freeing_step_runs_before_the_tables_are_created(self):
        """Order is the whole point: freeing the name after CreateModel is
        the same as not freeing it."""
        ops = _migration.Migration.operations
        kinds = [type(op).__name__ for op in ops]
        assert kinds[0] == "RunPython"
        assert ops[0].code is _migration.free_the_legacy_names
        assert "CreateModel" in kinds[1:]


def test_freeing_names_is_a_no_op_without_the_old_tables():
    free_names(None, _SchemaEditor())  # must not raise
