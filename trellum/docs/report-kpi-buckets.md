# Calendar coverage for KPI bucket trends

`CalendarCoverage` is optional UTC calendar metadata for ordinary `BarChart`
trends. It neither calculates chart values nor changes aggregation. The same
object can appear once as a shared visible note and be referenced by eight
related charts. Omit the object from the layout for hover context alone; omit
`bucket_context` from charts for a note alone. Both default to absent.

```python
from trellum.components.calendar_coverage import CalendarCoverage
from trellum.components import BarChart

coverage = CalendarCoverage(
    dataset_id="acquisition_facts",
    date_column="day",
    start="2026-06-12", end="2026-09-23",
    timezone="UTC", week_start="Monday", grain="monthly",
    bucket_column="month_bucket",  # keys already authored on original facts
    buckets={
        "2026-06-01": ("2026-06-01", "2026-06-30"),
        "2026-07-01": ("2026-07-01", "2026-07-31"),
        "2026-08-01": ("2026-08-01", "2026-08-31"),
        "2026-09-01": ("2026-09-01", "2026-09-30"),
    },
    input_columns=("cost", "installs", "revenue_d7"),
    horizon={"name": "D7", "days": 7, "observed_through": "2026-11-04"},
)
ctx.add_section("Acquisition trends", [
    coverage,
    BarChart(facts, "month_bucket", "cost", "CPI",
             dataset_id="acquisition_facts", y_format="currency",
             ratios=[{"numerator": "cost", "denominator": "installs", "label": "CPI"}],
             bucket_context=coverage),
])
```

Declare the original `DataSource` and native `FilterBar` first. The component
reads the public filter state, intersects selected daily date ranges with the
inclusive authored reporting interval, and uses the engine's filtered source
population. A single native `ScopedDataSource` may add local filters; parent
filters still apply. Other filters (including platform) apply normally. It
never derives the requested interval from the first or last remaining row.

All `CalendarCoverage` parameters and the new `BarChart.bucket_context` option
are keyword-only. Existing BarChart positional and subclass parameters remain
unchanged. An explicit `dataset_id` still takes precedence over `static=True`.
Passing `None` emits no context payload, note, spacing or changed tooltip.

## Authoring contract and limits

Dates must be genuine Gregorian `YYYY-MM-DD` whole days. Supported grains are
`daily`, `weekly`, `monthly`, and `quarterly`, with UTC reporting and Monday
weeks. Map every exact authored key to its full canonical calendar start/end,
in ascending, nonoverlapping calendar order. The mapping must cover exactly
the buckets intersecting the declared interval. Keys are text (at most 160
characters); charts must preserve their canonical chronological order. Context
matches exact plotted labels and never guesses dates from array positions.

The source must be one complete snapshot, optionally behind one native scope.
Live queries, chunked sources, derived projections and chained scopes are
unsupported. Admission limits are 10,000 source facts, 4 MiB of relevant
serialized columns, 3,660 inclusive reporting days, 3,660 authored buckets,
32 distinct input columns, and 160 characters per name/key/caption. There is no
silent truncation. Runtime source replacement is checked again. Unavailable
context explains its reason while ordinary chart values retain their behavior.

Use ordinary unsorted vertical bars on the declared dataset and bucket column.
Horizontal, stacked, top-N, sorted, net-color, month-context and monthly-average
combinations are rejected with this option. The declaration supplies metadata;
it does not create bucket columns, rebucket rows, repair gaps or change values.

## Calendar dates, metric inputs and horizon maturity

For each bucket the hover title shows full calendar bounds, exact selected
included bounds, included/full day counts, distinct observed dates and missing
dates. Partial selected calendar edges are different from holes inside that
selection. Multiple platform rows, or duplicate contributors on a date, count
as one observed date. An absent date is unknown, never a measured zero.

`input_columns` optionally names numeric source inputs shared by the note and
its charts. A date with any null, missing, nonfinite, nonnumeric or unsafe
magnitude input is identified separately from a missing date. Finite measured
zeros remain valid observed inputs. With no input declaration, numeric
completeness is explicitly unknown. These statements concern the declared
inputs only; they do not prove production, metric or population completeness.

An optional horizon declares a name, integer days (0–3,660), and an explicit
observed-through date. A selected date is mature when date + horizon days is
on or before observed-through. Maturity includes selected dates independently
of whether source facts are present. Calendar coverage is separate. There is
no today-relative inference, timezone/DST conversion, fiscal calendar, forecast
or model inference. The example's D1, D7 and D30 horizons are all mature.

## Native example and compatibility

`demo/reports/component-showcase/examples_kpi_buckets.py:add_sections(ctx)`
provides baseline and context tabs, native grain/horizon Toggles and Visible
panels, a native FilterBar, Grid, and eight existing BarCharts over one shared
208-row snapshot. It authors four bucket-key columns on original facts. All
12 grain × horizon choices use normal sums or ratios of sums, including CPI
and recovery. Lazy visibility does not eliminate the memory used by hidden
chart configurations. No report-local calculation renderer is installed.

The deterministic D7 daily all-platform values are cost 50, installs 40,
CPI 1.25, revenue 30, recovery 0.60, retention 0.30, revenue/install 0.75,
and first-depositor proxies 5. D30 recovery is 1.20. Counts/proxies are supplied
acquisition-cohort measures, not period distinct people. Full calendar coverage
has 104 daily, 16 weekly, four monthly, and two quarterly buckets. The first
and last weeks include three days each; June includes 19/30 days and September
23/30. Q2 includes 19/91 and Q3 85/92 days.

Tooltips retain the existing nearest-point selection, value labels, units and
style. Context wraps the actual title callback used by the external HTML
tooltip. Plot heights, libraries, data arrays, controls and chart drawing are
unchanged. The optional note adds its own layout height outside the charts.
Descriptions use text content and tooltip escaping, including authored captions.

CSV downloads retain the existing schema and plotted values in original
numeric units. Ratios remain fractions. Extra hover metadata is not exported
to CSV. Native section, report PNG and PDF captures can include the visible
shared note. Hover-only exact dates are not part of those images.
