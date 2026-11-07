# Grid theme color cycling

`Grid(..., color_cycle="theme")` optionally assigns successive colors from
the active theme palette to eligible single-metric LineChart, AreaChart, and
ordinary BarChart tiles. The default `None` preserves existing output. Slots
follow authored tile order, including tiles that have an explicit color or are
otherwise ineligible, so filtering and responsive wrapping do not shift them.

Explicit colors and category color declarations keep their existing meaning.
Category splits, multi-metric charts, stacked charts, and other palette-owning
charts are not eligible. Use `category_colors` when a category must retain its
identity across charts; Grid cycling distinguishes metric tiles only.
