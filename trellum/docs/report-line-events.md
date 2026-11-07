# LineChart dated events

Newly built ordinary `LineChart`s use keyword-only `event_labels="adaptive"`.
They retain native annotation lines and translucent range bands. Sparse labels
show dates and names in a 40px rail above the unchanged plot (48px on phones).
Narrow charts show date buttons plus a wrapping, visible name key beneath the
plot. No eligible dated events means no rail, key or reserved space.

Colliding controls merge into literal counts and original start-date spans, such
as “3 events · 1 Sep–3 Sep”. These are counts, never lookup numbers or a claim
that the separate events form one continuous duration. Each original mark and
range band remains. A “View all N events” button opens every eligible event in
chronological order in a local scroller bounded to 280px. Nothing is paginated
or silently omitted.

Click, tap or keyboard-activate a native event button to open exact names,
original dates, optional range end dates and types beside its chart. The
associated marks are highlighted. Close or Escape returns focus to the owning
control; a surviving source identity retains focus through redraws and
regrouping. When all events disappear, owned focus returns to the named chart.
Unrelated filter focus is preserved. Phone event buttons and Close have at
least 44px touch targets. Event names are literal text and wrap in the key and
details. Plot tooltips, zoom, controls and metric filters retain their behavior.

```python
LineChart(daily, "day", "orders", "Orders", static=True)
LineChart(daily, "day", "orders", "Full labels", static=True,
          event_labels="full")
LineChart(daily, "day", "orders", "Numbered compatibility markers", static=True,
          event_labels="grouped")
LineChart(rows, "day", "orders", "Selected orders", dataset_id="orders")
```

Only `"adaptive"`, `"grouped"` and `"full"` are accepted, including when options are changed
before rendering. `"full"` uses in-plot labels with collision protection:
crowded labels move into the ordinary event-details disclosure, while sparse
labels remain in the plot. Grouped mode keeps its separate numbered markers
and expandable list.
Static, dataset-bound wide-series, ratios and dynamic stack paths support the
option. Explicit `dataset_id` still takes precedence over `static=True`; omit
`dataset_id` for an independent static example. Area, Bar and Combo charts and
two-argument `window._buildAnnotations(events, labels)` calls keep full labels.
Generated payloads without `event_labels` also keep full labels.

## Explicit grouped compatibility mode

`event_labels="grouped"` keeps numbered canvas markers and a collapsed list.
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
Canvas-only PNG/PDF remains canvas-only; off-canvas rail and event details
require native report/section capture. Adaptive labels do not add metric data
or DOM details to a canvas image.

Native report/section captures preserve the rail and visible name key. Their
export clone expands any open adaptive details to include all selected rows;
clusters additionally include a compact exact event key with every eligible
name, type and original date/range. The screen's open state, local scroll and
focus stay unchanged. Captures exceeding 16,384 pixels in either dimension or
32 megapixels at capture scale fail with an explicit smaller-selection message,
rather than cutting off event identities. Full-label collision disclosures keep
their existing clone expansion. Explicit grouped captures retain their current
collapsed/expanded list and local scroll viewport.

The shared capture pipeline must call
`window._fwPrepareEventCapture(doc, root, options.scale)` in its `onclone` hook,
after caller clone changes and before computed-paint conversion. This helper
only changes captured descendants. A canvas-only target has no event descendants.

Rebuild generated reports with matching framework assets for this default
appearance change. Existing generated payloads without the flag remain full
labels; there is no artifact schema change. Use `event_labels="full"` or
`event_labels="grouped"` to retain a previous presentation when rebuilding.
