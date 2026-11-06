/* Hover prefetch: the server answers in ~5ms, so warming the next page on
   pointerover makes navigation feel instant. GET-only, same-origin plain
   paths; query-string links (e.g. /?org=) are skipped because they mutate
   session state, as are api/auth endpoints. */
(function () {
  var seen = {};
  document.addEventListener('pointerover', function (ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href');
    if (!href || href[0] !== '/' || seen[href]) return;
    if (href.indexOf('?') !== -1 || href.indexOf('/api/') !== -1 ||
        href.indexOf('/logout') !== -1 || href.indexOf('/r/') !== -1) return;
    seen[href] = 1;
    var link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = href;
    document.head.appendChild(link);
  }, { passive: true });
})();

/* Light/Dark/Auto for the management surface (user menu). Independent of
   the dashboard's fw-theme picker by design. */
function setMgmtTheme(pref) {
  if (window.TrellumConsoleShell && window.TrellumConsoleShell.setTheme) {
    window.TrellumConsoleShell.setTheme(pref);
    return;
  }
  try {
    localStorage.setItem('mgmt-theme', pref);
  } catch (e) {}
  var t = (pref === 'light' || pref === 'dark') ? pref
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', t);
}
/* A row-action menu inside .ui-table-scroll gets clipped: `overflow-x: auto`
   forces the computed `overflow-y` to `auto` as well (CSS will not pair it
   with `visible`), so the wrapper scrolls in both directions and cuts the
   menu off — and grows a scrollbar the moment one opens. Lift the open menu
   out of the scroller with position:fixed. */
(function () {
  var open = null;

  function place(details) {
    var menu = details.querySelector('.shell-menu');
    var summary = details.querySelector('summary');
    if (!menu || !summary) return;
    var gutter = 12;
    var r = summary.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.left = '0';
    menu.style.right = 'auto';
    menu.style.maxHeight = '';
    menu.style.overflowY = '';

    var box = menu.getBoundingClientRect();
    var left = Math.min(
      Math.max(gutter, r.right - box.width),
      window.innerWidth - box.width - gutter
    );
    menu.style.left = Math.max(gutter, left) + 'px';

    var below = window.innerHeight - r.bottom - gutter - 4;
    var above = r.top - gutter - 4;
    var opensAbove = box.height > below && above > below;
    var available = Math.max(0, opensAbove ? above : below);
    menu.style.maxHeight = available + 'px';
    if (box.height > available) menu.style.overflowY = 'auto';
    menu.style.top = opensAbove
      ? Math.max(gutter, r.top - Math.min(box.height, available) - 4) + 'px'
      : (r.bottom + 4) + 'px';
  }

  function reset(details) {
    var menu = details.querySelector('.shell-menu');
    if (!menu) return;
    menu.style.position = '';
    menu.style.top = '';
    menu.style.left = '';
    menu.style.right = '';
    menu.style.maxHeight = '';
    menu.style.overflowY = '';
  }

  document.addEventListener('toggle', function (ev) {
    var d = ev.target;
    if (!d.matches || !d.matches('details.shell-dd')) return;
    if (!d.closest('.ui-table-scroll')) return;
    if (d.open) { open = d; place(d); } else { reset(d); if (open === d) open = null; }
  }, true);

  /* A fixed menu does not travel with its row, so close rather than detach. */
  window.addEventListener('scroll', function (ev) {
    if (open && !open.contains(ev.target)) { open.open = false; }
  }, true);
  window.addEventListener('resize', function () {
    if (open) { open.open = false; }
  });
})();

/* Studio tab bar: on narrow screens the rail scrolls horizontally, so bring
   the active tab into view on load (a no-op when everything already fits). */
(function () {
  var rail = document.querySelector('.studio-tabs-rail');
  if (!rail || rail.scrollWidth <= rail.clientWidth) return;
  var active = rail.querySelector('.active');
  if (active && active.scrollIntoView) {
    active.scrollIntoView({ block: 'nearest', inline: 'center' });
  }
})();

/* Copy buttons rendered by _copy_field.html: copy the sibling value and show
   the inline "copied ✓" badge for a moment. One delegated handler for all. */
document.addEventListener('click', function (ev) {
  var btn = ev.target && ev.target.closest ? ev.target.closest('[data-ui-copy]') : null;
  if (!btn) return;
  var box = btn.closest('.ui-copy');
  var value = box.querySelector('.ui-copy-value');
  if (!value) return;
  var text = value.textContent.trim();
  var done = function () {
    box.classList.add('copied');
    setTimeout(function () { box.classList.remove('copied'); }, 2000);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () {});
  } else {
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) { /* nothing to do */ }
    document.body.removeChild(ta);
  }
});
