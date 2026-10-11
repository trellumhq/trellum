"""Older application code must keep writing after the schema expands."""
import json

import pytest
from django.db import connection
from django.utils import timezone

from apps.alerts.models import AlertRun

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("recipients", [[], ["historical@test.example"]])
def test_previous_release_insert_omitting_new_columns_preserves_uncertainty(rule, recipients):
    with connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO alerts_alertrun
               (rule_id, started_at, finished_at, status, decision, title, message,
                evidence, cost_usd, delivered_to, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            [rule.pk, timezone.now(), None, "ok", "alert", "Historical alert", "",
             "{}", 0, json.dumps(recipients), ""],
        )
        run_id = cursor.fetchone()[0]
    run = AlertRun.objects.get(pk=run_id)
    assert run.execution_mode is None and run.history_namespace is None
    assert run.delivery_status is None and run.failed_to is None
    assert not run.budget_warning_sent
    assert run.delivered_to == recipients
    assert run.is_legacy
    assert run.get_delivery_status_display() == "Legacy: delivery uncertain"
