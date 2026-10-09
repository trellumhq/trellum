# BI Report Framework -- Agent Workflow Guide

This is the **workflow guide** for AI assistants (Claude Code, Cursor, Codex)
building reports with this framework. It is the only document you need resident.

`README.md` is written for people, and it is long. **Do not load it wholesale.**
Ask for the part you need instead:

```
trellum                    what the framework is, in ~30 lines
trellum guide <topic>      one section of depth, on demand
trellum checks             every validator check id and its level
trellum validate <dir>     the current validation state of a built report
```

`trellum guide` topics: `answer`, `queries`, `format`, `generator`,
`components`, `metrics`, `filters`, `live-queries`, `rawhtml`, `validation`,
`report-yaml`, `themes`, `review`, `portal`, `analysis`.

**Why this shape.** Your whole context is re-read on every API round-trip, so a
large always-loaded reference is paid for dozens of times per task. A command is
paid for once, when it is actually needed. Reach for `trellum guide` freely —
it is cheaper than it looks, and far cheaper than guessing.

## Where to look, by question

| Question | Answer |
|---|---|
| The user wants a number, not a report | `trellum guide answer` — query the source by name, reply with the number |
| What components exist? | `trellum` — the bare command lists all of them |
| What does this validator check id mean? | `trellum checks`, then `trellum guide validation` |
| How do I filter a chart? | `trellum guide filters` |
| Is this KPI already a defined business metric? | `trellum metrics` — claim it by id, don't re-derive it |
| Why is my chart empty / not reacting? | `trellum guide format` — it is almost always wide-vs-long |
| Where does aggregation belong? | `trellum guide queries` — pandas, not SQL |
| What goes in report.yaml? | `trellum guide report-yaml` |
| What did the portal publish, and did the build there pass? | `trellum guide portal` — the MCP loop, when `.mcp.json` names a `trellum` server |
| Everything else | `README.md`, read the relevant section only |

**Before writing a generator, read `trellum guide queries` and
`trellum guide format`.** Those two rules are not guessable from the API, and
getting them wrong produces a report that builds cleanly and displays nothing
useful.

## When to use the framework

| Goal | Where it goes |
|---|---|
| Recurring, interactive report — served standalone, on a schedule, or embedded in another application | `reports/{slug}/` (the framework) |
| Publish a written finding with fixed evidence | `trellum analysis new <slug>`; Markdown in `reports/{slug}/content.md`, `kind: analysis` in `report.yaml`. See `trellum guide analysis` |
| A number or an answer — "what was X yesterday", a one-off check, quick exploration | `trellum query "SELECT ..."` or `from trellum import query` in Python, against the configured source by name. No report. See `trellum guide answer` |
| Unsure | Answer first. Promote to a report when it needs a schedule, more than one reader, a consequential number that should be reviewed, or the same question comes back a third time |

Scaffold a new report: `python3 -m trellum.new my-report --studio my-studio --category Revenue`. Build it: `python3 -m trellum.run reports/my-report --no-serve`.

**Always pass `--no-serve` unless you have been asked to open the report in a
browser.** Without it the command starts a preview server on :8050 and never
exits — the build itself finishes in under a second, but the process stays in
the foreground until it is killed. Build first, then serve as a separate step
if a human is going to look at it.

**Read files with your file-reading tool, not with `cat`, `ls` or `grep` in a
shell.** Every shell command starts a process; your native read, glob and search
tools do not. On the machine these runs were measured on a bare `ls` cost about
twelve seconds and an identical `Read` cost none — that gap is environment-
specific, but its direction never is.

The trap is that batching looks cheaper: `cat a.py; cat b.py; cat c.py` is one
command where three reads are three calls. Under a per-command cost that
reasoning is right, and here it is exactly backwards — three native reads are
free and the one shell command is not. Use the shell for things that genuinely
need it: running the build, the validator, git.

When you do serve, two things are not guessable and both have cost real time:

- **A single report is served at `/`, not at `/<slug>`.** Only `--all --serve`
  puts an index at `/` with reports beneath it. `/<slug>` on a single-report
  server returns an error page.
- **Wait for the `Serving at ...` line before opening the URL.** `--serve`
  rebuilds the report and binds the port last, so until that line appears the
  URL may still be answered by a previous server — showing a different report
  rather than failing, which is far more confusing than a refused connection.

<!-- topic: answer -->
## Answering a question without a report

