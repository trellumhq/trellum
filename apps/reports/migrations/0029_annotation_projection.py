import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reports", "0028_report_runtime_experiment_projection")]
    operations = [
        migrations.CreateModel(name="AnnotationCatalogState", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("studio", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="annotation_catalog", to="studios.studio")),
            ("revision", models.PositiveBigIntegerField(default=0)),
            ("published_revision", models.PositiveBigIntegerField(default=0)),
            ("type_defaults", models.JSONField(default=dict)),
        ]),
        migrations.CreateModel(name="AnnotationSourceState", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("studio", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="annotation_sources", to="studios.studio")),
            ("report", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="annotation_source", to="reports.report")),
            ("kind", models.CharField(max_length=8)),
            ("digest", models.CharField(blank=True, max_length=64)),
            ("provenance_sha", models.CharField(blank=True, max_length=64)),
            ("desired_generation", models.PositiveBigIntegerField(default=1)),
            ("published_generation", models.PositiveBigIntegerField(default=0)),
            ("owner_workspace_id", models.CharField(blank=True, max_length=32)),
            ("ownership_generation", models.PositiveBigIntegerField(default=0)),
            ("status", models.CharField(default="pending", max_length=16)),
            ("observed_at", models.DateTimeField(blank=True, null=True)),
            ("indexed_at", models.DateTimeField(blank=True, null=True)),
            ("row_count", models.PositiveIntegerField(default=0)),
            ("error_code", models.CharField(blank=True, max_length=32)),
            ("failures", models.PositiveIntegerField(default=0)),
            ("next_check_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("lease_token", models.CharField(blank=True, max_length=32)),
            ("lease_until", models.DateTimeField(blank=True, null=True)),
        ], options={
            "constraints": [models.UniqueConstraint(fields=("studio",), condition=models.Q(kind="global"), name="anno_one_global"), models.CheckConstraint(condition=models.Q(kind="global", report__isnull=True) | models.Q(kind="report", report__isnull=False), name="anno_source_kind")],
            "indexes": [models.Index(fields=["owner_workspace_id", "next_check_at", "id"], name="anno_owner_due")],
        }),
        migrations.CreateModel(name="AnnotationEvent", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("source", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="reports.annotationsourcestate")),
            ("studio", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="studios.studio")),
            ("generation", models.PositiveBigIntegerField()),
            ("ordinal", models.PositiveIntegerField()),
            ("start_date", models.DateField()),
            ("end_date", models.DateField(blank=True, null=True)),
            ("label", models.CharField(blank=True, max_length=2048)),
            ("event_type", models.CharField(max_length=256)),
            ("scope", models.CharField(blank=True, max_length=256)),
            ("visible_default", models.BooleanField(default=True)),
        ], options={
            "constraints": [models.UniqueConstraint(fields=("source", "ordinal"), name="anno_source_ordinal")],
            "indexes": [models.Index(fields=["studio", "start_date", "end_date"], name="anno_studio_dates"), models.Index(fields=["studio", "event_type"], name="anno_studio_type"), models.Index(fields=["studio", "scope"], name="anno_studio_scope")],
        }),
    ]
