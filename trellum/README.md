<p align="center">
  <img src="https://raw.githubusercontent.com/trellumhq/trellum/main/website/static/trellum-lattice.svg" width="72" height="72" alt="Trellum">
</p>

<h1 align="center">Trellum for Python</h1>

<p align="center">
  Build interactive data reports in Python and publish them as portable HTML.
</p>

<p align="center">
  <a href="https://trellum.dev/demo/">Live demo</a> ·
  <a href="https://trellum.dev/docs/latest/framework/the-framework/">Documentation</a> ·
  <a href="https://github.com/trellumhq/trellum">Source</a> ·
  <a href="https://github.com/trellumhq/trellum/blob/main/LICENSE">AGPL-3.0-only</a>
</p>

## Install

Trellum requires Python 3.11 or newer. Install the versioned wheel from the
GitHub release:

```bash
python -m pip install https://github.com/trellumhq/trellum/releases/download/v0.1.0/trellum-0.1.0-py3-none-any.whl
```

Then install the synthetic demo and build one portable report:

```bash
python -m trellum.demo --dest trellum-demo
cd trellum-demo
python -m trellum.run reports/player-overview --no-serve --portable
```

SQLite and the included demo need only the base install. To add every optional
database, cloud-file, and cloud-storage driver:

```bash
python -m pip install "trellum[drivers] @ https://github.com/trellumhq/trellum/releases/download/v0.1.0/trellum-0.1.0-py3-none-any.whl"
```

<p align="center">
  <img src="https://raw.githubusercontent.com/trellumhq/trellum/main/docs/assets/player-overview.jpg" alt="Player Overview report with KPI cards, filters, and a daily active users chart">
  <br>
  <sub>Player Overview uses the synthetic dataset included with Trellum.</sub>
</p>

Trellum queries data in Python, turns components into HTML and JSON, and runs
filters, charts, tables, annotations, themes, and exports in the browser. The
result can be served locally or published to any static host. The framework is
host-independent and does not need the optional Trellum platform.

This file is the complete framework reference: components, the `ctx` API,
themes, data sources, browser runtime, validation, extension points, and CLI.
The [repository README](../README.md) explains the self-hosted platform.

---

## Table of Contents

