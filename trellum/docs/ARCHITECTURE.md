# Architecture

This document describes the internal architecture of the BI report framework. It is intended for contributors who want to understand or modify the framework itself. For report authors, see the main [README](../README.md).

---

## Module Map

```
trellum/                             # the repo root IS the package
├── __init__.py                  # Version, public exports (BaseReport, ReportContext)
├── assets.py                    # Reads static/js and static/css; the only place
│                                #   that knows where client assets live
├── project.py                   # Project root resolution (FW_PROJECT_ROOT, cwd)
├── report.py                    # BaseReport, ReportContext
├── meta.py                      # _meta.json helpers (framework output artifact)
├── config.yaml                  # Project config defaults (extensions block)
│
├── agent/                       # What the framework tells an agent about itself
│   ├── agentdoc.py              # Topics, component signatures, validator check ids
│   │                            #   -- all derived from the code, never hand-kept
│   └── agentsetup.py            # CLAUDE.md / AGENTS.md pointers, session hooks
│
├── cli/                         # `python -m trellum ...`
│   ├── __init__.py              # Argument parsing, dispatch, main()
│   ├── console.py               # UTF-8 terminal setup
│   ├── describe.py              # What `trellum` with no arguments prints
│   └── commands/                # One module per command group: guide, checks,
│                                #   validate, setup, data, query, serve, review,
│                                #   doctor
│
├── components/                  # All UI components
│   ├── base.py                  # Component, RenderContext, asset collection
│   ├── charts/                  # base (_ChartBase), line_area, bar, doughnut,
│   │                            #   heatmap, funnel, treemap, scatter
│   ├── kpis.py                  # KpiCard, KpiRow, MiniKpi
│   ├── tables.py                # DataTable, PivotTable, ComparisonTable
│   ├── controls.py              # Toggle, Dropdown, TabGroup, DateSelector
│   ├── layout.py                # Section, Panel, Grid, SplitPane, RawHTML, Visible
│   ├── filterable.py            # DataSource, ScopedDataSource, FilterBar
│   ├── header.py                # ReportHeader
│   ├── ab_compare.py            # ABCompare
│   └── filters/                 # Filter plugins: date_range, toggle, dropdown, flag
│
├── rendering/                   # HTML assembly pipeline
│   ├── html_builder.py          # render_report(), page assembly
│   ├── sections.py              # Section-list rendering, host nav injection
│   ├── artifacts.py             # manifest.json, _meta.json, data chunks and their GC
│   ├── js_runtime.py            # Loads static/js/runtime/*.js, emits the _fwConfig
│   └── cdn.py                   # Vendor URL registry and tag builder
│
├── static/                      # Everything the browser runs, as real files
│   ├── js/runtime/              # 19 modules: formatters, theme, chart_defaults,
│   │                            #   annotations, filter_engine, url_sync, aggregate,
│   │                            #   state, auto_refresh, ui_handlers, chunk_loader,
│   │                            #   export, fw_namespace, ...
│   ├── js/components/           # One per component type that has client JS
│   ├── js/review/               # The review overlay, 15 modules
│   ├── js/data_loader.js        # Fetches data.json and dispatches to renderers
│   ├── css/                     # base.css, review.css, components/*.css
│   └── vendor/                  # Chart.js, Plotly, topojson, Inter -- bundled
│
├── validation/                  # Post-generation structural validator
│   ├── result.py                # Check, ValidationResult, suppression handling
│   ├── walk.py                  # Shared component-tree walking
│   └── checks/                  # One module per rule family: datasource, rawhtml,
│                                #   theme, annotations, columns, visibility, scopes,
│                                #   yaml_schema, structural, effectiveness
│
├── runner/                      # Building and serving reports
│   ├── __init__.py              # CLI parsing, main()
│   ├── execute.py               # run_report / run_all_reports
│   ├── discovery.py             # Finding reports, project themes and components
│   ├── events.py                # Timeline events, weekday highlighting
│   ├── serve.py                 # The preview HTTP server
│   ├── ports.py                 # Which port, and who already holds it
│   └── console.py               # Console encoding, log timestamps
│
├── review/                      # The live review loop (server half)
│   ├── state.py                 # The session, shared by handler threads
│   ├── http.py                  # /_fw/review* endpoints
│   └── inject.py                # Overlay assembly and injection
│
├── reporting/                   # What the framework says about a finished build
│   ├── diagnostics/             # types, walk, datasets, filters -- _details.json
│   └── gallery.py               # The index page over a directory of built reports
│
├── scaffold/                    # Writing new things into a project
│   ├── init_project.py          # `python -m trellum.init`
│   └── new_report.py            # `python -m trellum.new`
│
├── themes/                      # Theme dataclass + one module per theme (13)
├── data/                        # query_df, caching, connections, resolvers,
│   └── drivers/                 #   and one driver module per database
├── output_backends/             # OutputBackend protocol, local filesystem
├── stats/                       # winsor, bootstrap, cuped, and ab/ (users,
│                                #   aggregate, modes)
├── testing/                     # test_report / test_all_reports, mock data,
│                                #   and the suite itself
│
├── docs/                        # ARCHITECTURE (this file), COMPATIBILITY,
│                                #   MIGRATIONS, LICENSING
├── demo/                        # Standalone demo project: fixtures, reports, dev.ps1
├── examples/                    # Portable examples for other projects
│
├── run/__main__.py              # python -m trellum.run entry point
├── new/__main__.py              # python -m trellum.new entry point
└── init/__main__.py             # python -m trellum.init entry point
```

