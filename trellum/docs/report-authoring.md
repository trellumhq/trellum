# Choose a report by the question

Use this before writing a generator, together with `python -m trellum guide
queries format`. `python -m trellum guide components` supplies the native
inventory and current constructor signatures. Choose a component and its data
contract first; follow the linked reference only for the option you need.

## Establish the facts and interaction

- Name the question, reporting dates, units and each dataset's grain. Keep
  filterable dimensions in rows. One grain gets one DataSource; synchronize
  shared dimensions across grains with FilterBar `propagate_to`, rather than
  joining monthly totals onto daily facts.
- Check `python -m trellum metrics` before defining a business KPI. Preserve
  additive numerator and denominator columns for rates; an overall rate is a
  ratio of sums, never a sum or unweighted mean of row rates. Distinct people,
  balances and cumulative values are not automatically additive.
- Declare which views follow report filters and which are fixed evidence.
  Use `dataset_id` for reactive consumers and `ScopedDataSource` for local
  filters on shared rows. A standalone Dropdown does not filter datasets.
- Preserve measured zero separately from absent or unknown input. Existing
  bound sum reducers can zero-fill or produce partial subtotals. Presentation
  options and header summaries cannot recover that lost distinction.

For interactive charts, declare DataSource and FilterBar before consumers.
For an independent static chart, omit `dataset_id` and use `static=True` where
supported. Most chart paths give explicit `dataset_id` precedence; CohortTable
rejects combining it with `static=True`. Static and bound paths can
have different aggregation, ordering and formatting. Inspect the chosen
family's contract rather than treating them as interchangeable.

## Match the view to the decision

| Reader's question | Native choice | Choose deliberately |
|---|---|---|
| What is the selected total or weighted rate? | `KpiRow(dataset_id=...)` | Claim registered metrics. Use raw numerator/denominator for `agg="ratio"`. A standalone `KpiCard` is a supplied snapshot. [Formatting and alignment](report-kpis.md) |
| What changed over time? | `LineChart` | Use `stack_by` on long rows for category lines and `ratios` for rates. Compact plots suit comparable small multiples. [Points and curves](report-line-styles.md) |
| Which category is largest? | Horizontal sorted `BarChart` | Bound ratios retain raw inputs. State Top-N/Other scope. Horizontal category value columns are automatic; `show_values=False` opts out. [Bar labels](report-bar-labels.md) |
| How do additive parts change over time? | `StackedBar` or `AreaChart(stacked=True)` | Parts must be additive and nonoverlapping. Stack totals/percent labels opt in with `show_totals` / `show_percentages`; see [eligibility](report-bar-labels.md). Unstacked areas show independent series; [never stack rates](report-area-charts.md). |
| What is the composition of one population? | `DoughnutChart` | Supply compatible parts. Opt into `center_total`, units and precision when a total is meaningful. Name its surrounding Panel/section: its title is not an internal heading. [Population and display](report-doughnuts.md) |
| How are two differently scaled measures related over time? | `ComboChart` | Declare bar and line units separately; a shared date does not make two axes comparable. [Combo choices](report-combos.md) |
| Where are concentrations across two dimensions? | `HeatmapChart` | Cells aggregate supplied coordinates. Missing coordinates are not evidence of zero. Explicit linear/log/row-normalized scales answer different questions. [Scale and labels](report-heatmaps.md) |
| How do supplied observations relate? | `ScatterChart` | Points/bubbles describe delivered observations. Optional `trend_line` fits each selected series; bubble sizes do not weight the fit. [Native trends](report-chart-trends.md) |
| How large are supplied stages? | `FunnelChart` | Stage totals do not establish distinct-person conversion or complete journeys. |
| How do additive leaf amounts divide hierarchically? | `TreemapChart` | Parent rectangles and labels are not additional facts. [Leaf population](report-treemaps.md) |
| Which paths or adjacent transitions were supplied? | `SankeyChart` | Choose `paths` or `edges`; adjacent edges cannot establish complete journeys. [Input and population](report-sankey-charts.md) |
| Which records support the chart? | `DataTable` | Opt into sorting, row choices, totals, pinning or row drill-down as needed. Local search changes the table, not neighboring metrics. [Table choices](report-data-tables.md) |
| Which dimensions should the reader pivot? | `PivotTable` | Existing missing-as-zero behavior remains; `freeze=True` adds a local scroller. [Pivot choices](report-pivots.md) |
| Which additive child facts explain a parent? | `HierarchyTable` | Supply unique leaf fact identities, never parent summaries mixed with leaves. [Hierarchy contract](report-hierarchy-tables.md) |
| Which cohort cells are known or mature? | `CohortTable` | Supply the cells and their explicit state; do not infer cohort maturity from null values. [Cohort contract](report-cohort-tables.md) |
| How do periods compare? | `ComparisonTable` for supplied results; `PeriodComparison` to calculate eligible snapshot comparisons | The registrar requires complete daily snapshots and explicit periods, metrics and direction. It is not an unrestricted live-query calculator. [Comparison calculation](report-period-comparison.md) |
| How should several filtered summary panels share one calculation? | `MetricView` + bound `MiniKpi` / `DataTable` | Use bounded ordinary unchunked snapshots, explicit groups and additive/ratio inputs. Static MiniKpis never bind automatically. [Filtered metric views](report-metric-views.md) |

