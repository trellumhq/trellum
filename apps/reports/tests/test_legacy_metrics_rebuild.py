"""A legacy Metrics workspace is rebuilt once, automatically, by background reconciliation.

Design under test: the attempt is recorded in ``legacy_rebuild_queued_for`` (the
artifact identity it acted on) in the same transaction as the queued run. While
it is set nothing more is queued, even if the artifact is replaced by another
legacy one (a runner still on the old image); only publishing a non-legacy
workspace clears it, and then a regression is owed one new attempt.
"""
import json
import os
import time

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.reports.models import Report, ReportSourceSummary
from apps.reports.source_reconciliation import run_batch
from apps.reports.source_summary import (
    begin_attempt,
    finish_attempt,
    queue_legacy_metrics_rebuild,
    reconcile,
)
from apps.reports.tests.test_source_summary import due, metadata, report
from apps.reports.tests.test_workspace_source_version import generated
from apps.runner.models import Run

pytestmark = pytest.mark.django_db(transaction=True)


def runs(row):
    return list(Run.objects.filter(report=row).order_by('created_at'))


def summary_of(row):
    return ReportSourceSummary.objects.get(report=row)


def finish(row, status=Run.SUCCESS):
    """The queued build ended (without touching the artifact); reconcile is due again."""
    Run.objects.filter(report=row, status__in=Run.ACTIVE_STATUSES).update(
        status=status, finished_at=timezone.now())
    due(row)


def write_artifact(studio, row, version, *names, status='success'):
    data = metadata(*names or ('a',), status=status)
    if version is not None:
        data['metrics_workspace'] = {'version': version}
    directory = studio.output_dir / row.slug
    (directory / '_meta.json').write_text(json.dumps(data))
    (directory / 'data.json').write_text('{}')  # a readable payload lets the stamp settle
    # Artifacts older than the racy window are confirmable from their stamp alone.
    old = time.time() - 60
    for path in directory.iterdir():
        os.utime(path, (old, old))
    os.utime(directory, (old, old))


def observed_legacy(studio, version=None):
    """A Metrics report whose legacy artifact is indexed, with nothing acted on yet.

    This is the state of a portal that learned about the artifact before this
    behaviour existed (or while the report could not be rebuilt).
    """
    row = generated(studio, version)
    write_artifact(studio, row, version)
    Report.objects.filter(pk=row.pk).update(disabled=True)
    result = reconcile(row.pk)
    assert result['status'] == 'ready' and 'legacy_rebuild' not in result
    Report.objects.filter(pk=row.pk).update(disabled=False)
    summary = summary_of(row)
    assert summary.workspace_version == 0 and summary.legacy_rebuild_queued_for is None
    assert summary.change_stamp and not runs(row)
    due(row)
    return row


@pytest.mark.parametrize('version', [None, 0])
def test_legacy_artifact_queues_exactly_one_audited_rebuild(studio_tree, version):
    row = generated(studio_tree, version)
    result = reconcile(row.pk)
    assert result['status'] == 'ready' and result['legacy_rebuild'] == 'queued'
    summary = summary_of(row)
    assert summary.workspace_version == 0 and summary.state == 'ready'
    [run] = runs(row)
    assert (run.status, run.trigger, run.requested_by, run.slug, run.studio_id) == (
        Run.QUEUED, 'catchup', None, 'metrics', studio_tree.pk)
    assert summary.legacy_rebuild_queued_for == summary.source_identity != ''
    entry = AuditLog.objects.get(action='run.auto_enqueue')
    assert (entry.category, entry.actor, entry.org_id) == ('system', None, studio_tree.org_id)
    assert entry.metadata['slug'] == 'metrics' and entry.metadata['report_id'] == row.pk
    assert entry.metadata['artifact'] == summary.source_identity


@pytest.mark.parametrize('ended', [Run.SUCCESS, Run.ERROR, Run.STOPPED])
def test_repeated_passes_never_queue_a_second_rebuild_for_the_same_artifact(studio_tree, ended):
    row = generated(studio_tree, None)
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    for _ in range(3):
        finish(row, ended)
        result = reconcile(row.pk)
        assert result['status'] == 'ready' and 'legacy_rebuild' not in result
    assert len(runs(row)) == 1
    assert AuditLog.objects.filter(action='run.auto_enqueue').count() == 1


def test_background_batches_queue_it_once(studio_tree):
    row = generated(studio_tree, None)
    first = run_batch()
    assert [item['status'] for item in first['results']] == ['ready']
    assert first['results'][0]['legacy_rebuild'] == 'queued'
    finish(row)
    run_batch()
    finish(row)
    run_batch()
    assert len(runs(row)) == 1


def test_stamp_confirmed_legacy_artifact_is_rebuilt_once_too(studio_tree):
    # Indexed by an earlier release that did nothing about it: the next passes
    # only confirm the unchanged artifact and never inspect it again.
    row = observed_legacy(studio_tree)
    result = reconcile(row.pk)
    assert result.get('unchanged') and result['legacy_rebuild'] == 'queued'
    finish(row)
    assert 'legacy_rebuild' not in reconcile(row.pk)
    assert len(runs(row)) == 1


@pytest.mark.parametrize('status', [Run.QUEUED, Run.STARTING, Run.RUNNING])
def test_queued_or_running_build_is_not_duplicated_and_does_not_use_up_the_attempt(studio_tree, status):
    row = observed_legacy(studio_tree)
    Run.objects.create(report=row, studio=studio_tree, slug=row.slug, pool=studio_tree.pool,
                       trigger='manual', status=status)
    assert reconcile(row.pk)['status'] == 'ineligible'  # reconciliation waits for the build
    assert queue_legacy_metrics_rebuild(row.pk) is False
    assert len(runs(row)) == 1 and summary_of(row).legacy_rebuild_queued_for is None
    finish(row)  # it ended and the artifact is still legacy
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    assert [run.trigger for run in runs(row)] == ['manual', 'catchup']