---

## Rendering Pipeline

The framework uses a **server-side generation, client-side rendering** pattern.

### Step 1: Python generates HTML shell + JSON

```
runner.py::run_report()
    │
    ├── discover_report()            # Import generator.py, find BaseReport subclass
    ├── _load_events()               # Load events.yaml, filter by studio + date range
    ├── _discover_project_themes()   # Auto-discover themes/ directory
    ├── _discover_project_components()
    │
    ├── report.generate(ctx)         # User code runs here: queries DB, builds component tree
    │
    ├── validate_report(ctx)         # Post-generation structural validation
    │
    └── render_report(ctx)           # HTML builder assembles everything
        │
        ├── _render_section_list()   # Walk sections, call Component.render_html(RenderContext)
        │   ├── RenderContext.render_children()
        │   │   └── child.render_html(ctx)   # Each component emits HTML + registers data
        │   │       └── ctx.register(id, data)
        │   └── Skip .fw-section wrapper when _no_section_wrap=True (DataSource/FilterBar)
        │
        ├── Collect CSS/JS assets    # Deduped union from all rendered component types
        │   ├── Component.css()
        │   ├── Component.client_js()
        │   └── Component.cdn_deps()
        │
        ├── build_cdn_tags()         # Merge _ALWAYS_INCLUDED + component CDN keys → <script>/<link>
        ├── generate_js_runtime()    # Embed formatters, filter engine, theme switcher, etc.
        ├── _generate_data_loader_js()  # Fetch data.json + dispatch to renderers
        │
        ├── Write index.html         # Complete HTML document
        ├── Write data.json + .gz    # All component data, events, raw data
        └── Write _meta.json         # Run metadata (schema_version, slug, name, timestamps, validation)
```

### Step 2: Browser loads and renders

```
Browser loads index.html
    │
    ├── Flash prevention script      # Set data-theme from localStorage before first paint
    ├── CDN scripts load             # Chart.js, plugins, fonts
    ├── JS runtime executes          # Formatters, filter engine, theme system initialized
    ├── Component client_js loads    # window._fwRenderers[type] registered per component type
    │
    ├── Loading overlay shown
    │
    ├── fetch('data.json')
    │   └── _renderComps(components)
    │       └── For each component ID in data.json:
    │           ├── Find DOM element by ID
    │           ├── Look up renderer: window._fwRenderers[cfg.type]
    │           └── Call renderer(id, cfg) → Chart.js chart created, KPI filled, etc.
    │
    ├── _initToggleVis()             # Wire toggle-driven visibility
    ├── _buildAnnoToggleBar()        # Build annotation toggle pills
    ├── _fwUrlSync.write()           # Sync filter state to URL
    │
    └── Loading overlay fades out
```

---

## Component System

### Component Base Class

Every UI element extends `Component` from `trellum/components/base.py`. The base class defines four key methods:

- **`render_html(ctx: RenderContext)`** -- Returns the HTML shell for this component. Calls `ctx.register(id, data)` to push data into the JSON payload.
- **`client_js()`** (classmethod) -- Returns JS that registers a renderer on `window._fwRenderers[type]`.
- **`css()`** (classmethod) -- Returns CSS rules for this component type.
- **`cdn_deps()`** (classmethod) -- Returns a list of CDN keys this component needs.

Two class-level attributes control behavior: `_component_type` (the type key for renderer dispatch) and `_no_section_wrap` (skip the `.fw-section` wrapper, used by DataSource/FilterBar).

### RenderContext

Created by the HTML builder (one per scope for multi-scope reports):

