# Heatmap cell summaries

`HeatmapChart` accepts optional, keyword-only `header_summary=None`. Existing
positional and subclass arguments remain unchanged. Omit the option to retain
existing payloads, markup and spacing. Opting in adds a header above the normal
key and plot; it does not change plot height, row arrangement, cell values,
normalization, colors, fitted values, tooltips, filters or libraries.

```python
HeatmapChart(
    df, "hour", "band", "units", "Supplied units",
    dataset_id="observations",
    header_context="Raw supplied units · independent authored context",
    header_summary={"stat": "mean", "scope": "current_cells"},
)
```

The declaration is a dict containing exactly the supported keys `stat`,
`scope`, and optional `label` and `format`. It does not accept row, column or
series selection. Construction and every render validate it; the pure
`heatmap_option_problems(chart)` hook also checks post-construction mutation.

| `stat` | Default label | Arithmetic |
| --- | --- | --- |
| `count` | Cell entries | Number of retained aggregated coordinate entries |
| `sum` | Cell total | Sum of eligible retained raw aggregate values |
| `min` | Minimum cell | Smallest eligible retained raw aggregate value |
| `max` | Maximum cell | Largest eligible retained raw aggregate value |
| `mean` | Mean cell | Unweighted arithmetic mean across eligible retained aggregate cells |

`scope="current_cells"` is required. The population is the current delivered
static snapshot or report-filtered bound rows, aggregated by the existing
coordinate implementation, after existing `drop_empty` selection and before
color normalization/log scaling. Zoom does not narrow it. Duplicate input
coordinates aggregate into one output entry; absent coordinates do not become
zero. Count is neither source-row count, distinct users, painted labels nor
matrix area, and is independent of numeric completeness. Empty count is `0`.
Numeric empty results show `—` with a reason; measured zero remains zero.

`drop_empty` retains a cell only when its x **and** y groups contain a positive
cell. Zero-only and negative-only groups may disappear, even though finite
negative inputs are eligible summary values. The summary follows the existing
retained population exactly. Signed colors retain their legacy semantics.

## Eligibility and precision

For numeric reducers, every supplied raw value must be a finite native numeric
value, excluding booleans, numeric-looking strings, nulls/missing values,
dates, Decimal objects and unsafe integers. Integer-valued numbers must fit
JavaScript's safe integer range `[-(2**53-1), 2**53-1]`. Every retained aggregate
must also be finite and safe. Numeric-looking strings or booleans do not become
eligible because the plot coerces them. This guard is conservative: an invalid
raw value withholds the numeric summary even if `drop_empty` later excludes its
coordinate group. Count does not require that guard.

The static path captures only a compact eligibility reason before pandas
aggregation. It does not copy source rows into the component payload. The bound
path checks current filtered raw values before JavaScript's legacy coercion.
Existing pandas/JavaScript aggregation, ordering and coercion differences are
preserved. In particular, static nonnumeric/null values still coerce to zero;
bound values still use the existing JavaScript coercion and cell-map addition.

Sum and mean use compensated arithmetic in raw units, falling back to scaled
compensation if an intermediate total overflows. Mean need not construct a representable total; min/max do not compute
an unused total. An invalid aggregate, nonfinite result, arithmetic overflow or
unsafe integer result shows `—` with an accessible reason. Already-lost upstream
precision cannot be recovered. Arithmetic still uses floating point; display
formatting may round values. Neither the guard nor a successful result certifies
source/warehouse completeness, inferred missingness, coordinate integrity or
upstream precision.

Existing coordinate identity remains unchanged: static coordinates are
stringified (including date handling); bound coordinates use stringification,
plain-object domain maps and NUL-separated cell keys. String-equivalent
coordinates, inherited-property names and NUL-key collisions can therefore
collapse or omit entries under legacy rules. The summary describes those
retained entries; it does not repair or certify categorization/serialization.

## Labels and units

`label` must be nonblank text of at most 60 characters. It is rendered with
`textContent`, so HTML-like text is displayed literally. `header_context` remains
independent and keeps its own accessible description token.

`format` accepts `number`, `currency`, `percent`, `chips`, `ratio`, or `plain`.
Numeric summaries default to the **actual existing cell formatter**:
`number`, `currency`, or `percent`. Other `value_format` names fall back to
`number` unless explicitly overridden in the summary. Count permits only
`number`/`plain` and always displays an integer entry count.

`sum` rejects either declared `value_format` or summary `format` of `percent`
or `ratio`, even when the other format is overridden. Choose an unweighted
`mean`, `min`, `max` or `count` for those values. The author is responsible for
additive units; no inferred rates, weighting, quantiles, across-chart comparisons
or normalized-color statistics are provided.

The accessible scope explains current aggregate entries, absent coordinates,
zoom, input eligibility and any `drop_empty` selection. A visible reason appears
only for unavailable results. Removal/replacement retires the heatmap's owned
header, description token, subscriptions, theme listener and double-click
handler. Unrelated descriptions, authored context and shared LiveWrap leases
keep their existing contracts.

## Capture and CSV

The summary belongs to normal DOM capture. Native section/report PNG and PDF
capture use their existing completion/admission rules and completed source
populations. A direct raw-canvas export contains only the plot and excludes the
DOM header.

Existing heatmap CSV remains limited. The generic chart delegate reads
`chart.data.labels`, while heatmap domains live on scales. In a native mixed
report loading that delegate, the observed static default/off/on downloads are
identical header-only `label,Value` CSV files. No exact matrix CSV schema is
introduced. A report containing only heatmaps may not load the ordinary-chart
CSV delegate. Include a normal `DataTable` to download meaningful current
filtered **raw source rows**, with columns/units clearly labeled; these rows are
not the aggregated matrix. The examples' iOS/August 1 source CSV has five rows
`0,10,20,12,18` totaling 60 units, versus four matrix entries with mean 15.

Existing multi-FilterBar URL date serialization may select another bar's active
preset (for example `All`) rather than the current bar's custom dates. This
extension preserves that behavior. Native explicit date-range URL decoding
(`dataset.day=2026-08-02..2026-08-02`) and platform URL restoration update the
summary from the rows actually restored.

## Native examples and integration

`trellum/demo/reports/component-showcase/examples_heatmap_summaries.py` exports
`add_sections(ctx)`. It uses native DataSource, FilterBar, Grid, Panel, TabGroup,
DataTable and HeatmapChart components. RawHTML provides explanation only. Four
named sections use independent dataset IDs:

- `showcase_heatmap_summary_pair`: static off/on snapshots plus a filterable raw source table.
- `showcase_heatmap_summary_reducers`: filtered sum/mean/min/max/count and raw source rows.
- `showcase_heatmap_summary_normalization`: global versus row-relative raw means with independent header context.
- `showcase_heatmap_summary_eligibility`: absent/zero, null/string/boolean eligibility, signed/fractional values, and exact `drop_empty` behavior.

The main fixture has observation dates August 1/2, 2026 and iOS/Android
multipliers 1/2. Mon/Tue are supplied example categories, not computed dates.
Twenty facts aggregate into four cells `[0,90,180,270]`, sum 540, mean 135,
minimum 0, maximum 270, count 4. iOS/August 1 yields `[0,10,20,30]`, sum 60,
mean 15. The 30 contribution is deliberately split into 12+18 source facts.

The common showcase includes these four sections, reachable from its Heatmap
summaries link. Import the existing `HeatmapChart` from `trellum.components`.
The standard chart-options validator dispatches `heatmap_option_problems`
and reports `chart-summary-invalid` for malformed declarations, including
post-construction mutations. Native validation also retains structural
filter coverage and column checks.
