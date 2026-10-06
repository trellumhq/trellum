/* Public share links (internal planning#6): "Share" button + panel on a report
 * page's header, next to the Email & alerts button (static/report_delivery.js
 * — this file mirrors its structure: same self-injected styling approach,
 * same cookie/fetch helpers, same overlay+drawer shape, so the two widgets
 * feel like one product rather than two bolted-together ones).
 *
 * Served two ways, both frozen URLs (report builds carry no template to
 * attach a {% static %} tag to — see apps.reports.views.share_widget_js's
 * docstring for why):
 *
 *   - report page -> byte-for-byte at /api/reports/share-widget.js, baked
 *     into every built report (apps.runner.executor.portal_extensions)
 *     alongside the assistant and delivery widgets. Unlike those two, the
 *     header button here only self-mounts for a viewer who can actually
 *     manage links: the very first thing autoMount does is ask the manage
 *     API for this report's share links, and only builds a button if that
 *     comes back ok. A viewer without permission gets a 403 and the report
 *     page looks exactly as if this file weren't loaded at all.
 *
 * Anonymous /share/<token>/ pages never load this file — they carry no
 * "manage links" affordance at all, only the report itself.
 */
(function () {
    'use strict';

    var CSS = ''
        // Drawer animation: slide in from the right + a slight fade,
        // ~180ms ease-out; reverse on close -- see playOpen() below for the
        // force-reflow-before-first-open half of this (the reported bug).
        + '#rswOverlay{position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:9200;opacity:0;visibility:hidden;transition:opacity .18s ease-out,visibility .18s}'
        + '#rswOverlay.open{opacity:1;visibility:visible}'
        + '#rswDrawer{position:fixed;top:0;right:0;bottom:0;width:480px;max-width:92vw;'
        + 'background:var(--bg-card,var(--bg-main,#fff));border-left:1px solid var(--border,var(--border-color,#ddd));'
        + 'box-shadow:-4px 0 24px rgba(0,0,0,0.16);z-index:9201;opacity:0;visibility:hidden;transform:translateX(100%);'
        + 'transition:transform .18s ease-out,opacity .18s ease-out,visibility .18s;display:flex;flex-direction:column;font-size:13px;'
        + 'color:var(--text,var(--text-main,#1a1a2e));font-family:inherit}'
        + '#rswDrawer.open{opacity:1;visibility:visible;transform:translateX(0)}'
        + '#rswDrawer *{box-sizing:border-box}'
        + '@media(prefers-reduced-motion:reduce){#rswOverlay,#rswDrawer{transition:none}}'
        + '.rsw-header{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;'
        + 'padding:16px 20px;border-bottom:1px solid var(--border,var(--border-color,#ddd));flex-shrink:0}'
        + '.rsw-title{font-size:15px;font-weight:600;margin:0}'
        + '.rsw-subtitle{font-size:12px;color:var(--text2,var(--text-secondary,#777));margin-top:2px;'
        + 'max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
        + '.rsw-close{background:none;border:none;font-size:20px;line-height:1;cursor:pointer;'
        + 'color:var(--text3,var(--text-secondary,#999));padding:0 2px}'
        + '.rsw-close:hover{color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rsw-body{flex:1;overflow-y:auto;padding:16px 20px 28px}'
        + '.rsw-section{margin-bottom:22px}'
        + '.rsw-section-title{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;'
        + 'color:var(--text2,var(--text-secondary,#777));margin:0 0 10px}'
        + '.rsw-note{color:var(--text2,var(--text-secondary,#777));font-size:12px;margin:0;line-height:1.5}'
        // Same !important rationale as static/report_delivery.js's CSS block:
        // this widget can be opened from a page whose own stylesheet
        // (ui.css's body.mgmt rules) outranks single-class selectors on real
        // specificity, so self-injected widget CSS has to be able to assume
        // it always wins.
        + '.rsw-btn{display:inline-flex!important;align-items:center;gap:6px;border-radius:6px!important;padding:6px 12px!important;'
        + 'font-size:12px!important;font-weight:500;cursor:pointer;border:1px solid var(--border,var(--border-color,#ddd))!important;'
        + 'background:var(--bg-card,var(--bg-main,#fff))!important;color:var(--text,var(--text-main,#1a1a2e));transition:all .15s}'
        + '.rsw-btn:hover{border-color:var(--accent,var(--bg-header,#4e79a7));color:inherit}'
        + '.rsw-btn:disabled{opacity:.5;cursor:default}'
        + '.rsw-btn.primary{background:var(--accent,var(--bg-header,#4e79a7))!important;border-color:var(--accent,var(--bg-header,#4e79a7))!important;color:var(--on-accent,#fff)}'
        + '.rsw-btn.danger{color:var(--red,var(--accent-red,#dc2626))}'
        + '.rsw-btn.danger:hover{border-color:var(--red,var(--accent-red,#dc2626))}'
        + '.rsw-btn.small{padding:3px 8px!important;font-size:11px!important}'
        + '.rsw-field{margin-bottom:12px}'
        + '.rsw-label{display:block;font-size:11px;font-weight:600;color:var(--text2,var(--text-secondary,#777));margin-bottom:4px}'
        + '.rsw-input{width:100%;padding:6px 8px;border-radius:6px;font-size:13px;'
        + 'border:1px solid var(--border,var(--border-color,#ddd));background:var(--bg,var(--bg-main,#fff));'
        + 'color:var(--text,var(--text-main,#1a1a2e))}'
        + '.rsw-input:focus,.rsw-btn:focus-visible,.rsw-close:focus-visible{'
        + 'outline:2px solid var(--focus,var(--accent,#4e79a7));outline-offset:1px}'
        + '.rsw-check-row{display:flex!important;align-items:flex-start;gap:8px;padding:6px 0;cursor:pointer}'
        + '.rsw-check-row input{margin-top:2px;flex-shrink:0}'
        + '.rsw-check-row .rsw-check-label{font-weight:500}'
        + '.rsw-help{display:block;color:var(--text3,var(--text-secondary,#999));font-size:11px;margin-top:4px}'
        + '.rsw-check-row .rsw-help{margin-top:1px}'
        + '.rsw-field-error{color:var(--red,var(--accent-red,#dc2626));font-size:11px;margin-top:3px}'
        + '.rsw-link-row{border:1px solid var(--border,var(--border-color,#ddd));border-radius:8px;'
        + 'padding:10px 12px;margin-bottom:8px}'
        // Inactive rows (revoked / expired / blocked-by-policy) read as
        // clearly not-live: reduced opacity AND a desaturated neutral
        // border (not the normal border token, which reads as "just
        // slightly quieter" rather than "this isn't active") -- the
        // complaint this responds to was that an active and a revoked row
        // looked nearly identical.
        + '.rsw-link-row.rsw-inactive-row{opacity:.6;border-color:var(--text3,var(--text-secondary,#999))}'
        + '.rsw-link-meta{color:var(--text2,var(--text-secondary,#777));font-size:11px;margin-bottom:6px;line-height:1.6}'
        + '.rsw-link-url{display:block;width:100%;padding:5px 7px;border-radius:5px;font-size:11px;'
        + 'font-family:ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--border,var(--border-color,#ddd));'
        + 'background:var(--bg,var(--bg-main,#fff));color:var(--text,var(--text-main,#1a1a2e));'
        + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-bottom:8px}'
        + '.rsw-link-actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center}'
        + '.rsw-badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;'
        + 'letter-spacing:.02em;margin-left:6px}'
        + '.rsw-badge.on{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 14%,transparent)}'
        + '.rsw-badge.off{color:var(--text3,var(--text-secondary,#999));background:color-mix(in srgb,var(--text3,#999) 14%,transparent)}'
        // Status chips: one clearly distinct color family per state, so a
        // row's liveness reads at a glance instead of requiring the meta
        // text to be parsed. active = the "this works" color (teal/green,
        // same family as .rsw-badge.on); revoked = red (a person did this,
        // permanent, deliberate); expired = neutral grey (nothing to do,
        // natural lifecycle end); blocked = amber (recoverable -- resumes
        // on its own if the org policy relaxes, so it reads as "paused"
        // rather than "dead" like revoked/expired do).
        + '.rsw-chip{display:inline-flex;align-items:center;padding:1px 8px;border-radius:10px;font-size:10px;'
        + 'font-weight:700;letter-spacing:.03em;text-transform:uppercase;white-space:nowrap}'
        + '.rsw-chip.active{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 16%,transparent)}'
        + '.rsw-chip.revoked{color:var(--red,#dc2626);background:color-mix(in srgb,var(--red,#dc2626) 14%,transparent)}'
        + '.rsw-chip.expired{color:var(--text3,var(--text-secondary,#999));background:color-mix(in srgb,var(--text3,#999) 16%,transparent)}'
        + '.rsw-chip.blocked{color:var(--warn,#d97706);background:color-mix(in srgb,var(--warn,#d97706) 16%,transparent)}'
        + '.rsw-chip.embed{color:var(--accent,#4e79a7);background:color-mix(in srgb,var(--accent,#4e79a7) 16%,transparent)}'
        + '.rsw-field[hidden]{display:none!important}'
        + '.rsw-empty{color:var(--text3,var(--text-secondary,#999));font-size:12px;padding:10px 0}'
        + '.rsw-flash{border-radius:6px;padding:8px 12px;font-size:12px;margin-bottom:12px;display:flex;'
        + 'align-items:flex-start;justify-content:space-between;gap:8px}'
        + '.rsw-flash.success{color:var(--green,#00b894);background:color-mix(in srgb,var(--green,#00b894) 14%,transparent)}'
        + '.rsw-flash.error{color:var(--red,#dc2626);background:color-mix(in srgb,var(--red,#dc2626) 14%,transparent)}'
        + '.rsw-flash-close{background:none;border:none;cursor:pointer;color:inherit;font-size:14px;line-height:1;opacity:.7}'
        // Collapsed-by-default "Show inactive links" disclosure at the
        // bottom of the panel -- same button look as every other rsw-btn,
        // just full-width so it reads as a section toggle, not an action.
        + '.rsw-toggle-btn{display:flex!important;width:100%;justify-content:center;margin-top:4px}'
        + '.rsw-inactive-list{margin-top:8px}'
        + '.rsw-inactive-list[hidden]{display:none!important}'
        // One-time password reveal after creating a link with a password:
        // deliberately styled apart from the ordinary link rows (a warm
        // border, not the neutral one) so it reads as "pay attention, this
        // is the only time you'll see this" rather than blending into the
        // rest of the panel.
        + '.rsw-reveal{border:1px solid var(--warn,#d97706);border-radius:8px;padding:12px 14px;margin-bottom:16px}'
        + '.rsw-reveal-title{font-weight:600;margin:0 0 6px}'
        + '.rsw-reveal-warn{color:var(--warn,#d97706);font-weight:600;font-size:12px;margin:8px 0}'
        + '.rsw-reveal-row{margin-top:8px}'
        // Inline embed preview: a small frame of the link's own URL, so the
        // report scrolls inside it rather than the drawer growing to fit a
        // report of any height.
        + '.rsw-preview{margin-top:8px}'
        + '.rsw-preview[hidden]{display:none!important}'
        + '.rsw-preview iframe{display:block;width:100%;height:260px;border:1px solid '
        + 'var(--border,var(--border-color,#ddd));border-radius:6px;background:var(--bg,#fff)}';

    // The header button (Email delivery/Share/Activity/Export, all in one
    // "Options" dropdown) lives in static/report_menu.js now -- this file
    // registers an item instead of mounting its own button. See autoMount()
    // below.

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── cookie / fetch (mirrors static/report_delivery.js) ──────────────────
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

    function postJson(url, method, body) {
        return apiFetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (parsed) {
                return { ok: r.ok, data: parsed };
            });
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
    var state = null;   // { prefix, slug, orgSlug, name, triggerEl }
    var data = null;    // last GET /share_links payload
    var built = false;

    function apiBase() { return state.prefix + '/api/reports/' + encodeURIComponent(state.slug) + '/share_links'; }

    // prefix is "/s/<org>/<studio>" -- the org slug the "enable in
    // organization settings" link (render(), when sharing is org-disabled)
    // points at.
    function orgSlugFromPrefix(prefix) { return (prefix || '').split('/')[2] || ''; }

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
        style.id = 'rswStyle';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    // ── DOM scaffold ─────────────────────────────────────────────────────
    function ensureDom() {
        if (built) return;
        built = true;
        injectStyle();

        var overlay = document.createElement('div');
        overlay.id = 'rswOverlay';
        overlay.addEventListener('click', close);
        document.body.appendChild(overlay);

        var drawer = document.createElement('div');
        drawer.id = 'rswDrawer';
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-modal', 'true');
        drawer.setAttribute('aria-labelledby', 'rswTitle');
        drawer.innerHTML =
            '<div class="rsw-header">'
            + '<div><h2 class="rsw-title" id="rswTitle">Share</h2><div class="rsw-subtitle" id="rswSubtitle"></div></div>'
            + '<button type="button" class="rsw-close" id="rswClose" title="Close" aria-label="Close">&times;</button>'
            + '</div>'
            + '<div class="rsw-body" id="rswBody"></div>';
        document.body.appendChild(drawer);

        document.getElementById('rswClose').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (!drawer.classList.contains('open')) return;
            e.stopPropagation();
            close();
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

    // `seed`, when given, is a share_links payload already fetched (the
    // report-page mount-time permission probe -- see autoMount() below):
    // used once instead of paying for a second round trip, then never
    // again, so a later open always re-fetches and the panel does not go
    // stale across a long-lived tab.
    function open(prefix, slug, name, triggerEl, seed) {
        ensureDom();
        state = {
            prefix: prefix, slug: slug, orgSlug: orgSlugFromPrefix(prefix),
            name: name, triggerEl: triggerEl || document.activeElement
        };
        document.getElementById('rswSubtitle').textContent = name || slug;
        document.getElementById('rswBody').innerHTML = '<div class="rsw-empty">Loading…</div>';
        playOpen(document.getElementById('rswOverlay'), document.getElementById('rswDrawer'));
        var closeBtn = document.getElementById('rswClose');
        if (closeBtn) closeBtn.focus();
        if (seed) { data = seed; render(); } else { loadAndRender(); }
    }

    function close() {
        var overlay = document.getElementById('rswOverlay');
        var drawer = document.getElementById('rswDrawer');
        if (!drawer || !drawer.classList.contains('open')) return;
        overlay.classList.remove('open');
        drawer.classList.remove('open');
        if (state && state.triggerEl && typeof state.triggerEl.focus === 'function') {
            state.triggerEl.focus();
        }
    }

    function loadAndRender() {
        apiFetch(apiBase()).then(function (r) {
            if (!r.ok) throw new Error('failed to load');
            return r.json();
        }).then(function (d) {
            data = d;
            render();
        }).catch(function () {
            document.getElementById('rswBody').innerHTML =
                '<div class="rsw-flash error">Could not load share links.</div>';
        });
    }

    function flash(msg, type) {
        var body = document.getElementById('rswBody');
        var old = body.querySelector('.rsw-flash');
        if (old) old.remove();
        var div = document.createElement('div');
        div.className = 'rsw-flash ' + type;
        div.setAttribute('role', type === 'error' ? 'alert' : 'status');
        div.innerHTML = '<span>' + esc(msg) + '</span><button type="button" class="rsw-flash-close" aria-label="Dismiss">&times;</button>';
        div.querySelector('.rsw-flash-close').addEventListener('click', function () { div.remove(); });
        body.insertBefore(div, body.firstChild);
        if (type !== 'error') setTimeout(function () { if (div.parentNode) div.remove(); }, 4000);
    }

    // ── render ───────────────────────────────────────────────────────────
    function pad2n(n) { return (n < 10 ? '0' : '') + n; }

    // datetime-local's value format has no timezone -- every helper below
    // treats "now" as the *browser's* local time, matching how the input
    // itself is read back (see the submit handler's expires_at conversion).
    function toDatetimeLocalValue(d) {
        return d.getFullYear() + '-' + pad2n(d.getMonth() + 1) + '-' + pad2n(d.getDate())
            + 'T' + pad2n(d.getHours()) + ':' + pad2n(d.getMinutes());
    }

    function datetimeLocalDaysFromNow(days) {
        return toDatetimeLocalValue(new Date(Date.now() + days * 86400000));
    }

    // Prefill for a fresh "New link" form: a day out, rounded up to the
    // next full hour (a bare "+1 day" lands on today's odd minute, which
    // just reads as a bug), clamped to the org's cap when that's shorter
    // than a day. The user can still change or clear this -- see the
    // `required` attribute below, which is the only thing that actually
    // forces a value.
    function defaultExpiryValue(maxExpiryDays) {
        var d = new Date(Date.now() + 86400000);
        d.setMinutes(0, 0, 0);
        d.setHours(d.getHours() + 1);
        if (maxExpiryDays) {
            var cap = new Date(Date.now() + maxExpiryDays * 86400000);
            if (d > cap) d = cap;
        }
        return toDatetimeLocalValue(d);
    }

    // Unambiguous alphabet for the "Generate" button: a-z/A-Z/2-9 minus
    // characters that are easily confused with one another when read off a
    // screen or a printout (l/I, O/o; 0 and 1 are already excluded by
    // starting the digits at 2).
    var GEN_PASSWORD_ALPHABET = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    var GEN_PASSWORD_LENGTH = 16;

    function generatePassword() {
        var bytes = new Uint32Array(GEN_PASSWORD_LENGTH);
        if (window.crypto && window.crypto.getRandomValues) {
            window.crypto.getRandomValues(bytes);
        } else {
            // No Web Crypto (ancient browser): weaker, but this is a
            // convenience generator, not a key -- the 12-character minimum
            // and the server-side check are what actually guard a link's
            // password, on any browser.
            for (var i = 0; i < GEN_PASSWORD_LENGTH; i++) bytes[i] = Math.floor(Math.random() * 4294967296);
        }
        var out = '';
        for (var j = 0; j < GEN_PASSWORD_LENGTH; j++) {
            out += GEN_PASSWORD_ALPHABET[bytes[j] % GEN_PASSWORD_ALPHABET.length];
        }
        return out;
    }

    function copyToClipboard(text, successMessage) {
        var done = function () { flash(successMessage, 'success'); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () {
                flash('Could not copy — select and copy the value manually.', 'error');
            });
        } else {
            var ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); done(); } catch (err) { /* ignore */ }
            document.body.removeChild(ta);
        }
    }

    // What "Copy embed code" hands over: the iframe *and* the listener that
    // sizes it. An embedded report hides its own scrollbars and reports its
    // height with trellum:height (static/report_embed.js), so an iframe on
    // its own clips a tall report at min-height with nothing to scroll.
    // The listener finds its iframe through document.currentScript rather
    // than an id so two embeds on one page stay independent -- including
    // the same link pasted twice, which any id derived from the URL would
    // collide on. Kept ES5 and dependency-free: it is pasted into pages we
    // know nothing about.
    function embedSnippet(url, title) {
        return '<iframe src="' + esc(url) + '" style="width:100%;border:0;min-height:480px"'
            + ' loading="lazy" title="' + esc(title) + '"></iframe>\n'
            + '<script>\n'
            + '(function () {\n'
            + '  var f = document.currentScript.previousElementSibling;\n'
            + '  var portal = new URL(f.src).origin;\n'
            + '  window.addEventListener("message", function (e) {\n'
            + '    if (e.source !== f.contentWindow || e.origin !== portal) return;\n'
            + '    if (e.data && e.data.type === "trellum:height") f.style.height = e.data.height + "px";\n'
            + '  });\n'
            + '})();\n'
            + '<\/script>';
    }

    // The themes this very page has CSS for (the framework runtime publishes
    // them as window._themes), so the dropdown can only ever offer a theme
    // the embed will actually render in -- no extra endpoint, and a repo's
    // own registered theme is offered on its own reports.
    function themeOptionsHtml() {
        var html = '<option value="">Studio default</option>';
        Object.keys(window._themes || {}).forEach(function (name) {
            html += '<option value="' + esc(name) + '">' + esc(name) + '</option>';
        });
        return html;
    }

    // One of 'active' | 'revoked' | 'expired' | 'blocked' -- the single
    // source of truth the status chip, the active/inactive split, and the
    // row's available actions all read from, so they can never disagree
    // about one link's state.
    function linkStatus(link, orgDisabled) {
        if (link.revoked_at) return 'revoked';
        if (link.expires_at && new Date(link.expires_at) <= new Date()) return 'expired';
        if (orgDisabled || link.blocked_by_policy) return 'blocked';
        return 'active';
    }

    var STATUS_CHIP_LABEL = { active: 'Active', revoked: 'Revoked', expired: 'Expired', blocked: 'Blocked by policy' };

    function linkRowHtml(link, status) {
        var expiry = link.expires_at ? fmtDate(link.expires_at) : 'No expiry';
        var origins = link.embed_origins || [];
        // Embed links have no expiry or password: their trust line is the
        // origin allow-list instead.
        var trust = link.embed
            ? 'Embeddable on ' + (origins.indexOf('*') !== -1 ? 'any site' : esc(origins.join(', ')))
            : expiry + ' &middot; ' + (link.has_password ? 'Password-protected' : 'No password');
        var meta = 'Created ' + fmtDate(link.created_at)
            + (link.created_by ? ' by ' + esc(link.created_by) : '') + '<br>'
            + trust
            + ' &middot; ' + (link.allow_export ? 'Export allowed' : 'Export hidden') + '<br>'
            + link.view_count + ' view' + (link.view_count === 1 ? '' : 's')
            + (link.last_viewed_at ? ' &middot; last ' + fmtDate(link.last_viewed_at) : '');

        var actions = '';
        if (status === 'active') {
            actions += '<button type="button" class="rsw-btn small" data-copy="' + esc(link.url) + '">Copy link</button>';
            if (link.embed) {
                actions += '<button type="button" class="rsw-btn small" data-embed-copy="' + esc(link.url) + '">Copy embed code</button>'
                    + '<button type="button" class="rsw-btn small" aria-expanded="false" data-preview="'
                    + esc(link.url) + '">Preview</button>';
            }
        }
        // Revoke stays available for any not-yet-revoked link, even one
        // that's already expired or policy-blocked -- there's no reason to
        // withhold it just because the link is already inactive some other way.
        if (status !== 'revoked') {
            actions += '<button type="button" class="rsw-btn small danger" data-revoke="' + link.id + '">Revoke</button>';
        }

        return '<div class="rsw-link-row' + (status === 'active' ? '' : ' rsw-inactive-row') + '" data-id="' + link.id + '">'
            + '<div class="rsw-link-meta">' + meta
            + (link.embed ? '<span class="rsw-chip embed">Embed</span> ' : '')
            + '<span class="rsw-chip ' + status + '">' + STATUS_CHIP_LABEL[status] + '</span></div>'
            + '<code class="rsw-link-url">' + esc(link.url) + '</code>'
            + '<div class="rsw-link-actions">' + actions + '</div>'
            + (link.embed ? '<div class="rsw-preview" hidden></div>' : '')
            + '</div>';
    }

    // One-time reveal after creating a link WITH a password: the server
    // never returns the plaintext (it only ever stores the hash), so this
    // is the form's own value, shown exactly once and never persisted or
    // re-fetchable -- refreshing or reopening the panel cannot bring it
    // back, which is the whole point of the warning it carries.
    function renderPasswordReveal(link, password) {
        var body = document.getElementById('rswBody');
        body.innerHTML = '<div class="rsw-reveal">'
            + '<p class="rsw-reveal-title">Link created</p>'
            + '<div class="rsw-reveal-row"><label class="rsw-label">URL</label>'
            + '<code class="rsw-link-url">' + esc(link.url) + '</code>'
            + '<button type="button" class="rsw-btn small" data-reveal-copy="' + esc(link.url) + '">Copy link</button></div>'
            + '<div class="rsw-reveal-row"><label class="rsw-label">Password</label>'
            + '<code class="rsw-link-url">' + esc(password) + '</code>'
            + '<button type="button" class="rsw-btn small" data-reveal-copy="' + esc(password) + '">Copy password</button></div>'
            + '<p class="rsw-reveal-warn">Copy it now — it can’t be shown again. It’s stored hashed, so '
            + 'nobody, including you, can retrieve it later.</p>'
            + '<button type="button" class="rsw-btn primary" id="rswRevealDone" style="margin-top:6px">Done</button>'
            + '</div>';

        var copyBtns = body.querySelectorAll('[data-reveal-copy]');
        for (var i = 0; i < copyBtns.length; i++) {
            copyBtns[i].addEventListener('click', function () {
                copyToClipboard(this.getAttribute('data-reveal-copy'), 'Copied.');
            });
        }
        var doneBtn = document.getElementById('rswRevealDone');
        doneBtn.addEventListener('click', function () {
            render();
            flash('Share link created.', 'success');
        });
        doneBtn.focus();
    }

    function render() {
        var body = document.getElementById('rswBody');
        var links = data.share_links || [];
        var orgDisabled = data.sharing_enabled === false;
        var requirePassword = !!data.require_password;
        var maxExpiryDays = data.max_expiry_days || null;

        var active = [];
        var inactive = [];
        links.forEach(function (link) {
            var status = linkStatus(link, orgDisabled);
            (status === 'active' ? active : inactive).push({ link: link, status: status });
        });

        var html = '';

        if (orgDisabled) {
            html += '<div class="rsw-section"><p class="rsw-note">'
                + 'Public share links are disabled for this organization.'
                + (data.can_manage_policy
                    ? ' <a href="/orgs/' + esc(state.orgSlug) + '/settings/sharing">Enable them in organization settings.</a>'
                    : '')
                + '</p></div>';
        }

        // New link first: this is what the panel gets opened to do, and it
        // has to be visible without scrolling past a long link list.
        if (!orgDisabled) {
            var expiryLabel = 'Expiry' + (maxExpiryDays
                ? ' (required, at most ' + maxExpiryDays + ' day' + (maxExpiryDays === 1 ? '' : 's') + ')'
                : ' (optional)');
            var passwordLabel = 'Password' + (requirePassword ? ' (required by org policy)' : ' (optional, min 12 characters)');
            var defaultExpiry = defaultExpiryValue(maxExpiryDays);
            var embedOk = !!data.embed_links_enabled;

            html += '<div class="rsw-section">'
                + '<h3 class="rsw-section-title">New link</h3>'
                + '<form id="rswNewForm">'
                + '<div class="rsw-field"><label class="rsw-label" for="rswExpires">' + esc(expiryLabel) + '</label>'
                + '<input class="rsw-input" type="datetime-local" id="rswExpires" name="expires_at" value="' + esc(defaultExpiry) + '"'
                + (maxExpiryDays ? ' max="' + datetimeLocalDaysFromNow(maxExpiryDays) + '" required' : '') + '></div>'
                + '<div class="rsw-field"><label class="rsw-label" for="rswPassword">' + esc(passwordLabel) + '</label>'
                + '<div style="display:flex;gap:6px">'
                + '<input class="rsw-input" type="password" id="rswPassword" name="password" autocomplete="new-password" '
                + 'minlength="12" style="flex:1"' + (requirePassword ? ' required' : '') + '>'
                + '<button type="button" class="rsw-btn small" id="rswGeneratePassword">Generate</button>'
                + '</div>'
                + '<span class="rsw-help">At least 12 characters. "Generate" fills in a random one you can copy.</span></div>'
                + '<label class="rsw-check-row"><input type="checkbox" id="rswAllowExport" name="allow_export">'
                + '<span class="rsw-check-label">Allow export</span>'
                + '<span class="rsw-help">Hides export buttons and blocks spreadsheet downloads. '
                + 'The underlying data stays fetchable by anyone with the link — treat shared reports as visible.</span></label>'
                + '<label class="rsw-check-row"><input type="checkbox" id="rswEmbed" name="embed"' + (embedOk ? '' : ' disabled') + '>'
                + '<span class="rsw-check-label">Embed in another site</span>'
                + '<span class="rsw-help">' + (embedOk
                    ? 'Never expires, no password; only the sites listed below may frame it.'
                    : 'Not allowed by this organization\'s sharing policy.') + '</span></label>'
                + '<div class="rsw-field" id="rswEmbedOriginsField" hidden>'
                + '<label class="rsw-label" for="rswEmbedOrigins">Allowed sites</label>'
                + '<textarea class="rsw-input" id="rswEmbedOrigins" rows="3" placeholder="https://app.example.com"></textarea>'
                + '<span class="rsw-help">One origin per line — scheme://host[:port]. * allows any site.</span></div>'
                + '<div class="rsw-field" id="rswEmbedAppearanceField" hidden>'
                + '<label class="rsw-label" for="rswEmbedTheme">Theme</label>'
                + '<select class="rsw-input" id="rswEmbedTheme">' + themeOptionsHtml() + '</select>'
                + '<span class="rsw-help">The embedded report renders in this theme whatever the '
                + 'visitor\'s browser last stored. Studio default follows the studio.</span>'
                + '<label class="rsw-check-row" style="margin-top:6px">'
                + '<input type="checkbox" id="rswEmbedHideFilters">'
                + '<span class="rsw-check-label">Hide the filter bar</span>'
                + '<span class="rsw-help">A static view — visitors on the host page see the report '
                + 'as built and can\'t change the data it shows.</span></label></div>'
                + '<div id="rswFormErrors"></div>'
                + '<button type="submit" class="rsw-btn primary" style="margin-top:8px">Create link</button>'
                + '</form>'
                + '</div>';
        }

        html += '<div class="rsw-section"><h3 class="rsw-section-title">Active links</h3><div id="rswActiveList">';
        if (!active.length) {
            html += '<div class="rsw-empty">No active share links.</div>';
        } else {
            active.forEach(function (row) { html += linkRowHtml(row.link, row.status); });
        }
        html += '</div>';

        // Revoked / expired / policy-blocked links are collapsed behind a
        // toggle rather than interleaved with the active ones -- they were
        // most of the complaint this responds to (an active row and a dead
        // one looked nearly identical); tucking them away by default keeps
        // the panel's default view to "what's live right now".
        if (inactive.length) {
            html += '<button type="button" class="rsw-btn rsw-toggle-btn" id="rswToggleInactive" aria-expanded="false">'
                + 'Show inactive links (' + inactive.length + ')</button>'
                + '<div class="rsw-inactive-list" id="rswInactiveList" hidden>';
            inactive.forEach(function (row) { html += linkRowHtml(row.link, row.status); });
            html += '</div>';
        }
        html += '</div>';

        body.innerHTML = html;
        wireControls();
    }

    // Only one preview open at a time, and closing one EMPTIES it rather
    // than hiding it: dropping the <iframe> stops the report inside it (it
    // polls for a new build every minute) and means a reopen re-fetches
    // instead of the drawer holding several hundred kilobytes per row.
    function closePreviews() {
        var boxes = document.querySelectorAll('.rsw-preview');
        for (var i = 0; i < boxes.length; i++) {
            boxes[i].innerHTML = '';
            boxes[i].hidden = true;
        }
        var btns = document.querySelectorAll('[data-preview]');
        for (var j = 0; j < btns.length; j++) {
            btns[j].textContent = 'Preview';
            btns[j].setAttribute('aria-expanded', 'false');
        }
    }

    function wireControls() {
        function wireLinkList(listEl) {
            if (!listEl) return;
            listEl.addEventListener('click', function (e) {
                var copyBtn = e.target.closest('[data-copy]');
                if (copyBtn) {
                    copyToClipboard(copyBtn.getAttribute('data-copy'), 'Link copied.');
                    return;
                }
                var embedBtn = e.target.closest('[data-embed-copy]');
                if (embedBtn) {
                    copyToClipboard(
                        embedSnippet(embedBtn.getAttribute('data-embed-copy'), state.name || state.slug),
                        'Embed code copied.');
                    return;
                }
                // The frame loads on the first click, never on render: it is
                // the link's own URL, so what it shows is exactly what a host
                // page gets -- including its theme and hidden filter bar.
                // Same-origin (the drawer lives on a portal page), which the
                // embed response's frame-ancestors 'self' allows.
                var previewBtn = e.target.closest('[data-preview]');
                if (previewBtn) {
                    var box = previewBtn.closest('.rsw-link-row').querySelector('.rsw-preview');
                    var opening = box.hidden;
                    closePreviews();
                    if (opening) {
                        box.innerHTML = '<iframe src="' + esc(previewBtn.getAttribute('data-preview'))
                            + '" title="Embed preview"></iframe>';
                        box.hidden = false;
                        previewBtn.textContent = 'Hide preview';
                        previewBtn.setAttribute('aria-expanded', 'true');
                    }
                    return;
                }
                var revokeBtn = e.target.closest('[data-revoke]');
                if (revokeBtn) {
                    if (!window.confirm('Revoke this share link? Anyone using it will lose access immediately.')) return;
                    revokeBtn.disabled = true;
                    // Global endpoint (apps.reports.views.api_share_link_revoke)
                    // -- not studio-prefixed like apiBase(), because a revoke
                    // only ever needs the link's own id.
                    postJson('/api/share_links/' + revokeBtn.getAttribute('data-revoke') + '/revoke', 'POST', {})
                        .then(function (res) {
                            if (!res.ok) {
                                revokeBtn.disabled = false;
                                flash(res.data.error || 'Could not revoke the link.', 'error');
                                return;
                            }
                            data.share_links = data.share_links.map(function (l) {
                                return l.id === res.data.share_link.id ? res.data.share_link : l;
                            });
                            render();
                            flash('Link revoked.', 'success');
                        }).catch(function () {
                            revokeBtn.disabled = false;
                            flash('Could not revoke the link — check your connection and try again.', 'error');
                        });
                }
            });
        }

        wireLinkList(document.getElementById('rswActiveList'));
        wireLinkList(document.getElementById('rswInactiveList'));

        var toggleBtn = document.getElementById('rswToggleInactive');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', function () {
                var list = document.getElementById('rswInactiveList');
                var wasHidden = list.hidden;
                list.hidden = !wasHidden;
                toggleBtn.setAttribute('aria-expanded', String(wasHidden));
                var count = list.querySelectorAll('.rsw-link-row').length;
                toggleBtn.textContent = (wasHidden ? 'Hide' : 'Show') + ' inactive links (' + count + ')';
            });
        }

        var genBtn = document.getElementById('rswGeneratePassword');
        if (genBtn) {
            genBtn.addEventListener('click', function () {
                var input = document.getElementById('rswPassword');
                input.type = 'text';
                input.value = generatePassword();
                input.focus();
                input.select();
            });
        }

        var form = document.getElementById('rswNewForm');
        if (!form) return;  // sharing disabled for this org -- no create form rendered

        // Embed links take no expiry or password: checking the box hides
        // those inputs' values (disabled + cleared) and shows the origin list
        // plus the appearance choices, which mean nothing on a plain link.
        var embedCb = document.getElementById('rswEmbed');
        embedCb.addEventListener('change', function () {
            var on = embedCb.checked;
            document.getElementById('rswEmbedOriginsField').hidden = !on;
            document.getElementById('rswEmbedAppearanceField').hidden = !on;
            ['rswExpires', 'rswPassword'].forEach(function (id) {
                var el = document.getElementById(id);
                el.disabled = on;
                el.value = on ? '' : el.defaultValue;
            });
            document.getElementById('rswGeneratePassword').disabled = on;
        });

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var errBox = document.getElementById('rswFormErrors');
            var embed = embedCb.checked;
            var expiresLocal = document.getElementById('rswExpires').value;
            var passwordValue = document.getElementById('rswPassword').value;
            var requirePassword = !embed && !!data.require_password;
            var maxExpiryDays = embed ? null : (data.max_expiry_days || null);
            var origins = embed
                ? document.getElementById('rswEmbedOrigins').value.split('\n')
                    .map(function (s) { return s.trim(); }).filter(Boolean)
                : [];
            if (embed && !origins.length) {
                errBox.innerHTML = '<div class="rsw-field-error">List at least one site, or * for any site.</div>';
                return;
            }

            // Mirrors the server's own checks (apps.reports.views
            // .api_share_links) so a policy violation shows up before the
            // round trip rather than after it -- the server still enforces
            // every one of these, this is purely a faster no.
            if (passwordValue && passwordValue.length < 12) {
                errBox.innerHTML = '<div class="rsw-field-error">Use at least 12 characters.</div>';
                return;
            }
            if (requirePassword && !passwordValue) {
                errBox.innerHTML = '<div class="rsw-field-error">This organization requires a password on every share link.</div>';
                return;
            }
            if (maxExpiryDays && !expiresLocal) {
                errBox.innerHTML = '<div class="rsw-field-error">This organization requires an expiry, at most '
                    + maxExpiryDays + ' day' + (maxExpiryDays === 1 ? '' : 's') + ' out.</div>';
                return;
            }
            if (maxExpiryDays && expiresLocal && new Date(expiresLocal) > new Date(Date.now() + maxExpiryDays * 86400000)) {
                errBox.innerHTML = '<div class="rsw-field-error">Expiry can be at most '
                    + maxExpiryDays + ' day' + (maxExpiryDays === 1 ? '' : 's') + ' from now.</div>';
                return;
            }
            errBox.innerHTML = '';

            var body = { allow_export: document.getElementById('rswAllowExport').checked };
            if (embed) {
                body.embed = true;
                body.embed_origins = origins;
                body.embed_theme = document.getElementById('rswEmbedTheme').value;
                body.embed_hide_filters = document.getElementById('rswEmbedHideFilters').checked;
            } else {
                body.password = passwordValue;
            }
            if (!embed && expiresLocal) {
                // datetime-local has no timezone; treat it as the browser's
                // own local time, same convention the schedule form uses.
                body.expires_at = new Date(expiresLocal).toISOString();
            }
            var submitBtn = form.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            postJson(apiBase(), 'POST', body).then(function (res) {
                submitBtn.disabled = false;
                if (!res.ok) {
                    var msg = (res.data && (res.data.message || res.data.error)) || 'Could not create the link.';
                    errBox.innerHTML = '<div class="rsw-field-error">' + esc(msg) + '</div>';
                    return;
                }
                data.share_links = data.share_links || [];
                data.share_links.unshift(res.data.share_link);
                // The plaintext lives only in `passwordValue`, read from the
                // form itself -- the server response never carries it back
                // (see apps.reports.views._share_link_dict, which returns
                // only has_password). Shown once, here, and nowhere else.
                if (passwordValue) {
                    renderPasswordReveal(res.data.share_link, passwordValue);
                } else {
                    render();
                    flash(embed ? 'Embed link created.' : 'Share link created.', 'success');
                }
            }).catch(function () {
                submitBtn.disabled = false;
                flash('Could not create the link — check your connection and try again.', 'error');
            });
        });
    }

    window.ReportShare = { open: open, close: close };

    // ── report-page auto-mount ──────────────────────────────────────────
    // Only runs on a built report page (never the dashboard, which has its
    // own management UI elsewhere) — see static/report_delivery.js's
    // identical guard for why window.PORTAL_CTX is the signal. Registers a
    // "Share" item on the shared Options menu (static/report_menu.js)
    // rather than mounting its own header button.
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
            apiFetch(prefix + '/api/reports/' + encodeURIComponent(slug) + '/share_links')
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d) return;
                    _registered = true;

                    window.__reportMenu.register({
                        id: 'share', order: 40, label: 'Share',
                        icon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="15" cy="5" r="2.2"/><circle cx="5" cy="10" r="2.2"/><circle cx="15" cy="15" r="2.2"/><path d="M6.9 8.9l6.2-2.9M6.9 11.1l6.2 2.9"/></svg>',
                        onSelect: function () {
                            var h1 = document.querySelector('.fw-header h1');
                            var name = h1 ? h1.textContent : document.title;
                            // A report page only ever has one share context,
                            // so the payload from the mount-time gating
                            // fetch above stays valid for the drawer's whole
                            // lifetime -- but only on the FIRST open (see
                            // open()'s own `seed` param docstring).
                            open(prefix, slug, name, document.getElementById('fwOptionsBtn'), data ? null : d);
                        }
                    });
                }).catch(function () {});
        };
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
        else mount();
    }
    autoMount();
})();
