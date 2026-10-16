from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0034_drop_unused_annotation_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='reportsourcesummary',
            name='legacy_rebuild_queued_for',
            field=models.CharField(blank=True, max_length=300, null=True),
        ),
    ]
