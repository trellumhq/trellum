# Area charts

`AreaChart(df, x, y, title, stacked=False, dataset_id=None)` uses the same
plot container and height behavior as before. The plot height is responsive to
the available viewport and is not set by an AreaChart size option.

Ordinary areas use a light fill of approximately 12% opacity and the existing
opaque 2px series outline. Series overlap around the chart's baseline. Use
this mode when the series are independent measures and should not be added.

`stacked=True` uses approximately 65% opacity bands with opaque 1.5px
boundaries. The first visible band fills to the origin; each following band
fills to the preceding visible series. The chart runtime follows visibility
when a legend item is hidden, so the remaining bands continue to show cumulative
geometry. This mode represents additive data: each series must be a component
that can be summed with the others. Do not stack overlapping totals or ratios.

The fill appearance is consistent for static DataFrames and charts connected
to a `DataSource`. For an explicit static fixture, omit `dataset_id` and set
`static=True` to declare the intentional absence of a dataset binding. As before,
an explicitly supplied `dataset_id` takes precedence. Labels,
order, values, colors, tooltips, legends, filters, exports, and chart sizing
retain their existing contracts. The lighter fills are an intentional
appearance change. AreaChart retains its existing
responsive plot container; it does not use LineChart height presets.

This change does not alter aggregation, sorting, missing-value handling, axis
scaling, zero-baseline behavior, or export data. In particular, the live path
continues to sum rows by x value, sorts category labels, and currently treats
missing quantities as zero, while static data preserves supplied row order and
null gaps. Static rendering also currently omits `y_format` and `show_labels`
from its runtime configuration. Missing calendar dates are not inserted, and
automatic scale baselines (including positive-only and all-zero data) retain
their existing behavior. These differences are outside the fill style change.
