/* Closed operator measurements: explicit admission, read-only observation. */
(function () {
  'use strict';
  var controllers = new Set();
  function mount(root) {
    if (root._operatorObservation || !window.TrellumActions) return;
    var form = root.querySelector('[data-observation-refresh]');
    if (!form) return;
    var status = root.querySelector('[data-observation-status]');
    var button = form.querySelector('button[type=submit]');
    var poller = null, post = null, deadline = null, disposed = false, waitingExpired = false;
    function current() { return !disposed && root.isConnected; }
    function feedback(message) { if (current() && status) status.textContent = message; }
    function render(result) {
      var action = result.action;
      if (!action || action.id !== root.dataset.observationId) throw new Error('Obsolete observation');
      feedback(action.message);
      var snapshot = result.snapshot;
      if (snapshot && snapshot.id === action.id) {
        var age = root.querySelector('[data-observation-age]');
        if (age) age.textContent = 'Last observed ' + snapshot.completed_at + ' · ' + snapshot.state + ' · ' + snapshot.scope;
        var value = root.querySelector('[data-observation-value]');
        if (value && snapshot.quality === 'complete' && Number.isSafeInteger(snapshot.bytes)) value.textContent = (snapshot.bytes / 1048576).toFixed(1) + ' MB (last observed)';
        var body = root.querySelector('[data-observation-checks]');
        if (body && Array.isArray(snapshot.checks)) {
          body.replaceChildren();
          snapshot.checks.forEach(function (check) {
            var row = document.createElement('tr');
            [check.label, check.ok ? 'ok on coordinator' : 'FAIL on coordinator', check.detail].forEach(function (text, index) {
              var cell = document.createElement('td'); cell.dataset.label = ['Check', 'Status', 'Detail'][index];
              cell.textContent = String(text); row.appendChild(cell);
            });
            body.appendChild(row);
          });
        }
      }
      if (['succeeded', 'failed', 'unconfirmed'].includes(action.state)) {
        clearTimeout(deadline); deadline = null; button.disabled = false;
        if (result.next_nonce) form.elements.request_key.value = result.next_nonce;
      }
    }
    function watch(url, id) {
      if (poller) poller.stop();
      clearTimeout(deadline);
      waitingExpired = false;
      root.dataset.observationUrl = window.TrellumActions.statusURL(url);
      root.dataset.observationId = id;
      poller = window.TrellumActions.createPoller({
        intervalMs: 2000, maxIntervalMs: 10000, timeoutMs: 10000,
        isCurrent: current,
        read: async function (context) {
          var response = await fetch(root.dataset.observationUrl, {signal: context.signal, credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
          var result = await response.json();
          if (!response.ok || !result.action || result.action.id !== id) throw new Error('Observation unavailable');
          return result;
        },
        onStatus: render,
        onError: function () { feedback('Observation unavailable; server outcome remains unconfirmed. Use Read latest observation before retrying.'); },
        isTerminal: function (result) { return ['succeeded', 'failed', 'unconfirmed'].includes(result.action.state); }
      });
      poller.start();
      if (document.hidden) poller.pause();
      deadline = setTimeout(function () {
        waitingExpired = true;
        poller.pause(); button.disabled = false;
        feedback('Waiting timed out; server outcome remains unconfirmed. Use Read latest observation before retrying.');
      }, 45000);
    }
    async function submit(event) {
      event.preventDefault();
      if (post || button.disabled) return;
      button.disabled = true;
      feedback('Requesting measurement…');
      post = new AbortController();
      var timer = setTimeout(function () { if (post) post.abort(); }, 10000);
      try {
        var response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', signal: post.signal, headers: {Accept: 'application/json'}});
        var result = await response.json();
        if (!current()) return;
        if (!response.ok || !result.action) { feedback(result.message || 'Measurement was not accepted.'); button.disabled = false; return; }
        feedback(result.action.message);
        watch(result.action.status_url, result.action.id);
      } catch (_) {
        feedback('Request outcome is unconfirmed. Read latest observation before retrying; no request was replayed.');
        if (current()) button.disabled = false;
      } finally { clearTimeout(timer); post = null; }
    }
    function visibility() { if (poller) { if (document.hidden) poller.pause(); else if (!waitingExpired) poller.resume(); } }
    function cleanup() {
      disposed = true; clearTimeout(deadline);
      if (poller) poller.stop(); if (post) post.abort();
      form.removeEventListener('submit', submit); document.removeEventListener('visibilitychange', visibility);
      controllers.delete(cleanup); root._operatorObservation = null;
      button.disabled = false;
    }
    form.addEventListener('submit', submit);
    document.addEventListener('visibilitychange', visibility);
    controllers.add(cleanup); root._operatorObservation = cleanup;
    cleanup.root = root;
    if (root.dataset.observationUrl && root.dataset.observationId) watch(root.dataset.observationUrl, root.dataset.observationId);
  }
  function initialize() {
    Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
    document.querySelectorAll('[data-operator-observation]').forEach(mount);
  }
  window.addEventListener('pagehide', function () { Array.from(controllers).forEach(function (cleanup) { cleanup(); }); });
  window.addEventListener('pageshow', initialize);
  document.addEventListener('settings:updated', initialize);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize); else initialize();
}());
