# Framework Compatibility Policy

## Unreleased: optional TreemapChart summaries

Keyword-only `header_summary=None` preserves existing constructor positions,
markup and payloads. Enabled summaries reduce final leaf entries after current
grouping; numeric results require safe nonnegative supplied values and complete
leaf coverage. Count is independent of numeric eligibility. Existing sizing,
colors, plot heights and CSV remain unchanged. See
[TreemapChart summaries](report-treemap-summaries.md) for scope and retained
precision/grouping limits. Rebuild matching assets; no migration is needed.

## Unreleased: optional StackedBar summaries

Keyword-only `header_summary=None` preserves existing constructor positions,
markup and payloads. Opted-in summaries require an exact final bar caption and
retain original units in Percentage mode. Local/shared controls, plot heights,
legacy static title omission and CSV units remain unchanged.
See [StackedBar summaries](report-stacked-summaries.md) for input scope, owned
listener cleanup and precision limits. Rebuild matching assets; no migration.

## Unreleased: optional HeatmapChart summaries

Keyword-only `header_summary=None` preserves existing calls and default output.
Opted-in headers describe retained aggregate cells after `drop_empty`, before
color normalization. Numeric summaries require eligible original inputs;
existing plot coercion, coordinate identity and CSV limitations remain.
See [Heatmap summaries](report-heatmap-summaries.md). Rebuild matching assets;
no database or host migration is required.

## Unreleased: snapshot metric views and table column formats

`MetricView` adds a bounded derived dataset over an ordinary snapshot or one
scoped snapshot. Keyword-only `MiniKpi.binding=None` and
`DataTable.column_formats=None` preserve existing supplied metrics and table
formatting. Explicit column formats affect display/search; CSV and numeric sort
retain raw values. Comparison and workspace tables keep their existing formats.
See [metric views and formatting](report-metric-views.md) for source bounds,
precision, lifecycle and capture scope. No database or host migration is needed.

## Unreleased: optional ScatterChart summaries

Keyword-only `header_summary=None` preserves existing constructors and plots.
Opted-in summaries count point entries or summarize one coordinate of complete
finite pairs; they do not summarize bubble pixels or infer rates. Structural
filter checks remain active, while the aggregate-by-x grain heuristic no longer
misclassifies separate Scatter points. Rebuild report assets; see
[Scatter summary guidance](report-scatter-summaries.md).

## Unreleased: optional shared StackedBar controls and height

Keyword-only `show_controls=True` and `height=None` preserve existing constructors,
responsive plot sizes and local controls. Opt-in shared controls require explicit
native Toggle IDs unique across the report, including inactive scopes. Explicit
integer heights apply to the plot at every viewport. Plotted values, normalization,
CSV units and independent legends retain their current semantics. Rebuild matching
report assets; no host or database migration is required.
See [Traffic Composition](report-traffic-composition.md).

## Unreleased: optional FunnelChart summaries

Keyword-only `header_summary=None` preserves existing constructors, plots and
CSV schema. Explicit named-stage, maximum or entry-count declarations use
`scope="current_stages"`; no sequential conversion or cross-stage total is
inferred. Invalid amounts and ambiguous labels explain unavailable summaries.
Existing grouping/order is preserved. Rebuild matching report assets; no host
or database migration is required. See [Funnel summaries](report-funnel-summaries.md).

## Unreleased: optional DoughnutChart summaries

Keyword-only `header_summary=None` preserves existing constructors, markup,
plots and CSV. Explicit `sum`, `max` or `count` declarations use
`scope="all_categories"`, including hidden entries in the current result.
Sum/max withhold invalid amounts; count describes entries independently of
amount completeness. Rebuild matching report assets for the option; no host
or database migration is required. See [Doughnut summaries](report-doughnut-summaries.md).

## Unreleased: report result presentation

New ordinary LineCharts default to `result_presentation=True`; no-result text
uses the existing plot area, and retained live selections add context above it.
Live-bound KPI rows and ordinary DataTables default to `live_context=True`,
sharing one displayed-result note per dataset and native section. Notes add
wrapping height; existing card sizes, plotted values and ordinary KPI/table
loading feedback remain unchanged. Both keyword-only flags accept `False`
to preserve the prior presentation. Legacy payloads without their markers and
metrics workspace consumers retain their behavior.

No host endpoint, artifact, constructor positional-argument or CSV change is
required. See [Line result presentation](report-line-results.md) and
[live KPI/table context](report-live-result-context.md) for scope, lifecycle,
capture and completeness limits.

## Unreleased: live-result identity and request ownership

The live-query binder adds detached per-dataset state and subscriptions without
changing component constructors, request bodies, host gates or completed layouts.
Native registration owns snapshot installation so repeat rendering preserves
queried rows. Source replacement invalidates old work; a 30-second client
deadline covers fetch and body parsing. Malformed success envelopes retain the
previous result and report failure instead of becoming empty results. The first
429 still receives one automatic retry. Consumers must release subscriptions;
snapshot replacement requires new objects. Rebuild matching runtime assets;
no host or database migration is required. See [live-result state](live-result-state.md).