- [Install](#install)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Report Structure](#report-structure)
- [Components](#components)
  - [Charts](#charts)
  - [KPIs](#kpis)
  - [Tables](#tables)
  - [Controls](#controls)
  - [Layout](#layout)
  - [Advanced](#advanced)
- [DataSource and FilterBar](#datasource-and-filterbar)
  - [Cascading filters (`depends_on`)](#cascading-filters-depends_on)
  - [ScopedDataSource — section-local filters on the same data](#scopeddatasource--section-local-filters-on-the-same-data)
  - [Chunked DataSource Loading](#chunked-datasource-loading)
- [Live Queries](#live-queries)
- [Themes](#themes)
- [Events and Annotations](#events-and-annotations)
- [Data Layer](#data-layer)
  - [Ad-hoc: a DataFrame without a report](#ad-hoc-a-dataframe-without-a-report)
- [Multi-Scope Reports](#multi-scope-reports)
- [Custom Dashboards](#custom-dashboards)
- [A/B Testing](#ab-testing)
- [JavaScript Runtime API](#javascript-runtime-api)
- [CLI Reference](#cli-reference)
- [Troubleshooting](#troubleshooting)
- [Validation](#validation)
- [Testing](#testing)
- [Deployment](#deployment)
- [Extensibility](#extensibility) — including [Extensions](#extensions)
- [Configuration Reference](#configuration-reference)
- [Licence](#licence)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Report Directory                         │
│  report.yaml ─ generator.py ─ queries.py ─ custom_sections.py  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │     Runner      │
                    │  (trellum.run)│
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───────┐ ┌───▼───┐ ┌────────▼────────┐
     │ ReportContext   │ │Themes │ │   Validation    │
     │ - dates         │ │       │ │                 │
     │ - connections   │ └───┬───┘ └────────┬────────┘
     │ - sections      │     │              │
     └────────┬───────┘     │              │
              │              │              │
     ┌────────▼──────────────▼──────────────▼───┐
     │              HTML Builder                 │
     │  Components → HTML shells + data.json     │
     │  JS Runtime + CDN deps → index.html       │
     └────────────────────┬──────────────────────┘
                          │
               ┌──────────▼──────────┐
               │   Output (local)    │
               │  index.html         │
               │  data.json          │
               │  _meta.json         │
               │  _validation.json   │
               └──────────┬──────────┘
                          │
               ┌──────────▼──────────┐
               │   Browser Client    │
               │  fetch data.json    │
               │  Chart.js render    │
               │  Filter engine      │
               │  Theme switching    │
               └─────────────────────┘
```

The framework follows a server-side generation, client-side rendering pattern:

1. **Python** queries the database, builds a component tree, and serializes everything to HTML + JSON.
2. **The browser** loads the HTML shell, fetches `data.json`, and uses Chart.js renderers to fill the DOM.
3. **Interactivity** (filters, themes, zoom, annotations) is handled entirely client-side via the embedded JS runtime.

---

## Quick Start

> **Working inside this monorepo?** The [`demo/`](demo/README.md) directory is
> a self-contained project with generated data and ten reports — no database,
> credentials, or external service required:
>
> ```powershell
> cd demo
> .\dev.ps1 setup                 # virtualenv + dependencies + fixtures
> .\dev.ps1 run player-overview   # serves on http://localhost:8050
> ```
>
> Also `.\dev.ps1 list`, `test`, `build` and `serve`. It is the fastest way to
> verify a framework change end to end — see
> [`demo/README.md`](demo/README.md).
>
> **Installed the framework standalone?** The package ships the same demo
> project as an installed module:
>
> ```bash
> mkdir my-project && cd my-project
> python -m trellum.demo            # copies reports/ + generates a SQLite warehouse
> python -m trellum.run reports/player-overview --serve
> ```
>
> `python -m trellum.demo --list` shows what would be installed without
> writing anything; `--force` overwrites files already present.

Run the commands below from the **project root**, the directory that contains
`reports/`. Use `python3` if your environment does not expose Python 3 as
`python`.

### Installing the framework

For a released standalone installation, use its versioned wheel. Python 3.11
or newer is required:

```bash
python -m pip install https://github.com/trellumhq/trellum/releases/download/v0.1.0/trellum-0.1.0-py3-none-any.whl
```

Versioned wheels are attached to their GitHub releases. Trellum is not
currently published on PyPI.

For current development, install from a checkout:

```bash
git clone https://github.com/trellumhq/trellum.git
cd trellum
python -m pip install -e ./trellum
```

Either way, create and activate a virtual environment first and use the same
interpreter for every command. The framework install contains no scheduler,
portal, JWT library, or LLM SDK.

There are two entry paths:

- **Initializing a new project** — run once when first adding this framework layout to a repository (scaffolding is committed to git).
- **Opening an existing repository** — you cloned or pulled a repo that already has `reports/`, `events.yaml`, and `.env.example`; you still need **local** credentials on each machine.

### Initializing a new project (once per repository)

Use this when you are bootstrapping a **new** BI project or an empty repo that does not yet have the standard layout.

```bash
python3 -m trellum.init
```

This creates `events.yaml`, `.env.example`, `reports/`, and related scaffolding. Commit those files to git so teammates receive them when they clone.

**If you clone an already-scaffolded repository**, you normally **skip** `trellum.init` — only run it again when fixing a broken or incomplete checkout.

### Opening an existing repository (first time on your machine)

After `git clone` or `git pull`, check that `reports/` and `.env.example` exist. If they do, **do not run `trellum.init`** unless something is missing.

Credentials are **local and not in git** (`.env` is gitignored). On each new clone or machine:

```bash
cp .env.example .env
# Edit .env and fill in database / secret values from your team
```

Get connection details from your team’s usual process (internal docs, secrets manager, etc.).

If reports fail to connect to the database after this, see [Troubleshooting](#troubleshooting).

### Create and run a single report

Use this flow once your `.env` is in place (whether you just scaffolded the repo or joined an existing one).

```bash
# 1. Create a new report
python3 -m trellum.new my-report --studio my-studio --category Revenue

# 2. Edit reports/my-report/generator.py and queries.py

# 3. Generate and open one report (HTTP server on port 8050 by default)
python3 -m trellum.run reports/my-report
# Opens at http://localhost:8050 — use --port 8070 to pick another port
```

`trellum.run` serves **one** report at a time. Output is written under `output/<slug>/` (e.g. `index.html`, `data.json`).

**No database yet?** The scaffold from `trellum.new` queries a real data
source (`vertica` by default) and will fail with a credentials error until
`data-sources/config.yaml` / `.env` point at something real. Add `--test` to
build the same report against auto-generated mock data instead — no DB, no
`.env` needed — which is the fastest way to see the report render while you
are still writing the query:

```bash
python3 -m trellum.run reports/my-report --test
```

### Browse all reports at once

`trellum.run --all --serve` builds every report and serves the whole output directory on one port, with a generated index. No extra service required.

```bash
python3 -m trellum.run --all --serve

# Optional: set port (default is 8050)
python3 -m trellum.run --all --serve --port 8060
```

---

<!-- topic: report-yaml -->
## Report Structure

Every report is a directory under `reports/` with three required files and one optional:

```
reports/{slug}/
├── report.yaml          # Metadata, schedule, data sources, display config
├── generator.py         # Python class that builds the report
├── queries.py           # SQL queries as module-level constants
└── custom_sections.py   # (optional) CSS/HTML/JS for advanced dashboards
```

- **`generator.py`** -- Subclass `BaseReport` and implement `generate(self, ctx)`. Use `ctx.get_connection()` to query data, then `ctx.add_section()` with components like `DataSource`, `FilterBar`, `LineChart`, `KpiRow`, etc. The scaffolded template from `python -m trellum.new` provides a working starting point.
- **`queries.py`** -- SQL queries as module-level string constants with `:param_name` placeholders. Referenced from the generator via `from . import queries`.
- **`custom_sections.py`** -- Optional. Contains `CUSTOM_CSS`, `CUSTOM_HTML`, and `CUSTOM_JS` string constants for advanced `RawHTML` dashboards.

### ReportContext (`ctx`)

| Property / Method | Description |
|---|---|
| `ctx.today` | Today's date as `"YYYY-MM-DD"` (UTC) |
| `ctx.yesterday` | Yesterday's date |
| `ctx.two_days_ago` | Two days ago |
| `ctx.week_ago` | Seven days ago |
| `ctx.now_utc` | Current UTC timestamp (ISO 8601) |
| `ctx.config` | The parsed `report.yaml` dict |
| `ctx.slug` | Report slug |
| `ctx.theme` | Active `Theme` instance |
| `ctx.output_dir` | Output directory path |
| `ctx.get_connection(name)` | Get or create a DB connection by name |
| `ctx.close_connections()` | Close all open connections |
| `ctx.add_section(title, components, collapsible=False)` | Add a titled section. Empty `title` = unwrapped. |
| `ctx.set_header(subtitle=None, meta=None)` | Set header subtitle or metadata |
| `ctx.set_header_component(component)` | Override the default `ReportHeader` |
| `ctx.set_scope(name, label=None)` | Activate a named scope (for multi-game reports) |
| `ctx.set_custom_output(html, data)` | Bypass the component tree with raw HTML |

---

<!-- topic: components -->
## Components

### Charts

| Component | Constructor | Description |
|---|---|---|
| `LineChart` | `(df, x, y, title, dataset_id)` | Time series. `y` can be a string or list. Supports `ratios=[{numerator, denominator, label}]` for computed ratio lines. `stack_by="col"` pivots rows by a categorical column to produce one line per value (works with both `y` and `ratios`; long-format friendly). `y_format` controls axis formatting. |
| `AreaChart` | `(df, x, y, title, stacked, dataset_id)` | Filled area chart. `stacked=True` for composition over time. |
| `BarChart` | `(df, x, y, title, dataset_id)` | Vertical or horizontal bars. `horizontal=True`, `stacked=True`, `cross_filter=True`. `ratios=[{numerator, denominator, label}]` plots sum(num)/sum(den) per x-value — combine with `horizontal=True, sort='desc'` for categorical ratio rankings (CPD by comfort_zone, ARPPU by country). |
| `StackedBar` | `(df, x, y_cols, title, dataset_id)` | Stacked bar chart. `stack_tooltip_breakdown=True` shows segment details. `value_format` controls tooltips (`"currency"` or `"number"`). `stack_by` / `stack_by_options` for dynamic grouping. `cross_filter=True` for click-to-filter. |
| `ComboChart` | `(df, x, bar_cols, line_cols, title, dataset_id)` | Dual-axis: bars on left y-axis, lines on right. `bar_format`/`line_format` control axis formatting. `stacked_bars=True`. |
| `DoughnutChart` | `(df, label, value, title, dataset_id)` | Proportional donut chart. `cross_filter=True`. |
| `HeatmapChart` | `(df, x, y, value, title, dataset_id)` | Color-coded matrix. `color_scale` accepts `"auto"`, `("lo", "hi")`, or `("lo", "mid", "hi")`. `log_scale` and `value_format` options. |
| `FunnelChart` | `(df, label, value, title, dataset_id)` | Conversion funnel. Each row is a stage. `show_percentages=True` (default). |
| `TreemapChart` | `(df, group_cols, value, title, dataset_id)` | Hierarchical treemap. `group_cols` is a list for nested grouping. Optional `color_col`. |
| `ScatterChart` | `(df, x, y, title, dataset_id)` | Scatter or bubble chart. `size` column enables bubble mode. `color_by` splits by category. |

### KPIs

| Component | Constructor | Description |
|---|---|---|
| `KpiRow` | `(kpis, dataset_id)` | Row of KPI cards. Each kpi: `{label, value, format}`. Live mode with `dataset_id` supports aggregation: `sum`, `abssum`, `ratio`, `count`, `purchase_pct`. |
| `KpiCard` | `(label, value, format)` | Single KPI card. Formats: `number`, `currency`, `chips`, `percent`, `ratio`, `plain`. Optional `delta`, `delta_direction`, `forecast`, `sub`. |
| `MiniKpi` | `(label, value, format)` | Compact inline KPI. |

### Tables

| Component | Constructor | Description |
|---|---|---|
| `DataTable` | `(df, title, dataset_id)` | Scrollable data table with CSV download. Auto-search when >20 rows. `max_rows`, `sortable`, `searchable`, `conditional_formats`, `bar_column`. |
| `PivotTable` | `(df, rows, cols, values, agg, dataset_id)` | Interactive pivot grid. Users can change dimensions and aggregation via dropdowns. `agg`: `sum`/`count`/`avg`. `value_format`. |
| `ComparisonTable` | `(df, title, value_col, compare_cols, dataset_id)` | Table with period comparison columns. |

### Controls

| Component | Constructor | Description |
|---|---|---|
| `Toggle` | `(id, options, default)` | Button group toggle. |
| `Dropdown` | `(id, options, default)` | Select dropdown. |
| `TabGroup` | `(tabs, id, mode)` | Tabbed content. Each tab: `{"label": "...", "content": [...]}`. `mode`: `"tabs"` or `"dropdown"` (uses Slim Select). |
| `DateSelector` | `(id, options)` | Day picker (Today / Yesterday / 7D). Delegates to `Toggle`. |

### Layout

| Component | Constructor | Description |
|---|---|---|
| `Section` | `(title, children, collapsible)` | Titled content block. `collapsible=True` for expand/collapse. |
| `Panel` | `(children, title)` | Card container. |
| `Grid` | `(children, columns, card)` | Multi-column CSS grid. `card=True` for card-panel styling. |
| `SplitPane` | `(left, right, ratio)` | Side-by-side panels. `ratio="1:1"`. |
| `Visible` | `(children, toggle_target, toggle_value)` | Toggle-driven visibility. Shows `children` when the specified toggle matches `toggle_value`. |

### Advanced

| Component | Constructor | Description |
|---|---|---|
| `RawHTML` | `(html, js, css, data_key, data)` | Custom CSS/HTML/JS block. Data is written to `data.json` under `data_key`. |
| `ABCompare` | `(rows, title, control_label, test_label, ...)` | A/B test comparison: volumes table, KPI delta bars vs control, optional segment tabs via `modes`, clickable rows drill down via `timeseries`. `exclude_keys` hides specific rows. |

---

<!-- topic: filters -->
## DataSource and FilterBar

Every report should include at least one `DataSource` + `FilterBar` pair for interactive data slicing.

### Rules

1. Create a `DataSource(id, df)` for each dataset.
2. Create a `FilterBar(dataset_id, df, filters=[...])` with filters for every useful dimension.
3. Every chart, KPI, and table must have `dataset_id=...` pointing to the appropriate DataSource.
4. Include a `date_range` filter as the first filter for time-series data.
5. One grain = one DataSource. Use `FilterBar(propagate_to={...})` to sync shared dimensions across DataSources with different grains.

### Two layout patterns

The framework supports two FilterBar placements; they compose.

**Main FilterBar** (default for most reports). Place in an **untitled top section**: `ctx.add_section("", [DataSource(...), FilterBar(...)])`. Sticks to the viewport top. Reaches its primary DataSource plus any DataSource listed in `propagate_to`. Use for filters shared across the whole report (date_range, audience_segment, platform, etc).

**Section-scoped FilterBar** (drill-downs). Place inside a titled section as `[DataSource(s), FilterBar, ...content]`. Sticks below the main FilterBar while its section is in view. Use for filters that only make sense inside that section (e.g. `chest_level`, `difficulty_tier` for a chest analysis section).

Constraints (validator-enforced):

- At most one main FilterBar per report.
- At most one section-scoped FilterBar per titled section.
- Section-scoped pair must be at the start of the section, before any chart/KPI/table.

A chart inside a section is filtered by the AND of its main FilterBar (if covering its DataSource) and its section FilterBar (if any) — the JS engine composes them automatically.

### Filter Types

| Type | Usage |
|---|---|
| `dropdown` | Default. Multi-select dropdown for categorical columns. |
| `toggle` | Button group for a small number of options. |
| `flag` | Checkbox-style for boolean columns. |
| `date_range` | Date range selector with presets. |
| `slider` | noUiSlider control: a continuous numeric range (`mode="range"`, default) or snapping to one distinct value (`mode="single"`) -- or, with an explicit `"values"` list, an ORDINAL slider over ordered categories instead of a numeric column. |

A slider fits a continuous numeric range (a discount %, a price, a quantity) where enumerating options as a dropdown would be absurd; a dropdown fits categorical values; a toggle fits a handful of distinct values; dates always stay on `date_range` -- never model a date column as a `slider`.

#### Ordinal sliders: sliders over ordered categories

A slider also fits ORDERED categories that carry no numeric value of their own -- a spender tier, a severity level, a size class. Give it `"values"` (the ordered list) instead of letting it derive bounds from `df[col].min()/.max()`; the column doesn't need to be numeric:

```python
{"column": "spender_tier", "label": "Spender Tier", "type": "slider",
 "values": ["non_spender", "minnow", "dolphin", "whale"],
 "labels": {"non_spender": "Non-spender", "minnow": "Minnow",
            "dolphin": "Dolphin", "whale": "Whale"},
 "mode": "range", "default_min": "minnow", "default_max": "whale"}
```

Internally the values map to integer positions so noUiSlider can drive them; pips at each position show `labels` (falling back to the raw value) if they fit the slider's width, otherwise just the two endpoints. `mode="range"` commits the CONTIGUOUS SPAN of selected categories as a list through the existing `'in'` engine mode -- exactly what a dropdown multi-select commits, so `propagate_to`, `depends_on` cascades, and URL sync all keep working unchanged. `mode="single"` commits the selected category through `equals`, same as the numeric slider's single mode.

Reach for the ordinal slider only when the categories have a real order a reader would drag through end to end. An unordered set (`platform`, `country`) stays a `dropdown` -- a slider implies an order that isn't there.

All filter specs accept an optional `"options"` key with an explicit list of values. When provided, the dropdown is populated from that list instead of reading from `df[col]`. This is useful when the filter column exists only on a secondary DataSource (e.g. a dimension that is propagated but not present in the primary DataFrame):

```python
{"column": "country_name", "label": "Country",
 "options": sorted(df_country["country_name"].dropna().unique().tolist())}
```

### Cascading filters (`depends_on`)

A dropdown filter can declare another filter on the same FilterBar as its parent. When the parent's selection changes, the child's option list narrows automatically to values present in the parent-filtered rows:

```python
FilterBar("cpd_pkg", df, filters=[
    {"column": "package_group"},
    {"column": "package_name", "depends_on": "package_group"},
])
```

- `depends_on` accepts a single column or a list of columns.
- Both filters must live on the same FilterBar and refer to real DataFrame columns. The validator checks this (`filter-depends-on-missing`, `filter-depends-on-self`).
- Currently selected values that remain valid under the parent's new selection are preserved; values that drop out are removed and the URL is rewritten.
- The child's options are derived using `getFilteredExcluding(dsId, [self_col])` so the child's own selection doesn't restrict its own options.

### Cross-DataSource Filter Propagation

When datasets have different grains (e.g., daily vs monthly), use `FilterBar(propagate_to={target_ds: {src_col: tgt_col}})` to sync shared dimensions. Setting a filter on the source dataset automatically applies the equivalent filter on the target dataset using the mapped column name.

### ScopedDataSource — section-local filters on the same data

When a section needs an extra filter dimension on data that the **main DataSource already carries** (e.g. *Price Tier* drill-down on top of an already-loaded purchase DataFrame), don't create a fresh DataSource and don't wire `propagate_to` — use `ScopedDataSource`:

```python
# Untitled top section: one base DataSource + main FilterBar
ctx.add_section("", [
    DataSource("cpd", df),
    FilterBar("cpd", df, filters=[
        {"column": "event_date", "type": "date_range"},
        {"column": "package_group"},
    ]),
])

# Drill-down with its own local filter
ctx.add_section("CPD by Price Point", [
    ScopedDataSource("cpd_pp", parent="cpd"),
    FilterBar("cpd_pp", df, filters=[
        {"column": "price_tier"},  # only affects charts in this section
    ]),
    LineChart(df=df, x="event_date", dataset_id="cpd_pp",
              stack_by="price_point_display",
              ratios=[{"numerator": "chips_allocated",
                       "denominator": "revenue", "label": "CPD"}],
              title="CPD by Price Point"),
])
```

How it works:

- **Python**: `ScopedDataSource` emits no data of its own — just a declaration that `"cpd_pp"` is a virtual child of `"cpd"`. No DataFrame duplication; `data.json` size is unchanged.
- **Client**: `fw.filterEngine.getFiltered("cpd_pp")` returns the parent's currently filtered rows further filtered by the child's local filters. Whenever the parent's filters change, all scoped children re-derive and notify their subscribers.
- **Charts**: any component using `dataset_id="cpd_pp"` reacts to the AND of (main FilterBar filters) ∧ (section FilterBar filters) automatically.

Rules (validator-enforced):

- `parent` must reference an existing base `DataSource` (`scoped-ds-parent-missing`).
- A `ScopedDataSource` cannot itself be a parent of another scoped DS (`scoped-ds-parent-not-base`).
- A `ScopedDataSource` SHOULD have a section-scoped FilterBar — otherwise it just mirrors the parent.
- Listing a `ScopedDataSource` in another FilterBar's `propagate_to` is unnecessary and emits `propagate-to-scoped-ds` (the child already inherits).

When to use `ScopedDataSource` vs. a separate `DataSource`:

- Same DataFrame, different filter scope per section → **ScopedDataSource**.
- Different grain (daily revenue vs monthly MAU) or different SQL → **separate DataSource + `propagate_to`**.

Reference: `reports/cpd-per-package/generator.py` uses two `ScopedDataSource`s, one with cascading `depends_on` filters.

### Chunked DataSource Loading

For reports with large date ranges, the `DataSource` can split a DataFrame into an inline portion (embedded in `data.json`) and a set of deferred chunk files fetched on demand as the user expands the date range.

```python
DataSource("my_ds", df, chunk_by="month", default_chunks=3)
```

| Parameter | Default | Description |
|---|---|---|
| `chunk_by` | `None` | `"month"` or `"week"`. When set, partitions the DataFrame by calendar period. `None` disables chunking (current behaviour). |
| `default_chunks` | `3` | Number of most-recent periods to embed inline in `data.json`. Older periods are written as separate files and fetched lazily. |

**How it works end-to-end:**

1. **Build time** — the runner writes the most-recent `default_chunks` periods into `data.json` as `_ds_{id}` (same as a non-chunked DataSource). Each older period is written as `data_chunk_{id}_{period}.json` alongside `data.json`. A manifest at `_chunk_manifests.{id}` in `data.json` lists every chunk file with its date range.
2. **Browser — initial load** — the filter engine initialises with the inline data only. The default date-range filter (90 days) typically falls entirely within those 3 months, so no extra fetches are needed on first paint.
3. **Browser — date range expansion** — when the user selects a wider range (or "All"), `_fwChunkLoader` detects which periods are now in range and not yet loaded, fetches the missing chunk files in parallel, and merges their rows into the filter engine. A loading banner appears while fetches are in flight.
4. **Scope switches** — when a multi-scope report switches scope, the DataSource is re-initialised with fresh 3-month inline data. `_fwChunkLoader` detects the re-initialisation, re-merges any previously fetched chunks, and triggers a re-render — so previously expanded ranges remain intact after the switch.

**Rules:**

- `chunk_by` requires a `date_range` filter to be attached to the DataSource (either directly on its own FilterBar, or via `propagate_to` from another FilterBar). The validator enforces this (`chunk-no-date-filter`).
- Chunk files are named `data_chunk_{id}_{period}.json` (e.g. `data_chunk_pf_gop3_2025-11.json`). For multi-scope reports the scope prefix is included: `data_chunk_{scope}_{id}_{period}.json`.
- Chunking is transparent to all components — charts, KPIs, and tables using `dataset_id` automatically see the merged data after each fetch.
- The `large-ds-no-chunking` validator warns when a DataSource serialises to >50 MB and `chunk_by` is not set.

---

## Live Queries

Every component covered so far computes at build time and ships its rows
baked into the artifact — that compiled default is fast, works from a
share link or an emailed copy, and needs nothing running to keep working.
A **live query** is the deliberate, rare exception: per-entity data at a
cardinality too high to bake in full. "Type a user id, see that user's raw
events" against a table with a million users does not compile — the
report declares a parameterized SQL query at build time, bakes ONE
entity's slice into the artifact as the snapshot (never the whole table),
and lets a serving host re-run the query on demand for whichever id a
viewer asks for. Use it only when a `FilterBar` over a compiled
`DataSource` genuinely cannot answer the question — aggregates and trends
almost always compile fine.

A live query is a filter-engine **dataset**, not a bespoke control:
`LiveDataSource` registers the snapshot under a `dataset_id` exactly like
`DataSource`, and an ordinary `FilterBar` drives it by naming, per filter,
the declared query param that filter's column feeds. Every chart, table
and KPI row built against that `dataset_id` reacts exactly as it would to
a compiled dataset.

```python
# generator.py
events = ctx.declare_live_query(
    "user_events",                          # id components reference
    queries.USER_EVENTS,                    # "... WHERE user_id = :user_id ..."
    datasource="demo_db",                   # any SQL source (sqlite, postgres, ...)
    params=[{"name": "user_id", "type": "int", "required": True}],
    snapshot_params={"user_id": 1042},      # executed once, at build time
)

ctx.add_section("Events", [
    LiveDataSource("events", query="user_events", df=events),
    FilterBar("events", events, filters=[
        {"type": "text", "column": "user_id", "param": "user_id",
         "label": "User ID", "placeholder": "e.g. 1042"},
    ]),
    DataTable(events, title="User events", dataset_id="events", sortable=True),
])
```

**Combine freely with compiled components.** A live dataset and a compiled
one can share a report, even a section — nothing forces an all-or-nothing
choice. `demo/reports/user-event-log` puts a live `FilterBar`-driven KPI
row, two charts and a table beside a plain compiled `LineChart(...,
static=True)` aggregating the same metric over the entire history: the
live components answer "show me this one thing, right now"; the compiled
chart answers "how does this look in aggregate" — for free, since it never
needs to change per viewer.

**How it degrades.** The built artifact stays a complete, self-contained
snapshot. Standalone — opened from disk, a share link, an email snapshot,
or served by a host that predates (or never implements) live queries — the
`FilterBar` renders server-side disabled with the label *"Live lookup —
available when served by a host; showing data from the last build"*, and
the snapshot rows stand. Nothing is fetched unless the page was built with
an `extensions.live_query_url` (the host's endpoint; `{slug}` in it is
replaced with the report's slug at build time). Two hosts implement the
fetch today: a portal that owns the report, in production, and `python -m
trellum serve`, for local development — the dev server answers the same
request shape against the project's own data sources for a report that
declared a live query, so you can exercise the live path while writing the
report rather than only after deploying it somewhere with a host.

**How it fetches.** Commits are auto-query for dropdown/toggle/date-preset
filters and on slider release; explicit (Enter/blur) for text — typing
alone never fires a query. The runtime POSTs `{"query_id": ...,
"params": {...}}` to the endpoint and feeds the response back into the
filter engine, so every component subscribed to that `dataset_id` — chart,
table, KPI row — redraws through its normal path; sorting, search and CSV
download keep working on live results. A `429` shows "busy — retrying" and
retries once after `Retry-After`.

**What ships where.** The SQL is written to `_live_queries.json` beside
`_meta.json` for the host to read. It never enters `data.json` or
`index.html` — the page carries only the query id, the param schema
(`int | float | str | date | enum`, enums with `values`, strings with
`max_length`, default 200), and the FilterBar's own snapshot rows. A
viewer can change *which* value a declared query runs with; they can never
see or influence the query text itself.

The validator holds it together: `live-query-unknown` (component
references an undeclared id — FAIL), `live-query-source-not-sql`
(datasource has no SQL driver — FAIL), `live-query-param-schema` (bad type
/ enum without values / `required: false` — FAIL), `live-query-sql-params`
(the SQL's `:name` placeholders and the declared params disagree — an
undeclared placeholder FAILs, an unused param WARNs), `live-query-no-snapshot`
(no `snapshot_params` — WARN, the control renders empty standalone),
`live-query-param-uncovered` (a declared param no filter binds — FAIL, it
could never change from its snapshot default). See the demo's
`user-event-log` report ("Live Ops Monitor") for the complete pattern,
including a second live query sharing the primary FilterBar's filters via
`propagate_to`.

---

<!-- topic: themes -->
## Themes

The framework ships with **13 built-in themes**:

| Theme | Description |
|---|---|
| `trellum dark` | Dark ground, teal header accent (**default** when `theme` is unset) |
| `trellum light` | The same identity on a light ground |
| `light` | The pre-v0.6.0 default: white background, red header |
| `dark` | The pre-v0.6.0 dark: navy background, red header |
| `money` | Deep green/gold palette |
| `blossom` | Soft pink/purple/lavender |
| `midnight` | Deep blue/navy palette |
| `sunset` | Warm orange/amber tones |
| `nord` | Arctic blue-gray palette |
| `dracula` | Dark purple/green palette |
| `solarized` | Ethan Schoonover's color scheme |
| `ocean` | Deep sea blues |
| `monokai` | Classic code editor colors |

Set the default theme in `report.yaml` with the `theme` field. Set `theme_switcher: false` to lock the report to a single theme and hide the dropdown.

Users can switch themes at runtime via the header dropdown. Their preference persists in `localStorage` across all reports.

### CSS Variables

Themes control all styling through CSS custom properties. Use `var(--name)` in CSS, never hardcode colors:

| Variable | Purpose |
|---|---|
| `--bg-main` | Page background |
| `--bg-card` | Card/panel background |
| `--bg-card-hover` | Card hover background |
| `--bg-header` | Header accent color |
| `--text-main` | Primary text color |
| `--text-secondary` | Secondary text color |
| `--text-muted` | Muted/disabled text |
| `--border-color` | Border color |
| `--accent-green` | Positive/success color |
| `--accent-red` | Negative/error color |
| `--accent-blue` | Info/link color |
| `--accent-yellow` | Warning color |
| `--shadow` | Card shadow |

### JS Theme Helpers

| Function | Returns |
|---|---|
| `getActiveTheme()` | Current theme name string |
| `getThemeColors()` | `{chart_colors, grid_color, tick_color}` for Chart.js |
| `switchTheme(name)` | Programmatically switch theme |
| `window._themes` | Full theme registry object |

Listen for the `fw-theme-change` event on `window` to react to theme switches in custom JS. The event detail contains `{theme: "theme_name"}`.

### Custom Themes

Create a `Theme` instance with all required design tokens and register it via `register_theme(name, theme)`. Alternatively, place theme files in a project-level `themes/` directory for auto-discovery by the runner.

---

## Events and Annotations

Optional chart annotations from a centralized `events.yaml` at the project root. Each event has a `date`, `label`, `type` (`release`, `campaign`, `ab_test`, `incident`, `event`), and `studio`. Events can optionally have an `end_date` for range annotations. Global `type_defaults` control which event types are visible by default.

### How It Works

1. The runner loads `events.yaml`, filters by studio and date range (last 90 days).
2. Filtered events are passed as `_events` in `data.json`.
3. Framework charts (`LineChart`, `BarChart`, etc.) show annotations automatically.
4. An **annotation toggle bar** appears below the header with clickable pills per event type.
5. Toggle state persists in `localStorage`.

### Visibility Priority (highest wins)

1. **`report.yaml`** `annotations.default_visible: [event, release]` -- per-report override
2. **Per-event** `default_visible: true/false` in `events.yaml`
3. **`type_defaults`** in `events.yaml` -- global per-type default
4. **Fallback** -- `true` (visible)

### Opting Out

Set `annotations: false` in `report.yaml` to disable annotations for a specific report. If no `events.yaml` exists, annotations are silently skipped. Nothing breaks.

---

<!-- topic: queries -->
## Data Layer

### Central Data Source Configuration

Data sources are defined in `data-sources/config.yaml` — the single source of truth for all connection details. Reports reference sources by name.

```yaml
# data-sources/config.yaml
sources:
  primary_warehouse:
    type: vertica
    description: "Primary analytics warehouse"
    host: warehouse.example.com
    port: 5433
    database: analytics
    credentials:
      local: BI_PRIMARY                # env var prefix → reads _USER, _PASS from .env
      production: bi-reports/primary   # opaque id for a registered resolver

  vip_list:
    type: file
    description: "VIP player list (uploaded by ops)"
    path: data-sources/uploads/vip-list.xlsx
    upload: true                       # marks the file as replaceable
```

Non-secret info (host, port, database, description) lives in the config file.
Credentials (user, password) live in `.env` locally and come from a registered resolver in production — never in the config.

When a portal serves the project, this file is the declaration it reads: each source appears there with these details already filled in, and its credentials are entered in the portal instead of `.env`.

### SSH tunnels

Any host/port source (`postgres`, `mysql`, `redshift`, `sqlserver`, `clickhouse`, `vertica`, `trino`) can be reached through an SSH bastion. Add `ssh_host`, and the framework connects to the bastion, forwards a local port from there to the source's own `host`/`port`, and hands the driver that local port. Everything that opens a connection -- a report build, `trellum query`, `trellum datasource add`, a portal's connection test -- takes the same path, and the tunnel closes with the connection.

```yaml
sources:
  warehouse:
    type: postgres
    host: 10.0.1.5                        # the database as the bastion sees it
    port: 5432
    database: analytics
    ssh_host: bastion.example.com
    ssh_port: 22                          # optional; this is the default
    ssh_user: analytics
    ssh_host_key: "ssh-ed25519 AAAA..."   # from `ssh-keyscan bastion.example.com`
    credentials:
      local: BI_WAREHOUSE
```

The bastion's own credential goes in `.env` under the same prefix, one of:

```
BI_WAREHOUSE_SSH_KEY_PATH=~/.ssh/id_ed25519      # a private key file
BI_WAREHOUSE_SSH_PRIVATE_KEY="-----BEGIN ..."    # or the key's text (what a portal stores)
BI_WAREHOUSE_SSH_PASSWORD=...                    # or a password; also the key's passphrase, if any
```

Every `ssh_*` field has the matching `_SSH_*` suffix (`_SSH_HOST`, `_SSH_PORT`, `_SSH_USER`, `_SSH_HOST_KEY`), so the whole tunnel may also live in `.env`. `ssh_host_key` is the bastion's public host key; with it set, a bastion presenting any other key is refused before the credential is sent. Without it, whatever key the bastion presents is accepted and a warning is logged. Needs `paramiko` (in `requirements-drivers.txt`).

Reports reference sources by name:

```yaml
# reports/my-report/report.yaml
data_sources:
  - primary_warehouse
  - vip_list
```

Full inline definitions (legacy) still work for backward compatibility:

```yaml
data_sources:
  - name: primary_warehouse
    type: vertica
    local_env: BI_PRIMARY
```

### File Data Sources

Sources with `type: file` can be read with `ctx.read_source("name")`:

```python
df = ctx.read_source("vip_list", sheet_name="Sheet1")
```

Supports `.xlsx`, `.csv`, and `.parquet` files. If `upload: true`, the file is marked as replaceable by an application that offers uploads.

### Connections

Use `ctx.get_connection("name")` in the generator. Connections are lazy-created and automatically closed after generation.

### Ad-hoc: a DataFrame without a report

Not every question is a report. For a script, a notebook, or a one-off answer, query a configured source by **name** and get a `DataFrame` back — no `reports/`, no build:

```python
from trellum import query
df = query("primary_warehouse", "SELECT day, revenue FROM fact_daily WHERE day = :day", {"day": "2026-06-15"})
print(df)
```

`sources()` lists what `data-sources/config.yaml` configures; `connect(name)` returns the raw connection for `pandas.read_sql` (close it yourself). Results go through `query_df`, so `params`, `cache_ttl` and the query cache behave exactly as in a report.

- **Root inference.** These three work from any subdirectory of the project: if no root is set (`set_project_root` / `FW_PROJECT_ROOT`) and the working directory has no `data-sources/config.yaml`, the nearest ancestor that has one becomes the root. Report builds are untouched — they still resolve the root as before.
- **Read-only.** Local file databases (`sqlite`, `duckdb`) are opened read-only from this entry point; remote warehouses rely on their own permissions.

The same route from the shell is [`python -m trellum query`](#python--m-trellum-query).

### Credential Resolvers

Resolvers are tried in priority order:

| Resolver | Priority | Source |
|---|---|---|
| `ConfigFileResolver` | 10 | INI config file (legacy path) |
| `LocalEnvResolver` | 0 | Environment variables (`PREFIX_USER`, `PREFIX_PASS`) |

For local development, copy `.env.example` to `.env` and add credentials. Connection details (host, port, database) come from `data-sources/config.yaml` — only user/password go in `.env` (using the `credentials.local` prefix declared there):

```
BI_PRIMARY_USER=your_username
BI_PRIMARY_PASS=your_password
```

### query_df

The primary data access function. Pass a connection, SQL string, and a `params` dict. Returns a pandas `DataFrame`.

Features:
- **Parameter binding**: `:param_name` placeholders are replaced with properly quoted values.
- **Caching**: Results cached for 24h by default (keyed on fully-bound SQL, so an edited query re-runs at once). Pass `cache_ttl=0` to skip, or `--no-cache` for a whole build.
- **Concurrency**: A process-wide semaphore (default 4) prevents overloading the database.
- **Decimal conversion**: `decimal.Decimal` columns are automatically converted to `float`.

### Cache Management

| Function | Purpose |
|---|---|
| `set_default_cache_ttl(seconds)` | Change global default TTL |
| `set_max_concurrent_queries(n)` | Change concurrency limit |
| `disable_cache()` | Disable caching globally |
| `enable_cache()` | Re-enable caching |
| `clear_cache()` | Delete all cached results |

CLI flags: `--no-cache`, `--clear-cache`, `--force-cache` (sets TTL to 1 year).

### Drivers

The framework ships with the built-in drivers below. Additional drivers can be registered by implementing the `ConnectionDriver` protocol (requires a `default_port` property and a `connect(conn_info)` method) and calling `register_driver(type_name, driver)`.

#### Supported Source Types

| Type | Client library | Default port | Notes |
|---|---|---|---|
| `vertica` | `vertica-python` | 5433 | Connects with `tlsmode=disable`; the default type when `type` is omitted |
| `postgres` | `psycopg2` | 5432 | |
| `mysql` | `pymysql` | 3306 | |
| `redshift` | `psycopg2` | 5439 | Subclass of the Postgres driver -- psycopg2 speaks the Redshift wire protocol, but Redshift-only SQL dialect differences aren't abstracted |
| `sqlserver` | `pymssql` | 1433 | pymssql over pyodbc to avoid a system ODBC driver dependency; `database` defaults to `"master"` |
| `clickhouse` | `clickhouse-connect` | 8443 | `secure` defaults to `true`; set `secure: false` (or the `_SECURE` env suffix) for plain-HTTP internal deployments, typically alongside port 8123 |
| `snowflake` | `snowflake-connector-python` | 443 | Falls back to `authenticator=externalbrowser` (SSO) when no password is supplied |
| `bigquery` | `google-cloud-bigquery` | n/a | No port; supports service-account file/JSON, Application Default Credentials, or an `api_endpoint` override (private endpoints/emulators) with anonymous credentials |
| `trino` | `trino` | 8080 | `catalog` / `schema` set the session defaults. A `password` turns on HTTP basic auth, which Trino only accepts over HTTPS, so `secure` defaults to on when a password is set or the port is 443; `secure: true`/`false` (or the `_SECURE` env suffix) overrides that |
| `databricks` | `databricks-sql-connector` | 443 (fixed) | Needs `http_path` (the SQL warehouse's HTTP path) and `access_token` (a personal access token) -- the `_HTTP_PATH` / `_ACCESS_TOKEN` env suffixes -- plus optional `catalog` / `schema`. String literals are backslash-escaped, as for BigQuery |
| `sqlite` | stdlib `sqlite3` | n/a | No port; inline `path` short-circuits credential resolution |
| `duckdb` | `duckdb` | n/a | No port; inline `path` short-circuits credential resolution, `:memory:` supported |
| `file` | `pandas` (+ `pyarrow`, `fsspec`/`s3fs`/`gcsfs`) | n/a | `.xlsx`, `.csv`, `.parquet`; supports `s3://` / `gs://` paths |
| `google_sheets` | `gspread` | n/a | Service account inline (`credentials_json` / the `_CREDENTIALS_JSON` env suffix, a JSON string), else a key file (`credentials_path` / `_CREDENTIALS_PATH`), else ADC |
| `onedrive` | Microsoft Graph API (`msal`) | n/a | App-only or delegated auth depending on which credentials are set |

---

## Multi-Scope Reports

For dashboards that serve multiple games or views (e.g., GoP3 and Monopoly), use `ctx.set_scope(name, label)` to define scopes. Within each scope, add DataSources, components, and sections as usual.

The framework renders a scope toggle in the header. Each scope gets its own data file (`data_{scope}.json`) and component set. The browser loads only the active scope's data.

---

## Custom Dashboards

For visualizations beyond what framework components offer, use `RawHTML` with a `custom_sections.py` file. This file contains three string constants: `CUSTOM_CSS`, `CUSTOM_HTML`, and `CUSTOM_JS`. The generator imports them and passes them to `RawHTML(css=..., html=..., js=..., data={...})`.

### Custom JS Rules

- Always destroy chart instances before re-creating: `if (_charts.x) _charts.x.destroy();`
- Call `getThemeColors()` at render time (inside functions), never at module init.
- Listen for `fw-theme-change` and re-render all charts.
- Use framework formatters (`fmtCompact`, `fmtCompact$`, etc.) -- do not re-implement them.
- For charts with manual legends, disable the global plugin: `plugins: { fwLegend: { display: false } }`
- **Patch BOTH `window._initToggleVis` and `window.renderAll`** to re-render — see "Lifecycle hooks" below. Patching only `renderAll` is a common bug: the chart renders fine after a toggle click but is blank on initial load and on URL preload, because the framework calls `_renderComps()` directly on first load (not `renderAll`).
- **Defer renders triggered from these hooks with `requestAnimationFrame`** — `_updateToggleVis` flips `display` synchronously, and your render code runs before the layout pass. Without `rAF`, Chart.js measures the canvas as 0×0 and the chart is blank even though the parent is now visible.

### Lifecycle hooks

Custom JS that creates Chart.js instances inside a `Visible` (or any toggle-driven container) needs to react to four distinct events. Each is caught by a different hook:

| Hook | Catches | Skip it and... |
|---|---|---|
| `fw.filterEngine.subscribe(dsId, id, fn)` | filter changes | chart never updates when user filters |
| `window.addEventListener('fw-theme-change', ...)` | theme switches | chart keeps the old palette |
| `window._initToggleVis = (function(prev){ return function(){ if(prev) prev(); requestAnimationFrame(myRefresh); }; })(window._initToggleVis)` | **initial page load + URL preload** | chart is blank on first load — only renders after a toggle click |
| `window.renderAll = (function(prev){ return function(){ if(prev) prev(); requestAnimationFrame(myRefresh); }; })(window.renderAll)` | scope switches, auto-refresh, theme apply | chart goes stale after global re-renders |

Why `_initToggleVis` is necessary: the framework's data-loader calls `_renderComps(comps)` directly on first load (then `_initToggleVis()` to wire up Visibles) — it does **not** call `renderAll()`. `renderAll` is reserved for scope-switch / theme / auto-refresh paths. A common mistake is to patch only `renderAll`; the chart works fine on subsequent navigation but is blank on cold load and on URL-preloaded state.

Why `requestAnimationFrame`: `_updateToggleVis` sets `el.style.display = ''` synchronously; your render runs in the same tick before the browser performs layout, so `canvas.offsetWidth` is still 0. Deferring one frame lets layout reflow first.

---

## A/B Testing

The `ABCompare` component is the rendering surface for A/B test reports. For tests on heavy-tailed metrics (revenue / ARPDAU / ARPPU), point-estimate deltas are dominated by whale noise — a +20% headline can easily flip to -10% with one or two extra whales in either arm. The `trellum.stats` package provides three composable techniques to surface the real signal, and `ABCompare` renders their output natively.

### Variance reduction at a glance

| Technique | What it does | Variance ↓ | Where to use |
|---|---|---:|---|
| **Winsorization** (`stats.ab.winsorize_user_df`) | Caps each user's metric at the pooled p99 — same cap both arms | 10–30% | Always — quick win, replaces "Excl. Top X%" patterns |
| **Bootstrap CIs** (`stats.ab.bootstrap_ab_cis`) | Resamples users with replacement, recomputes the delta, takes 2.5–97.5 quantiles | n/a (visualises uncertainty) | Always — needed to tell signal from noise |
| **CUPED** (`stats.ab.cuped_user_df`) | Subtracts the part of the outcome predictable from a pre-experiment covariate | 30–50% | Whenever you have ≥1–2 weeks of pre-experiment user data; revenue metrics benefit most |

### Pattern: variance-reduction modes

Use `ABCompare.modes` to expose raw, winsor, and CUPED views side-by-side. Default to the CUPED mode for the subpopulation the test actually affects.

```python
from trellum.stats import bootstrap_ab_cis, cuped_user_df, winsorize_user_df

# df_user: per-user totals for the test window (one row per active user).
# Must have at minimum: user_id, variant, active_days, gross_revenue, is_payer.
# For CUPED, also: pre_gross_revenue (totals over the 14 days before test_start).

modes = {}

# 1. Raw mode — backstop / sanity check.
c_raw = _agg_from_users(df_user[df_user.variant == "Control"], n_days)
t_raw = _agg_from_users(df_user[df_user.variant == "Test"],    n_days)
modes["all_users"] = {
    "label": "All users",
    "rows": build_rows(c_raw, t_raw, cis=bootstrap_ab_cis(df_user)),
    "timeseries": {},
}

# 2. Winsor — same cap both arms at pooled p99.
df_w, _ = winsorize_user_df(df_user, pct=99.0)
modes["all_users_winsor"] = {
    "label": "All users · Winsor p99",
    "rows": build_rows(
        _agg_from_users(df_w[df_w.variant == "Control"], n_days),
        _agg_from_users(df_w[df_w.variant == "Test"],    n_days),
        cis=bootstrap_ab_cis(df_w),
    ),
    "timeseries": {},
}

# 3. CUPED — pre-experiment covariate adjustment.
df_c, info = cuped_user_df(df_user)
modes["all_users_cuped"] = {
    "label": "All users · CUPED",
    "rows": build_rows(
        _agg_from_users(df_c[df_c.variant == "Control"], n_days),
        _agg_from_users(df_c[df_c.variant == "Test"],    n_days),
        cis=bootstrap_ab_cis(df_c),
    ),
    "timeseries": {},
}
# info["variance_reduction_pct"]: expected R² × 100, ≈ 30–50% for revenue.

return ABCompare(
    rows=modes["all_users_cuped"]["rows"],
    modes=modes,
    default_mode="all_users_cuped",   # CUPED-adjusted opens first
    ...
)
```

### How CIs render

When a row in `rows` (or any `modes[*]["rows"]`) carries `ci_pct=[lo, hi]`:

- The KPI cell shows `95% CI [lo, hi]` (10px, italic) under the delta percentage.
- If `lo ≤ 0 ≤ hi` (interval straddles zero), the bar renders **neutral grey** and the CI italicises — signalling "not statistically significant".
- One-sided intervals keep the standard green (`up`) / red (`down`) bar colour.

This makes the *width* of the CI the primary visual carrier of uncertainty: a tight ±2% CI shows a confident small effect, a wide ±30% CI shows you don't yet have enough data to call.

### Picking the pre-experiment window for CUPED

- **1–2 weeks** is optimal per Microsoft / Statsig guidance. Shorter windows don't capture enough behaviour; longer windows add noise from churned users / seasonality.
- The window must end **strictly before** the test starts — covariates must not be influenced by the treatment.
- New users (zero pre-experiment data) get `θ * (X - mean(X)) = θ * (0 - mean(X))` ≠ 0 in their adjusted outcome — CUPED still applies, but the variance reduction comes from returning users where prior spend predicts experiment spend.

### Two-stage decomposition

ARPDAU = `Conversion × ARPPU`. Showing them separately makes it obvious whether a revenue lift comes from more payers (Conversion) or bigger baskets (ARPPU). The CUPED / winsor row builders typically emit both decomposition rows alongside the headline ARPDAU.

### Canonical example

`reports/local-currency-ab/generator.py` — uses all three techniques, exposes nine modes (3 populations × {raw, winsor, CUPED}), and defaults to `local_eligible_cuped`.

### What NOT to do

- **Don't compute the winsor threshold per-variant.** That compares two different truncations and biases the comparison. `winsorize_user_df` uses the pooled threshold automatically.
- **Don't CUPED-adjust the payer indicator.** CUPED is for continuous metrics. The `is_payer` column should be preserved from raw revenue *before* any winsor / CUPED transformation; pass it untouched through aggregation, and the report's payer count stays correct.
- **Don't naive-bootstrap heavy-tailed data without first winsorising.** Bootstrap means break on infinite-variance distributions. The `bootstrap_ab_cis` helper is safe on winsorised input; on raw heavy-tailed input it can produce CIs that look reasonable but are subtly wrong.
- **Don't use `Excl. Top X%` modes anymore.** They drop the user entirely *and* use per-variant cutoffs — strictly inferior to winsorization. Replace with `*_winsor` modes.

---

## JavaScript Runtime API

The framework embeds a JS runtime in every report. All public APIs are accessible via the `window.fw` namespace.

### Formatters

| Function | Signature | Example |
|---|---|---|
| `fw.fmtCompact(n)` | `number -> string` | `1234567` -> `"1.2M"` |
| `fw['fmtCompact$'](n)` | `number -> string` | `1234567` -> `"$1.2M"` |
| `fw['fmt$'](n)` | `number -> string` | `4.5` -> `"$4.50"` |
| `fw.fmtChips(n)` | `number -> string` | `1234567` -> `"1.2M chips"` |
| `fw.fmtPercent(n)` | `number -> string` | `0.053` -> `"5.30%"` |
| `fw.getFormatter(name)` | `string -> function` | `"currency"` -> `fmtCompact$` |

### Theme

| Function | Description |
|---|---|
| `fw.getActiveTheme()` | Current theme name |
| `fw.getThemeColors()` | `{chart_colors, grid_color, tick_color}` |
| `fw.switchTheme(name)` | Switch theme programmatically |
| `fw.themes` | Full theme registry object |

### Filter Engine

| Method | Description |
|---|---|
| `fw.filterEngine.initColumnar(dsId, data)` | Initialize dataset (`_cols/_data` columnar payload; row arrays auto-normalized) |
| `fw.filterEngine.setFilter(dsId, filterId, column, mode, value)` | Set a filter (modes: `equals`, `in`, `range`, `flag`) |
| `fw.filterEngine.getFiltered(dsId)` | Get current filtered rows |
| `fw.filterEngine.getFilteredExcluding(dsId, columns)` | Get filtered rows while ignoring specific filter columns |
| `fw.filterEngine.getFilterState(dsId)` | Read-only snapshot of active filters |
| `fw.filterEngine.getDatasetIds()` | List initialized dataset IDs |
| `fw.filterEngine.getDatasetRowCount(dsId)` | Return row count for dataset |
| `fw.filterEngine.isReady(dsId)` | Dataset initialized and ready |
| `fw.filterEngine.onReady(dsId, fn)` | Fires `fn()` once `dsId` is ready. Replaces fragile `setTimeout(init,100)` polling — preferred ready-detection in custom RawHTML. |
| `fw.filterEngine.addScopedChild(childId, parentId)` | Register a virtual child dataset derived from `parentId`. Normally called automatically by `ScopedDataSource`; only invoke directly from custom JS. |
| `fw.filterEngine.clearFilter(dsId, filterId)` | Remove a single filter and notify subscribers |
| `fw.filterEngine.clearAllFilters(dsId)` | Remove all filters for dataset and notify subscribers |
| `fw.filterEngine.removeDataset(dsId)` | Remove dataset state/subscribers from engine |
| `fw.filterEngine.subscribe(dsId, id, fn)` | Subscribe to filter changes |
| `fw.filterEngine.notify(dsId)` | Trigger subscriber callbacks |

### Aggregation

| Function | Description |
|---|---|
| `fw.aggregate.sum(rows, col)` | Sum a column |
| `fw.aggregate.sumCols(rows, cols)` | Sum multiple columns |
| `fw.aggregate.absSum(rows, col)` | Sum of absolute values |
| `fw.aggregate.groupBy(rows, x, vals)` | Group by column, aggregate values |
| `fw.aggregate.pivot(rows, x, stack, vals)` | Pivot data for stacked charts |

### Annotations

| Function | Description |
|---|---|
| `fw.buildAnnotations(events, labels)` | Convert events array to Chart.js annotation config |

### Export

| Function | Description |
|---|---|
| `fw.exportPNG()` | Capture report as 2x PNG and trigger download |
| `fw.exportPDF()` | Capture report as PNG, convert to single-page PDF |

### Other

| Property | Description |
|---|---|
| `fw.chartInstances` | All Chart.js instances keyed by canvas ID |
| `fw.registerChart(id, chart)` | Manually register a chart instance |
| `fw.liveWrap(id, cfg, drawFn)` | Subscribe `drawFn` to filter engine and call with current rows |
| `fw.urlSync` | URL state sync utilities |
| `fw.data` | The loaded `data.json` payload |
| `fw.events` | Shortcut for `_events` from report data |
| `fw.currentScope` | Active scope key for multi-scope reports |
| `fw.downloadCsv(columns, rows, filename)` | Trigger CSV download |

### Chart.js Plugins (loaded globally)

| Plugin | Purpose |
|---|---|
| `chartjs-plugin-zoom` | Drag-to-zoom and pan on time series |
| `chartjs-plugin-annotation` | Event markers, goal lines, reference lines |
| `chartjs-plugin-datalabels` | Value labels on bars/points/segments (disabled by default, enable per-chart) |
| `fwLegend` | Standardized HTML legend buttons above multi-dataset charts (automatic) |

### Events

| Event | Detail | Description |
|---|---|---|
| `fw-theme-change` | `{theme: string}` | Fired on `window` when theme switches |

### Lifecycle hooks (patchable)

Wrap with `(function(prev){ return function(){ if(prev) prev(); /* your code */ }; })(window.X)` to extend rather than replace.

| Hook | When Called | Catches |
|---|---|---|
| `window._initToggleVis()` | Initial page load (after `_renderComps`) AND from `renderAll` | First-load + URL-preloaded state for charts inside `Visible` |
| `window.renderAll()` | Scope switch, auto-refresh, theme apply | Global re-renders |

Custom JS that creates Chart.js instances inside a `Visible` must patch BOTH and defer with `requestAnimationFrame` (see "Lifecycle hooks" under Custom Dashboards for the why).

### Optional Hooks

| Hook | When Called |
|---|---|
| `window.onScopeChange(scope)` | After scope toggle or auto-refresh |
| `window.onToggleChange(toggleId, activeLabel)` | After any non-scope toggle click |

---

## CLI Reference

### `python -m trellum.run`

Run a single report or all reports.

| Argument | Description |
|---|---|
| `report_dir` | Path to the report directory (e.g. `reports/my-report`) |
| `--all` | Run all discovered reports |
| `--max-concurrent N` | Max concurrent reports for `--all` (default: 3) |
| `--studio STUDIO` | Filter by studio when using `--all` |
| `--category CAT` | Filter by category when using `--all` |
| `--serve` | Start HTTP server after generating (default behavior) |
| `--no-serve` | Generate output only, no HTTP server |
| `--auto-refresh` | Re-run report on its cron schedule while serving |
| `--port PORT` | HTTP server port (default: 8050) |
| `--production` | Publish via registered output backend; disables cache |
| `--output DIR` / `-o DIR` | Override output directory |
| `--no-cache` | Bypass query cache |
| `--clear-cache` | Delete all cached query results |
| `--force-cache` | Aggressive cache reuse (TTL set to 1 year) |
| `--skip-fresh [SEC]` | Skip reports with fresh output (default: 300s) |

**Testing flags:**

| Argument | Description |
|---|---|
| `--test` | Test with mock data (no DB needed) |
| `--test-screenshot` | Include Playwright screenshots |
| `--test-output DIR` | Custom test output directory |
| `--fail-fast` | Stop on first failure |
| `--save-baseline` | Save screenshots as visual regression baselines |
| `--check-baseline` | Compare against saved baselines |
| `--baseline-threshold N` | Max % changed pixels (default: 5.0) |
| `-v` / `--verbose` | Verbose output |

### `python -m trellum query`

Ad-hoc SQL against a configured data source, addressed by **name** — never by
file path, so the same command works when the source is a local SQLite file
or a remote warehouse. SQLite sources are opened read-only.

| Argument | Description |
|---|---|
| `sql` | The query; `:key` placeholders bind via `--param` |
| `--source NAME` | Source name (default: the only configured one) |
| `--param KEY=VALUE` | Bind a `:key` placeholder (repeatable) |
| `--max-rows N` | Rows to print (default 50) |
| `--csv` | Emit complete CSV for piping instead of a table |

### `python -m trellum serve`

Serve every built report under `output/` (the same gallery index the
published site uses). `--background` starts the server detached, prints the
URLs, and returns immediately — the intended last action of a working
session, so the user always ends up with a clickable link. Idempotent:
re-running reuses a server already up for this output directory.

| Argument | Description |
|---|---|
| `--background` | Detach, print the link, return |
| `--port N` | Preferred port (steps to a free one if busy) |

### `python -m trellum review`

The live review loop: the served report gains a click-to-select overlay, the
user queues per-element change requests plus an optional chat message and
sends the batch to the coding agent, which resolves it, rebuilds, and replies
— the browser reloads itself after every rebuild. Endpoints live under
`/_fw/review/` on the normal dev server (loopback-only); the on-disk HTML is
never modified — the overlay is injected into the served copy only.

| Verb | Description |
|---|---|
| `start <report>` | Build if needed, serve, enable review, open the browser (`--rebuild`, `--no-browser`, `--port N`, `--restart-server`) |
| `poll` | Block until feedback; exit 0 feedback / 2 `--timeout` / 3 ended (`--reply TEXT`, `--report SLUG` to scope to one report when several agents share the project, `--json`) |
| `reply TEXT` | Answer into the browser's chat (`--report SLUG` to reach one report's pages only) |
| `status` | One-line session state (`--json`) |
| `end` | End the session from the agent's side |

### `python -m trellum.new`

Create a new report from a template.

| Argument | Description |
|---|---|
| `name` | Report name (used for slug) |
| `--studio STUDIO` | Studio/tenant identifier (default: `default`). Whatever grouping label your project uses to tag reports. |
| `--category CAT` | Category used to group reports (default: `Uncategorized`) |

### `python -m trellum.init`

Initialize project scaffolding (creates `reports/`, `events.yaml`, `.env.example`).

| Argument | Description |
|---|---|
| `target` | Target directory (default: project root) |

---


## Troubleshooting

### Database connection errors or missing credentials

Ensure `.env` exists in the project root (`cp .env.example .env`) and is filled in for each machine; it is gitignored and not shared via git. Variable names must match the `local_env` prefix in each report’s `report.yaml` (see [Data Layer](#data-layer) and `.env.example`).

### I forgot the URL or port

- Read the line printed when the server starts.
- **Single report** (`trellum.run reports/...`): default port **8050** unless you passed `--port`.
- **All reports** (`trellum.run --all --serve`): same port, with a generated index at `/`.
- If the port is taken, pass a different `--port` and use the new URL.

### Opening reports from disk (`file://`) does not work

Reports load `data.json` via HTTP. Always use the URL printed by `trellum.run`, not `File → Open` in the browser.

### Reaching the dev server from another device (LAN)

It binds to all local interfaces (`0.0.0.0`). Use your machine's LAN IP and the **same port** as in the startup message, e.g. `http://192.168.1.10:8050`. On macOS you can get a typical Wi-Fi address with `ipconfig getifaddr en0` (interface names vary).

---

<!-- topic: validation -->
## Validation

Every report generation automatically runs a post-generation validator that checks structural invariants AND a runtime diagnostic that builds a per-chart × per-filter coverage matrix. Results are printed to the console, written to `_validation.json` + `_details.json`, embedded in `_meta.json`, and available to whatever displays the report.

### Severity Levels

| Level | Meaning | Action |
|---|---|---|
| **FAIL** | Structural bug that will cause broken rendering or incorrect data | Must fix |
| **WARN** | Likely mistake that degrades quality or causes subtle issues | Should fix or accept via report.yaml |
| **INFO** | Informational note about something unusual but potentially intentional | No action required |
| **SUPPRESSED** | Real issue that has been deliberately accepted via report.yaml | Visible in the drawer's Suppressed group; revisit if data shape changes |

### Check Categories

| Category | Key Check IDs |
|---|---|
| DataSource / FilterBar wiring | `duplicate-datasource-id`, `ds-no-filterbar`, `filterbar-orphan`, `filter-column-missing`, `filter-slider-not-numeric`, `filter-slider-value-unused`, `propagate-target-missing`, `single-main-filterbar`, `single-filterbar-per-section`, `filterbar-coverage` |
| Component dataset_id | `component-missing-dataset-id`, `component-orphan-dataset-id` |
| Per-chart filter coverage | `chart-filter-coverage`, `chart-value-grain-mismatch`, `filterbar-incomplete-propagation` |
| RawHTML DOM consistency | `rawhtml-canvas-mismatch`, `rawhtml-data-key-unused`, `rawhtml-missing-renderall`, `rawhtml-no-theme-change-handler`, `rawhtml-no-chart-destroy`, `rawhtml-chart-missing-init-toggle-vis`, `rawhtml-chart-missing-raf-defer`, `rawhtml-reimpl-utils` |
| Theme compatibility | `theme-invalid`, `rawhtml-cached-theme-colors`, `rawhtml-hardcoded-colors` |
| Annotation support | `annotations-enabled-no-events`, `annotations-studio-mismatch`, `annotations-non-date-x-axis` |
| Column validation | `chart-column-missing`, `ratios-without-dataset-id`, `ratios-missing-keys`, `ratios-column-missing`, `stackedbar-option-column-missing` |
| Toggle / Visible wiring | `visible-target-missing`, `visible-value-missing`, `toggle-unused`, `rawhtml-toggle-id-mismatch` |
| Scope system | `scope-empty`, `scope-no-default` |
| YAML schema | `yaml-missing-required`, `yaml-missing-data-sources`, `yaml-cron-invalid`, `report-description-weak` |
| Structural conventions | `datasource-not-in-untitled-section`, `missing-date-range-filter` |
| Chunked DataSource | `chunk-no-date-filter`, `large-ds-no-chunking` |
| ScopedDataSource | `scoped-ds-parent-missing`, `scoped-ds-parent-not-base`, `propagate-to-scoped-ds` |
| Cascading filters | `filter-depends-on-missing`, `filter-depends-on-self` |
| Custom RawHTML deviations | `rawhtml-custom-slimselect`, `rawhtml-filterengine-polling`, `rawhtml-custom-kpi` |
| Hardcoded hex colors (FAIL) | `rawhtml-hardcoded-hex` — any `#RGB`/`#RRGGBB` literal in `RawHTML.js` or `RawHTML.html`. `#fff` / `#000` whitelisted (text-on-color overlays). Use `fw.getThemeColors().chart_colors[i]` for chart palettes, CSS vars (`var(--accent-red)`, `var(--text-main)`, `var(--bg-card)`) for HTML/CSS. Suppress in `report.yaml` only when a color genuinely must not theme. |
| Hardcoded rgba colors (WARN) | `rawhtml-hardcoded-rgba` — `rgba?(r,g,b,...)` / `rgb(r,g,b)` literals in `RawHTML.js` (HTML/CSS are not checked). `rgba(0,0,0,0)` exempt (transparent placeholder). Fix: `fw.getThemeColors().grid_color` for grid, `.tick_color` for axis ticks / legend, `.chart_colors[i]` for datasets. Suppress for intentional fixed-color semantic annotation markers. |
| CSS var string in Chart.js (WARN) | `rawhtml-css-var-in-chartjs` — `color: 'var(--...'` (or `borderColor`/`backgroundColor`) passed to Chart.js. Chart.js has no CSS resolver — the string is treated as an invalid color. Use `fw.getThemeColors().tick_color` / `.grid_color`, or `getComputedStyle(document.documentElement).getPropertyValue('--name').trim()`. |

### Per-chart filter coverage (the diagnostic)

`chart-filter-coverage` walks every chart's `dataset_id` and asks: does every FilterBar filter actually reach this chart? It catches the silent class of bug where users see filter dropdowns that change nothing on certain charts. Underlying causes:

- **Wide-pivot bug** — the dimension was baked into column names (`dau_ios`, `dau_android` instead of `platform = "ios"`). Refactor to long format; see `Long format vs wide format` in `AGENTS.md`.
- **Missing propagate_to** — the dimension exists on the target DataSource but the FilterBar's `propagate_to` map omits it. One-line fix.
- **SQL dropped the dim** — the SQL collapsed the dimension before the DataFrame reached the DataSource. Widen the SQL or accept (see suppression below).
- **Grain mismatch** — `chart-value-grain-mismatch` fires when a chart's value column is constant within each x-axis value (e.g. a daily total merged by `event_date` only into a date × audience × spender grain). Filters reach the DS structurally but the chart's plotted value never changes. Fix by extracting the column to its own DataSource at native grain.

### Suppression hierarchy (3 layers, broadest → narrowest)

```yaml
validation:
  # 1. Global — silence every instance of these check IDs
  suppress:
    - some-check-id

  # 2. Per-DataSource — silence these check IDs on a specific DS
  suppress_per_dataset:
    my_ds:
      - chart-filter-coverage   # blanket: ALL filters on my_ds

  # 3. Per-(DataSource, filter_column) — granular and PREFERRED for chart-filter-coverage
  accept_inactive_filters:
    my_ds:
      - template_type           # only this filter is accepted as inactive
      - chest_tier              # any future inactive filter still fires
```

Suppressed checks are not removed from the result — they're flagged with `suppressed: true` and grouped under **Suppressed** with rationale text. The matrix renders suppressed inactive cells as `✗ⓢ` (italicized, gray) so a reviewer can audit the choices later.

Prefer `accept_inactive_filters` (per-filter) over `suppress_per_dataset` (per-DS) when the dim list is finite and known — future drift then still triggers an active warning.

### Details JSON output

Each run also writes `_details.json` (and embeds the same data in `_meta.json` under the `details` key):

```json
{
  "data_source": "real",            // "real" | "mock" — flagged when run via --test
  "totals": {
    "datasource_count": 12,
    "chart_count": 74,
    "total_rows": 1209776,
    "total_size_bytes": 35122610   // actual on-disk size of data.json + chunk files
  },
  "datasets": [{                    // per-DataSource metrics
    "id": "pf_gop3", "rows": 42900, "columns": 17,
    "size_bytes_inline": 1024000, "size_bytes_chunks_total": 4338000,
    "chunk_by": "month", "chunk_count": 13, "scope": "gop3"
  }, ...],
  "filter_matrix": [{               // per-chart × per-filter coverage
    "chart_id": "...", "chart_title": "ARPDAU by Platform",
    "chart_kind": "LineChart", "section": "Monetization",
    "scope": "gop3", "dataset_id": "pf_gop3",
    "filters": [
      {"column": "event_date", "target_column": "event_date",
       "status": "active", "reason": "..."},
      {"column": "platform", "target_column": "platform",
       "status": "inactive", "reason": "...", "suppressed": true}
    ],
    "static_value_columns": []      // grain-mismatch detection
  }, ...],
  "coverage_by_dataset": {...}     // collapsed per-DS view used by in-report badges
}
```

The same data surfaces as in-report admin badges at the top-left of each affected chart. They are diagnostic, not for end users, so they stay hidden unless the viewer is known to be an admin. With no [extensions](#extensions) configured there is no host to ask and no badge; add `?health=1` to the URL to force them on, which is the documented path for local development and direct `file://` access. Any host application that configures extensions is asked over `/api/auth/me` and must return an `admin` role for the badges to appear.

### Stale chunk garbage collection

Every successful render calls `_gc_stale_chunks(output_dir, canonical_filenames)` after writing the chunk set. Files matching `data_chunk_*.json` that aren't in this run's pending set are deleted. This stops `output/<slug>/` from growing indefinitely as date ranges shift, DataSources are renamed, or refactors change shape. Test mode (`--test`) opts out so a mock run never deletes real-data chunks.

---

## Testing

The framework has a five-layer test suite.

### Layer 0: Report Integration Tests (mock data)

Generates every report with auto-generated mock data (no DB needed). Validates generation, HTML/JSON output, and structural validation.

```bash
python -m trellum.run reports/my-report --test
python -m trellum.run --all --test
python -m trellum.run --all --test --test-screenshot
python -m trellum.run --all --test --fail-fast -v
```

Mock data is generated by parsing SQL SELECT clauses to infer column names and types.

### Layer 1: Component Unit Tests

Tests every `Component` subclass in isolation.

```bash
python3 -m pytest trellum/testing/test_components.py -v
```

### Layer 2: JS Runtime Unit Tests

Tests JavaScript functions using Playwright's `page.evaluate()` in a real browser.

```bash
python3 -m pytest trellum/testing/test_js_runtime.py -v
```

Covers formatters, filter engine, aggregation, themes, annotations, and the `fw` namespace.

### Layer 3: Browser Interaction Tests

Generates reports with mock data and tests real user interactions in Playwright.

```bash
python3 -m pytest trellum/testing/test_interactions.py -v
```

Covers initial load, theme switching, filter operations, chart integrity, and UI stability.

### Layer 4: Visual Regression

Saves and compares screenshots against baselines using pixel-by-pixel diffing.

```bash
python -m trellum.run --all --test --test-screenshot --save-baseline
python -m trellum.run --all --test --test-screenshot --check-baseline
python -m trellum.run --all --test --test-screenshot --check-baseline --baseline-threshold 3.0
```

### Prerequisites

- **Layers 0-1**: No extra dependencies
- **Layers 2-4**: `pip install playwright && python3 -m playwright install chromium`
- **Layer 4 (diffing)**: `pip install Pillow`

### When to Run

| Change Type | Run |
|---|---|
| Report generator changes | `python -m trellum.run reports/<slug> --test` |
| Framework component changes | `pytest trellum/testing/test_components.py` |
| Framework JS runtime changes | `pytest trellum/testing/test_js_runtime.py` |
| Theme or rendering changes | `pytest trellum/testing/test_interactions.py` |
| Any framework change | All of the above + `--all --test` |

---

## Deployment

### Local Development

Reports are served automatically on `http://localhost:8050`. The server handles port conflicts by stopping previous `trellum.run` processes on the same port.

### Production Mode

```bash
python -m trellum.run reports/my-report --production
```

Production mode:
- Disables query caching.
- Publishes output via the registered output backend.
- Skips the HTTP server.

### Output Backends

Register a backend in a project-level `bootstrap.py` using `set_output_backend()` — any object with a `publish(output_dir, slug)` method. The `bootstrap.py` file is automatically executed by the runner on startup.

---

## Extensibility

### Extensions

The framework emits no markup that assumes anything is serving the report. An embedding application — a wiki, an internal shell, a documentation site — injects its own through the `extensions` block, and that is the only supported way in.

```yaml
# config.yaml, at the project root
extensions:
  nav_html: '<a class="fw-back-link" href="/">Home</a>'   # into the header nav group
  scripts:                                                # <script src> before </body>
    - /assets/support-widget.js
```

Resolution order, first match wins:

1. `FW_EXTENSIONS_JSON` — the same object as a JSON string in the environment. This is how a parent process passes extensions to a report subprocess it spawns, so nothing has to be written to disk.
2. the `extensions:` block in the project-root `config.yaml`
3. nothing — the framework renders no extension markup at all

With no extensions configured, a generated report is fully self-contained: no navigation to a page that may not exist, and no requests to an API that is not there. That is the default, and the demo project relies on it.

`nav_html` is injected verbatim. It is your markup in your page — the framework does not escape it, and you should not build it from untrusted input. The framework's own header CSS (`.fw-nav-group`, `.fw-back-link`) is always present, so a link styled with those classes matches the header without shipping a stylesheet.

### Custom Components

Subclass `Component` and implement `render_html()`, `css()`, `client_js()`, and optionally `cdn_deps()`. Place in a project-level `components/` directory for auto-discovery.

### Custom Themes

Create a `Theme` instance and place in a project-level `themes/` directory. Auto-discovered by the runner.

### Custom Drivers

Implement the `ConnectionDriver` protocol and call `register_driver(type_name, driver)`.

### Custom Resolvers

Implement the `CredentialResolver` protocol and call `register_resolver(resolver, priority)`.

### Extra CDN Libraries

Add per-report CDN dependencies via the `extra_cdn` field in `report.yaml`, or register globally using `register_cdn(name, url)` from `trellum.rendering.cdn`.

### Custom Headers

Subclass `ReportHeader` and pass to `ctx.set_header_component()`.

---

## Configuration Reference

### report.yaml -- Full Schema

```yaml
# ── Required ──
name: "Human Readable Name"
slug: my-slug                      # URL-safe identifier (default: directory name)
description: "What this report shows"

# ── Schedule ──
schedule:
  cron: "*/5 * * * *"             # Standard cron expression
  timezone: "UTC"                  # Optional timezone
  description: "Every 5 minutes"   # Optional human label

# ── Classification ──
studio: my-studio                  # Tenant / product grouping used by your project
category: Revenue                  # Grouping label
tags: [daily, revenue, core]       # Searchable tags

# ── Data Sources ──
data_sources:
  - name: primary_warehouse
    type: vertica                  # Driver type (default: vertica)
    secret: bi/primary/warehouse   # opaque id for a registered resolver
    local_env: BI_PRIMARY          # Env var prefix (local dev)

# ── Appearance ──
theme: trellum dark                # trellum dark (default) | trellum light | light | dark | money | blossom | midnight | sunset | nord | dracula | solarized | ocean | monokai
theme_switcher: true               # Show/hide theme dropdown (default: true)
layout:
  full_width: true                 # Use full browser width (default: false)

# ── Annotations ──
annotations: true                  # true | false | {default_visible: [event, release]}

# ── Display ──
display:
  color: "#FF4081"                 # Accent color on the report card
  priority: 5                      # Sort order (lower = first, default 99)
  icon: "bar-chart-2"             # Lucide icon name

# ── Visibility ──
hidden: false                      # Hide from listings but still runnable
disabled: false                    # Hide from listings AND skip in --all

# ── Extra CDN ──
extra_cdn:
  chartjs_geo: "https://cdn.jsdelivr.net/npm/chartjs-chart-geo@4"

# ── A/B Test (for ABCompare reports) ──
ab_test:
  name: "My A/B Test"
  test_name: "test_variant"
  description: "Testing new feature"
  split: "60/40"
  start_date: "2026-01-01"
  end_date: "2026-02-01"
  control_label: "Control"
  test_label: "Test"

# ── Validation ──
validation:
  suppress:
    - check-id-to-silence

# ── Version ──
version: "1.0"
```

---

## Licence

Copyright (c) 2026 Apollo Meijer. Trellum-owned code is licensed under
[AGPL-3.0-only](LICENSE).

Your data, SQL, and report content retain their own terms. Generated reports
include the Trellum browser runtime under AGPL-3.0-only and link to its exact
source. See [docs/LICENSING.md](docs/LICENSING.md) for the detailed boundary.

The software is provided without warranty under the terms of its licence.

Every third-party library, font and dataset bundled here keeps its own licence
— [THIRD-PARTY.md](THIRD-PARTY.md) lists all of them with an audit date, and
[docs/LICENSING.md](docs/LICENSING.md) covers the rest, including why there are
no per-file licence headers.
