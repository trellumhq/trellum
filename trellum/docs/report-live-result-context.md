# Live result context for KPI rows and ordinary tables

`KpiRow(..., live_context=True)` and `DataTable(..., live_context=True)` add a
shared context note when their dataset is a registered `LiveDataSource`, or a
`ScopedDataSource` descended from one. The option is a strict boolean and is
keyword-only; all previous positional constructors remain valid. The default is
`True`. Use `live_context=False` on each consumer to retain its previous context-free
presentation. Older payloads without the marker keep their previous presentation.
Static values, ordinary compiled datasets and their scoped children, metrics
workspaces, ComparisonTable and other table wrappers do not acquire this note.

```python
KpiRow([
    {"label": "Returned value (USD)", "agg": "sum", "column": "value",
     "format": "currency"},
    {"label": "Observations", "agg": "count"},
], dataset_id="observations")
DataTable(frame, "Returned observations", dataset_id="observations",
          sortable=True, live_context=True)
```

The native `live-result-examples` report uses a configured `demo_db` SQLite
source and a bounded literal synthetic SELECT/UNION ALL declaration. It has
Current, Alternative and Empty selections, a line chart and a local platform
subset. It needs no source tables or schema changes. A standalone or shared
artifact shows the fixed Current snapshot and disabled live controls. A supported
serving host executes the registered query. Loading, failed and limited responses
are shown only when the actual binder reports them; the report has no simulated
production fetch override or state menu.

## Reading the note

The first line identifies the **displayed** build snapshot or queried result and
its normalized parameter keys and values. Unknown displayed parameters are
explicitly unknown: the presenter never substitutes current controls or drafts.
Query receipt timestamps describe when the browser received/applied a response,
not database execution time, source event time or source freshness.

The scope line distinguishes snapshot rows, returned query rows and valid empty
results. A missing/invalid snapshot is **no displayed result**, not a confirmed
zero-row result. A valid zero-row snapshot or response is explicitly empty;
upstream zero and null values retain their existing treatment.

Snapshot completeness is unknown. For `truncated=true`, the note says the result
is limited to the returned rows and associated aggregates cover only those rows.
For `truncated=false`, it says *returned rows*, never entire warehouse totals:
authored SQL may contain its own LIMIT. An author must still explain any authored
SQL limit and the grain/formulas/units of the report. This note does not generate
whole-source counts, estimate omitted totals or change any aggregation.

While another selection is pending or has failed, an extra line identifies the
requested parameters and says whether the displayed result is retained. A
requested selection different from the displayed one is identified even when the
host cannot run queries. Metadata and errors are plain text. The status region is
polite, updates only when its text changes, and never moves focus, disables
controls or adds a Retry button. Generic failures do not all automatically retry;
transport policy remains the binder's existing policy.

A scoped child identifies its local selected subset of the displayed live parent.
It inherits the parent's returned-window limitation, including when its local
selected subset is small or empty. Local selections come from the actual engine
scope chain, not inferred controls. Ordinary compiled datasets keep their current
local inherited-filter presentation without this additional explanation.

## Grouping and lifecycle

All opted-in ordinary KPI/table consumers of the same logical dataset in the
same nearest native report Section share one owned block above the first
consumer. Each different child scope or separate/nested Section owns its own
block. A Panel preserves its Section ownership. The block appears before the
first affected consumer even when a table there is still waiting for lazy
rendering. Context adds wrapping flow height; KPI cards, row arrangement, chart
heights and table controls keep their existing dimensions and behavior.

The helper is loaded through the two component classes, with an idempotent shared
initializer. It reads `_fwLiveQuery.getDatasetState/subscribeDataset` and follows
`_fwFilterEngine._scopes` with a cycle/depth bound. It owns its immediate binder
subscription and local scoped-engine subscriptions per group. Repeated renderAll
and theme draws reuse them; consumer removal, opt-out or changed dataset/Section
membership reconciles them. Losing a live source removes its note, while another
source remains independent; source replacement uses the binder's new generation.
The observer releases subscriptions when their last consumer disappears. It does
not clear another component's engine state or make transport requests.

Current ordinary KPI/table loading feedback, retained previous values, local
filters, table search, stable sort, visible row limits, totals, CSV matching rows
and raw value units all remain in place. The note does not withhold generic
results or redefine missingness. CSV contains matching selected rows in the
chosen order, with no context/metadata rows. Completed section/report PNG and PDF
captures include the visible note alongside its results, without a footer.
The report-wide pending/error live capture gate remains unchanged.

## Native verification measurements

The deterministic native test fixture compares the required baseline renderer,
current opt-out and current default presentation at 1280 and 390 CSS pixels in
light and dark themes. Card dimensions and offsets within each KPI row, chart
heights, table dimensions and controls remain equal. Opt-out screenshot comparisons
allow at most one color level of Chromium antialiasing variation per channel.

In that fixture the main snapshot note occupies 36px plus a 12px bottom margin
at both widths. Pending context occupies 54px on desktop and 72px on phone, plus
that margin. Limited-result context occupies 36px/72px; the hostile-error fixture
occupies 54px/90px. The phone's scoped snapshot note occupies 54px plus margin.
These are measured wrapping costs for these parameter strings, not fixed heights.
Longer metadata can take more space. Native diagnostics/validation run without
suppression, and browser tests exercise real binder requests, locally executed
manifest SQL, retained values, scoped matching CSV and completed section/report
PNG/PDF captures. Status tests inspect DOM announcements; they do not substitute
for a manual assistive-technology audit.

## Compatibility and integration

The wire marker is `live_context: true` on `kpi_row_live` and ordinary dataset
`table` payloads only. A false option omits it, preserving baseline payloads and
legacy renderers. No export, engine, binder, chart, FilterBar or library contract
changes. Existing constructors and component-local asset collection remain valid.
No shared runtime load-order wiring or public export is required.

Gallery maintainers can link `reports/live-result-examples` as a hosted teaching
report after including the directory in their normal report build/discovery.
Its class is `LiveResultExamplesReport` in `generator.py`; it intentionally has no
`add_sections` module because live queries require report-level declarations.
Use `trellum/docs/report-live-result-context.md` as the API/compatibility link.
Existing foundation pixel fixtures should explicitly set `live_context=False`
on their KpiRow and ordinary DataTable instances when testing old presentation.
