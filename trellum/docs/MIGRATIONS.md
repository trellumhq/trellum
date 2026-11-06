# Framework Migration Guide

Each section documents a breaking or significant change, how to detect affected
code, and the exact replacement.

## Unreleased: ComboChart axis descriptions and grouped legends

Rebuild reports to add wrapping Left/bars and Right/lines context outside the
existing canvas. Keyword-only `left_axis_label=None` and `right_axis_label=None`
accept nonempty strings to declare units; defaults list actual series captions.
Existing positional/subclass arguments and explicit dataset binding precedence
are preserved. Same captions on opposite axes now have separate native legend
buttons; same-axis captions remain paired. Values, scales, plot heights, nearest
tooltips and raw CSV scope remain unchanged. Static format/bounds/label option
omissions remain; an axis description does not activate those options.

New `combo_axes` payload metadata scopes this behavior to rebuilt ComboCharts.
Untagged payloads and other chart families keep their legacy legends. Older
runtimes ignore the metadata safely. See [report combos](report-combos.md).

## Unreleased: HeatmapChart scales and cell values

Rebuild reports to apply the automatic single-hue palette for finite nonnegative
aggregated cells, an explicit global/relative scale key, and hatched absent
coordinates. The new keyword-only `show_values=True` option draws existing-unit
labels when measured space allows; `False` hides labels. Tooltips retain full
aggregate values. Existing positional calls and dataclass subclasses remain
compatible. Explicit palettes, normalization/logarithmic math, aggregation,
static/live precedence, category order and `drop_empty` removal rules remain.

The additive `heatmap_style="sequential-v1"`, `show_values` and static
`scale_population` metadata do not change the raw cells. Untagged payloads keep
legacy palette/label behavior; markup without a key is safe. New payloads remain
readable by older runtimes, which ignore the presentation fields. Rebuild with
matching assets to apply the presentation. No host or database migration is
required. Signed/nonfinite cells keep legacy calculations/geometry with honest
context; null coercion, category-key collisions and empty matrix CSV limitations
are unchanged. See [report heatmaps](report-heatmaps.md).

## Unreleased: TreemapChart hierarchy and optional shares

Rebuilding report treemaps adds one theme color per first-level hierarchy group,
a wrapping encoding key, fitting native-size labels and expandable full-path
details. Context adds panel height; rectangle geometry, values, spacing, borders
and existing responsive plot heights are unchanged. Explicit `color_col` keeps
its existing first-source-child behavior, including numeric row-order dependence;
the encoding note discloses this without a quantitative color legend.

Keyword-only `show_percentages=False` enables share lines only on fitting tile
labels. It must be boolean. Existing positional/subclass constructors and
explicit `dataset_id` precedence over `static=True` remain compatible. Shares
use all current supplied/filtered values, including tiny and zero categories;
empty/incomplete/signed/nonfinite or blank-hierarchy populations have unavailable
shares, and zero-total shares are undefined. Authors must supply additive values.

New metadata is additive; older payloads and wrappers remain safe. Rebuild with
the matching runtime to enable this presentation. Legacy zero/signed/blank-parent
sizing and header-only CSV remain unchanged. See [report treemaps](report-treemaps.md)
for exact values, tuple identity, color semantics and compatibility details.

## Unreleased: PivotTable presentation and optional freezing

PivotTable adds keyword-only `freeze=False` and `freeze_height=320`. The latter
must be a positive integer and applies only when `freeze=True`. Existing
positional arguments and subclasses remain compatible. The default table gains
compact spacing, grouped column-category headings, subtle striping and a quiet
grand-total band. Frozen mode uses a bounded local scroller and pins both header
rows and the first row-dimension column; other dimension columns remain
scrollable. Selectors, values, totals, aggregation and CSV scope are unchanged.
See [report pivots](report-pivots.md) for existing grouping, null and export
limitations.

## Unreleased: ordinary LineChart adaptive points and optional curves

