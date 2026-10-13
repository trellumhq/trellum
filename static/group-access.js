/* Native browsing submits the full draft and is separate from settings writes. */
(function () {
  'use strict';
  document.addEventListener('submit', function (event) {
    var submitter = event.submitter;
    if (!submitter || submitter.value !== 'browse_access' || submitter.name !== 'action') return;
    var form = event.target;
    if (!form.hasAttribute('data-studio-access')) return;
    form.removeAttribute('data-settings-save');
    setTimeout(function () { form.setAttribute('data-settings-save', ''); }, 0);
  }, true);
})();
