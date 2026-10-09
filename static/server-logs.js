(function () {
  'use strict';
  var root = document.querySelector('[data-server-logs]');
  if (!root) return;

  var form = root.querySelector('[data-filter-form]');
  var tbody = root.querySelector('[data-entries]');
  var status = root.querySelector('[data-status]');
  var coverage = root.querySelector('[data-coverage]');
  var scroll = root.querySelector('[data-log-scroll]');
  var liveButton = root.querySelector('[data-live]');
  var olderButton = root.querySelector('[data-load-older]');
  var download = root.querySelector('[data-download]');
  var entries = [];
  var entryById = new Map();
  var rowNodes = new Map();
  var expanded = new Set();
  var runDetails = new Map();
  var runPending = new Set();
  var generation = 0;
  var controller = null;
  var runControllers = new Set();
  var inFlight = false;
  var paused = false;
  var hasOlder = false;
  var nextBefore = null;
  var nextAfter = null;
  var appliedFilters = null;
  var pollTimer = null;
  var retryDelay = 2500;
  var maxVisible = 1000;
  var trimmed = false;

  function text(tag, value, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null || value === '' ? '—' : String(value);
    return node;
  }
  function button(label, key, action, id) {
    var node = document.createElement('button');
    node.type = 'button'; node.className = 'ui-btn ghost logs-small-button';
    node.textContent = label; node.setAttribute('data-focus-key', key);
    node.dataset.action = action;
    if (id) node.dataset.id = id;
    return node;
  }
  function localTime(value) {
    var date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || '—') : new Intl.DateTimeFormat(undefined, {
      year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
    }).format(date);
  }
  function readFilters() {
    var data = new FormData(form);
    var out = {};
    data.forEach(function (value, key) { if (String(value).trim()) out[key] = String(value).trim(); });
    var since = out.since || '24h';
    var now = Date.now();
    var duration = since === '1h' ? 3600000 : since === '7d' ? 604800000 : 86400000;
    out.since = new Date(now - duration).toISOString();
    return out;
  }
  function queryUrl(base, params) {
    var url = new URL(base, location.origin);
    Object.keys(params).forEach(function (key) { if (params[key]) url.searchParams.set(key, params[key]); });
    return url;
  }
  function makeDownloadUrl() {
    var params = Object.assign({}, appliedFilters || {}); params.limit = '1000';
    download.href = queryUrl(root.dataset.exportUrl, params).toString();
  }
  function updateCoverage(data) {
    if (!data) return;
    coverage.replaceChildren(); coverage.hidden = false;
    var line = document.createElement('p'); line.className = 'logs-coverage-text';
    var parts = [];
    if (data.enabled === false) parts.push('Log capture is off.');
    if (data.retention_days != null) parts.push('Retention: ' + data.retention_days + ' days.');
    if (data.max_rows != null) parts.push('Maximum retained rows: ' + data.max_rows + '.');
    if (data.gaps || Number(data.gap_count) > 0) parts.push('Capture gaps have been reported' + (data.gap_count != null ? ' (' + data.gap_count + ').' : '.'));
    if (!parts.length) parts.push('Retention and capture status are provided by the server.');
    line.textContent = parts.join(' '); coverage.appendChild(line);
  }
  function preserveView(mutator) {
    var top = scroll.scrollTop;
    var viewportTop = scroll.getBoundingClientRect().top;
    var anchor = Array.from(tbody.querySelectorAll('tr[data-entry-id]')).find(function (row) {
      return row.getBoundingClientRect().bottom >= viewportTop;
    });
    var anchorId = anchor && anchor.dataset.entryId;
    var anchorOffset = anchor ? anchor.getBoundingClientRect().top - viewportTop : 0;
    var focused = document.activeElement;
    var focusKey = focused && root.contains(focused) ? focused.getAttribute('data-focus-key') : null;
    mutator();
    scroll.scrollTop = top;
    if (anchorId && top > 12) {
      var anchorAfter = tbody.querySelector('tr[data-entry-id="' + CSS.escape(anchorId) + '"]');
      if (anchorAfter) scroll.scrollTop += anchorAfter.getBoundingClientRect().top - scroll.getBoundingClientRect().top - anchorOffset;
    }
    if (focusKey) {
      var replacement = root.querySelector('[data-focus-key="' + CSS.escape(focusKey) + '"]');
      if (replacement) replacement.focus({ preventScroll: true });
    }
  }
  function mainRow(entry) {
        var row = document.createElement('tr'); row.dataset.entryId = entry.id;
        var when = document.createElement('td'); when.className = 'logs-time';
        when.appendChild(text('time', localTime(entry.time))); when.title = entry.time || '';
        row.appendChild(when);
        var level = document.createElement('td');
        level.appendChild(text('span', entry.level || 'unknown', 'ui-badge logs-level logs-level-' + String(entry.level || 'unknown').toLowerCase()));
        row.appendChild(level);
        var origin = document.createElement('td'); origin.className = 'logs-origin';
        origin.appendChild(text('strong', entry.service || 'unknown'));
        if (entry.host) origin.appendChild(text('span', entry.host));
        if (entry.process) origin.appendChild(text('span', entry.process));
        if (entry.worker_id) origin.appendChild(text('span', 'worker ' + entry.worker_id));
        row.appendChild(origin);
        var message = document.createElement('td'); message.className = 'logs-message'; message.appendChild(text('div', entry.message || '—'));
        row.appendChild(message);
        var controls = document.createElement('td'); controls.className = 'logs-entry-controls';
        controls.appendChild(button(expanded.has(entry.id) ? 'Hide details' : 'Details', 'details-' + entry.id, 'toggle', entry.id));
        controls.appendChild(button('Copy', 'copy-' + entry.id, 'copy', entry.id));
        if (entry.run_id && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(entry.run_id)) {
          controls.appendChild(button('Build details', 'run-' + entry.id, 'run', entry.id));
        }
        row.appendChild(controls); return row;
  }
  function detailRow(entry) {
          var detailRow = document.createElement('tr'); detailRow.className = 'logs-detail-row'; detailRow.dataset.detailFor = entry.id;
          var cell = document.createElement('td'); cell.colSpan = 5;
          var detail = document.createElement('section'); detail.className = 'logs-detail'; detail.setAttribute('aria-label', 'Log entry details');
          var meta = document.createElement('dl'); meta.className = 'logs-meta';
          [['Host', entry.host], ['Process', entry.process], ['Logger', entry.logger], ['Worker', entry.worker_id], ['Report', entry.report_slug], ['Run ID', entry.run_id], ['Request ID', entry.request_id], ['Trigger', entry.trigger]].forEach(function (pair) {
            if (pair[1]) { meta.appendChild(text('dt', pair[0])); meta.appendChild(text('dd', pair[1])); }
          });
          detail.appendChild(meta);
          if (entry.exception) { detail.appendChild(text('h3', 'Exception')); detail.appendChild(text('pre', entry.exception, 'logs-pre')); }
          if (entry.context && typeof entry.context === 'object') {
            var context = Object.assign({}, entry.context); var run = context.build_run; delete context.build_run;
            if (Object.keys(context).length) { detail.appendChild(text('h3', 'Context')); detail.appendChild(text('pre', JSON.stringify(context, null, 2), 'logs-pre')); }
            if (run) {
              detail.appendChild(text('h3', 'Build details'));
              var runMeta = document.createElement('dl'); runMeta.className = 'logs-meta';
              [['Status', run.status], ['Report', run.slug], ['Studio', run.studio], ['Organization', run.org], ['Trigger', run.trigger], ['Worker', run.worker_id], ['Created', run.created_at], ['Started', run.started_at], ['Finished', run.finished_at], ['Exit code', run.exit_code], ['Memory allocation', run.memory_limit_mb == null ? null : run.memory_limit_mb + ' MB'], ['Peak memory', run.peak_memory_mb == null ? null : run.peak_memory_mb + ' MB']].forEach(function (pair) { if (pair[1] != null) { runMeta.appendChild(text('dt', pair[0])); runMeta.appendChild(text('dd', pair[1])); } });
              detail.appendChild(runMeta);
              if (run.output_source || run.output_note) detail.appendChild(text('p', [run.output_source, run.output_note].filter(Boolean).join(' · '), 'ui-help'));
              if (run.output_truncated) detail.appendChild(text('p', 'Build output was truncated.', 'ui-help'));
              if (run.stdout) { detail.appendChild(text('h3', 'stdout')); detail.appendChild(text('pre', run.stdout, 'logs-pre')); }
              if (run.stderr) { detail.appendChild(text('h3', 'stderr')); detail.appendChild(text('pre', run.stderr, 'logs-pre')); }
            }
          }
          cell.appendChild(detail); detailRow.appendChild(cell); return detailRow;
  }
  function render() {
    preserveView(function () {
      var visible = new Set(entries.map(function (entry) { return entry.id; }));
      rowNodes.forEach(function (nodes, id) {
        if (!visible.has(id)) { nodes.row.remove(); if (nodes.detail) nodes.detail.remove(); rowNodes.delete(id); }
      });
      entries.forEach(function (entry) {
        if (!rowNodes.has(entry.id)) rowNodes.set(entry.id, { row: mainRow(entry), detail: null, context: entry.context });
      });
      for (var index = entries.length - 1; index >= 0; index -= 1) {
        var entry = entries[index];
        var nodes = rowNodes.get(entry.id);
        if (!nodes.row.isConnected) tbody.appendChild(nodes.row);
        nodes.row.querySelector('[data-action="toggle"]').textContent = expanded.has(entry.id) ? 'Hide details' : 'Details';
        if (expanded.has(entry.id)) {
          if (!nodes.detail || nodes.context !== entry.context) {
            if (nodes.detail) nodes.detail.remove();
            nodes.detail = detailRow(entry); nodes.context = entry.context;
          }
          if (nodes.row.nextElementSibling !== nodes.detail) nodes.row.after(nodes.detail);
        } else if (nodes.detail) { nodes.detail.remove(); nodes.detail = null; nodes.context = entry.context; }
        var next = index + 1 < entries.length ? rowNodes.get(entries[index + 1].id) : null;
        if (next && nodes.row.nextElementSibling !== (nodes.detail || next.row)) {
          tbody.insertBefore(nodes.row, next.row);
          if (nodes.detail) nodes.row.after(nodes.detail);
        }
      }
    });
  }
  function setStatus(message, kind) {
    status.textContent = message;
    status.dataset.state = kind || 'info';
  }
  function resetPollTimer(delay) {
    clearTimeout(pollTimer);
    if (!paused && !document.hidden) pollTimer = setTimeout(function () { fetchPage('poll'); }, delay);
  }
  function invalidate() {
    generation += 1;
    if (controller) controller.abort();
    runControllers.forEach(function (request) { request.abort(); });
    runControllers.clear(); runPending.clear();
    controller = null; inFlight = false;
    clearTimeout(pollTimer);
  }
  function addEntries(incoming) {
    incoming.forEach(function (entry) {
      if (!entry || entry.id == null || entryById.has(String(entry.id))) return;
      entry.id = String(entry.id); entryById.set(entry.id, entry); entries.push(entry);
    });
    entries.sort(function (a, b) {
      var idDiff = Number(b.id) - Number(a.id);
      return Number.isFinite(idDiff) && idDiff ? idDiff : String(b.id).localeCompare(String(a.id));
    });
    if (entries.length > maxVisible) {
      entries = entries.slice(0, maxVisible); entryById = new Map(entries.map(function (e) { return [e.id, e]; })); trimmed = true;
      var kept = new Set(entryById.keys());
      expanded.forEach(function (id) { if (!kept.has(id)) expanded.delete(id); });
      var keptRuns = new Set(entries.map(function (e) { return e.run_id; }).filter(Boolean));
      runDetails.forEach(function (_, id) { if (!keptRuns.has(id)) runDetails.delete(id); });
    }
  }
  async function fetchPage(kind) {
    if (inFlight || (paused && kind === 'poll') || document.hidden) return;
    var ownGeneration = generation;
    var params = Object.assign({}, appliedFilters || {}); params.limit = '200';
    var catchingUp = (kind === 'poll' || kind === 'refresh') && nextAfter !== null;
    if (kind === 'older' && nextBefore) params.before = nextBefore;
    if (catchingUp) params.after = nextAfter;
    controller = new AbortController(); inFlight = true;
    if (kind === 'initial' || kind === 'filters') setStatus('Loading server logs…');
    var drainCatchup = false;
    try {
      var response = await fetch(queryUrl(root.dataset.eventsUrl, params), { signal: controller.signal, headers: { Accept: 'application/json' } });
      if (ownGeneration !== generation) return;
      if (!response.ok) throw new Error('The server returned HTTP ' + response.status + '.');
      var payload = await response.json();
      if (ownGeneration !== generation) return;
      if (!payload || !Array.isArray(payload.entries)) throw new Error('The log response had an unexpected format.');
      addEntries(payload.entries);
      if (!catchingUp) {
        hasOlder = Boolean(payload.has_more);
        nextBefore = payload.next_before || (payload.entries.length ? String(payload.entries[payload.entries.length - 1].id) : nextBefore);
      }
      if (kind !== 'older') nextAfter = payload.next_after || (payload.entries.length ? String(payload.entries[0].id) : nextAfter);
      olderButton.disabled = !hasOlder || entries.length >= maxVisible;
      updateCoverage(payload.coverage);
      makeDownloadUrl(); render();
      if (!entries.length) setStatus(payload.coverage && payload.coverage.enabled === false ? 'Log capture is off. No entries are available.' : 'No log entries match these filters.');
      else if (trimmed || entries.length >= maxVisible) setStatus('Showing the newest 1,000 entries. Narrow the time range or filters to browse older entries.');
      else setStatus(entries.length + (hasOlder ? ' entries shown. More older entries are available.' : ' entries shown.') + (paused ? ' Live updates are paused.' : ''));
      retryDelay = 2500;
      if (catchingUp && payload.has_more && payload.entries.length) {
        // Drain the bounded response window before the next interval so no event is skipped.
        nextAfter = payload.next_after || nextAfter;
        drainCatchup = true;
      }
    } catch (error) {
      if (ownGeneration !== generation || error.name === 'AbortError') return;
      setStatus('Could not load server logs. ' + error.message + ' Retrying…', 'error');
      retryDelay = Math.min(retryDelay * 2, 30000);
    } finally {
      if (ownGeneration === generation) {
        inFlight = false; controller = null;
        if (drainCatchup && (!paused || kind === 'refresh') && !document.hidden) queueMicrotask(function () { fetchPage(kind); });
        else resetPollTimer(retryDelay);
      }
    }
  }
  function applyFilters() {
    invalidate(); entries = []; entryById.clear(); expanded.clear(); runDetails.clear(); render();
    hasOlder = false; nextBefore = null; nextAfter = null; trimmed = false;
    appliedFilters = readFilters();
    olderButton.disabled = true; makeDownloadUrl(); fetchPage('filters');
  }
  async function showRun(id, entryId) {
    var entry = entryById.get(entryId);
    if (!entry || !entry.run_id || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(entry.run_id)) return;
    var key = entry.run_id;
    var cached = runDetails.get(key);
    if (cached && ['queued', 'starting', 'running'].indexOf(cached.status) === -1) { entry.context = Object.assign({}, entry.context || {}, { build_run: cached }); expanded.add(entryId); render(); return; }
    if (runPending.has(key)) return;
    var ownGeneration = generation;
    var request = new AbortController();
    runControllers.add(request);
    runPending.add(key);
    try {
      var response = await fetch(root.dataset.runUrl + encodeURIComponent(key) + '/', { signal: request.signal, headers: { Accept: 'application/json' } });
      if (ownGeneration !== generation || entryById.get(entryId) !== entry) return;
      if (!response.ok) throw new Error('Build details could not be loaded (HTTP ' + response.status + ').');
      var payload = await response.json();
      if (ownGeneration !== generation || entryById.get(entryId) !== entry) return;
      var run = payload.run || payload;
      runDetails.set(key, run); entry.context = Object.assign({}, entry.context || {}, { build_run: run }); expanded.add(entryId); render();
    } catch (error) { if (ownGeneration === generation && error.name !== 'AbortError') setStatus(error.message, 'error'); }
    finally { if (ownGeneration === generation) runPending.delete(key); runControllers.delete(request); }
  }
  tbody.addEventListener('click', function (event) {
    var target = event.target.closest('button[data-action]'); if (!target) return;
    var id = target.dataset.id; var action = target.dataset.action;
    if (action === 'toggle') { expanded.has(id) ? expanded.delete(id) : expanded.add(id); render(); }
    if (action === 'copy') {
      var entry = entryById.get(id); if (!entry) return;
      var copied = JSON.stringify(entry, null, 2);
      navigator.clipboard.writeText(copied).then(function () { setStatus('Log entry copied.'); }, function () { setStatus('Could not copy this entry.'); });
    }
    if (action === 'run') showRun(id, id);
  });
  form.addEventListener('submit', function (event) { event.preventDefault(); applyFilters(); });
  root.querySelector('[data-refresh]').addEventListener('click', function () { invalidate(); fetchPage('refresh'); });
  olderButton.addEventListener('click', function () { if (hasOlder && nextBefore && entries.length < maxVisible) fetchPage('older'); });
  liveButton.addEventListener('click', function () {
    paused = !paused; liveButton.textContent = paused ? 'Resume live' : 'Pause live'; liveButton.setAttribute('aria-pressed', String(!paused));
    if (paused) { invalidate(); setStatus(entries.length ? entries.length + ' entries shown. Live updates are paused.' : 'Live updates are paused.'); }
    else fetchPage('poll');
  });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { invalidate(); }
    else if (!paused) fetchPage(entries.length ? 'poll' : 'initial');
  });
  window.addEventListener('pagehide', invalidate);
  appliedFilters = readFilters();
  makeDownloadUrl(); olderButton.disabled = true; fetchPage('initial');
})();
