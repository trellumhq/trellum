# Declared KPI availability, cohort maturity and comparisons

`KpiCard` accepts keyword-only `availability`, `maturity` and `comparison`
mappings. Dataset-bound `KpiRow` dictionaries accept the same keys. Omit them
for existing aggregation behavior. These declarations describe supplied inputs
in the current filter selection. They do not assert remote, calendar or warehouse
coverage and do not change DataSource loading, errors, requests or retained data.
MiniKpi and the metric registry are unchanged.

## Availability

An authored state works on standalone cards, static rows and bound rows:

```python
KpiCard("Revenue", None, format="currency", availability={
    "state": "unavailable", "reason": "Revenue observation not supplied."
})
```

`state` is `available` or `unavailable`; `reason` is optional nonempty text.
`unavailable` displays an em dash. A short metric-labeled reason appears below
the real row, outside card internals, with `aria-describedby` on its card. A
standalone card has a following sibling note. Notes wrap on phones, follow
mobile card expansion, and disappear when values become available. Text is
inserted as text, including labels and reasons containing markup characters.
Valid siblings, measured zeros and count zero remain visible. Normal valid rows
reserve no note space.

For complete supplied-input semantics, use this alternative shape on a bound
row (do not combine it with `state`):

```python
{
    "label": "Revenue", "agg": "sum", "column": "revenue", "format": "currency",
    "availability": {"required_columns": ["revenue"], "empty": "unavailable",
                     "reason": "Revenue needs complete supplied observations."},
}
```

Every matching row must contain a finite numeric value in every named column.
Missing keys, null, nonfinite numbers, booleans and numeric strings fail this
check; numeric zero passes. Only declared columns are checked. `empty` is
required: `unavailable` means no observations is unknown; `zero` deliberately
allows the existing reducer's empty result. No declaration keeps existing
partial-input/subtotal and empty-sum behavior. No formatted string determines
validity. A registered empty population is treated the same as a no-match
filtered population under this explicit policy.

To require a finite, nonzero summed denominator for a declared ratio, add
`"ratio_denominator": "nonzero"` and include **both** the KPI's numerator and
denominator in `required_columns`. This option requires `agg="ratio"` and
also withholds an empty denominator, even with `empty="zero"`. Otherwise the
legacy ratio still divides by the absolute summed denominator, returns zero
for a zero denominator, and scales percent display as before. This option does
not redefine signed-denominator math, other reducers, or coverage completeness.

## Explicit numeric metadata

Observed values, supplied forecasts and comparison periods use one numeric
specification shape:

| Shape | Meaning |
| --- | --- |
| `{"value": 8, "unit": "percent"}` | Supplied 8%, not a fraction |
| `{"value": .08, "unit": "fraction"}` | Supplied fraction, displayed as 8% |
| `{"value": 42, "unit": "number"}` | Original numeric units |
| `{"agg": "sum", "column": "revenue", "unit": "number"}` | Sum of complete matching inputs |
| `{"agg": "abssum", "column": "cost", "unit": "number"}` | Existing sum of absolute inputs |
| `{"agg": "count", "unit": "number"}` | Matching row count, including valid empty zero |
| `{"agg": "ratio", "numerator": "revenue", "denominator": "cost", "unit": "fraction"}` | Ratio of period sums |

Literal values must be finite numbers (not booleans), or `None` to declare an
unavailable observation/period. Reducers require a bound row; they require all
referenced inputs to be finite numeric values. Empty sum/abssum/ratio is
unavailable, while empty count is zero. New ratio metadata supports
nonnegative denominator inputs and a finite, strictly positive summed denominator;
signed or zero denominators are unavailable. It does not reinterpret legacy
ratio reducers. All non-ratio metadata reducers use `unit="number"`; summing
precomputed percentages is not supported by this new API. Use each period's
numerator/denominator or supply an explicit already computed scalar.

These units belong only to new metadata. Existing main-value units remain:

| Existing input | Percent units |
| --- | --- |
| Standalone KpiCard or static KpiRow numeric value/forecast | Fraction: `.08` displays `8.00%` |
| Constant in a dataset-bound KpiRow (including a KpiCard object in that row) | Percentage-scale: `8` displays `8.0%` |
| Bound sum-percent value or sum/abssum-percent `delta_col` | Percentage-scale; `.1` delta displays `+0.1%` |
| Bound ratio percent value or ratio-percent delta | Numerator / absolute denominator; display multiplies by 100 |
| Currency delta | Original currency units |

Existing precision/full-number options, alignment and main-value formatting
remain. Metadata rate details display as percent; `number` details use the
card's numeric format (or ordinary number if its main format is percent).

## Cohort maturity

```python
KpiCard("D7 ROAS", None, format="percent", precision=2, maturity={
    "state": "immature", "age": 3, "horizon": 7,
    "observed": {"value": 8, "unit": "percent"},
    "forecast": {"value": .15, "unit": "fraction"},
})
```

