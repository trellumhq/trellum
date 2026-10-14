(function () {
  'use strict';
  if (window.TrellumRecipients) { window.TrellumRecipients.init(document); return; }
  var TYPES = { people: 'People', roles: 'Roles', groups: 'Groups' };

  function setup(field) {
    if (field._trellumRecipients) { field._trellumRecipients.refresh(); return field._trellumRecipients; }
    var helper = window.TrellumPicker;
    var label = helper.labelFor(field, 'Recipients');
    var generic = field.dataset.recipientMode === 'choices';
    var records = Array.prototype.map.call(field.querySelectorAll('input[type="checkbox"]'), function (input) {
      var row = input.closest('label') || (input.labels && input.labels[0]);
      if (!row) return null;
      var group = input.closest('[data-recipient-type]');
      var type = group && group.dataset.recipientType;
      return { input: input, row: row, type: !generic && TYPES[type] ? type : null,
        name: input.dataset.recipientName || row.textContent.trim(), identity: input.dataset.recipientIdentity || '' };
    }).filter(Boolean);
    var compact = field.hasAttribute('data-recipient-compact') && field.querySelector('[data-compact-choices]');
    var packetInput = compact && compact.querySelector('[data-compact-packet]');
    if (compact && !compact.hasAttribute('data-compact-invalid')) {
      var seed = compact.querySelector('script[type="application/json"]');
      var known = new Set(records.map(function (record) { return record.input.value; }));
      JSON.parse(seed.textContent).forEach(function (choice) {
        if (known.has(choice.value)) return;
        known.add(choice.value);
        var row = helper.element('label'), input = document.createElement('input');
        input.type = 'checkbox'; input.name = field.dataset.recipientName; input.value = choice.value;
        input.checked = input.defaultChecked = true; row.appendChild(input);
        records.push({input: input, row: row, type: null, name: choice.label, identity: ''});
      });
    }
    var fallback = Array.prototype.filter.call(field.children, function (node) {
      return node.matches('[data-recipient-type]') || node.querySelector('input[type="checkbox"]');
    });
    var root = helper.element('div', 'recipient-picker');
    ['pickerLabel', 'pickerSearchPlaceholder', 'pickerEmptyState'].forEach(function (key) {
      if (field.dataset[key]) root.dataset[key] = field.dataset[key];
    });
    field.appendChild(root);
    var api = helper.popup(root, label), filter = 'all';
    var source = field.dataset.recipientSource, generation = 0, controller = null, timeout = null, debounce = null;
    var remoteState = '', remoteData = null, visible = new Set();
    var pager = helper.element('div', 'ui-picker-pager');
    Object.assign(pager.style, {display: 'flex', flexWrap: 'wrap', gap: '8px', flex: 'none'});
    if (source) api.popup.appendChild(pager);
    function cancelRemote() { generation++; clearTimeout(timeout); clearTimeout(debounce); if (controller) controller.abort(); controller = null; }
    function loadRemote(page) {
      cancelRemote(); var token = generation; controller = new AbortController();
      remoteState = 'loading'; remoteData = null; visible.clear(); api.refresh();
      var url = new URL(source, location.href);
      if (url.origin !== location.origin) { remoteState = 'error'; api.refresh(); return; }
      url.searchParams.set('q', api.search.value.trim()); url.searchParams.set('page', page || 1);
      if (generic) {
        url.searchParams.delete('selected');
        records.filter(function (r) { return r.input.checked; }).forEach(function (r) { url.searchParams.append('selected', r.input.value); });
      } else {
      url.searchParams.set('selected_people', records.filter(function (r) { return r.type === 'people' && r.input.checked; }).map(function (r) { return r.input.value; }).join(','));
      url.searchParams.set('selected_groups', records.filter(function (r) { return r.type === 'groups' && r.input.checked; }).map(function (r) { return r.input.value; }).join(','));
      }
      timeout = setTimeout(function () { if (token !== generation) return; controller.abort(); generation++; remoteState = 'error'; api.refresh(); }, 15000);
      var options = {signal: controller.signal, credentials: 'same-origin', headers: {Accept: 'application/json'}};
      // Keep large selections in a read-only body below the server request-line limit.
      if (new TextEncoder().encode(url.pathname + url.search).length > 3000) {
        var parameters = {};
        url.searchParams.forEach(function (value, key) { parameters[key] = value; });
        if (generic) parameters.selected = url.searchParams.getAll('selected');
        var owner = field.closest('form'), csrf = owner && owner.querySelector('input[name="csrfmiddlewaretoken"]');
        if (!csrf) csrf = document.querySelector('input[name="csrfmiddlewaretoken"]');
        options.method = 'POST'; options.headers['Content-Type'] = 'application/json';
        if (csrf) options.headers['X-CSRFToken'] = csrf.value;
        options.body = JSON.stringify(parameters); url.search = '';
      }
      fetch(url, options).then(function (response) {
        if (!response.ok) throw new Error('Recipients unavailable'); return response.json();
      }).then(function (data) {
        if (token !== generation || !field.isConnected || api.popup.hidden) return;
        if (generic) {
          if (!Array.isArray(data.results) || data.results.length > 25 || !Array.isArray(data.selected) || !data.pagination) throw new Error('Invalid choices');
          var pageInfo = data.pagination;
          if (!Number.isSafeInteger(pageInfo.page) || pageInfo.page < 1 || !Number.isSafeInteger(pageInfo.per_page) || pageInfo.per_page < 1 || pageInfo.per_page > 25 || !Number.isSafeInteger(pageInfo.total) || pageInfo.total < 0 || typeof pageInfo.has_previous !== 'boolean' || typeof pageInfo.has_next !== 'boolean' || !data.results.concat(data.selected).every(function (row) { return row && typeof row.value === 'string' && row.value && typeof row.label === 'string'; })) throw new Error('Invalid choices');
        } else if (!Array.isArray(data.members) || data.members.length > 25 || !Array.isArray(data.groups) || data.groups.length > 25 || !data.pagination || !data.pagination.members || !data.pagination.groups || !data.selected || !Array.isArray(data.selected.members) || !Array.isArray(data.selected.groups)) throw new Error('Invalid recipients');
        clearTimeout(timeout);
        var wanted = new Set(), byKey = new Map();
        records.forEach(function (record) { byKey.set(record.type + ':' + record.input.value, record); });
        var pairs = generic ? [[null, 'results']] : [['people', 'members'], ['groups', 'groups']];
        pairs.forEach(function (pair) {
          data[pair[1]].forEach(function (row) { visible.add(pair[0] + ':' + (generic ? row.value : row.id)); });
          var rows = data[pair[1]].concat(generic ? data.selected : data.selected[pair[1]]);
          rows.forEach(function (value) {
            var id = generic ? value.value : value.id, name = generic ? value.label : value.name;
            if ((generic ? typeof id !== 'string' || !id : !Number.isSafeInteger(id)) || typeof name !== 'string') throw new Error('Invalid choice');
            var key = pair[0] + ':' + id; wanted.add(key);
            var record = byKey.get(key);
            if (!record) {
              var row = helper.element('label'), input = document.createElement('input'); input.type = 'checkbox';
              input.name = generic ? (field.dataset.recipientName || 'choices') : pair[0] === 'people' ? (field.dataset.recipientPeopleName || 'recipients') : (field.dataset.recipientGroupName || 'recipient_groups');
              input.value = id; row.appendChild(input);
              record = {input: input, row: row, type: pair[0], name: name, identity: generic ? '' : value.email || ''}; records.push(record); byKey.set(key, record); decorate(record);
            } else {
              record.name = name; record.identity = generic ? '' : value.email || '';
              var caption = record.row.querySelector('.recipient-picker-option-caption');
              if (caption) { caption.textContent = record.name; if (record.identity || record.type) caption.appendChild(helper.element('small', 'recipient-picker-identity', record.identity || TYPES[record.type])); }
            }
          });
        });
        records = records.filter(function (r) {
          if (r.type === 'roles' || wanted.has(r.type + ':' + r.input.value)) return true;
          if (r.input.checked || r.input.defaultChecked) {
            if (r.input.checked) {
              r.name = generic ? (field.dataset.unavailableLabel || 'Selected choice is unavailable') : 'Selected ' + (r.type === 'people' ? 'person' : 'group') + ' is unavailable'; r.identity = '';
              r.input.dataset.recipientName = r.name; r.input.dataset.recipientIdentity = '';
              var caption = r.row.querySelector('.recipient-picker-option-caption'); if (caption) caption.textContent = r.name;
            }
            return true;
          }
          r.row.remove(); return false;
        });
        if (window.TrellumSettingsForms && field.closest('form')) TrellumSettingsForms.trackControls(field.closest('form'), records.map(function (record) { return record.input; }));
        remoteState = 'ready'; remoteData = data; api.refresh();
      }).catch(function (error) { if (token !== generation || error.name === 'AbortError') return; clearTimeout(timeout); remoteState = 'error'; api.refresh(); });
    }
    if (source) {
      var open = api.open, close = api.close;
      api.open = function () { open(); if (!api.popup.hidden) loadRemote(1); };
      api.close = function (focus) { cancelRemote(); close(focus); };
    }
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
    var combined = !generic && (records.some(function (record) { return record.type; }) || field.querySelector('[data-recipient-type]'));
    if (combined) {
      [['all', 'All'], ['people', 'People'], ['roles', 'Roles'], ['groups', 'Groups']].forEach(function (entry) {
        var button = helper.button('ui-picker-filter', entry[1]);
        button.dataset.recipientFilter = entry[0];
        button.setAttribute('aria-pressed', String(entry[0] === filter));
        button.addEventListener('click', function () {
          filter = entry[0];
          filters.querySelectorAll('button').forEach(function (node) { node.setAttribute('aria-pressed', String(node === button)); });
          if (source && !api.popup.hidden) loadRemote(1); else api.refresh();
        });
        filters.appendChild(button);
      });
      api.search.after(filters);
    }
    var done = helper.button('ui-picker-done', 'Done');
    done.addEventListener('click', function () { api.close(true); });
    api.popup.appendChild(done);
    function decorate(record) {
      record.row.classList.add('recipient-picker-option');
      if (generic || record.identity || record.type || record.input.dataset.recipientName) {
        // Keep the checkbox node, its form name and label association.
        var caption = helper.element('span', 'recipient-picker-option-caption', record.name);
        var identity = record.identity || (record.type ? TYPES[record.type] : '');
        if (identity) caption.appendChild(helper.element('small', 'recipient-picker-identity', identity));
        record.row.replaceChildren(record.input, caption);
      }
      api.options.appendChild(record.row);
    }
    records.forEach(decorate);
    fallback.forEach(function (node) {
      node.querySelectorAll('.errorlist,[data-settings-error]').forEach(function (error) { field.appendChild(error); });
      node.classList.add('recipient-picker-fallback');
      node.hidden = true;
    });
    field.querySelectorAll('[data-picker-fallback]').forEach(function (node) {
      node.hidden = true; node.querySelectorAll('button[type="submit"]').forEach(function (button) { button.type = 'button'; });
    });
    var form = field.closest('form');
    if (source && form) form.querySelectorAll('[data-report-native-choices]').forEach(function (node) {
      node.hidden = true; node.querySelectorAll('button[type="submit"]').forEach(function (button) { button.type = 'button'; });
    });

    if (compact && form && packetInput) {
      form.addEventListener('formdata', function (event) {
        // Disabled fieldsets and controls follow the browser's successful-control rules.
        if (packetInput.matches(':disabled')) return;
        if (compact.hasAttribute('data-compact-invalid')) return;
        var selectedValues = Array.from(new Set(event.formData.getAll(field.dataset.recipientName)));
        event.formData.delete(field.dataset.recipientName);
        event.formData.set(packetInput.name, JSON.stringify({version: 1, selected: selectedValues, editable: []}));
      });
      if (window.TrellumSettingsForms) TrellumSettingsForms.trackControls(form, records.map(function (record) { return record.input; }));
    }

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
      var existing = Array.prototype.slice.call(selected.children), chips = new Map();
      existing.forEach(function (chip) { chips.set(chip._record, chip); });
      existing.forEach(function (chip) { if (!chip._record.input.checked) chip.remove(); });
      chosen.forEach(function (record) {
        var chip = chips.get(record);
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
            status.textContent = chip._label + ' removed. ' + records.filter(function (entry) { return entry.input.checked; }).length + ' selected.';
          });
          chip.append(caption, remove);
          selected.appendChild(chip);
        }
        chip._label = (record.type && record.type !== 'people' ? TYPES[record.type].slice(0, -1) + ': ' : '') + record.name;
        chip.querySelector('span').textContent = chip._label; chip.querySelector('button').setAttribute('aria-label', 'Remove ' + chip._label);
        chip.querySelector('button').disabled = record.input.matches(':disabled');
      });
      var query = api.search.value.trim().toLocaleLowerCase(), count = 0;
      records.forEach(function (record) {
        var matches = (filter === 'all' || record.type === filter) && (record.name + ' ' + record.identity).toLocaleLowerCase().indexOf(query) >= 0 && (!source || record.type === 'roles' || visible.has(record.type + ':' + record.input.value));
        record.row.hidden = !matches;
        if (matches) count++;
      });
      api.message.textContent = count ? '' : query ? 'No matches.' : filter !== 'all' ? 'No ' + TYPES[filter].toLowerCase() + ' are available.' : (field.dataset.pickerEmptyState || 'No recipients are available.');
      if (source) {
        if (remoteState === 'loading') api.message.textContent = generic ? 'Loading choices…' : 'Loading recipients…';
        if (remoteState === 'error') api.message.textContent = generic ? 'Could not load choices. Your selections are retained.' : 'Could not load recipients. Your selections are retained.';
        pager.replaceChildren();
        if (remoteState === 'error') {
          var retry = helper.button('ui-picker-filter', 'Retry'); retry.addEventListener('click', function () { loadRemote(1); }); pager.appendChild(retry);
        } else if (remoteData) {
          var parts = generic ? [remoteData.pagination] : filter === 'people' ? [remoteData.pagination.members] : filter === 'groups' ? [remoteData.pagination.groups] : [remoteData.pagination.members, remoteData.pagination.groups];
          if (filter !== 'roles') {
            var number = Math.max.apply(null, parts.map(function (p) { return p.page; }));
            var previous = helper.button('ui-picker-filter', 'Previous'), next = helper.button('ui-picker-filter', 'Next');
            previous.disabled = !parts.some(function (p) { return p.has_previous; }); next.disabled = !parts.some(function (p) { return p.has_next; });
            previous.addEventListener('click', function () { api.search.focus({preventScroll: true}); loadRemote(number - 1); });
            next.addEventListener('click', function () { api.search.focus({preventScroll: true}); loadRemote(number + 1); });
            var countLabel = helper.element('span', '', 'Page ' + number + ' · ' + parts.reduce(function (n, p) { return n + p.total; }, 0) + ' choices');
            countLabel.style.flexBasis = '100%'; previous.style.flex = next.style.flex = '1';
            pager.append(countLabel, previous, next);
          }
        }
      }
      api.message.hidden = !!count && (!source || remoteState === 'ready');
      selected.hidden = !chosen.length;
      if (api.trigger.disabled && !api.popup.hidden) api.close(false);
      api.reposition();
    };
    api.search.addEventListener('input', function () { if (!source) { api.refresh(); return; } cancelRemote(); remoteState = 'loading'; api.refresh(); debounce = setTimeout(function () { loadRemote(1); }, 200); });
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
