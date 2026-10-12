(function () {
  'use strict';

  function setup(field) {
    var list = Array.prototype.find.call(field.children, function (child) {
      return child.querySelector('input[type="checkbox"]');
    });
    var labels = list ? Array.prototype.slice.call(list.querySelectorAll('label')) : [];
    var title = field.querySelector(':scope > label');
    var name = title ? title.textContent.trim().replace(/:\s*$/, '') : 'recipients';
    var picker = document.createElement('div');
    picker.className = 'recipient-picker';
    var search = document.createElement('input');
    search.type = 'search';
    search.className = 'ui-input recipient-picker-search';
    search.placeholder = 'Add ' + name.toLocaleLowerCase() + '…';
    search.setAttribute('aria-label', 'Search ' + name);
    search.setAttribute('aria-expanded', 'false');
    search.setAttribute('data-settings-ignore', '');
    var selected = document.createElement('div');
    selected.className = 'recipient-picker-selected';
    selected.setAttribute('aria-live', 'polite');
    var results = document.createElement('div');
    results.className = 'recipient-picker-results';
    results.setAttribute('role', 'group');
    results.setAttribute('aria-label', name + ' choices');
    var empty = document.createElement('p');
    empty.className = 'recipient-picker-empty';
    empty.hidden = true;
    picker.append(search, selected, results, empty);
    field.insertBefore(picker, list || null);
    if (list) labels.forEach(function (label) { results.appendChild(label); });
    var fallback = field.querySelector('[data-picker-fallback]');
    if (!labels.length && fallback) fallback.hidden = true;
    var pointerInside = false;
    picker.addEventListener('pointerdown', function () {
      pointerInside = true;
      window.setTimeout(function () { pointerInside = false; }, 250);
    });
    document.addEventListener('pointerup', function () {
      window.setTimeout(function () { pointerInside = false; }, 100);
    }, true);

    function open() {
      if (labels.length) results.hidden = false;
      search.setAttribute('aria-expanded', String(labels.length > 0 && !results.hidden));
    }
    function close() {
      results.hidden = true;
      search.setAttribute('aria-expanded', 'false');
    }
    function render() {
      var query = search.value.trim().toLocaleLowerCase();
      var shown = 0;
      selected.replaceChildren();
      labels.forEach(function (label) {
        var checkbox = label.querySelector('input[type="checkbox"]');
        var text = label.textContent.trim();
        if (checkbox.checked) {
          var chip = document.createElement('span');
          chip.className = 'ui-chip recipient-picker-chip';
          var caption = document.createElement('span');
          caption.textContent = text;
          var remove = document.createElement('button');
          remove.type = 'button';
          remove.textContent = '×';
          remove.setAttribute('aria-label', 'Remove ' + text);
          remove.addEventListener('click', function () {
            checkbox.checked = false;
            checkbox.dispatchEvent(new Event('change', { bubbles: true }));
            search.focus({ preventScroll: true });
          });
          chip.append(caption, remove);
          selected.appendChild(chip);
        }
        var matches = !query || text.toLocaleLowerCase().indexOf(query) >= 0;
        label.hidden = !matches || shown >= 50;
        if (matches && shown < 50) shown++;
      });
      if (!labels.length) {
        empty.textContent = field.dataset.pickerEmptyState || ('No ' + name.toLocaleLowerCase() + ' are available.');
        empty.hidden = false;
      } else {
        empty.textContent = shown ? '' : 'No matches.';
        if (shown === 50) empty.textContent = 'Showing the first 50 matches. Refine your search.';
        empty.hidden = shown !== 0 && shown !== 50;
        if (shown === 50) empty.hidden = false;
      }
    }

    search.addEventListener('focus', function () { open(); });
    search.addEventListener('input', function () { open(); render(); });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      if (event.key === 'ArrowDown' && results.hidden) open();
      if (event.key === 'ArrowDown' && !results.hidden) {
        var first = results.querySelector('label:not([hidden]) input[type="checkbox"]');
        if (first) { event.preventDefault(); first.focus(); }
      }
    });
    field.addEventListener('change', function (event) {
      if (event.target.matches('input[type="checkbox"]')) render();
    });
    field.addEventListener('focusout', function () {
      window.setTimeout(function () {
        if (!pointerInside && !field.contains(document.activeElement)) close();
      }, 0);
    });
    results.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { search.focus({ preventScroll: true }); close(); }
    });
    document.addEventListener('pointerdown', function (event) {
      if (!picker.contains(event.target)) close();
    });
    if (list) list.hidden = true;
    results.hidden = true;
    render();
  }

  document.querySelectorAll('[data-recipient-picker]').forEach(setup);
})();
