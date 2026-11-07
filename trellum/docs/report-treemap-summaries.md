# Treemap chart header summaries

`TreemapChart(..., header_summary=None)` preserves the existing output by
default. The option is keyword-only; all existing positional arguments remain
valid. An enabled summary adds a wrapping header above the existing plot,
independently of `header_context` and `show_percentages`. The title appears
once. Chart heights, plugin grouping/sizing, fitting leaf labels, hierarchy key,
supplied colors, details, hover tooltips, filters and libraries are unchanged.

```python
TreemapChart(
    facts, ["channel", "campaign"], "synthetic_usd",
    title="Supplied campaign amounts", dataset_id="campaign_facts",
    value_format="currency", header_context="Synthetic additive USD snapshot",
    header_summary={"stat": "mean", "scope": "leaf_entries"},
)
```

## Declaration

Only `stat`, `scope`, `label` and `format` are accepted. Malformed declarations,
unknown keys and invalid combinations raise `ValueError` at construction and
render, including after mutation. Setting the option to `None` disables it.

| Field | Contract |
| --- | --- |
| `scope` | Required, exactly `"leaf_entries"`. |
| `stat` | Required: `"count"`, `"sum"`, `"min"`, `"max"` or `"mean"`. |
| `label` | Optional nonblank text, at most 60 characters, rendered as text. Default labels: Leaf entries, Leaf total, Minimum leaf, Maximum leaf, Mean leaf. |
| `format` | Optional number, currency, percent, chips, ratio or plain. Count permits only number/plain and always displays the full integer. |

Numeric summaries default to the treemap's **actual existing formatter**:
number, currency or percent. Unsupported `value_format` declarations have
always fallen back to number; the summary follows that fallback unless an
explicit summary format is supplied. Formats affect display only. Percent
uses the existing fractional-value formatter, without changing arithmetic.

Sum rejects either a declared percent/ratio `value_format` or a percent/ratio
summary format, even when the other format is overridden. Authors must choose
valid additive units; a format declaration cannot establish additivity.

## Exact population and conservative eligibility

The population is the **final leaf rectangle entries** of the current plugin
result (`raw.l === group_cols.length - 1`), after existing grouping. These are
the same leaves shown in full-path details. Parents are never included in a
leaf reducer. Multiple source rows at one full path produce one aggregate
leaf; the same leaf name under different parents remains separate.

Every current supplied or filter-matching final leaf participates, including
leaves outside the view and leaves without fitting labels. Details disclosure,
hover, colors and optional tile percentages do not change this population.
It is not source-fact count, unique people, visible labels, matrix areas or
path count. No warehouse completeness is inferred.

Count observes final plugin entries independently of numeric eligibility and
geometry area validity. Empty completed results count as zero. A missing or
removed source has no completed result and shows a dash, never a fabricated
zero. Count does **not** claim all source categories survived legacy blank
parent suppression, nor that all counted entries can be drawn correctly.

Numeric reducers require all of the following:

- Every current raw value is a native finite nonnegative number at safe
  snapshot precision. Booleans, strings, null/missing values, Decimal values
  and unsafe integers are ineligible. Static charts keep a compact eligibility
  bit before row iteration/serialization can coerce a supplied value; bound
  charts inspect the current filtered raw input directly.
- Every hierarchy name is nonblank after the existing renderer's coercion.
  Whitespace-only names are blank. Static names retain their existing Python
  string/date conversion; bound names retain their existing null-to-empty and
  JavaScript string conversion. The summary does not reconcile these rules.
- The final leaf source-child membership covers every supplied tree row
  exactly once. Blank/suppressed groups withhold numeric summaries instead of
  changing legacy grouping. This comparison checks only the delivered input.
- Every final aggregate leaf and the selected result are finite, nonnegative
  and no larger than JavaScript's safe integer limit (9,007,199,254,740,991).

