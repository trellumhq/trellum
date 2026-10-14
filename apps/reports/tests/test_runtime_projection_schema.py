"""Upgrade keeps historical facts and queues bounded worker repair."""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from apps.reports.models import ReportSourceSummary, ReportExperimentSummary

pytestmark = pytest.mark.django_db(transaction=True)


def test_extension_upgrade_and_reverse_preserve_history(studio_tree):
    executor = MigrationExecutor(connection)
    leaf = executor.loader.graph.leaf_nodes('reports')
    previous = [('reports', '0027_metric_binding_workspace_layout')]
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        report = apps.get_model('reports', 'Report').objects.create(studio_id=studio_tree.pk, slug='historical')
        old = apps.get_model('reports', 'ReportSourceSummary').objects.create(
            report_id=report.pk, state='ready', source_identity='prior', workspace_version=1,
            generation=7, lease_token='prior-lease', lease_until=timezone.now())
        executor = MigrationExecutor(connection)
        executor.migrate(leaf)
        row = ReportSourceSummary.objects.get(pk=old.pk)
        assert (row.source_identity, row.state, row.workspace_version) == ('prior', 'ready', 1)
        assert row.schema_version == 2 and row.generation == 8 and not row.lease_token
        assert row.runtime_state == row.experiment_state == 'pending'
        assert row.has_output is None and row.has_live_queries is None
        assert not ReportExperimentSummary.objects.exists()
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        assert apps.get_model('reports', 'ReportSourceSummary').objects.get(pk=old.pk).source_identity == 'prior'
    finally:
        MigrationExecutor(connection).migrate(leaf)
