/* Operations data adapter: reads the bounded /api/operations/* contracts for
   the Operations table (its report rows are the registry rows). Pure functions only; portal.js
   owns fetching, lifecycle and DOM so stale responses stay cancellable. */
(function() {
'use strict';
/* The page lists every report up to this many, with no pager (the server's
   apps.core.limits.INLINE_LIMIT, handed over by the page; 500 when absent).
   Past it the page says so and search (server-side) reaches the rest. */
var ROW_CAP = (window.PORTAL_CTX && window.PORTAL_CTX.inline_limit) || 500;

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
    return {
        name: row.name, type: row.type, state: row.state, detail: row.detail,
        upload: !!row.upload_allowed, binding_scope: row.binding_scope,
        /* A portal-held file backs the source (stored metadata, no file read). */
        file: !!row.download_available,
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
    ROW_CAP: ROW_CAP, mapSource: mapSource, rowsQuery: rowsQuery, sourcesQuery: sourcesQuery,
    statusToLegacy: statusToLegacy, logText: logText
};
})();
