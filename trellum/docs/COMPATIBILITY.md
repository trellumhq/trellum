# Framework Compatibility Policy

This document defines the stability tiers for framework APIs and the deprecation process for removing or changing them.

## Stability Tiers

### Tier 1: Stable (breaking changes require a major version bump)

These are the public contracts that reports and custom JS depend on. Changes must be backward-compatible or go through the deprecation process below.

| Surface | Description |
|---------|-------------|
| `window.fw.*` keys | All properties listed in `TestFwApiSnapshot.EXPECTED_KEYS` in `test_js_runtime.py` |
| `data.json` top-level schema | `_freshness`, `_framework_version`, `components`, `defaultScope`, `_scopeFiles`, `_events`, `_ds_{id}`, `_metrics` (claimed metrics.yaml metrics only — `{id: {label, format, agg spec if any, version, definition_hash, component_ids}}`; absent when the report claims none) |
| `_meta.json` schema | `schema_version`, `slug`, `name`, `framework_version`, `last_run`, `last_status`, `generation_timings`, `validation`, `metrics_used` (the metrics.yaml metrics this build claims; `[]` when none). Written by `trellum.meta` (`META_SCHEMA_VERSION`, currently `2`); a missing key means version 0, which is structurally identical to v1. Bump the version on any breaking shape change. **`metrics_used` shape, by schema version:** v2 (current) is `[{"id", "definition_hash", "version"}]` — the id, the metric's `definition_hash` at build time, and its human `version`, so a host can tell a claim's build-time definition apart from the metric's current one without opening `data.json`. v1 (or a missing `schema_version`) is a bare list of ids, `["gross_revenue", ...]`. Read either through `trellum.meta.normalize_metrics_used(meta.get("metrics_used"))`, which always returns the v2 shape — a v1 entry normalizes with `definition_hash`/`version` both `None` ("unknown"), never a guessed value. See MIGRATIONS.md for the 0.2.0 note. |
| `extensions` config block | `nav_html`, `scripts`, `live_query_url` in the project-root `config.yaml`, or the same object as JSON in `FW_EXTENSIONS_JSON`. The only supported way to inject host-specific markup into a generated report. |
| `_live_queries.json` schema | `{"version": 1, "queries": {"<query_id>": {"sql": ..., "datasource": ..., "params": [{"name", "type", "required", "values"?, "max_length"?}]}}}`, written beside `_meta.json` only when the build declared live queries (`ctx.declare_live_query`). Param `type` ∈ `int\|float\|str\|date\|enum`; `enum` carries `values`; `str` may carry `max_length` (default 200). This is the ONLY artifact carrying the SQL — data.json and index.html get the `live_data_source` component entry (below), never the query text. A version other than `1` must be treated by a host as if the manifest were absent. |
| `live_data_source` component entry | `{"type": "live_data_source", "dataset_id": "...", "live": {"query_id", "params", "defaults", "bindings": [{"param"\|"min_param"/"max_param", "column", "filter_type", "sentinel"?}]}}`. A live query is a filter-engine *dataset*: `params`/`defaults` are the declared schema and snapshot values (never SQL — same invariant as `_live_queries.json`), and `bindings` is how an ordinary FilterBar's filter specs (each carrying a `param` key) map engine filter state to query params. The snapshot rows are a normal `_ds_{dataset_id}` entry alongside it. This replaced the M1 `{"live": {"query_id", "params"}}` shape a `DataTable(live=...)` used to carry directly — that form is removed, not shimmed (`DataTable(live=...)` now raises). |
| Live-query endpoint contract | Unchanged by the filter-driven redesign. The `live_query_url` extensions key is a URL *template*: a literal `{slug}` in it is replaced with the report's slug at build time, and its presence (together with `window._fwHasHost` and the absence of `window._fwShareLink` — see below) is what arms the runtime — no host, no request ever. The page POSTs `{"query_id": "...", "params": {name: value}}` to the resolved URL with `credentials: 'same-origin'` and, best effort, header `X-Trellum-Report: <slug>`. A `200` returns `{"columns": [...], "rows": [[...], ...], "truncated": bool, "elapsed_ms": int}`; non-200 renders an inline error state, with `429` honouring `Retry-After` for exactly one automatic retry. Hosts MAY rate-limit per organization/tenant at any value; the client has no way to learn that value and paces its own requests against a fixed heuristic budget divided across however many live datasets share a page — this is a courtesy only, never a substitute for the host's own enforcement. |
| `window._fwShareLink` | Optional. A host stamps this `true` (via the same build-time/serve-time script-injection mechanism as `window._fwHasHost`) to mark the page as an anonymous, unauthenticated view (e.g. a public share link) that the live-query endpoint will refuse regardless. When set, every live FilterBar renders disabled with a share-specific note instead of attempting to arm — distinct from the plain "no host" standalone note. Absent (or `false`) on every other view. |
| `window._fwEmbed` | Optional. A host stamps this `true` (same mechanism as `window._fwShareLink`, and alongside it) on a view served for framing by another origin — an embed link. The framework runtime does not act on it; it exists so host-injected scripts can tell an embedded view apart from a plain share view. Its companions `window._fwEmbedOrigins` and `window._fwEmbedBadge` are host-private and carry no framework contract. Absent (or `false`) on every other view. |
| `report.yaml` schema | All documented fields in `AGENTS.md` |
| `Component` public methods | `render_html(ctx)`, `client_js()`, `css()`, `cdn_deps()` |
| `BaseReport` interface | `generate(ctx)`, `load_config(path)` |
| `ReportContext` public methods | `add_section()`, `set_scope()`, `get_connection()`, `metrics()`, `mark()`, date properties |
| `query_df()` signature | `query_df(conn, sql, params=None, cache_ttl=None)` |
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
| `trellum.data.query_df` / `resolve_connection` | Run a query against a configured data source, per the `query_df()` Tier 1 row |
| `trellum.data.query.sql_dialect_for(source_type)` | The string-literal dialect a host must bind parameters with for a datasource type |
| `trellum.data.query.bind_params(sql, params, dialect="standard")` | Substitute `:name` placeholders with escaped literals; `dialect` from `sql_dialect_for` |
| `trellum.runner.local_annotation_events(config)` | A report's `annotations.events` from its `report.yaml` config, normalised the way a build merges them into `_events` |
| `trellum.data.datasource_config` | `data-sources/config.yaml` read/write helpers |
| `trellum.metrics.load_metrics` | The project's `metrics.yaml` as `{name: Metric}` — the business-metric registry a host may present or index. `{}` when the file is absent |
| `trellum.project.get_project_root()` | Project root resolution |
| `trellum.project.load_project_config()` | The project's `config.yaml` |
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
