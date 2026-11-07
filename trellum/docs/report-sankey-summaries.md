# Sankey header summaries

`SankeyChart(..., header_summary=None)` adds an optional author-declared header
beside the existing title. The new argument is keyword-only and defaults off.
All existing constructor arguments, default markup/config, graph geometry,
colors, stage labels, plot heights, library, population explanations, details
and CSV schema remain unchanged. Historical objects without `header_summary`
also default off. No new import or public export is needed.

```python
SankeyChart(
    paths, "Supplied paths", mode="paths", stages=stages,
    weight="players", unit="players", dataset_id="paths",
    header_summary={"stat": "sum", "scope": "complete_paths"},
)

SankeyChart(
    edges, "Supplied transitions", mode="edges", stages=stages,
    source="from_category", target="to_category",
    source_stage="from_week", target_stage="to_week",
    weight="players", unit="players", dataset_id="edges",
    header_summary={"stat": "max", "scope": "adjacent_boundary",
                    "from_stage": "w2", "to_stage": "w3"},
)
```

Each declaration requires `stat` and `scope`. Optional fields are `label` and
`format`. Edge declarations additionally require `from_stage` and `to_stage`;
path declarations forbid both fields, including fields whose value is `None`.
No other declaration fields are accepted.

| Mode | Required scope | Population | `sum` | `max` | `count` |
| --- | --- | --- | --- | --- | --- |
| `paths` | `complete_paths` | Current admitted grouped complete paths | Graph's single complete-path total | Largest grouped path weight | Number of grouped paths |
| `edges` | `adjacent_boundary` | Current admitted grouped edges at exactly the declared adjacent boundary | Graph's total at that boundary | Largest grouped edge weight at that boundary | Number of grouped edges at that boundary |

Boundary identifiers are declared stage **keys**, with source immediately
preceding target in declared order. Stage labels may repeat; labels do not
identify a boundary. Missing, unknown, reversed, skipped or ambiguous keys fail
validation. A summary scope never selects a category or filters source facts.

Default labels are `Path total`, `Largest path`, `Path entries`, or `Boundary
total`, `Largest edge`, `Edge entries`. A custom label must be nonblank text of
at most 60 characters. It is rendered as text, including quotes, markup and
newlines, with no HTML interpretation. Visible scope gives the declared weight
unit for numeric reducers and `paths` or `edges` for counts. A boundary scope
also gives both exact stage labels and keys.

Formats are `number`, `currency`, `chips`, `plain`; default `number` is explicit
and independent of the weight unit. Numeric reducers use the existing shared
formatters, including their compact display and rounding. `currency` uses the
existing dollar formatter; declare the intended unit (for example `USD`)
explicitly. No currency or unit inference is performed. Counts allow only
`number` and `plain` and always display an integer entry count. Exact details
and CSV continue to retain the raw weights and units; compact headers do not
change their precision.

Counts include grouped supplied zero-weight paths/edges and tiny positive
entries even when no ribbon can be painted. Duplicate complete-path facts
merge before counting. Counts are neither source-row counts, node counts,
painted ribbon counts nor unique-person totals. Null classification remains an
explicit unavailable observation with its valid weight included. A literal
`Unavailable` category remains distinct from null.

For an empty selected population or boundary, count is `0`, while `sum` and
`max` show `—` and `No measured observations.` A supplied zero-only population
has numeric zero for `sum`/`max` and its actual grouped-entry count. A chosen
boundary may be empty while other boundaries have observations.

A path's weight is counted once regardless of the number of boundaries. Edge
mode never combines boundary totals, invents journeys, balances flows or
asserts a single cohort total. Summaries do not derive rates, ratios,
quantiles, unique-person counts, or weights from pixel geometry. They cannot
recover upstream precision or certify source-person uniqueness.

The full effective snapshot and selected graph must pass existing finite,
nonnegative, count/quantity, domain, source-completeness and resource guards
before any summary, including a count, appears. Invalid definitions fail at
construction, `config()`, render, and the existing `sankey-invalid` shared
validation dispatch. Runtime-injected malformed configuration is rejected by
the existing `Sankey unavailable` state. Invalid or unready data removes old
summaries and keeps native capture blocked. Limits remain 10,000 source rows,
200 nodes, 1,000 edges/paths, 4 MiB relevant payload, safe-integer count totals,
and quantity totals at most 1e15. Live and chunked sources remain unsupported.

Native filters update graph and summary together. Disclosures, hover, theme,
scroll, resize and image capture do not redefine the population. Header scope
participates in the canvas description; disposal removes only the summary's
owned `aria-describedby` token. Header wrapping on phones does not change plot
height or the existing horizontal stage-scroll region.

Native section and report PNG/PDF capture include the DOM header, preserving
existing disclosure and scroll restoration. Ready, zero and empty results are
completed states; invalid results remain blocked. CSV contains the same
ordered grouped records and exact raw unit values as before. Summaries never
insert, remove or reorder exported records.

## Deterministic examples and validation

`demo/reports/component-showcase/examples_sankey_summaries.py` provides
`add_sections(ctx)` with four independently named sections: static off/on,
filtered complete paths, independent adjacent boundaries, and zero/empty/null
quantities. It uses shared `SankeyChart`, native filters, panels, grids and tabs;
RawHTML supplies explanation only. Each filtered population has its own dataset
ID. Existing showcase examples need no changes.

The shared showcase includes these four sections through its Sankey summaries
link, beside the existing Sankey examples. The native `check_sankey_charts`
validator calls `snapshot` → `config`, including summary validation.
Import the existing `SankeyChart` from `trellum.components`.
