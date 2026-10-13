/* Opt-in read lifecycle. Never submits, retries or cancels server mutations. */
(function (global) {
  'use strict';
  function statusURL(value) {
    if (typeof value !== 'string' || !value.trim() || value.length > 2048 || /[\\\x00-\x20\x7f]/.test(value)) throw new Error('Invalid status URL');
    var url = new URL(value, global.location.href);
    if (url.origin !== global.location.origin || !/^https?:$/.test(url.protocol) || url.username || url.password || url.hash) throw new Error('Status URL must be same-origin');
    return url.href;
  }
  function createPoller(options) {
    if (!options || typeof options.read !== 'function' || typeof options.onStatus !== 'function') throw new TypeError('read and onStatus are required');
    var interval = options.intervalMs === undefined ? 2000 : options.intervalMs;
    var maximum = options.maxIntervalMs === undefined ? 30000 : options.maxIntervalMs;
    var deadline = options.timeoutMs === undefined ? 10000 : options.timeoutMs;
    [interval, maximum, deadline].forEach(function (n) { if (!Number.isFinite(n) || n <= 0) throw new TypeError('Invalid timing'); });
    maximum = Math.max(interval, maximum);
    var epoch = 0, timer = null, active = null, started = false, paused = false, stopped = false, queued = false, failures = 0;
    function clearTimer() { if (timer !== null) clearTimeout(timer); timer = null; }
    function current(token) { return !stopped && !paused && epoch === token && (!options.isCurrent || options.isCurrent()); }
    function invalidate() {
      epoch++; clearTimer(); queued = false;
      if (active) { clearTimeout(active.timeout); active.controller.abort(); active = null; }
    }
    function schedule(delay) { clearTimer(); if (started && !paused && !stopped) timer = setTimeout(run, delay); }
    function run() {
      clearTimer();
      if (!started || stopped || paused) return;
      if (active) { queued = true; return; }
      var token = ++epoch, controller = new AbortController(), flight = {controller: controller, timeout: null};
      active = flight;
      var timeout = new Promise(function (_, reject) {
        flight.timeout = setTimeout(function () {
          controller.abort();
          reject(new Error('Status observation timed out; outcome is unconfirmed.'));
        }, deadline);
      });
      /* read must await body parsing AND all dependent status/history reads.
         It must not commit DOM itself; onStatus owns the guarded commit. */
      var read = Promise.resolve().then(function () {
        return options.read({signal: controller.signal, isCurrent: function () { return current(token) && !controller.signal.aborted; }, statusURL: statusURL});
      });
      Promise.race([read, timeout]).then(function (result) {
        if (!current(token) || controller.signal.aborted) return;
        failures = 0;
        var terminal = options.isTerminal ? options.isTerminal(result) : false;
        options.onStatus(result);
        if (terminal && current(token)) { started = false; queued = false; }
      }).catch(function (error) {
        if (!current(token)) return;
        failures++;
        if (options.onError) options.onError(error);
      }).finally(function () {
        clearTimeout(flight.timeout);
        controller.abort();
        /* Discard late work even when a custom reader ignores its signal. */
        if (active !== flight) return;
        active = null;
        epoch++;
        if (queued) { queued = false; schedule(0); }
        else schedule(Math.min(maximum, interval * Math.pow(2, Math.min(failures, 20))));
      });
    }
    function stop() { stopped = true; started = false; invalidate(); if (options.signal) options.signal.removeEventListener('abort', stop); }
    var api = {
      start: function () { if (stopped || started) return; started = true; schedule(0); },
      refresh: function () { if (stopped || paused) return; started = true; failures = 0; if (active) queued = true; else schedule(0); },
      pause: function () { paused = true; invalidate(); },
      resume: function () { if (stopped || !paused) return; paused = false; failures = 0; if (started) schedule(0); },
      stop: stop
    };
    if (options.signal) { if (options.signal.aborted) stop(); else options.signal.addEventListener('abort', stop, {once: true}); }
    return api;
  }
  global.TrellumActions = {createPoller: createPoller, statusURL: statusURL};
})(window);