- `next_id()` -- generates unique DOM IDs (`fw_c1`, `fw_c2`, ...)
- `register(cid, data)` -- pushes component data into the JSON payload
- `render_children(children)` / `render_child(child)` -- recursively render nested components
- `add_raw_js(js)` / `add_raw_data(key, data)` -- used by RawHTML and DataSource
- `collect_assets()` -- deduped CSS/JS from all seen component types
- `collect_cdn_deps()` -- union of CDN keys from all rendered components
- `_seen_types` -- set of component classes that were rendered (for asset collection)

### Renderer Dispatch Pattern

Components are self-describing. The framework never hard-codes component types:

1. **Python side**: `Component.render_html()` emits an empty container (e.g. `<canvas id="fw_c3">`) and calls `ctx.register("fw_c3", {"type": "chart", ...})`.
2. **JS side**: `Component.client_js()` registers a renderer function: `window._fwRenderers['chart'] = function(id, cfg) { new Chart(...) }`.
3. **At load time**: The data loader iterates `data.json.components`, finds the DOM element by ID, looks up the renderer by `cfg.type`, and calls it.

This means adding a new component type requires zero changes to the framework dispatcher -- the component carries everything it needs.

### Asset Deduplication

CSS and JS are collected from all rendered component types using content-based deduplication. This is important because chart types like `LineChart`, `BarChart`, and `StackedBar` all inherit from `_ChartBase`, which provides a large shared JS renderer and CSS. The deduplication ensures this is emitted only once.

---

## Data Flow

```
SQL (queries.py)
    │
    ├── query_df(conn, sql, params)
    │   ├── bind_params()           # Replace :param_name with quoted values
    │   ├── Cache check              # SHA256(bound_sql) → pickle file
    │   ├── Semaphore acquire        # Limit concurrent DB queries (default 4)
    │   ├── cursor.execute()
    │   ├── DataFrame construction
    │   ├── Decimal → float conversion
    │   └── Cache write (atomic: tempfile + rename)
    │
    └── Returns pd.DataFrame
          │
          ├── DataSource("ds_id", df)
          │   └── render_html():
          │       ├── Columnar JSON serialization (+ optional dictionary encoding)
          │       └── ctx.add_raw_data("_ds_ds_id", columnar_data)
          │
          ├── Components use df for static rendering
          │   └── LineChart(df, x="col", y="col", dataset_id="ds_id")
          │       └── register(id, {type:"chart", dataset_id:"ds_id", x:"col", ...})
          │
          └── data.json
              ├── components: {fw_c1: {...}, fw_c2: {...}}
              ├── _ds_ds_id: {columns, data}   # DataSource payload
              ├── _events: [...]                # Annotation events
              └── _freshness: {generated_at, refresh_seconds}
                    │
                    └── Browser
                        ├── _fwFilterEngine.initColumnar("ds_id", data)   # Register columnar dataset + indexes
                        ├── FilterBar → setFilter() → notify()
                        │   └── Subscribers re-render with filtered rows
                        └── Charts/KPIs/Tables subscribe to filter changes
```

---

## Filter Engine Internals

The filter engine (`window._fwFilterEngine`) is a client-side pub/sub system:

### Data Flow

1. **DataSource renderer** calls `_fwFilterEngine.initColumnar(dsId, data)` -- stores the dataset in columnar form and builds indexes.
2. **FilterBar renderer** initializes filter plugins and wires them to call `setFilter()` on user interaction.
3. **`setFilter(dsId, filterId, column, mode, value)`** merges the filter into the active filter set for that dataset.
4. **`notify(dsId)`** runs all active filters (AND logic), produces filtered rows, and calls all subscribers.
5. **Subscribers** (charts, KPIs, tables with `dataset_id`) receive the filtered rows and re-render.

### Filter Modes

| Mode | Behavior |
|---|---|
| `equals` | Row passes if `row[column] === value` |
| `in` | Row passes if `value.includes(row[column])` |
| `range` | Row passes if `value[0] <= row[column] <= value[1]` |
| `flag` | Row passes if `row[column] == flagValue` when active |

### Propagation

When `FilterBar` has `propagate_to={target_ds: {src_col: tgt_col}}`, setting a filter on the source dataset also calls `setFilter()` on the target dataset with the mapped column name. This syncs shared dimensions (e.g. platform) across datasets with different grains (daily vs monthly).

### URL Sync

`window._fwUrlSync` serializes filter state, toggle positions, and tab selections to the URL query string. This enables shareable links that preserve the dashboard state. Each component registers with `_fwUrlSync` and the state is read back on page load.

---

## Theme System Internals

