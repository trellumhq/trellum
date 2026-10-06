/* Embed runtime for /share/<token>/ pages served from an embed link. The
 * portal injects it at serve time (apps.reports.views._share_head_extra)
 * after setting window._fwEmbed, window._fwEmbedOrigins (lowercase
 * origins, or ["*"]) and window._fwEmbedBadge; served byte-for-byte at
 * /api/reports/embed-widget.js.
 *
 * Outbound to the host page:  trellum:ready, trellum:height {height}.
 * Inbound from an allowed origin: trellum:theme {theme, vars}, trellum:reload.
 */
(function () {
    'use strict';
    if (!window._fwEmbed) return;

    var origins = window._fwEmbedOrigins || [];
    function allowed(origin) {
        origin = String(origin || '').toLowerCase();
        return origins.indexOf('*') !== -1 || origins.indexOf(origin) !== -1;
    }

    // ready/height carry no report data, so '*' is fine here; which sites
    // may frame this page at all is enforced server-side by the
    // Content-Security-Policy frame-ancestors header.
    function post(msg) {
        if (parent !== window) parent.postMessage(msg, '*');
    }

    post({ type: 'trellum:ready' });

    if (window.ResizeObserver) {
        new ResizeObserver(function () {
            post({ type: 'trellum:height', height: document.documentElement.scrollHeight });
        }).observe(document.body);
    }

    // Host palette push. Themes declare their variables on <html data-theme=...>,
    // so an inline style on documentElement wins and inherits everywhere.
    // ponytail: chart series colours come from the theme's JS palette, not CSS
    // variables, so vars restyles surfaces/text/accents but not series colours;
    // upgrade = a trellum:theme `palette` list once the runtime reads one.
    var NAME_RE = /^--[a-z0-9-]{1,48}$/, VALUE_RE = /^[\w#%(),.\s'"-]+$/;
    function applyVars(vars) {
        if (!vars || typeof vars !== 'object') return;
        var style = document.documentElement.style;
        Object.keys(vars).slice(0, 40).forEach(function (name) {
            var value = vars[name];
            if (NAME_RE.test(name) && typeof value === 'string' && value.length <= 80
                && VALUE_RE.test(value) && !/url/i.test(value)) {
                style.setProperty(name, value);
            }
        });
    }

    window.addEventListener('message', function (e) {
        if (!allowed(e.origin)) return;
        var d = e.data || {};
        if (d.type === 'trellum:theme') {
            if (d.theme && typeof window.switchTheme === 'function') window.switchTheme(String(d.theme));
            applyVars(d.vars);
        } else if (d.type === 'trellum:reload') {
            location.reload();
        }
    });

    // Reload when a newer build lands: _meta.json is rewritten on every run.
    // ponytail: full reload drops filter state; upgrade = re-fetch data.json in place like runtime/auto_refresh.js
    var lastRun;
    function checkBuild() {
        fetch('_meta.json', { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (m) {
                if (!m || !m.last_run) return;
                if (lastRun && m.last_run !== lastRun) location.reload();
                lastRun = m.last_run;
            })
            .catch(function () {});
    }
    checkBuild();
    setInterval(checkBuild, 60000);

    if (window._fwEmbedBadge) {
        var a = document.createElement('a');
        a.href = 'https://trellum.dev';
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = 'Powered by Trellum';
        a.style.cssText = 'position:fixed;right:8px;bottom:6px;z-index:9000;font:11px/1 system-ui,sans-serif;'
            + 'color:inherit;opacity:.7;text-decoration:none';
        document.body.appendChild(a);
    }
})();
