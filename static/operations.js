/* Operations data adapter: maps the bounded /api/operations/* contracts to the
   row shape the Operations table already renders. Pure functions only; portal.js
   owns fetching, lifecycle and DOM so stale responses stay cancellable. */
(function() {
'use strict';
/* The page lists every report up to this many, with no pager. Past it the page
   says so and search (server-side) reaches the rest. */
var ROW_CAP = 500;
var MAX_LIVE_TERMINALS = 4;

function mapRow(row) {
    var validation = row.validation && row.validation.summary ? { summary: row.validation.summary } : null;
    return {
        id: row.id, slug: row.slug, kind: row.kind, metrics_report: row.metrics_report,
        name: row.name, category: row.category, description: row.description,
        description_truncated: row.description_truncated,
        schedule: row.schedule, schedule_timezone: row.schedule_timezone, next_run: row.next_run,
        disabled: row.disabled, refresh_suspended_at: row.refresh_suspended_at,
        /* Never observed reads "Not run", as it always has. */
        build_status: !row.build_status || row.build_status === 'unknown' ? 'not_run' : row.build_status,
        last_run: row.last_attempt_at, last_error: row.last_error,
        waiting: row.state === 'waiting', waiting_current: row.waiting_current,
        blocked_by: row.blocked_by || [], failing_source: row.failing_source,
        has_output: !!row.has_output, html_entry: row.html_entry, live: row.live,
        framework_version: row.framework_version, validation: validation,
        observation_known: row.observation_known, quality: row.quality,
        ops_state: row.state
    };
}

function rowsQuery(q) {
    var p = new URLSearchParams();
    p.set('page_size', String(ROW_CAP));
    if (q) p.set('q', q);
    return p;
}

function sourcesQuery(q) {
    var p = new URLSearchParams();
    p.set('page_size', String(ROW_CAP));
    if (q) p.set('source_q', q);
    return p;
}

function statusToLegacy(payload, rows, now) {
    var running = {}, queue = [], scheduled = [];
    (payload.rows || []).forEach(function(run) {
        if (run.status === 'queued') {
            queue.push({ slug: run.slug, priority: run.priority, cache_mode: run.cache_mode });
        } else {
            var started = Date.parse(run.started_at || run.created_at);
            running[run.slug] = { slug: run.slug, started_at: run.started_at,
                elapsed_seconds: isNaN(started) ? 0 : Math.max(0, ((now || Date.now()) - started) / 1000) };
        }
    });
    (rows || []).forEach(function(r) {
        if (r.next_run) scheduled.push({ slug: r.slug, next_run: r.next_run });
    });
    return { running: running, queue: queue, scheduled_jobs: scheduled,
        max_concurrent: payload.max_concurrent, worker_alive: payload.worker_alive,
        server_uptime_seconds: payload.server_uptime_seconds,
        active_jobs_total: payload.active_jobs_total, coverage: payload.coverage };
}

function mapSource(row) {
    var fileTypes = ['sqlite', 'duckdb', 'file', 'image'];
    return {
        name: row.name, type: row.type, state: row.state, detail: row.detail,
        upload: !!row.upload_allowed, binding_scope: row.binding_scope,
        /* Availability is unverified on this read; the endpoint answers 404. */
        file: fileTypes.indexOf(row.type) >= 0 && row.binding_id != null,
        used_by: new Array(row.used_by_total || 0),
        used_by_total: row.used_by_total || 0,
        blocks: row.blocks_observed || [], blocks_total: row.blocks_observed_total || 0
    };
}

function logText(payload) {
    // A build that is starting has no live output yet: not an error, not finished.
    if (payload && payload.state === 'waiting') return { text: '(waiting for output...)', quiet: true };
    if (!payload || payload.state !== 'ready') {
        return { text: (payload && payload.message) || 'No log available.', unavailable: true };
    }
    var text = payload.stdout || '';
    if (payload.stderr) text += (text ? '\n\n--- stderr ---\n' : '') + payload.stderr;
    if (!text) {
        return { text: payload.mode === 'live' ? '(no output yet)' : 'No output was recorded for this run.', quiet: true };
    }
    var streams = payload.streams || {};
    var clipped = (streams.stdout && streams.stdout.truncated) || (streams.stderr && streams.stderr.truncated);
    if (clipped) text = '(showing the end of the output)\n' + text;
    return { text: text, truncated: !!clipped };
}

window.TrellumOperations = {
    ROW_CAP: ROW_CAP, MAX_LIVE_TERMINALS: MAX_LIVE_TERMINALS,
    mapRow: mapRow, mapSource: mapSource, rowsQuery: rowsQuery, sourcesQuery: sourcesQuery,
    statusToLegacy: statusToLegacy, logText: logText
};
})();
