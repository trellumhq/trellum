# Ordinary LineChart results

Newly built ordinary `LineChart` components use keyword-only
`result_presentation=True`. Set `result_presentation=False` to retain the
previous presentation. It must be a Python `bool`; positional constructor
arguments are unchanged. Old payloads without `line_result_presentation: true`
retain their behavior. Area, Combo, other chart families and the metrics
workspace do not use this presenter.

```python
LineChart(frame, "day", "orders", dataset_id="orders")
LineChart(frame, "day", "orders", result_presentation=False)
```

An owned text overlay uses the existing 220px plot allocation (160px compact,
320px roomy). It does not change the canvas size, axes, aggregation, point
values, nearest tooltip, legend or chart library. Ordinary completed lines and
measured zeros have no extra visible context. Hiding every legend entry or
zooming to a region without points does not make a result empty. Null gaps and
isolated finite points remain as supplied by the existing plotting path.

The presenter classifies full final plotted vectors after declared availability
rules, before legend visibility and zoom. A finite zero is usable. A nonempty
result without usable observations says so, and declared unavailability retains
its supplied reason and existing detailed availability note. It never creates
points or turns unavailable observations into no matches. Existing aggregation
semantics, including undeclared missing contributor behavior, are unchanged;
use `series_availability` when incomplete raw inputs must withhold a point.

For an actual live source, [live result state](live-result-state.md) is the
authority. With no usable prior observations, a pending request says “Loading
requested data” and a failed request says “Could not load requested data”. A
completed valid empty selection says “No matching data”. A missing/invalid live
snapshot says “Result unavailable”, never confirmed empty. Unknown or unready
ordinary datasets say “Data not ready”; an empty ready base/static source may
say “No data supplied”. Empty local selections say “No matching data”.

When a pending or failed request retains usable prior observations, wrapping
text above the plot names the requested parameters and the displayed snapshot
or query parameters separately. Only normalized binder parameters are used;
unknown displayed parameters say “unknown”. Returning to the displayed
selection or applying a matching result removes the transient context. A scoped
chart reads only its own engine-filtered rows and follows the registered
`_scopes` parent chain (at most 64 edges, with cycle protection) to its live
ancestor. Its retained context identifies a local subset of that displayed live
parent. It adds no inherited-filter controls and propagates no filters.

Status changes only update text, without rebuilding Chart.js or resetting legend
visibility/zoom. Native theme/reentry draws of identical marked results retain
their legend visibility and x window. Source replacement and changed data keep
the native draw path. Text uses `textContent`, readable theme roles, wrapping,
and polite status regions; identical messages are not rewritten. No focus moves
or new Retry/Refresh controls are introduced. Consumer subscriptions and ordinary
line listeners are released on replacement and removal.

Query completion is not source freshness. No retry is promised: the binder owns
its existing single initial-429 retry and generic failure policy. Ordinary
chunked datasets expose installed rows, not per-chart chunk failure/completeness
metadata. This presenter cannot identify pending/failed chunks or assert complete
population coverage. Their existing loading and completeness behavior is
unchanged; result text describes installed selected rows only. Unknown snapshot
completeness and truncated live responses keep their existing aggregation rules.

CSV and optional header summaries still use actual displayed chart values.
Successful empty, completed and measured-zero reports/sections capture their
current presentation. Native report/section PNG and PDF capture admission is
unchanged: pending or failed live work can block capture, including unrelated
sources under the existing report-wide guard. No stale-scope exception, export
footer or canvas-only text rasterizer is added. Consumers capturing the raw
canvas directly receive its native chart paint; the result message belongs to
the report DOM and therefore requires a report or section capture.

`demo/reports/component-showcase/examples_line_results.py` provides deterministic
database-free native examples with independent IDs and named-section filters.
Hosted pending/error states use genuine ReportContexts with SQLite snapshots
and intercepted binder requests in `testing/test_line_results_browser.py`.
