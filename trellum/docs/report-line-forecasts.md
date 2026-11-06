# Supplied forecast styling on LineChart

`LineChart(..., forecast_series=...)` marks selected, author-supplied wide series
as forecasts. It is keyword-only and accepts a list or tuple of non-bool integer
series positions or exact displayed labels. Positions and labels refer to the
ordinary wide series (`y`/`y_labels`), or the declared ratio labels in a bound
ratio chart. Labels must identify exactly one plotted series; duplicate
selections, invalid positions and captions shared by another series are errors.
Declarations are copied on construction and checked again when rendered.

An enabled series keeps its supplied values and order. It receives a dashed line
and hollow markers whose outline follows the series color and whose center uses
the current card surface. Its existing HTML legend chip includes a matching
symbol. Existing adaptive marker density, isolated observations, plot height,
nearest tooltip, exports and chart geometry remain in effect. On narrow widths,
the longer forecast symbol can make a legend chip slightly wider and may change
which row a chip wraps onto; the plot height does not change.

Forecast styling works with static lines, bound ordinary wide-series lines and
bound ratios. An explicit `dataset_id` continues to take precedence over
`static=True`. For a static chart, omit `dataset_id` and pass `static=True`.
`None` and an empty sequence add no forecast declaration to the payload. Dynamic
`stack_by` and `stack_by_options` are not supported when a nonempty declaration
is supplied because the resulting series identities depend on data.

This option only styles data explicitly supplied by the report author. It does
not calculate, extrapolate or anchor forecast values. To show an observed and
forecast horizon, supply distinct dates and values for each series and leave
the other series null outside its own horizon. Static LineChart preserves those
null gaps. Bound sum aggregation currently zero-fills missing input values; a
bound actual-versus-earlier-forecast comparison should therefore supply complete
values for every series on each compared date. This presentation option does
not change aggregation or establish general missing-value correctness.

```python
LineChart(frame, "day", ["observed", "supplied_forecast"], "Daily values",
          y_labels=["Observed", "Forecast"], forecast_series=["Forecast"],
          colors=["#2563eb", "#c2410c"], static=True)
```

Rebuild generated reports with matching framework assets to show the styling.
