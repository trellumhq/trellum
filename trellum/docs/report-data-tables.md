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
| `pin_header` | `False` | Keyword-only opt-in to pin the current table header inside its bounded scroller. |
| `pin_footer` | `False` | Keyword-only opt-in to pin the existing semantic totals footer; requires nonempty `totals`. |
| `drill_down` | `None` | Keyword-only row action targeting an existing ordinary dropdown, with selection context and explained empty feedback. |
| `pin_height` | `320` | Positive integer maximum scroller height in CSS pixels when either pin option is enabled. |

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


## Optional totals over matching delivered rows

`DataTable.totals=None` is keyword-only. Omitted, `None` and `{}` add no
configuration, footer or space. Declarations are copied and checked at
construction and rendering. Each key must name a supplied, displayed metric.
The first displayed column is reserved for the **Total** label and cannot have
a total declaration. All-numeric tables that need their first metric summed
must supply an explicit label column first. `columns=None` and `columns=[]`
retain their existing all-columns fallback.

```python
DataTable(df, "Campaign contributions", static=True,
          columns=["Campaign", "Spend", "Revenue", "ROAS"],
          totals={
              "Spend": {"agg": "sum", "format": "currency"},
              "Revenue": {"agg": "sum", "format": "currency"},
              "ROAS": {"agg": "ratio", "numerator": "Revenue",
                       "denominator": "Spend", "format": "ratio", "precision": 2},
          })
```

A sum declares a genuinely additive metric: do not sum stocks, cumulative
counts, distinct users or rates. It reads its own column. A ratio sums the
explicit numerator and denominator over the **same matching records**, then
divides; it never averages row rates or parses formatted values. Ratio input
columns must be present in the supplied dataframe but may be hidden from the
display. Hidden inputs do not become searchable or appear in CSV.

Only `agg` is required for sums; ratios also require nonempty string
`numerator` and `denominator` names. `format` defaults to `number` and
`precision` to `2`. Sums accept `number`, `currency`, `plain`; ratios also
accept `percent` (ratio × 100) and `ratio` (an `x` suffix). Precision must be
an integer from 0 to 6, excluding booleans. Unknown options, columns, enums,
floating precision, callbacks and malformed declarations raise `ValueError`.
Report validation also checks hidden ratio inputs against the actual bound
DataSource (or scoped ancestor), and reports mutated invalid declarations.

Totals cover the last completed draw's matching delivered population **before**
`row_limit` or `max_rows`. Report filters narrow that population; table search
narrows it locally. Sorting changes order only. The Rows selector changes no
total, report filter, KPI or chart. No matches means no total row or scope note.
The semantic footer and adjacent scope note identify matching row counts and
delivered table scope, without claiming warehouse completeness.

Every total independently requires finite genuinely numeric inputs for every
matching record. Null/NA/NaN, infinity, booleans, authored numeric-looking
strings and unsupported types withhold that metric as a dash and a named
reason; valid siblings and measured zero remain. Signed additive values are
supported. A ratio requires complete components and a **positive summed
denominator**; zero or negative denominators withhold it.

Static `int`, `float` and `Decimal` inputs use a separate typed numeric text
payload indexed by original row position. It includes only columns required
by declarations and preserves numeric provenance and Decimal/large-int
precision without changing display, raw rows, search or CSV. Ordinary
DataSource/ScopedDataSource deliveries require finite JavaScript numbers;
unsafe integer Numbers and strings cannot establish precise inputs and
withhold the affected metric. Precision lost upstream cannot be recovered.
Live-query sources and their scoped descendants withhold totals with
“Live-query total scope is not declared.” Ordinary unready sources cannot
establish total scope until ready. These states leave ordinary cells and
loading/failure handling intact.

New totals use bounded decimal coefficient arithmetic with BigInt, exact sums,
and half-away-from-zero rounding only at the requested output precision.
Numeric declarations allow at most 1,000 coefficient digits and absolute
base-ten exponent 1,000; intermediates and results are bounded to 2,048 digits.
Values outside these limits display “Numeric range is unsupported.” rather
than expanding extreme exponents. Browsers without BigInt explicitly withhold
totals. Number/currency/percent/ratio use en-US grouping and fixed precision;
plain omits grouping. Currency uses `$` (`-$` for negatives), and rounded
negative zero is normalized. No compact notation hides authored precision.

CSV remains **only matching source rows in current stable sort order**, with
no total/footer or unit changes. Native report/section PNG and PDF capture
the rendered footer and scope note in normal flow, including multi-row stripes
and threshold fills. The capture runtime converts modern computed CSS colors
to equivalent 8-bit sRGB with alpha in its temporary clone; it leaves on-screen
styles intact. Rebuild HTML/runtime assets to pick up this compatibility fix.
Other CSS features remain subject to the bundled html2canvas renderer's support.
Captures retain the current bounded table display and horizontal scroll viewport; they do not expand
hidden rows/columns or add an export-only total. This option adds no pinning,
hierarchy, row drill-down or Pivot changes. ComparisonTable exposes no totals
argument, and workspace definition catalogs ignore injected totals settings.
Rebuild artifacts with matching Python and browser assets to enable totals.

`examples_table_totals.add_sections(ctx)` supplies native off/on static,
filtered 36-contributor and incomplete-input examples.

## Optional DataTable edge pinning

