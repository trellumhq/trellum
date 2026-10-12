(function () {
  'use strict';
  var form = document.querySelector('form[data-alert-editor]');
  if (!form) return;
  var result = document.getElementById('alertPreview');
  var version = 0, controller = null, button = form.querySelector('[data-alert-preview]');
  var priorFocus = null, priorPosition = null;
  var interaction = 0, interactionAtStart = 0, previewRunning = false;

  var timezone = form.querySelector('#id_timezone');
  var timezoneData = form.querySelector('[data-timezone-picker]');
  if (timezone && timezoneData) {
    var zones = timezoneData.dataset.timezones.split(',');
    var choices = document.createElement('div');
    choices.className = 'al-timezone-results'; choices.hidden = true;
    choices.setAttribute('role', 'group'); choices.setAttribute('aria-label', 'Supported timezones');
    timezoneData.hidden = false;
    timezoneData.appendChild(choices);
    timezone.removeAttribute('list');
    var suppressTimezoneFocus = false;
    function timezoneResults() {
      var query = timezone.value.trim().toLocaleLowerCase();
      choices.replaceChildren();
      if (!query) { choices.hidden = true; return; }
      var matches = zones.filter(function (zone) { return zone.toLocaleLowerCase().indexOf(query) >= 0; }).slice(0, 50);
      matches.forEach(function (zone) {
        var option = document.createElement('button'); option.type = 'button';
        option.textContent = zone;
        option.addEventListener('click', function () {
          timezone.value = zone;
          timezone.dispatchEvent(new Event('input', { bubbles: true }));
          timezone.dispatchEvent(new Event('change', { bubbles: true }));
          choices.hidden = true;
        });
        choices.appendChild(option);
      });
      choices.hidden = !matches.length;
    }
    timezone.addEventListener('focus', function () {
      if (suppressTimezoneFocus) { suppressTimezoneFocus = false; return; }
      timezoneResults();
    });
    timezone.addEventListener('input', timezoneResults);
    timezone.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') choices.hidden = true;
      if (event.key === 'ArrowDown' && !choices.hidden) { event.preventDefault(); var first = choices.querySelector('button'); if (first) first.focus(); }
    });
    choices.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        choices.hidden = true;
        suppressTimezoneFocus = true;
        timezone.focus();
      }
    });
    document.addEventListener('click', function (event) {
      if (!timezoneData.contains(event.target) && event.target !== timezone) choices.hidden = true;
    });
    if (form.hasAttribute('data-browser-timezone-default') && !form.dataset.timezoneDefaultApplied) {
      form.dataset.timezoneDefaultApplied = '1';
      try {
        var browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (zones.indexOf(browserZone) >= 0) {
          timezone.value = browserZone;
        }
      } catch (error) { /* Keep the server's UTC fallback for unknown browser zones. */ }
    }
  }

  document.addEventListener('focusin', function (event) {
    if (event.target !== button && event.target !== document.body) {
      if (previewRunning) interaction++;
    }
  });
  ['wheel', 'touchstart'].forEach(function (type) {
    document.addEventListener(type, function () { if (previewRunning) interaction++; }, { passive: true });
  });
  document.addEventListener('pointerdown', function (event) {
    if (previewRunning && event.target !== button) interaction++;
  });
  document.addEventListener('keydown', function (event) {
    if (previewRunning && ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].indexOf(event.key) >= 0) interaction++;
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
    priorFocus = null;
    priorPosition = null;
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
  form.addEventListener('change', function () { if (previewRunning || !priorPosition) invalidate(); });
  form.addEventListener('submit', function (event) {
    if (event.submitter !== button) {
      cancelPending('Preview result discarded because saving started.', true);
      clearErrors();
      return;
    }
    event.preventDefault();
    if (controller) controller.abort();
    if (!priorPosition) {
      priorFocus = document.activeElement;
      priorPosition = { x: scrollX, y: scrollY };
    }
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
        var focusLost = document.activeElement === document.body || document.activeElement === button;
        if (interaction === interactionAtStart) {
          if (focusLost) {
            var focus = priorFocus && priorFocus.isConnected && priorFocus !== document.body ? priorFocus : button;
            focus.focus({ preventScroll: true });
          }
          if (priorPosition) window.scrollTo(priorPosition.x, priorPosition.y);
        }
        priorFocus = null;
        priorPosition = null;
      }
    });
  });
})();