### Theme Dataclass

`Theme` (in `trellum/themes/theme.py`) is a Python `@dataclass` with fields for every design token:

- **Backgrounds**: `bg_main`, `bg_card`, `bg_card_hover`, `bg_header`
- **Text**: `text_main`, `text_secondary`, `text_muted`
- **Accents**: `accent_green`, `accent_red`, `accent_blue`, `accent_yellow`
- **Borders/Shadow**: `border_color`, `border_radius`, `shadow`
- **Chart palette**: `chart_colors` (list of 10 hex colors)
- **Grid/Tick**: `grid_color`, `tick_color`
- **Typography**: `font_family`, `font_size_base`, `font_size_small`, `font_size_kpi`
- **Spacing**: `spacing_sm`, `spacing_md`, `spacing_lg`

### CSS Variable Generation

`Theme.to_css_vars(selector)` converts all scalar fields to CSS custom properties (e.g., `bg_main` becomes `--bg-main`). List fields (like `chart_colors`) are excluded from CSS and exposed to JavaScript instead.

### HTML Output

The HTML builder generates `[data-theme="name"] { ... }` blocks for every registered theme in a single `<style>` tag. A flash-prevention script sets `data-theme` from `localStorage` before the first paint.

### Client-Side Switching

`switchTheme(name)`:

1. Sets `document.documentElement.setAttribute('data-theme', name)` -- CSS variables update instantly.
2. Saves to `localStorage('fw-theme')`.
3. Calls `_applyThemeToCharts()` -- updates Chart.js instances:
   - Sets `Chart.defaults.color` and `Chart.defaults.borderColor`.
   - Iterates all registered chart instances.
   - For doughnut charts: reassigns the palette array on `backgroundColor`.
   - For other charts: updates `borderColor` and `backgroundColor` on datasets marked `_themeManaged`.
   - Updates grid/tick colors on x and y scales.
   - Calls `chart.update('none')` (no animation).
4. Syncs the theme `<select>` dropdown.
5. Dispatches `window.dispatchEvent(new CustomEvent('fw-theme-change', {detail: {theme}}))`.

### Auto-Discovery

The runner calls `_discover_project_themes()` which scans a project-level `themes/` directory for Python files containing `Theme` instances. These are registered via `register_theme()` and become available in the theme dropdown.

---

## Vendor-Bundled Libraries

All JS/CSS libraries are bundled locally in `trellum/static/vendor/` instead of loaded from external CDNs. Reports are fully self-contained and work offline with no external network dependency.

`trellum/rendering/cdn.py` maintains a registry of library paths served via the `/_vendor/` URL path:

### Always Included

These are loaded on every report regardless of which components are used:

- Inter font files (replaces Google Fonts CDN)
- Chart.js v4
- Hammer.js (touch support for zoom)
- chartjs-plugin-zoom
- chartjs-plugin-annotation
- chartjs-plugin-datalabels
- html2canvas (for PNG export)
- jsPDF (for PDF export)

### Component-Requested

Components declare vendor dependencies via `cdn_deps()`. For example:

- `HeatmapChart` requests `chartjs_matrix`
- `FunnelChart` requests `chartjs_funnel`
- `TreemapChart` requests `chartjs_treemap`
- `SliderFilter` requests `nouislider_js` and `nouislider_css` (aggregated onto the page by `FilterBar.cdn_deps()`, same as every other filter plugin)
- `TabGroup` (dropdown mode) requests `slim_select_js` and `slim_select_css`

### Version Tracking

- Library versions are tracked in `trellum/static/vendor/MANIFEST.json`.
- Update instructions are in `trellum/static/vendor/UPDATING.md`.
- To add a new library: download the file to `vendor/`, add an entry to `_LIBRARIES` in `cdn.py`, and add to `MANIFEST.json`.

### Serving

- The `/_vendor/` path serves files from `trellum/static/vendor/` with 1-year immutable cache headers.
- `build_cdn_tags(keys)` merges the requested keys with `_ALWAYS_INCLUDED`, iterates in `_LIBRARIES` order (for deterministic output), and emits `<link>` for CSS/fonts and `<script>` for JS.

### Registration

- **Framework**: Library paths are defined in `_LIBRARIES` in `cdn.py`.
- **Report-level**: `extra_cdn` in `report.yaml` registers additional libraries.
- **Programmatic**: `register_cdn(name, url)` from `trellum.rendering.cdn`.

---

## Output Backends

### Local (Default)

Reports are written to `output/{slug}/` in the project root. The runner starts an HTTP server to serve the output directory.