Rebuild reports to apply persistent dots based on current plot spacing (12 CSS
pixels between connected observations). Isolated finite observations, including
zero, retain dots at any label count. All measurements, hover targets, null gaps,
CSV and existing line widths/heights remain. AreaChart and ComboChart are unchanged.

Keyword-only `tension=0.35` opts into curves; default `0.0` remains straight.
Finite numbers in [0, 1] are accepted, excluding booleans; later invalid mutations
produce `line-chart-tension-invalid`. Existing positional and subclass arguments
retain their positions. Curves can overshoot and are not averages or forecasts.

New `line_style`/`tension` payload metadata is additive. The new runtime keeps
legacy behavior for untagged payloads. Old runtimes load new payloads; rebuild
with the matching runtime to apply live curves and adaptive points. Host APIs and
explicit dataset binding precedence remain. See [line styles](report-line-styles.md).

## Unreleased: ComparisonTable presentation and declared context

Rebuilding comparison tables adds a theme-border divider before the comparison
group and emphasizes an existing `value_col`. Compact striped rows, numeric
alignment, full-population matching and raw CSV use DataTable's existing path.
Use keyword-only `show_divider=False` to omit the divider.

Existing positional constructors and dataclass subclasses remain supported;
legacy absolute comparison columns and formatted-string sign rules are unchanged.
The historically unused `value_col` is still allowed to be absent. No values are
recalculated and no automatic dates or delta units are introduced.

Keyword-only `comparison_context` opts into supplied period/change headers,
per-metric formats/direction and explanatory outcome text. Relative-percent inputs
are fractions; percentage-point inputs are already in points. Null/nonfinite
changes show a dash and a supplied reason or Unavailable. Reason columns are
hidden by default; explicitly select them to display/export them. CSV retains
selected source names and raw units, without outcome columns. Explicit conditional
formats, including an empty dict, globally suppress automatic context colors.
See [report comparison tables](report-comparison-tables.md) for the full contract.

## Unreleased: compact doughnuts and optional center totals

Rebuilding applies a compact ring and wrapping category/value/share HTML legend.
Narrow panels stack the ring above the legend, which can increase chart height
so every name and value remains reachable. This layout uses local panel width;
ordinary line-chart heights do not apply. A keyword-only `center_total=False`
option enables a labeled total without changing ring dimensions. `center_label`,
`value_format` and `precision` control its explicit units and display. Existing
positional constructor arguments and static duplicate-row grain are preserved.
Static `show_labels=True` is now honored as it already was for bound charts.

Shares and optional totals include all categories in the current supplied or
filtered result, including legend-hidden categories. Legend buttons change only
arc visibility; CSV still exports all raw chart categories and values. Native
visible-arc geometry may differ from these explicitly labeled all-category
shares. New totals/shares require complete finite nonnegative numeric inputs;
zero totals have undefined shares, and empty/incomplete/signed/overflowing
results show a dash/reason. Authors are responsible for additive quantities.
Legacy signed/incomplete arc behavior, aggregation, redraw visibility reset and
cross-filter selection-memory limitations remain unchanged.

No host, exporter or artifact migration is required. Older markup without the
new HTML legend wrapper keeps a safe native canvas legend fallback. See
[report doughnuts](report-doughnuts.md) for API, formatting and population details.

## Unreleased: DataTable search, sort and CSV population

Rebuilding applies compact spacing, subtle row shading and right-aligned numeric
columns. Existing constructor arguments and host APIs remain supported.

`max_rows` now limits display after local search and sorting. Static tables embed
every supplied row, so searches can find matches beyond the initial display and
CSV exports all matches in table sort order. For large static inputs, use the
existing bounded dataset/query mechanisms; `max_rows` is not a payload-size limit.
Dataset tables export the population last delivered to the table, including its
report filters and local search, without fetching a pending replacement result.

Static `columns` now selects displayed/exported columns, as it already does for
dataset tables. Static numeric CSV cells contain original numbers, rather than
rounded display strings. Raw numeric values determine numeric sorting; text
columns, including numeric-looking identifiers, sort as text. This can change
order previously inferred from display strings. Equal values retain source order.

