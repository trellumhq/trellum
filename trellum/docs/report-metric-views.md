# Filtered metric views

`MetricView(id, *, parent, groups, metrics)` is a nonvisual dataset registrar.
Import it from `trellum.components.metric_views`. Declare an ordinary unchunked
snapshot `DataSource` first, optionally followed by one `ScopedDataSource`, then
its MetricView, then the consumers. Chained views, live sources, propagated-live
scope, arbitrary expressions and joins are unsupported. This calculates facts
already delivered in the report; it makes no warehouse completeness claim.

```python
view = MetricView('geography', parent='facts',
    groups=[{'id': 'all', 'label': 'Worldwide'},
            {'id': 'uk', 'label': 'UK',
             'where': {'column': 'country', 'values': ['GB']}}],
    metrics={'cost': {'agg': 'sum', 'column': 'cost'},
             'facts': {'agg': 'count'},
             'cpi': {'agg': 'ratio', 'numerator': 'cost',
                     'denominator': 'installs'}})
mini = MiniKpi('CPI', format='currency',
    binding={'dataset_id': 'geography', 'group': 'uk', 'metric': 'cpi'})
table = DataTable(view.empty_frame(), 'Summary', dataset_id='geography',
                  columns=['group_label', 'cost', 'cpi', 'Notes'])
```

The keyword-only MiniKpi binding has exactly `dataset_id`, `group`, `metric`.
A supplied non-None value and a binding are ambiguous and rejected. Static
MiniKpis retain supplied-value formatting, positional arguments and layout.
Bound values use the same formatter. Unavailable values show an em dash and
an accessible visible reason. Static MiniKpis are never automatically bound.

Each group has exactly `id`, `label` and optional `where`. Membership has exactly
`column` and `values` (unique literal string keys); omission means all selected
parent rows. At most 32 groups, 32 metrics and 32 membership keys per group.
IDs are nonblank, at most 64 characters; labels and membership keys at most 120.
Metric keys match `[A-Za-z][A-Za-z0-9_]{0,63}` and cannot collide with reserved
or generated columns. Column names are nonblank, at most 128 characters.

The complete source is bounded to 10,000 rows and 4 MiB of UTF-8 JSON for relevant
columns (membership and additive metric inputs). Nothing silently truncates.
Prepare a smaller complete snapshot when either bound is exceeded. Source
metric columns must exist and contain native int/float values or missing values.
Decimal, strings, booleans and integers outside JavaScript's safe range require
explicit precision-safe preparation before delivery, rather than implicit
conversion. Source replacement is checked again in the browser; incompatible/unsafe
cells withhold affected metrics with reasons while valid sibling metrics remain.

Output column order is `group_id`, `group_label`, `matching_rows`, declared
metric values, each metric's `<key>_status`, `<key>_reason`, then `Notes`.
Metrics are numbers or null; status is `available` or `unavailable`; a valid
metric has an empty reason. Notes joins unavailable metric names and reasons.
Every declared group remains, including empty groups. Counts are explicit zero;
empty sums/ratios are unavailable. Any matching missing/nonnumeric/nonfinite or
unsafe input withholds that metric only. Zero is real. Ratios divide sums of
explicit additive raw columns, in fraction units; zero denominators and unsafe
intermediate sums/ratios are unavailable. Aggregates outside the safe-number
range are withheld, including intermediate overflow before later cancellation.
Unsupported source/bounds produce explicit unavailable result rows and prevent
capture readiness. No missing-row, maturity, inference or forecast policy exists.

Groups intersect parent report filters. Overlapping groups are allowed; their
sum is not a population total. Local DataTable search/sort affects only that
table's displayed/exported rows. Panel metrics retain the selected parent scope.
CSV includes numeric fractions and group labels; no overlapping grand total is
added. Use DataTable `column_formats` for currency/percentage display; CSV retains
the exact raw numbers and fraction units. Use a separately labeled source table for source-fact exports.

The registrar runs before consumers, owns its parent row/lifecycle subscriptions,
and publishes via the ordinary filter-engine dataset API. Its pure
`window._fwMetricViews.reduceMetric(rows, spec)` helper returns
`{value, status, reason}` for reuse by other bounded snapshot calculations. DOM/scope removal
cleans up only owned subscriptions and its result dataset. Reinitialization
reattaches row subscriptions lost during source removal. Scoped reads bypass
stale scoped caches without changing filters. Derived diagnostics report zero
source fact rows/serialized bytes plus `derived_group_rows`, schema and parent
identity; filter coverage follows the parent lineage. Derived rows are computed
in the browser, not serialized as duplicate source facts.

The small engine hook is `subscribeLifecycle(callback) -> unsubscribe`.
Each callback receives `{type: 'init'|'remove'|'scope', dataset_id}` after the
structural operation is consistent, including `initColumnar(id, null)`.
Unsubscribe is idempotent. This hook is separate from ordinary row subscribers;
init/remove do not acquire ordinary notifications or change live request policy.
Consumers must ignore their own publications and unsubscribe on disposal.

Until central dispatch is wired, call
`validation.checks.metric_views.check_metric_views(ctx, result)` alongside
`validate_report(ctx, details=compute_details(ctx))`. No warning suppression is
needed for valid bindings. Native capture keeps current exporter behavior:
only the active tab and expanded disclosures appear; horizontally scrolled
wide tables capture the current viewport, not all offscreen columns. Open the
summary and source-fact disclosures to initialize their lazy tables before a
section capture. The current readiness check may otherwise report loading for
a collapsed table; capture does not expand disclosures automatically.
PNG/PDF are presentation captures, not complete data exports.


## Ordinary DataTable column formats

`DataTable(..., column_formats=None)` is keyword-only. None and an empty mapping
preserve existing markup, payload and numeric display. Otherwise map known
**displayed** columns to exactly `{"format": ...}` and optional `"precision"`.
Formats are number, currency, percent, ratio, plain. Precision is an integer
0–12 (never bool); plain rejects precision. Declarations are checked at both
construction and render, including mutations. ComparisonTable does not accept
this option, and workspace table rendering ignores it.

Without precision, use the existing named runtime formatter. Explicit precision
uses fixed full-number display matching `MiniKpi(full_number=True, precision=...)`:
`{"cpi": {"format": "currency", "precision": 2}, "recovery":
{"format": "percent", "precision": 2}}` displays raw `12/7` as `$1.71` and
raw `1.0875` as `108.75%`. Null/nonfinite explicitly formatted cells show an em
dash; real zero remains zero. Only native numbers are numerically formatted.
Strings and serialized exact Decimal text retain their text without Number
coercion. Boolean snapshot cells in explicitly formatted columns are rejected
with guidance to supply explicit text; runtime replacement booleans retain
their text. Unsafe native integer inputs are rejected at build with
preparation guidance; unsafe runtime replacement numbers show an explicit
precision error instead of a rounded number. Supply exact text or safe units.

Search uses the formatted visible text. Numeric sort, conditional rules, bars,
shading, raw matching-row CSV, total calculations/own formats, row limits and
pinning keep their existing inputs and behavior. No KPI renderer is loaded by
a table: a small table-local helper handles fixed precision. MetricView Notes
continues to explain unavailable metrics; formatting does not infer a reason.

For common mutation-validation dispatch, invoke
`validation.checks.metric_views.check_column_formats(comps, result)` alongside
the native validator until its shared hook is wired.
