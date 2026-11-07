# Grid theme color cycling

`Grid(..., color_cycle="theme")` optionally assigns successive colors from
the active theme palette to eligible single-metric LineChart, AreaChart, and
ordinary BarChart tiles. The default `None` preserves existing output. Slots
follow authored tile order, including tiles that have an explicit color or are
otherwise ineligible, so filtering and responsive wrapping do not shift them.

```python
Grid([
    LineChart(daily, "day", "orders", "Orders", dataset_id="daily"),
    AreaChart(daily, "day", "visits", "Visits", dataset_id="daily"),
    BarChart(daily, "day", "revenue", "Revenue", dataset_id="daily"),
], columns=3, color_cycle="theme")
```

The option is keyword-only; existing positional Grid arguments keep their
meaning. `None` and `"theme"` are the only accepted values, including after
mutation. Invalid values fail before rendering and produce the shared
`grid-color-cycle-invalid` report validation check.

Explicit colors and category color declarations keep their existing meaning.
Category splits, multi-metric charts, stacked charts, and other palette-owning
charts are not eligible. Use `category_colors` when a category must retain its
identity across charts; Grid cycling distinguishes metric tiles only.

Only direct chart tiles receive a fallback. A Panel or other wrapper does not
pass its slot to arbitrary descendants. Nested Grids own their settings, start
at slot zero when enabled, and keep ordinary colors when omitted. Reusing a
chart in another Grid or standalone does not retain the earlier assignment.
One complete bound ratio is a single metric; multiple ratios, category splits,
stacked charts, dual axes and net/outcome palettes keep their existing colors.

Static and filter-bound charts resolve slots against `getThemeColors()` on
entry, redraw and theme changes. Area/bar alpha, adaptive points, explicit
colors and source values remain intact. After the palette ends, slots cycle;
six distinct tile colors require six distinct entries in the active palette.
Native report PNG/PDF captures use the displayed active colors. Derived trends
should use the resolved source dataset color, not a second palette index.

`demo/reports/component-showcase/examples_grid_colors.py` exposes
`add_sections(ctx)` for an unwired comparison of six static and six filter-bound
charts with the option off/on. Its October 1–7, 2026 values are deterministic
synthetic daily observations. `daily_frame()` and `metric_grid(frame, bound=...,
enabled=...)` can also be reused in native report fixtures.