Payload additions `numericColumns` and optional `rawRows` are backward compatible.
Existing generated files keep their behavior; rebuild previously truncated static
tables to include omitted rows. New code still accepts older payloads. Matching,
available and export counts describe the actual table population. See
[report data tables](report-data-tables.md) for precision and live-result details.

## Unreleased: report date range presentation

Rebuild reports to receive readable locale-aware trigger dates, paired native
Start/End date and time fields, quieter presets and explicit recorded-bound
context. Closed trigger height and existing filter values remain unchanged;
long text fits its panel. Escape closes the floating picker and returns focus.
Unavailable recorded bounds disable selection with a brief explanation.

No authoring API, host, artifact or URL migration is required. Existing
`default_days`, custom presets, inclusive ranges and timezone calculations are
preserved. Custom styles targeting the former separate time row should account
for `.fw-date-endpoint` wrappers. Date/time classes, `data-edge` attributes and
Start/End query order remain intact. See [report date ranges](report-date-ranges.md)
for API usage and pre-existing precision, timezone and open-link limitations.

## Unreleased: AreaChart fill appearance

AreaChart now renders ordinary overlapping fills at approximately 12% opacity
with the existing 2px outline. Stacked additive areas use approximately 65%
opacity and 1.5px boundaries; subsequent visible series fill to the preceding
visible series. No constructor arguments or data contracts changed. Rebuild
reports to pick up the intentional visual change. See
[`report-area-charts.md`](report-area-charts.md) for usage and unchanged
aggregation, scaling and missing-data behavior.

## Unreleased: standalone report choices and views

Rebuilding applies a labeled, searchable, wrapping presentation to standalone
`Dropdown` controls, and underlined wrapping tabs to ordinary `TabGroup`s. Short
targets are at least 36px for choices and 35px for a tab row on desktop, and
44px on phones. Empty choices and groups now explain their empty state.

Constructor positions, native select values/events, public `fw` keys and existing
state/URL parameter names are preserved. Keyword-only `label` names either
control. `mode="dropdown"` remains a selector and is now searchable;
`mode="sidebar"` is a new opt-in panel-width-responsive layout. Unknown legacy
modes continue to render tabs. Report filter dropdowns retain their presentation.

Custom selectors should account for the new `.fw-choice` label/select wrapper and
`.fw-tab-root`, `.fw-tab-nav`, `.fw-tab-panels` ownership wrappers. Tab panel
contents remain intact. Previously generated controls without the new metadata
retain native standalone selects and the older tab-selector initialization.
Find authoring sites with `rg 'Dropdown\(|TabGroup\(' reports`; see
[report controls](report-controls.md) for API and keyboard details.

## Unreleased: report chart sizes, panel-width layouts and KPI presentation

Existing constructor positional arguments remain accepted. No host API or
artifact schema changes are required. Rebuilding a report applies these
presentation changes; previously generated report files are unaffected.

- Ordinary `LineChart` plots use 220px at every viewport width. Choose
  `height="compact"` for 160px or `height="roomy"` for 320px. Optional
  `header_summary` is off by default and follows the filtered plotted series.
- `Grid(columns=...)` is now a maximum constrained by panel width. This also
  applies when `min_width` is supplied: previously automatic grids ignored
  `columns`. For an existing automatic grid intended to show four tiles across,
  use `Grid(children, columns=4, min_width=320)` explicitly. The minimum is
  preferred; narrower panels still fit one cell without overflow.
- Automatic grids keep chart titles and breakdown controls visible, and use
  the chart's own plot height. They no longer shorten non-LineChart plots to
  200px. To omit redundant context explicitly, use the keyword-only
  `show_titles=False` or `show_controls=False`; omission stops at nested Grids.
- `SplitPane` keeps each child's full output together and stacks according to
  its panel width instead of a viewport breakpoint. The existing ratio argument
  is preserved. Set keyword-only `left_min_width` and `right_min_width` to tune
  the stacking point; both default to 320px. Custom CSS that targets direct
  SplitPane children should account for the new `.fw-split-pane` wrapper.
