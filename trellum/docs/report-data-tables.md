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
| `row_limit` | `None` | Keyword-only opt-in to a Rows selector (10, 20, 50, 100 or `"all"`), initially selected at construction. |
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

`ComparisonTable` accepts the same keyword-only `row_limit` option and uses the
same display and export rules.

With `row_limit` enabled, the author-selected choice controls display after
local search and sorting and supersedes `max_rows` for display only. It never
changes supplied data, filters, or CSV. All displays every matching row up to
and including 1,000 matches. Above 1,000 matches, All is disabled; an active
or authored All falls back to 100 and remains at 100 until the reader chooses
another option. The adjacent note states that All is available for up to 1,000
matches and CSV still includes every match. This bound is exclusive to the
opt-in selector; legacy `max_rows` behavior is unchanged.

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

## Optional threshold cell shading

Keyword-only `cell_shading=None` is off by default. `None`, an omitted option
and `{}` add no payload, key or reserved space. Authors declare each target and
whether higher or lower is favorable; metric names and signs imply nothing:

```python
from decimal import Decimal

DataTable(df, "Cohort targets", cell_shading={
    "cpi": {"target": Decimal("1.80"), "direction": "lower"},
    "recovery_d30": {"target": 100, "direction": "higher"},
})
```

Each entry has exactly `target` and `direction`. Columns must be string names
present in both the supplied schema and displayed projection. Targets accept
finite `int`, `float` or `Decimal` values, excluding booleans. Strings,
NaN/infinity, other types, unknown keys and directions other than `higher` or
`lower` raise `ValueError` at render time, including after option mutation.
Existing positional and subclass parameters keep their positions.

Targets use **original raw column units**. `lower` means raw value ≤ target;
`higher` means raw value ≥ target. Equality meets the target, with no tolerance
or rounded boundary. Fractional retention `.25` needs target `.25`; a source
explicitly on a 0–100 scale needs target `25`. No conversion is inferred.
Static integers and Decimals retain exact text for comparison, including large
exponents; ordinary floats retain only the precision already supplied.

Only genuine finite numeric cells are shaded. Nulls, nonfinite values,
booleans, dates and text (including numeric-looking IDs and formatted numbers)
remain unshaded. Static `rawRows` decimal/integer text is trusted only with its
original `numericColumns` type declaration. Mixed static columns carry a small
additional typed-numeric-only payload for shading, preserving their established
CSV/display representation. Ordinary dataset deliveries require actual finite
JavaScript numbers: numeric-looking strings are never promoted by stale snapshot
dtype metadata. Precision lost by an upstream dataset serializer cannot be
recovered; use a static numeric snapshot when exact Decimal/integer boundaries
must survive transport.

One wrapping key outside the horizontal scroller explains each column's target,
direction and two outcomes. Classified cells expose their outcome and exact
target in a native accessible title. Source/display/export strings, compact cell
height, stripes, hover, bars, filters, search and stable sort remain intact.
Favorable/unfavorable fills use existing theme tokens. Existing
`conditional_formats` still owns foreground colors; custom authored colors
remain the author's responsibility for readability against these subtle fills.
There is no runtime shading toggle. Rebuild with matching assets to enable it.

`examples_table_shading.add_sections(ctx)` in the component showcase provides
default/opt-in snapshots, filterable cohort metrics with explicit text rules,
lossless boundaries and a homogeneous retention comparison.
