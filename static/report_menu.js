/* Report page "Options" menu: consolidates the framework's own Export
 * control (.fw-export-wrap, rendered unconditionally by every build --
 * trellum/components/header.py::ReportHeader) and this workstream's three
 * injected header buttons (Email delivery, Share, Activity) into ONE
 * dropdown, rather than up to four separate buttons crowding the header.
 *
 * Host/registry split: this file is the HOST -- it owns the button, the
 * dropdown, and a tiny registry (window.__reportMenu.register). It does not
 * know what an "Email delivery" item does; static/report_delivery.js,
 * static/report_share.js and static/report_views.js each register their
 * own item instead of mounting their own header button (see each file's
 * own autoMount()). This file also detects and forwards the framework's
 * native export actions itself (detectExport() below) -- nothing else
 * knows those exist.
 *
 * Load order matters: this script must run BEFORE the three widget scripts
 * (see apps.runner.executor.portal_extensions()["scripts"]) so
 * window.__reportMenu.register already exists by the time any of them try
 * to call it. All four are `defer` scripts (trellum/rendering/
 * html_builder.py), which execute in document order, so listing this one
 * first is sufficient -- no readiness race to guard against here.
 *
 * If nothing ever registers (e.g. an anonymous share view with export
 * disabled), the button never mounts at all -- see ensureButton(), only
 * ever called from register().
 *
 * Same frozen-URL delivery as the other three (apps.reports.views
 * .menu_widget_js) and the same FOUC-safety pattern established for them:
 * the stylesheet is injected synchronously, before this file does anything
 * that could create the button, and the button also carries the same look
 * inline as a second, redundant guarantee.
 */
