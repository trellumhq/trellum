from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0021_reportpermissiongrant")]

    operations = [
        migrations.AddField(
            model_name="report",
            name="kind",
            field=models.CharField(
                choices=[("report", "Report"), ("analysis", "Analysis")],
                default="report",
                # Previous releases omit kind on INSERT after an application rollback.
                db_default="report",
                max_length=16,
            ),
        ),
    ]