A user's ask has different shapes. **A number or
an answer** ("what was revenue yesterday?", "how many players churned in
June?") wants a reply, not a build: no `reports/<slug>/`, no generator, no
validator. **A report** wants a schedule, an audience beyond the asker, or
interactive filters — that is the framework proper. **An analysis article**
preserves a written explanation and fixed captured evidence; use
`trellum guide analysis` rather than scaffolding a Python generator.
**Unsure? Answer first.**
An answer is one call; a report is a session.

How to answer:

1. **Metric first.** `python -m trellum metrics` lists the business metrics
   this project defines; `python -m trellum metrics <name>` prints one in
   full, including its canonical `sql`. If the number the user wants is
   defined there, use that derivation — a second hand-written definition of
   "gross revenue" is how two answers come to disagree.
2. **Query the source by NAME**, never by file path — the path breaks the day
   the source is a remote warehouse; the name does not. From the shell:

   ```
   python -m trellum query "SELECT SUM(revenue) AS revenue FROM fact_daily WHERE event_date = :day" --param day=2026-06-15
   ```

   From Python — a script, a notebook, a one-off `python -c`:

   ```python
   from trellum import query
   df = query("warehouse", "SELECT SUM(revenue) AS revenue FROM fact_daily WHERE event_date = :day", {"day": "2026-06-15"})
   print(df)
   ```

   `sources()` lists what is configured; `connect(name)` returns a connection
   supporting the driver's methods. Use `query` or `query_df` for bounded
   fresh-connection retries; direct calls such as `pandas.read_sql` retain
   their own error handling. With `new_connection_per_query: true` on a remote
   datasource, use only `query`/`query_df`: every uncached query opens and closes
   its own connection, and direct driver access is rejected. Queries must not
   depend on shared temporary tables, session settings or transactions.
   All three work from any subdirectory of
   the project: the root is found by walking up to `data-sources/config.yaml`.
   `python -m trellum data` prints each table's columns and date span — query
   outside the span and you get zero rows, not an error.
3. **Read-only.** Local databases (sqlite, duckdb) are opened read-only from
   both entry points; an answer cannot mutate the warehouse. Remote
   warehouses rely on their own permissions.
4. **Reply with four things:** the number, the source name it came from, the
   as-of date (the latest date in the data, or the date you filtered on), and
   the SQL you ran — so the user can check it and the next person can repeat
   it.

Promote the answer to a report when any of these becomes true: it needs a
schedule; more than one person will read it; the number is going somewhere
consequential and should be reviewed; or the same question comes back a third
time. Then scaffold it — `python -m trellum.new <slug> --studio <studio>
--category <category>` — and read `python -m trellum guide report` for the
authoring contract.

<!-- topic: queries -->
## How to write queries (MANDATORY — read before touching `queries.py`)

Reports can load spreadsheets, files, APIs, or database data into pandas
DataFrames. Use the appropriate reader for the source, such as
`ctx.read_source("name")` for configured files. The SQL guidance below applies
when reading a database; SQL is not required for other sources.

**Exploring the data first?** `python -m trellum query "SELECT ..."` runs
ad-hoc SQL against a configured source by NAME — never hunt for the database
file or hardcode its path, both of which break the day the source is a
remote warehouse. `--param day=2026-06-15` binds `:day`; sqlite sources are
opened read-only.

**The rule for report queries:** SQL pulls raw rows with a date filter.
Python does the aggregation. Never both in the same query.

```sql
-- ✓ RIGHT — minimal SQL: date filter + column projection, no aggregation
SELECT event_date, dim_a, dim_b, metric_col
FROM <schema>.<fact_table>
WHERE event_date BETWEEN DATE :start_date AND DATE :end_date
```

```python
# ✓ RIGHT — aggregate in the generator with pandas
df_raw = query_df(conn, queries.MY_QUERY, params={...})
daily = df_raw.groupby("event_date")["metric_col"].sum().reset_index()
split = df_raw.groupby(["event_date", "dim_a"])["metric_col"].sum().reset_index()
```

```sql
-- ✗ WRONG — SQL-side GROUP BY on a fact table
SELECT event_date, dim_a, SUM(metric_col)
FROM <schema>.<fact_table>
WHERE event_date BETWEEN ... GROUP BY 1, 2
```

Two reasons this rule is non-negotiable:

1. **Memory footprint.** A `GROUP BY + SUM` on a wide fact table makes
   the database pre-allocate several GB for the hash aggregate, and
   local-dev user accounts frequently run under tight resource-pool
   caps. Raw-row retrieval with a date filter stays small — a typical
   30-day window of a fact table fits in tens of MB of pandas.
2. **Dynamic filtering is the framework's whole point.** The
   client-side `FilterBar` re-aggregates every time the user changes
   a filter. Pre-aggregating in SQL collapses dimensions and breaks
   that interactivity — the chart freezes at whatever grain the SQL
   produced. Raw-row `DataSource`s let the client slice any dimension
   you included.

Code smells — triggers to rewrite the query:

- `SUM()` / `AVG()` / `COUNT(DISTINCT)` in the SELECT that collapses a
  dimension the user might want to filter by.
- `GROUP BY` on only a subset of the filterable dimensions. Either
  drop the GROUP BY or group by every dim the client might filter on
  (denormalized grain).
- `HAVING` — do it in pandas.
- `JOIN` that explodes row count to attach a dim — better as a pandas
  `.merge()` after both sides load.

Acceptable exceptions (rare):

- Querying an already-aggregated table (one whose name signals a
  pre-rolled grain like `*_agg_*` / `*_daily_*`): pull columns
  directly, no further SQL aggregation.
- A `GROUP BY` on the union of every filterable dimension. Preserves
  filterability but compresses duplicates. Use only if raw-row
  retrieval genuinely returns too much data.

Project-specific table names, memory caps, and schema particulars live
in `project_context/chat_rules/` — the chat feature loads them at
runtime; human readers can look there for the concrete tables to query.

<!-- topic: format -->
## Long format vs wide format (MANDATORY)

**Rule:** dimensions stay in **rows**, never in **column names**. One
row per (date × every breakdown dim), one column per metric.

```python
# ✓ RIGHT — long format
event_date  | platform | spender_tier | dau   | iap_revenue | ad_revenue
2026-04-01  | ios      | Whale        | 12000 | 9800        | 250
2026-04-01  | android  | Whale        | 8500  | 5400        | 180
```

```python
# ✗ WRONG — platform baked into column names ("wide format")
event_date  | spender_tier | dau_ios | dau_android | iap_revenue_ios | iap_revenue_android | ...
2026-04-01  | Whale        | 12000   | 8500        | 9800            | 5400                | ...
```

Why long format is the default:

1. **Filter coverage.** The client filter engine matches on column
   *values*, not column *names*. A filter on `platform = "ios"` only
   works when there's a `platform` column carrying `"ios"` as a value.
   Wide format makes the filter inert.
2. **Smaller payload.** Dictionary encoding via `_serialize_columnar`
   compresses high-repetition columns (e.g. `platform` with 8 unique
   values across 50 k rows) to integer indices — typically 60–70 %
   smaller than the wide equivalent with one column per platform.
3. **Auto-discovery.** Adding a new platform just adds rows to the
   data; charts using `stack_by="platform"` pick it up. Wide format
   forces editing every chart's `y=[col_a, col_b, ...]` list.
4. **Simpler SQL.** `GROUP BY event_date, platform, ...` instead of
   one `SUM(CASE WHEN platform='ios' THEN dau END) AS dau_ios` per
   metric per platform.

Render breakdowns with `stack_by`:

```python
# Long format → one line per platform via stack_by
LineChart(df=df, x="event_date", y="iap_revenue",
          stack_by="platform", dataset_id=ds, y_format="currency",
          title="IAP Revenue by Platform")

# Long format + ratio → one line per platform, computing iap/dau per (date, platform)
LineChart(df=df, x="event_date", dataset_id=ds,
          ratios=[{"numerator": "iap_revenue", "denominator": "dau",
                   "label": "IAP ARPDAU"}],
          stack_by="platform", y_format="currency",
          title="ARPDAU (IAP) by Platform")
```

The `chart-filter-coverage` validator catches accidental wide-pivots —
when a chart's DataSource cannot react to a FilterBar filter because
the dimension was collapsed into column names. Long format is the
fix; suppression is reserved for charts that are intentionally a
fixed rollup (e.g. period-over-period reference rollups).

<!-- topic: generator -->
## The 5-step process to write generator.py

1. Subclass `BaseReport` and implement `generate(self, ctx)`.
2. Load a DataFrame using the reader for your source. For configured files, use `ctx.read_source("name")`; for databases, use `conn = ctx.get_connection("name")` and `query_df(conn, queries.X, params={...})`. Source names come from `data-sources/config.yaml`.
3. Wrap each DataFrame in a `DataSource` + a `FilterBar` placed together in an **untitled section**: `ctx.add_section("", [DataSource(...), FilterBar(...)])`. Untitled is required for sticky positioning.
4. Add content sections: `ctx.add_section(title, [...])`.
5. **Every chart, KPI, and table component MUST carry `dataset_id="..."`** pointing to its DataSource. Without it the component renders statically and silently ignores filters — this is the most common mistake.

To copy from, list what this project actually has (`ls reports/`) and open one.
Report names are not named here on purpose: this file ships with the framework
and travels into every project, so any slug written down is a report somebody
else has. A measured run followed three such names and got three "file does not
exist" errors before it thought to look.

<!-- topic: report-yaml -->
## Write the report.yaml description for discoverability (MANDATORY)

A search or assistant layer routes user questions to reports using
the `description` and `tags` in `report.yaml`, plus the dataset columns it
indexes from the built output. A vague description ("Revenue report") makes
the report invisible to it. The description MUST state:

1. **Which business questions the report answers** ("how many payers churn
   per week and why"), not just its topic.
2. **The key metrics and dimensions** it carries (churn rate, revenue at
   risk; split by platform/tier/country).
3. **Grain and freshness** (weekly cohorts; daily; refreshes every 5 min).

The `report-description-weak` validator check WARNs on short descriptions.
Descriptive column names in DataSources matter for the same reason — the
assistant reads them from data.json to decide which dataset answers a
question.

<!-- topic: components -->
## Component Reuse Policy

**Before writing any custom HTML, CSS, or JS, name the framework component you ruled out and why.** Most needs are already solved.

| Need | Required component |
|---|---|
| Filters / dropdowns / sticky filter bar | `DataSource` + `FilterBar` |
| Section-local filter on same data (no propagate_to wiring) | `ScopedDataSource(id, parent=...)` + section-scoped `FilterBar` |
| Cascading dropdowns (parent → child option narrowing) | Add `depends_on: "parent_col"` to a dropdown filter spec |
| KPI cards | `KpiRow(dataset_id=...)` (`agg`: `sum` / `ratio` / `count` / `abssum`) |
| Time series | `LineChart` |
| Bar / stacked bar | `BarChart` / `StackedBar` |
| Area / stacked area | `AreaChart` (`stacked=True`) |
| Doughnut / pie | `DoughnutChart` |
| Heatmap / day×hour / cohort grid | `HeatmapChart` |
| Funnel | `FunnelChart` |
| Treemap | `TreemapChart` |
| Scatter / correlation / bubble | `ScatterChart` |
| Dual-axis bar + line | `ComboChart` |
| Ratio metrics (ARPDAU, ARPPU, retention, conversion) | `LineChart(ratios=[{numerator, denominator, label}])` — never hand-roll the division in JS |
| Ratio by category (CPD per comfort_zone, ARPPU by country) | `BarChart(ratios=[...], horizontal=True, sort='desc')` — categorical ratio bars |
| Tables | `DataTable`, `ComparisonTable`, `PivotTable` |
| Layout | `Grid`, `Panel`, `SplitPane`, `TabGroup` |
| Card-panel grid | `Grid(card=True)` |
| Cross-grain filter sync | `FilterBar(propagate_to={target_ds: {src_col: tgt_col}})` |
| A/B test report | `ABCompare.from_users(df, variant_col=..., control=..., test=..., metrics=[Metric(...)])` — the front door: a per-user frame in YOUR column names, out comes the finished component with an SRM badge and raw / winsor / CUPED modes (a `Metric` that declares `pre_col` opts into CUPED). Groups fetched separately (one query per arm)? `ABCompare.from_groups({"Control": df_a, "Test": df_b}, metrics=[...])`. Self-contained — no DataSource/FilterBar needed. The raw constructor `ABCompare(rows=..., modes=...)` plus `trellum.stats.ab.{winsorize_user_df, cuped_user_df, bootstrap_ab_cis}` remains for custom row sets. See `trellum/README.md` "A/B Testing". |

`RawHTML` is the **exception, not the default**. It is permitted only when **both**:

1. The visualization is genuinely novel (force-directed graph, Sankey, etc.) and no combination of framework components can express it.
2. You have explicitly named the components you considered and why each was insufficient.

For complex reports prefer the **hybrid pattern**: framework components for filters/standard charts/KPIs; `RawHTML` only for the genuinely novel section. The custom JS must subscribe to the framework filter engine — `window._fwFilterEngine.subscribe(dsId, id, fn)` — instead of managing its own filter state. See `trellum/README.md` "Custom Dashboards" for the full pattern and the `fw.*` API.

### Anti-patterns to avoid

- **Hardcoded hex colors anywhere in RawHTML JS/HTML** — the `rawhtml-hardcoded-hex` validator **FAILS** the report on any `#RGB`/`#RRGGBB` literal except `#fff` and `#000`. Use `fw.getThemeColors().chart_colors[i]` for chart palettes and CSS vars (`var(--accent-red)`, `var(--text-main)`, `var(--bg-card)`) for HTML/CSS.
- **Hardcoded `rgba?()`/`rgb()` literals in RawHTML JS** — `rawhtml-hardcoded-rgba` (WARN) flags numeric color literals that don't update on theme switch. Use `fw.getThemeColors().grid_color` for grid lines, `.tick_color` for axis ticks / legend labels, `.chart_colors[i]` for dataset colors. `rgba(0,0,0,0)` (transparent) is exempt. Suppress for intentional fixed-color semantic annotations.
- **CSS variable strings in Chart.js color properties** — `rawhtml-css-var-in-chartjs` (WARN) flags patterns like `color: 'var(--text-main)'`. Chart.js has no CSS resolver; the string is used as-is (invalid color). Use `fw.getThemeColors().tick_color` / `.grid_color`, or resolve with `getComputedStyle(document.documentElement).getPropertyValue('--name').trim()`.
- Re-implementing Slim Select dropdowns (use `FilterBar`; `ScopedDataSource` for section-local; `depends_on` for cascading)
- Writing custom `getFilteredRows()` / filter state management (use the public `fw.filterEngine.*` API)
- Polling with `setTimeout(init, 100)` to wait for `_fwFilterEngine` (use `fw.filterEngine.onReady(dsId, fn)`)
- Referencing `window._fwFilterEngine` directly in RawHTML (it's private; use `fw.filterEngine`)
- Duplicating a DataFrame to get a separate DataSource for section-local filtering (use `ScopedDataSource(id, parent=...)`)
- Duplicating KPI rendering when `KpiRow(dataset_id=...)` covers it
- Building custom layout grids when `Grid` / `Panel` / `SplitPane` suffice
- Going 100% `RawHTML` when only one or two sections need custom logic
- LEFT JOINing coarser-grain data (monthly MAU, install cohort) into a finer-grain DataFrame to avoid creating a second DataSource — duplicates rows and silently breaks ratio aggregations under filtering

### KpiRow aggregation semantics (exact, from the runtime)

Every `agg` recomputes over the **filtered rows**, so pass raw columns and let the runtime do the arithmetic — a pre-divided or pre-summed column cannot re-aggregate. The complete set:

| `agg` | Computes | Notes |
|---|---|---|
| `sum` | `sum(column)` — or summed across a `columns` list | the default |
| `count` | number of filtered rows | no `column` needed; add a literal `df["x_n"] = 1` column when you also need a ratio denominator |
| `abssum` | `sum(abs(column))` | for signed ledgers |
| `ratio` | `sum(numerator) / abs(sum(denominator))`, ×100 **only** when `format: "percent"` | the scaling follows the FORMAT, so a currency ratio (ARPDAU, AOV, revenue per order) and a plain one (sessions per user, `format: "ratio"` → "2.98x") are ordinary ratio KPIs. Charts do the same: `ratios=[{numerator, denominator, label}]` divides raw and the axis format scales |
| `avg_by_date` | `sum(column) / count(distinct date_col)` | per-day average; `date_col` defaults to `event_date` |
| `purchase_pct` | share of `source_col` volume where `type_col` is in `match_values`, ×100 | percent-format only — unlike `ratio`, its ×100 is still unconditional |

Where the ×100 lives in `ratio` is the most re-derived fact in measured
sessions — one agent spent 15 tool calls reading `kpis.py`, `validation.py`
and the JS runtime to establish it. It is display, not aggregation, and it is
stated here so the next one does not have to look.

<!-- topic: metrics -->
## Claimed metrics: metrics.yaml, the `{"metric": id}` claim, and `ctx.metrics()`

**Before inventing a KPI dict, run `python -m trellum metrics`.** If the
number you are about to compute is already defined there, **claim it by id**
instead of writing another `{"label", "agg", "column"}` dict — a second
hand-written definition of "gross revenue" is how two reports come to
disagree about what gross revenue is. If it is a business number and it is
NOT defined, propose adding it to the project-root `metrics.yaml` rather than
hard-coding a definition only this report knows about. Inline KPI dicts stay
fully supported — claims are for the numbers that must mean the same thing
everywhere.

A claim replaces the KPI dict with the metric's id:

```python
KpiRow([
    {"metric": "gross_revenue"},          # label, format, agg, column all
    {"metric": "transactions"},           #   expand from metrics.yaml
], dataset_id="rev")

KpiCard(metric="gross_revenue", value=total)   # static claim: identity only,
MiniKpi(metric="transactions", value=n)        #   the value must be supplied
```

The claim expands **at build time** into the exact aggregation config the
client engine already executes — there is no new computation engine, and a
claimed KPI filters and re-aggregates exactly like an inline one. Explicit
keys on the claim dict win over the definition, but the `metric-overridden`
check WARNs when they conflict: an override means the card no longer computes
the definition it names.

### Getting the rows: datasets and `ctx.metrics()`

A metric can **bind to a dataset** — the one place that says "these rows are
daily, over `event_date`, filterable by these dimensions". Bound metrics are
fetched with `ctx.metrics()` instead of a hand-written query:

```python
df = ctx.metrics(["gross_revenue", "dau"], by=["platform"], window=90)
ctx.add_section("", [DataSource("rev", df),
                     FilterBar("rev", df, filters=[
                         {"column": "event_date", "type": "date_range"},
                         {"column": "platform"}])])
ctx.add_section("Overview", [
    KpiRow([{"metric": "gross_revenue"}, {"metric": "dau"}], dataset_id="rev"),
    LineChart(df, x="event_date", y="total_revenue", stack_by="platform",
              dataset_id="rev", title="Gross Revenue"),
])
```

It returns a plain long-format DataFrame — the dataset's time column, the
`by` dimensions, one column per measure the metrics need — so a bare claim on
a `KpiRow` over it computes by construction and `metric-column-missing`
passes. **One call covers one dataset**; ids from two datasets raise with the
split spelled out (two datasets are two DataSources anyway). `by` must name
declared dimensions of the dataset; anything else raises before a query runs.
`window` is days back from `ctx.today` or an explicit `(start, end)`; the
default is the dataset's `lookback`, else 90 days. Identical requests in one
build cost one fetch, and the generated SQL has a stable column order so it
hits the query cache across reports.

The SQL shortcut selects time + dims + measures with the date filter, and
GROUPs BY time + dims with `SUM()` when every requested metric is additive
across dimensions — the one sanctioned exception to "aggregate in pandas",
because the grouping is over every dimension the FilterBar can use. A
`count` metric means rows, so its presence keeps native grain. Derived
per-row columns (`total_revenue: "iap_revenue + ad_revenue"`) live on the
dataset's `columns:` map and are derived once, there, instead of in every
report. Raw event tables that would not survive a GROUP BY get a Python
`provider: module:function` instead, called as `(ctx, req) -> DataFrame`;
the layer projects and rolls up its frame the same way.

`metrics.yaml` format (a LIST, so duplicate ids are lintable):

```yaml
version: 1
datasets:                          # optional; files without it stay valid
  daily:
    source: demo_db                # a source from data-sources/config.yaml
    table: fact_daily              #   + table, optional where: "day_number = 1"
    columns: {total_revenue: "iap_revenue + ad_revenue"}   # derived per row
    # provider: metrics_data:daily   # OR a Python (ctx, req) -> DataFrame
    time: {column: event_date, grain: day}   # hour|day|week|month, or time: none
    dimensions: [title, platform, region]    # what the dataset can be filtered by
    lookback: 90                             # default window, days
metrics:
  - name: gross_revenue            # ^[a-z0-9][a-z0-9_]*$
    label: "Gross Revenue"
    description: >
      IAP plus ad revenue, gross of platform fees, daily grain.
    owner: finance@example.com
    format: currency               # any KpiCard format
    agg: sum                       # sum | abssum | count over `column`,
    column: total_revenue          #   or agg: ratio with numerator/denominator
    dataset: daily                 # the binding; absent = listed, not monitored
    time_agg: avg                  # optional: avg (per period) | last (default: sum)
    sql: "iap_revenue + ad_revenue"   # canonical derivation — informational
    dimensions: [event_date, title]   # informational
    tags: [revenue]
```

A metric **without** an `agg` spec is *descriptive*: claimable only where a
`value` is supplied (static KpiCard/MiniKpi). A metric **with** a spec is
*executable* and can be claimed bare inside a live `KpiRow(dataset_id=...)`.
`time_agg: avg` makes a bare claim expand to the client's `avg_by_date` over
the dataset's time column (a DAU card reads "average DAU over the window");
`time_agg: last` (balances) is rendered from a Python-computed `value=` for
now. `dataset` and `time_agg` are provenance, not meaning, so they do not
move `definition_hash`.

**`python -m trellum metrics --report`** scaffolds `reports/metrics/`: an
all-metrics-at-a-glance page for every bound metric — one DataSource per
dataset, then one block per metric (a KPI and a trend), grouped by tag, plus a
table of the metrics defined but not bound — from a one-line generator. Bind a
metric and rebuild; nothing else to edit.

The blocks in a tag sit **side by side**, in `Grid(min_width=340)` — an
overview is metrics read against each other, not one chart per screenful. The
grid is `auto-fill`, so the number across follows the available width (three
at the container's 1400px cap, two around 900px, one on a phone) with no
breakpoints to keep in sync, and each cell is a **tile**: `grid.css` shortens
its chart to 200px, drops the chart's own title (the block above it already
carries the name) and hides the breakdown toggle, and measures a `KpiRow`
against the tile instead of the window. `Grid(min_width=...)` is the general
primitive, not a metrics-report special case — reach for it whenever a row of
charts should reflow rather than sit at a fixed column count.

Two controls, two scopes. The **date range** is shared: one FilterBar on the
first time dataset, propagated onto every other time dataset's own time
column, because "the last 30 days" means the same thing at every grain. A
dataset with no time axis is left out of it rather than filtered on a column
it does not have. The **breakdown is per metric**: each trend carries its own
`stack_by_options` toggle over its dataset's dimensions ("Total" first, so no
split is the default) — hidden at tile width, where a row of dimension buttons
is taller than the chart it labels, and back in the single-block view below.
There is deliberately no report-wide dimension filter — datasets share a time
axis but not their dimensions.

Each block is a `Section` with `anchor="metric-<name>"`, which makes it
addressable: **`?only=<section id>`** on any report page renders that section
alone (its ancestors and the shared FilterBar with it, page chrome dropped),
which is what an `<iframe>` onto a single block loads and what `#<id>` scrolls
to. The tile grid collapses there — one block is not a grid — so the surviving
block gets the whole frame at full size, breakdown toggle included. An id the
build does not have leaves the page whole. Give any generated section an
`anchor` when its title is not a name you want in a URL.

What the build produces: `data.json` gains a top-level `_metrics` block
(claimed metrics only — label, format, agg spec, version, `definition_hash`,
claiming component ids) and `_meta.json` gains `metrics_used:
[{"id", "definition_hash", "version"}, ...]`, so anything reading built
artifacts can group KPIs by business meaning, and tell a claim's build-time
definition apart from the metric's current one without opening `data.json`
(read it via `trellum.meta.normalize_metrics_used`, which also accepts an
older build's bare-id-list shape).

Validation (`metrics` check group): `metrics-yaml-schema` (broken file —
duplicate ids, bad slugs, unknown format/agg, a `dataset` ref that does not
exist, a dataset with neither `provider` nor `source` + `table`, a dataset
without `time`), `metric-undefined` (claim of an id metrics.yaml does not
define — FAIL), `metric-column-missing` (the claim cannot compute: definition
columns absent from the DataSource, or a claim with no value where one is
required — FAIL), `metric-overridden` (WARN), `metric-description-weak`
(INFO).

CLI: `python -m trellum metrics` lists definitions, datasets and claim
coverage; `python -m trellum metrics <name>` prints one full definition;
`python -m trellum metrics --lint` reports duplicates and orphans;
`python -m trellum metrics --report` scaffolds the monitoring report.

<!-- topic: rawhtml -->
## RawHTML chart lifecycle (mandatory when the chart lives inside a `Visible`)

If your `RawHTML` creates a Chart.js instance and the `RawHTML` is anywhere inside a toggle-driven `Visible`, the JS **must** patch BOTH `_initToggleVis` and `renderAll`, AND defer the render with `requestAnimationFrame`:

```js
function _refresh() {
    if (fw.filterEngine.isReady(dsId)) _render(canvasId, dsId, fw.filterEngine.getFiltered(dsId));
}

fw.filterEngine.subscribe(dsId, 'mychart', _render);
window.addEventListener('fw-theme-change', _refresh);

// _initToggleVis runs on initial page load (renderAll does NOT — the framework
// calls _renderComps() directly on first load). Without this hook the chart is
// blank on first load and on URL-preloaded state.
window._initToggleVis = (function (prev) {
    return function () { if (prev) prev(); requestAnimationFrame(_refresh); };
})(window._initToggleVis);

// renderAll catches scope switches, theme changes, auto-refresh.
window.renderAll = (function (prev) {
    return function () { if (prev) prev(); requestAnimationFrame(_refresh); };
})(window.renderAll);
```

Why each piece:

| Hook | Catches | Without it |
|---|---|---|
| `fw.filterEngine.subscribe(dsId, ...)` | filter changes | chart never updates when user filters |
| `fw-theme-change` listener | theme switches | chart keeps old palette |
| `window._initToggleVis` patch | **initial page load + URL preload** | chart blank on first load — only renders after user clicks a toggle |
| `window.renderAll` patch | scope switches, auto-refresh, theme switches | chart stale after re-renders |
| `requestAnimationFrame` defer | layout reflow after `_updateToggleVis` flips `display` | Chart.js measures canvas at 0×0 → blank chart even though parent is visible |

For multi-canvas patterns (one canvas per per-feature DataSource), use a `_ensureSubs()` that finds canvases by attribute (e.g. `[data-split-ds]`) and lazily subscribes once each, then `requestAnimationFrame(_refreshAll)` from both lifecycle hooks.

<!-- topic: filters -->
## DataSource + FilterBar rules

- Every report has at least one `DataSource` + `FilterBar` pair (exceptions: `ABCompare`-only reports; single-day dashboards may omit the date_range filter but should still use `DataSource` for any filterable dimension).
- `date_range` filter first when there's a time-series dimension; one filter per useful categorical dimension.
- **One grain = one DataSource.** Different grain (monthly MAU vs daily revenue) gets its own `DataSource`. Use `propagate_to` to sync shared dimensions.
- `static=True` only when data is fundamentally incompatible with the report's filters (point-in-time snapshot, external system data with different date semantics) — rare.

### Two layout patterns: main vs section FilterBar

The framework supports two FilterBar placements, and they compose:

**Main FilterBar** (the default for most reports). Placed in an
**untitled section** at the top. Use it for filters that apply across
the whole report — typically `date_range`, `audience_segment`,
`platform`, etc. The main FilterBar reaches its primary DataSource
plus any DataSources listed in `propagate_to`. Sticks to the viewport
top.

```python
ctx.add_section("", [                # untitled section — sticky top
    DataSource("daily", df_daily, chunk_by="month"),
    DataSource("monthly", df_monthly),
    FilterBar("daily", df_daily, filters=[
        {"column": "event_date", "type": "date_range"},
        {"column": "platform"},
    ], propagate_to={
        "monthly": {"event_date": "event_date"},
    }),
])
```

**Section-scoped FilterBar** (drill-downs). Placed inside a titled
section right after that section's DataSource(s). Use it when a
section has a dimension that doesn't make sense for the rest of the
report — e.g. `chest_level` only applies to chest charts. Section
FilterBars compose with the main FilterBar: a chart inside a section
is filtered by the AND of both. Sticks below the main FilterBar while
its section is in view.

Canonical layout for a section-scoped pair (validator recognizes this
exact order):

```python
ctx.add_section("Chest Daily Trends", [
    DataSource("chest_daily", df_chest),
    FilterBar("chest_daily", df_chest, filters=[
        {"column": "chest_level"},
        {"column": "difficulty_tier"},
    ]),
    LineChart(..., dataset_id="chest_daily"),
    LineChart(..., dataset_id="chest_daily"),
])
```

### Section-local filters on the SAME data — use `ScopedDataSource`

When a section needs **another local filter** on data that's already
loaded via the main DataSource (e.g. "Price Tier" filter that only
affects one chart, not the whole report), use a `ScopedDataSource`
instead of a fresh DataSource. It:

- inherits all of the parent's filters automatically — no `propagate_to`
  wiring needed
- ships no extra data — rows are derived from the parent at runtime
- composes with the section's own FilterBar (parent filters AND child
  filters)

```python
# Untitled top section: ONE base DataSource + main FilterBar
ctx.add_section("", [
    DataSource("cpd", df),
    FilterBar("cpd", df, filters=[
        {"column": "event_date", "type": "date_range"},
        {"column": "package_group"},
    ]),
])

# Drill-down section with its OWN local filter
ctx.add_section("CPD by Price Point", [
    ScopedDataSource("cpd_pp", parent="cpd"),
    FilterBar("cpd_pp", df, filters=[
        {"column": "price_tier"},      # only affects this section
    ]),
    LineChart(df=df, x="event_date", dataset_id="cpd_pp",
              ratios=[{"numerator": "chips", "denominator": "revenue",
                       "label": "CPD"}],
              stack_by="price_point_display",
              title="CPD by Price Point"),
])
```

**Cascading dropdowns** (e.g. *Package Group* → *Package Name*): add
`depends_on` to the child filter spec. The child's option list narrows
automatically when the parent selection changes:

```python
FilterBar("cpd_pkg", df, filters=[
    {"column": "package_group"},
    {"column": "package_name", "depends_on": "package_group"},
])
```

### Picking a filter type

A slider fits a continuous numeric range (a discount %, a price, a
quantity) -- somewhere enumerating every value as `dropdown` options
would be absurd. A dropdown fits categorical values. A toggle fits a
handful of distinct values. A date column always stays on `date_range`
-- never model it as a `slider`, even though both are range-shaped.

```python
FilterBar("orders", df, filters=[
    {"column": "discount_pct", "label": "Discount %",
     "type": "slider", "format": "percent"},
])
```

`mode="range"` (the default) gives a two-handle min/max band; commits
land in the filter engine as its own `numrange` mode (numeric
comparison -- unlike the string-compared `range` mode `date_range`
uses, which would misorder plain numbers). `mode="single"` snaps to one
of the column's distinct values and commits through the existing
`equals` mode, same as a dropdown's single selection. The validator
flags a slider on a non-numeric column (`filter-slider-not-numeric`).

#### Ordinal sliders: sliders over ORDERED CATEGORIES

A slider also fits ORDERED categories that have no numeric value of
their own -- a spender tier, a severity level, a size class. Give it
`"values"` (the ordered list) instead of letting it derive bounds from
`df[col].min()/.max()`; the column doesn't need to be numeric:

```python
FilterBar("players", df, filters=[
    {"column": "spender_tier", "label": "Spender Tier", "type": "slider",
     "values": ["non_spender", "minnow", "dolphin", "whale"],
     "labels": {"non_spender": "Non-spender", "minnow": "Minnow",
                "dolphin": "Dolphin", "whale": "Whale"},
     "mode": "range", "default_min": "minnow", "default_max": "whale"},
])
```

Internally the values map to integer positions (0, 1, 2, ...) so
noUiSlider can drive them -- pips at each position show `labels`
(fallback: the raw value) if they fit the slider's width, otherwise
just the two endpoints. `mode="range"` commits the CONTIGUOUS SPAN of
selected categories as a list through the existing `'in'` mode --
exactly what a dropdown multi-select commits, so `propagate_to`,
`depends_on` cascades, and URL sync all keep working with no changes.
`mode="single"` commits the selected category through `equals`, same
as the numeric slider's single mode.

Reach for the ordinal slider only when the categories have a real
order the reader would drag through end to end; an unordered set (e.g.
`platform`, `country`) stays a `dropdown` -- a slider implies an order
that doesn't exist there.

The validator extends the same slider check for the ordinal case:
`values` must be a non-empty list of strings and any default must be
one of them (`filter-slider-not-numeric`, fail); a listed value that
never occurs in the DataFrame is flagged (`filter-slider-value-unused`,
warn, not fail) -- a tier legitimately going empty under a narrow date
filter is normal, not a broken config.

### Custom RawHTML lifecycle

When you do need custom JS (genuinely novel visualization), always use
the public `fw.*` API:

```js
fw.filterEngine.onReady('cpd', function() {
    // engine is ready — wire your chart now
    fw.filterEngine.subscribe('cpd', 'my-chart', _render);
    _render(fw.filterEngine.getFiltered('cpd'));
});
```

**Never** poll with `setTimeout(init, 100)`, **never** reference
`window._fwFilterEngine` directly, **never** instantiate `new SlimSelect(...)`
in RawHTML. Each of those is a validator WARN now. Use FilterBar +
ScopedDataSource for the dropdown UX; use `fw.filterEngine.onReady` for
ready-detection; use `fw.filterEngine.*` for everything filter-related.

Validator constraints:

- At most one main FilterBar per report (untitled top section).
- At most one section-scoped FilterBar per titled section.
- Section-scoped pair must be laid out as `[DataSource(s) or ScopedDataSource, FilterBar, ...content]` at the start of the section.
- A `ScopedDataSource`'s `parent` must reference a base `DataSource` (not another scoped child).
- A filter with `depends_on` must reference another filter on the same FilterBar with a real column in the underlying DataFrame.

Use `ScopedDataSource` when a section filters the *same* rows more narrowly. When the grain genuinely differs, give each section its own `DataSource` instead — different data is not a scope of the same data.

<!-- topic: live-queries -->
## Live queries: per-entity lookups a snapshot cannot carry

**Compiled is the default; live is the rare exception.** Every component
you have used so far — `DataSource`, `FilterBar`, every chart, every
table — computes at build time and ships its rows baked into the artifact.
That is the right trade almost always: it is fast, it works from a share
link or an emailed copy, and it needs nothing running to keep working.
Reach for a live query ONLY when the question is genuinely per-entity and
the entity count is too large to bake in full — "type a user id, see that
user's raw events" against a table with a million users, not "show revenue
by day" or anything a `FilterBar` over a compiled `DataSource` already
answers. If a compiled component can answer it, use the compiled
component; a live query is the opt-in exception, not a habit.

**The snapshot is ONE slice, never the whole table.** Declaring a live
query still bakes something in — the build runs the declared query once,
at build time, for a single parameter set you choose
(`snapshot_params`), and stores that one result as the ordinary snapshot
every other dataset already gets. A million-user table does not become a
million-row artifact; it becomes one example user's rows, plus a promise
that a serving host can fetch any other user's on demand. Give
`snapshot_params` so the un-hosted page still shows something real — the
`live-query-no-snapshot` WARN flags the alternative (an empty control,
standalone).

**A live query is a filter-engine DATASET, not a bespoke control.**
`ctx.declare_live_query` registers the query; a `LiveDataSource` puts its
snapshot into the filter engine under a `dataset_id` exactly like an
ordinary `DataSource`; an ordinary `FilterBar` drives it by naming, on
each filter, the declared param that filter's column feeds. Every
chart/table/KPI row built against that `dataset_id` reacts exactly as it
would to a compiled dataset — it does not know its rows came from a POST
instead of a local filter.

```python
# generator.py
events = ctx.declare_live_query(
    "user_events",                        # slug: ^[a-z0-9][a-z0-9_-]*$
    queries.USER_EVENTS,                  # SQL with :user_id, :since, :until
    datasource="demo_db",                 # must be a SQL source — see below
    params=[
        {"name": "user_id", "type": "int", "required": True},
        {"name": "since", "type": "date", "required": True},
        {"name": "until", "type": "date", "required": True},
    ],
    snapshot_params={"user_id": 1042, "since": "2026-01-01", "until": "2026-02-01"},
)

ctx.add_section("Events", [
    LiveDataSource("events", query="user_events", df=events),
    FilterBar("events", events, filters=[
        {"type": "text", "column": "user_id", "param": "user_id",
         "label": "User ID", "placeholder": "e.g. 1042"},
        {"type": "date_range", "column": "ts",
         "min_param": "since", "max_param": "until", "label": "Date"},
    ]),
    KpiRow(dataset_id="events", kpis=[{"label": "Events", "agg": "count"}]),
    DataTable(events, title="Raw events", dataset_id="events", sortable=True),
])
```

Param `type` is one of `int|float|str|date|enum`; `enum` carries
`values: [...]`; `str` may carry `max_length` (default 200). A filter's
`param` key (or `min_param`/`max_param` for a range-shaped filter —
`slider`, `date_range`) is what maps it to a declared query param;
`dropdown`/`toggle`/`flag` bind an `enum` param, `slider`/`date_range` bind
numeric/date scalars, `text` binds any scalar. Live-bound dropdowns are
single-select in v1 (`"multi": False`). A second `LiveDataSource` can share
the first `FilterBar`'s filters instead of rendering its own, via
`propagate_to` on the `FilterBar` — one filter change then drives two
queries; see `demo/reports/user-event-log` for a live grouped-aggregate
query fed this way.

The declaration writes `_live_queries.json` (SQL + param schema) beside
`_meta.json`, for a host to read. The SQL never enters `data.json` or
`index.html` — the page carries only the query id, the declared param
schema, and (for the FilterBar's own dataset) the snapshot rows. Commits
are auto-query for dropdown/toggle/date-preset/slider-release, explicit
(Enter/blur) for text — typing alone never fires a query.

**Combine freely with compiled components.** A live dataset and a compiled
one can sit in the same report, even the same section — nothing requires
an all-or-nothing choice. `demo/reports/user-event-log` puts a live
`FilterBar`-driven KPI row, two charts and a table above a plain compiled
`LineChart(..., static=True)` showing the same metric aggregated over the
*entire* history: the live components answer "show me this one thing in
detail, right now", the compiled chart answers "how does this look in
aggregate, over everything" — a live per-entity lookup is a poor way to
answer that, and a compiled aggregate answers it for free. Reach for
`static=True` on a chart/table sharing a section with live components
whenever what it shows is a compiled aggregate, not a per-entity slice.

**The standalone degradation.** Without a host advertising `live_query_url`
(standalone build, share link, email snapshot, an old host, or — the
common case while you are writing the report — an artifact just opened
from disk), every live `FilterBar` renders server-side disabled with the
label "Live lookup — available when served by a host; showing data from
the last build", and the baked snapshot stands. This is not a degraded
error state; it is correct behavior with nobody to ask. Two hosts
currently implement the fetch: the portal in production, and `trellum
serve` for local development — the dev server answers the same request
shape against the project's own data sources when the report you are
serving declared a live query, through the identical `coerce_params` /
`check_sql_safe` / `inject_limit` guard rails
(`trellum/data/live_query_guard.py`) the portal's production endpoint
enforces, so what works locally is a real preview of what the portal will
do. Use `trellum serve` (not a bare file open) while iterating on a live
query if you need to see it actually fetch.

**SQL sources only.** The host executes the manifest's SQL on demand, so
the datasource must have a connection driver (`sqlite`, `duckdb`,
`postgres`, `mysql`, `bigquery`, ...). File/API/sheet sources fail the
`live-query-source-not-sql` check. Referencing an undeclared query id is
the `live-query-unknown` FAIL; a bad param schema (including `required:
false`, which literal substitution cannot support) is
`live-query-param-schema`; the SQL's `:name` placeholders and the declared
params must agree in both directions (`live-query-sql-params` — an
undeclared placeholder FAILs, an unused param WARNs); a declared param no
filter binds is `live-query-param-uncovered` — it could never change from
its snapshot default. `trellum checks` lists every `live-query-*` id (and
every other check id) with its level; `trellum guide validation` explains
suppression.

<!-- topic: review -->
## The live review loop

Review mode turns a served report into a feedback surface: the user clicks
elements in the browser, types change requests, queues them with one optional
chat message, and hits **Send** — you receive the batch in the terminal with
each item's section title, component kind and title, DOM id, and selector,
which map straight to lines of `generator.py`.

The loop, from your seat:

1. `python -m trellum review start reports/<slug>` — builds if output is
   missing (`--rebuild` to force), starts or reuses the background server,
   enables review mode, and opens the browser. If a server predating review
   mode holds the port, rerun with `--restart-server`.
2. `python -m trellum review poll` — **BLOCKS** until the user sends
   feedback. From an agent harness, run it with a generous timeout or as a
   tracked background task; if it gets killed, just rerun it — queued
   feedback is never lost. Exit codes: `0` feedback arrived, `2` your
   `--timeout` elapsed, `3` the user ended the session.
3. Edit `reports/<slug>/generator.py` and rebuild with
   `python -m trellum.run reports/<slug> --no-serve`. The browser notices
   the rebuild and reloads itself within ~2 seconds — never hand-edit
   `output/` HTML, and never restart the server to "refresh".
4. `python -m trellum review poll --reply "what you changed"` — the reply
   appears in the browser's chat and unlocks the user's Send button, then
   the command waits for the next round.
5. Repeat until poll exits `3` (the user pressed End, or Send & End — that
   final batch still arrives first). `python -m trellum review end` ends
   it from your side when the user asks you to wrap up in conversation.

**Several reports, several agents, one project:** one server serves the
whole `output/` tree and one review session spans it — every report page
gets the overlay, and batches are tagged with their report's slug. When more
than one agent works the same project, each MUST poll with
`--report <slug>` (and reply with `--report <slug>`): a slug-scoped poll
drains only that report's batches, so agents never steal each other's
feedback. A slug-less poll drains everything — fine only when you are the
only agent. Different projects are automatically separate: each output
directory gets its own server on its own port (discovered via
`/_fw/server.json` identity on ports 8050–8069), with fully independent
review state.

The user can also send element-free chat messages; they arrive as a batch
whose `items` list is empty and whose message is the `note`. And on Chart.js
charts they can point at the data itself — Alt-click picks the nearest data
point, dragging marks an x-range — which arrives as a `data` field on the
item (`chart-point`: series/x/value; `chart-range`: x_from/x_to plus
per-series n/min/max and the points). That is a data investigation, not a
styling request: answer it with `trellum query` against the exact dates
before touching any code. Everything is served from the normal `trellum
serve` server — review endpoints live under `/_fw/review/`, loopback-only,
and the on-disk HTML is never modified (the overlay is injected into the
served copy only).

<!-- topic: portal -->
## The portal loop (when `.mcp.json` names a `trellum` server)

A repository wired with `trellum setup portal` publishes to one studio on the
portal, and that studio is reachable as MCP server `trellum`: the same tools
the portal's own assistant has, under the key owner's role. The key is
`TRELLUM_API_KEY` in `.env`; the config files only reference it. Never paste
it anywhere else, and never put a password, key or token into a tool call.

The loop, in order. Local work first — nothing on the portal changes until
the repository does:

1. **Edit** `reports/<slug>/` and build it:
   `python -m trellum.run reports/<slug> --no-serve`.
2. **Validate**: `python -m trellum validate reports/<slug>` — no FAILs.
3. **Commit and push.** The portal fetches the branch it is configured for;
   it never reads your working tree.
4. **`check_repo_changes`** — what the portal would publish: the remote head
   it last saw and when (`remote_checked_at`), the reports added, modified and
   removed since the last publish, data-source declarations that changed, and
   the last error. In `auto` publish mode every fetch publishes itself and
   nothing stays pending; in `manual` mode this is the review step. Say what
   is pending in one line before asking to publish.
5. **`publish_repo_changes`** (manual mode) — publishes at the reviewed head.
6. **`run_report`** queues a build; **`get_report_details`** reads the result:
   last run, status, and the validator summary (fail / warn / suppressed) as
   the portal saw it. A FAIL here is the same FAIL `trellum validate` shows
   locally — fix it in the repository, not on the portal.
7. **Data-source failure** (a build that fails in the driver, or a source the
   portal marks blocked): **`test_data_source`** re-runs the connection test
   and reports the detail line. If credentials are missing or wrong,
   **`configure_data_source`** does not take them — it returns the portal's
   Configure link, and a person types the secret there. Tell the user which
   source, hand them the link, then re-run the test.
8. **`create_alert`** when the user wants to be told about something: a
   report, plain-language instructions ("tell me if APAC DAU drops sharply"),
   a trigger (after each build, or a schedule) and recipients. `update_alert`
   changes one. The portal's own agent decides at each run whether to alert.

Scope: `check_repo_changes`, `list_reports`, `get_report_details`,
`query_report_data` and `read_doc` work on a `read` key.
`publish_repo_changes`, `run_report`, `test_data_source`,
`configure_data_source`, `create_alert` and `update_alert` need `write` scope
*and* the organization's actions switch on; the harness asks before each
call, and every call is audited under the key. A viewer gets the read tools
whatever the key's scope; `check_repo_changes` needs the developer role.

The portal never edits the repository, and neither should you through it:
a fix that belongs in `report.yaml`, a suppression or `metrics.yaml` is a
commit. A missing secret is never worked around by asking for it in chat.

<!-- topic: validation -->
## Validator response protocol (mandatory)

Every report run executes `trellum/validation/` post-generation. Output goes to console + `output/<slug>/_validation.json`.

A report task is **not done** until you have:

1. Read the validator output.
2. Surfaced every **FAIL** and **WARN** to the user with check ID, affected component/section, and a concrete fix.
3. **Fixed every FAIL.** WARNs should be fixed unless intentionally accepted (then suppress in `report.yaml` under `validation.suppress` with a one-line reason for why).
4. If the report has zero FAILs and zero WARNs, said so explicitly.
5. **Handed the user a clickable link.** The last action before reporting done is `python -m trellum serve --background` — it starts (or reuses) a detached server, prints the URLs, and returns immediately — and your final message to the user must contain the printed report URL. A finished build the user cannot click is not finished. (Keep using `--no-serve` for the build/iterate loop itself; the link is the closing move, not a per-build cost.)

Severity:

| Level | Action |
|---|---|
| **FAIL** | Must fix. Structural bug → broken rendering, missing interactivity, wrong data. Common: missing `dataset_id`, orphan FilterBar, missing DataFrame columns. |
| **WARN** | Fix unless accepted + suppressed. Common: non-date x-axis with annotations enabled (silently skipped), hardcoded colors (breaks on theme switch), missing `renderAll` in custom JS. |
| **INFO** | No action required. Just confirm intent. |

Full check list, suppression syntax, and how to add new checks: `trellum/README.md` "Validation".

### Filter coverage check

The validator emits **`chart-filter-coverage`** (WARN) for every chart whose `dataset_id` is not reached by one or more FilterBar filters — the most common silent bug, where users see filter dropdowns that change nothing on certain charts. The fix is almost always to extend `FilterBar.propagate_to` with the missing `{src_col: tgt_col}` entry.

A sibling check, **`chart-value-grain-mismatch`** (WARN), catches a subtler failure mode: the chart's value column is constant within each x-axis value (e.g. a daily total merged on `event_date` only into a date × audience × spender DataSource). Filters reach the DataSource structurally, but the chart's plotted value never changes — ratios stay constant, absolutes inflate by the count of replicated rows. Fix by extracting the column to its own DataSource at native grain (date-only) and filtering by `event_date` only there.

When a filter genuinely cannot apply to a DataSource (its SQL doesn't carry that dimension and adding it would require fictional attribution — e.g. attaching a `template_type` to a purchase event), accept the gap **per (DataSource, filter_column)** in `report.yaml`:

```yaml
validation:
  accept_inactive_filters:
    tc_rev_gop3:
      - template_type   # purchases don't carry a template_type — see queries.py:156
    tc_boost_gop3:
      - template_type   # boosters live in economy_balance, no template dim
      - chest_tier
```

This is the **preferred** form because future dead filters on the same DataSource still fire — only the explicitly-listed columns are silenced. The broader `validation.suppress_per_dataset: {ds: [chart-filter-coverage]}` swallows ALL future inactive filters on that DS and should be reserved for DataSources that are fundamentally non-reactive (tiny static rollups feeding one chart).

Full per-chart, per-filter matrix lives at `output/<slug>/_details.json` (also embedded in `_meta.json` under `details.filter_matrix`) and can be rendered as a coverage table. `details.totals` carries dataset count, chart count, total row count, total serialized data size — so you can see at a glance how heavy a report is. There is also a `data_source: "real" | "mock"` flag and an amber banner when the run was a mock/test build, so synthetic row counts don't get read as production reality.

With the health view enabled (`?health=1` in the URL, or an embedding application that reports the viewer as an admin), the rendered report itself shows a small **filter-health badge** above each chart whose dimensions are not all reactive — a red `⚠ N/M filters dead` chip that pops a styled per-filter detail panel on hover. Hidden by default for non-admins so end-users see a clean report.


### Suppression hierarchy

Three layers, narrowest → broadest:

```yaml
validation:
  # Granular, PREFERRED — accept specific (DataSource, filter_column) pairs
  # as inactive. Any OTHER inactive filter on the same DS still fires.
  accept_inactive_filters:
    pf_country_gop3: [platform]
    pf_cz_gop3: [platform, spender_tier]

  # Per-DataSource — silences the named check ID for ALL filters on that DS.
  # Use only when the DataSource is fundamentally non-reactive across every
  # dimension (tiny static rollup feeding one chart).
  suppress_per_dataset:
    economy_test_net: [ds-no-filterbar]

  # Global — silences every instance of the named check ID across the report.
  # Last resort; use for systemic exemptions (e.g. annotations-non-date-x-axis
  # on a report that is intentionally month-bucketed).
  suppress:
    - some-systemic-check
```

Suppressed checks are NOT removed from the result — they're flagged with `suppressed: true` and grouped under **Suppressed** with rationale text. The matrix renders suppressed inactive cells as `✗ⓢ` so a reviewer can audit later. Always pair a suppression with a `# rationale:` YAML comment naming the specific reason.

## After framework changes (only if explicitly asked)

If the task explicitly requires modifying `trellum/*`:

1. **Add or update validation checks in `trellum/validation/`** for the new behavior — non-negotiable. Skipping silently breaks future reports.
2. Run unit tests:
   - `python3 -m pytest trellum/testing/test_components.py -v` — component changes
   - `python3 -m pytest trellum/testing/test_js_runtime.py -v` — JS runtime changes
   - `python3 -m pytest trellum/testing/test_interactions.py -v` — theme / rendering changes
3. Rebuild a couple of this project's existing reports to verify nothing broke —
   pick ones that between them cover charts, KPIs, filters and any RawHTML:
   ```bash
   python3 -m trellum.run reports/<slug> --no-serve
   ```
4. For broader coverage, `python3 -m trellum.run --all --no-serve`.

## This framework ships from the monorepo

The framework lives at `trellum/` in the Trellum product monorepo and is also
published as a standalone wheel. It is not a separate repository or submodule,
so framework changes use an ordinary monorepo branch and commit.

The control plane and framework share one version:
`trellum_portal/__init__.py` and `trellum/__init__.py` must agree. After changes
merge to `main`, an annotated `vX.Y.Z` tag starts the release workflow, which
checks both versions, publishes the product images, and creates the GitHub
Release. Do not create releases manually or maintain a moving release branch.
The framework workflow builds and tests the standalone wheel; publishing that
wheel to PyPI is a separate protected dispatch at the immutable release tag.

The complete versioning rules, checks, and release procedure are in
[`RELEASING.md`](RELEASING.md). Read it before tagging a release or changing
either `__version__`.

## What NOT to do

- **Do not edit `trellum/*` while building a report.** Reports only touch `reports/{slug}/`. If a task seems to require framework changes, stop and discuss scope with the user — framework changes affect every report.
- **Do not run `--all` to verify a single change.** Build only the affected report (`python3 -m trellum.run reports/<slug> --no-serve`).
- **Do not start the preview server to check whether a build worked.** The build prints its own result and the validator writes `output/<slug>/_validation.json`. A bare `trellum.run` blocks until killed; `--no-serve` exits.
- **Do not write custom Chart.js code for any chart type listed above.** Use the framework component.
- **Do not hardcode colors.** CSS variables for HTML/CSS, `getThemeColors()` for Chart.js.
- **Do not import Chart.js or other JS libraries.** Bundled in `trellum/static/vendor/`.
- **Do not write connection / credential code.** Use `ctx.get_connection()`.
- **Do not use Chart.js native `legend: { display: true }` on non-doughnut charts.** The `fwLegend` plugin handles it.
- **Do not open `output/<slug>/index.html` as `file://`.** `data.json` is fetched via XHR and requires HTTP — the runner serves automatically on :8050.

## Licensing (applies to every change)

Owned Trellum code is AGPL-3.0-only. Third-party and pre-existing contributor
notices retain their own terms.

- **Preserve copyright, modification, and licence notices.** `LICENSE` governs
  owned code; generated reports carry the runtime notice and exact source link.
- **Adding a dependency means updating `THIRD-PARTY.md` in the same commit.**
  `docs/LICENSING.md` has the audit command. A new licence still needs review
  because it can change what a downstream user may do with a build.
- **Bundling is the stricter case than depending.** Code added under
  `static/vendor/` ships inside every copy of this repository, so its notice
  travels with it — record it in `static/vendor/MANIFEST.json`.