@pytest.mark.parametrize('version', [1, 2])
def test_current_workspace_is_left_alone(studio_tree, version):
    row = generated(studio_tree, version)
    result = reconcile(row.pk)
    assert result['status'] == 'ready' and 'legacy_rebuild' not in result
    assert summary_of(row).workspace_version == version
    assert not runs(row) and not AuditLog.objects.filter(action='run.auto_enqueue').exists()


def test_only_the_generated_metrics_report_is_rebuilt(studio_tree):
    ordinary = report(studio_tree, 'ordinary', meta=metadata('a'))
    untagged = report(studio_tree, 'metrics', meta={**metadata('a'), 'metrics_workspace': {'version': 0}})
    other = report(studio_tree, 'other', meta={**metadata('a'), 'metrics_workspace': {'version': 0}})
    Report.objects.filter(pk=other.pk).update(tags=['metrics'])
    for row in (ordinary, untagged, other):
        assert 'legacy_rebuild' not in reconcile(row.pk)
        # Even forced into a legacy-looking summary, the guard is in the query.
        ReportSourceSummary.objects.filter(report=row).update(workspace_version=0)
        assert queue_legacy_metrics_rebuild(row.pk) is False
    assert not Run.objects.exists()


@pytest.mark.parametrize('report_change,summary_change', [
    ({'disabled': True}, {}),
    ({'present_in_scan': False}, {}),
    ({'data_expired_at': timezone.now()}, {}),
    ({'kind': Report.KIND_ANALYSIS}, {}),
    ({}, {'state': 'uncertain'}),
    ({}, {'state': 'unavailable'}),
    ({}, {'state': 'pending'}),
    ({}, {'state': 'missing'}),
    ({}, {'quality': 'unknown'}),
    ({}, {'workspace_version': None}),
], ids=['disabled', 'removed', 'expired', 'analysis', 'uncertain', 'unavailable', 'pending',
        'missing', 'unknown-quality', 'unknown-layout'])
def test_unhealthy_or_absent_report_is_not_rebuilt(studio_tree, report_change, summary_change):
    row = observed_legacy(studio_tree)
    Report.objects.filter(pk=row.pk).update(**report_change)
    ReportSourceSummary.objects.filter(report=row).update(**summary_change)
    assert queue_legacy_metrics_rebuild(row.pk) is False
    assert not runs(row) and summary_of(row).legacy_rebuild_queued_for is None


def test_failed_rebuild_is_not_retried(studio_tree):
    row = generated(studio_tree, None)
    write_artifact(studio_tree, row, None)
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    [run] = runs(row)
    # The queued build starts, fails and leaves the legacy artifact in place.
    begin_attempt(row.pk, run.pk)
    write_artifact(studio_tree, row, None, status='error')
    finish_attempt(row.pk, run.pk, 'error')
    finish(row, Run.ERROR)
    assert reconcile(row.pk)['status'] == 'uncertain'
    # The failure is later superseded by a successful legacy-format build.
    write_artifact(studio_tree, row, None)
    due(row)
    assert 'legacy_rebuild' not in reconcile(row.pk)
    assert len(runs(row)) == 1


def test_rebuild_that_still_yields_legacy_never_loops_but_current_output_resets_it(studio_tree):
    row = generated(studio_tree, None)
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    attempted = summary_of(row).legacy_rebuild_queued_for
    # A runner image left on the old release publishes a different legacy artifact.
    finish(row)
    write_artifact(studio_tree, row, None, 'b')
    assert 'legacy_rebuild' not in reconcile(row.pk)
    summary = summary_of(row)
    assert summary.workspace_version == 0 and summary.source_identity != attempted
    assert summary.legacy_rebuild_queued_for == attempted  # still the first attempt, no loop
    # Someone fixes the runner: the current workspace ends the episode.
    write_artifact(studio_tree, row, 1, 'b')
    due(row)
    assert 'legacy_rebuild' not in reconcile(row.pk)
    summary = summary_of(row)
    assert summary.workspace_version == 1 and summary.legacy_rebuild_queued_for is None
    # Only a later regression to legacy output is owed one new attempt.
    write_artifact(studio_tree, row, None, 'c')
    due(row)
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    assert len(runs(row)) == 2
    assert AuditLog.objects.filter(action='run.auto_enqueue').count() == 2


def test_a_queueing_failure_does_not_fail_publication_or_use_up_the_attempt(studio_tree, monkeypatch):
    row = generated(studio_tree, None)
    monkeypatch.setattr('apps.runner.services.enqueue', lambda *a, **k: 1 / 0)
    result = reconcile(row.pk)
    assert result['status'] == 'ready' and 'legacy_rebuild' not in result
    summary = summary_of(row)
    assert summary.state == 'ready' and summary.workspace_version == 0
    assert summary.legacy_rebuild_queued_for is None
    assert not runs(row) and not AuditLog.objects.filter(action='run.auto_enqueue').exists()
    monkeypatch.undo()
    due(row)
    assert reconcile(row.pk)['legacy_rebuild'] == 'queued'
    assert len(runs(row)) == 1


def test_queueing_reads_only_the_database(studio_tree, monkeypatch):
    from apps.core import storage
    row = observed_legacy(studio_tree)

    def forbidden(*args, **kwargs):
        raise AssertionError('storage must not be touched')

    for name in ('read_summary_artifact', 'summary_change_stamp', 'summary_output_facts', 'read_meta'):
        monkeypatch.setattr(storage, name, forbidden)
    assert queue_legacy_metrics_rebuild(row.pk) is True
    assert len(runs(row)) == 1
