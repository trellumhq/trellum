(function () {
  'use strict';
  if (window.TrellumChoices) { window.TrellumChoices.init(document); return; }
  var nextId = 0, active = null;

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
    var left = view ? view.offsetLeft : 0, top = view ? view.offsetTop : 0;
    var width = view ? view.width : window.innerWidth, height = view ? view.height : window.innerHeight;
    var below = top + height - rect.bottom - 12, above = rect.top - top - 12;
    var upwards = below < 280 && above > below;
    var available = upwards ? above : below;
    var full = available < 220;
    picker.popup.style.width = Math.min(Math.max(rect.width, 300), width - 16) + 'px';
    picker.popup.style.maxHeight = Math.max(0, Math.min(400, full ? height - 16 : available)) + 'px';
    picker.popup.style.left = Math.max(left + 8, Math.min(rect.left, left + width - picker.popup.offsetWidth - 8)) + 'px';
    picker.popup.style.top = (full ? top + 8 : upwards ? rect.top - picker.popup.offsetHeight - 4 : rect.bottom + 4) + 'px';
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
    var errorMessage = element('p', 'ui-picker-error');
    errorMessage.id = api.popup.id + '-error';
    errorMessage.hidden = true;
    root.appendChild(errorMessage);
    select._trellumChoice = api;
    select.classList.add('ui-picker-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    Array.prototype.forEach.call(select.labels || [], function (labelNode) {
      labelNode.addEventListener('click', function (event) { event.preventDefault(); api.trigger.focus({ preventScroll: true }); });
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
    api.search.addEventListener('input', api.refresh);
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
