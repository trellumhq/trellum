"""Project reporting-day and Metrics schedule configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from trellum.reporting_day import validate_reporting_settings


class ReportingSetupError(ValueError):
    """A reporting configuration cannot safely be read or written."""


DEFAULTS = {"timezone": "UTC", "day_start": "00:00", "metrics_build_time": "07:00"}


def _read_config(path: Path) -> tuple[str, dict[str, Any]]:
    if not path.exists():
        return "", {}
    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ReportingSetupError(f"Malformed project config {path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ReportingSetupError(f"Malformed project config {path}: expected a YAML mapping")
    if "reporting" in data and not isinstance(data["reporting"], dict):
        raise ReportingSetupError(f"Malformed project config {path}: reporting must be a mapping")
    return raw, data


def _reporting_value_span(raw: str) -> tuple[int, int] | None:
    """Find only the reporting value node, leaving other source text intact."""
    if not raw.strip():
        return None
    try:
        node = yaml.compose(raw)
    except yaml.YAMLError as exc:
        raise ReportingSetupError(f"Malformed project YAML: {exc}") from exc
    if not isinstance(node, yaml.MappingNode):
        return None
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "reporting":
            start = value.start_mark.index
            end = value.end_mark.index
            # For an empty/null key, replace from its value indentation line.
            if start == end:
                start = key.end_mark.index
            return start, end
    return None


def _render_config(raw: str, block: dict[str, Any]) -> str:
    encoded = yaml.safe_dump({"reporting": block}, sort_keys=False, allow_unicode=True)
    value = encoded.split("\n", 1)[1].rstrip("\n")
    span = _reporting_value_span(raw)
    if span:
        start, end = span
        # The compose end mark excludes trailing comments, so these remain intact.
        value = value[2:] if value.startswith("  ") else value
        return raw[:start] + value + raw[end:]
    prefix = raw
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + "reporting:\n" + value + "\n"


def _ask(label: str, default: str, input_fn=None) -> str:
    input_fn = input if input_fn is None else input_fn
    answer = input_fn(f"{label} [{default}]: ").strip()
    return answer or default


def collect_reporting(root: str | Path, *, timezone: str | None = None,
                      day_start: str | None = None, metrics_build_time: str | None = None,
                      no_metrics_schedule: bool = False, non_interactive: bool = False,
                      prompt: bool = False, input_fn=None) -> dict[str, Any]:
    """Validate complete settings without writing; callers can then scaffold atomically."""
    path = Path(root) / "config.yaml"
    _, config = _read_config(path)
    current = config.get("reporting") or {}
    values: dict[str, Any] = {
        "timezone": timezone if timezone is not None else current.get("timezone", DEFAULTS["timezone"]),
        "day_start": day_start if day_start is not None else current.get("day_start", DEFAULTS["day_start"]),
        "metrics_build_time": (None if no_metrics_schedule else
            metrics_build_time if metrics_build_time is not None else
            current.get("metrics_build_time", DEFAULTS["metrics_build_time"])),
    }
    if prompt and not non_interactive:
        values["timezone"] = _ask(
            "Timezone for reporting day (IANA name)", str(values["timezone"]), input_fn
        )
        values["day_start"] = _ask(
            "Reporting-day start HH:MM (determines what today/yesterday mean)",
            str(values["day_start"]), input_fn,
        )
        schedule_default = "disabled" if values["metrics_build_time"] is None else str(values["metrics_build_time"])
        answer = _ask(
            "Metrics daily build time HH:MM (separate; when data is ready)",
            schedule_default, input_fn,
        )
        values["metrics_build_time"] = None if answer.lower() in {"none", "disabled", "off"} else answer
    # Keep a disabled schedule disabled on reruns unless explicitly enabled.
    if (current.get("metrics_build_time", DEFAULTS["metrics_build_time"]) is None
            and metrics_build_time is None and not no_metrics_schedule and not prompt):
        values["metrics_build_time"] = None
    candidate = dict(config)
    candidate["reporting"] = values
    try:
        validate_reporting_settings(candidate)
    except Exception as exc:
        raise ReportingSetupError(f"Invalid reporting settings: {exc}") from exc
    return values


def write_reporting(root: str | Path, values: dict[str, Any]) -> Path:
    path = Path(root) / "config.yaml"
    raw, config = _read_config(path)
    candidate = dict(config)
    candidate["reporting"] = values
    try:
        validate_reporting_settings(candidate)
    except Exception as exc:
        raise ReportingSetupError(f"Invalid reporting settings: {exc}") from exc
    updated = _render_config(raw, values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return path


def configure_reporting(root: str | Path, **options) -> dict[str, Any]:
    values = collect_reporting(root, **options)
    write_reporting(root, values)
    return values


def stdin_is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())
