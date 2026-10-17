/* Operator figures that keep themselves current.

   System health and an organization's uploaded-file usage are measured by the
   coordinator, never while a page renders. While the page is open it asks for the
   latest measurement every few seconds (the request itself tells the coordinator
   that someone is looking, so a stale figure is measured again) and renders
   whatever is newer. Nobody presses anything, and a poll that fails is simply
   tried again. */
(function () {
  'use strict';
  var controllers = new Set();
  var leaving = false;
  var observer = null;

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
    var url = root.dataset.observationUrl;
    if (root._operatorObservation || !url) return;
    var status = root.querySelector('[data-observation-status]');
    var every = (Number(root.dataset.observationPoll) || 10) * 1000;
    var doneText = root.dataset.observationDone || 'Checked';
    var busyText = root.dataset.observationBusy || 'Checking…';
    // The shown measurement's age comes from the server, so the browser clock
    // never has to agree with the server's.
    var snap = {at: root.dataset.observationAt || null, age: Number(root.dataset.observationAge), since: performance.now()};
    if (!snap.at || !Number.isFinite(snap.age)) snap.at = null;
    var shown = '', timer = null, clock = null, flight = null, generation = 0;
    var disposed = false, paused = document.hidden;

    function current() { return !disposed && !leaving && root.isConnected; }
    function age() { return snap.at === null ? Infinity : snap.age + (performance.now() - snap.since) / 1000; }
    function show() {
      if (!status) return;
      status.textContent = snap.at === null ? busyText : doneText + ' ' + ago(age());
      if (snap.at) {
        var when = new Date(snap.at);
        status.title = doneText.replace(/^[\s·]+/, '').replace(/^./, function (c) { return c.toUpperCase(); }) + ' ' +
          (isNaN(when) ? snap.at : when.toLocaleString());
      }
    }
    function render(body) {
      if (!body || typeof body.completed_at !== 'string') return;
      if (snap.at && Date.parse(body.completed_at) <= Date.parse(snap.at)) return;
      snap.at = body.completed_at;
      snap.age = Number.isFinite(body.age_seconds) ? body.age_seconds : 0;
      snap.since = performance.now();
      var rows = root.querySelector('[data-observation-checks]');
      if (rows && Array.isArray(body.checks)) {
        var signature = JSON.stringify(body.checks);
        if (signature !== shown) { rows.replaceChildren.apply(rows, body.checks.map(checkRow)); shown = signature; }
      }
      var value = root.querySelector('[data-observation-value]');
      if (value && Number.isSafeInteger(body.bytes)) value.textContent = (body.bytes / 1048576).toFixed(1) + ' MB';
      show();
    }
    function schedule(delay) {
      clearTimeout(timer); timer = null;
      if (current() && !paused) timer = setTimeout(poll, delay);
    }
    function poll() {
      timer = null;
      if (!current() || paused) return;
      var mine = ++generation, controller = new AbortController();
      if (flight) flight.abort();
      flight = controller;
      var abort = setTimeout(function () { controller.abort(); }, 8000);
      fetch(url, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: {Accept: 'application/json'}})
        .then(function (response) { if (!response.ok) throw new Error('unavailable'); return response.json(); })
        .then(function (body) { if (mine === generation && current()) render(body); })
        .catch(function () { /* tried again on the next poll */ })
        .finally(function () {
          clearTimeout(abort);
          if (flight === controller) flight = null;
          // Until the first measurement arrives, look again soon.
          if (mine === generation) schedule(snap.at ? every : 3000);
        });
    }
    function suspend() {
      clearTimeout(timer); clearInterval(clock);
      timer = clock = null;
      generation++;
      if (flight) { flight.abort(); flight = null; }
    }
    // The page was just rendered with the latest figure, so a first look can wait;
    // coming back to a hidden or restored page looks at once.
    function resume(now) {
      if (!current()) return;
      paused = document.hidden;
      clearInterval(clock);
      if (paused) { clock = null; return; }
      clock = setInterval(show, 15000);
      show();
      if (now) poll(); else schedule(snap.at ? every : 3000);
    }
    function visibility() { if (document.hidden) { paused = true; suspend(); } else resume(true); }
    function cleanup() {
      suspend(); disposed = true;
      document.removeEventListener('visibilitychange', visibility);
      controllers.delete(cleanup); root._operatorObservation = null;
    }
    document.addEventListener('visibilitychange', visibility);
    controllers.add(cleanup); root._operatorObservation = cleanup;
    cleanup.root = root; cleanup.suspend = suspend; cleanup.restore = function () { resume(true); };
    resume(false);
  }
  function initialize() {
    Array.from(controllers).forEach(function (cleanup) { if (!cleanup.root.isConnected) cleanup(); });
    document.querySelectorAll('[data-operator-observation]').forEach(mount);
    if (!observer && controllers.size) {
      // Stop polling when the console swaps the page's content away.
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