## Unreleased: optional SankeyChart

`SankeyChart` adds a snapshot-only chart API without changing existing chart
constructors or defaults. Complete paths preserve grouped path identities in
details/CSV; adjacent transitions never imply complete journeys or one total
across boundaries. Live-connected and chunked populations fail validation.
The existing Chart.js stack loads its bundled Sankey plugin on demand.
Image captures retain current scroll/disclosure scope; completed empty and
zero-flow states are capturable while invalid input blocks native analysis
capture. See [Sankey charts](report-sankey-charts.md) for bounds and exact data rules.

## Unreleased: optional CohortTable

`CohortTable` is an additive component API for author-supplied snapshot cells.
Existing constructors, PivotTable selectors, aggregations and KPI layouts stay
unchanged. Filters select complete precomputed cohorts; partial or altered
populations withhold the local result. Live sources, generated forecasts and
arbitrary-precision object numerics are unsupported. CSV contains all selected
source cells, while image exports retain the current horizontal view.
See [cohort tables](report-cohort-tables.md) for provenance and resource bounds.
Rebuild matching Python/HTML/data/runtime assets; no host migration is required.

## Unreleased: optional HierarchyTable

`HierarchyTable` is a new snapshot-only component; existing table constructors,
PivotTable selectors and aggregation behavior are unchanged. It consumes
uniquely identified additive leaf facts and includes collapsed descendants in
every parent total. Incomplete inputs are withheld by default; explicit
`known_subtotals=True` labels partial totals. CSV exports matching leaf facts
once, independent of expansion. Live/chunked populations and source encoding
that merges typed identities are rejected. Rebuild matching Python/HTML/data/
runtime assets; no host or database migration is required.
See [hierarchy tables](report-hierarchy-tables.md) for supported inputs and bounds.

## Unreleased: optional BarChart and AreaChart summaries

Keyword-only `header_summary=None` preserves existing constructors, plots and
output. Nonempty declarations require an explicit statistic and
`scope="plotted_series"`, with a selected series when more than one is plotted.
Summaries describe the final plotted vector after filters, aggregation, sorting
and Top-N/Other; they do not infer completeness or weighted ratio totals.
Existing LineChart declarations and calculations retain their current contract.
Rebuild matching Python/HTML/data/runtime assets for new summaries. No database
or host migration is required. See [chart summaries](report-chart-summaries.md).

## Unreleased: optional DataTable row drill-down

