from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("orgs", "0018_organization_api_keys_enabled")]

    operations = [
        migrations.AddField(
            model_name="permissiongroupgrant",
            name="viewer_scope",
            field=models.CharField(
                choices=[("all", "All reports"), ("selected", "Selected reports")],
                default="all",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="permissiongroupgrant",
            constraint=models.CheckConstraint(
                condition=models.Q(("viewer_scope", "all"), ("role", "viewer"), _connector="OR"),
                name="selected_reports_require_viewer_role",
            ),
        ),
    ]
