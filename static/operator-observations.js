/* Closed operator measurements: explicit admission, read-only observation. */
(function () {
  'use strict';
  var controllers = new Set();
  // Summary HTML may have been read before admission. Keep the current
  // document's operation when that HTML later remounts its observation panel.
  var observations = new Map();
  var leaving = false;
  var observer = null;
  function cacheKey(root, form) {
    try {
      var endpoint = new URL(form.action, location.href);
      var encoded = form.elements.request_key.value.split(':')[0].replace(/-/g, '+').replace(/_/g, '/');
      var scope = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=')));
      if (endpoint.origin !== location.origin || !Number.isSafeInteger(scope.actor) || scope.actor <= 0 || scope.kind !== root.dataset.observationKind) return null;
      if (scope.kind === 'full-health' ? scope.org !== null : scope.kind !== 'upload-usage' || !Number.isSafeInteger(scope.org) || scope.org <= 0) return null;
      // Decoding only partitions document-local state. The server verifies the
      // signature and actor/scope authorization on every admission and lookup.
      return JSON.stringify([endpoint.href, scope.actor, scope.kind, scope.org]);
    } catch (_) { return null; }
  }
  function mount(root) {
    if (root._operatorObservation || !window.TrellumActions) return;
    var form = root.querySelector('[data-observation-refresh]');
    if (!form) return;
    var status = root.querySelector('[data-observation-status]');
    var button = form.querySelector('button[type=submit]');
    var key = cacheKey(root, form);
    var state = key && observations.get(key);
    if (!state || (!state.retained && !state.id)) {
      state = {id: root.dataset.observationId, url: root.dataset.observationUrl,
        message: status ? status.textContent : '', nonce: form.elements.request_key.value,
        snapshot: null, retained: false, observing: !!root.dataset.observationId,
        expires: Date.now() + 45000, waitingExpired: false};
      if (key) observations.set(key, state);
    }
    var poller = null, post = null, deadline = null, disposed = false, epoch = 0;
    function current() { return !disposed && !leaving && root.isConnected && cacheKey(root, form) === key; }
    function busy(value) { button.disabled = value; if (value) form.setAttribute('aria-busy', 'true'); else form.removeAttribute('aria-busy'); }
    function feedback(message) { state.message = message; if (current() && status) status.textContent = message; }
    function renderSnapshot(snapshot) {
      if (!snapshot) return;
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
    function render(result) {
      var action = result.action;
      if (!action || action.id !== root.dataset.observationId) throw new Error('Obsolete observation');
      feedback(action.message);
      var snapshot = result.snapshot;
      if (snapshot && snapshot.id === action.id) {
        state.snapshot = snapshot; renderSnapshot(snapshot);
      }
      if (['succeeded', 'failed', 'unconfirmed'].includes(action.state)) {
        clearTimeout(deadline); deadline = null; state.observing = false; busy(false);
        if (result.next_nonce) { state.nonce = result.next_nonce; form.elements.request_key.value = state.nonce; }
      }
    }
    function watch(url, id, fresh) {
      if (poller) poller.stop();
      clearTimeout(deadline);
      state.url = window.TrellumActions.statusURL(url); state.id = id;
      root.dataset.observationUrl = state.url; root.dataset.observationId = state.id;
      if (fresh) { state.expires = Date.now() + 45000; state.waitingExpired = false; state.observing = true; }
      if (!state.observing || state.waitingExpired) return;
      busy(true);
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
        if (!current()) return;
        state.waitingExpired = true; state.observing = false;
        poller.pause(); busy(false);
        feedback('Waiting timed out; server outcome remains unconfirmed. Use Read latest observation before retrying.');
      }, Math.max(0, state.expires - Date.now()));
    }
    async function submit(event) {
      event.preventDefault();
      if (!current() || post || button.disabled) return;
      if (poller) poller.stop(); clearTimeout(deadline);
      state.retained = true; state.observing = false;
      state.id = null; state.url = null;
      delete root.dataset.observationId; delete root.dataset.observationUrl;
      busy(true);
      feedback('Requesting measurement…');
      var generation = ++epoch, flight = new AbortController(); post = flight;
      var timer = setTimeout(function () {
        if (post !== flight || generation !== epoch) return;
        ++epoch; post = null; flight.abort(); busy(false);
        feedback('Request outcome is unconfirmed. Read latest observation before retrying; no request was replayed.');
      }, 10000);
      try {
        var response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', signal: flight.signal, headers: {Accept: 'application/json'}});
        var result = await response.json();
        if (!current() || generation !== epoch || flight.signal.aborted) return;
        if (!response.ok || !result.action) { feedback(result.message || 'Measurement was not accepted.'); busy(false); return; }
        feedback(result.action.message);
        watch(result.action.status_url, result.action.id, true);
      } catch (_) {
        if (!current() || generation !== epoch) return;
        feedback('Request outcome is unconfirmed. Read latest observation before retrying; no request was replayed.');
        busy(false);
      } finally { clearTimeout(timer); if (post === flight) post = null; }
    }
    function visibility() { if (poller && current()) { if (document.hidden) poller.pause(); else if (!state.waitingExpired) poller.resume(); } }
    function suspend() {
      ++epoch; clearTimeout(deadline);
      if (poller) poller.stop(); poller = null;
      if (post) {
        post.abort(); post = null;
        feedback('Request outcome is unconfirmed. Read latest observation before retrying; no request was replayed.');
      }
      busy(false);
    }
    function restore() {
      if (!current()) return;
      feedback(state.message);
      if (state.id && state.url) watch(state.url, state.id, false);
      else { delete root.dataset.observationId; delete root.dataset.observationUrl; }
    }
    function cleanup() {
      suspend(); disposed = true;
      form.removeEventListener('submit', submit); document.removeEventListener('visibilitychange', visibility);
      controllers.delete(cleanup); root._operatorObservation = null;
      button.disabled = false;
    }
    form.addEventListener('submit', submit);
    document.addEventListener('visibilitychange', visibility);
    controllers.add(cleanup); root._operatorObservation = cleanup;
    cleanup.root = root; cleanup.suspend = suspend; cleanup.restore = restore;
    // Retain the admission key for deduplication; never copy a CSRF token.
    if (state.retained) form.elements.request_key.value = state.nonce;
    renderSnapshot(state.snapshot); restore();
  }
  function initialize() {
    Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
    document.querySelectorAll('[data-operator-observation]').forEach(mount);
    if (!observer && controllers.size) {
      observer = new MutationObserver(function () {
        Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
      });
      observer.observe(document.body, {childList: true, subtree: true});
    }
  }
  window.addEventListener('pagehide', function () {
    leaving = true;
    Array.from(controllers).forEach(function (cleanup) { cleanup(); });
    if (observer) observer.disconnect(); observer = null;
  });
  window.addEventListener('pageshow', function () { leaving = false; initialize(); });
  document.addEventListener('console:native-start', function () {
    leaving = true; controllers.forEach(function (cleanup) { cleanup.suspend(); });
  });
  document.addEventListener('console:native-restored', function () {
    leaving = false; controllers.forEach(function (cleanup) { cleanup.restore(); }); initialize();
  });
  document.addEventListener('settings:updated', initialize);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize); else initialize();
}());
