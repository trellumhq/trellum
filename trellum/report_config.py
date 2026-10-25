"""Report and analysis manifest trust boundaries."""

from pathlib import Path

import yaml


def load_content_config(report_dir: str) -> dict:
    """Read a manifest before resolving any project defaults."""
    path = Path(report_dir) / "report.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No report.yaml found in {report_dir}")
    with path.open(encoding="utf-8") as stream:
        return validate_content_config(yaml.safe_load(stream))


def load_report_config(report_dir: str) -> dict:
    """Read a report and resolve schedules from its containing project."""
    config = load_content_config(report_dir)
    schedule = config.get("schedule")
    if isinstance(schedule, dict) and "project" in schedule:
        from trellum.reporting_day import load_reporting_settings

        reporting_settings = None
        if schedule["project"] == "metrics" and "cron" not in schedule:
            reporting_settings = load_reporting_settings(Path(report_dir).absolute().parent.parent)
        return resolve_project_schedule(config, reporting_settings)
    return config


def resolve_project_schedule(config: dict, settings) -> dict:
    """Expand a project schedule without changing an explicit report cron.

    An explicit (even empty) cron is an override. Only reports that opt in
    inherit project settings; ordinary reports and analyses are unchanged.
    """
    schedule = config.get("schedule")
    if not isinstance(schedule, dict) or "project" not in schedule:
        return config
    if schedule["project"] != "metrics":
        raise ValueError("schedule.project must be 'metrics'")
    if "cron" in schedule:
        return config
    return {**config, "schedule": {"project": "metrics", **settings.metrics_schedule()}}


def validate_content_config(config: object) -> dict:
    """Validate the content discriminator before executing project code."""
    if not isinstance(config, dict):
        raise ValueError("report.yaml must contain a mapping")
    if config.get("kind", "report") not in ("report", "analysis"):
        raise ValueError("report.yaml kind must be 'report' or 'analysis'")
    if "initial_audience" in config and config["initial_audience"] not in ("studio", "private"):
        raise ValueError("report.yaml initial_audience must be 'studio' or 'private'")
    if config.get("kind") == "analysis":
        if config.get("format", "article") not in ("article", "presentation"):
            raise ValueError("Analysis format must be 'article' or 'presentation'")
        for key in ("data_sources", "schedule", "extra_cdn"):
            if key in config:
                raise ValueError(f"Analysis report.yaml cannot declare {key}")
        for key in ("author", "name", "description"):
            if key in config and not isinstance(config[key], str):
                raise ValueError(f"Analysis {key} must be a plain string")
    return config
