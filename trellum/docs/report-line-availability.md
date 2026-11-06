# Supplied LineChart observation availability

`LineChart(..., series_availability=None)` optionally withholds incomplete supplied
observations. It is keyword-only and accepts a list/tuple of rule dictionaries:

```python
LineChart(frame, "day", "orders", dataset_id="daily_orders", y_labels=["Orders"],
          series_availability=[{
              "series": "Orders", "required_columns": ["orders"],
              "state_column": "orders_state",  # optional
              "reason": "Orders require verified supplied inputs.",  # optional
          }])
```

Each rule requires `series` (a non-bool integer position or exact displayed
caption) and nonempty, distinct `required_columns` names. Include the underlying
y column, or **both** numerator and denominator for a bound ratio. Additional
required numeric columns are allowed. `state_column`, when present, must name a
nonempty column. `reason`, when present, is nonempty plain text of at most 240
characters, rendered literally. Other keys are rejected. Rules and column lists
are copied, and instance mutations are checked again at render/build. The report
validator reports invalid mutated declarations as `line-chart-availability-invalid`.

References use the same actual captions as `forecast_series`: bound blank/short
labels fall back to source column names; static blank captions become `Series N`.
Static shortened label lists retain their existing zip behavior. Missing,
out-of-range, ambiguous and duplicate targets are rejected. A selected caption
shared by another series is unsupported because the existing legend groups it.

Ordinary static and dataset-bound lines, bound ratios, independent declarations
on several metrics and explicitly supplied forecast series are supported.
Nonempty declarations with `stack_by` or `stack_by_options` raise an error:
dynamic split identities are outside this API. `None` and empty declarations
preserve legacy combinations, payloads and spacing. Explicit `dataset_id` wins
over `static=True`. For an independent static snapshot omit `dataset_id` and use
`static=True`; ratios without dataset binding retain ordinary static y behavior.

## Inputs and plotted values

For a declared series, every supplied contributor at a supplied x identity must
contain raw finite numeric values in every required column. Booleans, numeric
strings, missing keys, nulls and nonfinite numbers are incomplete inputs. If a
state column is declared, every contributor must additionally contain exactly
the string `available`. A supplied `available` state never makes an invalid
numeric value valid. All-finite measured zero remains numeric zero.

Bound charts check the **currently delivered and filtered** rows, grouped by the
existing x identity (`String(x)`, with missing/null x mapped to the empty string).
A single incomplete contributor withholds the whole selected metric at that x,
rather than displaying a partial subtotal. Complete buckets retain the existing
sum, order and units. Nonfinite summed results are unavailable. Bound ratios
require complete numerator and denominator inputs, divide the existing sum by
sum, and retain the strictly positive summed denominator rule. Signed inputs
remain supported when their summed denominator permits division. Nonpositive or
nonfinite denominators, overflowing numerators and nonfinite results are
unavailable. Undeclared siblings retain their existing aggregation semantics.

Static charts check each source row independently, preserving duplicate labels,
row order and null gaps. They do not group, resample or filter source points.
Missing required fields withhold points. Invalid numeric scalars in declared
static series, including pandas nullable values, serialize as JSON null; finite
values remain in the payload for the runtime's state and extra-input checks.
Declared lines use `spanGaps=false`;
isolated valid markers, optional curves and forecast styling remain available.
Header summaries consume the final masked series, skipping null and including
measured zero; no new summary math is introduced.

An entirely absent date remains absent. No range, timezone conversion, calendar
filling or new categories are inferred. Empty filtered results stay empty and
have no unavailable observation or note. This declaration describes supplied
inputs in the component's delivered population; it asserts no calendar or
warehouse completeness. Loading, transport failures and retained-result states
are outside this API. The shared aggregator and undeclared chart/KPI/table
skip-null sums are unchanged.

## Context, interaction and capture

When a visible declared series has unavailable supplied points, brief
`Selected data` context follows the fixed-height plot, optional month row and
grouped event disclosure. It names the actual displayed metric, unavailable
count, up to three exact supplied labels and the remaining count. Default text
distinguishes incomplete inputs, undefined denominators and nonfinite results;
an authored reason replaces that explanation. All text is literal and wraps on
phones. No note DOM or space remains for valid, empty or unaffected visible
selections. Hiding a series hides its context; showing it restores context.
Zoom changes only the viewport: this note continues to describe selected data.
Its own accessible description coexists with header/month/event descriptions.

CSV retains the existing rows, order and raw units, with unavailable cells
null/blank and no extra columns or footer. Native report/section DOM captures
retain the rendered note. Canvas-only capture includes the plot and omits
surrounding context, as before. Explicit axes and fixed plot heights remain;
withholding values can legitimately change automatic bounds.

Rebuild HTML, data and runtime assets together. Older runtimes ignore the
additive declaration and cannot apply its withholding semantics. No host or
database migration is required. The native `examples_line_availability` showcase
module supplies independent platform, contributor, ratio and static examples.
