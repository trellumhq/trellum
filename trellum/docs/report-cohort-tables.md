# Precomputed cohort tables

`CohortTable` displays author-supplied observations and optional forecasts in a
native HTML table. It is separate from `PivotTable`: existing pivot constructors,
selectors, aggregations, totals, and defaults retain their behavior. Existing KPI
states and geometry are unaffected. This component adds no renderer dependency.

```python
from trellum.components import CohortTable, DataSource, FilterBar

ctx.add_section("Acquisition cohorts", [
    DataSource("acquisition_cohorts", cells),
    FilterBar("acquisition_cohorts", cells, filters=[
        {"column": "platform", "type": "toggle"},
    ]),
    CohortTable(
        cells, "Supplied recovery ratios",
        cohort="cohort_id", cohort_month="month", horizon="day",
        observed="recovery", observed_state="coverage", reason="reason",
        population={
            "label": "Paid acquisition cohorts, individually supplied by platform",
            "as_of": "2026-02-04T00:00:00+00:00", "timezone": "UTC",
            "source_time": "2026-02-03T23:30:00+00:00",
        },
        forecast="forecast_recovery",
        forecast_metadata={
            "model": "Supplied model v2", "scenario": "Baseline",
            "issued_at": "2026-02-04T00:00:00+00:00",
            "scope": "Each named acquisition cohort independently",
        },
        milestone={"threshold": 1.0, "label": "Recovery milestone"},
        dataset_id="acquisition_cohorts",
    ),
])
```

## Constructor and source records

```python
CohortTable(df, title="", *, cohort, cohort_month, horizon, observed,
            observed_state, population, forecast=None, reason=None,
            forecast_metadata=None, value_format="percent", milestone=None,
            dataset_id=None, static=False)
```

All options after `title` are keyword-only. Column arguments name
DataFrame columns. A month column may also serve as the cohort identity when
that month uniquely identifies the cohort. The frame has one record per `(cohort, horizon)`, with no
implicit zero-valued cells. `cohort` is a nonblank string identity. `cohort_month`
is strictly `YYYY-MM`, years 0001–9999, and is constant within an identity.
Different identities may share a month; the header then displays their identities.
Columns sort chronologically, retaining source first-seen identity order within
each month. Rows sort by nonnegative integer day horizon; booleans, fractional
horizons, and numeric strings are rejected. No generic totals are calculated.

`observed_state` is one of:

| State | Observed value | Default explanation |
| --- | --- | --- |
| `observed` | Finite genuine numeric value, including zero or a negative value | Author-declared observation |
| `unavailable` | Null | Observation unavailable as of the declared snapshot |
| `not_mature` | Null | Not mature as of the declared snapshot |

An absent intersection is displayed as `No record`. It has no invented source
record or exported zero. A nonblank authored reason replaces the default
explanation; blank or null reasons use the distinct fallback. Infinity,
booleans, and numeric strings are rejected as values. Pandas missing sentinels
and NaN represent null; NaN cannot be an observed value. Integers outside
JavaScript's exact range (±9,007,199,254,740,991) are rejected to protect raw
snapshot precision. Native integer and binary floating-point snapshot scalars
are supported; arbitrary-precision Decimal/object numerics are unsupported. No sign restrictions are inferred from the value format.

`population` requires nonblank `label`, `as_of`, and `timezone`, with optional
`source_time`. Time values are offset-aware ISO datetimes; timezone is a valid
IANA timezone name. The compact context always identifies the authored
population, snapshot, timezone, and optional source watermark. The component
never uses the current clock to infer maturity.

## Forecasts and disclosure

The optional forecast column contains independently supplied finite numbers or
nulls. Configuring it requires `forecast_metadata` with exactly `model`,
`scenario`, `issued_at`, and `scope`; all are nonblank strings and `issued_at`
is an offset-aware ISO datetime. Metadata without a forecast column is rejected.

Observed values take display precedence. Otherwise a supplied forecast appears
with an **F** marker, italic text, a dotted underline, and an accessible forecast
label. Otherwise the cell shows a dash and its distinct missing state. The
native **Cell details and provenance** disclosure preserves the observation
state, raw observed value, raw forecast value, reason, display state, and all
forecast provenance. A record with both observation and forecast retains both.

The author declares the state and model applicability as of the snapshot.
There is no model fitting, pooling, generated confidence interval, freshness
policy, or automatic reuse of another cohort's forecast. Ratios, ages,
timezone boundaries, populations, and completeness must be calculated and
declared by the author before constructing the component.

