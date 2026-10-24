"""Reporting setup and Metrics report scaffolding behavior."""

from __future__ import annotations

import importlib

import pytest
import yaml

from trellum.scaffold import reporting
from trellum.scaffold.init_project import init_project
from trellum.cli.commands.metrics import _scaffold_report

init_module = importlib.import_module("trellum.scaffold.init_project")


def _valid(settings):
    from trellum.reporting_day import ReportingSettings
    return ReportingSettings(**settings["reporting"])


def test_init_prompts_three_questions_and_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    monkeypatch.setattr(init_module, "stdin_is_interactive", lambda: True)
    answers = iter(["Europe/Amsterdam", "04:30", "08:15"])
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return next(answers)
    monkeypatch.setattr("builtins.input", answer)
    init_project(str(tmp_path), prompt=True)
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert data["reporting"] == {
        "timezone": "Europe/Amsterdam", "day_start": "04:30",
        "metrics_build_time": "08:15",
    }
    assert len(prompts) == 3
    assert "today/yesterday" in prompts[1]
    assert "data is ready" in prompts[2]


def test_noninteractive_flags_and_later_reconfiguration(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    reporting.configure_reporting(tmp_path, non_interactive=True,
                                 timezone="Pacific/Auckland", day_start="05:00",
                                 metrics_build_time="09:00")
    reporting.configure_reporting(tmp_path, non_interactive=True,
                                 day_start="06:00")
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["reporting"] == {
        "timezone": "Pacific/Auckland", "day_start": "06:00",
        "metrics_build_time": "09:00",
    }


def test_unrelated_yaml_comments_and_values_are_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    original = "# project note\nname: Example # keep me\nsources:\n  warehouse: {type: duckdb}\nreporting:\n  timezone: UTC # reporting comment\n  day_start: '00:00'\n  metrics_build_time: '07:00'\n"
    path = tmp_path / "config.yaml"
    path.write_text(original)
    reporting.configure_reporting(tmp_path, timezone="Europe/Paris", non_interactive=True)
    updated = path.read_text()
    assert updated.startswith("# project note\nname: Example # keep me\nsources:\n  warehouse: {type: duckdb}\n")
    assert "# project note" in updated
    parsed = yaml.safe_load(updated)
    assert parsed["name"] == "Example"
    assert parsed["sources"] == {"warehouse": {"type": "duckdb"}}
    assert parsed["reporting"]["timezone"] == "Europe/Paris"


def test_malformed_config_does_not_clobber(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    path = tmp_path / "config.yaml"
    path.write_text("name: [unfinished\n")
    before = path.read_bytes()
    with pytest.raises(reporting.ReportingSetupError, match="Malformed"):
        reporting.configure_reporting(tmp_path, non_interactive=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("setting,value", [
    ("timezone", "Mars/Olympus"),
    ("day_start", "25:00"),
    ("metrics_build_time", "7am"),
])
def test_invalid_reporting_value_does_not_clobber(tmp_path, setting, value):
    path = tmp_path / "config.yaml"
    path.write_text("name: preserved\n")
    before = path.read_bytes()
    with pytest.raises(reporting.ReportingSetupError, match="Invalid reporting settings"):
        reporting.configure_reporting(tmp_path, non_interactive=True, **{setting: value})
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", ["", "# project settings\n\n"])
def test_blank_and_comment_only_config_are_supported(monkeypatch, tmp_path, contents):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    path = tmp_path / "config.yaml"
    path.write_text(contents)
    reporting.configure_reporting(tmp_path, non_interactive=True)
    parsed = yaml.safe_load(path.read_text())
    assert parsed["reporting"] == {
        "timezone": "UTC", "day_start": "00:00", "metrics_build_time": "07:00",
    }
    assert path.read_text().startswith(contents.rstrip("\n"))


def test_disabled_metrics_schedule_stays_disabled_on_rerun(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    reporting.configure_reporting(tmp_path, non_interactive=True, no_metrics_schedule=True)
    reporting.configure_reporting(tmp_path, non_interactive=True, day_start="01:00")
    data = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert data["reporting"]["metrics_build_time"] is None


def test_interactive_setup_can_explicitly_reenable_schedule(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    reporting.configure_reporting(tmp_path, non_interactive=True, no_metrics_schedule=True)
    answers = iter(["", "", "07:30"])
    settings = reporting.collect_reporting(
        tmp_path, prompt=True, input_fn=lambda _: next(answers)
    )
    assert settings["metrics_build_time"] == "07:30"


def test_eof_does_not_write_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    monkeypatch.setattr(init_module, "stdin_is_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
    with pytest.raises(EOFError):
        init_project(str(tmp_path), prompt=True)
    assert not (tmp_path / "config.yaml").exists()
    assert not (tmp_path / "reports").exists()


def test_direct_python_init_never_prompts(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    monkeypatch.setattr(init_module, "stdin_is_interactive", lambda: (_ for _ in ()).throw(AssertionError("prompt check")))
    init_project(str(tmp_path))
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["reporting"]["timezone"] == "UTC"


def test_metrics_scaffold_preserves_existing_files_and_adds_schedule(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    monkeypatch.setattr(reporting, "stdin_is_interactive", lambda: False)
    monkeypatch.setattr("trellum.data.datasource_config.load_datasource_config", lambda: {})
    (tmp_path / "config.yaml").write_text("reporting:\n  timezone: UTC\n  day_start: '00:00'\n  metrics_build_time: null\n")
    report_dir = tmp_path / "reports" / "metrics"
    report_dir.mkdir(parents=True)
    (report_dir / "report.yaml").write_text("custom report")
    (report_dir / "generator.py").write_text("custom generator")
    assert _scaffold_report(str(tmp_path)) == 0
    assert (report_dir / "report.yaml").read_text() == "custom report"
    assert (report_dir / "generator.py").read_text() == "custom generator"
    output = capsys.readouterr().out
    assert "remains unscheduled until configured" in output
    assert "python -m trellum setup reporting --dest ." in output


def test_new_metrics_report_has_inherited_schedule(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(reporting, "validate_reporting_settings", _valid)
    monkeypatch.setattr(reporting, "stdin_is_interactive", lambda: False)
    monkeypatch.setattr("trellum.data.datasource_config.load_datasource_config", lambda: {})
    (tmp_path / "config.yaml").write_text("reporting:\n  timezone: UTC\n  day_start: '00:00'\n  metrics_build_time: null\n")
    assert _scaffold_report(str(tmp_path)) == 0
    report = yaml.safe_load((tmp_path / "reports/metrics/report.yaml").read_text())
    assert report["schedule"] == {"project": "metrics"}
