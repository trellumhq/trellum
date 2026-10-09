/* "Metrics in this report" panel (semantic-layer Phase 2): what a report's
 * claimed KPIs mean, read where they're used. Mirrors static/report_views.js's
 * structure -- same self-injected styling approach, same cookie/fetch
 * helpers, same overlay+drawer shape -- so this feels like the same product
 * as the other three report-page widgets, not a bolted-on fourth one.
 *
 * Served two ways, both frozen URLs (report builds carry no template to
 * attach a {% static %} tag to -- see apps.reports.views.metrics_widget_js's
 * docstring, which mirrors delivery_widget_js/share_widget_js/views_widget_js
 * for why):
 *
 *   - report page -> byte-for-byte at /api/reports/metrics-widget.js, baked
 *     into every built report (apps.runner.executor.portal_extensions)
 *     alongside the assistant, delivery, share and views widgets.
 *
 * Unlike the Activity widget, this one never has to probe permission first
 * -- api_report_metrics is VIEWER-gated, same as every viewer of the report
 * already is. It only self-mounts when the build actually claims a metric:
 * a report with none gets no "Metrics" entry at all, same shape as the
 * catalog page's own "no reports claim this" state.
 *
 * Anonymous /share/<token>/ pages never load this file (menu-widget.js's own
 * host only ever registers on an authenticated report page).
 */
