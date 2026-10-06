"""The AI assistant's own tables, after BA Buddy was renamed.

Created fresh rather than moved from the former ``buddy`` app: a cross-app
model move is migration-state surgery, and there is no released install to
protect. ``carry_over_from_buddy`` below moves the rows instead, so a
developer's existing transcripts and spend ledger survive the rename. It is
a no-op on a database that never had the old tables.
"""

import apps.assistant.models
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


#: Named constraints and indexes the pre-rename tables still hold, whose
#: names the new tables want too.
#:
#: In PostgreSQL these names live in the schema, not on the table, so
#: creating ``assistant_llmusage`` while ``buddy_llmusage`` still carries
#: ``uniq_llm_usage_month`` fails outright -- before the data carry-over
#: below ever gets a chance to drop the old table. Anything whose name
#: contains its table name (every auto-named index) is safe and is not
#: listed; only the hand-named ones collide.
_COLLIDING_NAMES = ("uniq_llm_usage_month",)


def free_the_legacy_names(apps, schema_editor):
    """Move pre-rename constraint names aside so CreateModel can have them.

    Runs before the tables are created. A no-op on a database that never had
    the old app.
    """
    conn = schema_editor.connection
    if "buddy_llmusage" not in set(conn.introspection.table_names()):
        return
    with conn.cursor() as cur:
        for name in _COLLIDING_NAMES:
            cur.execute(
                "SELECT 1 FROM pg_constraint WHERE conname = %s", [name]
            )
            if cur.fetchone():
                cur.execute(f'ALTER TABLE buddy_llmusage RENAME CONSTRAINT "{name}" TO "{name}_legacy"')


def carry_over_from_buddy(apps, schema_editor):
    """Move rows from the pre-rename tables, then drop them.

    Columns are named explicitly rather than relying on ``SELECT *``: only
    the model name changed, so the names match, but a positional copy would
    silently mis-map if the two CREATE TABLE orders ever differed.

    Dropping the old tables takes the renamed-aside constraints with them.
    """
    conn = schema_editor.connection
    existing = set(conn.introspection.table_names())
    moves = (
        ("buddy_buddysession", "assistant_assistantsession",
         "id, title, state, created_at, updated_at, org_id, report_id, studio_id, user_id"),
        ("buddy_llmusage", "assistant_llmusage",
         "id, month, cost_usd, updated_at, org_id, user_id"),
    )
    with conn.cursor() as cur:
        for old, new, cols in moves:
            if old not in existing:
                continue
            cur.execute(f"INSERT INTO {new} ({cols}) SELECT {cols} FROM {old}")
            cur.execute(f"DROP TABLE {old}")
        # The former app's own migration record has nowhere to point now.
        cur.execute("DELETE FROM django_migrations WHERE app = %s", ["buddy"])


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('orgs', '0013_orgassistantconfig_related_name'),
        ('reports', '0013_orglivequerypolicy'),
        ('studios', '0008_studio_repo_theme'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            free_the_legacy_names,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name='AssistantSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(blank=True, max_length=200)),
                ('state', models.JSONField(blank=True, default=apps.assistant.models.default_state)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('org', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assistant_sessions', to='orgs.organization')),
                ('report', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assistant_sessions', to='reports.report')),
                ('studio', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='assistant_sessions', to='studios.studio')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assistant_sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-updated_at'],
                'indexes': [models.Index(fields=['user', '-updated_at'], name='assistant_user_recent_idx')],
            },
        ),
        migrations.CreateModel(
            name='LlmUsage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(help_text='First day of the month this spend is booked to.')),
                ('cost_usd', models.DecimalField(decimal_places=4, default=Decimal('0'), max_digits=9)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('org', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='llm_usage', to='orgs.organization')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='llm_usage', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-month'],
                'constraints': [models.UniqueConstraint(fields=('org', 'user', 'month'), name='uniq_llm_usage_month')],
            },
        ),
        migrations.RunPython(
            carry_over_from_buddy,
            migrations.RunPython.noop,
        ),
    ]
