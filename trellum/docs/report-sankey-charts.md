# Supplied paths and adjacent transitions

`SankeyChart` is an optional, bounded chart of data supplied by a report author.
It uses the bundled `chartjs-chart-sankey` 0.12.1 plugin and existing Chart.js
defaults. It does not replace an existing chart or change another chart's size,
legend, tooltip or filtering behavior.

```python
from trellum.components.charts.sankey import SankeyChart

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

The shared CSV helper currently quotes commas, quotes and line feeds, but not a
bare carriage return. Its integration fix must also quote `\r` so an authored
category containing that character round-trips exactly. Sankey does not replace
or patch the shared download helper.

## Integration hooks

During integration, export `SankeyChart` from `trellum.components.charts` and
`trellum.components` and add it to their `__all__` lists. Until then, examples
and tests import the implementation module directly.

Import `check_sankey_charts` from `trellum.validation.checks.sankey` in the shared
validator. After collecting `ds_map`, call
`_run("sankey charts", check_sankey_charts, comps, ds_map, result)` before
deduplication/suppressions. This supplies `sankey-valid` / `sankey-invalid`
diagnostics; add both IDs to the shared check catalogue. Include `weight` in
the diagnostic value-column introspector for `SankeyChart`, so filter health
can describe its measure. Its existing `_supports_dataset_id=True` metadata
already participates in generic binding and coverage discovery.

In `static/js/runtime/csv_download.js`, extend the existing cell-quoting
predicate to include `s.indexOf('\r') >= 0`. Keep its public signature and
download behavior; this completes the bare-carriage-return text case above.

In `static/js/runtime/export.js`, add `sankey: true` to `_analysisReady`'s
`chartTypes`. For `sankey` only, handle local `data-sankey-state` explicitly:
`empty` and `zero` are complete render states without a Chart.js instance;
`invalid` must fail capture with the local explanation. A ready positive-flow
Sankey still requires its instance/canvas. The outer `.fw-chart-container`
groups context and details for native chart-target capture. The component does
not bypass the shared readiness guard. Without this common hook, native section
capture fails its existing unknown-content guard; browser tests record that
pending hook instead of presenting a screenshot as native capture success.

Wire `examples_sankeys.add_sections(ctx)` into the common showcase generator and
add a visible navigation entry for the four Sankey sections. The examples use
independent `showcase_sankey_paths`, `showcase_sankey_observations` and
`showcase_sankey_edges` datasets/filters. They supply eight mutually exclusive
paths totaling 1,000, an iOS selection totaling 500, separate unavailable,
No purchase, tiny and zero cases, and honestly unbalanced adjacent edges.
The different-paths/same-links ambiguity has a focused Python test.

Proposed shared changelog entry: “Add optional Sankey charts for supplied complete
paths or adjacent transitions, with exact details, bounded snapshot validation,
native filters and CSV export.”
