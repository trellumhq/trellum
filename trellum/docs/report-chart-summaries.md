# Optional BarChart and AreaChart header summaries

`BarChart` and `AreaChart` accept keyword-only `header_summary=None`.
Omission preserves their existing markup, payload, spacing, plots and heights.
`header_context` is a separate opt-in plain-text description. Existing
`LineChart.header_summary` calls and calculations retain their original API.

```python
BarChart(df, "category", "orders", "Selected orders", dataset_id="orders",
         header_summary={"stat": "sum", "scope": "plotted_series"})
AreaChart(df, "day", ["orders", "returns"], "Returns band", stacked=True,
          dataset_id="orders", y_labels=["Orders", "Returns"],
          header_summary={"stat": "mean", "scope": "plotted_series",
                          "series": "Returns", "format": "number"})
```

Declarations require `stat` (`mean`, `sum`, `last`, `min`, `max`) and
`scope="plotted_series"`. `series` is a nonnegative integer index or a unique
actual plotted label. Omit it only when exactly one series is plotted.
Boolean indexes, unknown keys and invalid options fail construction and render;
report validation also checks declarations changed after construction.

The optional `label` is nonblank text of at most 60 characters. Defaults are
“Shown average”, “Shown total”, “Last shown”, “Shown min” and “Shown max”.
`format` is `number`, `currency`, `percent`, `chips`, `ratio` or `plain`.
An explicit format wins; otherwise a supported chart `y_format` wins, then
`number`. The resolved format is serialized even for static charts. This
option does not change existing static axis formatting.

The summary reads **one final plotted value vector**, after current filters,
aggregation, sort, Top-N and Other. It never combines separate plotted series.
Top-N with Other includes Other; dropping Other excludes that value. `last`
means the final displayed finite entry, including a final Other bar, rather
than the latest date. Legend hiding and zoom do not redefine the vector.
Stacked Area uses the selected raw band, not its cumulative plotted position.
The summary's title and accessible description explain this scope without
adding a footer.

Null and nonfinite entries are excluded, real zero is included. No usable
values and sum/mean overflow display an em dash. Existing bound-chart zero-fill
before plotting stays intact: these summaries cannot recover missingness
already replaced with zero. Authors must declare valid statistics and explain
population, units and completeness when relevant. An average of displayed
ratios is unweighted, never a weighted overall ratio. `sum` is rejected for
declared bound Bar ratio series and resolved `percent` or `ratio` formats.
Counts of distinct users or other nonadditive metrics still need author care.

Series selection follows the renderer. Static charts zip value columns and
labels, so a short label list truncates the actual plotted series. Bound charts
fall back to column names for missing or empty labels. Bound ratio Bars use
ratio declaration labels. Bound `net_color` Bars produce one synthesized
dataset named by the chart title; its vector already sums the declared value
columns per category. `net_color` combined with ratios is rejected when a
summary is requested because that combination has competing metric meanings.
Static ratio/net-color flags retain their existing static column rendering.

Titles may still be suppressed by a host or `Grid(show_titles=False)`; summaries
and authored context remain independently available. Current chart libraries,
normalization, scales, styling, heights and constructor positional arguments
are preserved. Other chart families do not gain this API here.

The deterministic `examples_chart_summaries.add_sections(ctx)` showcase uses
four named sections and independent dataset IDs. Its filters affect only the
corresponding example. It covers static off/on, horizontal categorical Bars,
a selected stacked Area band, and final Top-N/Other scope.
