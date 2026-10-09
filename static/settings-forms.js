(function () {
  'use strict';
  var nextId = 0, submitHandlers = new WeakMap();
  var refreshGeneration = 0, refreshController = null, pendingRefresh = new Set();
  function refreshRegions(keys) {
    var regions = Array.prototype.slice.call(document.querySelectorAll('[data-settings-refresh]'));
    keys.forEach(function (key) {
      if (regions.some(function (region) { return region.dataset.settingsRefresh === key; })) pendingRefresh.add(key);
    });
    if (!pendingRefresh.size) return Promise.resolve([]);
    if (refreshController) refreshController.abort();
    var generation = ++refreshGeneration;
    refreshController = new AbortController();
    var controller = refreshController, timedOut = false;
    var timeout = setTimeout(function () { timedOut = true; controller.abort(); }, 15000);
    return fetch(location.href, { credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'text/html' }, signal: controller.signal, redirect: 'error'
    }).then(function (response) {
      if (!response.ok || !(response.headers.get('Content-Type') || '').includes('text/html')) throw new Error('Summary refresh failed.');
      return response.text();
    }).then(function (html) {
      if (generation !== refreshGeneration) return;
      var parsed = new DOMParser().parseFromString(html, 'text/html');
      var position = { x: scrollX, y: scrollY }, missing = false;
      document.querySelectorAll('[data-settings-refresh]').forEach(function (current) {
        var key = current.dataset.settingsRefresh;
        if (!pendingRefresh.has(key)) return;
        if (current.contains(document.activeElement) || current.matches('form.is-dirty,form[aria-busy="true"]') || current.querySelector('form.is-dirty,form[aria-busy="true"]')) return;
        var replacement = Array.prototype.find.call(parsed.querySelectorAll('[data-settings-refresh]'), function (node) {
          return node.dataset.settingsRefresh === key;
        });
        if (!replacement) { missing = true; return; }
        replacement.querySelectorAll('script').forEach(function (node) { node.remove(); });
        current.replaceWith(replacement);
        initialize(replacement);
        pendingRefresh.delete(key);
        document.dispatchEvent(new CustomEvent('settings:updated', { detail: { root: replacement, key: key } }));
      });
      window.scrollTo(position.x, position.y);
      if (missing) throw new Error('The refreshed summary was missing.');
      return Array.from(pendingRefresh);
    }).catch(function (error) {
      if (timedOut) throw new Error('Summary refresh timed out.');
      throw error;
    }).finally(function () { clearTimeout(timeout); });
  }
  function controls(form, includeDisabled) {
    return Array.prototype.filter.call(form.elements, function (field) {
      return (includeDisabled || !field.matches(':disabled')) &&
        !field.hasAttribute('data-settings-ignore') &&
        !/^(button|submit|reset|hidden)$/i.test(field.type);
    });
  }
  function value(field) {
    if (/^(checkbox|radio)$/i.test(field.type)) return field.checked;
    if (field.tagName === 'SELECT') {
      return Array.prototype.map.call(field.options, function (option) { return option.selected; }).join(',');
    }
    return field.value;
  }
  function snapshot(form) {
    return controls(form, true).map(function (field) { return { field: field, value: value(field) }; });
  }
  function init(form) {
    if (form.dataset.settingsInitialized) return;
    form.dataset.settingsInitialized = '1';
    if (!form.getAttribute('id')) form.setAttribute('id', 'settings-form-' + (++nextId));
    var status = form.querySelector('[data-settings-dirty-status]') || document.createElement('span');
    status.classList.add('settings-form-status');
    status.setAttribute('data-settings-dirty-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    if (!status.parentNode) {
      var submit = form.querySelector('[type="submit"]');
      if (submit) submit.insertAdjacentElement('afterend', status);
      else form.appendChild(status);
    }
    var baseline = snapshot(form), saving = false, submitted = null;
    function update(clearState) {
      var dirty = baseline.some(function (entry) {
        return !entry.field.matches(':disabled') && value(entry.field) !== entry.value;
      });
      form.classList.toggle('is-dirty', dirty);
      if (saving) return;
      if (clearState || !/^(saved|error)$/.test(form.dataset.settingsState || '')) {
        form.dataset.settingsState = dirty ? 'dirty' : 'clean';
        status.textContent = dirty ? 'Unsaved changes' : '';
      }
    }
    function clearErrors() {
      form.querySelectorAll('[data-settings-error],.errorlist').forEach(function (node) { node.remove(); });
      controls(form, true).forEach(function (field) {
        if (field.dataset.settingsErrorId) {
          var ids = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(function (id) {
            return id && id !== field.dataset.settingsErrorId;
          });
          if (ids.length) field.setAttribute('aria-describedby', ids.join(' '));
          else field.removeAttribute('aria-describedby');
          field.removeAttribute('aria-invalid');
          delete field.dataset.settingsErrorId;
        }
      });
    }
    function showErrors(errors) {
      Object.keys(errors || {}).forEach(function (name, index) {
        var fields = controls(form, true).filter(function (field) { return field.name === name; });
        var node = document.createElement('span');
        node.className = 'settings-form-error';
        node.setAttribute('data-settings-error', '');
        node.id = form.getAttribute('id') + '-error-' + index;
        node.textContent = [].concat(errors[name]).join(' ');
        if (fields.length) fields[fields.length - 1].insertAdjacentElement('afterend', node);
        else status.insertAdjacentElement('afterend', node);
        fields.forEach(function (field) {
          field.setAttribute('aria-invalid', 'true');
          field.dataset.settingsErrorId = node.id;
          field.setAttribute('aria-describedby', ((field.getAttribute('aria-describedby') || '') + ' ' + node.id).trim());
        });
      });
    }
    form.addEventListener('input', function () { update(true); });
    form.addEventListener('change', function () { update(true); });
    form.addEventListener('reset', function () { setTimeout(function () { update(true); }, 0); });
    form.addEventListener('settings:saved', function () {
      baseline = submitted || snapshot(form);
      update(true);
    });
    update(true);
    // Delegation runs after form confirmation and existing custom submit listeners.
    submitHandlers.set(form, function (event) {
      if (!form.hasAttribute('data-settings-save') || event.defaultPrevented) return;
      var action = new URL(form.getAttribute('action') || location.href, location.href);
      if (action.origin !== location.origin) return;
      event.preventDefault();
      if (saving) return;
      // A later write makes any prior summary read obsolete.
      ++refreshGeneration;
      if (refreshController) refreshController.abort();
      var body = new FormData(form);
      if (event.submitter && event.submitter.name) body.append(event.submitter.name, event.submitter.value);
      submitted = snapshot(form);
      var focused = document.activeElement;
      var failureMessage = null;
      var buttons = Array.prototype.filter.call(form.elements, function (field) { return field.type === 'submit'; }).map(function (field) {
        var entry = { field: field, disabled: field.disabled }; field.disabled = true; return entry;
      });
      saving = true;
      clearErrors();
      form.dataset.settingsState = 'saving';
      form.setAttribute('aria-busy', 'true');
      status.textContent = 'Saving…';
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 30000);
      fetch(action.href, {
        method: 'POST', body: body, credentials: 'same-origin', signal: controller.signal,
        redirect: 'error', headers: { 'X-Trellum-Form': '1', 'Accept': 'application/json' }
      }).then(function (response) {
        return response.json().then(function (data) {
          if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('The server returned an invalid response. Check the saved settings before retrying.');
          if (!response.ok || data.ok !== true) {
            showErrors(data.errors);
            throw new Error(data.ok === true ? 'The server rejected the save. Please try again.' : data.message || data.error || 'Could not save. Please try again.');
          }
          // Apply normalized values only to the submitted draft, never to a newer edit.
          Object.keys(data.values || {}).forEach(function (name) {
            Array.prototype.forEach.call(form.elements, function (field) {
              if (field.name === name && field.type === 'hidden') field.value = data.values[name] === null ? '' : data.values[name];
            });
            submitted.forEach(function (entry) {
              if (entry.field.name !== name) return;
              var field = entry.field, normalized = data.values[name];
              var unchanged = value(field) === entry.value;
              var normalizedValue = normalized === null ? '' : String(normalized);
              if (/^(checkbox|radio)$/i.test(field.type)) {
                normalizedValue = field.type === 'radio' ? field.value === String(normalized) : !!normalized;
                if (unchanged) field.checked = normalizedValue;
              } else if (field.tagName === 'SELECT') {
                if (unchanged) field.value = normalizedValue;
                normalizedValue = Array.prototype.map.call(field.options, function (option) { return option.value === String(normalized); }).join(',');
              } else if (unchanged) field.value = normalizedValue;
              entry.value = normalizedValue;
            });
          });
          // Only plain text targets with explicit keys are updated; no HTML is executed.
          document.querySelectorAll('[data-settings-text]').forEach(function (node) {
            var key = node.dataset.settingsText;
            if (Object.prototype.hasOwnProperty.call(data.updates || {}, key)) node.textContent = data.updates[key];
          });
          saving = false;
          form.dispatchEvent(new Event('settings:saved', { bubbles: true }));
          form.dataset.settingsState = 'saved';
          status.textContent = (data.message || 'Saved.') + (form.classList.contains('is-dirty') ? ' Newer changes are still unsaved.' : '');
          form.dispatchEvent(new CustomEvent('settings:success', { bubbles: true, detail: data }));
          if (data.refresh && data.refresh.length) {
            refreshRegions(data.refresh).then(function (pending) {
              if (pending && pending.length) status.textContent += ' Summary update waits until editing is finished.';
            }).catch(function (error) {
              if (error.name !== 'AbortError') status.textContent += ' Saved, but the summary could not refresh. Reload to update it.';
            });
          }
          if (data.redirect) {
            var destination = new URL(data.redirect, location.href);
            if (destination.origin === location.origin) location.assign(destination.href);
          }
        }, function () { throw new Error('The server returned an invalid response. Check the saved settings before retrying.'); });
      }).catch(function (error) {
        form.dataset.settingsState = 'error';
        status.textContent = error instanceof TypeError || error.name === 'AbortError'
          ? 'Connection interrupted. The save outcome is unknown; check before retrying.' : error.message;
        failureMessage = status.textContent;
      }).finally(function () {
        clearTimeout(timeout);
        saving = false;
        submitted = null;
        form.removeAttribute('aria-busy');
        buttons.forEach(function (entry) { entry.field.disabled = entry.disabled; });
        if (document.activeElement === document.body && focused && focused.isConnected) focused.focus({ preventScroll: true });
        update(false);
        if (failureMessage) form.dispatchEvent(new CustomEvent('settings:error', { bubbles: true, detail: { message: failureMessage } }));
      });
    });
  }
  function initialize(root) {
    if (root.matches && root.matches('form[data-settings-form],form[data-settings-save]')) init(root);
    root.querySelectorAll('form[data-settings-form],form[data-settings-save]').forEach(init);
  }
  window.TrellumSettingsForms = { init: initialize, refreshRegions: refreshRegions };
  function retryRefresh() {
    if (!pendingRefresh.size) return;
    var generation = refreshGeneration;
    setTimeout(function () {
      if (generation === refreshGeneration && pendingRefresh.size) refreshRegions(Array.from(pendingRefresh)).catch(function () {});
    }, 0);
  }
  document.addEventListener('settings:saved', retryRefresh);
  document.addEventListener('reset', retryRefresh);
  document.addEventListener('settings:updated', function () {
    if (!pendingRefresh.size) document.querySelectorAll('[data-settings-dirty-status]').forEach(function (status) {
      status.textContent = status.textContent.replace(' Summary update waits until editing is finished.', '');
    });
  });
  document.addEventListener('focusout', function (event) {
    if (pendingRefresh.size && event.target.closest('[data-settings-refresh]')) retryRefresh();
  });
  document.addEventListener('submit', function (event) {
    var handler = submitHandlers.get(event.target);
    if (handler) handler(event);
  });
  initialize(document);
})();
