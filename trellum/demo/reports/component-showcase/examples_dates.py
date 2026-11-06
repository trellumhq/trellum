"""Fixed-date examples using the ordinary report filter engine."""

import pandas as pd

from trellum.components import DataSource, DataTable, FilterBar, RawHTML


def add_sections(ctx):
    """Add independent daily, hourly, older, unavailable and gap examples."""
    daily = pd.DataFrame({
        "day": pd.date_range("2026-10-01", periods=22).strftime("%Y-%m-%d"),
        "channel": ["Organic", "Paid"] * 11,
        "readings": [10 + i % 7 for i in range(22)],
    })
    hourly = pd.DataFrame({
        "recorded_at": pd.date_range("2026-10-22 08:00", periods=10, freq="30min"),
        "readings": [12, 14, 11, 15, 18, 16, 19, 20, 17, 21],
    })
    older = pd.DataFrame({"day": ["2024-02-01", "2024-02-10", "2024-02-22"],
                          "readings": [5, 8, 6]})
    unavailable = pd.DataFrame({"day": pd.Series([None, None], dtype="str"),
                                "readings": [3, 4]})
    gaps = pd.DataFrame({"day": ["2026-10-01", "2026-10-22"], "readings": [9, 13]})

    def example(title, dataset, frame, column, prose, *, default_days=None, channel=False):
        date_filter = {"column": column, "label": "Recorded range", "type": "date_range"}
        if default_days is not None:
            date_filter["default_days"] = default_days
        filters = [date_filter]
        if channel:
            filters.append({"column": "channel", "label": "Channel", "type": "toggle"})
        ctx.add_section(title, [
            RawHTML(html=f'<p style="margin-bottom:24px">{prose}</p>'),
            DataSource(dataset, frame),
            FilterBar(dataset, frame, filters=filters),
            DataTable(frame, title="Recorded readings", dataset_id=dataset),
        ])

    example("Date ranges — daily readings", "showcase_dates_daily", daily, "day",
            "7D starts seven days before the latest recorded date and includes both "
            "endpoints: eight dates here. Change the dates or Channel to filter the readings.",
            default_days=7, channel=True)
    example("Date ranges — paired hourly fields", "showcase_dates_hourly", hourly, "recorded_at",
            "Each endpoint pairs a native date input with the existing half-hour choices. "
            "1H includes the two endpoints and the half-hour reading between them.")
    example("Date ranges — older recorded snapshot", "showcase_dates_older", older, "day",
            "This fixed February 2024 snapshot deliberately predates the other examples. "
            "Presets refer to its latest recorded date, not today; records have gaps.")
    example("Date ranges — unavailable bounds", "showcase_dates_unavailable", unavailable, "day",
            "These readings have no recorded dates. The date control explains why selection "
            "is unavailable; the other report controls keep their normal behavior.")
    example("Date ranges — valid interval with no matches", "showcase_dates_gaps", gaps, "day",
            'Open the <a href="?showcase_dates_gaps.day=2026-10-10..2026-10-10'
            '#sec-date-ranges-%E2%80%94-valid-interval-with-no-matches">10 October 2026 interval</a> '
            "or set both dates to that day to select a valid interval without readings. "
            "All returns the two dated records. No continuous coverage is implied.")
