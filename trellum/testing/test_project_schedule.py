"""Project schedules resolve identically for builds and explicit-path scans."""

import pytest
import yaml

from trellum.report import BaseReport
from trellum.runner import scan_report_configs


def project(tmp_path, name, reporting=None):
    root = tmp_path / name
    root.mkdir()
    if reporting is not None:
        (root / "config.yaml").write_text(yaml.safe_dump({"reporting": reporting}))
    return root


def report(root, slug="metrics", schedule=None):
    folder = root / "reports" / slug
    folder.mkdir(parents=True)
    config = {"name": slug, "slug": slug}
    if schedule is not None:
        config["schedule"] = schedule
    (folder / "report.yaml").write_text(yaml.safe_dump(config))
    (folder / "generator.py").write_text("# Discovery must not execute this.\n")
    return folder


def test_build_and_scan_resolve_same_project_schedule(tmp_path, monkeypatch):
    root = project(tmp_path, "configured", {"timezone": "Asia/Tokyo", "day_start": "04:00", "metrics_build_time": "07:15"})
    folder = report(root, schedule={"project": "metrics"})
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path / "wrong-project"))
    original = (folder / "report.yaml").read_bytes()
    expected = {"project": "metrics", "cron": "15 7 * * *", "timezone": "Asia/Tokyo"}
    assert BaseReport.load_config(str(folder))["schedule"] == expected
    assert scan_report_configs(str(root / "reports"))[0]["config"]["schedule"] == expected
    assert (folder / "report.yaml").read_bytes() == original


@pytest.mark.parametrize("cron", ["", "0 9 * * *"])
def test_explicit_cron_overrides_project(tmp_path, cron):
    root = project(tmp_path, "configured", {"timezone": "Asia/Tokyo", "metrics_build_time": "07:00"})
    schedule = {"project": "metrics", "cron": cron, "timezone": "UTC"}
    folder = report(root, schedule=schedule)
    assert BaseReport.load_config(str(folder))["schedule"] == schedule


@pytest.mark.parametrize("reporting", [None, {}, {"metrics_build_time": None}])
def test_unconfigured_or_disabled_project_adds_no_schedule(tmp_path, reporting):
    root = project(tmp_path, "legacy", reporting)
    folder = report(root, schedule={"project": "metrics"})
    assert not BaseReport.load_config(str(folder))["schedule"].get("cron")


def test_ordinary_reports_do_not_inherit(tmp_path):
    root = project(tmp_path, "configured", {"metrics_build_time": "07:00"})
    folder = report(root)
    assert "schedule" not in BaseReport.load_config(str(folder))
    assert "schedule" not in scan_report_configs(str(root / "reports"))[0]["config"]


def test_scan_reads_settings_once_and_keeps_projects_separate(tmp_path, monkeypatch):
    from trellum import reporting_day

    first = project(tmp_path, "first", {"metrics_build_time": "07:00"})
    second = project(tmp_path, "second", {"metrics_build_time": "08:30", "timezone": "Europe/Amsterdam"})
    for root in (first, second):
        report(root, "metrics", {"project": "metrics"})
        report(root, "other-metrics", {"project": "metrics"})
    reads = []
    load = reporting_day.load_reporting_settings

    def tracked(root):
        reads.append(root)
        return load(root)

    monkeypatch.setattr(reporting_day, "load_reporting_settings", tracked)
    for root, cron, zone in ((first, "0 7 * * *", "UTC"), (second, "30 8 * * *", "Europe/Amsterdam")):
        entries = scan_report_configs(str(root / "reports"))
        assert all(entry["config"]["schedule"]["cron"] == cron for entry in entries)
        assert all(entry["config"]["schedule"]["timezone"] == zone for entry in entries)
    assert reads == [first, second]


def test_unknown_project_schedule_is_rejected(tmp_path):
    root = project(tmp_path, "configured")
    folder = report(root, schedule={"project": "typo"})
    with pytest.raises(ValueError, match="schedule.project"):
        BaseReport.load_config(str(folder))
