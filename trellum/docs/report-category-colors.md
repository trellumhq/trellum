# Declared category colors

`LineChart`, `StackedBar` and `DoughnutChart` accept keyword-only
`category_colors=None`. Omit it to retain the existing color behavior and
payload. It assigns existing theme palette slots to explicitly related category
values; it changes no quantities, sorting, stacking, normalization, axes,
dimensions, CSV or controls. Other chart families do not support this option.

```python
acquisition = {
    "domain": "acquisition",
    "column": "channel",
    "slots": {"UA": 0, "Retargeting": 2},
}
LineChart(spend, "day", "spend", "Spend", dataset_id="spend",
          stack_by="channel", category_colors=acquisition)
LineChart(revenue, "day", "revenue", "Revenue", dataset_id="revenue",
          stack_by="source", category_colors={**acquisition, "column": "source"})
DoughnutChart(snapshot, "channel", "spend", "Snapshot", static=True,
              category_colors={**acquisition, "overrides": {"UA": "#b1457b"}})
```

A domain is an author-declared semantic identity, independent of column names
and captions. Within one `RenderContext`, all declarations for the same domain
must have identical category/slot definitions, regardless of dictionary order.
Column names and per-chart overrides can differ. Conflicting definitions raise
`ValueError`; use a separate domain for a different category meaning. Domains
are independent: to give unrelated `Other` categories different colors, choose
different slots explicitly. No globally unique colors are invented.

The declaration has only these fields:

| Field | Contract |
| --- | --- |
| `domain` | Required nonempty string. |
| `column` | Required nonempty source-column string. |
| `slots` | Required nonempty mapping of string category values to non-boolean integer indices `0..2147483647`. An empty category string is allowed. |
| `overrides` | Optional mapping of a subset of declared categories to six-digit `#RRGGBB` colors. An override wins over its category's theme slot on this chart. |

Mappings can be reused: rendering copies them and never mutates caller data.
Invalid declarations fail at construction; rendering checks again and validates
applicability after dataset binding has been supplied. Explicit `dataset_id`
continues to take precedence over `static=True`. This option does not add
inferred dataset binding.

## Supported paths

For a dataset-bound `LineChart` or `StackedBar`, `column` must be a configured
`stack_by` or a nonempty `stack_by_options` value. Colors apply only while that
column is the active split. A different breakdown or Total keeps the existing
color behavior. Split ratio lines and diverging stacks use the original raw
category identity, including when series captions also contain metric names.
Line overlays retain their own palette path. Synthetic long-tail `Other (n)`
buckets keep their existing fallback even when the same text is declared.

Static wide-column LineChart and StackedBar inputs are series columns rather
than category rows; they reject this option with an explanation. Legacy static
`stack_by` is not reinterpreted. Existing static `LineChart.colors` remain
authoritative across theme switches. Dataset-bound `LineChart.colors` are
still omitted by the existing dataset path; use named `overrides` for mapped
categories. This option does not change ComboChart `line_colors` or supplied
Treemap colors.

For a static or dataset-bound `DoughnutChart`, `column` must equal `label`.
Color membership uses raw category values rather than row or legend position.
Static duplicate category rows keep their separate arcs and CSV rows, with the
same mapped color. Dataset-bound doughnuts retain their existing grouping.

Only declared **string** observations are mapped. Numeric `1` is not string
`"1"`, and missing values are not `""`. For bound charts, each redraw inspects
raw rows once. If coerced numeric/null observations share a legacy grouped
label with a string, the whole group keeps its fallback. Unknown categories
also keep the existing fallback; their colors are not guaranteed stable across
filter changes. The legacy aggregation layer's inherited-key and `||`
delimiter limitations remain; collision-safe color maps do not repair grouping.
Hostile keys such as `__proto__`, `constructor` and `toString` are supported by
the color resolver and static doughnut path.

## Themes and lifecycle

A normal slot resolves to the current theme's
`chart_colors[index % len(chart_colors)]`. Theme switches change those palette
colors; explicit overrides remain fixed. Finite palettes repeat, including
when more than ten categories are declared. Mapping survives filter removal
and restoration, lazy reveal, redraw and point hover/exit. Static doughnuts
carry their raw mapping keys through the shared theme path; no ordinary line/
bar renderer is needed in a doughnut-only report.

Rebuild reports with matching framework assets to use this additive option.
See [migration guidance](MIGRATIONS.md) and the deterministic category-color
examples in `demo/reports/component-showcase/examples_category_colors.py`.