### Production (Pluggable)

The `OutputBackend` protocol requires a single method: `publish(output_dir: str, slug: str) -> None`.

A backend can publish to any destination — object storage, a shared filesystem, SFTP. The framework ships only the local no-op backend; anything else is a project concern.

Backends are registered via `set_output_backend()`, typically in a project-level `bootstrap.py` that runs at startup.

---

## Validation Internals

### Check Pipeline

1. `validate_report(ctx, events=events)` is called after `generate()` and before `render_report()`.
2. All sections and scopes are flattened into `(component, section_title)` pairs via `_walk_components()` (recursive -- handles Grid, Panel, SplitPane, TabGroup, Visible).
3. Ten check functions run sequentially, each appending to a `ValidationResult`.
4. Checks are deduplicated by `(id, message)` tuple.
5. Suppressions from `report.yaml` `validation.suppress` remove matching check IDs.
6. Results are printed to console and written to `_validation.json`.

### Check Categories

| # | Function | What It Checks |
|---|---|---|
| 1 | `_check_datasource_filterbar` | DataSource/FilterBar wiring, orphan FilterBars, missing filter columns, propagate targets |
| 2 | `_check_dataset_id_integrity` | Components missing `dataset_id` when DataSources exist, orphan dataset_ids |
| 3 | `_check_rawhtml` | Custom JS DOM references, data_key usage, missing renderAll/destroy/theme listener |
| 4 | `_check_theme` | Invalid theme names, cached getThemeColors, hardcoded Chart.js colors |
| 5 | `_check_annotations` | Missing events.yaml, studio mismatch, non-date x-axis, custom charts without buildAnnotations |
| 6 | `_check_columns` | Missing DataFrame columns for chart x/y/y_cols, ratio keys, KPI aggregation types |
| 7 | `_check_toggle_visible` | Visible targeting non-existent Toggle, invalid toggle values, RawHTML toggle ID mismatches |
| 8 | `_check_scopes` | Empty scopes, multiple scope info |
| 9 | `_check_yaml_schema` | Missing recommended YAML fields, missing data_sources, invalid cron |
| 10 | `_check_structural` | DataSource in titled section (breaks sticky), missing date_range filter |

### Result Structure

The output is a JSON file (`_validation.json`) containing a timestamp, slug, summary counts (total, pass, info, warn, fail), and an array of individual check results (each with id, level, message, component, and section).

The summary is also merged into `_meta.json` so a consumer can display validation status without loading the full validation file.

---

## Concurrency Model

### Query Execution

`query_df()` uses a process-wide `threading.Semaphore(4)` to limit concurrent database queries. All report threads share this semaphore. Adjustable via `set_max_concurrent_queries(n)`.

### Batch Execution

`run_all_reports()` uses a `ThreadPoolExecutor(max_workers=N)` (default 3) to run reports concurrently. Each thread independently calls `run_report()`, which acquires the query semaphore for each query.

### Host applications

Scheduling and orchestration are deliberately outside this repository. A control plane runs each report as a **subprocess** (`python -m trellum.run …`), reads `schedule.cron` from `report.yaml` itself, and consumes the framework only through the surface frozen in [COMPATIBILITY.md](COMPATIBILITY.md#the-host-application-contract).

Nothing here imports such a host, and nothing here may. CI enforces it.

### Port Management

The runner automatically stops previous `trellum.run` processes on the same port. It checks `lsof -ti tcp:{port}`, identifies if the PID is a Python framework process, sends `SIGTERM`, and waits up to 3 seconds for the port to free.

---

## Auto-Discovery

The runner calls two discovery functions at startup:

### `_discover_project_themes(project_root)`

Scans `{project_root}/themes/*.py` for `Theme` instances. Each file is imported as a module, and any `Theme` instance found in its attributes is registered under the filename (e.g. `my_theme.py` → theme name `my-theme`).

### `_discover_project_components(project_root)`

Scans `{project_root}/components/*.py` and imports them. This allows projects to define custom component types without modifying the framework. The components register themselves via their `client_js()` / `css()` / `cdn_deps()` methods when used in a report.

---

## Bootstrap Lifecycle

When the runner starts:

1. `main()` in `runner.py` resolves `bootstrap.py` at the project root.
2. If it exists, it is imported and executed. This is where projects register their own plugins — a credential resolver, an output backend.
3. CLI flags are processed (`--no-cache`, `--force-cache`, `--clear-cache`, `--production`).
4. Report discovery and execution begins.

The bootstrap pattern allows the framework to remain cloud-agnostic while supporting full production deployments via project-level configuration.
