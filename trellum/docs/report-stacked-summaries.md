# StackedBar header summaries

`StackedBar(..., header_summary=None)` adds no header, space or summary payload
by default. The new argument is keyword-only; existing positional and subclass
constructors retain their order. Other chart families have their own contracts.

```python
StackedBar(
    facts, "day", ["events"], "Delivered events",
    dataset_id="events", stack_by="channel", value_format="number",
    normalize_toggle=True,
    header_summary={
        "stat": "sum", "scope": "absolute_series", "series": "UA",
        # Optional: "label": "Delivered UA events", "format": "number"
    },
)
```

The only declaration keys are `stat`, `scope`, `series`, `label`, `format`.
`scope="absolute_series"` is required. `series` is a required nonblank exact
**final bar-series caption**, at most 120 characters. Numeric indices and implicit
selection are unsupported. Exactly one final bar must match. Line overlays,
including a right-axis line with the same caption, never participate. If a
breakdown or filter removes the caption, max_stacks groups it away, or diverging
measures produce duplicate captions, the summary shows a dash and a concise
reason. It never picks a different series. An exact generated `Other (1)` can be
selected, but changing tail count can remove that identity. Existing Other
grouping is unchanged.

`stat` is `sum`, `mean`, `min`, `max` or `last`. It uses all entries of the
selected final rendered bar series **after existing filtering, aggregation,
pivot, sorting, max_stacks and Other**, and **before percentage normalization**.
The renderer reads `_absData` when present, otherwise `data`. Legend hiding and
zoom do not redefine the population. Last means the final **displayed** entry;
it makes no inference about the latest date. Existing zero-filled missing
category/date slots count as zero in the mean. Means are unweighted. No grand
total, weighted ratio, calendar weighting or unique-person count is inferred.

The default label includes the selected caption and statistic. Optional `label`
is nonblank text up to 60 characters. Labels and scope/reasons use text-only DOM
content. The supported formats are `number`, `currency`, `percent`, `chips`,
`ratio`, `plain`. An explicit summary format wins; otherwise a supported declared
`value_format` wins, falling back to `number`. These are always the **original
units**, also while the chart is in Percentage mode. `sum` is rejected if either
the declared `value_format` or explicit summary format is percent/ratio. Changing
the display format cannot make a declared rate additive.

Declarations are checked on construction and rendering. The pure
`stacked_summary_option_problems(comp)` helper reports mutated declarations for
shared validation. Lists, bools and malformed/unhashable field values raise
`ValueError`, rather than incidental lookup errors. Runtime missing/ambiguous
identities are valid declarations with unavailable results.

## Eligibility and numerical limits

All **currently filtered original bar input columns**, including unselected
categories/columns, must contain finite native numbers. Bool, string (including
numeric-looking strings), null, missing and unsafe integer inputs withhold the
summary. Line input columns are ignored. Static charts retain only a compact
original-input eligibility reason captured before legacy cleaning; they do not
copy source rows. Original plotted values and aggregation remain unchanged.
Finite signed and fractional values remain allowed. This conservative guard
covers the delivered population only: it cannot establish warehouse completeness
or fix an already mis-grained source.

Every selected final entry must also be usable; invalid entries are never
skipped. Empty series/data show a dash; valid measured zero shows zero. Results
must be finite with safe integer precision. A compensated scaled reduction avoids
avoidable intermediate overflow in means; min/max/last do not calculate an
unused sum. An unsafe/nonfinite sum or result shows a dash. No source cap,
rounding, serialization or aggregation policy is added.

## Presentation, controls and export compatibility

Summaries reuse the existing right header slot and wrap independently of plot
height. `header_context` stays independent. Bound titles appear once. Static
StackedBar's legacy supplied-title omission is preserved with and without a
summary. Visible scope says “Original units”; accessible scope and unavailable
reasons are attached with owned `aria-describedby` tokens. Valid results have no
blank reason row.

Native local and shared breakdown/Absolute/Percentage controls retain their
selected state. Chart legends remain independent. Tagged native StackedBars own
their subscriptions and control/theme/double-click handlers from their first
render, permitting later summary adoption/removal without legacy duplicate
handlers. Scope exit, DOM/canvas/config/control-owner replacement and source
removal/reinitialization reconcile only those owned bindings. Workspaces retain
`_fwLiveWrap` leases. Other chart-family summaries are not routed through this
reducer.

Native section/report PNG and report PDF include summaries. Raw canvas exports
contain only the existing plot. Capture admission is unchanged. CSV columns and
units remain those of the plotted datasets: in the existing pivot Percentage
path CSV exports normalized percentages, while Absolute exports original
aggregated values. Summaries do not change that existing difference, and they do
not repair historical aggregation or CSV behavior.

## Native examples and integration

`demo/reports/component-showcase/examples_stacked_summaries.py:add_sections(ctx)`
adds four named sections with independent dataset/filter identities: static
snapshots off/on, five reducers with a same-caption independent line overlay,
local/shared breakdown and normalization controls (responsive/fixed heights),
and eligibility/zero/signed/rate/Other/ambiguous cases. Its 12 complete synthetic
facts are dates 2026-08-01/02 × iOS/Android × UA/RT/Organic, with base 10/20/30,
day multipliers 1/2 and platform multipliers 1/2. Full UA entries are [30, 60],
total 90, mean 45, min 30, max/last 60; RT 180, Organic 270, overall 540.
These are additive events, never distinct people. iOS plus August 1 yields UA 10.

Shared integration owned by the report lead:

- Add `examples_stacked_summaries` to the showcase generator's existing
  `from . import (...)`, then call
  `examples_stacked_summaries.add_sections(ctx)` after the Traffic examples.
  Add this jump link to its existing navigation prose:
  `<a href="#sec-stacked-summaries-—-static-off/on">Stacked summaries</a>`.
  The other native anchors are `sec-stacked-summaries-—-selected-reducers`,
  `sec-stacked-summaries-—-local-and-shared-controls` and
  `sec-stacked-summaries-—-eligibility-and-identity`.
- In `validation/checks/chart_options.py`, import
  `stacked_summary_option_problems` from
  `trellum.components.charts._stacked_summary`; in the `StackedBar` dispatch,
  iterate its `(check_id, message)` results and call
  `result.fail(check_id, message, component="StackedBar", section=sec)`:

  ```python
  from trellum.components.charts._stacked_summary import stacked_summary_option_problems

  if _comp_name(comp) == "StackedBar":
      for check_id, message in stacked_summary_option_problems(comp):
          result.fail(check_id, message, component="StackedBar", section=sec)
  ```
- Link this page from the framework documentation index and compatibility notes.
  No public component export is needed: `StackedBar` is already exported.
- Suggested changelog: “Add opt-in StackedBar header summaries for one exact
  final bar series in original units, with conservative input eligibility.”
