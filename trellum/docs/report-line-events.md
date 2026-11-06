# LineChart dated events

Newly built ordinary `LineChart`s use keyword-only `event_labels="grouped"`.
They retain native annotation lines and translucent range bands, replacing full
in-plot event names with small numbered canvas markers. An expandable native
list below the unchanged plot contains every eligible event's original name,
exact `date`, optional `end_date`, and type. Names and dates are literal text.

```python
LineChart(daily, "day", "orders", "Orders", static=True)
LineChart(daily, "day", "orders", "Full labels", static=True,
          event_labels="full")
LineChart(rows, "day", "orders", "Selected orders", dataset_id="orders")
```

Only `"grouped"` and `"full"` are accepted, including when options are changed
before rendering. `"full"` retains the existing in-plot labels and adds no list.
Static, dataset-bound wide-series, ratios and dynamic stack paths support the
option. Explicit `dataset_id` still takes precedence over `static=True`; omit
`dataset_id` for an independent static example. Area, Bar and Combo charts and
two-argument `window._buildAnnotations(events, labels)` calls keep full labels.
Generated payloads without `event_labels` also keep full labels.

Starts within about 52 CSS pixels of the first start form a group. Groups follow
x position, with original event order breaking ties. Font measurement and edge
clamping can merge further groups to avoid marker collisions; every event is
retained. `2 · 3` means group 2 contains three events, not a metric value. These
numbers are local to the current width and selection and may change after a
resize, zoom, filter or reveal. A range starting before the displayed interval
anchors at the left edge. Only existing positioned lines/ranges intersecting
the displayed x interval are listed. Weekday highlights stay plain lines.

The collapsed summary reads “Events in displayed range” with event/group counts.
Open it by keyboard, click, or tap. Clicking/tapping a plotted marker opens its
chart's list, scrolls to the group and focuses its heading, without selecting
chart data or starting drag zoom. Expanded lists scroll locally at about 280px
and wrap long names/dates on phones. Open state and surviving focused items are
retained across redraws. No eligible dated events, no data or a non-date category
axis adds no visible row or reserved space. Existing annotation type controls,
legend, nearest tooltip, chart values, colors and plot heights stay unchanged.
Type controls retain their existing pointer-only span behavior.
If a focused event disappears, focus returns to that chart's summary; if the
entire disclosure disappears, it returns to the named chart instead. Focus in
unrelated controls stays where it is.

## Retained date and visibility limits

This is presentation of the existing annotation population, not a calendar or
timezone interpretation. The same builder still applies source order, type
visibility (including URL/localStorage), active scope, first/last category-label
clipping and preceding-label snapping. Details always use the original event
associated with each `ev_line_N`/`ev_box_N`; exact dates are never inferred from
snapped labels. Complete original ranges remain visible in details when marks
are clipped. Weekly ranges can snap to shorter bands or collapse to a line.
An event after the last weekly category label remains excluded even if its date
falls in that last weekly bucket.

Global `events.yaml` and local `annotations.events` loading retain their different
handling of studios, type defaults and report visibility overrides. The global
loader's 90-day **start-date** cutoff still applies; local declared events have
no such cutoff. Existing date parsing/category mapping, shared localStorage
semantics and weekday-only annotation refresh behavior are unchanged. No new
timezone, continuous coverage or calendar inference is made.

## Exports and rebuilding

CSV and chart-only data downloads retain supplied values and existing names.
Native canvas PNG/PDF capture includes markers. Normal report export preserves
the current collapsed/expanded disclosure state; it does not expand lists or add
a scope footer. Collapsed exports show marker numbers/counts without full names;
expand the list before report export to include event names. Long expanded lists
retain their local scroll viewport in screen capture.

Rebuild generated reports with matching framework assets for this default
appearance change. Existing generated payloads without the flag remain full
labels; there is no artifact schema change. Use `event_labels="full"` to retain
the old presentation when rebuilding.
