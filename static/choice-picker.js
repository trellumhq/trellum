(function () {
  'use strict';
  if (window.TrellumChoices) { window.TrellumChoices.init(document); return; }
  var nextId = 0, active = null;
  window.addEventListener('pagehide', function () { if (active) active.close(false); });
  new MutationObserver(function () { if (active && !active.root.isConnected) active.close(false); }).observe(document.documentElement, {childList: true, subtree: true});

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function button(className, text) {
    var node = element('button', 'ui-btn ' + className, text);
    node.type = 'button';
    node.setAttribute('data-settings-ignore', '');
    return node;
  }
  function labelFor(field, fallback) {
    var label = field.labels && field.labels[0];
    if (!label) label = field.querySelector && field.querySelector(':scope > label,:scope > legend');
    return field.dataset.pickerLabel || (label && label.textContent.trim().replace(/:\s*$/, '')) || fallback;
  }
  function position(picker) {
    if (!picker.root.isConnected) { picker.close(false); return; }
    var rect = picker.trigger.getBoundingClientRect(), view = window.visualViewport;
    var popupRect = picker.popup.getBoundingClientRect();
    var scale = picker.popup.offsetWidth ? popupRect.width / picker.popup.offsetWidth : 1;
    if (!scale || !Number.isFinite(scale)) scale = 1;
    var left = view ? view.offsetLeft : 0, top = view ? view.offsetTop : 0;
    var width = view ? view.width : window.innerWidth, height = view ? view.height : window.innerHeight;
    var below = top + height - rect.bottom - 12, above = rect.top - top - 12;
    var upwards = below < 280 && above > below;
    var available = upwards ? above : below;
    var full = available < 220 * scale;
    picker.popup.style.width = (Math.min(Math.max(rect.width, 300 * scale), width - 16) / scale) + 'px';
    picker.popup.style.maxHeight = (Math.max(0, Math.min(400 * scale, full ? height - 16 : available)) / scale) + 'px';
    picker.popup.style.left = (Math.max(left + 8, Math.min(rect.left, left + width - picker.popup.offsetWidth * scale - 8)) / scale) + 'px';
    var desiredTop = full ? top + 8 : upwards ? rect.top - picker.popup.offsetHeight * scale - 4 : rect.bottom + 4;
    picker.popup.style.top = (Math.max(top + 8, Math.min(desiredTop, top + height - picker.popup.offsetHeight * scale - 8)) / scale) + 'px';
  }
  // Manual popovers keep the nonmodal dialog inside its form while escaping drawer clipping.
  function popup(root, label) {
    root.classList.add('ui-picker');
    var trigger = button('ui-picker-trigger', '');
    var caption = element('span', 'ui-picker-caption');
    var arrow = element('span', 'ui-picker-chevron');
    arrow.setAttribute('aria-hidden', 'true');
    trigger.append(caption, arrow);
    var panel = element('div', 'ui-picker-popup');
    panel.id = 'tl-picker-' + (++nextId);
    panel.hidden = true;
    panel.setAttribute('popover', 'manual');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', label + ' choices');
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-controls', panel.id);
    trigger.setAttribute('aria-expanded', 'false');
    var search = element('input', 'ui-picker-search');
    search.type = 'search';
    search.autocomplete = 'off';
    search.placeholder = root.dataset.pickerSearchPlaceholder || 'Search ' + label.toLowerCase();
    search.setAttribute('aria-label', 'Search ' + label);
    search.setAttribute('data-settings-ignore', '');
    ['input', 'change'].forEach(function (type) {
      search.addEventListener(type, function (event) { event.stopPropagation(); });
    });
    var options = element('div', 'ui-picker-options');
    var message = element('p', 'ui-picker-message');
    message.setAttribute('role', 'status');
    message.hidden = true;
    panel.append(search, options, message);
    root.append(trigger, panel);
    var api = { root: root, trigger: trigger, caption: caption, popup: panel,
      search: search, options: options, message: message, refresh: function () {} };
    api.reposition = function () { if (!panel.hidden) position(api); };
    api.close = function (focus) {
      if (panel.hidePopover && panel.matches(':popover-open')) panel.hidePopover();
      panel.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (active === api) active = null;
      if (focus && trigger.isConnected) trigger.focus({ preventScroll: true });
    };
    api.open = function () {
      if (trigger.matches(':disabled')) return;
      if (active && active !== api) active.close(false);
      if (panel.hidden) search.value = '';
      api.refresh();
      panel.hidden = false;
      if (panel.showPopover) panel.showPopover();
      active = api;
      trigger.setAttribute('aria-expanded', 'true');
      position(api);
      search.focus({ preventScroll: true });
    };
    trigger.addEventListener('click', function () { if (panel.hidden) api.open(); else api.close(true); });
    trigger.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); event.stopPropagation(); api.close(true); return; }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); api.open(); }
    });
    panel.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); api.close(true); return; }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].indexOf(event.key) === -1) return;
      if ((event.key === 'Home' || event.key === 'End') && event.target === search) return;
      var nodes = Array.prototype.filter.call(options.querySelectorAll('button,input[type="checkbox"]'), function (node) {
        return !node.matches(':disabled') && !node.closest('[hidden]');
      });
      if (!nodes.length) return;
      var index = nodes.indexOf(document.activeElement);
      if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = nodes.length - 1;
      else if (index < 0) index = event.key === 'ArrowUp' ? nodes.length - 1 : 0;
      else index = Math.max(0, Math.min(nodes.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
      event.preventDefault();
      nodes[index].focus({ preventScroll: true });
      nodes[index].scrollIntoView({ block: 'nearest' });
    });
    return api;
  }
  document.addEventListener('pointerdown', function (event) { if (active && !active.root.contains(event.target)) active.close(false); });
  document.addEventListener('focusin', function (event) { if (active && !active.root.contains(event.target)) active.close(false); });
  window.addEventListener('resize', function () { if (active) position(active); });
  window.addEventListener('scroll', function () { if (active) position(active); }, true);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', function () { if (active) position(active); });
    window.visualViewport.addEventListener('scroll', function () { if (active) position(active); });
  }
  window.TrellumPicker = { element: element, button: button, labelFor: labelFor, popup: popup };

  function setup(select) {
    if (!select) return null;
    if (select._trellumChoice) { select._trellumChoice.refresh(); return select._trellumChoice; }
    if (select.multiple || select.size > 1) return null;
    var root = element('div', 'ui-choice-picker');
    ['pickerLabel', 'pickerPlaceholder', 'pickerSearchPlaceholder', 'pickerEmptyState'].forEach(function (key) {
      if (select.dataset[key]) root.dataset[key] = select.dataset[key];
      else if (select.parentElement.dataset[key]) root.dataset[key] = select.parentElement.dataset[key];
    });
    select.before(root);
    root.appendChild(select);
    var label = root.dataset.pickerLabel || labelFor(select, 'Option');
    var api = popup(root, label), invalid = false;
    var source = select.dataset.choiceSource, prefix = select.dataset.choiceValuePrefix || '';
    var remoteRows = [], remotePage = null, remoteState = '', generation = 0, controller = null, debounce = null, timeout = null;
    var pager = element('div', 'ui-picker-pager');
    Object.assign(pager.style, { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', flex: 'none' });
    if (source) api.popup.appendChild(pager);
    function cancelRemote() {
      generation++; clearTimeout(debounce); clearTimeout(timeout);
      if (controller) controller.abort();
      controller = null;
    }
    function loadRemote(page) {
      cancelRemote();
      var current = generation;
      var restoreSearchFocus = pager.contains(document.activeElement);
      if (restoreSearchFocus) api.search.focus({ preventScroll: true });
      controller = new AbortController();
      remoteState = 'loading'; remoteRows = []; remotePage = null; api.refresh();
      var url = new URL(source, window.location.href);
      if (url.origin !== window.location.origin) { remoteState = 'error'; api.refresh(); return; }
      url.searchParams.set('q', api.search.value.trim());
      url.searchParams.set('page', page || 1);
      var selectedValue = select.value;
      url.searchParams.set('selected', selectedValue);
      timeout = setTimeout(function () {
        if (current !== generation) return;
        controller.abort(); generation++; remoteState = 'error'; remoteRows = []; remotePage = null; api.refresh();
      }, 15000);
      fetch(url, { signal: controller.signal, credentials: 'same-origin', headers: { Accept: 'application/json' } }).then(function (response) {
        if (!response.ok) throw new Error('Choices unavailable');
        return response.json();
      }).then(function (result) {
        if (current !== generation || !root.isConnected || api.popup.hidden) return;
        if (!Array.isArray(result.results) || !result.pagination) throw new Error('Invalid choices');
        clearTimeout(timeout);
        var chosen = select.options[select.selectedIndex];
        if (chosen && chosen.value && select.value === selectedValue && select.value.indexOf('slug:') !== 0) {
          chosen.textContent = result.selected && prefix + String(result.selected.value) === select.value ? result.selected.label + (result.selected.hint ? ' · ' + result.selected.hint : '') : (select.dataset.unavailableLabel || root.dataset.unavailableLabel || 'Selected report is unavailable');
        }
        remoteRows = result.results; remotePage = result.pagination; remoteState = 'ready'; api.refresh();
        if (restoreSearchFocus) api.search.focus({ preventScroll: true });
      }).catch(function (error) {
        if (current !== generation || error.name === 'AbortError') return;
        clearTimeout(timeout); remoteRows = []; remotePage = null; remoteState = 'error'; api.refresh();
      });
    }
    var errorMessage = element('p', 'ui-picker-error');
    errorMessage.id = api.popup.id + '-error';
    errorMessage.hidden = true;
    root.appendChild(errorMessage);
    select._trellumChoice = api;
    select.classList.add('ui-picker-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    Array.prototype.forEach.call(select.labels || [], function (labelNode) {
      labelNode.addEventListener('click', function (event) {
        if (root.contains(event.target)) return;
        event.preventDefault();
        api.trigger.focus({ preventScroll: true });
      });
    });
    api.refresh = function () {
      var selected = select.options[select.selectedIndex];
      api.caption.textContent = selected ? selected.textContent : (root.dataset.pickerPlaceholder || 'Choose ' + label.toLowerCase());
      api.trigger.setAttribute('aria-label', label + ': ' + api.caption.textContent);
      api.trigger.disabled = select.matches(':disabled');
      var error = invalid || select.getAttribute('aria-invalid') === 'true';
      if (error) api.trigger.setAttribute('aria-invalid', 'true'); else api.trigger.removeAttribute('aria-invalid');
      errorMessage.textContent = invalid ? select.validationMessage : '';
      errorMessage.hidden = !invalid;
      var description = (select.getAttribute('aria-describedby') || '') + (invalid ? ' ' + errorMessage.id : '');
      description = description.trim();
      if (description) api.trigger.setAttribute('aria-describedby', description); else api.trigger.removeAttribute('aria-describedby');
      api.options.replaceChildren();
      if (source) {
        var rows = [];
        Array.prototype.forEach.call(select.options, function (option) {
          if (!option.value && !option.hidden) rows.push({value: '', label: option.textContent, disabled: option.disabled});
        });
        rows = rows.concat(remoteRows);
        rows.forEach(function (item) {
          var value = item.value ? prefix + item.value : '';
          var row = button('ui-picker-option', item.label + (item.hint ? ' · ' + item.hint : ''));
          row.disabled = !!item.disabled;
          row.setAttribute('aria-pressed', String(select.value === value));
          row.addEventListener('click', function () {
            var option = Array.prototype.find.call(select.options, function (candidate) { return candidate.value === value; });
            if (!option) { option = new Option(item.label + (item.hint ? ' · ' + item.hint : ''), value); select.add(option); }
            select.value = value; invalid = false;
            select.dispatchEvent(new Event('input', { bubbles: true }));
            select.dispatchEvent(new Event('change', { bubbles: true })); api.close(true);
          });
          api.options.appendChild(row);
        });
        api.message.textContent = remoteState === 'loading' ? 'Loading choices…' : remoteState === 'error' ? 'Could not load choices. Your selection is retained.' : !remoteRows.length ? (api.search.value ? 'No matches.' : 'No choices are available.') : '';
        api.message.hidden = !api.message.textContent;
        pager.replaceChildren();
        if (remoteState === 'error') {
          var retry = button('ui-picker-filter', 'Retry'); retry.addEventListener('click', function (event) { event.preventDefault(); loadRemote(1); }); pager.appendChild(retry);
        } else if (remotePage) {
          var previous = button('ui-picker-filter', 'Previous'), next = button('ui-picker-filter', 'Next');
          previous.disabled = !remotePage.has_previous; next.disabled = !remotePage.has_next;
          previous.addEventListener('click', function (event) { event.preventDefault(); loadRemote(remotePage.page - 1); });
          next.addEventListener('click', function (event) { event.preventDefault(); loadRemote(remotePage.page + 1); });
          pager.append(previous, element('span', '', 'Page ' + remotePage.page + ' · ' + remotePage.total + ' choices'), next);
        }
        if (api.trigger.disabled && !api.popup.hidden) api.close(false);
        if (!api.popup.hidden) position(api);
        return;
      }
      var query = api.search.value.trim().toLocaleLowerCase(), count = 0;
      Array.prototype.forEach.call(select.options, function (option) {
        if (option.hidden || (option.parentElement.tagName === 'OPTGROUP' && option.parentElement.hidden)) return;
        if ((option.textContent + ' ' + option.value).toLocaleLowerCase().indexOf(query) < 0) return;
        var row = button('ui-picker-option', option.textContent);
        row.disabled = option.disabled || (option.parentElement.tagName === 'OPTGROUP' && option.parentElement.disabled);
        row.setAttribute('aria-pressed', String(option.selected));
        row.addEventListener('click', function () {
          select.value = option.value;
          invalid = false;
          select.dispatchEvent(new Event('input', { bubbles: true }));
          select.dispatchEvent(new Event('change', { bubbles: true }));
          api.close(true);
        });
        api.options.appendChild(row);
        count++;
      });
      api.message.textContent = count ? '' : query ? 'No matches.' : (root.dataset.pickerEmptyState || 'No choices are available.');
      api.message.hidden = !api.message.textContent;
      if (api.trigger.disabled && !api.popup.hidden) api.close(false);
      if (!api.popup.hidden) position(api);
    };
    if (source) {
      var localOpen = api.open, localClose = api.close;
      api.open = function () { localOpen(); if (!api.popup.hidden) loadRemote(1); };
      api.close = function (focus) { cancelRemote(); localClose(focus); };
      api.search.addEventListener('input', function () {
        cancelRemote(); remoteState = 'loading'; remoteRows = []; remotePage = null; api.refresh();
        debounce = setTimeout(function () { loadRemote(1); }, 180);
      });
    } else api.search.addEventListener('input', api.refresh);
    select.addEventListener('input', function () { invalid = false; api.refresh(); });
    select.addEventListener('change', function () { invalid = false; api.refresh(); });
    select.addEventListener('invalid', function (event) {
      event.preventDefault(); invalid = true; api.refresh(); api.trigger.focus({ preventScroll: true });
    });
    new MutationObserver(api.refresh).observe(select, { attributes: true, childList: true, subtree: true, characterData: true });
    api.refresh();
    return api;
  }
  function init(root) {
    root = root || document;
    var targets = [];
    if (root.matches && root.matches('[data-choice-picker]')) targets.push(root);
    targets = targets.concat(Array.prototype.slice.call(root.querySelectorAll('[data-choice-picker]')));
    return targets.map(function (target) { return setup(target.matches('select') ? target : target.querySelector('select')); }).filter(Boolean);
  }
  window.TrellumChoices = { init: init };
  document.addEventListener('settings:updated', function () { init(document); });
  document.addEventListener('settings:saved', function () { init(document); });
  document.addEventListener('reset', function () { setTimeout(function () { init(document); }, 0); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(document); });
  else init(document);
})();
