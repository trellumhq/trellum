from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('runner', '0005_run_run_report_created_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='run',
            name='container_id',
            field=models.CharField(blank=True, default='', max_length=80),
        ),
        migrations.AddField(
            model_name='workerheartbeat',
            name='sandbox_mode',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
    ]
