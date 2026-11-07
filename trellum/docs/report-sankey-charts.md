# Supplied paths and adjacent transitions

`SankeyChart` is an optional, bounded chart of data supplied by a report author.
It uses the bundled `chartjs-chart-sankey` 0.12.1 plugin and existing Chart.js
defaults. It does not replace an existing chart or change another chart's size,
legend, tooltip or filtering behavior.

```python
from trellum.components import SankeyChart

stages = [
    {"key": "week_1", "label": "5 October"},
    {"key": "week_2", "label": "12 October"},
    {"key": "week_3", "label": "19 October"},
    {"key": "week_4", "label": "26 October"},
]
chart = SankeyChart(
    df, "Weekly player classifications",
    mode="paths", stages=stages, weight="players", unit="players",
    dataset_id="weekly_paths",
    header_context="One supplied classification per week for the fixed cohort.",
)
```

Declare the ordinary `DataSource` and its native `FilterBar` before its chart.
A `ScopedDataSource` may supply section-local filtering on that same snapshot.
Place the source and filter first in their named section. For fixed data that
does not participate in report filters, omit `dataset_id` and pass `static=True`.
The dataset's effective frame is validated independently of `chart.df`; a valid
placeholder frame cannot conceal invalid bound data.

## Constructor

```python
SankeyChart(
    df, title="", *, mode, stages, weight, unit, weight_kind="count",
    source=None, target=None, source_stage=None, target_stage=None,
    height=320, min_width=640, header_context=None,
    dataset_id=None, static=False,
)
```

Only `df` and `title` are positional. All other arguments are keyword-only.
`mode` must be `"paths"` or `"edges"`. `stages` is an ordered list of 2–12
dictionaries containing exactly `key` and `label`. Keys must be unique nonblank
strings (up to 128 characters); labels must be nonblank strings (up to 256).
Repeated labels are allowed: exact details show the stage key as well.

`weight` names a supplied numeric column, and `unit` is explicit nonblank text,
up to 128 characters. Units are never inferred from numeric values. `weight_kind`
is `"count"` for nonnegative safe integers, or `"quantity"` for finite
nonnegative numeric quantities. Integer-valued numeric floats are accepted as
counts. Booleans, numeric strings, null weights, NaN, infinity, negatives,
fractional counts and unsafe integers are rejected. Decimal/arbitrary precision
objects are not coerced. Quantity arithmetic follows ordinary binary floating
point; the chart makes no arbitrary precision claim.

Categories are nonblank strings up to 512 characters or missing observations
(`None`, pandas missing scalars/NaN). Missing classifications are displayed as
**Unavailable observation**, with a separate typed identity. A supplied category
called **Unavailable** remains an observed category. Neither missing observations
nor an explicit **No purchase** category implies an exit or measured zero.
HTML-like category text is displayed as text, never interpreted as markup.

Node identity is the pair of stage index and typed category identity; repeated
labels across stages and strings containing delimiters or prototype property
names do not merge nodes. Stage and weight columns must have distinct roles.
Edges mode's four column roles must also be distinct from each other.

`height` must be a positive Python integer in 160–1200, and `min_width` an integer
in 320–2400; booleans are rejected. These defaults apply only to Sankey.
The entire drawing region, including stage headings, is 320px by default.
Narrow panels retain a 640px diagram inside a local horizontal scroll region.
Tab to this region and use the arrow keys, or scroll/swipe it. Details have their
own local overflow containment. Neither region requires document-wide overflow.

`title` is text up to 512 characters; `header_context` is optional text up to
4096 characters, rendered with the shared chart context helper. The title,
context, diagram, population explanation, exact disclosure and CSV button form
one component and remain together inside `Panel`, `Grid` and section captures.

## Complete paths

For `mode="paths"`, each stage key names a dataframe classification column.
Every input row is an author-declared mutually exclusive weighted complete path
through **all** ordered stages. Omit all four edge column options.

Only identical complete path tuples are aggregated, summing their supplied
weights. Ribbons aggregate identical adjacent stage/category pairs. The selected
record set has one population total and equal stage/boundary totals; summing
all boundaries would count the same population repeatedly. A four-stage cohort
of 1,000 players has three boundaries of 1,000, not 3,000 unique players.

Merging ribbons loses path associations. Two populations can have the same
adjacent links with different origin/final associations. The native **Exact
complete paths** disclosure therefore retains every grouped complete path,
including tiny paths and supplied zero-weight paths, with exact weights/units.
It is available regardless of canvas label fit or pointer tooltips.

## Adjacent edges

```python
chart = SankeyChart(
    transitions, "Supplied adjacent flows",
    mode="edges", stages=stages, weight="players", unit="players",
    source="from_category", target="to_category",
    source_stage="from_week", target_stage="to_week",
    dataset_id="adjacent_transitions",
)
```

