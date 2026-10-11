(function () {
  'use strict';
  var form = document.querySelector('form[data-alert-editor]');
  if (!form) return;
  var result = document.getElementById('alertPreview');
  var version = 0, controller = null, button = form.querySelector('[data-alert-preview]');
  var priorFocus = null, priorPosition = null;
  var interaction = 0, interactionAtStart = 0, previewRunning = false;

  document.addEventListener('focusin', function (event) {
    if (previewRunning && event.target !== button) interaction++;
  });
  ['wheel', 'touchstart'].forEach(function (type) {
    document.addEventListener(type, function () { if (previewRunning) interaction++; }, { passive: true });
  });

  button.addEventListener('pointerdown', function () {
    priorFocus = document.activeElement;
    priorPosition = { x: scrollX, y: scrollY };
  });

  function clearErrors() {
    form.querySelectorAll('[data-alert-error]').forEach(function (node) { node.remove(); });
    form.querySelectorAll('[data-alert-invalid]').forEach(function (field) {
      field.removeAttribute('aria-invalid');
      delete field.dataset.alertInvalid;
    });
  }
  function cancelPending(message, clearResult) {
    version++;
    if (controller) controller.abort();
    controller = null;
    previewRunning = false;
    button.disabled = false;
    result.setAttribute('aria-busy', 'false');
    if (clearResult) {
      result.hidden = true;
      result.replaceChildren();
    }
    if (message) status(message);
  }
  function status(text) {
    var node = form.querySelector('[data-alert-preview-status]');
    if (!node) {
      node = document.createElement('span');
      node.setAttribute('data-alert-preview-status', '');
      node.setAttribute('role', 'status');
      node.setAttribute('aria-live', 'polite');
      button.insertAdjacentElement('afterend', node);
    }
    node.textContent = text;
  }
  function invalidate() {
    cancelPending('Draft changed. Test it again to see current results.', true);
    clearErrors();
  }
  function showErrors(errors) {
    Object.keys(errors || {}).forEach(function (name) {
      var fields = Array.prototype.filter.call(form.elements, function (field) { return field.name === name; });
      var message = document.createElement('span');
      message.setAttribute('data-alert-error', '');
      message.className = 'settings-form-error';
      message.textContent = [].concat(errors[name]).join(' ');
      if (fields.length) {
        var field = fields[fields.length - 1];
        field.setAttribute('aria-invalid', 'true');
        field.dataset.alertInvalid = '1';
        (field.closest('.ui-field') || field).insertAdjacentElement('beforeend', message);
      } else if (name === '__all__') {
        result.replaceChildren();
        result.appendChild(message);
        result.hidden = false;
      }
    });
  }
  function line(parent, tag, text) {
    if (!text) return;
    var node = document.createElement(tag);
    node.textContent = text;
    parent.appendChild(node);
  }
  function show(data) {
    var run = data.result || {};
    result.replaceChildren();
    line(result, 'h2', 'Draft test · ' + (run.condition || run.status || 'Result unavailable'));
    line(result, 'strong', run.title);
    line(result, 'p', run.message);
    if (run.evidence && run.evidence.length) {
      line(result, 'h3', 'Evidence');
      var list = document.createElement('ul');
      run.evidence.forEach(function (item) { line(list, 'li', item); });
      result.appendChild(list);
    }
    var meaning = run.interpretation || {};
    line(result, 'p', 'Window: ' + (meaning.window || 'unavailable') + ' · Timezone: ' +
      (meaning.timezone || 'unavailable') + ' · Scope: ' + (meaning.scope || 'unavailable') +
      ' · Freshness: ' + (meaning.freshness || 'unavailable'));
    line(result, 'p', 'Delivery simulation: ' + (run.simulation || 'unavailable') +
      (run.simulation_reason ? ' · ' + run.simulation_reason : ''));
    line(result, 'p', (run.cost == null ? 'Cost unavailable' : 'Cost $' + (run.cost || '0')) +
      ' · ' + (run.status || 'Result unavailable'));
    if (run.error) {
      var error = document.createElement('p');
      error.setAttribute('role', 'alert');
      error.textContent = run.error;
      result.appendChild(error);
    }
    line(result, 'p', 'Preview only. No delivery or budget warning was sent.');
    result.hidden = false;
    status(data.message || 'Draft tested.');
  }
  form.addEventListener('input', invalidate);
  form.addEventListener('change', invalidate);
  form.addEventListener('submit', function (event) {
    if (event.submitter !== button) {
      cancelPending('Preview canceled because saving started.', true);
      clearErrors();
      return;
    }
    event.preventDefault();
    if (controller) controller.abort();
    var requestVersion = ++version;
    var active = new AbortController();
    controller = active;
    previewRunning = true;
    interactionAtStart = interaction;
    button.disabled = true;
    result.hidden = true;
    result.setAttribute('aria-busy', 'true');
    clearErrors();
    status('Testing draft…');
    var body = new FormData(form);
    body.set('action', 'preview');
    fetch(form.getAttribute('action') || location.href, {
      method: 'POST', body: body, credentials: 'same-origin', signal: active.signal,
      redirect: 'error', headers: { 'X-Trellum-Form': '1', Accept: 'application/json' }
    }).then(function (response) {
      return response.json().then(function (data) {
        if (requestVersion !== version || controller !== active) return;
        if (!response.ok || data.ok !== true) {
          var errors = data.errors || {};
          showErrors(errors);
          throw new Error(data.message || 'Could not test this draft.');
        }
        if (requestVersion === version && controller === active) show(data);
      });
    }).catch(function (error) {
      if (requestVersion === version && controller === active && error.name !== 'AbortError') {
        status(error instanceof TypeError ? 'Connection interrupted. Your draft is still here; test again when connected.' : error.message);
      }
    }).finally(function () {
      if (controller === active) {
        controller = null;
        previewRunning = false;
        button.disabled = false;
        result.setAttribute('aria-busy', 'false');
        var positionUnchanged = priorPosition && scrollX === priorPosition.x && scrollY === priorPosition.y;
        var focusLost = document.activeElement === document.body || document.activeElement === button;
        if (interaction === interactionAtStart && positionUnchanged && focusLost) {
          var focus = priorFocus && priorFocus.isConnected && priorFocus !== document.body ? priorFocus : button;
          focus.focus({ preventScroll: true });
        }
        priorFocus = null;
        priorPosition = null;
      }
    });
  });
})();
