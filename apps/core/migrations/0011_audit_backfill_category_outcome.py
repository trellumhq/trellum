# The first data migration in this repository (docs/MIGRATIONS.md has no
# precedent to follow, so this sets the pattern: batched, idempotent, and
# resumable).
#
# Pure column population over rows AuditLog already has -- no event history is
# invented. Two things happen per row:
#
#   category  from apps.core.audit_actions' action -> category map (the same
#             mapping apps.core.audit.audit() uses for every row written from
#             here on), so a legacy row and a fresh one land in the same
#             category pill.
#   outcome   left at its "success" default except the two shapes that were
#             already recording a failure, just not in a column:
#               auth.login_failed                          -> "failure"
#             report.live_query with metadata.outcome in ("error", "timeout")
#                                                            -> "failure",
#             and that metadata key is stripped once it has become the column.
#
# Batched by primary key, not by a single UPDATE ... WHERE action = X: a big
# customer's AuditLog is the one table in this product explicitly documented
# as "only ever grows" (apps/core/models.py), so this has to behave on a table
# a lot larger than anything in this repository's own history.
#
# Idempotent and resumable: only rows with category="" (the field's
# pre-backfill default) are touched, so re-running after an interruption picks
# up exactly where it left off and touches nothing already done.
from __future__ import annotations

from django.db import migrations

BATCH_SIZE = 2000


def _category_for(action: str) -> str:
    # A plain-data mapping, not a model -- safe to import directly even though
    # this is a migration (see docs/MIGRATIONS.md's "never import models
    # directly" rule, which is about model *shape*, not a static dict). Using
    # the live registry rather than freezing a copy here means a legacy row
    # gets the best categorization this codebase currently knows, which is
    # what "not inventing history, just labeling what happened" wants.
    from apps.core.audit_actions import category_for

    return category_for(action, debug=False)


def backfill(apps, schema_editor):
    AuditLog = apps.get_model("core", "AuditLog")
    qs = AuditLog.objects.filter(category="").order_by("pk")

    while True:
        batch = list(qs[:BATCH_SIZE])
        if not batch:
            return
        for row in batch:
            row.category = _category_for(row.action)
            if row.action == "auth.login_failed":
                row.outcome = "failure"
            elif row.action == "report.live_query" and isinstance(row.metadata, dict):
                lifted = row.metadata.get("outcome")
                if lifted in ("error", "timeout"):
                    row.outcome = "failure"
                    row.metadata = {k: v for k, v in row.metadata.items() if k != "outcome"}
        AuditLog.objects.bulk_update(batch, ["category", "outcome", "metadata"])


def noop_reverse(apps, schema_editor):
    # Forward-only by policy (docs/MIGRATIONS.md): there is no meaningful
    # "un-categorize" and nothing downstream reads category/outcome until this
    # release ships, so reversing would only discard information for free.
    pass


class Migration(migrations.Migration):

    atomic = False  # each batch commits on its own; no single giant lock

    dependencies = [
        ('core', '0010_audit_feature_columns'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
