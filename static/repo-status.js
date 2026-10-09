(function () {
  'use strict';
  var results = document.querySelector('[data-repo-results]');
  var root = document.querySelector('[data-repo-status]');
  var status = document.getElementById('repoPollStatus');
  if (!results || !status) return;
  var statusUrl = (root && root.dataset.statusUrl) || results.dataset.statusUrl;

  var button = document.getElementById('repoSyncButton');
  var active = true, present = true, lifecycle = 0, resumeNeeded = false;
  var timer = null, request = null, refreshRequest = null, verifyRequest = null, publishRequest = null;
  var publishUnknown = false;
  var started = 0, failures = 0, refreshFailures = 0, generation = 0;
  var pendingRefresh = false, refreshBlocked = false, ignoreDialogClose = false;
  var modeConfirmed = false, confirmedModeClose = false, publishCheck = 0, publishVerifiedSha = '';
  var lastMessage = '', maxWait = 120000, interval = 2000;

  function message(text) {
    if (!present) return;
    if (text !== lastMessage) status.textContent = lastMessage = text;
    status.hidden = !text;
  }

  function setBusy(label) {
    if (!present) return;
    button = document.getElementById('repoSyncButton');
    if (button) { button.disabled = true; button.textContent = label; }
    message(label);
  }

  function restoreButton() {
    if (!present) return;
    button = document.getElementById('repoSyncButton');
    if (button) {
      button.disabled = false;
      button.textContent = button.dataset.idleLabel || 'Sync now';
    }
  }

  function currentRoot() {
    root = results.querySelector('[data-repo-status]');
    if (root && root.dataset.statusUrl) statusUrl = root.dataset.statusUrl;
    return root;
  }

  function setConnectionMode(mode) {
    if (mode !== 'auto' && mode !== 'manual') return;
    var hidden = document.querySelector('#repoConnectionForm input[name="publish_mode"]');
    if (hidden) hidden.value = mode;
  }

  function busyState(data) {
    return !!(data && (data.sync_requested || data.publishing || data.publish_requested));
  }

  function busyLabel(data) {
    return data && (data.publishing || data.publish_requested) ? 'Publishing…' : 'Checking for changes…';
  }

  function hasPendingWork() {
    return !!results.querySelector('form[data-settings-form].is-dirty, form.is-saving, form[aria-busy="true"], dialog[open]');
  }

  function deferRefresh() {
    pendingRefresh = true;
    message('Check finished. Save your settings or close the dialog to refresh results.');
    if (present && !refreshBlocked && !timer) timer = setTimeout(function () {
      timer = null;
      refreshResults();
    }, interval);
  }

  function focusIdentity(rootNode) {
    var el = document.activeElement;
    if (!el || !rootNode.contains(el)) return null;
    if (el.id) return { id: el.id };
    var same = Array.prototype.filter.call(rootNode.querySelectorAll(el.tagName.toLowerCase()), function (item) {
      return item.name === el.name && item.type === el.type;
    });
    return { tag: el.tagName.toLowerCase(), name: el.name, type: el.type, index: same.indexOf(el) };
  }

  function findFocus(rootNode, identity) {
    if (!identity) return null;
    if (identity.id) return rootNode.querySelector('#' + CSS.escape(identity.id));
    return Array.prototype.filter.call(rootNode.querySelectorAll(identity.tag), function (el) {
      return el.name === identity.name && el.type === identity.type;
    })[identity.index] || null;
  }

  function detailKey(detail, index) {
    return detail.dataset.preserveKey || [
      detail.querySelector('summary') ? detail.querySelector('summary').textContent.trim() : '',
      detail.closest('tr') ? detail.closest('tr').textContent.trim() : '',
      index
    ].join('|');
  }

  function captureView(rootNode) {
    var openDetails = [];
    rootNode.querySelectorAll('details').forEach(function (detail, index) {
      if (detail.open) openDetails.push(detailKey(detail, index));
    });
    return {
      x: window.scrollX,
      y: window.scrollY,
      focus: focusIdentity(rootNode),
      openDetails: openDetails
    };
  }

  function restoreView(rootNode, view) {
    var open = new Set(view.openDetails);
    rootNode.querySelectorAll('details').forEach(function (detail, index) {
      detail.open = open.has(detailKey(detail, index));
    });
    var focus = findFocus(rootNode, view.focus);
    if (!focus && view.focus) focus = rootNode.querySelector('#repoSyncButton');
    if (focus && focus.focus) focus.focus({ preventScroll: true });
    window.scrollTo(view.x, view.y);
  }

  function removeScripts(node) {
    if (node.nodeType !== 1) return;
    if (node.tagName === 'SCRIPT') { node.remove(); return; }
    node.querySelectorAll('script').forEach(function (script) { script.remove(); });
  }

  function applyResults(html, responseUrl, requestGeneration) {
    if (!present || requestGeneration !== generation) return;
    if (hasPendingWork()) { deferRefresh(); return; }
    var finalUrl = new URL(responseUrl, window.location.href);
    if (finalUrl.origin !== window.location.origin || finalUrl.pathname !== window.location.pathname) {
      throw new Error('Results came from a different page');
    }
    var parsed = new DOMParser().parseFromString(html, 'text/html');
    var incoming = parsed.querySelector('[data-repo-results]');
    if (!incoming) throw new Error('Repository results were missing');

    var view = captureView(results);
    var children = Array.prototype.slice.call(incoming.childNodes).map(function (child) {
      var clone = child.cloneNode(true);
      removeScripts(clone);
      return clone;
    });
    results.replaceChildren.apply(results, children);
    if (window.TrellumSettingsForms && typeof window.TrellumSettingsForms.init === 'function') {
      window.TrellumSettingsForms.init(results);
    }
    restoreView(results, view);
    pendingRefresh = false;
    refreshFailures = 0;
    message('');
    currentRoot();
    setConnectionMode(root && root.dataset.publishMode);
    if (root && (root.dataset.syncRequested === 'true' || root.dataset.publishRequested === 'true')) {
      startPolling({ sync_requested: root.dataset.syncRequested === 'true', publish_requested: root.dataset.publishRequested === 'true' });
    } else {
      stopPolling();
      restoreButton();
    }
  }

  function refreshResults() {
    if (!present || !pendingRefresh || refreshBlocked) return;
    if (hasPendingWork()) { deferRefresh(); return; }
    clearTimeout(timer);
    timer = null;
    if (refreshRequest) return;
    var requestGeneration = ++generation;
    var controller = new AbortController();
    refreshRequest = controller;
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    fetch(window.location.href, {
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { 'Accept': 'text/html' }
    }).then(function (response) {
      if (!response.ok) throw new Error('Refresh failed (' + response.status + ')');
      var type = response.headers.get('Content-Type') || '';
      if (type.indexOf('text/html') === -1) throw new Error('Refresh did not return a page');
      return Promise.all([response.text(), Promise.resolve(response.url || window.location.href)]);
    }).then(function (parts) {
      applyResults(parts[0], parts[1], requestGeneration);
    }).catch(function () {
      if (!present || requestGeneration !== generation) return;
      refreshFailures++;
      message(refreshFailures >= 5 ? 'Results could not be refreshed. Save pending changes, then use Sync now to try again.' : 'Could not refresh results. Retrying…');
      if (refreshFailures < 5) timer = setTimeout(function () { timer = null; refreshResults(); }, interval);
      else { pendingRefresh = false; restoreButton(); }
    }).finally(function () {
      clearTimeout(timeout);
      if (refreshRequest === controller) {
        refreshRequest = null;
        if (present && pendingRefresh && !refreshBlocked && requestGeneration !== generation) requestDeferredRefresh();
      }
    });
  }

  function stopPolling() {
    active = false;
    clearTimeout(timer);
    timer = null;
    if (request) request.abort();
    request = null;
  }

  function startPolling(initial) {
    if (!present) return;
    clearTimeout(timer);
    timer = null;
    if (!active) active = true;
    started = Date.now();
    failures = 0;
    pendingRefresh = false;
    generation++;
    setBusy(busyLabel(initial || {}));
    schedulePoll(0);
  }

  function schedulePoll(delay) {
    if (!present || !active || request || timer) return;
    timer = setTimeout(function () { timer = null; poll(); }, delay == null ? interval : delay);
  }

  function poll() {
    if (!present) return;
    if (!active || document.hidden) { schedulePoll(interval); return; }
    if (Date.now() - started >= maxWait || failures >= 5) {
      stopPolling();
      message('Could not confirm completion. Use the button to try again.');
      restoreButton();
      return;
    }
    currentRoot();
    var url = statusUrl;
    if (!url) { stopPolling(); return; }
    var controller = new AbortController();
    request = controller;
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    var requestGeneration = generation;
    var nextDelay = null;
    fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        if (!present || !active || requestGeneration !== generation) return;
        failures = 0;
        if (busyState(data)) {
          setBusy(busyLabel(data));
          nextDelay = interval;
          return;
        }
        active = false;
        pendingRefresh = true;
        if (hasPendingWork()) deferRefresh();
        else refreshResults();
      })
      .catch(function () {
        if (!present || !active || requestGeneration !== generation) return;
        failures++;
        message('Could not check status. Retrying…');
        nextDelay = interval;
      })
      .finally(function () {
        clearTimeout(timeout);
        if (request === controller) request = null;
        if (present && active && (requestGeneration !== generation || nextDelay !== null)) {
          schedulePoll(requestGeneration === generation ? nextDelay : 0);
        }
      });
  }

  function csrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function publishUrl() {
    var url = new URL(statusUrl, window.location.href);
    url.pathname = url.pathname.replace(/\/status$/, '/publish');
    return url.pathname + url.search;
  }

  function openPublishDialog() {
    if (!present) return;
    var dialog = document.getElementById('publishDialog');
    var review = document.getElementById('reviewBtn');
    var go = document.getElementById('publishGo');
    var note = document.getElementById('publishNote');
    if (!dialog || !review || !go || !note) return;
    publishCheck++;
    var check = publishCheck, pageLifecycle = lifecycle;
    if (verifyRequest) verifyRequest.abort();
    publishVerifiedSha = '';
    note.hidden = true;
    note.textContent = 'Checking the current repository head…';
    note.hidden = false;
    go.disabled = true;
    dialog.showModal();
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    verifyRequest = controller;
    fetch(statusUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal })
      .then(function (response) {
        if (!response.ok) throw new Error('Status check failed');
        return response.json();
      })
      .then(function (data) {
        if (!present || pageLifecycle !== lifecycle || check !== publishCheck || !dialog.open) return;
        var reviewedSha = review.dataset.remote;
        if (publishUnknown && (busyState(data) || data.last_synced_sha === reviewedSha)) {
          publishUnknown = false;
          note.textContent = busyState(data) ? 'Publishing is already in progress. Close this dialog to see updated status.' : 'This revision is already published. Close this dialog to refresh results.';
          note.hidden = false;
          go.disabled = true;
          if (busyState(data)) startPolling(data);
          else { pendingRefresh = true; requestDeferredRefresh(); }
          return;
        }
        publishUnknown = false;
        if (data.remote_sha && data.remote_sha === reviewedSha) {
          publishVerifiedSha = reviewedSha;
          note.hidden = true;
          go.disabled = false;
        } else {
          note.textContent = 'New commits arrived — review again.';
          note.hidden = false;
          go.disabled = true;
        }
      })
      .catch(function () {
        if (!present || pageLifecycle !== lifecycle || check !== publishCheck || !dialog.open) return;
        note.textContent = 'Could not verify the current repository head. Close this dialog and try again.';
        note.hidden = false;
        go.disabled = true;
      })
      .finally(function () {
        clearTimeout(timeout);
        if (verifyRequest === controller) verifyRequest = null;
      });
  }

  function submitPublish() {
    if (!present || publishRequest || publishUnknown) return;
    var review = document.getElementById('reviewBtn');
    var go = document.getElementById('publishGo');
    var note = document.getElementById('publishNote');
    var dialog = document.getElementById('publishDialog');
    if (!review || !go || !note || !dialog || go.disabled || !publishVerifiedSha) return;
    var pageLifecycle = lifecycle;
    var body = { to: publishVerifiedSha };
    var rebuild = document.getElementById('publishRebuild');
    if (rebuild) body.rebuild = rebuild.checked;
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    go.disabled = true;
    refreshBlocked = true;
    generation++;
    publishRequest = controller;
    fetch(publishUrl(), {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) {
          var error = new Error(data.error || 'The server rejected the publish request.');
          error.rejected = response.status < 500;
          throw error;
        }
        return data;
      });
    }).then(function () {
      if (!present || pageLifecycle !== lifecycle) return;
      ignoreDialogClose = true;
      dialog.close();
      ignoreDialogClose = false;
      refreshBlocked = false;
      startPolling({ publishing: true, sync_requested: true });
    }).catch(function (error) {
      if (!present || pageLifecycle !== lifecycle) return;
      refreshBlocked = false;
      publishVerifiedSha = '';
      publishUnknown = !error.rejected;
      note.textContent = publishUnknown
        ? 'The publish outcome is unknown. Close this dialog and review the current status before publishing again.'
        : error.message;
      note.hidden = false;
      go.disabled = true;
    }).finally(function () {
      clearTimeout(timeout);
      if (publishRequest === controller) publishRequest = null;
    });
  }

  function onSubmit(event) {
    if (!present) return;
    var form = event.target;
    if (!form || !form.matches('form')) return;
    if (form.id === 'modeForm') {
      var selected = form.querySelector('input[name="publish_mode"]:checked');
      var confirmDialog = document.getElementById('modeConfirm');
      if (selected && selected.value === 'auto' && confirmDialog && !modeConfirmed) {
        event.preventDefault();
        event.stopImmediatePropagation();
        confirmDialog.showModal();
        return;
      }
      if (modeConfirmed) modeConfirmed = false;
    }
    if (form.id === 'repoConnectionForm' || form.id === 'modeForm' || form.id === 'repoSyncForm') {
      generation++;
      refreshBlocked = true;
      if (refreshRequest) refreshRequest.abort();
    }
    if (form.id === 'repoSyncForm') setBusy('Checking for changes…');
  }

  document.addEventListener('submit', onSubmit, true);
  document.addEventListener('click', function (event) {
    if (!present) return;
    var target = event.target.closest && event.target.closest('#reviewBtn, #publishGo, #modeConfirm [data-go]');
    if (!target) return;
    if (target.id === 'reviewBtn') { event.preventDefault(); openPublishDialog(); }
    else if (target.id === 'publishGo') { event.preventDefault(); submitPublish(); }
    else {
      event.preventDefault();
      var dialog = document.getElementById('modeConfirm');
      var form = document.getElementById('modeForm');
      if (!dialog || !form) return;
      modeConfirmed = true;
      confirmedModeClose = true;
      refreshBlocked = true;
      generation++;
      ignoreDialogClose = true;
      dialog.close();
      ignoreDialogClose = false;
      form.requestSubmit();
    }
  });

  document.addEventListener('close', function (event) {
    if (!present) return;
    if (event.target.id === 'publishDialog') {
      publishCheck++;
      if (verifyRequest) verifyRequest.abort();
    }
    var confirmedClose = event.target.id === 'modeConfirm' && confirmedModeClose;
    if (confirmedClose) confirmedModeClose = false;
    if (!ignoreDialogClose && !confirmedClose) requestDeferredRefresh();
    if (event.target.id === 'modeConfirm' && !modeConfirmed && !confirmedClose) {
      var form = document.getElementById('modeForm');
      if (form) {
        var manual = form.querySelector('input[name="publish_mode"][value="manual"]');
        if (manual) { manual.checked = true; manual.dispatchEvent(new Event('change', { bubbles: true })); }
      }
    }
  }, true);

  function requestDeferredRefresh() {
    if (!present || !pendingRefresh || refreshBlocked) return;
    if (hasPendingWork()) { deferRefresh(); return; }
    clearTimeout(timer);
    timer = null;
    refreshResults();
  }

  document.addEventListener('settings:saved', requestDeferredRefresh);
  document.addEventListener('reset', function () { setTimeout(requestDeferredRefresh, 0); });
  function onSettingsEdit(event) {
    if (results.contains(event.target)) requestDeferredRefresh();
  }
  document.addEventListener('input', onSettingsEdit);
  document.addEventListener('change', onSettingsEdit);
  document.addEventListener('settings:success', function (event) {
    var form = event.target.closest && event.target.closest('form[data-settings-save]');
    if (!present || !form) return;
    if (form.id === 'defaultAudienceForm') return;
    var data = event.detail || {};
    setConnectionMode(data.publish_mode);
    if (root && data.publish_mode) root.dataset.publishMode = data.publish_mode;
    refreshBlocked = false;
    generation++;
    if (busyState(data) || form.id === 'repoSyncForm' || form.id === 'repoConnectionForm') {
      startPolling(data);
    } else {
      pendingRefresh = true;
      refreshResults();
    }
  });
  document.addEventListener('settings:error', function (event) {
    var form = event.target.closest && event.target.closest('form[data-settings-save]');
    if (!present || !form) return;
    refreshBlocked = false;
    if (form.id === 'repoSyncForm') restoreButton();
    requestDeferredRefresh();
  });

  window.addEventListener('pagehide', function () {
    resumeNeeded = started > 0 || pendingRefresh || !!verifyRequest || !!publishRequest;
    if (publishRequest) publishUnknown = true;
    present = false;
    active = false;
    lifecycle++;
    generation++;
    publishCheck++;
    publishVerifiedSha = '';
    clearTimeout(timer);
    timer = null;
    if (request) request.abort();
    if (refreshRequest) refreshRequest.abort();
    if (verifyRequest) verifyRequest.abort();
    if (publishRequest) publishRequest.abort();
  });
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    present = true;
    refreshBlocked = false;
    currentRoot();
    var go = document.getElementById('publishGo');
    if (go) go.disabled = true;
    if (publishUnknown) {
      var note = document.getElementById('publishNote');
      if (note) { note.textContent = 'The publish outcome is unknown. Close this dialog and review the current status before publishing again.'; note.hidden = false; }
    }
    if (pendingRefresh) refreshResults();
    else if (resumeNeeded || (root && (root.dataset.syncRequested === 'true' || root.dataset.publishRequested === 'true'))) startPolling({});
    resumeNeeded = false;
  });

  currentRoot();
  setConnectionMode(root && root.dataset.publishMode);
  if (root && (root.dataset.syncRequested === 'true' || root.dataset.publishRequested === 'true')) {
    startPolling({ sync_requested: root.dataset.syncRequested === 'true', publish_requested: root.dataset.publishRequested === 'true' });
  }
})();
