/* Portal-owned report notices; tenant-authored output uses separate routes. */
(function () {
  'use strict';
  var button = document.querySelector('[data-report-rebuild]');
  if (button) {
    var status = document.querySelector('[data-rebuild-status]');
    button.addEventListener('click', async function () {
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = 'Queuing…';
      status.textContent = '';
      status.hidden = true;
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 15000);
      try {
        var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
        var response = await fetch(button.dataset.runUrl, {
          method: 'POST', credentials: 'same-origin', redirect: 'error',
          signal: controller.signal,
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf ? csrf.value : ''},
          body: '{}'
        });
        if (response.status >= 400 && response.status < 500) {
          button.disabled = false;
          button.textContent = 'Rebuild now';
          status.hidden = false;
          status.textContent = response.status === 403
            ? 'You no longer have permission to rebuild this report, or your session has expired.'
            : 'The rebuild request was rejected. No rebuild was queued.';
          return;
        }
        if (!response.ok) throw new Error('unconfirmed');
        var result = await response.json();
        if (!result.ok || !['queued', 'already_running'].includes(result.status)) throw new Error('unconfirmed');
        button.textContent = result.status === 'already_running'
          ? 'Rebuild already running' : 'Rebuild queued — this can take a few minutes';
      } catch (_) {
        button.textContent = 'Rebuild unconfirmed';
        status.hidden = false;
        status.textContent = 'The request may have been queued. Check Operations before starting another rebuild.';
      } finally {
        clearTimeout(timeout);
      }
    });
  }

  if (!document.querySelector('[data-report-storage-outage]')) return;
  var timer = null;
  var flight = null;
  var stopped = false;
  var delay = 5000;
  function pause() {
    clearTimeout(timer);
    timer = null;
    if (flight) flight.abort();
  }
  function schedule() {
    if (!stopped && !document.hidden && !flight && !timer) timer = setTimeout(check, delay);
  }
  async function check() {
    timer = null;
    if (stopped || document.hidden || flight) return;
    var controller = new AbortController();
    flight = controller;
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    try {
      // HEAD probes the authorized entry route without downloading report data.
      // A redirect means output (or a sign-in page) is ready for navigation.
      var response = await fetch(location.href, {
        method: 'HEAD', credentials: 'same-origin', cache: 'no-store',
        redirect: 'manual', signal: controller.signal
      });
      if (flight !== controller || controller.signal.aborted || stopped || document.hidden) return;
      if (response.ok || response.type === 'opaqueredirect') {
        stopped = true;
        location.replace(location.href);
      }
    } catch (_) {
      // Read failures recover automatically; never replay a rebuild POST.
    } finally {
      clearTimeout(timeout);
      if (flight === controller) flight = null;
      delay = Math.min(delay * 2, 30000);
      schedule();
    }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) pause(); else schedule();
  });
  window.addEventListener('pagehide', function () { stopped = true; pause(); });
  window.addEventListener('pageshow', function () { stopped = false; schedule(); });
  schedule();
})();
