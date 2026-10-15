/* Operator measurements that keep themselves current.

   The coordinator measures; this page asks for a new measurement whenever the
   last one is about a minute old, watches it with the shared poller and renders
   the result. Nobody presses anything. The server's own rules still apply: one
   measurement in flight, thirty seconds between admissions, and an admission key
   that expires. Every refusal says what to wait for and carries a fresh key, so
   the page simply waits and tries again. */
(function () {
  'use strict';
  var controllers = new Set();
  var leaving = false;
  var observer = null;
  var FINISHED = ['succeeded', 'failed', 'unconfirmed'];

  function ago(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    if (seconds < 10) return 'just now';
    if (seconds < 60) return seconds + ' seconds ago';
    var units = [[86400, 'day'], [3600, 'hour'], [60, 'minute']];
    for (var i = 0; i < units.length; i++) {
      if (seconds >= units[i][0]) {
        var count = Math.floor(seconds / units[i][0]);
        return count + ' ' + units[i][1] + (count === 1 ? '' : 's') + ' ago';
      }
    }
  }
  function cell(label, parent) {
    var td = document.createElement('td');
    td.dataset.label = label;
    parent.appendChild(td);
    return td;
  }
  function checkRow(check) {
    var row = document.createElement('tr');
    var name = document.createElement('strong');
    name.textContent = String(check.label);
    cell('Check', row).appendChild(name);
    var badge = document.createElement('span');
    badge.className = 'ui-badge ' + (check.ok ? 'ok' : 'fail');
    badge.textContent = check.ok ? 'ok' : 'FAIL';
    cell('Status', row).appendChild(badge);
    var detail = cell('Detail', row);
    detail.className = 'muted';
    detail.textContent = String(check.detail);
    return row;
  }

  function mount(root) {
    if (root._operatorObservation || !window.TrellumActions) return;
    var form = root.querySelector('[data-observation-admission]');
    if (!form) return;
    var status = root.querySelector('[data-observation-status]');
    var every = (Number(root.dataset.observationRefresh) || 60) * 1000;
    var doneText = root.dataset.observationDone || 'Checked';
    var busyText = root.dataset.observationBusy || 'Checking…';
    // The last snapshot shown. Its age comes from the server so that the
    // browser clock never has to agree with the server's.
    var snap = {at: root.dataset.observationAt || null, age: null, since: performance.now(), node: root.dataset.observationNode || ''};
    if (snap.at && Number.isFinite(Number(root.dataset.observationAge))) snap.age = Number(root.dataset.observationAge);
    else snap.at = null;
    var shown = '';
    var measuring = false, note = '', failures = 0, nonceRetries = 0, nextAllowed = 0;
    var poller = null, flight = null, timer = null, clock = null, safety = null;
    var disposed = false, paused = document.hidden;

    function current() { return !disposed && !leaving && root.isConnected; }
    function age() { return snap.at === null ? Infinity : snap.age + (performance.now() - snap.since) / 1000; }
    function show() {
      if (!status) return;
      var text = measuring || snap.at === null ? busyText : doneText + ' ' + ago(age());
      if (!measuring && note) text += ' · ' + note;
      status.textContent = text;
      if (snap.at) {
        var when = new Date(snap.at);
        status.title = doneText.replace(/^[\s·]+/, '').replace(/^./, function (c) { return c.toUpperCase(); }) + ' ' +
          (isNaN(when) ? snap.at : when.toLocaleString()) + (snap.node ? ' on coordinator ' + snap.node : '');
      }
    }
    function render(snapshot) {
      if (!snapshot || typeof snapshot.completed_at !== 'string') return;
      if (snap.at && Date.parse(snapshot.completed_at) <= Date.parse(snap.at)) return;
      snap.at = snapshot.completed_at;
      snap.age = Number.isFinite(snapshot.age_seconds) ? snapshot.age_seconds : 0;
      snap.since = performance.now();
      snap.node = typeof snapshot.producer_node === 'string' ? snapshot.producer_node : '';
      var body = root.querySelector('[data-observation-checks]');
      if (body && Array.isArray(snapshot.checks)) {
        var signature = JSON.stringify(snapshot.checks);
        if (signature !== shown) { body.replaceChildren.apply(body, snapshot.checks.map(checkRow)); shown = signature; }
      }
      var value = root.querySelector('[data-observation-value]');
      if (value && snapshot.quality === 'complete' && Number.isSafeInteger(snapshot.bytes)) value.textContent = (snapshot.bytes / 1048576).toFixed(1) + ' MB';
    }
    function schedule() {
      clearTimeout(timer); timer = null;
      if (!current() || paused || measuring) return;
      timer = setTimeout(admit, Math.max(every - age() * 1000, nextAllowed - performance.now(), 0));
    }
    function wait(milliseconds) { nextAllowed = performance.now() + milliseconds; }
    function unfinished() {
      // The measurement could not be completed or read. Try again later; the
      // next admission is idempotent for a key that was already accepted.
      failures++; note = 'retrying';
      wait(Math.min(30000 * Math.pow(2, failures - 1), 120000));
      measuring = false; show(); schedule();
    }
    function keyFrom(body) {
      if (body && typeof body.next_nonce === 'string' && body.next_nonce) form.elements.request_key.value = body.next_nonce;
    }
    function finished(result) {
      return result.status === 404 || result.status === 401 ||
        (result.body && result.body.action && FINISHED.indexOf(result.body.action.state) !== -1);
    }
    function settle(result) {
      clearTimeout(safety); safety = null; poller = null;
      keyFrom(result.body);
      var succeeded = result.body && result.body.action && result.body.action.state === 'succeeded';
      if (!succeeded) { unfinished(); return; }
      failures = 0; nonceRetries = 0; note = '';
      measuring = false; show(); schedule();
    }
    function watch(url, id) {
      var statusUrl;
      try { statusUrl = window.TrellumActions.statusURL(url); } catch (_) { unfinished(); return; }
      if (poller) poller.stop();
      measuring = true; show();
      clearTimeout(safety);
      safety = setTimeout(function () {
        // Never watch forever. The server settles every measurement by its own
        // deadline; this only frees the page if it could not be reached.
        if (!current()) return;
        if (poller) poller.stop();
        poller = null; measuring = false; show(); schedule();
      }, 120000);
      poller = window.TrellumActions.createPoller({
        intervalMs: 2000, maxIntervalMs: 10000, timeoutMs: 10000,
        isCurrent: current,
        read: async function (context) {
          var response = await fetch(statusUrl, {signal: context.signal, credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
          var body = await response.json();
          if (!response.ok && response.status !== 404 && response.status !== 401) throw new Error('Status unavailable');
          return {status: response.status, body: body};
        },
        onStatus: function (result) {
          if (result.body && result.body.action && result.body.action.id !== id) return;
          if (result.body) render(result.body.snapshot);
          if (finished(result)) settle(result);
        },
        isTerminal: finished
      });
      poller.start();
      if (paused) poller.pause();
    }
    function refused(result) {
      var body = result.body || {};
      keyFrom(body); render(body.snapshot);
      measuring = false;
      if (result.status === 409 && body.busy && body.busy.status_url) { watch(body.busy.status_url, body.busy.id); return; }
      if (result.status === 429 || result.status === 409) {
        // Another measurement is running or just finished: wait, then look again.
        wait(result.status === 429 ? (Number(body.retry_after) || 30) * 1000 : 10000);
        show(); schedule(); return;
      }
      if (result.status === 400 && body.next_nonce && nonceRetries++ < 2) { show(); schedule(); return; }
      unfinished();
    }
    function admit() {
      timer = null;
      if (!current() || paused || measuring) return;
      measuring = true; show();
      var controller = new AbortController();
      flight = controller;
      var abort = setTimeout(function () { controller.abort(); }, 10000);
      fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', signal: controller.signal, headers: {Accept: 'application/json'}})
        .then(function (response) {
          return response.json().then(function (body) { return {status: response.status, ok: response.ok, body: body}; });
        })
        .then(function (result) {
          if (flight !== controller || !current()) return;
          if (result.ok && result.body && result.body.action) watch(result.body.action.status_url, result.body.action.id);
          else refused(result);
        })
        .catch(function () { if (flight === controller && current()) unfinished(); })
        .finally(function () { clearTimeout(abort); if (flight === controller) flight = null; });
    }
    function tick() { if (current() && !paused) show(); }
    function start() {
      clearInterval(clock);
      clock = setInterval(tick, 15000);
      show();
      if (measuring) return;
      var url = root.dataset.observationUrl, id = root.dataset.observationId;
      // The server may already know of a measurement in flight when it renders.
      delete root.dataset.observationUrl; delete root.dataset.observationId;
      if (url && id && !poller) watch(url, id); else schedule();
    }
    function visibility() {
      paused = document.hidden;
      if (paused) {
        clearTimeout(timer); timer = null; clearInterval(clock); clock = null;
        if (poller) poller.pause();
      } else {
        if (poller) poller.resume();
        clearInterval(clock); clock = setInterval(tick, 15000);
        show(); schedule();
      }
    }
    function suspend() {
      clearTimeout(timer); clearTimeout(safety); clearInterval(clock);
      timer = safety = clock = null;
      if (poller) poller.stop();
      poller = null;
      if (flight) { flight.abort(); flight = null; }
      measuring = false;
    }
    function restore() {
      if (!current()) return;
      paused = document.hidden;
      start();
    }
    function cleanup() {
      suspend(); disposed = true;
      document.removeEventListener('visibilitychange', visibility);
      controllers.delete(cleanup); root._operatorObservation = null;
    }
    document.addEventListener('visibilitychange', visibility);
    controllers.add(cleanup); root._operatorObservation = cleanup;
    cleanup.root = root; cleanup.suspend = suspend; cleanup.restore = restore;
    if (!paused) start();
    else show();
  }
  function initialize() {
    Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
    document.querySelectorAll('[data-operator-observation]').forEach(mount);
    if (!observer && controllers.size) {
      // Stop watching when the console swaps the page's content away.
      observer = new MutationObserver(function () {
        Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
      });
      observer.observe(document.body, {childList: true, subtree: true});
    }
  }
  window.addEventListener('pagehide', function () {
    leaving = true;
    Array.from(controllers).forEach(function (cleanup) { cleanup(); });
    if (observer) observer.disconnect();
    observer = null;
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
