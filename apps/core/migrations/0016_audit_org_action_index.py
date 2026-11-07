from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("core", "0015_instanceconfig_deliveries_paused_and_more")]
    operations = [
        AddIndexConcurrently(
            model_name="auditlog",
            index=models.Index(fields=["org", "action", "-created_at"], name="audit_org_action_time_idx"),
        ),
    ]
