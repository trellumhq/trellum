(function () {
  'use strict';

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
      return Array.prototype.map.call(field.options, function (option) {
        return option.selected;
      }).join(',');
    }
    return field.value;
  }

  document.querySelectorAll('form[data-settings-form]').forEach(function (form) {
    var status = document.createElement('span');
    status.className = 'settings-form-status';
    status.setAttribute('data-settings-dirty-status', '');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    var submit = form.querySelector('[type="submit"]');
    if (submit) submit.insertAdjacentElement('afterend', status);
    var baseline = controls(form, true).map(function (field) {
      return { field: field, value: value(field) };
    });

    function update() {
      var dirty = baseline.some(function (entry) {
        return !entry.field.matches(':disabled') && value(entry.field) !== entry.value;
      });
      form.classList.toggle('is-dirty', dirty);
      status.textContent = dirty ? 'Unsaved changes' : '';
    }

    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.addEventListener('reset', function () { setTimeout(update, 0); });
    form.addEventListener('settings:saved', function () {
      baseline = controls(form, true).map(function (field) {
        return { field: field, value: value(field) };
      });
      update();
    });
    update();
  });
})();
