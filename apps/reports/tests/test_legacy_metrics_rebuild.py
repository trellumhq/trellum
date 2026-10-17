"""The generated Metrics report is rebuilt once, by the coordinator, after the 0.8 upgrade.

``metrics_workspace_rebuild.run_once`` is called when the coordinator starts. The
first call queues one build per eligible Metrics report and records the outcome
in ``OpsState``; every later call (a restart included) does nothing.
"""
import pytest
from django.utils import timezone

from apps.core.models import AuditLog, OpsState
from apps.reports import metrics_workspace_rebuild as rebuild
from apps.reports.models import Report
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


def metrics(studio, **fields):
    fields.setdefault("tags", ["metrics"])
    return Report.objects.create(studio=studio, slug="metrics", **fields)


def runs(row):
    return list(Run.objects.filter(report=row).order_by("created_at"))


def audits():
    return AuditLog.objects.filter(action="run.auto_enqueue")


def test_first_start_queues_one_audited_rebuild_and_records_it(studio):
    row = metrics(studio)
    assert rebuild.run_once() == 1
    [run] = runs(row)
    assert (run.status, run.trigger, run.requested_by, run.slug, run.studio_id) == (
        Run.QUEUED, "catchup", None, "metrics", studio.pk)
    [entry] = audits()
    assert (entry.category, entry.actor, entry.org_id) == ("system", None, studio.org_id)
    assert entry.metadata["slug"] == "metrics" and entry.metadata["report_id"] == row.pk
    state = OpsState.objects.get(key="metrics_workspace_rebuild")
    assert state.ok and state.payload == {"workspace_version": 1, "queued": 1}


def test_a_second_call_and_a_restart_do_nothing(studio):
    row = metrics(studio)
    assert rebuild.run_once() == 1
    Run.objects.filter(report=row).update(status=Run.SUCCESS, finished_at=timezone.now())
    assert rebuild.run_once() is None  # the same process again
    assert rebuild.run_once() is None  # and a coordinator that restarted
    assert len(runs(row)) == 1 and audits().count() == 1
    assert OpsState.objects.get(key="metrics_workspace_rebuild").payload["queued"] == 1


def test_a_report_that_appears_later_is_not_built(studio):
    assert rebuild.run_once() == 0
    row = metrics(studio)
    assert rebuild.run_once() is None
    assert not runs(row) and not audits().exists()


def test_fresh_install_records_that_nothing_was_queued(studio):
    assert rebuild.run_once() == 0
    state = OpsState.objects.get(key="metrics_workspace_rebuild")
    assert state.payload == {"workspace_version": 1, "queued": 0}
    assert not Run.objects.exists() and not audits().exists()


@pytest.mark.parametrize("status", [Run.QUEUED, Run.STARTING, Run.RUNNING])
def test_an_active_run_is_not_duplicated(studio, status):
    row = metrics(studio)
    Run.objects.create(report=row, studio=studio, slug=row.slug, pool=studio.pool,
                       trigger="manual", status=status)
    assert rebuild.run_once() == 0
    assert [run.trigger for run in runs(row)] == ["manual"]
    assert not audits().exists()
    assert OpsState.objects.get(key="metrics_workspace_rebuild").payload["queued"] == 0


@pytest.mark.parametrize("fields", [
    {"refresh_suspended_at": timezone.now()},
    {"disabled": True},
    {"data_expired_at": timezone.now()},
    {"present_in_scan": False},
    {"kind": Report.KIND_ANALYSIS},
    {"tags": []},
    {"tags": ["other"]},
], ids=["idle-suspended", "disabled", "expired", "removed", "analysis", "untagged", "other-tag"])
def test_an_ineligible_metrics_report_is_skipped(studio, fields):
    row = metrics(studio, **fields)
    assert rebuild.run_once() == 0
    assert not runs(row) and not audits().exists()


def test_only_the_generated_metrics_report_is_built(studio):
    metrics(studio)
    other = Report.objects.create(studio=studio, slug="other", tags=["metrics"])
    ordinary = Report.objects.create(studio=studio, slug="ordinary")
    assert rebuild.run_once() == 1
    assert not runs(other) and not runs(ordinary)


def test_every_studio_with_a_metrics_report_gets_one(studio, studio2):
    rows = [metrics(studio), metrics(studio2)]
    assert rebuild.run_once() == 2
    assert [len(runs(row)) for row in rows] == [1, 1]
    assert audits().count() == 2


def test_a_failure_leaves_nothing_recorded_and_is_retried_at_the_next_start(studio, monkeypatch):
    row = metrics(studio)
    monkeypatch.setattr("apps.runner.services.enqueue", lambda *a, **k: 1 / 0)
    assert rebuild.run_once() is None  # logged; the coordinator carries on
    assert not OpsState.objects.filter(key="metrics_workspace_rebuild").exists()
    assert not runs(row) and not audits().exists()
    monkeypatch.undo()
    assert rebuild.run_once() == 1
    assert len(runs(row)) == 1
