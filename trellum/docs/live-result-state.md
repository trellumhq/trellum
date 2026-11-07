# Live result identity and lifecycle

The existing `window._fwLiveQuery` binder exposes two additive methods:

```javascript
const state = window._fwLiveQuery.getDatasetState(datasetId);
const unsubscribe = window._fwLiveQuery.subscribeDataset(datasetId, state => {
    // Read metadata; render any parameter/error strings as text.
});
// The consumer must release its subscription when removed or replaced.
unsubscribe();
```

Both methods return/deliver plain, detached JSON values. Mutating one never
changes the binder or another listener's copy. No SQL, configuration, raw rows,
DOM or request controller is exposed. Unknown/unregistered dataset IDs return
`null`; subscribing immediately delivers the current value, including `null`.
Notifications follow coherent transitions, suppress identical consecutive states,
and isolate thrown listener errors. The API describes a registered live dataset,
not every dataset or derived scope in the filter engine.

## State fields

| Field | Meaning |
| --- | --- |
| `dataset_id` | Registered dataset ID. |
| `generation` | Page-local increasing source ownership identifier. Changes on source replacement. |
| `revision` | Page-local applied-result revision. Initial snapshots start at zero for capture compatibility. Advances on replacement valid snapshots or matching valid query results, including empty results. Unavailable replacement alone does not apply a result. |
| `phase` | `ready`, `pending`, `error`, `unavailable`, or `idle`. |
| `origin` | `snapshot`, `query`, or `null` if no valid result is installed. |
| `requested_params` | Current committed selection collected by the existing bindings and coercion rules; normalized to the JSON request representation. |
| `displayed_params` | Declared public snapshot defaults or the exact query parameters that produced installed rows; `null` when unknown. |
| `has_result` | A valid result exists, independent of row count. |
| `row_count` | Installed row count, including zero; `null` without a valid result. |
| `truncated` | Boolean for a declared query response (`false` when its optional flag is omitted); `null` for snapshots/unknown completeness. |
| `completed_at` | ISO browser-observed query completion/application time; `null` for snapshots. |
| `error` | Current request error string or `null`. |

`ready` includes a valid empty snapshot and a successfully queried empty result.
`unavailable` describes a missing/invalid snapshot without work in progress.
`pending` covers the coalescing delay, budget wait, fetch, body parsing and the
single automatic rate-limit retry. `error` retains the previous valid result.
`idle` can occur when page suspension cancels pending work without a result.
Returning to the displayed selection immediately clears obsolete pending/error
context without repeating a successful query.

Data, selection, origin and completion are separate facts. While selection B is
pending or failed, rows and `displayed_params` can still describe selection A.
An empty response is data; zeros and upstream null cells are preserved. A query
completion time does not establish database execution time, latest source event,
source freshness, complete population coverage, or completeness of a derived
local scope. Snapshot completeness is deliberately unknown. A limited response
still feeds the existing aggregations over returned rows; this API adds no total
estimator or completeness policy.

## Registration and generation boundaries

The native `live_data_source` renderer calls:

```javascript
window._fwLiveQuery.registerDataset(datasetId, liveConfig, {
    raw: window._reportData['_ds_' + datasetId],
    report: window._reportData,
    owner: document.getElementById(rendererId)
});
```

This optional third argument is internal source ownership metadata. The binder
installs snapshot rows and metadata together. The same raw snapshot reference,
report-data object, hidden root and serialized public live configuration retain
one generation. Repeated `renderAll`, theme changes, and equivalent configuration
objects cannot restore snapshot rows over a queried result. Engine-added indexes
on the raw object do not create a generation.

A replacement raw object, report-data object, root, or public query/schema/
default/binding configuration creates a generation. Install the new artifact as
new objects; mutating snapshot cells in place is not a supported replacement
signal. The configuration is compared by its serialized value, so rebuilding a
configuration in a different key order may conservatively create a generation.
Only schema-declared defaults identify the snapshot; an incomplete declared
default set leaves `displayed_params` unknown (`null`). New-generation snapshots
have unknown completeness and no query completion time. Existing engine filter
state is preserved; an armed source schedules the actual selected parameters
when they differ from its snapshot. Missing/invalid raw payloads are not
confirmed empty snapshots (the engine still receives its compatible empty rows).

