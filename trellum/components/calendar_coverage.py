"""Authored UTC calendar metadata; never an aggregation or value repair."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from typing import Any

from trellum.assets import load_css, load_js
from trellum.components.base import Component, RenderContext


def daily_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("calendar coverage dates must be Gregorian YYYY-MM-DD whole days")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("calendar coverage dates must be genuine Gregorian days") from exc


def calendar_bounds(day: date, grain: str) -> tuple[date, date]:
    if grain == "daily":
        return day, day
    if grain == "weekly":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    month = day.month if grain == "monthly" else 1 + 3 * ((day.month - 1) // 3)
    start = day.replace(month=month, day=1)
    last_month = month if grain == "monthly" else month + 2
    return start, day.replace(month=last_month, day=calendar.monthrange(day.year, last_month)[1])


def _text(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 160
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(
            f"calendar coverage {name} must be nonempty text of at most 160 characters"
        )
    return value


@dataclass(kw_only=True)
class CalendarCoverage(Component):
    """Optional shared note and exact-key BarChart tooltip context.

    Construct once and pass the same declaration as ``bucket_context`` to
    related charts. Placing the object in a section authors its visible note;
    referencing it from a chart alone authors only the hover context.
    See docs/report-kpi-buckets.md for supported snapshot and metric semantics.
    """

    dataset_id: str
    date_column: str
    start: str
    end: str
    grain: str
    bucket_column: str
    buckets: dict[str, tuple[str, str]]
    timezone: str = "UTC"
    week_start: str = "Monday"
    input_columns: tuple[str, ...] = ()
    horizon: dict | None = None
    caption: str = "Calendar coverage"
    _component_type: str = field(default="calendar_coverage", init=False, repr=False)
    _supports_dataset_id = True

    def __post_init__(self):
        self.config()

    def config(self) -> dict:
        for name in ("dataset_id", "date_column", "bucket_column", "caption"):
            _text(getattr(self, name), name)
        start, end = daily_date(self.start), daily_date(self.end)
        if start > end or (end - start).days >= 3660:
            raise ValueError("calendar coverage interval must be ordered and at most 3660 days")
        if (
            self.timezone != "UTC"
            or self.week_start != "Monday"
            or self.grain not in ("daily", "weekly", "monthly", "quarterly")
        ):
            raise ValueError(
                "calendar coverage supports UTC Gregorian daily/weekly/monthly/quarterly days with Monday weeks only"
            )
        if not isinstance(self.buckets, dict) or not 1 <= len(self.buckets) <= 3660:
            raise ValueError("calendar coverage requires 1..3660 exact authored bucket keys")
        expected = set()
        day = start
        while day <= end:
            expected.add(tuple(d.isoformat() for d in calendar_bounds(day, self.grain)))
            if day == end:
                break
            day += timedelta(days=1)
        normalized = {}
        previous = None
        for key, bounds in self.buckets.items():
            _text(key, "bucket key")
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
                raise ValueError("calendar coverage bucket bounds must be start/end pairs")
            lo, hi = map(daily_date, bounds)
            if (
                (lo, hi) != calendar_bounds(lo, self.grain)
                or lo > hi
                or previous is not None
                and lo <= previous
            ):
                raise ValueError(
                    "calendar coverage buckets must be full canonical calendar bounds in ascending order"
                )
            normalized[key] = [lo.isoformat(), hi.isoformat()]
            previous = hi
        if {tuple(v) for v in normalized.values()} != expected:
            raise ValueError(
                "calendar coverage mapping must cover exactly the reporting interval's calendar buckets"
            )
        if (
            not isinstance(self.input_columns, (tuple, list))
            or len(self.input_columns) > 32
            or len(set(self.input_columns)) != len(self.input_columns)
        ):
            raise ValueError(
                "calendar coverage input_columns requires at most 32 unique column names"
            )
        for column in self.input_columns:
            _text(column, "input column")
        horizon = None
        if self.horizon is not None:
            if not isinstance(self.horizon, dict) or set(self.horizon) != {
                "name",
                "days",
                "observed_through",
            }:
                raise ValueError(
                    "calendar coverage horizon requires name, days and observed_through"
                )
            horizon = dict(self.horizon)
            _text(horizon["name"], "horizon name")
            if type(horizon["days"]) is not int or not 0 <= horizon["days"] <= 3660:
                raise ValueError("calendar coverage horizon days must be an integer in 0..3660")
            daily_date(horizon["observed_through"])
        return {
            "dataset_id": self.dataset_id,
            "date_column": self.date_column,
            "start": self.start,
            "end": self.end,
            "grain": self.grain,
            "bucket_column": self.bucket_column,
            "buckets": normalized,
            "timezone": self.timezone,
            "week_start": self.week_start,
            "input_columns": list(self.input_columns),
            "horizon": horizon,
            "caption": self.caption,
        }

    def render_html(self, ctx: RenderContext) -> str:
        cid = ctx.next_id()
        ctx.register(
            cid,
            {
                "type": self._component_type,
                "dataset_id": self.dataset_id,
                "coverage": self.config(),
            },
        )
        return f'<aside id="{cid}" class="fw-calendar-coverage" aria-label="{escape(self.caption, quote=True)}" data-coverage-ready="false">Calendar coverage loading.</aside>'

    @classmethod
    def client_js(cls):
        return load_js("components/calendar_coverage.js")

    @classmethod
    def css(cls):
        return load_css("components/calendar_coverage.css")


def bucket_context_config(chart) -> dict:
    declaration = chart.bucket_context
    if declaration is None:
        return {}
    if not isinstance(declaration, CalendarCoverage):
        raise ValueError("bucket_context must reference CalendarCoverage or be None")
    config = declaration.config()
    if (
        not chart.dataset_id
        or chart.dataset_id != declaration.dataset_id
        or chart.x != declaration.bucket_column
        or chart.horizontal
        or chart.stacked
        or chart.net_color
        or chart.sort is not None
        or chart.top_n is not None
        or chart.month_context
        or chart.monthly_average is not None
    ):
        raise ValueError(
            "bucket_context requires ordinary unsorted vertical snapshot bars on the declared dataset and canonical bucket column"
        )
    return {"bucket_context": config}
