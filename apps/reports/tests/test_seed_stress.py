"""seed_stress_reports: DEBUG gate, marker safety, scan mapping, showcase."""
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.reports.management.commands.seed_stress_reports import MARKER
from apps.reports.models import Report

pytestmark = pytest.mark.django_db


def _seed(**opts):
    out, err = StringIO(), StringIO()
    call_command("seed_stress_reports", stdout=out, stderr=err, **opts)
    return out.getvalue(), err.getvalue()


class TestGate:
    def test_refuses_outside_debug(self, settings, data_dir):
        settings.DEBUG = False
        with pytest.raises(CommandError, match="DEBUG is off"):
            call_command("seed_stress_reports")


class TestProfiles:
    @pytest.fixture(autouse=True)
    def _debug(self, settings, data_dir):
        settings.DEBUG = True
        pytest.importorskip("trellum.runner")  # scan needs the framework

    def test_writes_marked_reports_and_scan_registers_them(self, studio_tree):
        _seed(org=studio_tree.org.slug, studio=studio_tree.slug,
              profiles="sleep,memory,timeout", count_per_profile=2)

        reports_dir = studio_tree.reports_dir
        dirs = sorted(p.name for p in reports_dir.iterdir() if p.is_dir())
        # scaled profiles get N variants, failure profiles just one
        assert "stress-sleep-01" in dirs and "stress-sleep-02" in dirs
        assert "stress-timeout-01" in dirs and "stress-timeout-02" not in dirs
        for d in dirs:
            assert MARKER in (reports_dir / d / "report.yaml").read_text(encoding="utf-8")

        assert Report.objects.filter(studio=studio_tree, present_in_scan=True).count() == len(dirs)

    def test_generated_generators_compile(self, studio_tree):
        _seed(org=studio_tree.org.slug, studio=studio_tree.slug)
        for gen in studio_tree.reports_dir.glob("stress-*/generator.py"):
            compile(gen.read_text(encoding="utf-8"), str(gen), "exec")

    def test_remove_spares_unmarked_reports(self, studio_tree, write_report):
        write_report("real-report")
        _seed(org=studio_tree.org.slug, studio=studio_tree.slug, profiles="failing")
        _seed(org=studio_tree.org.slug, studio=studio_tree.slug, remove=True)
        names = {p.name for p in studio_tree.reports_dir.iterdir() if p.is_dir()}
        assert "real-report" in names
        assert not any(n.startswith("stress-") for n in names)

    def test_unknown_profile_rejected(self, studio_tree):
        with pytest.raises(CommandError, match="unknown profile"):
            _seed(org=studio_tree.org.slug, studio=studio_tree.slug, profiles="explode")

    def test_creates_missing_org_and_studio(self, data_dir):
        _seed(org="sim-99", studio="stress", profiles="failing")
        assert Report.objects.filter(
            studio__org__slug="sim-99", studio__slug="stress"
        ).exists()

    def test_warns_when_datasource_missing(self, studio_tree):
        _out, err = _seed(org=studio_tree.org.slug, studio=studio_tree.slug,
                          profiles="dbscan")
        assert "seed_synthetic_datasource" in err


class TestShowcase:
    @pytest.fixture(autouse=True)
    def _debug(self, settings, data_dir):
        settings.DEBUG = True
        pytest.importorskip("trellum.runner")

    def test_seeds_twenty_categorised_reports(self, studio_tree):
        _seed(org=studio_tree.org.slug, studio=studio_tree.slug, showcase=True)
        rows = Report.objects.filter(studio=studio_tree, present_in_scan=True)
        assert rows.count() == 20
        assert rows.exclude(category="Stress").count() == 20  # real categories
        for gen in studio_tree.reports_dir.glob("*/generator.py"):
            compile(gen.read_text(encoding="utf-8"), str(gen), "exec")
