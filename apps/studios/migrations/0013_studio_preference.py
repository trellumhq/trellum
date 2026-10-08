from django.conf import settings
from django.db import migrations, models, router
import django.db.models.deletion


class AddPreferenceDatabaseCascades(migrations.RunSQL):
    """Keep PostgreSQL parent deletion safe for older application registries."""

    def __init__(self):
        super().__init__(sql=self.noop, reverse_sql=self.noop)

    def deconstruct(self):
        return self.__class__.__qualname__, [], {}

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "postgresql" or not router.allow_migrate(
            schema_editor.connection.alias, app_label, **self.hints
        ):
            return
        preference = to_state.apps.get_model("studios", "StudioPreference")
        if schema_editor.collect_sql:
            schema_editor.execute("-- FK alterations follow CreateModel's deferred statements.")
        quote = schema_editor.quote_name
        table = quote(preference._meta.db_table)
        for field_name in ("user", "studio"):
            field = preference._meta.get_field(field_name)
            target = field.target_field
            constraint = schema_editor._fk_constraint_name(
                preference, field, "_fk_%(to_table)s_%(to_column)s"
            )
            # CreateModel's FK statements run when the schema editor exits.
            schema_editor.deferred_sql.extend([
                f"ALTER TABLE {table} DROP CONSTRAINT {constraint}",
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY ({quote(field.column)}) "
                f"REFERENCES {quote(target.model._meta.db_table)} ({quote(target.column)}) "
                "ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED",
            ])

    def describe(self):
        return "Enable database deletion cascades for studio preferences"


def copy_membership_themes(apps, schema_editor):
    StudioMembership = apps.get_model("studios", "StudioMembership")
    StudioPreference = apps.get_model("studios", "StudioPreference")
    alias = schema_editor.connection.alias
    memberships = (
        StudioMembership.objects.using(alias)
        .exclude(theme="")
        .values_list("user_id", "studio_id", "theme")
        .iterator(chunk_size=500)
    )
    batch = []
    for user_id, studio_id, theme in memberships:
        batch.append(StudioPreference(user_id=user_id, studio_id=studio_id, theme=theme))
        if len(batch) == 500:
            StudioPreference.objects.using(alias).bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        StudioPreference.objects.using(alias).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("studios", "0012_studio_default_analysis_audience_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StudioPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("theme", models.CharField(blank=True, default="", max_length=64)),
                (
                    "studio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preferences",
                        to="studios.studio",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="studio_preferences",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("user", "studio"), name="uniq_studio_preference")
                ],
            },
        ),
        # Previous-release deletion collectors cannot see this new table.
        AddPreferenceDatabaseCascades(),
        migrations.RunPython(copy_membership_themes, migrations.RunPython.noop),
    ]
