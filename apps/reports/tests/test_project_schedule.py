"""Repository scans propagate inherited schedules into the worker registry."""

import pytest
import yaml

from apps.reports.models import Report
from apps.reports.scan import sync_studio_registry

pytestmark = pytest.mark.django_db


def test_project_schedule_changes_reach_registry_on_next_sync(studio_tree, write_report, monkeypatch):
    write_report("metrics", schedule={"project": "metrics"})
    write_report("explicit", schedule={"cron": "0 9 * * *", "timezone": "UTC"})
    write_report("manual")
    path = studio_tree.reports_dir.parent / "config.yaml"
    monkeypatch.setenv("FW_PROJECT_ROOT", "/not-this-studio")

    def configure(build_time, zone="UTC"):
        path.write_text(yaml.safe_dump({"reporting": {
            "timezone": zone, "day_start": "04:00", "metrics_build_time": build_time,
        }}))
        assert sync_studio_registry(studio_tree) == 3
        return Report.objects.get(studio=studio_tree, slug="metrics")

    metrics = configure("07:00")
    assert (metrics.schedule_cron, metrics.schedule_timezone) == ("0 7 * * *", "UTC")
    metrics = configure("08:15", "Europe/Amsterdam")
    assert (metrics.schedule_cron, metrics.schedule_timezone) == ("15 8 * * *", "Europe/Amsterdam")
    metrics = configure(None)
    assert metrics.schedule_cron == ""
    assert Report.objects.get(studio=studio_tree, slug="explicit").schedule_cron == "0 9 * * *"
    assert Report.objects.get(studio=studio_tree, slug="manual").schedule_cron == ""


def test_invalid_project_schedule_does_not_replace_registered_schedule(studio_tree, write_report):
    write_report("metrics", schedule={"project": "metrics"})
    path = studio_tree.reports_dir.parent / "config.yaml"
    path.write_text('reporting:\n  metrics_build_time: "07:00"\n')
    sync_studio_registry(studio_tree)
    path.write_text('reporting:\n  metrics_build_time: "25:00"\n')
    with pytest.raises(ValueError, match="metrics_build_time"):
        sync_studio_registry(studio_tree)
    assert Report.objects.get(studio=studio_tree, slug="metrics").schedule_cron == "0 7 * * *"
