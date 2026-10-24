"""Project reporting dates and UTC windows for local business-day boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


@dataclass(frozen=True)
class ReportingSettings:
    """A reporting day is labelled by the local date on which it starts."""

    timezone: str = "UTC"
    day_start: str = "00:00"
    metrics_build_time: str | None = None

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.timezone, str):
                raise ValueError
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("reporting.timezone must be an IANA timezone name") from exc
        for name in ("day_start", "metrics_build_time"):
            value = getattr(self, name)
            if name == "metrics_build_time" and value is None:
                continue
            if not isinstance(value, str) or not _TIME.fullmatch(value):
                raise ValueError(f"reporting.{name} must be HH:MM (00:00 through 23:59)")

    def _boundary(self, day: date) -> datetime:
        # fold=0 selects the first repeated time. For a missing time, converting
        # that same fold to UTC advances the local boundary by the DST gap.
        local = datetime.combine(day, time.fromisoformat(self.day_start),
                                 tzinfo=ZoneInfo(self.timezone)).replace(fold=0)
        return local.astimezone(timezone.utc)

    def reporting_date(self, now: datetime) -> date:
        """Return the reporting label at an aware instant, comparing in UTC."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("reporting_date requires an aware datetime")
        instant = now.astimezone(timezone.utc)
        day = instant.astimezone(ZoneInfo(self.timezone)).date()
        return day if instant >= self._boundary(day) else day - timedelta(days=1)

    def day_bounds(self, day: date | str) -> tuple[datetime, datetime]:
        """Return half-open UTC bounds; local DST days need not be 24 hours."""
        if isinstance(day, str):
            try:
                parsed = date.fromisoformat(day)
                if parsed.isoformat() != day:
                    raise ValueError
                day = parsed
            except ValueError as exc:
                raise ValueError("reporting day must be an ISO date (YYYY-MM-DD)") from exc
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ValueError("reporting day must be a date or ISO date string")
        return self._boundary(day), self._boundary(day + timedelta(days=1))

    def metrics_schedule(self) -> dict:
        """Return a timezone-aware daily cron schedule when configured."""
        if self.metrics_build_time is None:
            return {}
        hour, minute = map(int, self.metrics_build_time.split(":"))
        return {"cron": f"{minute} {hour} * * *", "timezone": self.timezone}


def validate_reporting_settings(config: dict) -> ReportingSettings:
    """Validate the reporting mapping in a full project configuration."""
    if not isinstance(config, dict):
        raise ValueError("project config must be a mapping")
    reporting = config.get("reporting", {})
    if not isinstance(reporting, dict):
        raise ValueError("reporting must be a mapping")
    return ReportingSettings(
        timezone=reporting.get("timezone", "UTC"),
        day_start=reporting.get("day_start", "00:00"),
        metrics_build_time=reporting.get("metrics_build_time"),
    )


def load_reporting_settings(project_root: str | Path | None = None) -> ReportingSettings:
    """Read only the project's config.yaml, failing clearly on invalid content."""
    if project_root is None:
        from trellum.project import get_project_root

        project_root = get_project_root()
    path = Path(project_root) / "config.yaml"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ReportingSettings()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read reporting configuration from {path}: {exc}") from exc
    try:
        config = yaml.safe_load(content)
        return validate_reporting_settings(config)
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"Invalid reporting configuration in {path}: {exc}") from exc
