# Optional DoughnutChart header summaries

`DoughnutChart` accepts keyword-only `header_summary=None`. Omission preserves
baseline payloads, markup, spacing, ring/legend dimensions and CSV. Enable a
right-aligned header above the existing ring/legend with an explicit declaration:

```python
DoughnutChart(
    facts, "acquisition", "amount", "Acquisition units", dataset_id="acquisition",
    header_summary={"stat": "sum", "scope": "all_categories"},
)
```

The component's title still belongs to an enclosing Panel or section. This option
does not add an internal title. `header_context` and `center_total` are independent;
either, both or neither can be used with a header summary. The new header adds
height only when enabled, wraps within narrow panels, and uses theme text roles.
Labels and context render as text. There is no new footer or workspace marker.

| Field | Contract |
| --- | --- |
| `stat` | Required: `sum`, `max` or `count`; never inferred. |
| `scope` | Required: `"all_categories"`. |
| `label` | Optional nonblank text, at most 60 characters. |
| `format` | Optional `number`, `currency`, `percent`, `chips`, `ratio` or `plain`, subject to the statistic's guards below. |

Unknown keys, malformed types, unsupported statistics and scopes, and an empty
dict are errors at construction and rendering. Report validation catches
post-construction declaration mutations with `chart-summary-invalid`. Existing
positional constructor arguments are unchanged. This API is separate from
Bar/Area's `plotted_series` declarations and does not accept `series`.

## Entry population and arithmetic

The visible scope always reads **All categories in current result**, even with an
authored label. Its accessible description explicitly includes hidden entries.
It describes current supplied or report-filtered delivered rows, without claiming
a warehouse-wide total or completeness beyond those rows. Legend buttons change
only arc visibility, never the summary population. Filtering redraws from the
current delivered population; theme changes, re-entry and native capture use the
same renderer. No extra subscriptions or global listeners are added.

Static data preserves every category entry in source order, including duplicate
labels: UA 10, UA 20, Organic 30 yields sum 60, max 30, count 3. Bound data retains
the existing report-filtered `groupBy` category aggregation, sorting and string
key/type behavior: the same three source facts group to UA 30, Organic 30 and
count 2. Numeric and text labels with matching legacy string keys may group
together; this option does not change that behavior. Count means category entries,
not distinct category names or input source facts.

- `sum`, default label **Total**, requires author-supplied complete, disjoint,
  additive, nonnegative amounts. The declaration chooses a statistic; it cannot
  prove those business assumptions. Sum is rejected if either the chart
  `value_format` or the resolved summary format is `percent` or `ratio`.
  A format override cannot bypass that guard.
- `max`, default label **Largest category**, is the greatest current
  category-entry amount, rather than that entry's name. It may format a rate,
  but does not sum percentage shares. There is no mean or last statistic.
- `count`, default label **Category entries**, is independent of amount
  completeness. It defaults to integer `number` formatting regardless of chart
  units or precision. Only `number` and `plain` overrides are allowed. An empty
  result has an explicitly described entry count of zero.

For sum/max, an override wins over chart `value_format`, and chart `precision`
applies. Formatting follows the current doughnut number/currency/percent/chips/
ratio/plain semantics, locale and large-number bounds. Formatting cannot restore
precision already lost in delivered JavaScript numbers or upstream aggregation.

## Missing, zero, signed and overflowing amounts

Sum/max inspect original bound inputs **before** legacy groupBy replaces missing
amounts with zero. Any null, missing, nonnumeric, boolean, nonfinite or signed input
withholds these summaries with a dash and brief reason. Nonfinite grouped amounts
also withhold them. Static serialization can normalize nonfinite values to null;
both remain ineligible. No feature can recover missingness, precision or
completeness already lost before delivery.

An overflowing combined sum withholds sum. Max remains available if every original
input and grouped entry is finite and nonnegative, even when their combined sum
overflows. Empty results withhold sum/max with a no-categories reason. Complete
all-zero results show zero for sum/max; count retains every entry, even though the
existing percentage shares are undefined. Count is available with missing or
signed amounts and explicitly says it is independent of amount completeness.

The feature preserves the existing arcs, legend values/shares, center-total
eligibility and CSV arithmetic, including legacy coercion and grouping. It does
not repair invalid part-to-whole data, replace amounts, take absolute values,
change live-query admission or capture gates, or recompute arc values.

## Exports and examples

Native section PNG and report PNG/PDF captures include enabled headers and current
filtered data through the ordinary capture path. Default-off captures retain the
baseline output. Actual CSV downloads retain the `label,value` schema and all
current category entries, including hidden ones; summaries add no metadata rows.

`examples_doughnut_summaries.add_sections(ctx)` contains four deterministic named
sections: a static off/on pair with center totals off; a twelve-fact filtered
sum/max/count example; an independent filtered combination with center totals and
context; and a static duplicate-label example. Both filtered sections own their
native DataSource IDs and controls. For the twelve-fact fixture, overall
sum/max/count is 120/90/3; iOS day 1 is 20/12/3; Android day 2 is 40/36/3;
RT day 2 is 0/0/1. All amounts are explicitly synthetic additive units.

Related guides: [Doughnut rendering and population](report-doughnuts.md),
[Bar/Area summaries](report-chart-summaries.md),
[Chart context](report-chart-header-context.md).
