document.addEventListener('DOMContentLoaded', function(){
  var btn = document.getElementById('fwExportBtn');
  var menu = document.getElementById('fwExportMenu');
  if (!btn || !menu) return;

  var analysisItem = menu.querySelector('[data-export="analysis"]');
  if (analysisItem) {
    var contentKind = document.body.getAttribute('data-content-kind') || 'report';
    var hasTargets = Array.prototype.some.call(
      document.querySelectorAll('.fw-section,.fw-chart-container'), function(el) {
        var style = getComputedStyle(el);
        return el.getClientRects().length > 0 && style.display !== 'none'
          && style.visibility !== 'hidden' && style.visibility !== 'collapse'
          && style.opacity !== '0';
      });
    if (contentKind === 'analysis' || !hasTargets)
      analysisItem.hidden = true;
  }

  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    menu.classList.toggle('open');
  });
  document.addEventListener('click', function() { menu.classList.remove('open'); });
  menu.addEventListener('click', function(e) { e.stopPropagation(); });

  menu.querySelectorAll('[data-export]').forEach(function(b) {
    b.addEventListener('click', function() {
      menu.classList.remove('open');
      var fmt = b.getAttribute('data-export');
      if (fmt === 'png') fw.exportPNG();
      else if (fmt === 'pdf') fw.exportPDF();
      else if (fmt === 'analysis') fw.captureForAnalysis();
    });
  });

  // Help menu: detection runs on open so we catch extensions that inject
  // their markers lazily (after user interacts with the page).
  var hbtn = document.getElementById('fwHelpBtn');
  var hmenu = document.getElementById('fwHelpMenu');
  var hdet = document.getElementById('fwHelpDetected');
  if (hbtn && hmenu) {
    // Selectors for the DOM fingerprints each extension leaves behind.
    // These are public markers, not fingerprinting — we only look to
    // surface a hint if something is clearly present.
    var EXTENSIONS = [
      { name: 'Bitwarden',
        sel: '[data-bw-autofill],.com-bitwarden-browser-animated-fill,div[id^="bit-badge"]' },
      { name: '1Password',
        sel: '[data-com-1password-filled],[data-onepassword-title],com-1password-button' },
      { name: 'LastPass',
        sel: '[data-lastpass-icon-root],[data-lastpass-root]' },
      { name: 'Dashlane',
        sel: '[data-dashlane-rid],[data-dashlanecreated]' },
      { name: 'Keeper',
        sel: '[data-keeper-autofill],keeper-lock-icon' },
    ];
    function _detect() {
      var hits = [];
      for (var i = 0; i < EXTENSIONS.length; i++) {
        try {
          if (document.querySelector(EXTENSIONS[i].sel)) hits.push(EXTENSIONS[i].name);
        } catch (e) {}
      }
      if (!hdet) return;
      if (hits.length) {
        hdet.textContent = 'Detected on this page: ' + hits.join(', ');
        hdet.classList.add('visible');
      } else {
        hdet.classList.remove('visible');
        hdet.textContent = '';
      }
    }
    hbtn.addEventListener('click', function(e) {
      e.stopPropagation();
      var opening = !hmenu.classList.contains('open');
      hmenu.classList.toggle('open');
      if (opening) _detect();
      // Close export menu if it was open
      if (menu) menu.classList.remove('open');
    });
    hmenu.addEventListener('click', function(e) { e.stopPropagation(); });
    document.addEventListener('click', function() { hmenu.classList.remove('open'); });
  }
});
