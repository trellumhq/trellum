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
        assert agg["avg_duration_s"] == 8.0
        assert agg["p50_duration_s"] == 8.0
        # percentile_cont(0.95) over successful durations 1..15 is 14.3.
        assert agg["p95_duration_s"] == 14.3
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

    def test_all_failed_durations_have_no_data(self, report_row, make_terminal_run):
        for i, status in enumerate(stats.TERMINAL_STATUSES[1:]):
            make_terminal_run(
                status=status, duration_s=i + 1, wait_s=i + 1,
                peak_mb=100 * (i + 1), offset_s=60 * i,
            )
        agg = stats.report_aggregates(report_row)
        assert agg["runs"] == 4
        assert agg["success"] == 0
        assert agg["success_rate"] == 0.0
        for key in ("avg_duration_s", "p50_duration_s", "p95_duration_s"):
            assert agg[key] is None
        assert agg["avg_queue_wait_s"] == 2.5
        assert agg["p95_queue_wait_s"] == 3.8
        assert agg["avg_peak_memory_mb"] == 250
        assert agg["max_peak_memory_mb"] == 400
        assert agg["by_status"] == {status: 1 for status in stats.TERMINAL_STATUSES[1:]}

    @pytest.mark.parametrize("missing", ["started_at", "both"])  # either None nulls the duration
    @pytest.mark.parametrize("valid_success", [False, True])
    def test_missing_success_timestamps(
        self, report_row, make_terminal_run, missing, valid_success
    ):
        if valid_success:
            make_terminal_run(duration_s=20, wait_s=2, peak_mb=50)
        incomplete = make_terminal_run(duration_s=90, wait_s=4, peak_mb=None, offset_s=60)
        updates = {
            field: None for field in ("started_at", "finished_at")
            if missing in (field, "both")
        }
        Run.objects.filter(pk=incomplete.pk).update(**updates)
        make_terminal_run(
            status=Run.ERROR, duration_s=1, wait_s=10, peak_mb=300, offset_s=120
        )
        agg = stats.report_aggregates(report_row)
        assert agg["runs"] == 2 + valid_success
        assert agg["success"] == 1 + valid_success
        expected = 20.0 if valid_success else None
        for key in ("avg_duration_s", "p50_duration_s", "p95_duration_s"):
            assert agg[key] == expected
        assert agg["max_peak_memory_mb"] == 300
        assert agg["avg_queue_wait_s"] is not None
        out = stats.studio_run_stats(report_row.studio)[report_row.slug]
        assert out["agg"]["avg_duration_s"] == out["agg"]["p95_duration_s"] == expected
        assert out["last"]["status"] == Run.ERROR
        assert out["last"]["duration_s"] == 1.0


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
        assert main["agg"]["avg_duration_s"] == main["agg"]["p95_duration_s"] == 10.0
        assert main["agg"]["avg_peak_memory_mb"] == 100
        assert main["agg"]["max_peak_memory_mb"] == 150
        # latest terminal run (offset 60) is the ERROR one
        assert main["last"]["status"] == "error"
        assert main["last"]["duration_s"] == 20.0
        assert main["last"]["peak_memory_mb"] == 150
        assert out["other-report"]["agg"]["runs"] == 1

    def test_failed_only_rollup(self, report_row, make_terminal_run):
        make_terminal_run(status=Run.TIMEOUT, duration_s=120, peak_mb=256)
        out = stats.studio_run_stats(report_row.studio)[report_row.slug]
        assert out["agg"]["runs"] == 1
        assert out["agg"]["success_rate"] == 0.0
        assert out["agg"]["avg_duration_s"] is None
        assert out["agg"]["p95_duration_s"] is None
        assert out["last"]["status"] == Run.TIMEOUT
        assert out["last"]["duration_s"] == 120.0
        assert out["last"]["peak_memory_mb"] == 256

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


@pytest.mark.parametrize("successful", [False, True])
def test_duration_metrics_through_report_and_studio_apis(
    client, org_admin, report_row, make_terminal_run, successful
):
    if successful:
        make_terminal_run(duration_s=30, wait_s=2, peak_mb=50)
    make_terminal_run(
        status=Run.OOM_KILLED, duration_s=1, wait_s=8, peak_mb=256, offset_s=60
    )
    client.force_login(org_admin)
    prefix = f"/s/{report_row.studio.org.slug}/{report_row.studio.slug}"
    response = client.get(f"{prefix}/api/reports/{report_row.slug}/status")
    assert response.status_code == 200
    report_stats = response.json()["aggregates"]
    assert report_stats["runs"] == 1 + successful
    assert report_stats["success_rate"] == (0.5 if successful else 0.0)
    expected = 30.0 if successful else None
    for key in ("avg_duration_s", "p50_duration_s", "p95_duration_s"):
        assert report_stats[key] == expected
    assert report_stats["avg_queue_wait_s"] == (5.0 if successful else 8.0)
    assert report_stats["max_peak_memory_mb"] == 256
    latest = response.json()["history"][0]
    assert latest["status"] == Run.OOM_KILLED
    assert latest["duration_seconds"] == 1.0
    assert latest["peak_memory_mb"] == 256
    response = client.get(f"{prefix}/api/system/run-stats")
    assert response.status_code == 200
    studio_stats = response.json()["stats"][report_row.slug]
    assert studio_stats["agg"]["runs"] == 1 + successful
    for key in ("avg_duration_s", "p95_duration_s"):
        assert studio_stats["agg"][key] == expected
    assert studio_stats["agg"]["max_peak_memory_mb"] == 256
    assert studio_stats["last"]["status"] == Run.OOM_KILLED
    assert studio_stats["last"]["duration_s"] == 1.0
