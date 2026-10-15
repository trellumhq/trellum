(function () {
  'use strict';
  if (window.TrellumAutoFilter) return;
  // Forms marked data-auto-filter apply as soon as a control changes: selects,
  // pickers and toggles at once, text boxes once typing pauses. They need no
  // Apply, Go or Search button.
  var DELAY = 400, KEY = 'trellum:auto-filter-focus', timers = new WeakMap();
  var TEXT = 'input[type="search"],input[type="text"],input[type="email"],input[type="url"],input[type="number"],input:not([type])';

  function formOf(node) {
    var form = node && node.closest && node.closest('form[data-auto-filter]');
    if (!form || node.hasAttribute('data-settings-ignore')) return null;
    return form;
  }
  function submit(form, field) {
    clearTimeout(timers.get(form));
    if (field && field.name) {
      try { sessionStorage.setItem(KEY, JSON.stringify({path: location.pathname, name: field.name, at: field.selectionEnd})); } catch (error) { /* storage is optional */ }
    }
    if (form.requestSubmit) form.requestSubmit(); else form.submit();
  }
  document.addEventListener('input', function (event) {
    var field = event.target, form = formOf(field);
    if (!form || !field.matches(TEXT)) return;
    clearTimeout(timers.get(form));
    timers.set(form, setTimeout(function () { if (form.isConnected) submit(form, field); }, DELAY));
  });
  document.addEventListener('change', function (event) {
    var field = event.target, form = formOf(field);
    if (!form) return;
    // A text box commits through its pause or Enter; everything else commits now.
    if (field.matches(TEXT)) { if (timers.has(form)) submit(form, null); return; }
    submit(form, null);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' || event.isComposing) return;
    var field = event.target, form = formOf(field);
    if (!form || !field.matches(TEXT)) return;
    event.preventDefault();
    submit(form, field);
  });
  function restoreFocus() {
    var saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(KEY) || 'null'); sessionStorage.removeItem(KEY); } catch (error) { saved = null; }
    if (!saved || saved.path !== location.pathname) return;
    var field = Array.prototype.find.call(document.querySelectorAll('form[data-auto-filter] [name]'), function (node) { return node.name === saved.name && node.matches(TEXT); });
    if (!field) return;
    field.focus({preventScroll: true});
    var end = typeof saved.at === 'number' ? saved.at : field.value.length;
    try { field.setSelectionRange(end, end); } catch (error) { /* not all input types select text */ }
  }
  window.TrellumAutoFilter = {restoreFocus: restoreFocus};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', restoreFocus); else restoreFocus();
})();
