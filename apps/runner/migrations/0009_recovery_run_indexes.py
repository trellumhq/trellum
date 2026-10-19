"""Build indexes on growing run history without blocking queue writes."""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("runner", "0008_run_recovery")]
    operations = [
        AddIndexConcurrently("run", models.Index(fields=["recovery"], name="run_recovery_idx")),
        AddIndexConcurrently("run", models.Index(fields=["report"], condition=models.Q(status__in=["queued", "starting", "running"]), name="run_active_report_idx")),
    ]