Keyword-only `DataTable.drill_down=None` preserves default output and positional
and subclass constructors. Opt-in declarations target existing dropdown owners
and keep raw row keys separate from displayed/exported columns. Static and
ordinary DataSource/ScopedDataSource populations and owners are supported;
live-connected drill-down fails validation. Comparison/workspace tables ignore
injected runtime options. `clear_on_parent_change=True` is a strict optional
child-dropdown flag with nonempty `depends_on`; omitted/false cascades retain
their prior behavior. No public filter API, host or database migration is needed.
Rebuild matching HTML/data/runtime assets. Existing FilterBar URL serialization
and its limitations remain; see [row drill-down](report-data-tables.md#optional-row-drill-down).

## Unreleased: optional DataTable matching-population totals

Keyword-only `DataTable.totals=None` preserves existing callers and output.
Nonempty declarations add explicit additive sums or weighted ratios over
matching delivered records before the display limit. The first displayed
column labels Total; provide a label column if every metric needs a sum.
Incomplete/unsafe inputs and undeclared live-query scope withhold affected
totals with reasons. ComparisonTable has no new totals argument; Pivot totals
and CSV remain unchanged. Static typed numeric inputs are additive payload
metadata. Rebuild matching Python/HTML/data/runtime assets; older runtimes
do not render totals. No database or host migration is needed. Native captures
include the rendered footer within the current bounded table viewport. See
[Report data tables](report-data-tables.md#optional-totals-over-matching-delivered-rows).

## Unreleased: native capture colors

Rebuild report HTML/runtime assets to capture native striped and threshold-shaded
tables, ComparisonTables, Pivot bands and theme-relative component colors with
the bundled html2canvas. PNG, PDF and analysis-section captures convert modern
computed colors to 8-bit sRGB in a temporary clone, retaining alpha and the
current table viewport. On-screen styles, source data and CSV stay unchanged.
This uses browser relative-color serialization; browsers unable to convert a
color report a capture failure and restore controls. Custom multicolor modern
gradients reject because the vendor cannot preserve their interpolation;
uniform modern stops used by native table separators are supported. Other
html2canvas CSS limitations still apply. No Python API, host or database
migration is needed.

## Unreleased: DataTable ranked row-count selection

`DataTable.row_limit` and `ComparisonTable.row_limit` are keyword-only opt-ins.
`None` preserves legacy `max_rows` output and payloads. Choices are 10, 20, 50,
100 and `"all"`; All is bounded to 1,000 matching rows, while CSV continues to
include all matching delivered rows. Existing callers and workspace definition
tables retain their prior behavior. Rebuild report artifacts to include the
selector configuration and runtime.

This document defines the stability tiers for framework APIs and the deprecation process for removing or changing them.


## Unreleased: observed-day monthly averages

Ordinary LineChart and vertical BarChart accept keyword-only
`monthly_average=None`. Opting in requires an explicit additive daily metric,
unique or complete-sum aggregation, inclusive base dates and unit. At most 1200
calendar months bound coverage rows. Default payloads, plot geometry and CSV
remain unchanged; this does not enable `month_context`. Scoped date ranges
intersect the declared interval; live-query scope is unsupported. Incomplete
legacy daily subtotals suppress the option instead of changing plotted values.
Coverage follows availability context in normal document flow. Native section
captures include it; opted-in isolated plot analysis captures reject and direct
authors to the report section. Raw external canvas images cannot include DOM
coverage. Rebuild matching HTML/data/runtime assets. No host or database
migration is needed. See [the API and coverage contract](report-monthly-averages.md).


## Unreleased: supplied LineChart observation availability

Keyword-only `series_availability=None` is additive on ordinary static/bound
LineChart series and bound ratios. Nonempty rules intentionally withhold a
supplied point when any selected raw contributor lacks finite required inputs
or its declared state is not `available`. Complete zero remains zero; legacy
undeclared skip-null sums remain unchanged. Dynamic `stack_by` and
`stack_by_options` reject nonempty rules. Static points retain row positions;
bound points use currently delivered/filtered x groups. Absent dates stay absent,
and empty selections produce no observation or note. Explicit dataset binding
still wins over `static=True`; prior positional/subclass calls remain valid.

Short metric reasons follow plot/month/event context only for visible affected
series. Summaries and CSV consume masked values; report/section captures include
the note, while canvas-only captures omit it. Rebuild matching HTML/data/assets;
older runtimes cannot enforce the new withholding semantics. No database or host
migration is needed. See [the API and input contract](report-line-availability.md).


## Optional daily month context

The five vertical category-axis chart families accept keyword-only
`month_context=False`. Existing calls retain their output. `True` declares
supplied daily reporting dates and adds a 24px month row plus dividers only
for valid, unique, increasing canonical dates. Horizontal/ranked bars cannot
opt in. No dates, values or monthly statistics are inferred. Explicit dataset
binding still wins over `static=True`. Rebuild reports to pick up the runtime;
no host migration or export runtime update is required. See
[the input and capture contract](report-month-context.md).


## LineChart dated-event labels

New LineChart builds default to keyword-only `event_labels="grouped"`: numbered
native canvas markers plus a collapsed original-name/date list. This is an
intentional default appearance change. Explicit `"full"` retains existing
in-plot labels without details. Payloads missing the flag, other chart families
and the two-argument shared annotation helper retain full labels. Positional and
subclass constructors, explicit dataset binding precedence, values, event marks,
weekday lines and plot heights are preserved. Rebuild matching assets; no
artifact schema version changes. See [dated events](report-line-events.md) for
retained loader/date limits and export behavior, and [MIGRATIONS](MIGRATIONS.md).

## Declared category colors

Keyword-only `category_colors=None` is additive on LineChart, StackedBar and
DoughnutChart only. Positional/subclass constructors, explicit dataset binding
precedence and omitted-option payloads/markup are preserved. Definitions are
validated again at render, after supplied bindings; no automatic binding is
introduced. New declarations require matching rebuilt runtime assets. Existing
static LineChart.colors keep their authored values; the dataset-bound colors
omission remains. Static wide-column lines/bars reject category declarations;
legacy aggregation semantics and limitations remain. See
[category colors](report-category-colors.md) and [migration guidance](MIGRATIONS.md).

## Supplied LineChart forecast styling

LineChart adds keyword-only `forecast_series=None`, an additive declaration of
wide-series positions or exact displayed labels. Omitted, `None` and empty
sequences preserve the existing chart payload. Enabled declarations add role
metadata to the selected static datasets or bound chart config; matching
framework assets render dashed lines, hollow points and a forecast symbol in
the existing HTML legend. Positional/subclass calls and explicit `dataset_id`
precedence remain unchanged. Static, bound wide-series and bound ratio paths
are supported; nonempty declarations with dynamic stack splits are rejected.
See [LineChart supplied forecasts](report-line-forecasts.md) and the
[migration guide](MIGRATIONS.md). This presentation API does not generate values
or change bound aggregation's existing missing-input behavior.

## KPI declaration metadata

`KpiCard` adds keyword-only `availability`, `maturity` and `comparison`; bound
`KpiRow` dictionaries share the same shape. These additive payload fields require
matching rebuilt assets to apply their semantics. Older runtimes ignore them;
do not mix new data with old runtime assets when withholding is required.
Positional/subclass construction, metric claim overrides, explicit `dataset_id`
precedence and old percent/currency units remain. Declared state does not change
DataSource transport/loading or metric-registry policies. Missing explicit null
constants in mixed rows now stay missing; undefined legacy ratio comparisons
show neutral reasons, and true zero auto changes are neutral. Valid legacy
reductions and explicit favorable-direction overrides remain. See
[KPI states](report-kpi-states.md) and the [migration guide](MIGRATIONS.md).

## Stability Tiers

### Tier 1: Stable (breaking changes require a major version bump)

These are the public contracts that reports and custom JS depend on. Changes must be backward-compatible or go through the deprecation process below.

| Surface | Description |
|---------|-------------|
| `window.fw.*` keys | All properties listed in `TestFwApiSnapshot.EXPECTED_KEYS` in `test_js_runtime.py` |
| `data.json` top-level schema | `_freshness`, `_header_meta` (`[[label, value], ...]` shown in the metadata strip), `_framework_version`, `components`, `defaultScope`, `_scopeFiles`, `_events`, `_ds_{id}`, `_metrics` (claimed metrics.yaml metrics only — `{id: {label, format, agg spec if any, version, definition_hash, component_ids}}`; absent when the report claims none) |
| `_meta.json` schema | `schema_version`, `slug`, `name`, `framework_version`, `last_run`, `last_status`, `generation_timings`, `validation`, `metrics_used` (the metrics.yaml metrics this build claims; `[]` when none). Written by `trellum.meta` (`META_SCHEMA_VERSION`, currently `2`); a missing key means version 0, which is structurally identical to v1. Bump the version on any breaking shape change. **`metrics_used` shape, by schema version:** v2 (current) is `[{"id", "definition_hash", "version"}]` — the id, the metric's `definition_hash` at build time, and its human `version`, so a host can tell a claim's build-time definition apart from the metric's current one without opening `data.json`. v1 (or a missing `schema_version`) is a bare list of ids, `["gross_revenue", ...]`. Read either through `trellum.meta.normalize_metrics_used(meta.get("metrics_used"))`, which always returns the v2 shape — a v1 entry normalizes with `definition_hash`/`version` both `None` ("unknown"), never a guessed value. See MIGRATIONS.md for the 0.2.0 note. |
| `extensions` config block | `nav_html`, `scripts`, `live_query_url` in the project-root `config.yaml`, or the same object as JSON in `FW_EXTENSIONS_JSON`. The only supported way to inject host-specific markup into a generated report. |
| `_live_queries.json` schema | `{"version": 1, "queries": {"<query_id>": {"sql": ..., "datasource": ..., "params": [{"name", "type", "required", "values"?, "max_length"?}]}}}`, written beside `_meta.json` only when the build declared live queries (`ctx.declare_live_query`). Param `type` ∈ `int\|float\|str\|date\|enum`; `enum` carries `values`; `str` may carry `max_length` (default 200). This is the ONLY artifact carrying the SQL — data.json and index.html get the `live_data_source` component entry (below), never the query text. A version other than `1` must be treated by a host as if the manifest were absent. |
| `live_data_source` component entry | `{"type": "live_data_source", "dataset_id": "...", "live": {"query_id", "params", "defaults", "bindings": [{"param"\|"min_param"/"max_param", "column", "filter_type", "sentinel"?}]}}`. A live query is a filter-engine *dataset*: `params`/`defaults` are the declared schema and snapshot values (never SQL — same invariant as `_live_queries.json`), and `bindings` is how an ordinary FilterBar's filter specs (each carrying a `param` key) map engine filter state to query params. The snapshot rows are a normal `_ds_{dataset_id}` entry alongside it. This replaced the M1 `{"live": {"query_id", "params"}}` shape a `DataTable(live=...)` used to carry directly — that form is removed, not shimmed (`DataTable(live=...)` now raises). |
| Live-query endpoint contract | Unchanged by the filter-driven redesign. The `live_query_url` extensions key is a URL *template*: a literal `{slug}` in it is replaced with the report's slug at build time, and its presence (together with `window._fwHasHost` and the absence of `window._fwShareLink` — see below) is what arms the runtime — no host, no request ever. The page POSTs `{"query_id": "...", "params": {name: value}}` to the resolved URL with `credentials: 'same-origin'` and, best effort, header `X-Trellum-Report: <slug>`. A `200` returns `{"columns": [...], "rows": [[...], ...], "truncated": bool, "elapsed_ms": int}`; non-200 renders an inline error state, with `429` honouring `Retry-After` for exactly one automatic retry. Hosts MAY rate-limit per organization/tenant at any value; the client has no way to learn that value and paces its own requests against a fixed heuristic budget divided across however many live datasets share a page — this is a courtesy only, never a substitute for the host's own enforcement. |
| `window._fwShareLink` | Optional. A host stamps this `true` (via the same build-time/serve-time script-injection mechanism as `window._fwHasHost`) to mark the page as an anonymous, unauthenticated view (e.g. a public share link) that the live-query endpoint will refuse regardless. When set, every live FilterBar renders disabled with a share-specific note instead of attempting to arm — distinct from the plain "no host" standalone note. Absent (or `false`) on every other view. |
| `window._fwEmbed` | Optional. A host stamps this `true` (same mechanism as `window._fwShareLink`, and alongside it) on a view served for framing by another origin — an embed link. The framework runtime does not act on it; it exists so host-injected scripts can tell an embedded view apart from a plain share view. Its companions `window._fwEmbedOrigins` and `window._fwEmbedBadge` are host-private and carry no framework contract. Absent (or `false`) on every other view. |
| `report.yaml` schema | All documented fields in `AGENTS.md` |
| `Component` public methods | `render_html(ctx)`, `client_js()`, `css()`, `cdn_deps()` |
| Built-in chart header context | Optional keyword-only `header_context` on LineChart, AreaChart, BarChart, StackedBar, ComboChart, DoughnutChart, HeatmapChart, FunnelChart, TreemapChart and ScatterChart. This is generated presentation markup; it adds no `data.json` fields. |
| `BaseReport` interface | `generate(ctx)`, `load_config(path)` |
| `ReportContext` public methods | `add_section()`, `set_scope()`, `get_connection()`, `metrics()`, `mark()`, date properties |
| `query_df()` signature | `query_df(conn, sql, params=None, cache_ttl=None, label=None, compact_text=True)`. Since 0.9.0 a repeated text column comes back as a `category` column unless `compact_text=False`; see MIGRATIONS.md. |
| CLI flags | `--production`, `--test`, `--no-serve`, `--port`, `--fail-on-validation`, `--auto-refresh` |
| `_meta.json` Python API | `trellum.meta.read_meta`, `write_meta`, `write_error`, `is_fresh`, `META_SCHEMA_VERSION`, `normalize_metrics_used`. |

## The embedding-application contract

An application that embeds this framework — an index, a wiki, an internal
shell — installs the standalone wheel or uses a pinned source checkout. **This
is the entire surface it is allowed to use.** Everything else in the framework
is internal and may change in a patch release without warning.

| Surface | What it is |
|---|---|
| `trellum.meta.read_meta` / `write_meta` | Read and write a report's `_meta.json` |
| `trellum.meta.META_SCHEMA_VERSION` | Schema version of that artifact |
| `trellum.meta.normalize_metrics_used` | A report's claimed metrics as `[{"id", "definition_hash", "version"}]`, regardless of which `metrics_used` schema version wrote the file — the way to read that key without hand-rolling a v1/v2 branch |
| `_meta.json` schema | The versioned artifact itself, per the Tier 1 row above |
| `trellum.runner.scan_report_configs()` | Discover reports under a project root |
| `python -m trellum.run` CLI flags | The documented flags, per the Tier 1 row above. This is the supported way to build a report — never by importing the runner internals |
| `trellum.testing.runner.test_all_reports()` | Build every report against mock data, for a host's own "test run" action |
| `trellum.themes.THEME_REGISTRY` | Available themes |
| `trellum.data.assistant_query.validate_query`, `execute_query`, `describe_source` | Restricted PostgreSQL source reads: explicit relation grants, read-only identity, execution/cancellation deadlines and bounded JSON evidence. Synchronous worker API; callbacks run on auxiliary threads. `QueryRejected` is a safe rejection; `QueryExecutionError.confirmed` distinguishes a confirmed remote completion from an uncertain outcome, and `.cancelled` identifies confirmed caller cancellation. |
| `trellum.data.query_df` / `resolve_connection` | Run a query against a configured data source, per the `query_df()` Tier 1 row |
| `trellum.data.query.sql_dialect_for(source_type)` | The string-literal dialect a host must bind parameters with for a datasource type |
| `trellum.data.query.bind_params(sql, params, dialect="standard")` | Substitute `:name` placeholders with escaped literals; `dialect` from `sql_dialect_for` |
| `trellum.runner.local_annotation_events(config)` | A report's `annotations.events` from its `report.yaml` config, normalised the way a build merges them into `_events` |
| `trellum.data.datasource_config` | `data-sources/config.yaml` read/write helpers |
| `trellum.metrics.load_metrics` | The project's `metrics.yaml` as `{name: Metric}` — the business-metric registry a host may present or index. `{}` when the file is absent |
| `trellum.metrics.parse_metrics_document` | Parse an already decoded registry document without file I/O, imports of providers or SQL execution. Raises `ValueError` for non-JSON values, cycles or bounds (64 KiB serialized, 5000 nodes, depth 12, 200 metrics); returns `MetricsLoadResult` with structural `problems`. Callers must bound decoding before calling. |
| `trellum.metric_execution` | Optional pure canonical execution: `make_binding`, `publish_bindings`, `execute_metric`, `MetricUnavailable`; explicit build contracts and bounded complete daily inputs, as described below. |
| Optional execution metadata | `data.json._metric_execution` maps metric IDs to explicit versioned execution fingerprints, semantics, coverage and column mappings. `_meta.json.metric_execution_build` pins the successful build ID; absence never implies canonical eligibility. |
| `trellum.project.get_project_root()` | Project root resolution |
| `trellum.project.load_project_config()` | The project's `config.yaml` |
| `trellum.reporting_day.load_reporting_settings(project_root=None)` | Validated project reporting timezone, day boundary and Metrics build time. Pass an explicit root in multi-project hosts. |
| `trellum.rendering.cdn.serve_vendor_request()` | Serve `/_vendor/*` from your own HTTP server |
| `trellum.output_backends.backends.resolve_output_backend()` | The configured output backend |
| The `extensions` block | How an embedding application injects its markup, per the Tier 1 row above |

Two rules keep this honest, in both directions:

1. **The framework must not know the host exists.** No import, no
   `/api/*` request, no markup naming a product that isn't this one. Nothing
   enforces this automatically, so it is a review question.
2. **The host must not reach past this table.** Importing
   `trellum.rendering.html_builder` internals or a `_`-prefixed function
   couples the two release cycles back together, which is the thing the
   split was for.

Adding a row here is a deliberate act: it is a promise to a downstream product
that the framework cannot quietly break.

### Project reporting days and schedules

Project `config.yaml` may declare `reporting.timezone` (IANA name),
`reporting.day_start` (`HH:MM`), and `reporting.metrics_build_time` (`HH:MM`
or null). Absent settings mean UTC, midnight, and no inherited build schedule.
`ReportContext` date properties use the reporting-day label; `now_utc` stays
an actual UTC instant. `reporting_day_bounds(day)` supplies a half-open UTC
interval for timestamp queries. Existing daily-grain datasets are not rebucketed.

`schedule: {project: metrics}` in `report.yaml` opts into the project's daily
Metrics build time. `BaseReport.load_config(path)` and
`scan_report_configs(reports_dir)` resolve it into the usual `schedule.cron`
and `schedule.timezone` fields, using the project containing `reports/`.
An explicit `schedule.cron`, including an empty one, wins over inheritance.
Unknown project schedule names fail validation. Scans read inherited settings
once per project, without changing process-global root state or source files.
Hosts rescan after repository updates and schedule the resulting cron as usual.
Delivery schedules and idle-build suspension retain their existing semantics.

### Tier 2: Deprecated (will be removed after 2 minor versions)

These still work but should not be used in new code. The framework may emit `console.warn` in development mode.

| Surface | Replacement |
|---------|-------------|
| `window.fmtCompact` | `window.fw.fmtCompact` |
| `window.fmtCompact$` | `window.fw['fmtCompact$']` |
| `window.getActiveTheme` | `window.fw.getActiveTheme` |
| `window.getThemeColors` | `window.fw.getThemeColors` |
| `window.switchTheme` | `window.fw.switchTheme` |
| `window.getFormatter` | `window.fw.getFormatter` |
| `window._fwFilterEngine` | `window.fw.filterEngine` |
| `window._fwAggregate` | `window.fw.aggregate` |
| `window._fwLiveWrap` | `window.fw.liveWrap` |
| `window._fwFilterEngine._state[dsId]` | `window.fw.filterEngine.getFilterState(dsId)` |
| `window._fwFilterEngine._datasets[dsId]` | `window.fw.filterEngine.getFiltered(dsId)` |
| `window._fwFilterEngine._columnar[dsId]` | `window.fw.filterEngine.isReady(dsId)` + `getFiltered(dsId)` |

### Tier 3: Internal (may change without notice)

Anything prefixed with `_` that is not listed above, including:

- `window._fwFilterEngine._cache`, `._subscribers`, `._buildColumnarIndexes`
- `window._fwRenderers` (internal renderer registry)
- `window._fwTimings` (instrumentation data, structure may change)
- `window._scopeCache`, `window._currentScope`
- Python functions prefixed with `_` in framework modules

## Deprecation Process

1. **Announce**: Add a note to `MIGRATIONS.md` describing the change, the old API, and the replacement.
2. **Warn**: Add `console.warn('[fw deprecation] ...')` in development mode (non-production) on first use of the deprecated API. Keep the old behavior working.
3. **Test**: Add or update tests in `test_js_runtime.py` to verify the deprecation warning fires.
4. **Remove**: After 2 minor version bumps from the announcement, remove the deprecated API. Update `MIGRATIONS.md` with the removal version.

## Version Scheme

- `trellum.__version__` follows `MAJOR.MINOR.PATCH`.
- **MAJOR**: Breaking changes to Tier 1 APIs (should be rare).
- **MINOR**: New features, new components, Tier 2 deprecation announcements.
- **PATCH**: Bug fixes, performance improvements, internal refactors.

## Compatibility Matrix

| Framework Version | Report Contract | Notes |
|-------------------|-----------------|-------|
| 0.1.x | v1 | First public release. Columnar filter engine, `window.fw` namespace, `_framework_version` in outputs, `_meta.json` with `schema_version`, `extensions` for embedding markup |
| 0.2.x | v1 | `_meta.json` `META_SCHEMA_VERSION` 1 → 2: `metrics_used` entries carry `definition_hash`/`version`, not just an id — see MIGRATIONS.md. Additive to the report contract itself (no report.yaml/component change), so the contract version stays v1. |

0.1.0 is the first release. See `MIGRATIONS.md` for what changed since.

## Security compatibility notes

Generated Metrics workspaces declare `_meta.json.metrics_workspace.version = 1`.
Its `build` is a 32-character lowercase hexadecimal identity. The reserved sibling
files `_metrics-workspace-<build>-page-<positive integer>.html` and `.json`, plus
`_metrics-workspace-<build>-catalog.json`, are pages of the same report, not authored
views. Hosts can use `trellum.metrics_workspace.owned_name` to identify them.
Each page displays at most 25 monitored blocks, and only monitored blocks add
pages: with 25 or fewer monitored metrics there is one page, however many
unmonitored definitions exist. The "defined but not monitored" table appears once,
on the last page, where the catalog routes every definition; its rows come from
the catalog and a build writes no definition rows into any page payload.
Root `data.json`, `_meta.json.metrics_used` and build details remain complete for
source indexes and exports. Initial chart loading uses the immutable page payload
and rejects an HTML/catalog/metadata build mismatch with a refreshable failure.

Publication stages the entire replacement before exposing it. Root replacement
temporarily marks mutable metadata uncertain, publishes success last, and restores
the prior roots on failure. Previous owned page files are removed only after
success; an untrusted old manifest never authorizes deletion outside this exact
basename grammar or through symlinks. Deep links to obsolete pages therefore stop
serving prior content after a successful smaller rebuild.

Missing workspace markers on successfully inspected served artifacts mean legacy
format (version 0). Uninspected historical artifacts remain unknown, and hosts
must not infer format from current source code or report configuration. Legacy
artifacts remain available through explicit viewing and rebuilding; their
monolithic rendering cost is separate from the version 1 page budgets. Fresh,
ready external artifacts marked version 1 remain usable with an honest observed
source-quality label. Failed attempts do not supersede a served build's marker.

Ordinary `DataTable` cell/header strings and `PivotTable` dimension labels display
literally. Reports that relied on embedded HTML strings should use explicitly
authored `RawHTML` content. CSV exports retain the literal data.

`_live_queries.json` remains an internal host artifact. Preview servers deny its
URL, compressed sibling and filename aliases for GET and HEAD. Portal remote
builds store it under a separate `_private/` namespace; operators must update
their edge gateway and plan legacy manifest/cache cleanup as described in the
portal's storage upgrade notes. The host still reads older build manifests.

The workspace-only DataTable `workspacePageSize: 25` opt-in uses the complete
light catalog metadata for all-column search, sort and CSV, while rendering at
most 25 matching rows; ordinary tables retain their existing behavior. Chart page payloads project needed series
columns without changing the complete root payload. Selection preferences belong
to the artifact root and survive page navigation. A host unable to inspect a
legitimate cross-origin served iframe retains a bounded visible viewport and
labels chart readiness unconfirmed; iframe load is not chart-ready evidence.

## Build progress and failure evidence (v1)

A host may set `TRELLUM_DIAGNOSTICS_PATH` to a private, per-attempt file outside
published output. The framework atomically replaces this file before blocking
connection, execute, fetch and render work. `trellum.diagnostics.read_diagnostics`
reads at most 16 KiB and allowlists v1 fields; missing, invalid and unknown-version
files return `{}`. Diagnostics are optional and an unwritable destination never
changes the build outcome. Hosts own file lifetime, permissions and retention.

The snapshot contains `stage`, `state` (`active`, `completed`, `failed`), source
type, stage start time (Unix seconds), SHA-256 source/query identifiers, attempt count (at most three), managed-read retry count (capped at 255) and the last eight sanitized retry reasons, classifier
`transient`, `query_retry_safe`, sticky `unsafe_operation`, at most five cause
classes/codes and sixteen bounded stage timings. Query fingerprints use unbound
SQL; source names are hashed. Each cause includes up to three traceback location
hashes and line numbers. Exception messages, source paths, SQL, parameters,
credentials and result rows are excluded. Hashes identify repeated locations;
they omit SQL and traceback source (known candidate identifiers may still be correlated). Concurrent query threads share
one last-writer snapshot, so it is last-known evidence, not a complete trace.
Drivers combining execution and materialization (such as ClickHouse's DataFrame
API) expose one `execute` stage because they provide no separate fetch boundary.
Hosts may record publication with the same `stage("publish")` context manager.

A completed stage clears transient failure evidence from a recovered inner query
retry. `unsafe_operation` remains true for the attempt after unsafe SQL or direct
managed-driver APIs are entered, even if later safe operations succeed. An active
snapshot after process death identifies where work was last observed; it does
not establish why it died or prove the process has stopped.

Neither `transient` nor `query_retry_safe` authorizes whole-report replay. Report
code is arbitrary Python. Whole-report recovery is enabled by default for a
valid report configuration. Authors must opt out with
`recovery: {replay_safe: false}` when the generator or its helpers have external
side effects, unknown write outcomes or detached/background work that outlives
the build. A missing `recovery` section or `replay_safe` setting keeps recovery
enabled; malformed settings fail closed. This default is not a blanket override
for query safety: hosts still veto unknown write outcomes and unsafe operations.
A host must separately enforce admission, retry budgets, process
termination/fencing and delivery deduplication. The framework only validates the
configuration shape and emits evidence; it does not schedule retries. Raw driver
or arbitrary Python activity outside framework APIs cannot be fully observed.

### Optional canonical metric execution

Ordinary metric claims and older reports continue to render unchanged. Assistant
canonical execution additionally requires `metrics.yaml` `execution` metadata
and an explicit report.yaml `metric_bindings` entry. Missing or unsupported
metadata leaves that execution path unavailable; it does not change rendering.

`execution` declares `timezone` (IANA), `unit`, `currency` (string or null),
`grain: day`, `dimensions`, `additive_dimensions`, `filters` (dimension names),
`time_aggregation` (`sum`, `last`, `average`, `none`), `null_policy: propagate`,
`zero_denominator: "null"`, and `max_age_seconds` (1–31536000).
Dimensions and grain must match the registry dataset. Only canonical sum,
absolute sum and ratios of additive operands are supported. Ratios are computed
from operand totals; average means average of complete daily totals. Distinct
counts require non-additive declarations and cannot be rolled up beyond their
published grain. Missing periods or dimension-day cells are unavailable, never inferred as zero.
Every observed full dimension tuple within declared artifact coverage must have
every requested day, including before dimensions are rolled up. Filters and
dimension identities distinguish booleans, numbers and strings.

Each `metric_bindings.<metric_id>` declares `output_dataset`, `columns` (an
explicit canonical-column to output-column mapping covering date, dimensions
and operands), `coverage_start`, `coverage_end` (exclusive), and
`source_revision`. Registry dataset names never imply output dataset identity.
The build publishes only claimed, non-overridden opt-ins in
`data.json._metric_execution`, with a generated build ID and timestamp. The
matching `_meta.json.metric_execution_build` pins the metadata. A versioned
SHA-256 execution fingerprint covers the entire manifest, including binding,
semantics and coverage; existing metric definition hashes are unchanged.
This is an integrity/version check, not a signature or proof of source quality.

The executor accepts explicit ISO dates `[start, end)` in the business timezone,
declared filters and breakdowns. It requires complete daily coverage, rejects
chunked input and duplicate grain rows, and computes before display truncation.
The current inline budgets are 2 MiB input, 10000 rows, 366 periods, 50 output
rows and 32000 output bytes. Report-bound tools use only that report's published
snapshot; studio tools also compare the current bounded registry definition and
semantics before execution. Definitions never include canonical SQL in these
tools. Unsupported artifacts can be investigated through separately granted
source tools. Multi-chunk execution, non-daily calendars and live canonical
bindings are not implemented by this contract version.

Generic assistant report queries retain raw previews and legacy behavior when no
execution contract is present. Explicit contracts guard reductions of mapped
metric operands: non-additive dimensions must remain grouped, and last/average/
non-additive time rules cannot be replaced by an overall sum. Ratio operand
reductions are conservatively routed to `query_metric` for weighted calculation
or a granted source query. Raw reads and descriptive min/max/count/nunique reads
remain exploratory and receive no approved-calculation assurance.

## Optional DataTable pinning

New builds may opt in to keyword-only `DataTable.pin_header`, `pin_footer` and
`pin_height`; all default to the existing unpinned output. Footer pinning
requires declared totals. Existing reports need no edits; rebuild only to use
the new behavior. See [report data tables](report-data-tables.md#optional-datatable-edge-pinning).
