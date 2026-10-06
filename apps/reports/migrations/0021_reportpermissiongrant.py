import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orgs", "0019_permissiongroupgrant_viewer_scope"),
        ("reports", "0020_sharelink_embed_appearance"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReportPermissionGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "grant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report_grants",
                        to="orgs.permissiongroupgrant",
                    ),
                ),
                (
                    "report",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="permission_group_grants",
                        to="reports.report",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="reportpermissiongrant",
            constraint=models.UniqueConstraint(
                fields=("grant", "report"), name="uniq_group_report_grant"
            ),
        ),
    ]
