"""Date-column discovery and calendar boundaries for chunked DataSources."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

import pandas as pd


def _find_date_col(df: pd.DataFrame) -> str | None:
    """Return the first datetime64 column, or fall back to a date-like name."""
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col].dtype):
            return col
    for col in df.columns:
        if col.lower() in ("event_date", "date", "period", "dt"):
            return col
    # Any other *_date column (cohort_date, order_date, assigned_date, ...).
    # Without this, chunk_by silently does nothing for perfectly ordinary
    # column names.
    for col in df.columns:
        if col.lower().endswith("_date"):
            return col
    return None


def _period_key(date_str: str, chunk_by: str) -> str:
    """Return the period bucket key for a date string."""
    if chunk_by == "month":
        return date_str[:7]  # "YYYY-MM"
    if chunk_by == "week":
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return d.strftime("%G-W%V")  # ISO year + week, e.g. "2026-W17"
    return date_str[:7]


def _period_range(key: str, chunk_by: str) -> tuple[str, str]:
    """Return (min_date, max_date) strings for a period key."""
    if chunk_by == "month":
        year, month = int(key[:4]), int(key[5:7])
        last_day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
    if chunk_by == "week":
        # Parse ISO week: "%G-W%V" → Monday of that week
        monday = datetime.strptime(key + "-1", "%G-W%V-%u")
        sunday = monday + timedelta(days=6)
        return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")
    return key, key
