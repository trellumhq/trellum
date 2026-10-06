"""simulate_load: smoke, summary math, cleanup safety."""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.orgs.models import Organization
from apps.reports.models import Report
from apps.runner.management.commands.simulate_load import _jain
from apps.runner.models import Run
from apps.studios.models import Studio

pytestmark = pytest.mark.django_db


def _run(**opts):
    out, err = StringIO(), StringIO()
    call_command("simulate_load", stdout=out, stderr=err, **opts)
    return out.getvalue(), err.getvalue()


def _mk_sim_org(slug: str) -> Studio:
    org = Organization.objects.create(slug=slug, name=slug)
    return Studio.objects.create(org=org, slug="stress", name="Stress")


def _mk_run(studio, *, wait_s: float, status=Run.SUCCESS, duration_s: float = 10.0):
    created = timezone.now() - timedelta(minutes=30)
    report, _ = Report.objects.get_or_create(
        studio=studio, slug=f"r-{Report.objects.count()}", defaults={"name": "r"}
    )
    started = created + timedelta(seconds=wait_s)
    return Run.objects.create(
        report=report, studio=studio, slug=report.slug, status=status,
        created_at=created, started_at=started,
        finished_at=started + timedelta(seconds=duration_s),
    )


class TestGate:
    def test_refuses_outside_debug(self, settings):
        settings.DEBUG = False
        with pytest.raises(CommandError, match="DEBUG is off"):
            call_command("simulate_load")


class TestSmoke:
    def test_setup_and_drive_no_wait(self, settings, data_dir):
        settings.DEBUG = True
        pytest.importorskip("trellum.runner")
        out, _err = _run(
            orgs=2, reports_per_org=4, profile_mix="sleep:2,failing:1",
            bursts=1, burst_size=5, burst_interval=0, no_wait=True,
        )
        assert Organization.objects.filter(slug="sim-01").exists()
        assert Organization.objects.filter(slug="sim-02").exists()
        assert Run.objects.filter(studio__org__slug__startswith="sim-").count() > 0
        assert "simulate_load summary" in out
        assert "per-org fairness" in out


class TestSummaryMath:
    def test_since_mode_percentiles_and_jain(self, settings, data_dir):
        settings.DEBUG = True
        s1 = _mk_sim_org("sim-01")
        s2 = _mk_sim_org("sim-02")
        for _ in range(5):
            _mk_run(s1, wait_s=10.5)
            _mk_run(s2, wait_s=10.5)

        since = (timezone.now() - timedelta(hours=1)).isoformat()
        out, _ = _run(since=since)
        assert "runs in window: 10 (completed 10)" in out
        assert "p50 10.5s" in out
        # fractional max wait survives (Extract must not truncate to int)
        assert "10.5s" in out.split("per-org fairness:")[1]
        # symmetric orgs: perfectly fair
        assert "Jain index (avg wait)  : 1.0" in out
        assert "Jain index (completions): 1.0" in out

    def test_jain_flags_skew(self):
        assert _jain([10.0, 10.0]) == 1.0
        assert _jain([2.0, 18.0]) < 0.7
        assert _jain([]) is None

    def test_since_rejects_garbage(self, settings):
        settings.DEBUG = True
        with pytest.raises(CommandError, match="ISO timestamp"):
            call_command("simulate_load", since="yesterday-ish")


class TestCleanup:
    def test_removes_only_sim_nn_orgs(self, settings, data_dir):
        settings.DEBUG = True
        s1 = _mk_sim_org("sim-01")
        _mk_run(s1, wait_s=1.0)
        keeper = Organization.objects.create(slug="sim-acme", name="Sim Acme")

        out, _ = _run(cleanup=True)
        assert "removed sim-01" in out
        assert not Organization.objects.filter(slug="sim-01").exists()
        assert Organization.objects.filter(pk=keeper.pk).exists()
        assert Run.objects.count() == 0
