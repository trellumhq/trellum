"""Project reporting-day and Metrics schedule configuration."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from trellum.reporting_day import validate_reporting_settings


class ReportingSetupError(ValueError):
    """A reporting configuration cannot safely be read or written."""


DEFAULTS = {"timezone": "UTC", "day_start": "00:00", "metrics_build_time": "07:00"}


def _read_config(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    except (OSError, UnicodeError) as exc:
        raise ReportingSetupError(f"Could not read project config {path}: {exc}") from exc
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


def _load_rendered(raw: str, path: Path) -> dict[str, Any]:
    try:
        rendered = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ReportingSetupError(
            f"Cannot safely preserve YAML structure in {path}; original file was left unchanged: {exc}"
        ) from exc
    if rendered is None:
        rendered = {}
    if not isinstance(rendered, dict):
        raise ReportingSetupError(
            f"Cannot safely preserve YAML structure in {path}; original file was left unchanged"
        )
    return rendered


def _document_end_index(raw: str) -> int | None:
    try:
        for token in yaml.scan(raw):
            if isinstance(token, yaml.tokens.DocumentEndToken):
                return token.start_mark.index
    except yaml.YAMLError as exc:
        raise ReportingSetupError(f"Malformed project YAML: {exc}") from exc
    return None


def _block_mapping_text(block: dict[str, Any]) -> str:
    return yaml.safe_dump(block, sort_keys=False, allow_unicode=True).rstrip("\n")


def _flow_mapping_text(block: dict[str, Any]) -> str:
    return yaml.safe_dump(
        block, sort_keys=False, allow_unicode=True, default_flow_style=True,
    ).strip()


def _indent_lines(text: str, indentation: str) -> str:
    lines = text.splitlines()
    return "\n".join([lines[0], *(indentation + line for line in lines[1:])])


def _last_content_end(node: yaml.Node) -> int:
    """End of the final value token, excluding trailing comments/blank lines."""
    if isinstance(node, yaml.MappingNode) and node.value:
        return _last_content_end(node.value[-1][1])
    if isinstance(node, yaml.SequenceNode) and node.value:
        return _last_content_end(node.value[-1])
    return node.end_mark.index


def _render_config(raw: str, candidate: dict[str, Any], path: Path) -> str:
    """Patch the reporting node while retaining surrounding YAML source text."""
    try:
        root = yaml.compose(raw)
    except yaml.YAMLError as exc:
        raise ReportingSetupError(f"Malformed project config {path}: {exc}") from exc

    settings = candidate["reporting"]
    if isinstance(root, yaml.MappingNode):
        matches = [
            (key, value) for key, value in root.value
            if isinstance(key, yaml.ScalarNode) and key.value == "reporting"
        ]
        if len(matches) > 1:
            raise ReportingSetupError(
                f"Cannot safely update duplicate reporting keys in {path}; original file was left unchanged"
            )
        if matches:
            key, value = matches[0]
            start, end = value.start_mark.index, value.end_mark.index
            if root.flow_style or value.flow_style:
                replacement = _flow_mapping_text(settings)
            elif isinstance(value, yaml.MappingNode):
                content_end = _last_content_end(value)
                trailing = raw[content_end:end]
                replacement = _indent_lines(
                    _block_mapping_text(settings), " " * (key.start_mark.column + 2)
                ) + trailing
            else:
                raise ReportingSetupError(
                    f"Cannot safely update reporting value in {path}; original file was left unchanged"
                )
            updated = raw[:start] + replacement + raw[end:]
        elif root.flow_style:
            close = raw.rfind("}", root.start_mark.index, root.end_mark.index)
            if close < 0:
                raise ReportingSetupError(
                    f"Cannot safely update flow mapping in {path}; original file was left unchanged"
                )
            pair = _flow_mapping_text({"reporting": settings})[1:-1]
            separator = ", " if root.value else ""
            updated = raw[:close] + separator + pair + raw[close:]
        else:
            snippet = yaml.safe_dump(
                {"reporting": settings}, sort_keys=False, allow_unicode=True,
            )
            end = _document_end_index(raw)
            if end is None:
                prefix = raw
                if prefix and not prefix.endswith("\n"):
                    prefix += "\n"
                updated = prefix + snippet
            else:
                updated = raw[:end] + snippet + raw[end:]
    elif root is None:
        snippet = yaml.safe_dump({"reporting": settings}, sort_keys=False, allow_unicode=True)
        end = _document_end_index(raw)
        if end is None:
            prefix = raw
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            updated = prefix + snippet
        else:
            updated = raw[:end] + snippet + raw[end:]
    elif isinstance(root, yaml.ScalarNode) and root.tag.endswith(":null"):
        snippet = yaml.safe_dump({"reporting": settings}, sort_keys=False, allow_unicode=True).rstrip("\n")
        updated = raw[:root.start_mark.index] + snippet + raw[root.end_mark.index:]
    else:
        raise ReportingSetupError(
            f"Cannot safely update YAML document in {path}; original file was left unchanged"
        )

    if _load_rendered(updated, path) != candidate:
        raise ReportingSetupError(
            f"Cannot safely preserve unrelated YAML values in {path}; original file was left unchanged"
        )
    return updated


def _ask(label: str, default: str, input_fn=None) -> str:
    input_fn = input if input_fn is None else input_fn
    answer = input_fn(f"{label} [{default}]: ").strip()
    return answer or default


def _validate_candidate(candidate: dict[str, Any]) -> None:
    try:
        validate_reporting_settings(candidate)
    except Exception as exc:
        raise ReportingSetupError(f"Invalid reporting settings: {exc}") from exc


def collect_reporting(root: str | Path, *, timezone: str | None = None,
                      day_start: str | None = None, metrics_build_time: str | None = None,
                      no_metrics_schedule: bool = False, non_interactive: bool = False,
                      prompt: bool = False, input_fn=None) -> dict[str, Any]:
    """Validate complete settings without writing; callers can then scaffold atomically."""
    path = Path(root) / "config.yaml"
    _, config = _read_config(path)
    has_reporting = "reporting" in config
    current = config.get("reporting", {})
    if no_metrics_schedule and metrics_build_time is not None:
        raise ReportingSetupError("--metrics-build-time conflicts with --no-metrics-schedule")

    values: dict[str, Any] = dict(current)
    values.update({
        "timezone": timezone if timezone is not None else current.get("timezone", DEFAULTS["timezone"]),
        "day_start": day_start if day_start is not None else current.get("day_start", DEFAULTS["day_start"]),
        "metrics_build_time": (
            None if no_metrics_schedule else
            metrics_build_time if metrics_build_time is not None else
            current.get("metrics_build_time") if has_reporting else DEFAULTS["metrics_build_time"]
        ),
    })
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
    if no_metrics_schedule:
        values["metrics_build_time"] = None

    candidate = dict(config)
    candidate["reporting"] = values
    _validate_candidate(candidate)
    return values


def write_reporting(root: str | Path, values: dict[str, Any]) -> Path:
    path = Path(root) / "config.yaml"
    raw, config = _read_config(path)
    merged = dict(config.get("reporting", {}))
    merged.update(values)
    candidate = dict(config)
    candidate["reporting"] = merged
    _validate_candidate(candidate)
    if config.get("reporting") == merged:
        return path
    updated = _render_config(raw, candidate, path)
    temporary_path = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = None
        fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except (OSError, UnicodeError) as exc:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
        raise ReportingSetupError(f"Could not write project config {path}: {exc}") from exc
    return path


def configure_reporting(root: str | Path, **options) -> dict[str, Any]:
    values = collect_reporting(root, **options)
    write_reporting(root, values)
    return values


def stdin_is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())
