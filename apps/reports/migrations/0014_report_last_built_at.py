from django.db import migrations, models
from django.db.models import Max


def backfill_last_built_at(apps, schema_editor):
    """Seed the column from the newest SUCCESSFUL run per report.

    Reports with no successful run in surviving history keep NULL rather than
    a guessed date -- purge_built_data never expires a NULL, so an unknown
    age costs one extra window of storage instead of deleting output whose
    age nobody can vouch for.

    ``Run.report`` is PROTECT, so every run here still has its report; runs
    already purged simply do not contribute, which is the same "unknown" case.
    """
    Run = apps.get_model("runner", "Run")
    Report = apps.get_model("reports", "Report")
    newest = (
        Run.objects.filter(status="success", finished_at__isnull=False)
        .values("report_id")
        .annotate(built_at=Max("finished_at"))
    )
    for row in newest.iterator():
        Report.objects.filter(pk=row["report_id"]).update(last_built_at=row["built_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0013_orglivequerypolicy"),
        ("runner", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="report",
            name="last_built_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_last_built_at, migrations.RunPython.noop),
    ]
