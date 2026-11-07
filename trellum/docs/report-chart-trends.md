# Optional native linear trends

Ordinary `LineChart` and numeric `ScatterChart` accept the keyword-only
`trend_line=None`. Omitted output retains existing source behavior. Opt in:

```python
LineChart(df, "day", "revenue", dataset_id="daily",
          trend_line={"method": "linear", "series": "each"})
ScatterChart(df, "spend", "revenue", color_by="channel", dataset_id="pairs",
             trend_line={"method": "linear", "series": "each"})
```

`method` must be `"linear"`. `series` must be `"each"` or a nonempty list of
exact source identities. Positions and displayed captions are not identities:

```python
{"method": "linear", "series": [{"column": "revenue"}]}
{"method": "linear", "series": [{"group": "Web"}]}  # Line stack_by
{"method": "linear", "series": [{"column": "revenue", "group": "Web"}]}  # Scatter color_by
{"method": "linear", "series": [{"ratio": "Conversion rate", "group": "Web"}]}
```

Use `column` for ordinary columns and Scatter y, `ratio` for an exact unique
ratio declaration label, and `group` for the exact string group value when
`stack_by` or `color_by` is set. Ordinary split Line sums use a group-only
identity, including when the existing renderer sums multiple y columns into
one line per group. A single-column split also accepts column + group as an
alias. Empty group strings are literal identities, never positional fallbacks. Group order and filter changes never retarget
an identity. An absent selected group has an unavailable reason. With
`stack_by_options`, use `"each"`; exact selections require a fixed breakdown.
Unknown keys, methods, duplicate identities and ambiguous columns/ratio labels
raise `ValueError`. `details=False` optionally omits the fit disclosure; it
must be a bool. All other options are rejected.

The native Show/Hide trend button applies immediately. The dash-dot overlay
uses its source color, has no observation dots, and has a separate “Linear
trend” key. The ordinary source legend and tooltip are unchanged. Hiding a
source hides its paired overlay. Zoom crops the original fit without refitting.
Theme updates, resize and native report/section PNG/PDF include visible fits.
Canvas-only downloads contain only the canvas, not off-canvas fit details.

## Population and missingness

Fits use the current selected result **after existing aggregation**, separately
for each selected source series. Report and local filters recompute fits.
Line x must retain a regular canonical daily UTC grid: ISO `YYYY-MM-DD` strings
or midnight date/datetime values (naive dates are treated as UTC; aware dates
must have zero UTC offset). Null y slots stay at their supplied dates. A
compressed grid skipping dates is ineligible, even if the remaining labels
look chronological. No dates are inserted and source spacing is unchanged.

Finite numeric observations, including zero and negative values, are eligible.
Null, nonfinite, nonnumeric, unavailable and declared supplied-forecast series
are excluded. For bound Line sums and ratios, every raw contributor must have
finite required inputs; an incomplete bucket is excluded **before** legacy
zero-fill/skip-null aggregation can erase its missingness. Existing
`series_availability` rules also apply. Ratios additionally need a positive
finite summed denominator. The fit uses the same final plotted aggregate,
without modifying source arrays, CSV or header summaries. A legacy chart may
therefore still display a partial subtotal/zero which is excluded from its fit;
use `series_availability` when the plot itself should withhold those buckets.
Upstream missing rows or prefilled zeros cannot be recovered or certified.

Daily ratio fits weight each eligible day equally, rather than weighting by its
population. Scatter uses original finite numeric coordinate pairs, including
repeated x values as separate observations. Bubble sizes do not supply weights.
Forecast observations must be declared as separate `forecast_series` on Line;
there is no inference from captions or a model. Scatter has no forecast flag;
authors must supply observed coordinate pairs rather than mixing forecasts in.

Support is limited to static charts and ordinary complete snapshot/filter-bound
DataSources (including ordinary scoped views). Live-connected, live-propagated,
chunked and derived producers are refused with a visible reason. Stacked/area, dual-axis,
log-axis and other chart families have no trend API. No automatic model choice,
smoothing, extrapolation, outlier removal or silent sampling is performed.

## Numerical and resource limits

OLS requires three eligible observations and two distinct x values. Constant y
produces a horizontal fit with R² unavailable. Centered/scaled coordinates and
compensated sums avoid large epoch-sum cancellation. Coefficients, endpoints,
residuals and R² must be finite at supplied floating-point precision, otherwise
the optional fit is unavailable and the chart remains visible.

The per-chart budget is **50,000 candidate coordinate pairs across at most eight
selected series**, plus **50,000 raw contributors** for bound input checking.
Candidate limits include excluded entries, so missing inputs cannot trigger an
unbounded fit scan. Above a limit the entire optional fit is explicitly
unavailable, with no sampling; existing source-chart capacity is unchanged.
Work is linear in the bounded input per selected series. Each actual draw
recomputes from its current result; no fit cache can outlive its selection.
Owned controls, observers and listeners are removed on chart destruction or
canvas replacement, and Show/Hide/details state survives ordinary re-entry.

Details show the source, fit interval, included n, exclusions, slope in metric
units per day/x unit, equation with a readable x origin, and descriptive R².
Coefficients retain full precision; coefficient text uses six significant digits.
Numeric interval endpoints and x origins retain their supplied floating-point precision. Predictions
stop at the first and last eligible x and do not change source axis bounds;
out-of-axis fitted values are clipped and explained. A high R² is neither a
forecast guarantee nor evidence of causation. There are no p-values, confidence
bands or claims of independent daily residuals.
