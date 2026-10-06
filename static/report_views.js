/* Report view analytics (internal planning#4): "Activity" button + panel on a
 * report page's header, alongside the Email & alerts and Share buttons
 * (static/report_delivery.js / static/report_share.js -- this file mirrors
 * their structure: same self-injected styling approach, same cookie/fetch
 * helpers, same overlay+drawer shape, so all three widgets feel like one
 * product rather than three bolted-together ones).
 *
 * Served two ways, both frozen URLs (report builds carry no template to
 * attach a {% static %} tag to -- see apps.reports.views.views_widget_js's
 * docstring, which mirrors delivery_widget_js/share_widget_js for why):
 *
 *   - report page -> byte-for-byte at /api/reports/views-widget.js, baked
 *     into every built report (apps.runner.executor.portal_extensions)
 *     alongside the assistant, delivery and share widgets. Like the share
 *     widget, the header button here only self-mounts for a viewer who can
 *     actually see this data: the very first thing autoMount does is ask
 *     the views API for this report, and only builds a button if that
 *     comes back ok. A viewer without permission gets a 403 and the report
 *     page looks exactly as if this file weren't loaded at all.
 *
 * Anonymous /share/<token>/ pages never load this file -- an anonymous
 * visitor is exactly who this panel would be reporting on, not someone who
 * gets to see it.
 */
