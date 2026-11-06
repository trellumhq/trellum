# Report data tables

```python
from trellum.components import DataSource, DataTable, FilterBar

ctx.add_section("Events", [
    DataSource("events", events),
    FilterBar("events", events, filters=[
        {"column": "platform", "type": "toggle"},
    ]),
    DataTable(events, title="Acquisition events", dataset_id="events",
              columns=["event_id", "day", "platform", "revenue"],
              max_rows=10, searchable=True, sortable=True),
])
```

Omit `dataset_id` for a static table. A live query uses the same table API:
declare a `LiveDataSource` and pass its ID as `dataset_id`. The removed `live=`
configuration remains unsupported.

| Argument | Default | Behavior |
| --- | --- | --- |
| `df` | required | Static population, or initial schema for a dataset table. |
| `title` | required | Section/component title. |
| `columns` | all columns | Selected display and CSV columns, in the specified order, for static and dataset tables. |
| `max_rows` | `200` | Limits rendered rows after search and sorting. Does not limit search or CSV. |
| `searchable` | automatic above 20 initial rows | Enables local case-insensitive substring search across displayed column values. Leading/trailing search whitespace is ignored; typing applies after 200ms. |
| `sortable` | `False` | Click a header to sort all matches. A new column starts descending; another click reverses it. Equal values keep population order. |
| `conditional_formats` | `None` | Existing per-column colored-number rules, using `op`, `value` and `color`. |
| `bar_column` | `None` | Existing zero-based displayed column index for background bars. |
| `static` | `False` | Existing explicit opt-out from automatic dataset binding. |

Static tables retain every supplied row. Dataset tables use every row delivered
to the component by the normal filter/live wrapper. Within that population,
local search runs first, stable sorting runs second, and the display limit runs
last. The table shows its supplied, matching and CSV row counts. Generated
workspace definition tables retain their 25-row pagination and anchor links;
CSV spans every matching page.

CSV downloads exactly the current matching rows in the table's selected sort
order, across display limits and pages. Export uses the population from the
last completed draw, without fetching newer live data. If a live result is
retained while a new selection loads or fails, its table and export still
describe that retained result. Dataset filters, local search and sort selections
continue to apply on redraws. Search continues to match display-formatted text.

New static payloads add `numericColumns` and, when numeric columns are present,
`rawRows` alongside the existing formatted `rows`. Raw numeric values determine
sorting and CSV values, preserving precision hidden by table formatting. Static
CSV therefore now contains original numbers rather than rounded/comma-formatted
display strings; missing numeric values export as empty cells. Static
text/date/boolean representations remain unchanged. Dataset CSV already uses
raw values. Column selection, the shared CSV escaping helper and download
filename are unchanged. Older
payloads without these additive fields continue to render, sort and export the
rows/values they contain; a previously truncated artifact must be rebuilt to
include its missing source rows. Existing build payload/resource policies still
apply; `max_rows` is not a payload-size control.

Static integers beyond JavaScript's exact integer range and Python `Decimal`
values travel as numeric text in `rawRows`, so sorting and CSV retain their
precision. Ordinary numbers remain JSON numbers. Dataset tables preserve the
raw values delivered by their wrapper; they cannot recover precision already
lost upstream.

DataTables use compact spacing and subtle alternating backgrounds in each
named theme. Numeric dtype columns align right with tabular figures. Textual
IDs, dates and booleans remain text; formatted strings are not parsed to infer
numeric alignment. No additional appearance options are needed.