## Filtering and limits

Use `static=True` for an independent frame, or `dataset_id` for an ordinary
snapshot `DataSource` or `ScopedDataSource`. Do not combine `static=True` and
`dataset_id`. Declare the source before the table. Effective source data,
including all deferred snapshot chunks, determines the table's immutable
source manifest. Validation examines every effective source record, even if
the table's `df` is a smaller frame. A chunked source must retain each complete
cohort in its selected rows; a partial loading/selection produces a local
scope error until complete data is available.

Filters select whole precomputed cohort identities. A participating filter
column must be constant within each cohort. Horizon, observation, forecast,
state, and reason columns cannot be filter inputs for this component. Main,
section, propagated, and scoped-parent filters are checked. LiveDataSource
ancestry and propagation-connected live datasets in either direction are
unsupported. No global filter policy changes are required.

At runtime every selected identity must retain its exact source cell set and
every declared source field. An injected partial selection, duplicate, unknown
record, or changed field withholds the table and forecast results and disables
CSV with a specific local explanation. No matches produces an honest empty
message; clearing the filter restores the exact supplied data. Source row
fields must be bounded snapshot scalar values (text, numbers, booleans,
dates, or null); nested objects are unsupported.

Admission limits are 120 cohort identities, 64 distinct horizons, 7,680 records,
and 4 MiB of serialized cells and metadata per component. Text fields and
source column names are limited to 2,048 characters. These are client snapshot
safety bounds; excessive input fails clearly before allocating the display
cross-product. Data is never silently truncated. Each component owns its
subscription and removal observer; renderer re-entry disposes the prior
subscription. Theme changes use shared CSS roles without duplicating listeners.

## Sampled milestone

`milestone=None` leaves the milestone row off. A declaration requires a finite
numeric `threshold` and nonblank `label`. The separate final row identifies the
first displayed **observed** horizon meeting the threshold, otherwise the
first displayed **forecast** horizon meeting it. A forecast hidden by an
observation does not become a displayed forecast milestone. The component
keeps numeric order even for nonmonotone supplied values.

This is explicitly the **first displayed horizon**, never an exact crossing
date. Missing earlier horizons or values can conceal an earlier crossing.
`No displayed crossing` is neutral and does not assert failure. No red/green
status meaning is invented.

## Export, accessibility, and native reports

Numbers use the shared `getFormatter` presentation formats `percent`, `number`,
`currency`, or `ratio`. Formatting does not change exported numeric precision.
CSV uses the existing download contract and exports **all selected source
cells**, including columns outside the current horizontal viewport:

- Cohort identity, cohort month, numeric horizon, separate raw observed and
  forecast values, observed state, effective display state, and reason.
- Value format/units declaration; population label, snapshot, timezone, and
  optional source time; model, scenario, issue time, and forecast scope.

Nulls stay blank and zero stays zero. CSV contains no generic totals, generated
missing-intersection rows, or forecasts relabeled as observations. The CSV order
matches cohort month/identity order then numeric horizon order.

The table uses native headers, year groups, a caption, aligned numbers, a state
legend, and a keyboard-accessible local horizontal scroller. The first horizon
column remains visible while scrolling; there is no pinned header or extra
PivotTable control. Identities and reasons remain reachable in the disclosure.
Native section capture, report PNG, and PDF capture the rendered current filtered
view, population context, visible state labels, and milestone note. The closed
provenance disclosure is available to the reader and can be opened before
capture; CSV always carries all declared metadata. A phone capture shows the
current horizontal viewport, matching the existing native capture contract.

## Native integration and examples

The class registers its assets through normal component methods and the
`cohort_table` entry in `_fwRenderers`. Import it from `trellum.components`.
The shared `validate_report` pipeline checks the effective source and filter
scope in its cohort-tables group, emitting `cohort-table-invalid` failures or a
`cohort-table-snapshot` pass. The standard `_details.json` remains the
shared dataset/filter diagnostic schema; per-cell values and forecast
provenance are retained in component data, the disclosure, and CSV.

The component showcase's **cohort tables** link opens supplied observations and
forecasts, optional milestone, observed-only and existing PivotTable examples.
Its `examples_cohorts.add_sections(ctx)` module uses independent
`showcase_cohorts_supplied` and `showcase_cohorts_observed` datasets and named
sections; no private renderer or source data is needed.
