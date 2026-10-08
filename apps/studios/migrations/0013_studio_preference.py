from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


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
        migrations.RunPython(copy_membership_themes, migrations.RunPython.noop),
    ]
