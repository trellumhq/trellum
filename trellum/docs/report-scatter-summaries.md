# Scatter chart header summaries

`ScatterChart(..., header_summary=None)` keeps the existing presentation by
default. `header_summary` is keyword-only. It adds a wrapping header above the
existing fixed-height plot, independently of keyword-only `header_context`.
The title appears once. Dots, axes, colors, legend, tooltips, zoom, normalized
bubble radii, libraries and chart CSV behavior retain their existing contracts.

```python
ScatterChart(
    observations, "spend", "revenue", color_by="channel", dataset_id="observations",
    x_format="currency", y_format="currency",
    header_context="Synthetic supplied observations; select dates and platform",
    header_summary={"stat": "mean", "scope": "point_entries", "axis": "x"},
)
```

## Declaration

Only `stat`, `scope`, `axis`, `label` and `format` are accepted. Invalid types,
unknown keys and unsupported combinations raise `ValueError` at construction
and render, including after mutation. `None` disables the option.

| Field | Contract |
| --- | --- |
| `scope` | Required, exactly `"point_entries"`. |
| `stat` | Required: `"count"`, `"min"`, `"max"` or `"mean"`. |
| `axis` | Required `"x"` or `"y"` for min/max/mean; forbidden for count. |
| `label` | Optional nonblank text, at most 60 characters. Rendered as text; markup is escaped. Defaults: Point entries, Minimum x/y, Maximum x/y, Mean x/y. |
| `format` | Optional number, currency, percent, chips, ratio or plain. Count allows only number/plain and always displays the full integer. |

Without an explicit summary format, coordinate summaries use the selected
axis's **actual legacy formatting**: number, currency or percent. Scatter axes
with any other format already fall back to number; the summary follows that
fallback. An explicit summary override can use the richer supported formats.
A format changes display only; it cannot recover upstream precision or certify
completeness. Percent uses the existing fractional-value formatter; summary
arithmetic does not multiply coordinates by 100.

## Population and arithmetic

The scope is the final current supplied or report-filtered **point entries**
across all datasets, including legend-hidden entries and points outside zoom.
Duplicate observations contribute separately. It does not count unique names,
source facts, painted dots or people. Native filters update the current entries.

Count is the sum of dataset entry lengths, including entries with missing or
invalid coordinates or sizes. Empty count is zero. It does not certify that a
dot or bubble can be drawn.

For min, max and mean, **every** current entry must have both finite numeric x
and y before Chart.js parsing/coercion. Missing/null coordinates, strings,
booleans, date strings and nonfinite coordinates withhold the summary. An empty
coordinate population is unavailable. The header shows a dash with a brief
reason. Valid negative and zero coordinates remain valid; a zero mean is zero.
The option never removes, coerces or edits chart points.

Min/max reduce only the selected coordinate. Mean is its unweighted arithmetic
mean over all entries; it is never a ratio of axes, weighted by bubble size or
summed across charts. Compensated addition uses source units when the absolute
sum bound is safe. For larger finite coordinates it normalizes before addition
and rescales the mean, avoiding avoidable sum overflow. A nonfinite result at
floating-point extremes is withheld with a reason. Floating-point rounding and
upstream precision limits remain. Min/max do not depend on a cross-point sum.

Coordinate eligibility is independent of bubble size eligibility. These
summaries describe coordinate pairs; they do not certify bubble drawing.
Normalized pixel radius is never summarized. Existing raw size/tooltips and
radius scaling are unchanged. No duplicated source record payload is added.

Static `color_by` retains pandas grouping, including omission of missing
categories. Bound grouping retains its existing string keys, `Other` fallback
and first-seen order. Summary populations use those resulting datasets. The
option does not reconcile existing static/bound grouping differences.

## Lifecycle and downloads

Renderer re-entry retires its own filter subscription, theme listener,
double-click reset handler and detach observer. Source/config/canvas replacement,
bound/static transitions and option removal update or remove only the owned
summary markup and description token; independent header-context and unrelated
`aria-describedby` tokens persist. Workspace queue/disposal remains with the
existing leased LiveWrap. No global engine or binder behavior is added.

The existing generic chart CSV delegate iterates `chart.data.labels`, while
Scatter datasets contain points and no category labels. In mixed reports that
load that delegate, Scatter CSV remains **header-only**, with dataset column
names. A standalone Scatter report may not load that delegate at all. This
option does not introduce a scatter CSV schema or export renderer. The native
examples include a DataTable labeled **Source observations — current source
rows**; its CSV provides the actual filtered source rows. Source-row scope is
separate from final point-entry scope, particularly for static color grouping.
Native section PNG, report PNG and PDF capture use the ordinary report APIs.

## Native examples and integration

`demo/reports/component-showcase/examples_scatter_summaries.py` exposes
`add_sections(ctx)`. Call it once from the showcase generator. It contributes
four named sections with independent `showcase_scatter_summary_coordinates`
and `showcase_scatter_summary_context` datasets and section-local date/platform
filters. Static off/on, all supported coordinate statistics and count,
independent header context, ordinary/bubble plots, duplicate/missing/zero cases
and initially hidden long escaped labels use shared native components.

The deterministic eight-point fixture is two dates (2026-10-01/02), two platforms
(iOS/Android) and two channels (UA/Organic). Day factors are 1/2 and platform
factors 1/2. Base spend is UA 10 / Organic 4 USD, revenue 15/5 USD and supplied
size 100/40. All entries have spend sum 126 and revenue sum 180, mean x 15.75,
mean y 22.5, min x/y 4/5, max x/y 40/60 and count 8. iOS day 1 gives count 2,
mean x/y 7/10 and max y 15; Android day 2 gives count 2, mean x/y 28/40 and
min y 20. The data is synthetic and does not claim an original-report or
unique-person grain.

Shared validator dispatch should import
`scatter_option_problems` from `trellum.components.charts._scatter_summary`.
For each `ScatterChart` in `_check_chart_options`, emit each returned
`(check_id, message)` with component `ScatterChart` and the current section.
The helper is pure and returns `chart-summary-invalid` for malformed declarations.
No common public export is needed; ScatterChart is already exported.

The existing shared grain diagnostic assumes aggregation by x and warns when
value columns repeat within x. Scatter preserves independent pairs, so that
heuristic needs a family exemption in the shared diagnostics: skip
`_detect_static_columns` for ScatterChart, while retaining structural filter
coverage. This is separate from summary validation and applies equally with
the option off. The deterministic fixture intentionally includes duplicate x
coordinates; changing or deduplicating it would change the population.

Link this page from the report component docs/navigation. Proposed changelog:
“Add optional ScatterChart header summaries for explicit point-entry counts
and x/y minimum, maximum or unweighted mean, preserving existing plots.”
