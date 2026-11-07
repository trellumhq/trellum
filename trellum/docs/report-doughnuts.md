# Report doughnuts

`DoughnutChart` renders a compact 170px ring beside a wrapping HTML legend.
When its own panel is narrow, the ring appears above the full-width legend.
Every category stays available, including tiny values and repeated static labels.
The component does not render its `title` as a heading; name the surrounding
section or Panel when a visible heading is needed.

```python
DoughnutChart(
    frame, "channel", "revenue", "Revenue by channel",
    dataset_id="acquisition", show_labels=True,
    center_total=True, center_label="Revenue",
    value_format="currency", precision=2,
)
```

The existing positional arguments are unchanged: `df`, `label`, `value`, `title`,
`dataset_id`, `static`, `cross_filter`, `show_labels`. The new options are
keyword-only:

| Option | Default | Meaning |
| --- | --- | --- |
| `center_total` | `False` | Show a labeled total inside the existing ring. |
| `center_label` | `"Total"` | Author-supplied label, rendered as text. |
| `value_format` | `"number"` | Explicit value units; never inferred from column names. |
| `precision` | `None` | Optional decimal places, integer 0–12. |

Number and currency values in the legend and center use full, locale-formatted
numbers rather than compact magnitude suffixes. Currency retains the existing
`$` convention and defaults to two decimal places. Percent values are fractions
(`0.12` displays as `12.00%`); ratio values use an `x` suffix. `plain` and `chips`
are also supported. Number display defaults to up to twelve decimal places.
Rounding affects display only: each legend value's title includes the exact
supplied value text and the chart value. CSV retains the existing raw chart
values and `label,value` header. Shared formatters are unchanged.
Unusually long center values use scientific notation to fit the ring, with the
full formatted total in the center's title. Percent formatting also uses
scientific notation when multiplying a finite fraction by 100 would overflow.

Report authors must ensure that values are additive before enabling a total.
Summing rates, averages or ratios generally does not produce a useful metric.
The component offers formatting for those units without inferring additivity.

## Population, visibility and filtering

Shares and the optional total describe **all categories in the current supplied
or report-filtered result**, including categories hidden with legend buttons.
They are not a claim about rows outside a limited live-query result.

Each native legend button hides/restores only its corresponding arc. Pressed
means visible, and hidden entries carry readable Hidden text. Hiding does not
change filters, values, shares or CSV population. Chart.js renormalizes its
visible-arc geometry; the tooltip and legend explicitly say that their shares
still use all categories. Hiding every arc shows a clear empty ring and a
Restore all action. Toggling keeps keyboard focus; restoring all returns focus
to the first legend entry. There is no category truncation or synthetic Other.

`show_labels=True` is honored for both static and dataset-bound charts. Labels
appear only at or above 3% of all categories and show one decimal place, just
like the legend shares. Hidden categories stay in that denominator.

Static inputs preserve every supplied row in source order, including duplicate
category labels. Dataset-bound charts retain the existing `groupBy` category
aggregation and sorting. Colors retain index-based palette assignment and
follow the actual dataset after a theme change; category colors are not stable
across changing category populations.

`cross_filter=True` retains arc-click filtering through the existing FilterBar
bridge. A matching category dropdown must exist in that FilterBar. Legend
buttons remain visibility-only. Use that dropdown to clear an arc selection.
A filter/theme redraw recreates the chart and resets arc visibility. The
pre-existing repeat-click selection-memory limitation after callback recreation
is unchanged; there is no additional selection banner or Clear control.

## Eligibility and existing data limitations

New totals/shares require a nonempty result containing only finite, nonnegative
numeric values and a finite sum. The original live rows are inspected before
legacy aggregation, so a null that aggregation converts to zero cannot claim
a complete total. Numeric strings and booleans are not measured numbers.

- Complete all-zero inputs have a valid total of zero; shares are undefined
  and display a dash with an explanation.
- Empty inputs mean no categories, with no measured total.
- Missing, nonnumeric, nonfinite or signed inputs, and overflowing sums, show
  a dash/reason for the new total/share context.
- Invalid supplied values remain text in the legend. For an incomplete live
  group, its supplied values are listed rather than representing its legacy
  coerced arc value as a complete measurement. Titles disclose both populations.

Chart.js's pre-existing signed/incomplete arc semantics and the live aggregate's
null coercion remain unsupported for part-to-whole interpretation. This feature
does not drop problematic rows, substitute absolute-value shares, regroup static
inputs or change legacy arc/CSV calculations. Serialization can normalize
nonfinite values to null; static supplied-value text discloses the original
input where available. A live payload cannot recover distinctions already lost
before it reached the browser.

## Rebuilding and exports

Rebuild reports to receive the compact ring and HTML legend. This intentionally
changes default layout and may increase height on narrow panels to keep names
and values readable. Enabling the center total does not change ring dimensions.
New metadata is additive. Old payloads still render, and older markup without
an HTML legend wrapper falls back to the native canvas legend.

Ordinary report PNG/PDF captures include the rendered HTML legend and optional
center with the surrounding report. Export APIs and footer presentation are
unchanged. CSV always includes all current chart categories regardless of arc
visibility. The `component-showcase` doughnut example module provides
reproducible complete, tiny/long, duplicate, filtered, zero, empty, incomplete
and signed cases using ordinary framework components.
