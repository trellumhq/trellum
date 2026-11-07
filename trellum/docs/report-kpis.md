# KPI components

`KpiCard` renders one static metric. `KpiRow` groups static cards or computes
values from a registered dataset. `MiniKpi` renders a compact secondary metric.

```python
from trellum.components import KpiCard, KpiRow, MiniKpi

KpiCard(label="Revenue", value=1250345, format="currency")
KpiCard(label="Players", value=8400, alignment="center")
KpiCard(label="Full total", value=1250345, format="number",
        full_number=True)
KpiCard(label="Precise ratio", value=1.23456, format="ratio", precision=3)
MiniKpi(label="Conversion", value=0.0842, format="percent", stacked=True)
```

`alignment` accepts `"left"` (the default) or `"center"`. The option is
keyword-only. `MiniKpi` is inline by default; `stacked=True` places its label
above its value. KPI cards keep their standard height and row arrangement;
supplied comparisons use a subtle pill within the existing comparison line.
MiniKpi values use tabular numerals, at 14px inline and 16px stacked, while long
labels wrap. No metric icons are added.

`full_number=True` disables compact suffixes for `number`, `currency`,
`percent`, and `ratio` formats. It preserves each format's units and scaling.
`precision` is an optional integer from 0 through 12 and sets the number of
fractional digits. Without either option, the existing formatter is used.
Precision alone retains compact suffixes: a number of 12,345.67 with
`precision=2` displays `12.35k`; add `full_number=True` to display `12,345.67`.
These options are keyword-only on `KpiCard` and `MiniKpi`; in a live `KpiRow`,
put them on each KPI definition. Unsupported formats and invalid precision
values raise `ValueError`.

An interactive row uses its dataset's current filtered records:

```python
KpiRow([
    {"label": "Revenue", "agg": "sum", "column": "revenue",
     "format": "currency", "full_number": True},
    {"label": "Average order", "agg": "ratio", "numerator": "revenue",
     "denominator": "orders", "format": "currency", "precision": 2},
], dataset_id="sales", mobile_collapse=4)
```

`mobile_collapse` defaults to `0`, leaving every metric visible. A positive
threshold hides later cards on narrow screens and adds an accessible expand /
collapse button. The filtered row retains its expansion state as its values
update. Existing ratio and percent aggregation contracts are unchanged.
