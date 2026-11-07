# Optional daily month context

`LineChart`, `AreaChart`, vertical `BarChart`, `StackedBar` and `ComboChart`
accept keyword-only `month_context=False`. It must be a boolean, including
when an instance is changed before rendering. Enabling it declares that the
x labels represent **daily reporting dates**; it does not detect cadence.
Do not enable it for weekly observations that happen to have ISO date labels.

```python
LineChart(daily, "reporting_date", "orders", dataset_id="daily_orders",
          month_context=True)
```

The default emits no month configuration, plugin instance, DOM or spacing.
`True` is incompatible with horizontal bars, any configured value `sort`, or
any configured `top_n` (including zero). Those options keep their existing
behavior when month context is off. Category-series `stack_sort` and
`max_stacks` remain supported. Explicit `dataset_id` still takes precedence
over `static=True`.

The actual plotted categories must be nonempty, strictly increasing, unique,
canonical `YYYY-MM-DD` strings with real Gregorian calendar days (years
0001–9999). Empty results, invalid days, duplicates, unsorted categories,
timestamps, hourly or month labels omit the context without changing the
legacy plot. Runtime diagnostics expose the internal chart
`_fwMonthContext.status` and `.reason` (`empty`, `invalid-date`,
`duplicate-date`, `unsorted-dates`, or `unavailable-scale`). A later valid
result restores context. There is no warning banner or data conversion.

A single 24 CSS px text row immediately below the plotting container names
the visible calendar months. Neutral 1px dividers inside the plot mark the
midpoint between adjacent category centers where their month changes,
including offset bar scales. Missing days stay compressed; missing months
are not inserted. The row uses full month/year, abbreviated month/year, then
`YYYY-MM` as space permits. If none fits, its visual label is omitted; exact
full month/year remains in accessible DOM text and the span title. Every
visible label includes its year. Zoom clips spans to the current plot viewport
without changing supplied observations or selections. Event details follow
the month row when present. Header context and event descriptions coexist.

Plot heights, date ticks, scale bounds, datasets, legend, CSV values/order,
tooltips and chart styling remain unchanged. This option computes no monthly
means, sums, coverage counts or completeness claims. Rebuild existing report
HTML to include this runtime. No host or export API change is needed.
Native report and section PNG/PDF DOM captures include the row and dividers.
Explicitly capturing a canvas alone includes only the plot and dividers, not
the surrounding text row; the existing axis date ticks remain.
