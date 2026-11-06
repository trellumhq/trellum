# Report treemaps

`TreemapChart` draws a supplied hierarchy using the bundled Chart.js treemap
plugin. Rectangle area represents the declared `value` column. Without
`color_col`, one theme palette color identifies each first-level group and its
descendants. A wrapping key names those groups. Colors are local to the chart;
they persist through filtering during that page session, rather than forming a
shared mapping across charts or reports. Palettes repeat when exhausted.

```python
TreemapChart(
    frame, ["region", "channel"], "revenue", "Revenue composition",
    dataset_id="sales", value_format="currency", show_percentages=True,
)
```

The existing positional arguments remain `df`, `group_cols`, `value`, `title`,
`color_col`, `value_format`, `dataset_id`, `static`. The new keyword-only
`show_percentages=False` option must be a boolean. Positional dataclass subclass
arguments retain their positions. An explicit `dataset_id` takes precedence
over `static=True`. For independent static data, omit `dataset_id` and specify
`static=True`.

## Labels, details and units

Leaf labels show the actual leaf name and existing formatted value at the
native label font size (normally 12px). Enabling `show_percentages` adds a share
line. The whole label disappears if all lines do not fit; labels never shrink
to fill a tiny rectangle. Parent captions also disappear when their full name
does not fit. Geometry, grouping, row order, spacing, borders and plot heights
are unchanged. The shared responsive plot remains 320px at a 1280px desktop
viewport and 180px at a 390px phone viewport; other existing breakpoints remain.
The key and collapsed details add context height outside the plot.

Native expandable details retain every actual plugin leaf, including labels
that do not fit. Each row contains the ordered full hierarchy path, formatted
value, exact underlying numeric value and an available share. Details and
tooltips provide shares regardless of whether tile shares are enabled. Their
display uses one decimal place; the detail share's title contains the unrounded
numeric percentage. A tiny nonzero share can display `0.0%` without becoming
zero. Values retain the existing `value_format`: `currency` uses `$`, `percent`
uses fractional inputs, and `number` or the existing fallback uses number
formatting. The exact numeric line retains source units before formatting.
No unit is inferred from a column name.

Paths are identified by ordered tuples, not joined display strings. North /
Store differs from South / Store; `["A.B", "C"]` differs from `["A", "B.C"]`.
Names are inserted as DOM text. Parent nodes are excluded from detail totals
so values are not counted twice. Existing tooltip interaction is preserved,
with the deepest matching full path added to its title. The bundled plugin's
point selection can include both parent and leaf values. Details work with keyboard and
touch and keep their expanded/collapsed state across filters, theme changes and
refreshes that retain the component's DOM. Rows are replaced with the current
result. No legend visibility or cross-filter control is introduced.

## Share population and eligibility

Report authors must supply additive quantities. The component cannot determine
whether summing a rate, average or ratio is meaningful. Shares use **every
matching supplied or report-filtered value in the current result**, including
tiny and zero categories, rather than only tiles with visible labels. They do
not claim to represent rows outside a limited live-query result.

Eligibility is checked on the incoming values before the legacy missing-value
to zero conversion. A nonempty population must consist entirely of finite,
nonnegative numbers with a finite positive sum. Numeric strings and booleans
are not measured numbers. Empty, incomplete, signed, nonnumeric and nonfinite
populations have unavailable shares. Complete zero-total shares are undefined.
Blank hierarchy names or a mismatch between source totals and plugin leaves
also prevent new shares. Explanatory context remains visible when shares are
unavailable. No values are generated, discarded, resampled or changed into
absolute values for percentages.

For a leaf with incomplete incoming observations, details show supplied values
and explicitly identify the legacy size value, rather than presenting its
coerced zero as a complete measurement. Static metadata preserves supplied
value text where serialization would normalize NaN/nonfinite values to null.
Live payloads cannot recover distinctions lost before reaching the browser.

## Supplied colors and pre-existing sizing/export limits

Setting `color_col` retains the existing categorical or numeric color behavior.
It reads the **first source child** of each rectangle, not an aggregate over
the group. Source row order can therefore change colors without changing the
value total. Categorical palette indices follow current encounter order and
can change when filtering removes earlier categories. Missing colors retain
their existing index fallback.

Numeric supplied colors retain interpolation between the first and last native
palette colors using the current supplied minimum/maximum. No quantitative
legend is added: the component has no declared aggregate metric, color units,
fixed domain or missing-color contract. The visible encoding note discloses the
first-child semantics and row-order dependence. Aggregate a meaningful color
metric at the intended grain before authoring a chart if that is needed.

Unsupported legacy inputs keep their sizing behavior:

- All-zero data can create nonfinite rectangle geometry. Signed data can
  create negative or out-of-bounds rectangles. New labels omit invalid geometry;
  the feature does not redefine plugin sizing or claim part-to-whole shares.
- Live null hierarchy names become blank strings, and blank parents can
  suppress descendants. Details enumerate actual plugin leaves, not invented
  replacement leaves. Static group values retain their existing Python string
  conversion (`None` can consequently be a literal group name).
- Missing amounts still size as zero. The new context distinguishes incomplete
  incoming values from observed zero when that information is available.
- When the shared chart download handler is loaded, existing treemap CSV has
  the `label,value` header and no data rows. Treemap-only builds can lack that
  handler entirely. Exact details do not add a handler, redefine the export
  population or repair CSV.

Rebuild reports to receive the default hierarchy colors, fitting labels, key
and details. New payload metadata is additive. Older payloads default to tile
shares off; older markup without the new context wrapper safely keeps the
canvas renderer. An old runtime ignores the new option, so rebuild with a
matching framework runtime to use the presentation. Shared theme definitions,
aggregation, chart libraries and export handlers are unchanged.

The `component-showcase/examples_treemaps.py` module supplies deterministic
static and filterable examples, repeated leaf names, long paths, tiny and dense
tiles, shares off/on, categorical/numeric colors and zero/missing/signed sizing
diagnostics. It exposes `add_sections(ctx)` for report composition.
