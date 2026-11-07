# Report date ranges

Use a `date_range` filter in an ordinary `FilterBar` with a `DataSource` (or an
existing live datasource binding). Edits and presets apply immediately.

```python
ctx.add_section("Recorded readings", [
    DataSource("readings", frame),
    FilterBar("readings", frame, filters=[
        {"type": "date_range", "column": "day", "label": "Recorded range",
         "default_days": 7},
    ]),
    DataTable(frame, title="Readings", dataset_id="readings"),
])
```

`default_days` and numeric `presets=[{"label": "7D", "days": 7}, ...]` subtract
the supplied number of days from the dataset maximum and clamp to its minimum.
Both endpoints are inclusive: 7D covers eight calendar dates and 14D covers
fifteen when every date has records. `days="all"` uses the recorded bounds;
`days="ytd"` starts at January 1 of the recorded maximum's year. Presets use
the latest **recorded** date/time, not today. They do not promise continuous
coverage or a complete latest day. A custom interval can contain no records.

A datetime64 column with a nonmidnight value enables hourly mode. The native
date fields are paired with Start time and End time selects containing the
existing 00/30-minute choices. `hourly_presets` accepts entries such as
`{"label": "1H", "hours": 1}`. Daily and hourly preset sets remain customizable;
no choices are removed by the presentation update.

The compact trigger displays locale-aware readable calendar dates. Formatting
uses the original date components and retains the time/offset suffix verbatim;
it does not convert the endpoints into the browser timezone. Its tooltip
contains the exact underlying endpoints. Long trigger text is ellipsized to fit
its panel, and opening reveals the endpoint fields. The floating picker stays
within the viewport, uses associated native labels and closes on outside click
or Escape. Escape returns focus to the trigger. When recorded bounds are
unavailable, selection is disabled with an explanation.

## Existing range and link limitations

This presentation update preserves range calculations, endpoint precision and
URL encoding/restoration. In particular:

- Raw timestamp strings do not enable hourly mode. Bounds retain the existing
  date/minute truncation; normalize source types deliberately before authoring.
- Irregular times such as 10:17 or 12:43 have no matching half-hour select
  option. Initial endpoints retain their existing values, but a native change
  reads the current selects. There is no new rounding or normalization rule.
- Hour presets use browser-local `Date.setHours`; elapsed time across daylight
  saving transitions can differ by browser timezone. YTD/hour presets can
  extend before the recorded minimum; numeric day presets clamp to it.
- Runtime validation checks individual dates, not endpoint order or bounds.
  Reversed or out-of-bounds ranges retain their existing behavior.
- Explicit URL restoration requires both nonempty valid endpoints. Open-ended
  URL decoding has separate existing behavior and does not preserve all open
  ranges. Multiple date-range bars also share an existing active-preset lookup
  during URL serialization, which can write another bar's preset into a link.
  Explicit incoming ranges restore independently. This update does not migrate
  links or change the shared URL writer.

Rebuild existing report outputs to receive the new presentation. No Python
constructor, filter config, host API, artifact schema or live parameter changes
are required. Custom CSS targeting the old separate time row should instead
account for `.fw-date-endpoint` wrappers. The `.fw-date-input` and `.fw-date-hour`
query order remains Start then End, with unchanged `data-edge` values.
