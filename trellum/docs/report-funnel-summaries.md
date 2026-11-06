# Funnel header summaries

`FunnelChart(..., *, header_summary=None)` optionally describes **current stage
entries** above the existing funnel plot. The default adds no summary markup or
payload. Existing positional arguments, plot library, height, colors, order,
counts, percentage labels, tooltip percentages and CSV values are preserved.
There are no additional plot rows or labels.

```python
FunnelChart(
    df, "stage", "amount", "Supplied stage counts", dataset_id="stage_facts",
    header_context="Counts may overlap people; selected dates and platform",
    header_summary={
        "stat": "stage", "scope": "current_stages", "stage": "Completed",
        "label": "Completed entries", "format": "number",
    },
)
```

`header_context` and the summary are independent. The existing title is emitted
once. The summary includes a visible scope, a concise eligibility reason when
needed, and a canvas description that preserves other description tokens.

| Field | Contract |
|---|---|
| `stat` | Required: `stage`, `max` or `count` |
| `scope` | Required: exactly `current_stages` |
| `stage` | Required only for `stat="stage"`; nonblank string, at most 120 characters; forbidden for other stats |
| `label` | Optional nonblank string, at most 60 characters; defaults to Selected stage / Largest stage / Stage entries |
| `format` | Optional `number` (existing compact number helper) or `plain` (existing plain helper); default `number` |

The declaration must be a nonempty dict containing only these keys. Other
statistics, scopes, formats, malformed containers, booleans and unknown keys
raise `ValueError` at construction and render. `count` always displays an integer,
including with `number`; it does not compact an entry count into a fractional
magnitude. No currency, rates, sums, means, conversions or drop-offs are computed.

## Population and eligibility

A static chart retains every supplied entry in its original order, including
separate duplicate labels. A dataset-bound chart retains the existing `groupBy`
behavior: group contributions by the displayed string key, add within each group,
and sort the final stage entries by descending count. This does not establish a
sequential stage order. Native report filters run before this grouping. Every
category in the delivered result participates, including hidden entries and
entries outside a zoom window.

- `count` counts final stage entries independently of amount completeness. Empty
  results give **0**. It never counts unique people, distinct labels or original
  facts.
- `max` returns the maximum stage-entry amount. Every original contribution and
  every resulting grouped amount must be finite, numeric and nonnegative.
  Null/missing, strings, booleans, nonfinite or negative amounts withhold the
  summary. Empty results are unavailable; all-zero results give **0**. Overflow
  of a hypothetical sum across stages does not invalidate a finite maximum;
  overflow within a grouped stage does.
- `stage` matches the declared string against stringified displayed labels,
  independently of order. Exactly one static entry or bound grouped entry must
  match. Absent or duplicate static matches are unavailable with a reason. Only
  the matching entry's original contributions and its grouped amount must be
  eligible. Invalid amounts in another stage do not invalidate a valid selected
  stage. A real zero remains **0**.

Bound labels retain the existing string-key collisions: numeric `1` and string
`"1"` share an entry; missing/null labels and `""` share an entry. The legacy
aggregator uses ordinary JavaScript object keys and has existing limitations for
names such as `__proto__`, `constructor` and `toString`; normalize such source
labels before binding. This feature does not change that aggregator or recover
entries it omitted. Static duplicate labels remain independent.

Original bound contributions are inspected before legacy aggregation can
zero-fill or coerce them. The plotted values and CSV retain those legacy rules,
even when a summary is withheld. Static serialization can replace nonfinite
values with null; both are ineligible. No second copy of a source dataset or
provenance metadata is emitted. The summary cannot recover precision, missingness
or completeness lost before delivery. It makes no warehouse-completeness claim.
Stage amounts can overlap people; adding them would not establish a person count.

## Updates, downloads and capture

Native filters, re-entry, theme redraw, initially hidden tabs and replacement of
a source/config/canvas update the owned header. Removing the option removes only
the summary and its description token. Bound funnels keep one disposable scoped
filter subscription and one theme callback; replacing/detaching their canvas
retires that binding. Workspace reports retain the shared leased wrapper.

CSV continues to download `label,value`, in the same static order or bound
count-sorted order and with exactly the same plotted values. The header statistic
is presentation context and adds no export schema fields. A local button handler
also enables the existing CSV action in funnel-only reports; it stops propagation
so reports containing the ordinary chart delegate download once.

Native section capture includes the summary when selecting the enclosing report
section. Selecting only a canvas captures the plot, whose header is a sibling.
Native report PNG/PDF include the header. Existing capture readiness, live
identity and in-progress-data guards apply. Section capture uses a 2× image and
retains the existing 20 MiB payload limit; choose a smaller section if necessary.
Full-report exports retain the framework/browser canvas-size and PDF-page limits;
this option does not enlarge those limits or make captures raw-data exports.

## Native examples and integration

`demo/reports/component-showcase/examples_funnel_summaries.py` exposes
`add_sections(ctx)` and deterministic `facts()`. It supplies four named native
sections: static off/on parity; filtered stage/max/count; independent header
context plus summary in Grid; and duplicate/missing/zero snapshots. Each filtered
section owns its dataset ID and date/platform/stage controls. These are synthetic
component mechanics, with no production-report parity claim.

The twelve facts are two dates (2026-10-01/02), two platforms and three stage
labels. iOS day 1 is Opened 100, Started 60, Completed 20; day 2 is 80, 40, 0.
Android is twice iOS. Combined grouped entries are 540, 300, 60; `max=540`,
`count=3`. iOS day 1 selects Completed 20 and max 100. Android day 2 selects
Completed 0 and max 160. The static duplicate snapshot is Completed 10, Completed
20, Opened 50: max 50, count 3, selected Completed unavailable.

The component showcase includes these sections and a direct navigation link.
Shared chart-option validation detects declarations mutated after construction
and reports `chart-summary-invalid` before rendering.

Focused checks live in `testing/test_funnel_summaries.py` and
`testing/test_funnel_summaries_browser.py`. The native browser fixtures use
ReportContext, report diagnostics/validation and vendored assets. They compare
actual baseline Python/JavaScript/CSS output at 1280 and 390 pixels in light and
dark themes; phone checks are heavy. The reducer's Node checks exercise arithmetic
independently and are not themselves a full report validation.
