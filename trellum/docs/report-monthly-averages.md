# Observed-day monthly averages

Ordinary `LineChart` and vertical `BarChart` accept keyword-only
`monthly_average=None`. The default emits no declaration, plugin, DOM or space.
This is independent of `month_context`; it does not enable the 24px month row.

```python
LineChart(frame, "day", "orders", dataset_id="daily_orders",
          monthly_average={"metric": "daily_additive", "aggregation": "sum_complete",
                           "start": "2028-01-30", "end": "2028-03-02", "unit": "orders"},
          series_availability=[{"series": 0, "required_columns": ["orders"]}])
```

The dictionary requires exactly these five keys. `metric` is exactly
`daily_additive`; `aggregation` is `unique` or `sum_complete`; `start` and `end`
are real canonical Gregorian `YYYY-MM-DD` dates, inclusive and ordered. The
period may cover at most 1200 calendar months, bounding coverage rows and work.
`unit` is nonempty plain text of at most 60 characters. Dictionaries are copied
and checked again at render. This is an explicit author declaration of one
ordinary additive daily quantity, such as orders or spend. It is unsuitable for
stocks, distinct users, cumulative measures, rates, forecast or maturity
inference. Multiple plotted series, ratios, percent/ratio formats, forecast
series, dynamic line splits, horizontal/sorted/top-N bars, stacked and net-color
bar modes reject the option. Legacy combinations remain available with `None`.
The count uses actual plotted series, preserving short static `y_labels` behavior.

Static snapshots require `aggregation='unique'`. Omit `dataset_id` and use
`static=True` for an independent snapshot; explicit `dataset_id` always wins.
Static observations are neither grouped nor reordered. Finite numeric values,
including signed quantities and measured zero, are observed; nulls are unknown.
Numeric strings, booleans and unsupported scalars cannot establish observations.
Bound `unique` requires one delivered contributor per supplied day after
filtering. Bound `sum_complete` explicitly declares additive contributions which
sum to one daily quantity. Every raw metric contributor must be finite numeric;
no partial subtotal is accepted. Finite-input overflow is unavailable.

## Selection and coverage

The base reporting interval is authored, never inferred from the first/last row.
Labels must be strictly ascending, unique, valid canonical daily reporting dates
within the selected interval; no timezone conversion, filling or resampling is
performed. Ordinary bound DataSources and ScopedDataSources intersect the base
interval with **every** active date range on the x column, including inherited
parent ranges. Open bounds use the base bound. Timestamp or non-range date
predicates are unsupported (actual All/empty filters are ignored). Non-date
category filters change observations, not selected-day counts. Disjoint ranges
have an explicit empty-intersection status. Loading sources show data-unavailable
context until ready. Live-query sources and ancestors are unsupported because
requested/displayed scope is not safely exposed; snapshot rows do not establish
warehouse coverage.

Daily values are checked against final plotted values after LineChart
`series_availability`. Declared gaps, including state and extra-input masks,
are excluded even with finite metric inputs. If incomplete raw inputs still
produce a finite legacy zero/subtotal, the entire overlay/table is withheld with
an explanation. Authors can use LineChart availability for complete-input gaps
or supply an explicit static daily snapshot. Bound bars keep their existing null
aggregation and require complete finite inputs for any plotted daily total.
Inconsistent plotted representations also withhold the option; plots are never
changed as a side effect. Invalid runtime scope/data shows a concise plain-text
reason which disappears when eligible filtering is restored.

Each month averages finite **daily** values, including zero and excluding unknown
or absent days. A summed daily bucket counts once regardless of contributor
count. Means use finite scaled arithmetic, retain unrounded values internally,
and display to one decimal in the table. Selected days are the inclusive
intersection with each calendar month. Observed days count unique finite daily
values. Partial calendar selection and observation completeness are separate:
a partial month can have every selected day observed. Entirely absent months
and empty results still have declared selected days and zero observations, a
dash average, and no guide. No chart categories or zeros are added.

## Display, interaction and capture

Dashed neutral foreground guides span supplied category positions for each month,
using adjacent-center midpoints and clipping to the current chart area/zoom.
They make no elapsed-time claim. Offscreen guides do not change selected-range
means. Crowded labels are omitted; exact values remain in the table. Axes,
plot heights, datasets, legend, tooltips and header summaries remain unchanged.
The native readable coverage block follows plot, optional month row, event
disclosure and availability context. It states exact selected dates, units,
observed-day averaging and zoom semantics. Rows remain in normal document flow;
headings and dates wrap on phones. Hiding the sole series hides its guides and
context. Filters, themes, resizing and rerendering restore current context with
one owned accessible description.

CSV keeps original labels/order and raw plotted daily values/nulls, without
monthly rows or columns. Native report/section PNG/PDF and section analysis
captures include the full rendered coverage. For this opted-in feature, isolated
plot analysis targets are excluded and direct plot/canvas analysis capture
rejects with “Capture the report section to include monthly-average coverage.”
A target containing both chart and coverage is supported. Ordinary chart
captures retain their existing behavior. Raw external `canvas.toDataURL()` is
outside context-aware report capture and cannot include surrounding DOM; it is
not complete scope evidence. No canvas/chart API is monkeypatched.

Rebuild HTML, data and runtime assets together. No host or database migration is
needed. `examples_monthly_averages.add_sections(ctx)` supplies four independent
native synthetic sections with checkable means, filters, zeros and unknown days.
