document.addEventListener('DOMContentLoaded', function(){
  var btn = document.getElementById('fwExportBtn');
  var menu = document.getElementById('fwExportMenu');
  if (!btn || !menu) return;

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
(function(){
  function _measureSticky() {
    var root = document.documentElement;
    var hdr = document.querySelector('.fw-header');
    var ab = document.getElementById('fwAnnoBar');
    var hh = hdr ? hdr.offsetHeight : 48;
    var ah = ab ? ab.offsetHeight : 0;
    root.style.setProperty('--fw-header-h', hh + 'px');
    root.style.setProperty('--fw-sticky-offset', (hh + ah) + 'px');
  }
  _measureSticky();
  window.addEventListener('resize', _measureSticky);
  new MutationObserver(_measureSticky).observe(document.body, { childList: true, subtree: true });
})();
(function(){
  function _update() {
    var el = document.getElementById('fwFreshness');
    var d = window._reportData && window._reportData._freshness;
    if (!el || !d || !d.generated_at) return;
    var age = (Date.now() - new Date(d.generated_at).getTime()) / 1000;
    // Compact header: the freshness row is just a dot + a short age label
    // (no "Updated"/"ago" wording -- that lives in the title tooltip
    // instead, so the header line stays one line).
    var shortTxt, fullTxt;
    if (age < 60) {
      shortTxt = 'now'; fullTxt = 'Updated just now';
    } else if (age < 3600) {
      var m = Math.floor(age / 60);
      shortTxt = m + 'm'; fullTxt = 'Updated ' + m + 'm ago';
    } else if (age < 86400) {
      var h = Math.floor(age / 3600);
      shortTxt = h + 'h'; fullTxt = 'Updated ' + h + 'h ago';
    } else {
      var dAge = Math.floor(age / 86400);
      shortTxt = dAge + 'd'; fullTxt = 'Updated ' + dAge + 'd ago';
    }
    el.innerHTML = '<span class="fw-fresh-dot"></span>' + shortTxt;
    el.title = fullTxt;
    el.className = 'fw-freshness visible';
    var rs = d.refresh_seconds || 0;
    if (rs > 0 && age > rs * 4) el.classList.add('very-stale');
    else if (rs > 0 && age > rs * 2) el.classList.add('stale');
    else el.classList.add('fresh');
  }
  var _poll = setInterval(function() {
    if (window._reportData && document.getElementById('fwFreshness')) {
      clearInterval(_poll); _update(); setInterval(_update, 60000);
    }
  }, 200);
})();