Each edge's stage column values must be declared **keys**, connecting stage
`i` to `i + 1`. Unknown, backward, cyclic and skipped-stage edges are rejected.
Only identical adjacent stage/category pairs are aggregated. Complete paths
are never reconstructed, forecast or invented from edges.

Inflow and outflow may be unbalanced because boundaries can describe independent
populations. The chart identifies this population model, shows each boundary's
total, and provides **Exact adjacent edges**. It assigns no single unique-person
total across all boundaries and manufactures no entry/exit or balancing links.

## Bounds and state handling

The complete relevant source snapshot must satisfy all bounds:

| Resource | Maximum |
| --- | ---: |
| Source rows | 10,000 |
| Distinct stage/category nodes, including zero records | 200 |
| Distinct adjacent edges | 1,000 |
| Distinct complete path tuples | 1,000 |
| Relevant source and metadata, UTF-8 JSON | 4 MiB |
| Count input and each grouped total | 9,007,199,254,740,991 |
| Quantity input and each grouped total | 1,000,000,000,000,000 |

Bounds are checked before chart construction and again for filtered/injected
runtime data. Invalid data fails clearly; there is no top-N truncation, Other
bucket or denominator adjustment. Duplicate rows are aggregated with bounded
maps, and edges are never expanded into paths. Positive flows use the plugin's
proportional geometry with no minimum visible thickness. Zero-area links are
not painted but remain in details and CSV. Labels are omitted if they cannot
fit at the normal report label size; pointer tooltips retain exact adjacent
values and stage identities. Colors use a stable category domain from the full
declared snapshot, so filtering does not recolor surviving categories. Missing
observations use a neutral theme tone.

Empty selected rows show **No matching paths/transitions**. Supplied rows whose
weights are all zero show a separate **Zero flow** state with full detail.
Missing weight invalidates the result. Unready, invalid or out-of-domain injected
payloads clear old geometry and disable CSV with a local explanation.

`LiveDataSource`, live ancestry and filter propagation connected to live data
are unsupported: loaded-slice coverage is undeclared. Chunked sources are also
rejected; use a bounded, complete, unchunked snapshot. This restriction is local
to Sankey and does not alter existing live or chunked reports.

The renderer owns its Chart.js instance, returned filter unsubscribe function,
theme listener, resize/reveal handling, pending frame and DOM removal observer.
Repeated filtering, theme changes and `renderAll` do not add subscriptions.
Open disclosure and summary focus survive updates while their DOM survives.
Hidden/revealed and removed/replaced instances remain independent. The component
does not patch `renderAll` and starts no polling loop or server.

## CSV and image scope

The native download helper is used even in a report containing only Sankey.
The component button does not invoke generic Chart.js CSV delegation.

Paths CSV has fixed headers `mode`, `record_type`, `weight`, `unit`,
`weight_kind`, followed by `stage_N_key`, `stage_N_label`, `stage_N_category`,
`stage_N_availability` for each ordered stage. `record_type` is `complete_path`.
Edge CSV instead has fixed source/target stage key, label, category and
availability columns, with `record_type=adjacent_edge`. Unavailable categories
have blank category cells plus `availability=unavailable`; literal strings have
`availability=observed`. Zero records are retained. Neither export multiplies
a cohort total by its number of boundaries.

PNG/PDF retain the currently drawn chart and visible context/details. A collapsed
disclosure stays collapsed, and local scroll regions capture their current
visible area; images do not promise every offscreen label or detail row. Open
the disclosure or adjust local scroll before capturing. CSV retains all selected
exact records regardless of scroll or disclosure state.

The shared CSV writer quotes commas, quotes, line feeds and carriage returns in
headers and cells, preserving authored category text.

## Native integration and examples

Import `SankeyChart` from `trellum.components` or `trellum.components.charts`.
The shared validator checks effective snapshot scope and graph bounds in its
Sankey-charts group, with `sankey-valid` / `sankey-invalid` diagnostics. The check
catalogue discovers both IDs automatically. Filter diagnostics introspect the
supplied weight column.

Native analysis captures treat completed empty and zero-flow diagrams as ready;
invalid input blocks capture with the component's explanation. Positive-flow
results require a ready Chart.js instance and canvas. The outer component keeps
titles, context and details together in section or chart-target captures.

The shared component showcase's **Sankey paths** link opens four native sections
from `examples_sankeys.add_sections(ctx)`. Their independent datasets and filters
show eight complete paths totaling 1,000 players (iOS: 500), separate unavailable,
No purchase, tiny and zero cases, and unbalanced adjacent transitions. These are
synthetic supplied populations, not inferred journeys.
