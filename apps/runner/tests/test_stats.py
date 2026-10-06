"""Run statistics: SQL percentiles, NULL tolerance, per-slug rollups."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.runner import stats
from apps.runner.models import Run

pytestmark = pytest.mark.django_db


@pytest.fixture
def make_terminal_run(report_row):
    """Terminal run with exact, hand-picked timestamps."""
    base = timezone.now() - timedelta(hours=1)

    def _make(
        duration_s: float = 10.0,
        wait_s: float = 2.0,
        status: str = Run.SUCCESS,
        peak_mb: int | None = None,
        offset_s: float = 0.0,
        slug: str | None = None,
        age_days: int = 0,
    ):
        created = base + timedelta(seconds=offset_s) - timedelta(days=age_days)
        started = created + timedelta(seconds=wait_s)
        return Run.objects.create(
            report=report_row,
            studio=report_row.studio,
            slug=slug or report_row.slug,
            status=status,
            created_at=created,
            started_at=started,
            finished_at=started + timedelta(seconds=duration_s),
            peak_memory_mb=peak_mb,
        )

    return _make


class TestSummarize:
    def test_known_series(self, make_terminal_run):
        # durations 1..20s, 15 successes / 5 errors, even runs peak at 100 MB.
        # Fractional wait guards against integer truncation in the epoch
        # extraction (Extract defaults to an integer output field).
        for i in range(1, 21):
            make_terminal_run(
                duration_s=float(i),
                wait_s=2.5,
                status=Run.SUCCESS if i <= 15 else Run.ERROR,
                peak_mb=100 if i % 2 == 0 else None,
                offset_s=i * 60.0,
            )
        agg = stats.summarize(Run.objects.all())
        assert agg["runs"] == 20
        assert agg["success"] == 15
        assert agg["success_rate"] == 0.75
        assert agg["avg_duration_s"] == pytest.approx(10.5, abs=0.1)
        assert agg["p50_duration_s"] == pytest.approx(10.5, abs=0.1)
        # percentile_cont(0.95) over 1..20 interpolates to 19.05
        assert agg["p95_duration_s"] == pytest.approx(19.05, abs=0.1)
        assert agg["avg_queue_wait_s"] == pytest.approx(2.5, abs=0.05)
        assert agg["p95_queue_wait_s"] == pytest.approx(2.5, abs=0.05)
        assert agg["avg_peak_memory_mb"] == pytest.approx(100, abs=0.5)
        assert agg["max_peak_memory_mb"] == 100

    def test_empty_queryset(self, db):
        agg = stats.summarize(Run.objects.none())
        assert agg["runs"] == 0
        assert agg["success_rate"] is None
        assert agg["p95_duration_s"] is None
        assert agg["max_peak_memory_mb"] is None

    def test_all_null_peaks(self, make_terminal_run):
        make_terminal_run(peak_mb=None)
        agg = stats.summarize(Run.objects.all())
        assert agg["runs"] == 1
        assert agg["avg_peak_memory_mb"] is None
        assert agg["max_peak_memory_mb"] is None


class TestReportAggregates:
    def test_window_and_status_counts(self, report_row, make_terminal_run):
        make_terminal_run(status=Run.SUCCESS)
        make_terminal_run(status=Run.TIMEOUT, offset_s=60)
        make_terminal_run(status=Run.SUCCESS, age_days=120)  # outside 90d window
        agg = stats.report_aggregates(report_row)
        assert agg["runs"] == 2
        assert agg["by_status"] == {"success": 1, "timeout": 1}
        assert agg["window_days"] == 90

    def test_active_runs_excluded(self, report_row, make_terminal_run):
        make_terminal_run(status=Run.SUCCESS)
        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.RUNNING, started_at=timezone.now(),
        )
        assert stats.report_aggregates(report_row)["runs"] == 1


class TestStudioRunStats:
    def test_per_slug_rollup_with_last_run(self, report_row, make_terminal_run):
        make_terminal_run(duration_s=10.0, peak_mb=50, offset_s=0)
        make_terminal_run(duration_s=20.0, peak_mb=150, status=Run.ERROR, offset_s=60)
        # a second slug (denormalized; simulates a renamed/other report)
        make_terminal_run(duration_s=5.0, slug="other-report", offset_s=0)

        out = stats.studio_run_stats(report_row.studio)
        main = out[report_row.slug]
        assert main["agg"]["runs"] == 2
        assert main["agg"]["success_rate"] == 0.5
        assert main["agg"]["max_peak_memory_mb"] == 150
        # latest terminal run (offset 60) is the ERROR one
        assert main["last"]["status"] == "error"
        assert main["last"]["duration_s"] == 20.0
        assert main["last"]["peak_memory_mb"] == 150
        assert out["other-report"]["agg"]["runs"] == 1

    def test_active_and_old_runs_excluded(self, report_row, make_terminal_run):
        make_terminal_run(age_days=60)  # outside the 30d ops window
        Run.objects.create(
            report=report_row, studio=report_row.studio, slug=report_row.slug,
            status=Run.QUEUED,
        )
        assert stats.studio_run_stats(report_row.studio) == {}


class TestRunModel:
    def test_queue_wait_seconds(self, make_terminal_run):
        run = make_terminal_run(wait_s=3.5)
        assert run.queue_wait_seconds == 3.5

    def test_history_dict_new_keys(self, make_terminal_run):
        d = make_terminal_run(peak_mb=64).to_history_dict()
        for key in ("created_at", "queue_wait_seconds", "memory_limit_mb", "peak_memory_mb"):
            assert key in d
        assert d["peak_memory_mb"] == 64