(function () {
    'use strict';

    var OPTIONS_SVG = '<svg viewBox="0 0 20 20" fill="currentColor"><circle cx="4" cy="10" r="1.7"/><circle cx="10" cy="10" r="1.7"/><circle cx="16" cy="10" r="1.7"/></svg>';

    var CSS = ''
        + '.rmw-header-btn{position:relative;display:inline-flex;align-items:center;gap:4px;color:rgba(255,255,255,0.7);'
        + 'font-size:13px;font-weight:500;padding:5px 12px;border-radius:6px;background:rgba(0,0,0,0.2);'
        + 'border:1px solid rgba(255,255,255,0.15);cursor:pointer;transition:all .2s;white-space:nowrap}'
        + '.rmw-header-btn:hover{background:rgba(0,0,0,0.35);color:#fff}'
        + '.rmw-header-btn[aria-expanded="true"]{background:rgba(255,255,255,0.28);color:#fff}'
        + '.rmw-header-icon{display:inline-flex}'
        + '.rmw-header-icon svg{width:14px;height:14px}'
        + '@media(max-width:640px){.rmw-header-btn span:not(.rmw-header-icon){display:none}.rmw-header-btn{padding:6px 8px}}'
        // The dropdown: right-aligned under the button (position:relative on
        // #fwOptionsWrap, not the button itself, so the dropdown's own
        // absolute positioning is unaffected by the button's :hover
        // transform-free styling).
        + '#fwOptionsWrap{position:relative;display:inline-flex}'
        + '.rmw-dropdown{position:absolute;top:calc(100% + 6px);right:0;min-width:200px;'
        + 'background:var(--bg-card,var(--bg-main,#fff));border:1px solid var(--border,var(--border-color,#ddd));'
        + 'border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.16);z-index:9150;padding:6px;'
        // Closed state: invisible AND out of the a11y/interaction tree
        // (visibility:hidden), not display:none -- so the opacity/transform
        // transition below has something to animate FROM/TO. See
        // openMenu()'s force-reflow + double-rAF for why this alone isn't
        // enough on the very first open.
        + 'opacity:0;visibility:hidden;transform:translateY(-6px);'
        + 'transition:opacity .12s ease-out,transform .12s ease-out,visibility .12s}'
        + '.rmw-dropdown.open{opacity:1;visibility:visible;transform:translateY(0)}'
        + '.rmw-item{display:flex;align-items:center;gap:9px;width:100%;text-align:left;padding:8px 10px;'
        + 'border-radius:6px;border:none;background:none;cursor:pointer;font-size:13px;font-family:inherit;'
        + 'color:var(--text,var(--text-main,#1a1a2e));white-space:nowrap}'
        + '.rmw-item-icon{display:inline-flex;flex:none;color:var(--text2,var(--text-muted,#8a8a99))}'
        + '.rmw-item-icon svg{width:16px;height:16px;display:block}'
        + '.rmw-item:hover .rmw-item-icon,.rmw-item:focus-visible .rmw-item-icon{color:inherit}'
        + '.rmw-item:hover,.rmw-item:focus-visible{background:var(--bg-hover,rgba(0,0,0,0.06))}'
        + '.rmw-item:focus-visible{outline:2px solid var(--focus,var(--accent,#4e79a7));outline-offset:-2px}'
        // prefers-reduced-motion: the state still changes (open/closed),
        // just instantly -- only the transition itself is removed.
        + '@media(prefers-reduced-motion:reduce){.rmw-dropdown{transition:none}}';

    // Belt-and-suspenders against the button ever painting as a bare native
    // <button> -- see static/report_delivery.js's identical constant for
    // the FOUC bug this guards against and why the fix is "inject the
    // stylesheet before any element is created, AND duplicate the base
    // look inline".
    var HEADER_BTN_INLINE_STYLE = 'position:relative;display:inline-flex;align-items:center;gap:4px;'
        + 'color:rgba(255,255,255,0.7);font-size:13px;font-weight:500;padding:5px 12px;'
        + 'border-radius:6px;background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.15);'
        + 'cursor:pointer;transition:all .2s;white-space:nowrap;box-sizing:border-box';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── style injection (synchronous, eager -- see the call at the bottom of
    // this file) ─────────────────────────────────────────────────────────
    var styleInjected = false;
    function injectStyle() {
        if (styleInjected) return;
        styleInjected = true;
        var style = document.createElement('style');
        style.id = 'rmwStyle';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // ── registry ─────────────────────────────────────────────────────────
    // Items are plain {id, label, order, onSelect} objects. register() is
    // both "add a new item" and "update an existing one" -- a later call
    // with the same id merges onto the existing entry rather than
    // replacing it outright, so e.g. report_delivery.js can register once
    // with a full item (label + onSelect) and later refresh just the label
    // (subscription count changed) without resupplying onSelect.
    var ITEMS = [];

    function findIndex(id) {
        for (var i = 0; i < ITEMS.length; i++) {
            if (ITEMS[i].id === id) return i;
        }
        return -1;
    }

    function merge(existing, item) {
        var out = {};
        var k;
        if (existing) {
            for (k in existing) { if (existing.hasOwnProperty(k)) out[k] = existing[k]; }
        }
        for (k in item) { if (item.hasOwnProperty(k)) out[k] = item[k]; }
        return out;
    }

    // ── mount (lazy: only once something is actually registered) ───────────
    var mounted = false;
    var menuOpenState = false;

    function ensureButton() {
        if (mounted) return;
        var right = document.querySelector('.fw-header-right');
        if (!right) return;  // not yet in the DOM -- a later register() can retry
        mounted = true;
        injectStyle();

        var wrap = document.createElement('div');
        wrap.id = 'fwOptionsWrap';

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'fwOptionsBtn';
        btn.className = 'rmw-header-btn';
        btn.style.cssText = HEADER_BTN_INLINE_STYLE;  // never paint as a bare native button -- see the constant's own comment
        btn.title = 'Report options';
        btn.setAttribute('aria-haspopup', 'true');
        btn.setAttribute('aria-expanded', 'false');
        btn.innerHTML = '<span class="rmw-header-icon">' + OPTIONS_SVG + '</span><span>Options</span>';
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleMenu();
        });

        var dropdown = document.createElement('div');
        dropdown.id = 'fwOptionsDropdown';
        dropdown.className = 'rmw-dropdown';
        dropdown.setAttribute('role', 'menu');
        dropdown.setAttribute('aria-label', 'Report options');
        dropdown.addEventListener('click', function (e) { e.stopPropagation(); });

        wrap.appendChild(btn);
        wrap.appendChild(dropdown);
        right.insertBefore(wrap, right.firstChild);

        document.addEventListener('click', function () { closeMenu(); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeMenu();
        });

        renderItems();
    }

    function renderItems() {
        var dropdown = document.getElementById('fwOptionsDropdown');
        if (!dropdown) return;
        var html = '';
        for (var i = 0; i < ITEMS.length; i++) {
            // ITEMS[i].icon is trusted SVG supplied by our own widget code
            // (never user input), so it is inlined, not escaped.
            html += '<button type="button" class="rmw-item" role="menuitem" data-item-id="'
                + esc(ITEMS[i].id) + '">'
                + '<span class="rmw-item-icon">' + (ITEMS[i].icon || '') + '</span>'
                + '<span class="rmw-item-label">' + esc(ITEMS[i].label) + '</span></button>';
        }
        dropdown.innerHTML = html;
        var buttons = dropdown.querySelectorAll('.rmw-item');
        for (var j = 0; j < buttons.length; j++) {
            buttons[j].addEventListener('click', function (e) {
                var id = e.currentTarget.getAttribute('data-item-id');
                var idx = findIndex(id);
                closeMenu();
                if (idx >= 0 && typeof ITEMS[idx].onSelect === 'function') ITEMS[idx].onSelect();
            });
        }
    }

    // ── open / close (animated -- ~120ms fade + translateY) ────────────────
    function openMenu() {
        if (menuOpenState) return;
        menuOpenState = true;
        var dropdown = document.getElementById('fwOptionsDropdown');
        var btn = document.getElementById('fwOptionsBtn');
        if (!dropdown) return;
        // Force a reflow so the browser has actually painted the CLOSED
        // state at least once, then defer adding .open by two animation
        // frames. Skipping this is exactly the "first open doesn't
        // animate" bug: without it, the element's closed style and its
        // open style can land in the same paint, leaving nothing for the
        // transition to interpolate between.
        void dropdown.offsetHeight;
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                dropdown.classList.add('open');
            });
        });
        if (btn) btn.setAttribute('aria-expanded', 'true');
    }

    function closeMenu() {
        if (!menuOpenState) return;
        menuOpenState = false;
        var dropdown = document.getElementById('fwOptionsDropdown');
        if (dropdown) dropdown.classList.remove('open');
        var btn = document.getElementById('fwOptionsBtn');
        if (btn) {
            btn.setAttribute('aria-expanded', 'false');
            btn.focus();
        }
    }

    function toggleMenu() {
        if (menuOpenState) closeMenu(); else openMenu();
    }

    window.__reportMenu = {
        register: function (item) {
            if (!item || !item.id) return;
            var idx = findIndex(item.id);
            var merged = merge(idx >= 0 ? ITEMS[idx] : null, item);
            if (idx >= 0) ITEMS[idx] = merged; else ITEMS.push(merged);
            ITEMS.sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
            ensureButton();
            renderItems();
        }
    };

    (function registerAccess() {
        var match = window.location.pathname.match(/^\/s\/([a-z0-9-]+)\/([a-z0-9-]+)\/r\/([A-Za-z0-9_-]+)\//);
        if (!match) match = window.location.pathname.match(/^\/content\/([a-z0-9-]+)\/([a-z0-9-]+)\/([A-Za-z0-9_-]+)\/builds\//);
        if (!match) return;
        var accessUrl = '/s/' + match[1] + '/' + match[2] + '/r/' + match[3] + '/access';
        fetch(accessUrl, {credentials: 'same-origin', headers: {Accept: 'application/json'}})
            .then(function (response) { return response.ok && !response.redirected ? response.json() : null; })
            .then(function (data) {
                if (!data || !data.can_manage) return;
                window.__reportMenu.register({
                    id: 'access', order: 45, label: 'Access',
                    icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2.5l6 2.5v4.5c0 3.5-2.5 6.5-6 8-3.5-1.5-6-4.5-6-8V5z"/><path d="M7 10l2 2 4-4"/></svg>',
                    onSelect: function () {
                        var destination = window;
                        try {
                            if (window.parent !== window && window.parent.TrellumConsoleReportHost) destination = window.parent;
                        } catch (error) {}
                        destination.location.assign(accessUrl);
                    }
                });
            }).catch(function () {});
    })();

    // ── native Export forwarding ─────────────────────────────────────────
    // The framework always renders .fw-export-wrap (trellum/components/
    // header.py::ReportHeader.render_html), containing #fwExportBtn/
    // #fwExportMenu with two buttons: [data-export="png"] and
    // [data-export="pdf"], each already wired to fw.exportPNG()/
    // fw.exportPDF() (trellum/components/header.py::client_js). Rather
    // than reimplement or call into those internals, the registered items
    // below just re-click the native buttons -- whatever the framework's
    // export implementation does stays exactly as it was, we only move the
    // affordance into our own menu.
    //
    // getComputedStyle, not just "does the wrap exist": a no-export share
    // serve hides it with server-injected CSS (apps.reports.views
    // ._SHARE_HIDE_EXPORT_CSS) rather than omitting it, so "exists but
    // display:none" must read as "no export here", not "export here".
    (function detectExport() {
        var wrap = document.querySelector('.fw-export-wrap');
        if (!wrap) return;
        if (getComputedStyle(wrap).display === 'none') return;

        var exportMenu = document.getElementById('fwExportMenu');
        // We own Export now -- the framework's own button/menu would
        // otherwise sit in the header unstyled-into-consistency alongside
        // ours (or double up on the same action). Hidden, not removed: its
        // click handlers (and the buttons register()'s onSelect below
        // forwards to) stay intact.
        wrap.style.display = 'none';

        window.__reportMenu.register({
            id: 'export-pdf', order: 10, label: 'Export PDF',
            icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5H5.5A1.5 1.5 0 004 4v12a1.5 1.5 0 001.5 1.5h9A1.5 1.5 0 0016 16V7z"/><path d="M11.5 2.5V7H16"/></svg>',
            onSelect: function () {
                var b = exportMenu && exportMenu.querySelector('[data-export="pdf"]');
                if (b) b.click();
            }
        });
        window.__reportMenu.register({
            id: 'export-png', order: 20, label: 'Export PNG',
            icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3.5" width="14" height="13" rx="2"/><circle cx="7.5" cy="8" r="1.3"/><path d="M4 14l3.5-3.5 3 3 2.5-2.5L17 14"/></svg>',
            onSelect: function () {
                var b = exportMenu && exportMenu.querySelector('[data-export="png"]');
                if (b) b.click();
            }
        });
    })();

    // Eager and synchronous: guarantees the stylesheet is already in <head>
    // before ensureButton() ever runs, whether that happens synchronously
    // above (detectExport found a visible wrap) or later, off one of the
    // other widgets' own async permission-probe callbacks.
    injectStyle();
})();

