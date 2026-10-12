(function () {
  'use strict';
  if (window.TrellumRecipients) { window.TrellumRecipients.init(document); return; }
  var TYPES = { people: 'People', roles: 'Roles', groups: 'Groups' };

  function setup(field) {
    if (field._trellumRecipients) { field._trellumRecipients.refresh(); return field._trellumRecipients; }
    var helper = window.TrellumPicker;
    var label = helper.labelFor(field, 'Recipients');
    var records = Array.prototype.map.call(field.querySelectorAll('input[type="checkbox"]'), function (input) {
      var row = input.closest('label') || (input.labels && input.labels[0]);
      if (!row) return null;
      var group = input.closest('[data-recipient-type]');
      var type = group && group.dataset.recipientType;
      return { input: input, row: row, type: TYPES[type] ? type : null,
        name: input.dataset.recipientName || row.textContent.trim(), identity: input.dataset.recipientIdentity || '' };
    }).filter(Boolean);
    var fallback = Array.prototype.filter.call(field.children, function (node) {
      return node.matches('[data-recipient-type]') || node.querySelector('input[type="checkbox"]');
    });
    var root = helper.element('div', 'recipient-picker');
    ['pickerLabel', 'pickerSearchPlaceholder', 'pickerEmptyState'].forEach(function (key) {
      if (field.dataset[key]) root.dataset[key] = field.dataset[key];
    });
    field.appendChild(root);
    var api = helper.popup(root, label), filter = 'all';
    field._trellumRecipients = api;
    var selected = helper.element('div', 'recipient-picker-selected');
    var status = helper.element('span', 'recipient-picker-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    root.insertBefore(selected, api.popup);
    root.appendChild(status);
    var filters = helper.element('div', 'recipient-picker-filters');
    filters.setAttribute('role', 'group');
    filters.setAttribute('aria-label', 'Recipient type');
    var combined = records.some(function (record) { return record.type; }) || field.querySelector('[data-recipient-type]');
    if (combined) {
      [['all', 'All'], ['people', 'People'], ['roles', 'Roles'], ['groups', 'Groups']].forEach(function (entry) {
        var button = helper.button('ui-picker-filter', entry[1]);
        button.dataset.recipientFilter = entry[0];
        button.setAttribute('aria-pressed', String(entry[0] === filter));
        button.addEventListener('click', function () {
          filter = entry[0];
          filters.querySelectorAll('button').forEach(function (node) { node.setAttribute('aria-pressed', String(node === button)); });
          api.refresh();
        });
        filters.appendChild(button);
      });
      api.search.after(filters);
    }
    var done = helper.button('ui-picker-done', 'Done');
    done.addEventListener('click', function () { api.close(true); });
    api.popup.appendChild(done);
    records.forEach(function (record) {
      record.row.classList.add('recipient-picker-option');
      if (record.identity || record.type || record.input.dataset.recipientName) {
        // Keep the checkbox node, its form name and label association.
        var caption = helper.element('span', 'recipient-picker-option-caption', record.name);
        var identity = record.identity || (record.type ? TYPES[record.type] : '');
        if (identity) caption.appendChild(helper.element('small', 'recipient-picker-identity', identity));
        record.row.replaceChildren(record.input, caption);
      }
      api.options.appendChild(record.row);
    });
    fallback.forEach(function (node) {
      node.querySelectorAll('.errorlist,[data-settings-error]').forEach(function (error) { field.appendChild(error); });
      node.classList.add('recipient-picker-fallback');
      node.hidden = true;
    });
    field.querySelectorAll('[data-picker-fallback]').forEach(function (node) { node.hidden = true; });

    function changed(record) {
      record.input.dispatchEvent(new Event('input', { bubbles: true }));
      record.input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    api.refresh = function () {
      var chosen = records.filter(function (record) { return record.input.checked; });
      api.caption.textContent = chosen.length ? chosen.length + ' selected' : 'Choose ' + label.toLowerCase();
      api.trigger.setAttribute('aria-label', label + ': ' + api.caption.textContent);
      api.trigger.disabled = records.length > 0 && records.every(function (record) { return record.input.matches(':disabled'); });
      var errors = records.filter(function (record) { return record.input.getAttribute('aria-invalid') === 'true'; });
      if (errors.length) api.trigger.setAttribute('aria-invalid', 'true'); else api.trigger.removeAttribute('aria-invalid');
      var described = [];
      errors.forEach(function (record) { (record.input.getAttribute('aria-describedby') || '').split(/\s+/).forEach(function (id) {
        if (id && described.indexOf(id) < 0) described.push(id);
      }); });
      if (described.length) api.trigger.setAttribute('aria-describedby', described.join(' ')); else api.trigger.removeAttribute('aria-describedby');
      // Keep existing chips mounted so refreshes preserve removal focus.
      var existing = Array.prototype.slice.call(selected.children);
      existing.forEach(function (chip) { if (!chip._record.input.checked) chip.remove(); });
      chosen.forEach(function (record) {
        var chip = existing.find(function (node) { return node._record === record; });
        if (!chip) {
          chip = helper.element('span', 'recipient-picker-chip');
          chip._record = record;
          var text = (record.type && record.type !== 'people' ? TYPES[record.type].slice(0, -1) + ': ' : '') + record.name;
          var caption = helper.element('span', '', text);
          var remove = helper.button('', '×');
          remove.setAttribute('aria-label', 'Remove ' + text);
          remove.addEventListener('click', function () {
            var buttons = Array.prototype.slice.call(selected.querySelectorAll('button'));
            var index = buttons.indexOf(remove);
            record.input.checked = false;
            changed(record);
            var next = selected.querySelectorAll('button');
            (next[Math.min(index, next.length - 1)] || api.trigger).focus({ preventScroll: true });
            status.textContent = text + ' removed. ' + records.filter(function (entry) { return entry.input.checked; }).length + ' selected.';
          });
          chip.append(caption, remove);
          selected.appendChild(chip);
        }
        chip.querySelector('button').disabled = record.input.matches(':disabled');
      });
      var query = api.search.value.trim().toLocaleLowerCase(), count = 0;
      records.forEach(function (record) {
        var matches = (filter === 'all' || record.type === filter) && (record.name + ' ' + record.identity).toLocaleLowerCase().indexOf(query) >= 0;
        record.row.hidden = !matches;
        if (matches) count++;
      });
      api.message.textContent = count ? '' : query ? 'No matches.' : filter !== 'all' ? 'No ' + TYPES[filter].toLowerCase() + ' are available.' : (field.dataset.pickerEmptyState || 'No recipients are available.');
      api.message.hidden = !!count;
      selected.hidden = !chosen.length;
      if (api.trigger.disabled && !api.popup.hidden) api.close(false);
      api.reposition();
    };
    api.search.addEventListener('input', api.refresh);
    field.addEventListener('input', function (event) { if (event.target.matches('input[type="checkbox"]')) api.refresh(); });
    field.addEventListener('change', function (event) {
      if (event.target.matches('input[type="checkbox"]')) {
        api.refresh(); status.textContent = records.filter(function (record) { return record.input.checked; }).length + ' selected.';
      }
    });
    records.forEach(function (record) {
      record.input.addEventListener('invalid', function (event) {
        event.preventDefault(); filter = 'all'; api.search.value = '';
        filters.querySelectorAll('button').forEach(function (node) { node.setAttribute('aria-pressed', String(node.dataset.recipientFilter === 'all')); });
        api.open(); record.input.focus({ preventScroll: true });
      });
    });
    new MutationObserver(function (changes) {
      if (changes.some(function (change) { return change.target === field || records.some(function (record) { return record.input === change.target; }); })) api.refresh();
    }).observe(field, { attributes: true, subtree: true,
      attributeFilter: ['checked', 'disabled', 'aria-invalid', 'aria-describedby'] });
    api.refresh();
    return api;
  }
  function init(root) {
    if (!window.TrellumPicker) return [];
    root = root || document;
    var fields = [];
    if (root.matches && root.matches('[data-recipient-picker]')) fields.push(root);
    return fields.concat(Array.prototype.slice.call(root.querySelectorAll('[data-recipient-picker]'))).map(setup);
  }
  window.TrellumRecipients = { init: init };
  document.addEventListener('settings:updated', function () { init(document); });
  document.addEventListener('settings:saved', function () { init(document); });
  document.addEventListener('reset', function () { setTimeout(function () { init(document); }, 0); });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(document); });
  else init(document);
})();
