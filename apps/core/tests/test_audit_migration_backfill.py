"""Migration 0011's backfill: pure column population over rows that predate
`category`/`outcome`, spot-checked against representative legacy shapes.

The migration module's name starts with a digit, so it is loaded the same
way Django's own migration executor loads it -- importlib, by string name --
rather than a normal import statement. The backfill function only touches
columns apps.core.models.AuditLog already has today (it runs as migration
0011, immediately after 0010 adds them), so exercising it against the live
model via django.apps.apps is a faithful, much simpler stand-in for a full
historical-state migration test -- this is the first data migration in the
repository (docs/MIGRATIONS.md), and there is no heavier test harness for it
here yet.
"""
import importlib

import pytest
from django.apps import apps as django_apps

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def _backfill():
    module = importlib.import_module(
        "apps.core.migrations.0011_audit_backfill_category_outcome"
    )
    return module.backfill


class TestBackfillCategory:
    def test_a_mutation_row_gets_its_category(self):
        row = AuditLog.objects.create(org=None, action="member.invite")
        assert row.category == ""  # the pre-backfill default
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.category == "authz"
        assert row.outcome == "success"  # untouched -- not one of the two shapes

    def test_an_unrecognized_legacy_action_gets_the_fallback_category(self):
        row = AuditLog.objects.create(org=None, action="something.nobody_remembers")
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.category == "system"  # apps.core.audit_actions' default fallback

    def test_already_categorized_rows_are_left_alone(self):
        """Idempotency: a row written after 0010 landed (so it already has a
        real category from apps.core.audit.audit()) must not be re-touched
        by a resumed/re-run backfill."""
        row = AuditLog.objects.create(org=None, action="member.invite", category="authz")
        AuditLog.objects.filter(pk=row.pk).update(category="admin")  # deliberately "wrong"
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.category == "admin"  # untouched, because category != ""


class TestBackfillOutcome:
    def test_login_failed_becomes_failure(self):
        row = AuditLog.objects.create(
            org=None, action="auth.login_failed", metadata={"attempted": "x@y.com"},
        )
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.category == "auth"
        assert row.outcome == "failure"
        assert row.metadata == {"attempted": "x@y.com"}  # untouched shape

    def test_live_query_error_becomes_failure_and_the_metadata_key_is_lifted(self):
        row = AuditLog.objects.create(
            org=None, action="report.live_query",
            metadata={"query_id": "q1", "elapsed_ms": 12, "outcome": "error"},
        )
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.outcome == "failure"
        assert row.metadata == {"query_id": "q1", "elapsed_ms": 12}

    def test_live_query_timeout_becomes_failure_and_the_metadata_key_is_lifted(self):
        row = AuditLog.objects.create(
            org=None, action="report.live_query",
            metadata={"query_id": "q1", "elapsed_ms": 5000, "outcome": "timeout"},
        )
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.outcome == "failure"
        assert "outcome" not in row.metadata

    def test_live_query_ok_is_left_as_the_success_default(self):
        """Only the two known non-success shapes are lifted -- a legacy "ok"
        row was already the default and needs no correction, so its
        metadata is untouched rather than guessed at."""
        row = AuditLog.objects.create(
            org=None, action="report.live_query",
            metadata={"query_id": "q1", "outcome": "ok"},
        )
        _backfill()(django_apps, None)
        row.refresh_from_db()
        assert row.outcome == "success"
        assert row.metadata["outcome"] == "ok"  # not stripped -- only error/timeout are


class TestBackfillIsBatchedAndComplete:
    def test_every_uncategorized_row_gets_touched_across_batches(self, monkeypatch):
        module = importlib.import_module(
            "apps.core.migrations.0011_audit_backfill_category_outcome"
        )
        monkeypatch.setattr(module, "BATCH_SIZE", 3)  # force multiple batches
        for i in range(10):
            AuditLog.objects.create(org=None, action="member.invite", target_id=str(i))

        module.backfill(django_apps, None)
        assert not AuditLog.objects.filter(category="").exists()
        assert AuditLog.objects.filter(category="authz").count() == 10
