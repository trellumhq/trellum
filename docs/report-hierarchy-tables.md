# Hierarchy tables

`HierarchyTable` is an optional native table, separate from `PivotTable`.
Existing pivot selectors and table defaults are unchanged. Import from
`trellum.components.hierarchy_table` until the public export is integrated.

```python
HierarchyTable(
    facts, "Spend by source and platform",
    rows=["Source", "Platform"], value="Spend", record_id="Fact",
    column="Month", value_format="currency", precision=2,
    known_subtotals=False, expanded_paths=[["Paid search"]],
    dataset_id="spend", static=False,
)
```

Only `df` and `title=""` are positional. All other arguments are keyword-only.
`rows` is a list of one through eight distinct dimension column names. `value`
names an additive numeric metric. `record_id` names a unique, nonnull, nonempty
string identity per fact. `column=None` optionally names a categorical pivot
bucket. Dimension, bucket, metric and identity columns must be distinct.

Every supplied row must be a leaf fact. This declaration is the author's
responsibility: exclude parent summary rows and repeated parent budgets. Do
not sum rates, ratios or distinct counts. Only `number`, `currency` (USD `$`)
and `plain` formats are supported; precision is an integer 0–6. Additivity
cannot be inferred from a column's numeric dtype or name. Negative finite
facts and measured zero are valid. Numeric-looking strings and booleans are
unavailable metrics, with no numeric coercion.

Parents sum all matching descendant facts once, including collapsed branches.
The grand total sums the matching facts directly and never adds displayed
parent and child rows. Labels wrap and numbers align within a local horizontal
scroll region. Native buttons expose `aria-expanded`; standard Tab/Enter/Space
interaction applies. There is no treegrid keyboard pattern, row selection,
sorting, local search or drill-down action.

Default expansion is collapsed. `expanded_paths` accepts raw typed dimension
prefix **lists**, such as `[["Paid search"], ["Paid search", "iOS"]]`, validated
against the complete source. Paths retain expansion through filters that hide
them, including an empty population. Expansion does not write filter state.
Sibling and bucket order follow first appearance in the full source, including
when filtering removes the first fact of a surviving branch.

Identity uses structural typed paths, distinguishing null, empty strings,
booleans, strings and safe numbers. Null displays as **Missing**; empty strings
as **Empty**. Numeric and boolean labels carry type suffixes. Row/header details
(the native title attribute) and CSV preserve the full typed identity, including
literal strings named Missing or Empty. Delimiters and `__proto__` in labels
are ordinary text. Arrays, mappings, date objects, infinite numbers and unsafe
numeric dimension identities are rejected. Convert dates explicitly to strings
at the intended grain. Integer and floating values of the same safe number
share numeric identity. Text is limited to 1,024 characters per dimension/ID,
column names to 128 characters and titles to 512.

By default an unavailable metric withholds every cell/row/ancestor/grand total
that includes it. Independent valid cells and zero remain visible. With
`known_subtotals=True`, finite known inputs may be summed, every partial result
gets `*`, and one shared explanation gives the unavailable input count. A group
with only unavailable inputs stays a dash. A missing path/bucket intersection
has **No observations**, distinct from unavailable input and measured zero.
Cell details give the reason and fact count. No matches shows an empty message
and no invented zero grand total.

Static input uses the shared typed numeric encoder and exact bounded decimal
aggregation, preserving Decimal and large integer values before final display
rounding. Snapshot-bound values use the delivered JavaScript numeric data;
unsafe delivered integers are unavailable with an explicit precision reason.
Numeric range limits from the shared aggregator remain visible as dashes with
reasons; values are never rounded before addition. Coefficients/exponents are
bounded by that helper (1,000 input coefficient digits and exponent magnitude,
2,048 arithmetic digits). This is not arbitrary-precision unbounded arithmetic.
Raw metric export text exceeding 2,048 characters is rejected. The existing
DataSource serializer cannot deliver Decimal objects or integers outside its
64-bit JSON integer range; the hierarchy validator rejects those bound sources
with guidance to use static typed facts. Static Decimal/large integers do not
pass through that source serializer.

Ordinary `DataSource` and `ScopedDataSource` snapshots use native filters.
The complete effective source is validated, even when filtered output is small.
Declare sources before consumers. A prefiltered generator frame explicitly
supplies the population; this table makes no claim about hidden database rows.
`LiveDataSource` ancestry, live-connected propagation and chunked populations
are rejected: a client slice cannot certify complete parent totals. Unready,
missing or malformed runtime sources display a reason and cannot export stale
facts. The new validation hook must run before rendering (integration below).

Source limits apply before building/expanding the tree: 50,000 leaf facts,
2,000 distinct prefix nodes, 32 bucket categories, 64,000 numeric cells at full
expansion including the grand row, and 8 MiB of relevant typed serialized facts.
No limit silently truncates data or exports. Limits apply to the full source,
not just the currently selected slice. Work is bounded by facts × depth rather
than rescanning the full dataset for every row.

CSV is explicitly **leaf facts**, with each matching `record_id` exactly once
regardless of expansion. It includes `row_type=leaf_fact`, full typed path,
each individual typed dimension, optional typed bucket, raw value, value column,
format, availability, completeness and reason. No parent summaries are mixed
with leaves. Raw numeric precision is retained; precision lost upstream in a
bound data source cannot be recovered. CSV completeness describes each fact,
not a rendered group. Image, section capture and PDF use the current expanded
layout and its completeness explanation; they do not expand hidden branches.

Each instance owns its subscription, delegated click listener, theme callback,
pagehide cleanup and removal observer. Re-entry disposes previous handlers;
replacement/removal prevents stale callbacks. Focus stays on the same toggle,
its closest visible ancestor, or a stable local control after filtering.

## Integration

The component self-registers `hierarchy_table` JS and loads its scoped CSS
and the existing `data_table_totals.js` dependency through the component asset
collector. No global runtime renderer dispatcher changes are required.

The lead should:

1. Import `HierarchyTable` from `trellum.components.hierarchy_table` in
   `trellum/components/__init__.py` and add `"HierarchyTable"` to its `__all__`,
   following the other table exports. The root `trellum` module currently
   exports no table classes and needs no new eager component imports.
2. In `trellum/validation/checks/datasource.py`, after collecting `ds_map`, call
   `check_hierarchy_tables(comps, ds_map, result)` imported from
   `trellum.validation.checks.hierarchy_table`. The check records its completed
   group and reports `hierarchy-table-invalid`. Run it before render admission.
3. Include `examples_hierarchies.add_sections(ctx)` in the native component
   showcase generator. Each of its four named sections owns an independent
   source and native platform filter. The examples total 17,850 complete,
   10,250 for iOS, and 16,850 known spend with one unavailable Android/Oct fact.
   A Web filter produces the empty state. The flat PivotTable comparison is
   explicitly optional.
4. Add the API/limits to the shared compatibility and migration guidance and
   link this page in the documentation index. Suggested changelog entry:
   “Add optional HierarchyTable with expandable additive leaf-fact totals,
   explicit completeness states and expansion-independent leaf CSV.”

The dedicated test suite invokes the new check directly alongside ordinary
native `compute_details(ctx)` and `validate_report`. Until step 2 lands, the
central validator does not automatically run this additional check.
