# Framework Migration Guide

Each section documents a breaking or significant change, how to detect affected
code, and the exact replacement.

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
