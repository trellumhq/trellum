from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('reports', '0026_source_summaries')]
    operations = [
        migrations.AddField(model_name='metricdefinition', name='binding',
            field=models.JSONField(blank=True, default=None, null=True)),
        migrations.AddField(model_name='reportsourcesummary', name='workspace_version',
            field=models.PositiveSmallIntegerField(blank=True, default=None, null=True)),
    ]