/* Authenticated report display modes.  The query string is the complete
 * state: each change reloads the current report URL, preserving every other
 * parameter and the fragment, so separate tabs remain independent.
 *
 * This lives in the frozen menu bundle so existing proxy-served reports are
 * upgraded at serve time.  It only activates on authenticated report/content
 * paths and for the three supported values; shares, standalone files, missing
 * parameters and unknown values keep their existing behaviour. */
(function () {
    'use strict';

    var url = new URL(window.location.href);
    var parentContext = null;
    try {
        if (window.parent !== window && window.parent.TrellumConsoleReportHost
                && typeof window.parent.TrellumConsoleReportHost.contextFor === 'function') {
            parentContext = window.parent.TrellumConsoleReportHost.contextFor(window);
        }
    } catch (error) {}
    var mode = url.searchParams.get('display');
    if (!mode && parentContext) mode = 'focus';
    if (mode !== 'console' && mode !== 'focus' && mode !== 'monitor') return;

    function reportContext(pathname) {
        var match = pathname.match(/^\/s\/([^/]+)\/([^/]+)\/r\/([^/]+)(?:\/|$)/);
        if (match) return { org: match[1], studio: match[2], report: match[3] };
        match = pathname.match(/^\/content\/([^/]+)\/([^/]+)\/([^/]+)\/builds\/[^/]+(?:\/|$)/);
        return match ? { org: match[1], studio: match[2], report: match[3] } : null;
    }

    var context = reportContext(url.pathname);
    if (!context) return;
    if (parentContext && parentContext.report !== context.report) parentContext = null;
    var hostToken = (parentContext && parentContext.token)
        || url.searchParams.get('_console_host_token') || '';
    var hosted = mode === 'focus' && !!hostToken && window.parent !== window
        && (url.searchParams.get('_console_host') === '1' || !!parentContext);

    function postHost(type, detail) {
        if (!hosted) return;
        var message = detail || {};
        message.type = type;
        message.report = context.report;
        message.hostToken = hostToken;
        window.parent.postMessage(message, window.location.origin);
    }

    function postHostState() {
        var current = new URL(window.location.href);
        current.searchParams.delete('_console_host');
        current.searchParams.delete('_console_host_token');
        current.searchParams.set('display', 'console');
        postHost('trellum:report-state', { search: current.search, hash: current.hash });
    }

    var CSS = ''
        + 'body.tl-report-console .fw-container,body.tl-report-focus .fw-container,'
        + 'body.tl-report-monitor .fw-container{max-width:none}'
        + 'body.tl-report-console-pending,body.tl-report-console-mounted{padding-top:56px}'
        + 'body.tl-report-console-pending .fw-header,body.tl-report-console-mounted .fw-header{top:56px}'
        + 'body.tl-report-console-pending .fw-anno-bar,body.tl-report-console-mounted .fw-anno-bar{top:calc(var(--fw-header-h,48px) + 56px)}'
        + 'body.tl-report-console .fw-container>.fw-filter-bar,'
        + 'body.tl-report-focus .fw-container>.fw-filter-bar{margin-left:0;margin-right:0;'
        + 'padding-left:20px;padding-right:20px;width:auto;max-width:100%}'
        + '@media(max-width:640px){body.tl-report-console .fw-container>.fw-filter-bar,'
        + 'body.tl-report-focus .fw-container>.fw-filter-bar{padding-left:14px;padding-right:14px}}'
        + 'body.tl-report-console-pending .fw-container>.fw-filter-bar,'
        + 'body.tl-report-console-mounted .fw-container>.fw-filter-bar{'
        + 'top:calc(var(--fw-sticky-offset,48px) + 56px)}'
        + 'body.tl-report-console-mounted .tl-console-header{z-index:9500}'
        + 'body.tl-report-console-mounted .tl-console-backdrop{z-index:9501}'
        + 'body.tl-report-console-mounted .tl-console-sidebar{z-index:9502}'
        + '@media(min-width:1024px){body.tl-report-console-pending,body.tl-report-console-mounted{padding-left:240px}'
        + 'body.tl-report-console-pending.tl-console-collapsed,'
        + 'body.tl-report-console-mounted.tl-console-collapsed{padding-left:64px}}'
        + '@media(max-width:767px){body.tl-report-console-mounted.tl-console-title-known .fw-header-left{display:none}'
        + 'body.tl-report-console-hosted .fw-header-left{display:none}'
        + 'body.tl-report-console-hosted .fw-header{min-height:44px;justify-content:flex-end;flex-wrap:wrap}'
        + 'body.tl-report-console-hosted .fw-header-right{width:100%;justify-content:flex-end;flex-wrap:wrap}'
        + 'body.tl-report-console-hosted .fw-header-right button,'
        + 'body.tl-report-console-hosted .fw-header-right select{min-height:44px}'
        + 'body.tl-report-console-hosted .fw-header-right select{font-size:16px}'
        + 'body.tl-report-console-mounted .fw-header{min-height:44px;justify-content:flex-end;flex-wrap:wrap}'
        + 'body.tl-report-console-mounted .fw-header-right{width:100%;justify-content:flex-end;flex-wrap:wrap}'
        + 'body.tl-report-console-mounted .fw-header-right button,'
        + 'body.tl-report-console-mounted .fw-header-right select{min-height:44px}'
        + 'body.tl-report-console-mounted .fw-header-right select{font-size:16px}}'
        + 'body.tl-report-monitor .fw-header,body.tl-report-monitor .fw-anno-bar,'
        + 'body.tl-report-monitor .fw-filter-bar,body.tl-report-monitor #assistantLauncher,'
        + 'body.tl-report-monitor #assistantAsk,body.tl-report-monitor #assistantPill,'
        + 'body.tl-report-monitor #assistantPanel,body.tl-report-monitor .assistant-setup,'
        + 'body.tl-report-monitor .assistant-section-ask{display:none!important}'
        + '.tl-report-monitor-exit{position:fixed;top:8px;left:50%;z-index:10000;'
        + 'transform:translateX(-50%);padding:7px 12px;border:1px solid rgba(127,127,127,.45);'
        + 'border-radius:999px;background:var(--bg-card,#fff);color:var(--text-main,var(--text,#111));cursor:pointer;'
        + 'font:600 12px/1.2 var(--font-family,Inter,sans-serif);opacity:.18;transition:opacity .15s}'
        + '.tl-report-monitor-exit.is-visible,.tl-report-monitor-exit:hover,'
        + '.tl-report-monitor-exit:focus-visible{opacity:1;outline:2px solid currentColor;outline-offset:2px}'
        + '@media(hover:none){.tl-report-monitor-exit{opacity:.4}}'
        + '@media(prefers-reduced-motion:reduce){.tl-report-monitor-exit{transition:none}}'
        + '@media print{.tl-console-shell,.tl-report-monitor-exit{display:none!important}'
        + 'body.tl-report-console-pending,body.tl-report-console-mounted{padding:0!important}'
        + 'body.tl-report-console-mounted .fw-header{top:0}'
        + 'body.tl-report-console-mounted .fw-anno-bar{top:var(--fw-header-h,48px)}'
        + 'body.tl-report-console-mounted .fw-container>.fw-filter-bar{'
        + 'top:var(--fw-sticky-offset,48px)}}';

    var style = document.createElement('style');
    style.id = 'tlReportDisplayStyle';
    style.textContent = CSS;
    document.head.appendChild(style);
    document.body.classList.add('tl-report-' + mode);
    if (hosted) document.body.classList.add('tl-report-console-hosted');

    /* The report URL runtime owns filter state but exposes this hook for
     * host state. Registering display here keeps console/focus/monitor in the
     * query string whenever filters, tabs or scopes rewrite the URL. */
    function preserveDisplayMode() {
        if (!window._fwUrlSync || typeof window._fwUrlSync.registerCustom !== 'function') return false;
        window._fwUrlSync.registerCustom('display', function () { return mode; });
        if (hosted) {
            window._fwUrlSync.registerCustom('_console_host', function () { return '1'; });
            window._fwUrlSync.registerCustom('_console_host_token', function () { return hostToken; });
        }
        return true;
    }
    if (!preserveDisplayMode()) {
        document.addEventListener('DOMContentLoaded', preserveDisplayMode, { once: true });
        window.addEventListener('load', preserveDisplayMode, { once: true });
    }

    function urlFor(nextMode) {
        var next = new URL(window.location.href);
        next.searchParams.set('display', nextMode);
        return next.pathname + next.search + next.hash;
    }

    function go(nextMode) {
        if (hosted) {
            if (nextMode === 'console') postHost('trellum:report-close');
            else postHost('trellum:report-display', { display: nextMode });
            return;
        }
        window.location.assign(urlFor(nextMode));
    }

    function wireDisplayControls(root) {
        var controls = root.querySelectorAll('[data-report-display]');
        for (var i = 0; i < controls.length; i++) {
            controls[i].addEventListener('click', function (event) {
                go(event.currentTarget.getAttribute('data-report-display'));
            });
        }
    }

    function loadStyle(href) {
        return new Promise(function (resolve) {
            var existing = document.querySelector('link[data-tl-console-style]');
            if (existing) { resolve(true); return; }
            var link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = href;
            link.setAttribute('data-tl-console-style', '');
            link.onload = function () { resolve(true); };
            link.onerror = function () { resolve(false); };
            document.head.appendChild(link);
        });
    }

    function loadScript(src) {
        return new Promise(function (resolve) {
            if (window.TrellumConsoleShell) { resolve(true); return; }
            var script = document.createElement('script');
            script.src = src;
            script.defer = true;
            script.onload = function () { resolve(true); };
            script.onerror = function () { resolve(false); };
            document.head.appendChild(script);
        });
    }

    function mountConsole() {
        document.body.classList.add('tl-report-console-pending');
        try {
            if (window.matchMedia('(min-width:1024px)').matches
                    && localStorage.getItem('tl-console-collapsed') === '1') {
                document.body.classList.add('tl-console-collapsed');
            }
        } catch (error) {}

        function releaseReservation() {
            document.body.classList.remove('tl-report-console-pending');
            if (!document.body.classList.contains('tl-report-console-mounted')) {
                document.body.classList.remove('tl-console-collapsed');
            }
        }

        var shellUrl = '/s/' + context.org + '/' + context.studio
            + '/api/report-shell?report=' + context.report;
        fetch(shellUrl, { credentials: 'same-origin', headers: { Accept: 'text/html' } })
            .then(function (response) {
                if (!response.ok || response.redirected) throw new Error('report shell unavailable');
                return response.text();
            })
            .then(function (html) {
                var parsed = document.createElement('template');
                parsed.innerHTML = html;
                var shell = parsed.content.querySelector('[data-console-shell]');
                if (!shell) throw new Error('report shell response missing root');
                var cssUrl = shell.getAttribute('data-console-css');
                var scriptUrl = shell.getAttribute('data-console-script');
                if (!cssUrl || !scriptUrl) throw new Error('report shell response missing assets');
                return Promise.all([loadStyle(cssUrl), loadScript(scriptUrl)])
                    .then(function (loaded) {
                        if (!loaded[0] || !loaded[1] || !window.TrellumConsoleShell
                                || typeof window.TrellumConsoleShell.init !== 'function') {
                            throw new Error('report shell assets unavailable');
                        }
                        if (typeof window.TrellumConsoleShell.prepare === 'function') {
                            window.TrellumConsoleShell.prepare(shell);
                        }
                        document.body.appendChild(shell);
                        var pageTitle = shell.querySelector('[data-console-page-title]');
                        if (pageTitle && pageTitle.textContent.trim()) {
                            document.body.classList.add('tl-console-title-known');
                            if (typeof window.TrellumConsoleShell.setPageTitle === 'function') {
                                window.TrellumConsoleShell.setPageTitle(pageTitle.textContent.trim());
                            }
                        }
                        document.body.classList.add('tl-report-console-mounted');
                        document.body.classList.remove('tl-report-console-pending');
                        var reportContent = document.querySelector('.fw-container');
                        if (reportContent) reportContent.setAttribute('data-console-inert', '');
                        wireDisplayControls(shell);
                        try {
                            window.TrellumConsoleShell.init(shell);
                        } catch (error) {
                            shell.remove();
                            document.body.classList.remove(
                                'tl-report-console-mounted', 'tl-console-collapsed',
                                'tl-console-drawer-open'
                            );
                            if (reportContent) {
                                reportContent.removeAttribute('data-console-inert');
                                reportContent.removeAttribute('inert');
                            }
                            throw error;
                        }
                    });
            })
            .catch(function () {
                // Display controls in the report header still work, and no
                // partial shell is left behind to obstruct the report.
                releaseReservation();
            });
    }

    function registerDisplayItems() {
        if (!window.__reportMenu) return;
        if (mode === 'console' || hosted) {
            window.__reportMenu.register({
                id: 'display-focus', order: 80, label: 'Expand',
                icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 3H3v4.5M12.5 3H17v4.5M7.5 17H3v-4.5M12.5 17H17v-4.5"/></svg>',
                onSelect: function () { go('focus'); }
            });
        } else if (mode === 'focus') {
            window.__reportMenu.register({
                id: 'display-console', order: 80, label: 'Return to console',
                onSelect: function () { go('console'); }
            });
        }
        if (mode !== 'monitor') {
            window.__reportMenu.register({
                id: 'display-monitor', order: 90, label: 'Monitor',
                icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="3.5" width="15" height="11" rx="2"/><path d="M7 17h6M10 14.5V17"/></svg>',
                onSelect: function () { go('monitor'); }
            });
        }
    }

    if (mode === 'monitor') {
        var exit = document.createElement('button');
        exit.type = 'button';
        exit.className = 'tl-report-monitor-exit';
        exit.textContent = 'Return to console';
        exit.setAttribute('aria-label', 'Exit monitor view and return to console');
        exit.title = 'Return to console (Escape)';
        exit.setAttribute('data-html2canvas-ignore', 'true');
        exit.addEventListener('click', function () { go('console'); });
        document.body.appendChild(exit);
        var revealTimer;
        function revealExit() {
            exit.classList.add('is-visible');
            window.clearTimeout(revealTimer);
            revealTimer = window.setTimeout(function () {
                if (document.activeElement !== exit) exit.classList.remove('is-visible');
            }, 1800);
        }
        document.addEventListener('pointermove', revealExit, { passive: true });
        document.addEventListener('touchstart', revealExit, { passive: true });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') { event.preventDefault(); go('console'); }
            else if (event.key === 'Tab') revealExit();
        });
    } else {
        registerDisplayItems();
        if (mode === 'console') mountConsole();
    }

    if (hosted) {
        var replaceState = window.history.replaceState;
        window.history.replaceState = function () {
            replaceState.apply(window.history, arguments);
            postHostState();
        };
        window.addEventListener('popstate', postHostState);
        window.addEventListener('hashchange', postHostState);
        window.addEventListener('pageshow', function () {
            try {
                var restored = window.parent.TrellumConsoleReportHost.contextFor(window);
                if (!restored || restored.report !== context.report) return;
                parentContext = restored;
                hostToken = restored.token;
                preserveDisplayMode();
                postHost('trellum:report-ready');
                postHostState();
            } catch (error) {}
        });
        document.addEventListener('click', function (event) {
            var button = event.target.closest && event.target.closest('.fw-share-btn');
            if (!button) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            navigator.clipboard.writeText(window.parent.location.href).then(function () {
                button.classList.add('copied');
                window.setTimeout(function () { button.classList.remove('copied'); }, 1500);
            });
        }, true);
        document.addEventListener('click', function (event) {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey
                    || event.shiftKey || event.altKey) return;
            var link = event.target.closest && event.target.closest('a[href]');
            if (!link || link.hasAttribute('download')) return;
            var targetName = link.getAttribute('target');
            if (targetName && targetName.toLowerCase() !== '_self') return;
            var target = new URL(link.href, window.location.href);
            if (target.origin !== window.location.origin) return;
            var studioRoot = '/s/' + context.org + '/' + context.studio + '/';
            if (link.classList.contains('fw-back-link') || target.pathname === studioRoot) {
                event.preventDefault();
                event.stopImmediatePropagation();
                postHost('trellum:report-close');
                return;
            }
            if (target.pathname.indexOf(studioRoot + 'r/') === 0 && /\.html?$/i.test(target.pathname)) {
                event.preventDefault();
                event.stopImmediatePropagation();
                postHost('trellum:report-open', {
                    url: target.pathname + target.search + target.hash,
                    title: link.textContent.trim()
                });
                return;
            }
            if (link.classList.contains('fw-crumb')) {
                event.preventDefault();
                event.stopImmediatePropagation();
                window.parent.location.assign(target.href);
            }
        }, true);
        postHost('trellum:report-ready');
        postHostState();
    }

})();

