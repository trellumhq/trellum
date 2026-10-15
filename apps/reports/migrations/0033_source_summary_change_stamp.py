from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0032_merge_idle_refresh_and_source_summaries'),
    ]

    operations = [
        migrations.AddField(
            model_name='reportsourcesummary',
            name='change_stamp',
            field=models.CharField(blank=True, default=None, max_length=96, null=True),
        ),
    ]
