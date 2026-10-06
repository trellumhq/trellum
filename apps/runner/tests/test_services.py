"""Queue semantics: dedupe, stop, state — the legacy enqueue contract."""
import pytest

from apps.runner.models import Run
from apps.runner.services import enqueue, report_state, request_stop

pytestmark = pytest.mark.django_db


class TestEnqueue:
    def test_enqueue_creates_queued_run(self, report_row, org_admin):
        assert enqueue(report_row, user=org_admin) == "queued"
        run = Run.objects.get(report=report_row)
        assert run.status == Run.QUEUED
        assert run.priority == report_row.priority == 5
        assert run.requested_by == org_admin

    def test_duplicate_queued_is_deduped(self, report_row):
        assert enqueue(report_row) == "queued"
        assert enqueue(report_row) == "queued"
        assert Run.objects.filter(report=report_row).count() == 1

    def test_running_reports_already_running(self, report_row):
        enqueue(report_row)
        Run.objects.filter(report=report_row).update(status=Run.RUNNING)
        assert enqueue(report_row) == "already_running"
        assert Run.objects.filter(report=report_row).count() == 1

    def test_finished_run_allows_new_enqueue(self, report_row):
        enqueue(report_row)
        Run.objects.filter(report=report_row).update(status=Run.SUCCESS)
        assert enqueue(report_row) == "queued"
        assert Run.objects.filter(report=report_row).count() == 2

    def test_invalid_cache_mode_normalized(self, report_row):
        enqueue(report_row, cache_mode="nonsense")
        assert Run.objects.get(report=report_row).cache_mode == "normal"


class TestStop:
    def test_stop_queued_run_directly(self, report_row):
        enqueue(report_row)
        assert request_stop(report_row) is True
        assert Run.objects.get(report=report_row).status == Run.STOPPED

    def test_stop_running_flags_for_worker(self, report_row):
        enqueue(report_row)
        Run.objects.filter(report=report_row).update(status=Run.RUNNING)
        assert request_stop(report_row) is True
        run = Run.objects.get(report=report_row)
        assert run.status == Run.RUNNING  # the worker owns the transition
        assert run.stop_requested is True

    def test_stop_idle_returns_false(self, report_row):
        assert request_stop(report_row) is False


class TestReportState:
    def test_idle(self, report_row):
        assert report_state(report_row) == ("idle", None)

    def test_queued(self, report_row):
        enqueue(report_row)
        assert report_state(report_row) == ("queued", None)

    def test_running_with_elapsed(self, report_row):
        from django.utils import timezone

        enqueue(report_row)
        Run.objects.filter(report=report_row).update(
            status=Run.RUNNING, started_at=timezone.now() - timezone.timedelta(seconds=30)
        )
        state, elapsed = report_state(report_row)
        assert state == "running"
        assert elapsed >= 29