/* Referrer-aware back link. The breadcrumb chevron (.fw-back-link) points at
 * the studio dashboard, but when you opened this report FROM another page in
 * the same studio — Analytics, Experiments, Annotations — "back" should
 * return you there, not always to Reports. If the referrer is another page in
 * this studio that isn't itself a report, go back in history; otherwise the
 * chevron follows its href to the dashboard as before. Direct entry (no
 * referrer, a bookmark, a share link) always gets the dashboard. */
(function () {
    var back = document.querySelector('a.fw-back-link');
    if (!back) return;
    var hostedUrl = new URL(window.location.href);
    var hostToken = hostedUrl.searchParams.get('_console_host_token');
    if (window.parent !== window && hostedUrl.searchParams.get('_console_host') === '1'
            && hostToken) {
        var match = hostedUrl.pathname.match(/^\/s\/[^/]+\/[^/]+\/r\/([^/]+)(?:\/|$)/)
            || hostedUrl.pathname.match(/^\/content\/[^/]+\/[^/]+\/([^/]+)\/builds\/[^/]+(?:\/|$)/);
        if (!match) return;
        back.addEventListener('click', function (event) {
            event.preventDefault();
            window.parent.postMessage({
                type: 'trellum:report-close', report: match[1], hostToken: hostToken
            }, window.location.origin);
        });
        return;
    }
    var studio = back.getAttribute('href') || '';   // /s/<org>/<studio>/
    if (studio.charAt(0) !== '/') return;
    var base = window.location.origin + studio;      // absolute studio root
    back.addEventListener('click', function (e) {
        var ref = document.referrer;
        if (!ref || window.history.length <= 1) return;
        // Same studio, and not another report page (avoid report→report).
        if (ref.indexOf(base) === 0 && ref.indexOf(base + 'r/') !== 0) {
            e.preventDefault();
            window.history.back();
        }
    });
})();
