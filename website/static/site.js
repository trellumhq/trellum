/* Site behaviour: theme toggle, mobile nav, docs search, version switch.
 * Vanilla JS, no build step — same house rule as the console. */

(function () {
  'use strict';

  // ---- theme -------------------------------------------------------------
  // Three states: explicit light, explicit dark, or unset (follow the OS).
  // The toggle flips relative to what is actually on screen.
  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var root = document.documentElement;
      var explicit = root.getAttribute('data-theme');
      var dark = explicit
        ? explicit === 'dark'
        : window.matchMedia('(prefers-color-scheme: dark)').matches;
      var next = dark ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('trellum-theme', next); } catch (e) { /* private mode */ }
    });
  }

  // ---- mobile nav --------------------------------------------------------
  var navToggle = document.getElementById('nav-toggle');
  var nav = document.getElementById('site-nav');
  var side = document.getElementById('docs-side');
  if (navToggle && nav) {
    navToggle.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      if (side) { side.classList.toggle('open', open); }
      navToggle.setAttribute('aria-expanded', String(open));
    });
  }

  // ---- copy buttons ------------------------------------------------------
  // The agent prompt is meant to be pasted, so it gets a copy button wherever
  // it appears — the landing page and the blog post carry the same markup, so
  // the behaviour lives here rather than being written twice.
  Array.prototype.forEach.call(document.querySelectorAll('.copybtn'), function (btn) {
    btn.addEventListener('click', function () {
      var source = document.getElementById(btn.getAttribute('data-copy'));
      if (!source) { return; }
      var text = source.textContent;
      function done() {
        btn.textContent = 'copied ✓';
        setTimeout(function () { btn.textContent = 'copy'; }, 1600);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, done);
        return;
      }
      // execCommand is deprecated and still the only fallback on http:// and
      // in older browsers, where the clipboard API is simply absent.
      var area = document.createElement('textarea');
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try { document.execCommand('copy'); } catch (e) { /* nothing to do */ }
      document.body.removeChild(area);
      done();
    });
  });

  // ---- docs version switch ----------------------------------------------
  var version = document.getElementById('docs-version');
  if (version) {
    version.addEventListener('change', function () {
      // Same page in the chosen version when it exists there; the docs index
      // otherwise. A 404 is handled by the server, so try the direct path.
      var rest = window.location.pathname.split('/').slice(3).join('/');
      window.location.href = '/docs/' + version.value + '/' + rest;
    });
  }

  // ---- docs search -------------------------------------------------------
  // The whole corpus is a few dozen pages, so the index is fetched once on
  // first keystroke and filtered in memory — no search service, no second
  // build toolchain.
  var search = document.getElementById('docs-search');
  var results = document.getElementById('search-results');
  if (search && results) {
    var pages = null;
    var loading = false;

    function load() {
      if (pages || loading) { return Promise.resolve(); }
      loading = true;
      return fetch(search.dataset.index)
        .then(function (r) { return r.json(); })
        .then(function (data) { pages = data.pages || []; })
        .catch(function () { pages = []; })
        .finally(function () { loading = false; });
    }

    function render(query) {
      var nav = document.getElementById('docs-nav');
      if (!query) {
        results.classList.remove('open');
        results.innerHTML = '';
        if (nav) { nav.style.display = ''; }
        return;
      }
      if (nav) { nav.style.display = 'none'; }
      results.classList.add('open');

      var needle = query.toLowerCase();
      var hits = (pages || []).filter(function (page) {
        return (page.title + ' ' + page.section + ' ' + page.text)
          .toLowerCase().indexOf(needle) !== -1;
      }).slice(0, 12);

      if (!hits.length) {
        results.innerHTML = '<div class="none">No matches</div>';
        return;
      }
      results.innerHTML = hits.map(function (page) {
        return '<a href="' + page.url + '">' + escapeHtml(page.title) +
               '<small>' + escapeHtml(page.section) + '</small></a>';
      }).join('');
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, function (ch) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
      });
    }

    search.addEventListener('input', function () {
      var query = search.value.trim();
      load().then(function () { render(query); });
    });
    search.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') { search.value = ''; render(''); }
    });
  }
})();