A hidden root reused for another dataset retires the old binder. Returning to
that source creates a new generation from its snapshot and re-queries a retained
selection if needed. Distinct source roots and dataset IDs retain independent
work and errors. Removing a root (observed after the DOM mutation batch), or
calling `filterEngine.removeDataset`, retires its binder, aborts its work, clears
its timers, unregisters only its own engine hook, delivers `null`, and releases
its current state subscriptions. Same-ID source replacement keeps dataset
subscriptions so consumers see the new generation. Consumers must still call
`unsubscribe` on their own removal/replacement; this API cannot infer which DOM
node owns an arbitrary callback. Subscriptions created before first registration
receive `null` immediately and then receive the registration.

Legacy two-argument `registerDataset(id, config)` remains supported. It adopts
existing engine rows as a snapshot with unknown displayed parameters; without
raw ownership it cannot prove which selection produced those rows. Registration
alone does not initiate a legacy query. A caller replacing a legacy source must
supply ownership metadata or change its configuration. Native registration always
supplies ownership, including when the raw snapshot is absent.

## Transport and cancellation

The request body remains `{query_id, params}`, with the existing authentication,
report header, public parameter coercion, filter commit semantics, 250 ms
coalescing window and shared 30-per-minute client pacing heuristic. Transient
slider changes do not commit requested state or schedule a query. No requests
run in standalone, anonymous-share or old-host-without-endpoint contexts.

One owned operation per dataset covers headers, asynchronous body parsing and
its queued retry. Only an initial HTTP 429 gets one retry, using the existing
`Retry-After` policy (capped at 30 seconds). A different committed selection
retires a queued retry and schedules the latest parameters. Ordinary requests
coalesce selections until completion; obsolete responses/errors are discarded.
Generic failures have no new automatic retry policy.

Each fetch attempt has an `AbortController` and a 30-second client deadline
covering response body parsing. This exceeds the portal's current 10-second query
execution deadline. Portal response shaping currently caps at 2 MiB and 1,000
rows; development serving shares the row limit but does not apply the portal's
byte cap. This change adds no
smaller client row/byte cap. Malformed success envelopes (missing/non-array
columns or rows, non-string column names, inconsistent row widths, non-boolean
declared truncation) fail without replacing prior data. Cell values are not
coerced or restricted further.

Source replacement/disposal and `pagehide` abort transport and clear all owned
timers. Applied rows keep their identity during page suspension. On `pageshow`,
a canceled latest pending selection resumes once; a terminal generic failure is
not retried merely because the page returned. Entry, generation, operation token,
sequence, root connection and still-selected parameters guard every application,
including after asynchronous JSON parsing, even if a transport ignores abort.
Client abort does not promise cancellation of server-side SQL execution.

## Capture and consumer compatibility

`registerDataset`, `attachFilterBar`, `isArmed`, and `getCaptureStatus` remain
available. Capture status remains `{pending, error, revision}`: it aggregates
actual active pending/error phases and a monotonic applied-result revision.
Initial snapshot registration preserves revision zero; replacement snapshots and
query results advance the applied revision. Queued retry and body parsing block admission. Retired work cannot leave a
pending/error capture gate. A successful empty result is ready. Existing
report-wide export admission and artifact/host schemas are unchanged.

No new visible presenter, FilterBar layout, chart message, KPI/table status,
stylesheet, chart library or export footer is included. Existing generic
KPI/table feedback and local inherited-filter presentation remain in place.
The diagnostic dataset-size walker currently counts compiled `DataSource` only;
live sources are absent from its size totals. Native test artifacts retain those
unmodified diagnostics and separately assert the actual installed live rows.

Follow-up presenters should read this shared API, use `has_result` before
interpreting zero rows, name requested/displayed scope separately, render strings
as text, and own their subscription cleanup. They must not infer source freshness
from `completed_at` or snapshot completeness from a row count.

## Verification

`testing/test_live_result_state.py` runs the production engine, binder and
registrar in a deterministic Node VM with a controlled transport/clock. It is
logic coverage, not a full report check. `testing/test_live_result_state_browser.py`
builds genuine `ReportContext` reports with declared SQLite snapshots,
`compute_details`, `validate_report`, native FilterBar/LineChart/KpiRow/DataTable,
and vendored assets. Its intercepted endpoint observes real binder requests;
there is no post-hoc status adapter. Desktop/phone comparisons use the baseline
runtime, and heavy cases cover phone rendering and completed PNG/PDF exports.
