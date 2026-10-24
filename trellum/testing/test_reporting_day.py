"""Reporting labels, DST boundary policy, and date-window integration."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trellum.report import ReportContext
from trellum.reporting_day import (
    ReportingSettings,
    load_reporting_settings,
    validate_reporting_settings,
)


def instant(value):
    return datetime.fromisoformat(value)


def test_defaults_and_frozen_settings(tmp_path):
    settings = load_reporting_settings(tmp_path)
    assert settings == validate_reporting_settings({}) == ReportingSettings()
    assert settings.reporting_date(instant("2026-10-24T00:00:00Z")) == date(2026, 10, 24)
    assert settings.day_bounds("2026-10-24") == (
        instant("2026-10-24T00:00:00Z"), instant("2026-10-25T00:00:00Z"),
    )
    assert settings.metrics_schedule() == {}
    with pytest.raises(FrozenInstanceError):
        settings.day_start = "04:00"


@pytest.mark.parametrize("now,expected", [
    ("2026-10-24T03:59:59Z", "2026-10-23"),
    ("2026-10-24T04:00:00Z", "2026-10-24"),
    ("2026-10-24T05:00:00+01:00", "2026-10-24"),
])
def test_utc_cutoff(now, expected):
    assert ReportingSettings(day_start="04:00").reporting_date(instant(now)).isoformat() == expected


def test_non_utc_crossover_and_schedule():
    settings = ReportingSettings("Asia/Tokyo", "04:00", "05:07")
    assert settings.reporting_date(instant("2026-10-23T18:59:59Z")) == date(2026, 10, 23)
    assert settings.reporting_date(instant("2026-10-23T19:00:00Z")) == date(2026, 10, 24)
    assert settings.metrics_schedule() == {"cron": "7 5 * * *", "timezone": "Asia/Tokyo"}


def test_spring_missing_boundary_advances_by_gap():
    settings = ReportingSettings("Europe/Amsterdam", "02:30")
    start, end = settings.day_bounds("2026-03-29")
    assert start == instant("2026-03-29T01:30:00Z")  # 03:30 local
    assert end == instant("2026-03-30T00:30:00Z")
    assert settings.reporting_date(start - timedelta(microseconds=1)) == date(2026, 3, 28)
    assert settings.reporting_date(start) == date(2026, 3, 29)
    assert settings.reporting_date(end) == date(2026, 3, 30)


def test_autumn_ambiguous_boundary_uses_first_occurrence_and_is_monotonic():
    settings = ReportingSettings("Europe/Amsterdam", "02:30")
    start, end = settings.day_bounds(date(2026, 10, 25))
    assert start == instant("2026-10-25T00:30:00Z")
    assert end == instant("2026-10-26T01:30:00Z")
    zone = ZoneInfo("Europe/Amsterdam")
    instants = [start + timedelta(minutes=m) for m in range(-1, 92)]
    labels = [settings.reporting_date(now.astimezone(zone)) for now in instants]
    assert labels == sorted(labels)
    assert labels[0] == date(2026, 10, 24)
    assert all(label == date(2026, 10, 25) for label in labels[1:])
    # Second 02:15 remains after the first 02:30 boundary.
    assert settings.reporting_date(datetime(2026, 10, 25, 2, 15, tzinfo=zone, fold=1)) == date(2026, 10, 25)


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_dst_midnight_bounds(day, hours):
    start, end = ReportingSettings("Europe/Amsterdam").day_bounds(day)
    assert start.tzinfo is timezone.utc and end.tzinfo is timezone.utc
    assert end - start == timedelta(hours=hours)


@pytest.mark.parametrize("config", [
    None, [], "text", {"reporting": None}, {"reporting": []},
    {"reporting": {"timezone": "Not/AZone"}}, {"reporting": {"timezone": None}},
    *[{"reporting": {"day_start": value}} for value in
      [None, 400, True, "4:00", "04:0", "24:00", "04:60", "04:00:00", "04:00\n"]],
    {"reporting": {"metrics_build_time": "7:00"}},
])
def test_invalid_config(config):
    with pytest.raises(ValueError):
        validate_reporting_settings(config)


@pytest.mark.parametrize("text", ["[]", "false", "reporting: [", "reporting: null", "reporting:\n  day_start: nope"])
def test_strict_project_reader(tmp_path, text):
    (tmp_path / "config.yaml").write_text(text)
    with pytest.raises(ValueError, match="config.yaml"):
        load_reporting_settings(tmp_path)


@pytest.mark.parametrize("text", ["", "# Project settings\n", "null"])
def test_empty_project_settings_keep_legacy_defaults(tmp_path, text):
    (tmp_path / "config.yaml").write_text(text)
    assert load_reporting_settings(tmp_path) == ReportingSettings()


def test_project_reader_and_context_load_once(tmp_path, monkeypatch):
    monkeypatch.setattr("trellum.project.get_project_root", lambda: str(tmp_path))
    path = tmp_path / "config.yaml"
    path.write_text('reporting:\n  timezone: Asia/Tokyo\n  day_start: "04:00"\n  metrics_build_time: null\n')
    monkeypatch.setenv("FW_NOW", "2026-10-24T03:30:00+09:00")
    ctx = ReportContext({}, "test", str(tmp_path / "out"))
    assert ctx.reporting_timezone == "Asia/Tokyo"
    assert ctx.reporting_day_start == "04:00"
    assert (ctx.today, ctx.yesterday, ctx.two_days_ago, ctx.week_ago) == (
        "2026-10-23", "2026-10-22", "2026-10-21", "2026-10-16",
    )
    assert ctx._now_utc == instant("2026-10-23T18:30:00Z")
    assert ctx._now_utc.tzinfo is timezone.utc
    assert ctx.now_utc == "2026-10-23T18:30:00+00:00"
    path.write_text("reporting: invalid")
    assert ctx.reporting_day_bounds() == ctx.reporting_settings.day_bounds(ctx.today)
    ctx.today = "2026-10-20"
    assert ctx.reporting_day_bounds() == ctx.reporting_settings.day_bounds("2026-10-20")
    assert ctx.reporting_day_bounds("2026-10-21") == ctx.reporting_settings.day_bounds("2026-10-21")
    with pytest.raises(ValueError):
        ReportContext({}, "test", str(tmp_path / "out"))


@pytest.mark.parametrize("raw", ["2026-10-24", "2026-10-24T00:00:00Z", "2026-10-24T02:00:00+02:00"])
def test_default_context_fw_now(tmp_path, monkeypatch, raw):
    monkeypatch.setattr("trellum.project.get_project_root", lambda: str(tmp_path))
    monkeypatch.setenv("FW_NOW", raw)
    ctx = ReportContext({}, "test", str(tmp_path))
    assert ctx.today == "2026-10-24"
    assert ctx.now_utc == "2026-10-24T00:00:00+00:00"


def test_naive_instant_rejected():
    with pytest.raises(ValueError, match="aware"):
        ReportingSettings().reporting_date(datetime(2026, 10, 24))


@pytest.mark.parametrize("day", ["20261024", "not-a-date", 123, datetime(2026, 10, 24)])
def test_bad_day_bounds(day):
    with pytest.raises(ValueError):
        ReportingSettings().day_bounds(day)


@pytest.mark.parametrize("window,lookback,expected", [
    (None, 7, ("2026-10-16", "2026-10-23")),
    (None, None, ("2026-07-25", "2026-10-23")),
    (2, 7, ("2026-10-21", "2026-10-23")),
    (("2026-01-01", "2026-01-31"), 7, ("2026-01-01", "2026-01-31")),
])
def test_metrics_window_uses_reporting_label(monkeypatch, window, lookback, expected):
    import trellum.datasets as datasets

    metric = SimpleNamespace(executable=True, dataset="daily", agg="sum", column="amount",
                             numerator=None, denominator=None)
    dataset = SimpleNamespace(name="daily", time_column="day", dimensions=(), lookback=lookback, grain="day")
    monkeypatch.setattr(datasets, "load_metrics_result", lambda: SimpleNamespace(
        metrics={"amount": metric}, datasets={"daily": dataset}))
    monkeypatch.setattr(datasets, "load_dataset", lambda ctx, name, req, **kwargs: req)
    # No _now_utc or reporting settings required on compatible lightweight contexts.
    ctx = SimpleNamespace(today="2026-10-23")
    req = datasets.metrics_frame(ctx, ["amount"], window=window)
    assert (req.start, req.end) == expected