Unavailable summaries show a dash and a concise visible reason distinguishing
invalid raw values, blank/suppressed hierarchy, missing final leaves, incomplete
leaf coverage and unsafe leaf/result arithmetic. There is no empty reason row
for a valid numeric result. The header owns accessible scope and reason tokens;
independent header-context descriptions remain intact.

Mean is the **unweighted arithmetic mean of aggregate leaf entries**, not the
mean of source rows and not weighted by area. Compensated arithmetic adds
leaf/count terms so mean does not require an unused overflowing sum. Min/max
also do not require a raw total. Sum withholds an unsafe aggregate result.
Finite floating-point rounding and upstream snapshot precision remain; this
option does not reconstruct exact decimal arithmetic or missing upstream data.

A zero value is valid when present among final entries. A numeric reducer of
no final leaves is unavailable, including when legacy all-zero sizing drops
the leaves. Missing input is never relabeled as zero. Legacy signed/all-zero
geometry remains unsupported; the summary does not repair it. Ordered tuples
of existing stringified hierarchy names retain the details identity contract:
dotted names stay separate, while distinct original types that stringify to
the same path can still merge under legacy grouping.

## Lifecycle, captures and CSV

Repeated render/theme changes, option/config/canvas/source replacement,
bound/static transitions, scoped-source re-entry and DOM removal dispose the
renderer-owned subscriptions, lifecycle listener, theme listener and detach
observer. The existing workspace LiveWrap continues to own queued draw leases.
Removing the option clears only the owned header, scope and reason, preserving
context, details disclosure and focus. No shared engine changes are required.

Retained native filter limitation: with multiple date FilterBars, the shared
URL writer currently scans all bars for an active preset. Another section's
`All` preset can supersede a custom range in the written URL. In-session date
filters and explicit dataset-qualified `YYYY-MM-DD..YYYY-MM-DD` URL ranges
still filter summaries correctly. The summary does not change URL behavior.

Native capture admission remains unchanged and requires an actual completed
report. Section PNG, report PNG and PDF include the header in the current
scroll/disclosure scope. Exporting only the raw canvas excludes the HTML header.

In a mixed native report loading the ordinary chart CSV delegate, the existing
generic treemap CSV is **header-only** (`label,value`), because treemaps do not
populate category labels for that exporter. Summary on/off leaves those bytes
unchanged. This option adds no populated full-path CSV schema. Use the ordinary
DataTable in the examples for a meaningful **source-row grain** CSV; source-row
grain differs from final aggregate leaves.

## Native examples and validation

`demo/reports/component-showcase/examples_treemap_summaries.py` exposes
`add_sections(ctx)` and contributes exactly four named sections:

1. Treemap summaries — off and on.
2. Treemap summaries — filtered leaf reducers and count.
3. Treemap summaries — independent context shares and colors.
4. Treemap summaries — zero invalid blank and duplicate leaves.

The independent native datasets are `showcase_treemap_summary_leaves`,
`showcase_treemap_summary_context` and `showcase_treemap_summary_eligibility`.
Each FilterBar lives with its source in a named section. Examples use native
Grid, Panel, TabGroup, DataSource, FilterBar, TreemapChart and DataTable. RawHTML
contains explanation only.

The main fixture contains twenty facts across August 1/2, 2026 and iOS/Android.
Day factors are 1/2 and platform factors 1/2. Paid/UA has split weights 4+6,
Paid/RT 20, Organic/Store 30 and Organic/Web 40. Final full-population leaves
are 90/180/270/360: sum 900, count 4, mean 225, min 90 and max 360. Selecting
iOS and August 1 gives 10/20/30/40: sum 100, count 4 and mean 25. Parent values
also total 900 but never combine with leaves to make 1800. All amounts are
synthetic additive USD; no real-report data parity is claimed.

The shared showcase includes these sections through its Treemap summaries link.
The shared chart-options validator dispatches `treemap_summary_option_problems`
and reports `chart-summary-invalid` for malformed or mutated declarations.
Import the existing `TreemapChart` from `trellum.components`.