- KPI comparisons gain a subtle background without changing the card's spacing
  or height. `MiniKpi` uses an inline label and 14px tabular value; the optional
  stacked layout uses a 16px value. KPI card alignment, full-number formatting
  and precision are keyword-only options. Omitted options retain existing
  compact formatting. `precision` alone keeps compact number/currency suffixes.
- Filtered `KpiRow` now honors an explicitly supplied `mobile_collapse`, just as
  static rows do. Omit it (the default) to keep every metric visible. Expanding
  a row retains its state through filter changes.

Find affected authoring code with `rg 'LineChart\(|Grid\(|SplitPane\(' reports`.
See [report layouts](report-layouts.md) for examples and the
`component-showcase` demo for interactive configurations. KPI row arrangement,
report filter values and date calculations are unchanged by these layout options.

## 0.9.0: pandas 3, and repeated text from `query_df` as `category` columns

pandas 3.0 is now required, and `query_df` returns a text column whose values
repeat (at most half distinct) as a pandas `category` column instead of plain
text. Values, row order and all other columns are unchanged; the column's
dtype is what changed, and a few pandas operations behave differently on it:
`.map()` followed by arithmetic, `pd.to_datetime()` followed by `<`/`>`,
`.min()`/`.max()`, `fillna()` or assignment with a new value, and `+` with a
string. Each raises a `TypeError` naming `Categorical` or `category`, and a
build that hits one prints a `[HINT]` line pointing at the guide.

**Detecting affected code:** in each report's `generator.py`, the lines that
work on a text column from `query_df`:

```
grep -nE '\.map\(|to_datetime\(|\.min\(\)|\.max\(\)|fillna\(|\+ *["'"'"']|\.unique\(\)|value_counts\(|== *object|\]\[' reports/*/generator.py
```

**The fix:** the before/after for each operation, the pandas 3 changes that
reach report code (chained assignment, `dtype == object`), and how to check
that a report's output is unchanged are in `python -m trellum guide upgrade`
(the `upgrade` section of `AGENTS.md`). `query_df(..., compact_text=False)`
returns plain text where a fix is not worth it.

---

## 0.2.0: `_meta.json` `metrics_used` entries became objects

`META_SCHEMA_VERSION` moved 1 → 2. A build's claimed metrics still live at
`metrics_used` in `_meta.json`, but each entry is now an object instead of a
bare id string, so a host can tell a claim's build-time definition apart from
the current one in `metrics.yaml` without opening `data.json`:

```jsonc
// before (schema v1)
"metrics_used": ["gross_revenue", "paying_share"]

// after (schema v2)
"metrics_used": [
  {"id": "gross_revenue", "definition_hash": "3f9a0c1e2b7d", "version": 2},
  {"id": "paying_share",  "definition_hash": "a11ce0ffee42", "version": 1}
]
```

**Detecting affected code:** search for `metrics_used` outside this repo. Any
reader that assumed a bare id list (e.g. iterating entries as strings, or
comparing an entry directly against a metric id) needs to read `entry["id"]`
instead.

**The fix:** call `trellum.meta.normalize_metrics_used(meta.get("metrics_used"))`
rather than reading the key directly. It accepts both shapes — a v1 build's
bare ids normalize to `{"id": ..., "definition_hash": None, "version": None}`
("unknown", since a v1 artifact never recorded either — nothing else to read
them from) — so one call reads a claim list correctly regardless of which
schema version produced it. This is also how `python -m trellum metrics`
itself reads old builds now.

`data.json`'s `_metrics` block (label/format/agg spec/version/definition_hash/
component_ids per claimed metric) is unchanged.

---

**Before 0.2.0 there was nothing to migrate.** 0.1.0 was the first release, so
no report written against it needed editing.

The next entry lands here when a Tier 1 surface changes — see the deprecation
process in [COMPATIBILITY.md](COMPATIBILITY.md). An entry names the version that
changed it, gives a search pattern for finding affected code, and shows the
before and after.
