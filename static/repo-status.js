(function () {
  'use strict';
  var root = document.querySelector('[data-repo-status]');
  if (!root) return;
  var api = root.dataset.statusUrl, button = document.getElementById('repoSyncButton');
  var status = document.getElementById('repoPollStatus'), started = Date.now();
  var failures = 0, timer = null, active = true, lastMessage = '';
  var maxWait = 120000, interval = 2000;

  function message(text) {
    if (text !== lastMessage) status.textContent = lastMessage = text;
    status.hidden = false;
  }
  function busy(text) {
    if (button) { button.disabled = true; button.textContent = text; }
    message(text);
  }
  function safeToReload() {
    return !document.querySelector('form[data-settings-form].is-dirty') &&
      !document.querySelector('dialog[open]');
  }
  function reloadWhenSafe() {
    if (!active) return;
    if (!safeToReload()) {
      message('Check finished. Save your settings or close the dialog to refresh results.');
      timer = setTimeout(reloadWhenSafe, interval);
      return;
    }
    active = false;
    location.reload();
  }
  function tick() {
    if (!active) return;
    if (Date.now() - started >= maxWait || failures >= 5) {
      active = false;
      message('Could not confirm completion. Use the button to try again.');
      if (button) { button.disabled = false; button.textContent = button.dataset.idleLabel; }
      return;
    }
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    fetch(api, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal})
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(function (result) {
        if (!active) return;
        failures = 0;
        if (result.sync_requested || result.publishing) {
          busy(result.publishing ? 'Publishing…' : 'Checking for changes…');
          timer = setTimeout(tick, interval);
        } else reloadWhenSafe();
      })
      .catch(function () {
        if (!active) return;
        failures += 1;
        message('Could not check status. Retrying…');
        timer = setTimeout(tick, interval);
      })
      .finally(function () { clearTimeout(timeout); });
  }
  if (root.dataset.syncRequested === 'true' || root.dataset.publishRequested === 'true') {
    busy(root.dataset.publishRequested === 'true' ? 'Publishing…' : 'Checking for changes…');
    timer = setTimeout(tick, interval);
  }
  var syncForm = document.getElementById('repoSyncForm');
  if (syncForm) syncForm.addEventListener('submit', function () { busy('Checking for changes…'); });
  window.addEventListener('pagehide', function () { active = false; clearTimeout(timer); });
})();
