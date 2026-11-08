# LineChart dated events

Newly built ordinary `LineChart`s use keyword-only `event_labels="adaptive"`.
They keep every eligible native annotation line and translucent range band, and
add one small type-colored diamond per event inside the plot, at the top. Event
lines are drawn as quiet dotted strokes; types still determine the existing
colors and the report-wide annotation type controls still show or hide them.
No eligible dated events means no markers, row or reserved space.

Names are shown only where they fit. Each name is measured against the actual
chart area: it appears beside its own marker when the whole text fits without
covering another marker or name, and otherwise only the marker remains. There is
no phone breakpoint and no event-count threshold, so sparse short names stay
readable on a narrow chart, and names return as the chart widens, the date window
narrows, or an annotation type is hidden. Same-day and closely spaced events
stack as separate markers in up to five rows. Beyond that the extra markers share the
last row, and pressing there lists them. None is merged or dropped.

Click, tap or press a marker, or its name, to see the exact name and the
original date (and end date for a range) in a one-line detail below the plot;
the matching line or band is emphasised. Markers sharing one spot open a short
list of just those events. The single compact **All N events** control below the
plot opens a bounded list of every eligible event in chronological order, so
each event is reachable by keyboard. Nothing is paginated or silently omitted.
Escape closes the list or the selected detail and returns focus to the control
that owns it; a surviving source identity keeps focus across redraws. When all
events disappear, owned focus returns to the named chart. Unrelated filter focus
is preserved. Rows and details show names and dates only, never the type text,
because the annotation type controls already name each type. Names are literal
text. Pressing an event never starts metric filtering or drag zoom; plot
tooltips, metric clicks and zoom keep their behavior.

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
Canvas-only PNG/PDF includes the in-plot markers and any names that fit; it
excludes the DOM detail row and list, which require native report/section
capture. Adaptive events do not add metric data to a canvas image.

Native report/section captures preserve the markers and visible names. Their
export clone expands an open list to include every row; when names are collapsed
on the plot, it also adds a compact exact event key with every eligible name and
original date/range, unless the full list is already expanded. The screen's open
state, local scroll and focus stay unchanged. Captures exceeding 16,384 pixels in either dimension or
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
