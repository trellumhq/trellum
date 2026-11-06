# Period comparisons over complete daily snapshots

`PeriodComparison` calculates optional comparisons from one delivered, filtered
snapshot. `ComparisonTable` continues to format supplied results; its existing
constructors, supplied-reason handling and CSV units are unchanged. Ordinary
LineChart output and nearest-point hover remain unchanged when `point_context`
is omitted.

```python
from trellum.components.period_comparison import PeriodComparison

periods = PeriodComparison(
    'periods', parent='activity', toggle_id='period', default='MoM',
    date_column='date',
    presets={'MoM': {'A': ['2026-10-01', '2026-10-31'],
                     'B': ['2026-09-01', '2026-09-30']}},
    groups={'spend': 'Spend'},
    metrics={'cost': {
        'name': 'Cost (USD)', 'group': 'spend',
        'definition': {'agg': 'sum', 'column': 'cost'},
        'direction': 'neutral', 'format': 'currency', 'precision': 2,
        'change_precision': 2, 'change_measure': 'relative', 'daily': True,
    }},
)
```

Declare the raw `DataSource`, optionally one `ScopedDataSource`, then a native
`Toggle('period', ['MoM'], 'MoM')`, then this registrar, then consumers. The
Toggle's ordered options and default must exactly match the declaration.
Existing native controls own URL/state restoration. The registrar observes the
selected native control and subscribes to its parent; it never installs
`onToggleChange`, writes filters or uses report-specific calculation JavaScript.

All new PeriodComparison options after `id` are keyword-only. Inputs are copied
into the browser configuration. A declaration is checked at construction,
rendering and by `check_period_comparison(ctx, result)` in each native scope.
The central validator should call that hook; direct imports are supported.

## Dates, arithmetic and admission

Windows are explicit inclusive ISO `YYYY-MM-DD` Gregorian days in UTC. There are
no inferred timezones, DST boundaries, fiscal periods or relative-to-today
presets. Invalid dates, reversed windows and windows longer than 366 days are
rejected. Limits are 16 presets, 32 metrics, 8 nonempty authored groups,
10,000 complete source rows and 4 MiB of relevant source payload before filters.
No truncation occurs. Live, propagated-live, chunked, chained or cyclic parents,
joins and arbitrary expressions are unsupported. One scope over a raw snapshot
is supported. These bounds apply to the whole delivered source, not only a
selected window.

Metric `definition` is exactly one of:

| Definition | Calculation |
| --- | --- |
| `{'agg': 'sum', 'column': 'cost'}` | Sum of selected window facts |
| `{'agg': 'count'}` | Count of selected window facts |
| `{'agg': 'calendar_mean', 'column': 'cost'}` | Declared sum / inclusive calendar days in this window |
| `{'agg': 'ratio', 'numerator': 'revenue', 'denominator': 'cost'}` | Ratio of sums, never mean of row ratios |

The runtime reuses the pure MetricView reducer. Native numeric zero stays zero;
there is no string/bool coercion. Native source admission follows MetricView's
precision rules: prepare Decimal/string/bool inputs and unsafe integers before
delivery. Runtime missing, nonnumeric, unsafe or nonfinite metric inputs produce
null with a reason, independently for each metric. Display precision is explicit
0–12 decimal places; it never rounds the underlying value or changes CSV units.
Each metric explicitly declares `name`, `group`, `direction` (`neutral`, `higher`
or `lower`), `format` (`number`, `currency`, `percent` or `ratio`) and `precision`.
No direction, unit or formula is inferred from a display label. USD labels in the
example are authored; the component does not perform currency conversion.

Every required date must have at least one selected source row. An absent day
makes that window's summary incomplete, including count; a daily missing point
stays null. This detects absent dates, not absent contributors within a date:
a source must itself guarantee completeness at its intended dimension grain.
There is no missing-day imputation or extrapolation.

`change_measure='relative'` (default) calculates `(A - B) / B` in fraction units.
It requires a positive finite B; zero and negative baselines produce explicit
undefined/unsupported reasons. `percentage_points` requires an authored percent
metric whose A/B values remain fractions, and calculates `(A - B) * 100`.
Percentage-point change does not require a positive baseline.

## Outputs, filters and downloads

`outputs` returns schema/lineage descriptors. No raw facts are copied into results.

| Dataset | Rows and schema |
| --- | --- |
| `id` | Every authored metric, in declaration order |
| `id_group_<group key>` | Exact authored partition, with the same metric row schema |
| `id_daily` | `max(A_days, B_days)` aligned daily rows |

Summary columns are `metric` (stable key), `name`, `group`, `A`, `B`, `change`,
`A_reason`, `B_reason`, `change_reason`, `preset`, `A_start`, `A_end`, `B_start`,
`B_end`, `A_days`, `B_days`. `empty_frame()` supplies these columns without facts.
`comparison_context()` supplies the unchanged ComparisonTable semantic contract:
`metric_column='metric'`, A/B period labels, authored formats/directions, and
supplied reason columns. A simple native table is:

```python
ComparisonTable(periods.empty_frame(), 'All metrics', 'A', ['change'],
                dataset_id=periods.id, columns=['name', 'A', 'B', 'change'],
                comparison_context=periods.comparison_context(), row_limit='all')
```

