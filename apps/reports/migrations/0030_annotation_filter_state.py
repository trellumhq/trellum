import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import apps.reports.models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0029_annotation_projection"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name="AnnotationFilterState", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("token", models.CharField(default=apps.reports.models._annotation_filter_token, max_length=64, unique=True)),
            ("actor_fingerprint", models.CharField(max_length=64)),
            ("client_operation_id", models.CharField(max_length=128)),
            ("mode", models.CharField(max_length=8)),
            ("status", models.CharField(default="draft", max_length=8)),
            ("revision", models.PositiveBigIntegerField(default=0)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("last_activity_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("expires_at", models.DateTimeField()),
            ("expected_chunks", models.PositiveBigIntegerField()),
            ("expected_items", models.PositiveBigIntegerField()),
            ("expected_chain_digest", models.CharField(max_length=64)),
            ("next_chunk", models.PositiveBigIntegerField(default=0)),
            ("accepted_items", models.PositiveBigIntegerField(default=0)),
            ("accepted_distinct", models.PositiveBigIntegerField(default=0)),
            ("rolling_digest", models.CharField(max_length=64)),
            ("studio", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="studios.studio")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
        ], options={
            "constraints": [
                models.UniqueConstraint(fields=("actor_fingerprint", "studio", "client_operation_id"), name="anno_filter_create_op"),
                models.CheckConstraint(condition=models.Q(mode__in=["explicit", "defaults"]), name="anno_filter_mode"),
                models.CheckConstraint(condition=models.Q(status__in=["draft", "ready", "retiring"]), name="anno_filter_status"),
            ],
            "indexes": [
                models.Index(fields=["actor_fingerprint", "studio", "status", "expires_at", "id"], name="anno_filter_actor_draft"),
                models.Index(fields=["expires_at", "id"], name="anno_filter_expiry"),
                models.Index(fields=["status", "expires_at", "id"], name="anno_filter_status_expiry"),
            ],
        }),
        migrations.CreateModel(name="AnnotationFilterMembership", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("event_type", models.CharField(max_length=256)),
            ("hidden", models.BooleanField(default=True)),
            ("state", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="reports.annotationfilterstate")),
        ], options={
            "constraints": [models.UniqueConstraint(fields=("state", "event_type"), name="anno_filter_type")],
            "indexes": [models.Index(fields=["state", "hidden", "event_type"], name="anno_filter_hidden_type")],
        }),
        migrations.CreateModel(name="AnnotationFilterReceipt", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("kind", models.CharField(max_length=8)),
            ("operation_id", models.CharField(max_length=128)),
            ("chunk_index", models.PositiveBigIntegerField(blank=True, null=True)),
            ("body_digest", models.CharField(max_length=64)),
            ("base_revision", models.PositiveBigIntegerField(default=0)),
            ("result_revision", models.PositiveBigIntegerField(default=0)),
            ("status", models.CharField(default="succeeded", max_length=16)),
            ("accepted_items", models.PositiveBigIntegerField(default=0)),
            ("accepted_distinct", models.PositiveBigIntegerField(default=0)),
            ("next_chunk", models.PositiveBigIntegerField(default=0)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("state", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="receipts", to="reports.annotationfilterstate")),
        ], options={"constraints": [
            models.UniqueConstraint(fields=("state", "operation_id"), name="anno_filter_receipt_op"),
            models.UniqueConstraint(fields=("state", "chunk_index"), condition=models.Q(kind="append"), name="anno_filter_chunk_receipt"),
        ]}),
    ]