`state` (`immature` or `mature`), nonnegative finite `age` in days, positive
finite `horizon` in days, and `observed` are required. State must agree with
age versus horizon. Immature final-horizon values show an em dash with
`Age 3d · <7d`; the two existing detail lines show `Observed D3 · 8.00%` and
`Forecast D7 · 15.00%`. `forecast` is optional and supplied: omitting it adds
no blank slot. No forecast or common age is inferred. Mature state retains
the supplied/aggregated main final-horizon value. Maturity does not itself
assert complete final-value inputs; use availability when that is required.

On bound rows, `cohort_column` is required and must identify exactly one
non-null selected cohort. Use `state_column` instead of literal `state`, and
`age_column` instead of literal `age`, for filterable source facts. Every
selected row must agree on each declared field. Empty/mixed cohorts, mixed
ages/states and inconsistent maturity show a metric reason outside the row,
without fabricating an age or a combined observation. `observed` and `forecast`
can use the same validated reducers above. Optional nonempty `reason` overrides
the generic unsupported-cohort reason. Literal state/age also require a
single selected cohort on bound rows.

Maturity owns the existing two detail lines; combining it with legacy `sub`
or `forecast` is rejected. Long details retain their full DOM text and title
while truncating visually within those lines. Put longer provenance/method
context in adjacent report prose. Card padding, 24px values, default row
columns and optional centering remain unchanged.

## Comparisons

```python
comparison = {
    "measure": "percentage_points",  # or relative_percent
    "current": {"value": .22, "unit": "fraction"},
    "baseline": {"value": 20, "unit": "percent"},
    "precision": 1,
}
```

`relative_percent` computes `(current - baseline) / baseline * 100`;
`percentage_points` computes `(current - baseline) * 100` after normalizing
rate units. Thus 20% to 22% is `+10.0%` or `+2.0 pp`, chosen by the author.
Relative comparisons allow two number-unit specs, or two rate-unit specs;
percentage points requires rates. Relative baseline must be strictly positive;
a negative baseline has an unavailable signed-baseline explanation. No signed
baseline policy or automatic period/date discovery is inferred.

For filterable rates, declare a ratio reducer for **each** period, so each
numerator/denominator is aggregated before differencing. Never sum/average
arbitrary row-level rates or relative changes for a portfolio comparison.
The optional `precision` is 0–12 (default 1). Calculate before rounding. A
rounded display zero is neutral; explicit `delta_direction="up"` or `"down"`
retains the favorable-direction override on valid comparisons.

Alternatively, `{"measure": "percentage_points", "value": 2}` supplies a
delta directly in percentage points. With `relative_percent`, `value=10`
means 10%, not `.10`. This shape excludes `current`/`baseline`. Optional
`reason` supplies short unavailable-comparison text.

Current main-value rendering is independent of comparison inputs. A null,
incomplete or empty baseline shows `— · baseline unavailable`; zero relative
baseline shows `— · zero baseline`. An invalid current comparison input also
withholds only the comparison. These neutral, icon-free explanations use the
existing comparison line; long text keeps its full DOM text/title without
increasing that line's height.

Precedence: an explicit nonempty legacy `delta` string wins over `comparison`;
otherwise `comparison` wins over legacy `delta_col`. All supplied declarations
are still validated, even if overridden. An explicit favorable-direction
choice never colors an unavailable comparison. A supplied delta string and
its explicit direction remain authored text with no calculation.

## Compatibility and examples

Unknown declaration keys, wrong types, invalid units/reducer fields and invalid
post-construction mutations fail at render/build. Unrelated legacy dictionary
keys and reducers are not tightened. Metric claims still expand and explicit
overrides win. Explicit `dataset_id` wins over `static=True`. Standalone static
examples omit dataset binding and use `static=True`.

Two narrow presentation corrections also apply to old bound rows:

- Explicit `value=None` without a reducer/column is a missing constant, shown
  as an em dash, rather than falling through to `sum(undefined)=0`. Other
  constant units, absent `value` keys and aggregation specifications retain
  their behavior. Add availability for a metric-specific external reason.
- A legacy ratio delta with a zero, absent numeric or nonfinite aggregate denominator shows a
  neutral dash/reason instead of favorable `+0.0%`. True zero auto deltas are
  neutral; explicit valid up/down overrides remain. Valid signed legacy
  baselines still use the absolute denominator. A sum delta cannot reveal its
  original baseline; the renderer does not invent one. Legacy partial-input
  aggregation is otherwise unchanged.

The deterministic `examples_kpi_states.add_sections(ctx)` module in the
component showcase contains independent observation, cohort, paired-period and
static-snapshot sections. The supplied forecasts are examples, not predictions.
Rebuild with matching assets to enable the declarations. No host, database or
metric-registry migration is required. Older runtimes ignore the additive
metadata and cannot apply its withholding semantics; deploy HTML/data/assets
from the same rebuilt report.
