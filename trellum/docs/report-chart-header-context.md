# Chart header context

Every built-in chart accepts the keyword-only `header_context` argument:
`LineChart`, `AreaChart`, `BarChart`, `StackedBar`, `ComboChart`,
`DoughnutChart`, `HeatmapChart`, `FunnelChart`, `TreemapChart` and
`ScatterChart`.

```python
LineChart(
    daily, "day", "orders", "Completed orders",
    dataset_id="orders",
    header_summary={"stat": "sum", "format": "number"},
    header_context="Orders; sum of matching completed orders",
)
```

The authored plain-text description appears below the title and any LineChart
summary, before chart controls, legend and plot. It can name units, describe a
period, identify scope, or give a short definition. Its value is escaped and
rendered literally; newlines are preserved and long text wraps inside narrow
panels. Empty and whitespace-only values add no markup or spacing.

The text is authored metadata. It does not track filter selections, infer
periods or time zones, or validate that its meaning matches the supplied data.
Write context that remains true after filtering, such as “USD; sum of matching
completed orders” or “Supplied snapshot: 1–6 October”. It performs no
calculation and changes neither chart data nor existing `LineChart` summary
behavior.

Context adds height above a chart. It does not change plot height, including
LineChart's 220/160/320px sizes and a custom ComboChart height. It is separate
from chart titles, so Grid's `show_titles=False` continues to hide titles while
leaving independently authored context visible. Omit `header_context` when a
Grid tile should contain no context. Existing title omissions in static
StackedBar and DoughnutChart remain as before.

The option is keyword-only and defaults to `None`; older positional calls
remain valid. No generated data payload or report schema changes when the
option is omitted. Rebuild reports to include authored context in generated
HTML. See [MIGRATIONS.md](MIGRATIONS.md#unreleased-chart-header-context).