(function () {
    'use strict';

    var CSS = ''
        // Drawer animation: slide in from the right + a slight fade,
        // ~180ms ease-out; reverse on close -- see playOpen() below for the
        // force-reflow-before-first-open half of this (the reported bug).
        + '#rvwOverlay{position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:9200;opacity:0;visibility:hidden;transition:opacity .18s ease-out,visibility .18s}'
        + '#rvwOverlay.open{opacity:1;visibility:visible}'
        + '#rvwDrawer{position:fixed;top:0;right:0;bottom:0;width:480px;max-width:92vw;'
        + 'background:var(--bg-card,var(--bg-main,#fff));border-left:1px solid var(--border,var(--border-color,#ddd));'
        + 'box-shadow:-4px 0 24px rgba(0,0,0,0.16);z-index:9201;opacity:0;visibility:hidden;transform:translateX(100%);'
        + 'transition:transform .18s ease-out,opacity .18s ease-out,visibility .18s;display:flex;flex-direction:column;font-size:13px;'
        + 'color:var(--text,var(--text-main,#1a1a2e));font-family:inherit}'
        + '#rvwDrawer.open{opacity:1;visibility:visible;transform:translateX(0)}'
        + '#rvwDrawer *{box-sizing:border-box}'
        + '@media(prefers-reduced-motion:reduce){#rvwOverlay,#rvwDrawer{transition:none}}'
        + '.rvw-header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;'
        + 'padding:16px 20px;border-bottom:1px solid var(--border,var(--border-color,#ddd));flex-shrink:0}'
        + '.rvw-title{font-size:15px;font-weight:600;margin:0}'
        + '.rvw-subtitle{font-size:12px;color:var(--text2,var(--text-secondary,#777));margin-top:2px;'
        + 'max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.rvw-close{background:none;border:none;font-size:20px;line-height:1;cursor:pointer;'
        + 'color:var(--text3,var(--text-secondary,#999));padding:0 2px}'
        + '.rvw-close:hover{color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rvw-body{flex:1;overflow-y:auto;padding:16px 20px 28px}'
        + '.rvw-section{margin-bottom:22px}'
        + '.rvw-section-title{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;'
        + 'color:var(--text2,var(--text-secondary,#777));margin:0 0 10px}'
        + '.rvw-summary{display:flex;gap:20px;margin-bottom:4px}'
        + '.rvw-stat{flex:1}'
        + '.rvw-stat-value{font-size:22px;font-weight:700;line-height:1.2}'
        + '.rvw-stat-label{font-size:11px;color:var(--text2,var(--text-secondary,#777));margin-top:2px}'
        + '.rvw-empty{color:var(--text3,var(--text-secondary,#999));font-size:12px;padding:10px 0}'
        + '.rvw-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;'
        + 'padding:7px 0;border-bottom:1px solid var(--border,var(--border-color,#ddd))}'
        + '.rvw-row:last-child{border-bottom:none}'
        + '.rvw-row-who{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.rvw-row-when{color:var(--text2,var(--text-secondary,#777));font-size:11px;white-space:nowrap;flex-shrink:0}'
        + '.rvw-via-share{color:var(--text3,var(--text-secondary,#999));font-style:italic}'
        + '.rvw-flash{border-radius:6px;padding:8px 12px;font-size:12px;margin-bottom:12px}'
        + '.rvw-flash.error{color:var(--red,#dc2626);background:color-mix(in srgb,var(--red,#dc2626) 14%,transparent)}';

    // The header button (Email delivery/Share/Activity/Export, all in one
    // "Options" dropdown) lives in static/report_menu.js now -- this file
    // registers an item instead of mounting its own button. See autoMount()
    // below.

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── cookie / fetch (mirrors static/report_share.js) ─────────────────────
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
    }

    function apiFetch(url, options) {
        options = options || {};
        options.headers = options.headers || {};
        options.credentials = 'same-origin';
        var method = (options.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD') {
            options.headers['X-CSRFToken'] = getCookie('csrftoken');
        }
        return fetch(url, options).then(function (resp) {
            if (resp.status === 401) window.location = '/login';
            return resp;
        });
    }

    function fmtDate(iso) {
        if (!iso) return '';
        try {
            var d = new Date(iso);
            return d.toLocaleString(undefined, {
                year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
            });
        } catch (e) { return iso; }
    }

    function relativeTime(iso) {
        if (!iso) return 'Never';
        var diff = (Date.now() - new Date(iso).getTime()) / 1000;
        if (diff < 60) return 'Just now';
        if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return Math.floor(diff / 86400) + 'd ago';
    }

    // ── state ────────────────────────────────────────────────────────────
    var state = null;   // { prefix, slug, name, triggerEl }
    var built = false;

    function apiBase() { return state.prefix + '/api/reports/' + encodeURIComponent(state.slug) + '/views'; }

    // ── style injection ──────────────────────────────────────────────────
    // The header button that used to make this eager (report-page autoMount
    // creating a button before its CSS existed -- the FOUC bug) now lives in
    // static/report_menu.js instead; this file only ever shows UI it creates
    // and displays in the same call (ensureDom() then immediately open()),
    // so lazy injection from ensureDom() is sufficient here. injectStyle()
    // stays idempotent regardless.
    var styleInjected = false;
    function injectStyle() {
        if (styleInjected) return;
        styleInjected = true;
        var style = document.createElement('style');
        style.id = 'rvwStyle';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // ── DOM scaffold ─────────────────────────────────────────────────────
    function ensureDom() {
        if (built) return;
        built = true;
        injectStyle();

        var overlay = document.createElement('div');
        overlay.id = 'rvwOverlay';
        overlay.addEventListener('click', close);
        document.body.appendChild(overlay);

        var drawer = document.createElement('div');
        drawer.id = 'rvwDrawer';
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-modal', 'true');
        drawer.setAttribute('aria-labelledby', 'rvwTitle');
        drawer.innerHTML =
            '<div class="rvw-header">'
            + '<div><h2 class="rvw-title" id="rvwTitle">Activity</h2><div class="rvw-subtitle" id="rvwSubtitle"></div></div>'
            + '<button type="button" class="rvw-close" id="rvwClose" title="Close" aria-label="Close">&times;</button>'
            + '</div>'
            + '<div class="rvw-body" id="rvwBody"></div>';
        document.body.appendChild(drawer);

        document.getElementById('rvwClose').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (!drawer.classList.contains('open')) return;
            e.stopPropagation();
            close();
        });
    }

    function close() {
        var overlay = document.getElementById('rvwOverlay');
        var drawer = document.getElementById('rvwDrawer');
        if (!drawer || !drawer.classList.contains('open')) return;
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        if (state && state.triggerEl && typeof state.triggerEl.focus === 'function') {
            state.triggerEl.focus();
        }
    }

    function rowHtml(row) {
        var who = row.via_share
            ? '<span class="rvw-via-share">via share link</span>'
            : esc(row.who || 'Unknown');
        return '<div class="rvw-row"><span class="rvw-row-who">' + who + '</span>'
            + '<span class="rvw-row-when">' + esc(fmtDate(row.when)) + '</span></div>';
    }

    function render(data) {
        var body = document.getElementById('rvwBody');
        var summary = data.summary || {};
        var recent = data.recent || [];

        var html = '<div class="rvw-section"><div class="rvw-summary">'
            + '<div class="rvw-stat"><div class="rvw-stat-value">' + (summary.views_30d || 0) + '</div>'
            + '<div class="rvw-stat-label">Views (30d)</div></div>'
            + '<div class="rvw-stat"><div class="rvw-stat-value">' + esc(relativeTime(summary.last_viewed)) + '</div>'
            + '<div class="rvw-stat-label">Last viewed</div></div>'
            + '</div></div>';

        html += '<div class="rvw-section"><h3 class="rvw-section-title">Recent views</h3>';
        if (!recent.length) {
            html += '<div class="rvw-empty">No views yet.</div>';
        } else {
            html += recent.map(rowHtml).join('');
        }
        html += '</div>';

        body.innerHTML = html;
    }

    function loadAndRender() {
        apiFetch(apiBase()).then(function (r) {
            if (!r.ok) throw new Error('failed to load');
            return r.json();
        }).then(function (d) {
            render(d);
        }).catch(function () {
            document.getElementById('rvwBody').innerHTML =
                '<div class="rvw-flash error">Could not load view activity.</div>';
        });
    }

    // Force a reflow so the browser has actually painted the CLOSED state
    // at least once, then defer adding .open by two animation frames --
    // see static/report_delivery.js's identical helper for why (the "first
    // open doesn't animate" bug).
    function playOpen(overlay, drawer) {
        if (!overlay || !drawer) return;
        void drawer.offsetHeight;
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                overlay.classList.add('open');
                drawer.classList.add('open');
            });
        });
    }

    // `seed`, when given, is a views payload already fetched (the
    // report-page mount-time permission probe -- see autoMount() below):
    // used once instead of paying for a second round trip, then never
    // again, so a later open always re-fetches and the panel does not go
    // stale across a long-lived tab.
    function open(prefix, slug, name, triggerEl, seed) {
        ensureDom();
        state = { prefix: prefix, slug: slug, name: name, triggerEl: triggerEl || document.activeElement };
        document.getElementById('rvwSubtitle').textContent = name || slug;
        document.getElementById('rvwBody').innerHTML = '<div class="rvw-empty">Loading…</div>';
        playOpen(document.getElementById('rvwOverlay'), document.getElementById('rvwDrawer'));
        var closeBtn = document.getElementById('rvwClose');
        if (closeBtn) closeBtn.focus();
        if (seed) { render(seed); } else { loadAndRender(); }
    }

    window.ReportViews = { open: open, close: close };

    // ── report-page auto-mount ──────────────────────────────────────────
    // Only runs on a built report page (never the dashboard, which has its
    // own reporting elsewhere) -- see static/report_delivery.js's identical
    // guard for why window.PORTAL_CTX is the signal.
    // Registers an "Activity" item on the shared Options menu
    // (static/report_menu.js) rather than mounting its own header button.
    var _registered = false;
    function autoMount() {
        if (window.PORTAL_CTX) return;
        var m = window.location.pathname.match(/^(\/s\/[a-z0-9-]+\/[a-z0-9-]+)\/r\/([A-Za-z0-9_-]+)\//);
        if (!m) return;
        var prefix = m[1], slug = m[2];
        var mount = function () {
            if (_registered || !window.__reportMenu) return;

            // Gate the item's very existence on the viewer's own role: a
            // 403 here (viewer, not developer/admin) leaves the report page
            // looking exactly as if this script weren't loaded. The payload
            // from this same call seeds the drawer on first open so opening
            // it doesn't cost a second round trip.
            var seed = null;
            apiFetch(prefix + '/api/reports/' + encodeURIComponent(slug) + '/views')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d) return;
                    _registered = true;
                    seed = d;

                    window.__reportMenu.register({
                        id: 'activity', order: 50, label: 'Activity',
                        icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 11h3l2-5.5 4 9 2-3.5h4"/></svg>',
                        onSelect: function () {
                            var h1 = document.querySelector('.fw-header h1');
                            var name = h1 ? h1.textContent : document.title;
                            open(prefix, slug, name, document.getElementById('fwOptionsBtn'), seed);
                            seed = null;  // only the first open gets the free seed
                        }
                    });
                }).catch(function () {});
        };
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
        else mount();
    }
    autoMount();
})();