`pin_header=False` and `pin_footer=False` are independent keyword-only
DataTable options. When enabled, the current header and/or the existing
`totals` footer stay at the edge of the same bounded vertical and horizontal
scroller. `pin_footer=True` requires a nonempty validated `totals` declaration;
it never creates a total. If there are no matching rows, the normal total
footer remains absent. Header pinning does not require totals.

`pin_height=320` is a positive integer maximum height in CSS pixels. The
scroller shrinks to its content when the table is short or empty. Columns,
including the first column, scroll horizontally together. Search, CSV and Rows
controls and the totals scope note stay outside the scroller. The focusable
scroll region uses the table title as its accessible name, or “Data table” when
the title is empty. Native keyboard scrolling remains available.

With both flags false, pinning adds no payload, markup, classes, attributes or
reserved space. Pinning is not available on `ComparisonTable`, does not affect
Pivot tables or workspace catalogs, and does not change filtering, sorting,
display limits, CSV rows or total scope. Native captures preserve the visible
bounded viewport, sticky edges and ordinary totals scope note; they do not
expand hidden rows or columns. Rebuild existing reports to enable pinning.

`examples_table_pinning.add_sections(ctx)` shows the static default and the
three independent edge combinations, followed by bound, searchable, filterable
observations with an additive footer and full-scope KPI.


## Optional row drill-down

`DataTable.drill_down=None` leaves payloads, markup and listeners unchanged.
A nonempty mapping opts into native label-cell buttons; it does not make rows
clickable or create another filter state:

```python
DataTable(facts, "Source observations", dataset_id="facts",
          columns=["Source", "Day", "Installs"], row_limit=10,
          drill_down={
              "dataset_id": "detail",  # existing FilterBar owner
              "column": "source_key",  # owner's dropdown column
              "value_column": "source_key",  # raw row identity, may be hidden
              "label_column": "Source",  # displayed action label
              "context": [
                  {"column": "source_key", "label": "Source"},
                  {"column": "campaign_key", "label": "Campaign"},
              ],
          })
```

The table's `dataset_id` chooses its population; the action's `dataset_id`
chooses the existing dropdown owner. Explicit table binding keeps precedence
over `static`. Construction and rendering validate and copy the declaration.
Only the four required keys and optional `context` are accepted; names must be
nonempty strings. `label_column` must be displayed, while `value_column` can
remain hidden. Context defaults to one item naming the target column as both
column and label. Each item has exactly nonempty `column` and `label` strings,
with no repeated columns. Report validation checks the effective datasource,
target/context columns and exactly one compatible dropdown owner per column.
Static tables and ordinary DataSource/ScopedDataSource populations and owners
are supported. Live targets, live-backed populations/scopes and any connected
live propagation are rejected, rather than acting on a retained snapshot.
ComparisonTable and generated workspace tables do not acquire this option.

Identities are declared raw nonempty strings, never formatted cell labels or
row positions. Null, empty and non-string keys have disabled buttons with an
accessible reason. Duplicate labels can have distinct keys; repeated
observations of one key select the same entity. Unavailable options or invalid
legacy owner references disable actions with a reason. Raw action metadata is
separate from display/search/CSV; hidden keys never enter the selected export
columns. Existing raw numeric and Decimal CSV precision is preserved.

A row action replaces the dropdown selection with its key. Repeating the sole
selected key clears it; native dropdown multiselect remains available. Buttons
use native Enter/Space and expose actual selected keys with `aria-pressed`.
Wrapping context outside the scroller shows actual dropdown selections,
including multiple keys, verbatim. Named clear buttons use that same owner
path. Context survives local search, display limits and incompatible report
filters. Focus follows a surviving key, its clear action or the named table
context when redraw removes the focused row.

For parent/child selection, opt the child into
`{"column": "campaign_key", "depends_on": "source_key", "clear_on_parent_change": True}`.
A real parent-selection change clears that child even if its key remains
available. Initial valid URL restoration, unchanged parents and reordered
equivalent selection sets preserve the child. Omission/false preserves the
existing option-narrowing and available-child behavior. The option requires a
strict boolean on a dropdown with nonempty `depends_on`. Native dropdown
changes, existing Clear actions, row buttons and context clear actions all
use the same cascade, propagation and URL path.

Empty opted-in tables retain their headers and dimensions without a tall empty
body or invented totals. Local search explains its own empty result and offers
Clear search, which changes only that table. Empty supplied or report-filtered
populations have brief explanations beside retained selection context; clearing
a local selection never widens date/platform automatically.

`examples_table_drill_down.add_sections(ctx)` supplies 56 synthetic daily
campaign/platform observations. Source actions read the independent base;
campaign actions read a sibling scope receiving only source propagation;
detail KPI/trend read a scope receiving both selections. The base date/platform
FilterBar precedes these dependents. The campaign table explicitly accepts its
inactive campaign filter in `validation.accept_inactive_filters`. Totals divide
underlying sums for ratios; these are observations, not aggregate source ranks.

URL state remains the existing FilterBar serialization. Its comma-delimited
multiselect format cannot round-trip keys containing commas, and its writer
serializes registered framework controls rather than retaining arbitrary query
parameters. Multiple bars use dataset-qualified keys. Drill-down adds no
separate URL state or URL-writer changes; rebuild matching report artifacts.
