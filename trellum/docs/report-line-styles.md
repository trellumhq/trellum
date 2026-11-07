# Ordinary LineChart points and curves

Newly built ordinary `LineChart` reports use adaptive persistent markers. A finite
observation keeps its 2px dot when each immediately adjacent finite observation
is at least 12 CSS pixels away horizontally in the current plot. Dense connected
points hide their persistent dots; every observation and hover target remains.
An isolated finite observation always keeps its dot, including measured zero.
Null/nonfinite observations remain gaps. No resampling or decimation is applied.

Spacing follows the actual chart scale on resize, zoom/reset and reactive rebuilds.
It does not depend on page width or total label count. Existing chips, nearest-point
tooltips, hover/hit radii, zoom gestures, 2.5px lines, heights and summaries remain.
Point colors follow each line through theme changes; static explicit author colors
remain explicit. AreaChart, ComboChart and other families retain their own styles.

```python
LineChart(df, x="day", y="revenue", dataset_id="daily")  # straight default
LineChart(df, x="day", y="revenue", dataset_id="daily", tension=0.35)
```

`tension` is keyword-only, defaults to `0.0`, and accepts finite numbers from 0 to
1 inclusive; booleans are invalid. It maps to Chart.js cubic interpolation. Curves
infer movement between observations and can overshoot, including between equal
values. They are not averages, forecasts or monotone estimates. Use care for
bounded rates, plateaus, spikes and irregular observations. Source values, null
gaps, numeric domains, category spacing and CSV stay unchanged.

Construction raises `ValueError` for invalid tension. Report validation also
reports `line-chart-tension-invalid` if an option was mutated afterwards.

New payloads carry `line_style: "adaptive"` and `tension` in every ordinary
LineChart branch (static, dataset, ratio and breakdown). Older payloads lacking
the tag retain their previous radius/400-label cutoff behavior in the new runtime.
Older runtimes can load new payloads and ignore the added metadata; rebuild with
the matching runtime to get adaptive points and live interpolation. No host API
or constructor positional argument changes are required.

The component showcase's `examples_line_styles.add_sections(ctx)` supplies fixed
dense, sparse, width-sensitive, isolated-zero, null-gap, curve and filtered ratio
fixtures. Static fixtures use no binding and `static=True`; an explicit
`dataset_id` continues to take precedence over `static=True`.
