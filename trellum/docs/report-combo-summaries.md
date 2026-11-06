# ComboChart header summaries

`ComboChart.header_summary` is keyword-only and defaults to `None`. Opting in
adds a right-aligned header beside the existing title, above the plot. It does
not alter plot height, axes, grouped legends, nearest-series tooltips, data
labels, values, aggregation, stacking, filtering, month/context options, chart
library or CSV. An omitted option emits no summary payload, markup or space.
Existing positional and dataclass subclass argument positions are preserved.
An explicit `dataset_id` still takes precedence over `static=True`.

```python
ComboChart(
    df, "day", ["spend", "fees"], ["orders", "visits"], "Daily activity",
    dataset_id="activity", bar_labels=["Daily total", "Fees"],
    line_labels=["Daily total", "Visits"],
    bar_format="currency", line_format="number",
    header_summary={
        "stat": "sum", "scope": "axis_series", "axis": "left",
        "series": "Daily total",
    },
)
```

The only accepted keys are `stat`, `scope`, `axis`, `series`, `label`, `format`.
The required fields are:

| Field | Values |
|---|---|
| `stat` | `sum`, `mean`, `min`, `max`, `last` |
| `scope` | Exactly `axis_series` |
| `axis` | `left` (bars, `y`) or `right` (lines, `y1`) |
| `series` | Nonblank exact final caption, at most 120 characters; never a numeric index |

Optional `label` is nonblank text of at most 60 characters. Its default combines
Total/Mean/Minimum/Maximum/Last shown with the caption. Optional `format` is
`number`, `currency`, `percent`, `chips`, `ratio` or `plain`. Labels, captions,
scope descriptions and unavailable reasons use text nodes. Invalid declarations
raise `ValueError` both at construction and render, including after mutation;
unhashable values do not leak incidental type errors. The pure
`combo_summary_option_problems(comp)` returns `[("chart-summary-invalid", message)]`
for invalid declarations and `[]` otherwise.

Exactly one final dataset must match both the selected axis and final caption.
The same caption on the opposite axis is independent. Duplicate captions on the
selected axis are unavailable, with an explicit ambiguity reason. Missing
captions are unavailable; no first-series, numeric-index or source-name fallback
is used. Ordinary bound label fallbacks still determine the final identity:
missing or blank labels fall back to the source column. Independent static
charts retain their existing `zip(columns, labels)` truncation and actual label
values. A summary follows these rules rather than changing them.

The statistic covers every entry of that individual final series, after
existing bound `groupBy` and display order, including signed values and measured
zeros. `last` means the final displayed entry, including an unsorted static
snapshot. `mean` is the unweighted mean of displayed entries. Stacked bars use
the selected dataset's individual values, not cumulative stack height. Legend
hiding, isolation and zoom do not change scope. There are no cross-axis totals,
inferred weights, ratios, unique-person claims or warehouse-completeness claims.

Only the selected original input column is inspected: raw static values before
cleaning, or raw currently filtered bound rows before existing aggregation and
zero-fill. Every value must be a finite native number, excluding bool, string,
null, missing and unsafe integers (outside ±9,007,199,254,740,991). Every selected
final entry and final result must also be finite and safe. Missing raw values
therefore make the summary unavailable even when the plot historically converts
them to zero. Unselected invalid values do not withhold an eligible summary.
Empty input is unavailable. Nothing is skipped. A dash and concise reason
explain unavailability; valid results have no blank reason row. Sum and mean
use compensated arithmetic, preserving residuals such as `[1e15, 1, -1e15]`
(sum 1; mean 1/3). Min/max/last do not depend on a successful sum.

## Formats and existing behavior

Defaults follow the actual selected-axis renderer:

| Path | Left default | Right default |
|---|---|---|
| Bound | Supported `bar_format` currency/number/percent, otherwise currency | Supported `line_format` currency/number/percent, otherwise number |
| Independent static | number | currency |

Static Combo does not currently forward `bar_format` or `line_format`. Its
summary defaults preserve the compact-number left and currency right renderer,
even if a declared static percent format differs. An explicit summary format is
allowed. `sum` is rejected when either the selected axis's declared format or
the explicit summary format is percent/ratio; this applies even when the static
plot ignores that declared format. The active plot/tooltip formatter is never
changed. Existing compact number formatting rounds 13.5 to `14`; use explicit
summary `format="plain"` to display `13.5`.

[Combo axes and legends](report-combos.md) documents the retained static/live
differences: static `y_max`/`y1_max` are ignored, static label/factory forwarding
is unchanged, and bound rows retain current additive aggregation. Reusing a
source column on both axes currently adds it twice in bound `groupBy`. This
legacy plotted result is preserved: spend `[30, 60]` becomes `[60, 120]` and its
summary sum is 180. CSV preserves all values, order, headers and duplicate
captions, including hidden series and this source reuse behavior.

Both ordinary and explicit-height charts retain their existing plot dimensions
and a single title. Header context and month context are independent; long
summary text wraps outside the canvas. Section/report PNG and PDF captures
include the DOM header. Raw-canvas images exclude it. Capture admission remains
unchanged; native capture still has the existing raster dimensions, resource
and pagination limits of the report export pipeline. Its PDF is a single tall
raster page, not a semantic or paginated document; a long showcase can produce
large files. Native captures remove interactive filter bars from the image.

The existing multi-date-filter URL writer can serialize a custom range using
another bar's active All preset. This is reproducible on the historical OFF
runtime and is unchanged here. Incoming explicit scoped range URLs hydrate the
correct independent filters; a subsequent serialized URL may lose that custom
range. No engine, filter or URL-sync changes are part of this component option.

## Native examples and validation

`demo/reports/component-showcase/examples_combo_summaries.py` exposes
`add_sections(ctx)`. It adds four named sections with independent dataset IDs
and date/platform filters: static off/on, bound axis identities and reducers,
context/custom-height/stacked bars, and eligibility/legacy formats. The base
oracle is four complete facts on August 1/2, 2026 × iOS/Android, with factor
`day * platform_multiplier`, where multipliers are 1/2: spend `10*f`, fees `2*f`,
orders `3*f`, visits `20*f`. Full daily spend is `[30, 60]`, orders `[9, 18]`,
fees total 18, visits total 180. The iOS/August 1 subset is spend 10/orders 3.
Variants are explicitly separate teaching populations; the zero example uses
one measured fact per date. No custom renderers or invalid declarations are
included in the report.

The generated payload uses dedicated `combo_summary` and, for static charts,
a compact `combo_summary_input_reason`, never duplicated raw rows. The renderer
forwards this as `comboSummary`/`comboSummaryInputReason` only for native
`combo_axes` charts. Dedicated DOM (`data-combo-summary`) and description tokens
are independent of generic and StackedBar summaries. Tagged Combo charts own
subscriptions, structural lifecycle notifications and abortable listeners from
the first draw, including summary adoption/removal. Replaced/removed canvases,
configs and sources dispose their ownership; native scope re-entry rebinds.
Workspace leases and untagged legacy payload behavior are preserved. Rebuild
reports to acquire Python, runtime and CSS together; older runtimes ignore the
new metadata and cannot display the summary. No artifact or host schema,
dependency, version or migration changes are required.

The shared component showcase includes these four sections through its
Combo summaries link, beside the ordinary Combo examples. The native
chart-options validator dispatches `combo_summary_option_problems`, including
mutated declarations, while accepting historical objects without this option.
Import the existing `ComboChart` from `trellum.components`.

Browser checks use native generated reports, vendored assets, actual downloaded
CSV and native section/report captures.