## Compose an overview before adding detail

Start with a shared filter bar, a few headline KPIs and compact daily trends.
Use `Grid(columns=3, min_width=280, children=...)` for comparable tiles; use
`Panel`, `SplitPane`, `TabGroup` or a collapsible `Section` for supporting
detail. These are native layout choices, not a requirement to give every
report the same shape. [Overview example](report-overview-composition.md)
shows weighted rates, scoped metrics and detailed sections together.

Grid keeps titles and local controls visible by default. Omit them explicitly
only when a Panel heading or shared control supplies the missing context.
LineChart `height="balanced"` is 220px; `"compact"` is 160px and `"roomy"`
is 320px. Other families have their own sizing. [Layout choices](report-layouts.md)
and [controls](report-controls.md) describe responsive behavior and lazy tabs.

Repeated categories and distinct single-series tiles have different color
needs. To make the same channel retain its color across supported bound
LineChart/StackedBar splits and static or bound doughnuts, declare one
`category_colors` domain with the same explicit category-to-slot mapping on
each chart. Unknown categories retain fallback colors; finite palettes repeat.
This option does not assign different colors to unrelated metric tiles, and
static wide line series reject it. For distinct single-metric Line/Area/ordinary
Bar tiles, opt into `Grid(..., color_cycle="theme")`. Authored tile order fixes
each palette position across filtering, wrapping and theme changes. Existing
grids keep their colors by default; nested grids choose independently and only
direct eligible chart children receive a slot. Category splits, multi-series,
stacked and outcome colors keep their own meaning. See [Grid colors](report-grid-colors.md).
For an intentional fixed LineChart color, use its explicit `colors` list.
Unsplit bound value/ratio series use matching list positions; omitted or empty
lists and missing entries retain theme fallback.
Explicit colors persist through filtering and theme redraws. An active category
split keeps its category color rules and ignores the series list. Do not use
different category domains to disguise metrics as categories.
[Category color contract](report-category-colors.md)

## Add context only when its meaning remains true

| Need | Default and opt-in |
|---|---|
| Units, denominator or fixed scope below a chart title | `header_context=None` by default. Author plain text that stays true under filters; it never derives selected dates. [Header context](report-chart-header-context.md) |
| One headline statistic beside a chart | `header_summary=None` by default. Select the family's explicit population and statistic; examples below are not interchangeable. |
| Explain source freshness | Use the report's native freshness/header metadata (`python -m trellum guide header`). Query completion and selected dates are not source freshness. |
| Explain a date selection | Use native FilterBar `date_range`; its presets use the latest recorded date, inclusive endpoints, and no continuous-coverage promise. [Date ranges](report-date-ranges.md) |
| Explain missing supplied line observations | Opt into `series_availability`, naming the underlying metric or both ratio inputs. Incomplete supplied points become gaps; no dates are invented. Dynamic splits are unsupported. [Availability](report-line-availability.md) |
| Explain unavailable or immature KPI values | Opt into KPI `availability` / `maturity` / `comparison`, with explicit inputs, units and horizon evidence. Omission retains legacy reducer behavior. [KPI states](report-kpi-states.md) |
| Show pending, empty or failed line results | Ordinary LineChart defaults to `result_presentation=True`. Keep its native messages; they do not certify source coverage. [Result presentation](report-line-results.md) |
| Annotate dated events | Ordinary LineChart defaults to `event_labels="adaptive"`: an in-plot marker per event, names beside markers only where they fit, and exact names/dates from selection or an "All N events" list. `"grouped"` preserves numbered markers; `"full"` preserves in-plot labels with crowding protection. [Events](report-line-events.md) |
| Identify daily calendar months | `month_context=False` by default. Opt in only for canonical daily dates, never weekly observations with date-shaped labels. Missing days remain compressed. [Month context](report-month-context.md) |
| Compare daily values with their monthly observed-day mean | `monthly_average=None` by default. Requires one additive daily line/vertical bar, explicit bounds/unit and `unique` or `sum_complete` input contract; static requires `unique`. It reports selected/observed days and excludes unknown days rather than filling them. [Monthly averages](report-monthly-averages.md) |
| Explain calendar bucket coverage and a supplied cohort horizon | Opt into `CalendarCoverage` and eligible BarChart `bucket_context`. Author bucket keys on original facts. Coverage and maturity follow explicit calendar/input/horizon declarations; bucket values are unchanged. [Bucket context](report-kpi-buckets.md) |

