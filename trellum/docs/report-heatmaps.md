# Report heatmaps

`HeatmapChart(df, x, y, value, ..., show_values=True)` renders one summed value
per coordinate using the bundled Chart.js matrix renderer. Rebuild reports to
apply the presentation. All existing positional arguments retain their order;
`show_values` is keyword-only and accepts only `True` or `False`.

```python
HeatmapChart(
    observations, x="hour", y="weekday", value="orders",
    title="Orders by weekday and hour", dataset_id="orders",
    log_scale=False, show_values=True,
)
```

Use `DataSource` and `FilterBar` for interactive observations. An explicit
`dataset_id` still takes precedence over `static=True`. For an independent
static example, omit `dataset_id` and set `static=True`.

## Colors and the scale key

For finite nonnegative aggregated cells, the automatic palette uses the theme's
first chart hue, mixed toward white at the low end and black at the high end.
An explicit tuple of color stops retains its supplied stops and interpolation.
Colors convey magnitude, without favorable/unfavorable semantics.

The compact key gives numeric endpoints in `value_format` units (`number`,
`currency` or fractional `percent`). The existing positive-only extent is
unchanged: zero uses the low color and is named separately. Global constant
positive values and all-zero results show a single swatch and their value,
without claiming a range.

- `normalize=None` uses a global positive-value scale. `log_scale=False` is
  linear; `True` retains logarithmic coloring. With `None`, the existing
  automatic logarithmic rule remains: `max / max(min_positive, 1) > 10`.
  The key discloses automatic selection. The existing logarithmic floor at 1
  is disclosed when relevant; positive values below 1 clamp to the low color.
- `normalize="row"` or `"col"` uses each group's positive minimum and maximum
  with linear normalization, irrespective of `log_scale`. Its key is explicitly
  relative (0 to 1), without raw-unit endpoints. Colors do not compare raw
  quantities across groups. Uniform positive groups use the midpoint; zero uses
  the low color. If every positive group is uniform, the key names the midpoint
  rather than claiming a populated gradient range.
- Signed or nonfinite results retain legacy color calculations and rectangle
  geometry. A concise explanation replaces the nonnegative scale key. No signed
  scale or new nonfinite-value calculation is introduced.

Labels use the existing compact formatters and units. The renderer measures
text and rectangle space at the theme's label font size, uses contrasting black
or white ink, and omits labels that cannot fit. `show_values=False` hides cell
labels, including missing-coordinate dashes. Tooltips retain full numeric
aggregate values and units, including when a compact label cannot fit.

Supported nonnegative rectangles fit their actual axis slots on first render
and resize. The plot retains its existing row-count height rule; the key adds
space above it. Theme changes, filters, empty results and rebuilds update the
key and cells together. Legacy payloads without the `heatmap_style` tag keep
their legacy palette and label behavior, and old markup without a key remains
safe. Match generated assets and data by rebuilding the report.

## Zero, absence and retained data boundaries

An absent coordinate within the current x/y domains has a hatched cell, a dash
when it fits, and a “No observation” tooltip. No entirely absent categories are
inferred. A supplied zero is a measured cell, with low-color fill and a zero
label. Missing-coordinate presentation adds no synthetic observations to chart
values, aggregations or exports.

These existing rules remain:

- Static inputs stringify category labels, coerce nonnumeric/null values to
  zero, sum duplicate coordinates, and preserve first-seen category order.
  An all-null coordinate is therefore zero; `80 + null` is 80. Neither is
  reinterpreted as unavailable or as an incomplete subtotal.
- Bound inputs keep their existing JavaScript sum/coercion and sorted category
  order. JavaScript numeric precision and nonfinite behavior remain unchanged.
- `drop_empty=True` keeps only rows/columns containing a positive value. This
  known limitation can remove zero-only and negative-only groups.
- Live category maps and normalization/drop maps retain their existing key and
  inherited-property collision limitations. Category identity is not changed by
  this presentation.
- The shared CSV handler still depends on `chart.data.labels`, whereas matrix
  domains live in scale labels. Heatmap CSV can consequently contain no data
  rows. This presentation does not repair or promise a new CSV contract.

Missing cells use a patterned row background and coordinate lookup rather than
allocating an x-by-y dataset. Missing dashes are visited only when their measured
text and row height fit; the number of candidate columns is thus bounded by
paintable horizontal pixels. Genuine observations and category domains are
never capped or dropped. Very large domains still inherit the matrix renderer's
category-axis cost, row-count height and browser canvas limits; small slots may
carry only color, with values available on hover.

The component showcase's `examples_heatmaps.add_sections(ctx)` supplies complete
and explicit-palette matrices, zero versus absence, hidden values, unequal
row scales, automatic logarithmic coloring, independent live filters, all-zero
results, signed caveats and null/duplicate coercion examples.
