/* Operations data adapter: maps the bounded /api/operations/* contracts to the
   row shape the Operations table already renders. Pure functions only; portal.js
   owns fetching, lifecycle and DOM so stale responses stay cancellable. */
(function() {
'use strict';
var PAGE_SIZE = 25;
var MAX_LIVE_TERMINALS = 4;

function mapRow(row) {
    var validation = row.validation && row.validation.summary ? { summary: row.validation.summary } : null;
    return {
        id: row.id, slug: row.slug, kind: row.kind, metrics_report: row.metrics_report,
        name: row.name, category: row.category, description: row.description,
        description_truncated: row.description_truncated,
        schedule: row.schedule, schedule_timezone: row.schedule_timezone, next_run: row.next_run,
        disabled: row.disabled, build_status: row.build_status || 'unknown',
        last_run: row.last_attempt_at, last_error: row.last_error,
        waiting: row.state === 'waiting', waiting_current: row.waiting_current,
        blocked_by: row.blocked_by || [], failing_source: row.failing_source,
        has_output: !!row.has_output, html_entry: row.html_entry, live: row.live,
        framework_version: row.framework_version, validation: validation,
        observation_known: row.observation_known, quality: row.quality,
        ops_state: row.state
    };
}

function catalogQuery(s) {
    var p = new URLSearchParams();
    if (s.q) p.set('q', s.q);
    if (s.category) p.set('category', s.category);
    if (s.folder && s.folder !== 'all') p.set('folder', s.folder);
    if (s.folder === 'recent') (s.recent || []).slice(0, 8).forEach(function(v) { p.append('recent', v); });
    if (s.state && s.state !== 'all') p.set('state', s.state);
    if (s.page > 1) p.set('page', String(s.page));
    if (s.sourceQ) p.set('source_q', s.sourceQ);
    if (s.sourcePage > 1) p.set('source_page', String(s.sourcePage));
    return p;
}

function slugQuery(slugs) {
    var p = new URLSearchParams();
    (slugs || []).slice(0, PAGE_SIZE).forEach(function(slug) { p.append('slug', slug); });
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
    PAGE_SIZE: PAGE_SIZE, MAX_LIVE_TERMINALS: MAX_LIVE_TERMINALS,
    mapRow: mapRow, mapSource: mapSource, catalogQuery: catalogQuery, slugQuery: slugQuery,
    statusToLegacy: statusToLegacy, logText: logText
};
})();