(function () {
    'use strict';

    var CSS = ''
        // Drawer animation: identical timing/easing to report_views.js's
        // #rvwOverlay/#rvwDrawer -- see that file's own comment for the
        // force-reflow-before-first-open half of this.
        + '#mtpOverlay{position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:9200;opacity:0;visibility:hidden;transition:opacity .18s ease-out,visibility .18s}'
        + '#mtpOverlay.open{opacity:1;visibility:visible}'
        + '#mtpDrawer{position:fixed;top:0;right:0;bottom:0;width:480px;max-width:92vw;'
        + 'background:var(--bg-card,var(--bg-main,#fff));border-left:1px solid var(--border,var(--border-color,#ddd));'
        + 'box-shadow:-4px 0 24px rgba(0,0,0,0.16);z-index:9201;opacity:0;visibility:hidden;transform:translateX(100%);'
        + 'transition:transform .18s ease-out,opacity .18s ease-out,visibility .18s;display:flex;flex-direction:column;font-size:13px;'
        + 'color:var(--text,var(--text-main,#1a1a2e));font-family:inherit}'
        + '#mtpDrawer.open{opacity:1;visibility:visible;transform:translateX(0)}'
        + '#mtpDrawer *{box-sizing:border-box}'
        + '@media(prefers-reduced-motion:reduce){#mtpOverlay,#mtpDrawer{transition:none}}'
        + '.mtp-header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;'
        + 'padding:16px 20px;border-bottom:1px solid var(--border,var(--border-color,#ddd));flex-shrink:0}'
        + '.mtp-title{font-size:15px;font-weight:600;margin:0}'
        + '.mtp-subtitle{font-size:12px;color:var(--text2,var(--text-secondary,#777));margin-top:2px;'
        + 'max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.mtp-close{background:none;border:none;font-size:20px;line-height:1;cursor:pointer;'
        + 'color:var(--text3,var(--text-secondary,#999));padding:0 2px}'
        + '.mtp-close:hover{color:var(--text,var(--text-main,#1a1a2e))}'
        + '.mtp-body{flex:1;overflow-y:auto;padding:8px 20px 16px}'
        + '.mtp-card{padding:12px 0;border-bottom:1px solid var(--border,var(--border-color,#ddd))}'
        + '.mtp-card:last-child{border-bottom:none}'
        + '.mtp-card-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}'
        + '.mtp-name{font-size:13px;font-weight:600}'
        + '.mtp-badge{font-size:10px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;'
        + 'padding:2px 8px;border-radius:99px;white-space:nowrap}'
        + '.mtp-badge.current{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 14%,transparent)}'
        + '.mtp-badge.stale{color:var(--warn,#d97706);background:color-mix(in srgb,var(--warn,#d97706) 14%,transparent)}'
        + '.mtp-desc{font-size:12px;color:var(--text2,var(--text-secondary,#777));margin-top:5px;line-height:1.5}'
        + '.mtp-spec{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;color:var(--text,var(--text-main,#1a1a2e));'
        + 'background:var(--bg,var(--bg-main,#f4f4f6));border-radius:4px;padding:1px 6px;margin-top:6px;display:inline-block}'
        + '.mtp-empty{color:var(--text3,var(--text-secondary,#999));font-size:12px;padding:10px 0}'
        + '.mtp-flash{border-radius:6px;padding:8px 12px;font-size:12px;margin-bottom:12px}'
        + '.mtp-flash.error{color:var(--red,#dc2626);background:color-mix(in srgb,var(--red,#dc2626) 14%,transparent)}'
        + '.mtp-foot{font-size:11px;color:var(--text3,var(--text-secondary,#999));padding:12px 20px;'
        + 'border-top:1px solid var(--border,var(--border-color,#ddd));flex-shrink:0}'
        + '.mtp-foot a{color:var(--accent,#4e79a7)}';

    // The header button (Export/Email delivery/Share/Activity/Metrics, all in
    // one "Options" dropdown) lives in static/report_menu.js -- this file
    // registers an item instead of mounting its own button. See autoMount().

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── cookie / fetch (mirrors static/report_views.js) ─────────────────────
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

    // ── state ────────────────────────────────────────────────────────────
    var state = null;   // { prefix, slug, name, triggerEl }
    var built = false;

    function apiBase() { return state.prefix + '/api/reports/' + encodeURIComponent(state.slug) + '/metrics'; }

    // ── style injection ──────────────────────────────────────────────────
    var styleInjected = false;
    function injectStyle() {
        if (styleInjected) return;
        styleInjected = true;
        var style = document.createElement('style');
        style.id = 'mtpStyle';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // ── DOM scaffold ─────────────────────────────────────────────────────
    function ensureDom() {
        if (built) return;
        built = true;
        injectStyle();

        var overlay = document.createElement('div');
        overlay.id = 'mtpOverlay';
        overlay.addEventListener('click', close);
        document.body.appendChild(overlay);

        var drawer = document.createElement('div');
        drawer.id = 'mtpDrawer';
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-modal', 'true');
        drawer.setAttribute('aria-labelledby', 'mtpTitle');
        drawer.innerHTML =
            '<div class="mtp-header">'
            + '<div><h2 class="mtp-title" id="mtpTitle">Metrics</h2><div class="mtp-subtitle" id="mtpSubtitle"></div></div>'
            + '<button type="button" class="mtp-close" id="mtpClose" title="Close" aria-label="Close">&times;</button>'
            + '</div>'
            + '<div class="mtp-body" id="mtpBody"></div>'
            + '<div class="mtp-foot" id="mtpFoot"></div>';
        document.body.appendChild(drawer);

        document.getElementById('mtpClose').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (!drawer.classList.contains('open')) return;
            e.stopPropagation();
            close();
        });
    }

    function close() {
        var overlay = document.getElementById('mtpOverlay');
        var drawer = document.getElementById('mtpDrawer');
        if (!drawer || !drawer.classList.contains('open')) return;
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        if (state && state.triggerEl && typeof state.triggerEl.focus === 'function') {
            state.triggerEl.focus();
        }
    }

    function cardHtml(m) {
        var badge = m.current
            ? '<span class="mtp-badge current">current</span>'
            : '<span class="mtp-badge stale">' + (m.removed
                ? 'no longer in metrics.yaml'
                : 'definition updated since this build') + '</span>';
        var desc = m.description ? '<div class="mtp-desc">' + esc(m.description) + '</div>' : '';
        var spec = m.spec ? '<div><span class="mtp-spec">' + esc(m.spec) + '</span></div>' : '';
        return '<div class="mtp-card">'
            + '<div class="mtp-card-head"><span class="mtp-name">' + esc(m.label) + '</span>' + badge + '</div>'
            + desc + spec
            + '</div>';
    }

    function render(data) {
        var body = document.getElementById('mtpBody');
        var metrics = data.metrics || [];
        body.innerHTML = metrics.length
            ? metrics.map(cardHtml).join('')
            : '<div class="mtp-empty">This build claims no metrics.</div>';

        var foot = document.getElementById('mtpFoot');
        var built = data.built_at ? 'From this build (' + esc(fmtDate(data.built_at)) + '). ' : '';
        var link = data.studio_metrics_url
            ? '<a href="' + esc(data.studio_metrics_url) + '">Studio &#9656; Metrics</a>'
            : 'Studio &#9656; Metrics';
        foot.innerHTML = built + 'Full catalog: ' + link;
    }

    function loadAndRender() {
        apiFetch(apiBase()).then(function (r) {
            if (!r.ok) throw new Error('failed to load');
            return r.json();
        }).then(function (d) {
            render(d);
        }).catch(function () {
            document.getElementById('mtpBody').innerHTML =
                '<div class="mtp-flash error">Could not load this report’s metrics.</div>';
        });
    }

    // Force a reflow before the first open -- see report_views.js's
    // identical helper for why (the "first open doesn't animate" bug).
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

    // `seed`, when given, is a metrics payload already fetched (the
    // report-page mount-time claim probe -- see autoMount() below): used
    // once instead of paying for a second round trip, then never again, so
    // a later open always re-fetches and the panel does not go stale across
    // a long-lived tab.
    function open(prefix, slug, name, triggerEl, seed) {
        ensureDom();
        state = { prefix: prefix, slug: slug, name: name, triggerEl: triggerEl || document.activeElement };
        document.getElementById('mtpSubtitle').textContent = name || slug;
        document.getElementById('mtpBody').innerHTML = '<div class="mtp-empty">Loading…</div>';
        document.getElementById('mtpFoot').innerHTML = '';
        playOpen(document.getElementById('mtpOverlay'), document.getElementById('mtpDrawer'));
        var closeBtn = document.getElementById('mtpClose');
        if (closeBtn) closeBtn.focus();
        if (seed) { render(seed); } else { loadAndRender(); }
    }

    window.ReportMetrics = { open: open, close: close };

    // ── report-page auto-mount ──────────────────────────────────────────
    // Only runs on a built report page (never the dashboard) -- see
    // static/report_views.js's identical guard for why window.PORTAL_CTX is
    // the signal. Registers a "Metrics · N" item on the shared Options
    // menu (static/report_menu.js); a build that claims nothing never gets
    // the entry at all.
    var _registered = false;
    function autoMount() {
        if (window.PORTAL_CTX) return;
        var m = window.location.pathname.match(/^(\/s\/[a-z0-9-]+\/[a-z0-9-]+)\/r\/([A-Za-z0-9_-]+)\//);
        if (!m) return;
        var prefix = m[1], slug = m[2];
        var mount = function () {
            if (_registered || !window.__reportMenu) return;

            var seed = null;
            apiFetch(prefix + '/api/reports/' + encodeURIComponent(slug) + '/metrics')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d || !d.metrics || !d.metrics.length) return;
                    _registered = true;
                    seed = d;

                    window.__reportMenu.register({
                        id: 'metrics', order: 60, label: 'Metrics · ' + d.metrics.length,
                        icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v14h14"/><path d="M7 14v-3"/><path d="M11 14V7"/><path d="M15 14v-6"/></svg>',
                        onSelect: function () {
                            var h1 = document.querySelector('.fw-header h1');
                            var name = h1 ? h1.textContent : document.title;
                            open(prefix, slug, name, window.__reportMenu.getTrigger(), seed);
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
