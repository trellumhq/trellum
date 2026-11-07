# Report comparison tables

`ComparisonTable` displays supplied values; it never subtracts, divides or
calculates period comparisons. Existing calls keep sign-based comparison colors,
including absolute reference columns such as Budget. `value_col` may still name
an absent column in legacy calls. When present, its cells receive emphasis.

Rebuilding adds a subtle theme-border divider before the first visible comparison
column, with DataTable's compact striped rows and numeric alignment. Set the
keyword-only `show_divider=False` to omit it. Wide tables scroll locally on phones.

Use keyword-only `comparison_context` for explicit periods, units, per-metric
directions and explanations. For example, with columns Metric, Previous, Current,
Change, Current reason and Change reason:

```python
ComparisonTable(
    df, "Weekly outcomes", "Current", ["Change"],
    columns=["Metric", "Previous", "Current", "Change"],
    comparison_context={
        "metric_column": "Metric",
        "period_labels": {
            "Previous": "Previous · 21–27 Oct",
            "Current": "Current · 28 Oct–3 Nov",
        },
        "change_labels": {"Change": "Change · vs previous"},
        "metrics": {
            "Revenue": {
                "direction": "higher", "format": "currency", "precision": 0,
                "change_measure": "relative_percent", "change_precision": 1,
            },
            "Cost per install": {
                "direction": "lower", "format": "currency", "precision": 2,
                "change_measure": "relative_percent", "change_precision": 1,
            },
            "Refund rate": {
                "direction": "lower", "format": "percent", "precision": 1,
                "change_measure": "percentage_points", "change_precision": 1,
            },
        },
        "reason_columns": {"Current": "Current reason", "Change": "Change reason"},
    },
)
```

The same declaration works with `dataset_id` and `DataSource`/`FilterBar`. Calculate
deltas upstream from unrounded inputs with matching baseline/current scopes.
Authors own scope, zero-baseline and maturity policies. Headers show supplied
labels only; no dates, favorable direction or missing reasons are inferred.

`metric_column` names a supplied row key. Duplicate keys share metric semantics,
while each row keeps its own reasons through filtering, local search and redraw.
Unknown metric keys and directions remain neutral. `direction` can be `higher`,
`lower` or `neutral`; other strings are treated as unknown. Only finite raw numeric
deltas are classified: higher/lower produce Improved or Worsened, and numeric
zero (including negative zero) is Unchanged, without a color. Formatted strings
remain supplied text and never produce inferred outcomes.

Numeric period cells named in `period_labels`, and `value_col` when present, use
the metric's `format`: `number`, `currency` (dollars), `percent` (fraction input) or
`ratio` (x suffix). `precision` and `change_precision` are integers from 0 to 12;
defaults are 2 and 1 respectively. Unknown formats or invalid precision raise
`ValueError`. Labels, metric keys, reasons and existing strings render as text.

Each metric declares the meaning of every supplied `compare_cols` value:

| `change_measure` | Supplied raw input | Display | Outcome |
| --- | --- | --- | --- |
| `relative_percent` | Fraction, e.g. `0.108` | `+10.8%` | Sign and declared direction |
| `percentage_points` | Points, e.g. `1.6` | `+1.6 pp` | Sign and declared direction |
| `absolute` | Metric unit, e.g. currency `-1.625` | `-$1.6` with default change precision | Sign and declared direction |
| `value` | Absolute reference in metric units | Metric format, without a plus sign | No change inference |
| Unknown or omitted | Supplied value | Supplied text or raw numeric text | Neutral |

This modest contract applies one measure per metric to all its comparison columns.
Use separate tables for different measures of the same metric. Period precision
does not change the raw delta. Percent absolute deltas use fractions, matching
the metric unit; percentage-point deltas are already in points.

Null and nonfinite period/change values display an em dash with the row's reason
from `reason_columns`, or “Unavailable” if no reason is supplied. A valid zero or
other numeric value stays valid even if a reason is supplied. Valid current and
baseline cells remain visible when a change is unavailable. Zero baseline and
immature cohort are examples of author-supplied reasons, not inferred states.

Color is supplementary to the outcome text. With no `conditional_formats`,
context-enabled numeric changes use metric-aware green/red colors. If
`conditional_formats is not None`, including `{}`, only those supplied rules
color values anywhere in the table; outcome text still describes the declared
semantics. Rules evaluate supplied raw values in the context-enabled path, so
relative-percent rule thresholds use fractions. Without context, existing rules
and sign-color behavior remain unchanged.

Reason columns are hidden from the default column set and CSV. Explicit `columns`
selects exactly what the author requests, including reasons if desired. Metadata
can use a metric key/reasons that are absent from the visible projection. CSV uses
original selected column names and raw units/precision, with no generated outcome
columns. Display headers, currency signs and percentage scaling do not change CSV.
The existing DataTable population path searches before its 200-row display cap
and exports all matching delivered rows; see [report data tables](report-data-tables.md).

The component showcase's `examples_comparisons.add_sections(ctx)` provides static,
filterable, explicit-rule and legacy absolute-reference examples.

Keyword-only `cell_shading` passes explicitly to the shared DataTable renderer;
see [threshold cell shading](report-data-tables.md#optional-threshold-cell-shading).
It compares raw values **before** comparison display formatting. For example,
retention fractions displayed as 25% use a target of `.25`, not `25`. A declared
target applies to every numeric cell in that column. Do not put a universal
target on a heterogeneous-unit Current/Change column; use a homogeneous table
or leave shading off. Comparison metric directions and automatic sign colors do
not supply targets or threshold inheritance. Explicit colored-number rules keep
precedence, and existing comparison outcome text is independent of target fills.
