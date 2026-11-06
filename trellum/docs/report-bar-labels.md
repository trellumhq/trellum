# Bar value columns and optional stack labels

`BarChart` adds keyword-only `show_values=None`. Automatic values apply to
horizontal, nonstacked category bars. Date recognition uses the existing ISO
`YYYY-MM-DD` prefix check on the first plotted label; those horizontal date
series retain their previous presentation. `show_values=True` explicitly enables
values for supported horizontal nonstacked bars, including dates. `False` omits
them and releases the reserved width. Vertical bars and `BarChart(stacked=True)`
receive no new labels. Existing `show_labels` retains its separate semantics;
its current static forwarding limitation remains.

```python
BarChart(df, "channel", "revenue", "Revenue", dataset_id="sales",
         horizontal=True, y_format="currency")
BarChart(df, "channel", "revenue", "Revenue", dataset_id="sales",
         horizontal=True, show_values=False)
```

The right-aligned column reserves at least 62 CSS pixels and adapts to measured
compact text. Each visible series uses its actual bar center, including grouped
bars. Values come directly from the plotted datasets after filters, sorting,
Top-N/Other and ratio division. They use the current numeric-axis formatter;
signed values retain signs, measured zero remains zero, and unavailable values
show a dash. No additional aggregation, unit conversion or precision policy is
introduced. Tooltips retain the existing value path.

Labels are omitted when measured bounds would collide or leave the canvas. A
panel at 260px or narrower receives no value column. Wider panels also omit it
if the measured category context plus column leaves less than 80px for the plot,
or the column would take over one third of the canvas. This preserves the
existing category ticks and height policy; it does not wrap or truncate source
categories. Existing phone category clipping can still occur. Exact plotted
values remain in tooltips and CSV, even if a label does not fit.

## StackedBar

Keyword-only `show_totals=False` and `show_percentages=False` accept booleans.
Both default off. Enabling either adds context below the unchanged plot container.
Authors must use additive quantities: the options do not make rates, averages
or ratios additive.

```python
StackedBar(df, "day", ["revenue"], "Revenue by source", dataset_id="sales",
           stack_by="source", normalize_toggle=True,
           show_totals=True, show_percentages=True, value_format="currency")
```

Totals sum currently visible bar segments at each x in absolute `value_format`
units. They exclude line overlays. In normalized mode they read the existing
absolute `_absData`. Totals sit above the actual visible stack, with modest
internal top padding; disabling totals releases that padding. No fixed axis
bounds or new normalization rules are applied.

Percentages appear only in genuine live pivot normalization (`dataset_id`,
`stack_by`, and `normalize_toggle` with Percentage selected). They use the existing
all-series denominator: hiding a legend series changes the visible total but
leaves each remaining share unchanged. Whole-percent rounding can produce
rounded shares that do not sum to 100. A label must fit both dimensions of its
actual segment and the plot bounds. Its black/white contrast is chosen against
the theme card and composited fill. Tiny or dense segments retain tooltips.
Measured all-zero buckets have valid absolute zero totals and no valid shares;
empty charts have neither labels nor fabricated totals.

The new labels require complete finite nonnegative numeric inputs. Eligibility
is checked on filtered raw rows before legacy null-to-zero coercion. Any signed,
incomplete or nonfinite input conservatively withholds the new labels for the
whole chart and explains why. Duplicate configured source columns, ambiguous
pivot delimiter keys, generated Other/source identity collisions and inconsistent
plotted versus supplied populations also withhold them. These safeguards do not
repair legacy aggregation, legend identity, tooltip or CSV behavior. Optional
context states that totals include visible bars and, in normalized mode, that
shares include hidden series. With both options off there is no new stack
context, eligibility scan or reserved space.

## Preserved path differences

- Static `StackedBar` renders supplied wide rows, omits its title, always shows
  its legend, and does not emit breakdown or normalization controls or line
  overlays. Static
  `show_legend=False` still does not hide that legend.
- Live stacks without a pivot do not normalize, even if their existing Percentage
  control is selected. New percentage labels are withheld and context explains
  the requirement. Static percentage labels are likewise unavailable.
- Static currency ticks and live pivot ticks can differ: live currency ticks
  without an overlay or normalize toggle retain compact-number units. New
  totals always use declared `value_format`; no axis-format behavior is changed.
- Static BarChart still uses supplied `y`/`y_cols`, rather than evaluating `ratios`,
  and retains its existing formatting/bounds/sorting/`show_labels` omissions.
- The live `net_color` path retains its existing orientation/stacking omissions;
  specifying horizontal there does not create a horizontal value column. Ratio
  BarCharts retain their existing stacking omission, but requesting `stacked=True`
  still disables the new value column.
- Live overlay tooltips retain their existing currency formatting. The existing
  shared tooltip presentation does not display every configured footer.
- CSV exports exactly the current plotted datasets, including hidden series.
  Selecting Percentage retains existing percentage-valued CSV columns; absolute
  units are not added to that schema. Source data and dataset arrays are never
  changed by label calculation.

Existing grid/category styling, chart libraries, legends, responsive heights,
color identity and cross-filter controls are retained. Rebuild reports to emit
`bar_values` and `stack_labels` tags. Untagged payloads retain their previous
presentation; older runtimes safely ignore the additive metadata. Explicit
`dataset_id` still takes precedence over `static=True`. Existing positional
arguments and dataclass subclass fields retain their positions.

`component-showcase/examples_bars.py` provides deterministic category, weighted
ratio, grouped, Top-N, fixed, filtered, breakdown, normalized, tiny, zero, signed,
incomplete and overlay examples via `add_sections(ctx)`.