Summary declarations describe the current result population, which can differ
from the whole source or currently visible pixels:

| Chart family | Summary scope to read before choosing a statistic |
|---|---|
| Line | Existing `header_summary` selects a plotted series (`stat`, `series`, optional label/format). An arithmetic mean of daily ratios is unweighted. |
| Bar / Area | [`plotted_series`](report-chart-summaries.md), after existing sort/Top-N/Other; Area selects a raw band. |
| StackedBar | [`absolute_series`](report-stacked-summaries.md), before percentage normalization; zero-filled slots remain zeros. |
| Combo | [`axis_series`](report-combo-summaries.md), naming both axis and exact series caption. |
| Doughnut | [`all_categories`](report-doughnut-summaries.md), including legend-hidden categories. |
| Heatmap | [`current_cells`](report-heatmap-summaries.md), aggregated coordinate entries before color normalization. |
| Funnel | [`current_stages`](report-funnel-summaries.md); stage, maximum or count, never summed stage population. |
| Scatter / Treemap | [`point_entries`](report-scatter-summaries.md) / [`leaf_entries`](report-treemap-summaries.md), using each family's admission rules. |
| Sankey | [`complete_paths` or `adjacent_boundary`](report-sankey-summaries.md), selected by input mode; never sum all boundaries. |

Do not copy a summary dictionary between families without checking its scope,
allowed statistics and units. A plotted mean is not a weighted overall KPI;
legend hiding and zoom generally do not narrow these summary populations.

## Trend, smoothing and forecast are separate claims

To describe a linear direction, opt into `trend_line={"method": "linear",
"series": "each"}` on ordinary LineChart or numeric ScatterChart. Native fits
recompute after report filters and existing aggregation, independently per
selected source series. Line dates must form a regular daily UTC grid; Scatter
uses finite coordinate pairs. Daily ratios and bubble points are unweighted.
Use exact series identities when only specific measures should be fitted.
The overlay stops at the observed range, retains source colors, and can be
hidden without changing the plotted observations, summaries or CSV.

Read [native trend eligibility](report-chart-trends.md) before enabling it:
fits need at least three eligible observations and two distinct x values;
forecast and unavailable inputs are excluded. Live, chunked and derived sources
are unsupported. Per chart, limits are eight selected series, 50,000 candidate
pairs and 50,000 raw contributors; an unavailable reason replaces the fit
instead of silently sampling. A high R² does not establish causation or predict
future performance. Existing charts keep `trend_line=None`.

The straight default (`tension=0.0`) and opt-in cubic curves (`0..1`) change
connections between observations. Curves can overshoot; they are not a moving
average, regression or forecast. Monthly observed-day average guides are
piecewise monthly means with explicit coverage, not a fitted trend.

`forecast_series` only styles explicitly supplied series with dashes and hollow
markers. It never extrapolates, anchors or estimates values. Static null gaps
are preserved; bound sum paths need explicit availability rules or complete
supplied values to avoid legacy zero-fill. Forecast styling does not repair
missingness. [Supplied forecasts](report-line-forecasts.md)

## Verify the reader's result

Build with `--no-serve`, run `trellum validate <dir>`, and compare actual values
and exports before/after representative filters. Check zero, no match, unknown
inputs, ratio denominator failure and fixed-versus-reactive scope. Inspect the
rendered report on desktop and mobile, including wrapped context and controls.
Use native report/section capture when context outside the canvas must travel
with an image. For a human preview, follow `python -m trellum guide review`.
