"""Workspace layout markers follow the served source identity and fences."""
import json

import pytest
from django.utils import timezone
from django.db import connection

from apps.reports.models import MetricCatalogState, Report, ReportSourceSummary
from apps.reports.source_summary import begin_attempt, finish_attempt, reconcile
from apps.reports.tests.test_source_summary import report, metadata, due

pytestmark = pytest.mark.django_db(transaction=True)


def generated(studio, version=1):
    data = metadata('a')
    if version is not None:
        data['metrics_workspace'] = {'version': version}
    row = report(studio, slug='metrics', meta=data)
    row.tags = ['metrics']; row.save(update_fields=['tags'])
    return row


def test_historical_unknown_becomes_observed_legacy_only_after_inspection(studio_tree):
    row = generated(studio_tree, None)
    summary = ReportSourceSummary.objects.create(report=row)
    assert summary.workspace_version is None
    assert reconcile(row.pk)['status'] == 'ready'
    summary.refresh_from_db()
    assert summary.workspace_version == 0 and summary.quality == 'observed'


def test_observed_paged_layout_and_format_only_change_advance_summary_revision(studio_tree):
    row = generated(studio_tree)
    assert reconcile(row.pk)['status'] == 'ready'
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.workspace_version == 1 and summary.quality == 'observed'
    revision = MetricCatalogState.objects.get(studio=studio_tree).summary_revision
    path = studio_tree.output_dir / row.slug / '_meta.json'
    data = json.loads(path.read_text()); data['metrics_workspace']['version'] = 2
    path.write_text(json.dumps(data)); due(row)
    assert reconcile(row.pk)['status'] == 'ready'
    summary.refresh_from_db(); assert summary.workspace_version == 2
    assert MetricCatalogState.objects.get(studio=studio_tree).summary_revision == revision + 1


@pytest.mark.parametrize('version', [True, False, '1', 1.0, -1, 32768, None])
def test_invalid_workspace_version_retains_prior_format_and_claims(studio_tree, version):
    row = generated(studio_tree)
    reconcile(row.pk)
    path = studio_tree.output_dir / row.slug / '_meta.json'
    data = metadata('replacement'); data['metrics_workspace'] = {'version': version}
    path.write_text(json.dumps(data)); due(row)
    assert reconcile(row.pk)['status'] == 'unavailable'
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.workspace_version == 1
    assert list(summary.claims.values_list('metric_name', flat=True)) == ['a']


@pytest.mark.parametrize('marker', [None, True, [], 'paged', {}])
def test_malformed_marker_is_not_reported_as_legacy(studio_tree, marker):
    row = generated(studio_tree)
    path = studio_tree.output_dir / row.slug / '_meta.json'
    data = metadata('a'); data['metrics_workspace'] = marker; path.write_text(json.dumps(data))
    assert reconcile(row.pk)['status'] == 'unavailable'
    assert ReportSourceSummary.objects.get(report=row).workspace_version is None


def test_failure_and_missing_source_do_not_claim_fresh_paged_format(studio_tree):
    row = generated(studio_tree)
    reconcile(row.pk)
    begin_attempt(row.pk, 'external-failed-build')
    path = studio_tree.output_dir / row.slug / '_meta.json'
    path.write_text(json.dumps({**metadata('partial', status='error'), 'metrics_workspace': {'version': 2}}))
    finish_attempt(row.pk, 'external-failed-build', 'error')
    assert reconcile(row.pk)['status'] == 'uncertain'
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.workspace_version == 1 and summary.state == 'uncertain'
    path.unlink(); due(row)
    assert reconcile(row.pk)['status'] == 'uncertain'  # Failed mutable attempt is not a verified empty build.
    summary.refresh_from_db(); assert summary.workspace_version == 1


def test_new_attempt_fences_workspace_format_publication(studio_tree, monkeypatch):
    from apps.reports import source_summary
    row = generated(studio_tree)
    original = source_summary.inspect_source
    def obsolete(*args):
        result = original(*args); begin_attempt(row.pk, 'new-build'); return result
    monkeypatch.setattr(source_summary, 'inspect_source', obsolete)
    assert reconcile(row.pk)['status'] == 'superseded'
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.workspace_version is None and summary.state == 'pending'


def test_layout_marker_uses_existing_io_without_transaction(studio_tree, monkeypatch):
    from apps.core import storage
    row = generated(studio_tree)
    original = storage.read_summary_artifact
    reads = []
    def observed(*args, **kwargs):
        assert not connection.in_atomic_block
        reads.append(args[2]); return original(*args, **kwargs)
    monkeypatch.setattr(storage, 'read_summary_artifact', observed)
    assert reconcile(row.pk)['status'] == 'ready'
    assert reads.count('_meta.json') == 2
    assert reads.count('data.json') == 1


def test_remote_layout_comes_from_served_pointer_and_pointer_race_retains_it(studio_tree, monkeypatch):
    from apps.core import storage
    row = generated(studio_tree)
    served = metadata('served'); served['metrics_workspace'] = {'version': 1}
    attempt = metadata('partial', status='error'); attempt['metrics_workspace'] = {'version': 2}
    monkeypatch.setattr(storage, 'is_remote', lambda: True)
    monkeypatch.setattr(storage, 'summary_build_identity', lambda *args: 'published')
    def read(studio, slug, name, **kwargs):
        value = {} if name == 'data.json' else attempt if kwargs.get('mutable_status') else served
        return json.dumps(value).encode()
    monkeypatch.setattr(storage, 'read_summary_artifact', read)
    assert reconcile(row.pk)['status'] == 'ready'
    summary = ReportSourceSummary.objects.get(report=row)
    assert summary.workspace_version == 1 and summary.source_identity == 'published'
    assert summary.attempt_status == 'error'
    assert list(summary.claims.values_list('metric_name', flat=True)) == ['served']
    due(row)
    pointers = iter(['published', 'replacement'])
    monkeypatch.setattr(storage, 'summary_build_identity', lambda *args: next(pointers))
    assert reconcile(row.pk)['status'] == 'unavailable'
    summary.refresh_from_db()
    assert summary.workspace_version == 1 and summary.source_identity == 'published'
    assert list(summary.claims.values_list('metric_name', flat=True)) == ['served']


def test_binding_and_layout_upgrade_preserves_historical_unknowns(studio_tree):
    from django.db.migrations.executor import MigrationExecutor
    from apps.reports.models import MetricDefinition
    row = report(studio_tree, 'migration-history')
    previous = [('reports', '0026_source_summaries')]
    try:
        executor = MigrationExecutor(connection); executor.migrate(previous)
        old = executor.loader.project_state(previous).apps
        old.get_model('reports', 'MetricDefinition').objects.create(studio_id=studio_tree.pk, name='historical')
        old.get_model('reports', 'ReportSourceSummary').objects.create(report_id=row.pk)
        executor = MigrationExecutor(connection)
        executor.migrate([('reports', '0027_metric_binding_workspace_layout')])
        assert MetricDefinition.objects.get(studio=studio_tree, name='historical').binding is None
        assert ReportSourceSummary.objects.get(report=row).workspace_version is None
        assert Report.objects.filter(pk=row.pk).exists()
        executor = MigrationExecutor(connection); executor.migrate(previous)
        columns = {column.name for column in connection.introspection.get_table_description(connection.cursor(), 'reports_reportsourcesummary')}
        assert 'workspace_version' not in columns
        assert old.get_model('reports', 'MetricDefinition').objects.filter(name='historical').exists()
    finally:
        executor = MigrationExecutor(connection); executor.migrate(executor.loader.graph.leaf_nodes('reports'))