Daily columns start with numeric `day` (ordered 1..N calendar positions),
`A_date`, `B_date`, `A_window`, `B_window`. Each metric with `daily=True` adds
`<metric>_A`, `<metric>_A_state`, `<metric>_A_reason` and the equivalent B columns.
Daily means use that individual day's sum. Out-of-period dates are null, numeric
values are null and state is `outside-period`. Missing measurements inside a
window are separately `unavailable`. September Day 31 is outside-period, never
zero, a missing observation or forecast.

Use ordinary LineChart `series_availability` rules for each A/B metric column,
including its state column and an authored explanation. This masks the existing
aggregator's null subtotals without changing aggregation or libraries. Charts
remain at their ordinary 220px plotting height. Ratios in the daily projection
are safe to plot as wide values because it publishes one row per day index;
they are already calculated from that day's selected raw facts.

All derived outputs inherit every parent filter, including optional scope
filters. Native multi-scope reports share their structural HTML shell, so the
period Toggle id/options/default and component order must agree across scopes;
the parent and result dataset ids may differ. Table-local search, sorting and row choices affect that table's matching
CSV only. They never change the projection or other summaries. Keep an all-metric
table available outside a grouped layout if an all-metric CSV is required.
Group CSV contains exactly its group rows. Table and chart CSV retain their
existing schemas: chart CSV is `label` plus plotted series, uses raw fractions,
and leaves outside-period values blank. **Hover dates and window context are not
added to chart CSV columns.** Summary period metadata is available if those
columns are explicitly included in a table.

## LineChart original-point context

`point_context=None` is keyword-only and opt-in. For bound ordinary wide series:

```python
LineChart(periods.empty_frame(daily=True), 'day', ['cost_A', 'cost_B'],
          'Daily cost (USD)', dataset_id=periods.id + '_daily',
          y_labels=['A', 'B'], y_format='currency',
          point_context=[
              {'series': 0, 'date_column': 'A_date', 'window_column': 'A_window'},
              {'series': 1, 'date_column': 'B_date', 'window_column': 'B_window'},
          ],
          series_availability=[
              {'series': 0, 'required_columns': ['cost_A'], 'state_column': 'cost_A_state'},
              {'series': 1, 'required_columns': ['cost_B'], 'state_column': 'cost_B_state'},
          ])
```

Each `series` is an exact unique caption or zero-based position; duplicate
captions/targets are rejected. Bound records must have unique x values and
known date/window columns. Date text is strict ISO daily; window text is literal
plain text, at most 240 characters. The painted tooltip title displays the exact
original date of the hovered series; its literal window appears in body lines. A and B are resolved separately,
including for the same aligned x. The existing nearest-point selection, body,
formatting and styles are retained. The helper owns a copied title callback;
it does not mutate authored configuration or install HTML renderers.

For static charts, replace the column fields with `records`, a list matching the
source row count, containing `{'date': '2026-10-22', 'window': 'A: …'}` or null.
Missing/invalid runtime context falls back to the ordinary title and never
alters plotted data. Live, propagated-live and chunked bindings, dynamic splits, ratio-mode charts,
month context and monthly average overlays are rejected for this option, since their point identities or
calendar coordinates need separate contracts. Forecast/event/result/availability
behavior remains available for ordinary wide series; this feature does not
reinterpret those values.

## Lifecycle and capture

The registrar replaces owned subscriptions on render/source/scope changes and
cleans them up when removed. Native Toggle removal publishes an unavailable
state. Source disappearance publishes null/reasons instead of retaining a stale
summary. All outputs are initialized before consumers are notified. Hidden and
revealed views consume the same projection; theme rendering preserves the
selected native state.

Native capture follows every derived dataset to its raw parent for filter
lineage. Unresolved/missing sources or removed outputs block capture. Completed
empty/incomplete populations and unavailable individual metrics remain completed
results with explicit reasons, rather than unfinished computations. Dataset
metrics count the delivered raw facts once and describe derived schemas with
zero warehouse-fact rows/bytes.

## Native example and integration

`demo/reports/component-showcase/examples_period_comparison.py:add_sections(ctx)`
adds **Period comparison**, anchor `#sec-period-comparison`. It has an independent
platform filter, MoM default, WoW/MoM/YoY native presets, Flat comparison default,
Grouped comparison, All metrics CSV, Original dates off/on views, four real line
charts, and Selected calendar source facts and CSV. Spend/Volume/Revenue/
Efficiency have exactly 3/2/2/2 metric rows. The source contains 184 complete
synthetic UTC daily facts across October 2025, September 2026 and October 2026.
The context panels derive their authored text from the same Python preset
mapping used by the projection. There is no report-specific calculation JS.

Common wiring for maintainers:

1. Export `PeriodComparison` from `trellum.components` and its `__all__` list, following the existing MetricView pattern.
   The framework root does not currently re-export components; no root export is needed.
2. Import `check_period_comparison` in `validation/__init__.py` and call it once
   per report (`check_period_comparison(ctx, result)`), alongside metric checks.
   It also checks bound/static point-context admission and output collisions.
3. Add `examples_period_comparison.add_sections(ctx)` to the component-showcase
   generator and its example-module loading convention. Add its section name to
   the showcase navigation/expected-section checks.
4. Link this page from the common component/API docs and compatibility index.
   Suggested changelog: “Add opt-in bounded UTC period comparisons and per-series
   original-date context for ordinary LineChart tooltips.”

No dependency, chart-library, portal, migration, release or version change is
required. Existing period controls, supplied comparisons, KPI layout and other
chart types retain their current contracts.
