/* ── AI assistant widget ───────────────────────────────────────────────────
 * Self-initializing: injects an "Ask" button into the page header (report
 * pages: .fw-header-right, in front of the Options menu; dashboard: the
 * .shell-tools or .tl-console-header cluster beside Search) plus a chat panel. The panel docks to
 * the right edge at >= 1024px (pushing the page over so charts reflow),
 * floats as an overlay between 640 and 1024, and becomes a bottom sheet on
 * phones, where a full-width pill replaces the header button. Hides itself
 * when the assistant is unavailable.
 *
 * Auth is the portal session cookie — every request goes out with
 * credentials:'same-origin' plus the CSRF token when it mutates state.
 * The widget is studio-scoped: it derives its API base from the page it is
 * running on (/s/<org>/<studio>/…) and stays hidden anywhere else.
 */
(function () {
    'use strict';

    // Guard against double-injection (portal inlines the widget; report
    // pages load it via /api/assistant/widget.js).
    if (window.__assistantWidgetLoaded) return;
    window.__assistantWidgetLoaded = true;

    // ── Studio context ────────────────────────────────────────────────
    // Everything the assistant can read is scoped to one studio, so the
    // widget only exists on studio pages: the dashboard, its settings and
    // every built report page under /s/<org>/<studio>/r/<slug>/.
    var _ctx = /^\/s\/([a-z0-9-]+)\/([a-z0-9-]+)(?:\/|$)/.exec(window.location.pathname);
    var API_BASE = _ctx ? ('/s/' + _ctx[1] + '/' + _ctx[2] + '/api/assistant') : null;
    var IS_REPORT = /^\/s\/[a-z0-9-]+\/[a-z0-9-]+\/r\//.test(window.location.pathname);
    var HOSTED_BY_CONSOLE = false;
    try {
        HOSTED_BY_CONSOLE = window.parent !== window
            && !!window.parent.TrellumConsoleReportHost
            && !!window.parent.TrellumConsoleReportHost.contextFor(window);
    } catch (e) {}

    function getCookie(name) {
        var match = ('; ' + document.cookie).split('; ' + name + '=');
        if (match.length !== 2) return null;
        return decodeURIComponent(match.pop().split(';').shift());
    }

    function _fetch(url, options) {
        options = options || {};
        options.credentials = 'same-origin';
        options.headers = options.headers || {};
        var method = (options.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD') {
            var token = getCookie('csrftoken');
            if (token) options.headers['X-CSRFToken'] = token;
        }
        return fetch(url, options);
    }

    var sessionId = null;
    var busy = false;
    var available = false;
    var _deadline = null;  // seconds a reply may take, from the probe
    var _chart = null;     // section the next question is about (report pages)
    var _hostedDocument = null;
    var _directReport = /\/r\/([A-Za-z0-9_-]+)\//.exec(window.location.pathname);
    var _contextReport = _directReport ? _directReport[1] : null;
    var sessionScope = null, sessionReport = null;

    // The conversation follows the user across portal + report pages:
    // session id and open/closed state persist in localStorage, and the
    // transcript is re-rendered from the server on every page load.
    var LS_SESSION = 'assistant_session_id';
    var LS_OPEN = 'assistant_open';
    var LS_WIDTH = 'assistant_width';
    var LS_PINS = 'assistant_pins';

    function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
    function lsJson(k) { try { return JSON.parse(lsGet(k)) || {}; } catch (e) { return {}; } }

    // One session pointer per studio, kept as a JSON map under the old key.
    // A bare id left by the previous single-value format is adopted for the
    // studio it is read on.
    var STUDIO_KEY = _ctx ? _ctx[1] + '/' + _ctx[2] : '';
    function sessionMap() {
        var raw = lsGet(LS_SESSION);
        if (!raw) return {};
        try {
            var m = JSON.parse(raw);
            if (m && typeof m === 'object') return m;
        } catch (e) { /* legacy bare id */ }
        var legacy = {};
        legacy[STUDIO_KEY] = raw;
        return legacy;
    }
    function reportKey(slug) { return STUDIO_KEY + '/r/' + slug; }
    function storedSession() {
        var m = sessionMap();
        return (_contextReport && m[reportKey(_contextReport)]) || m[STUDIO_KEY] || null;
    }
    function storeSession(id, scope, report) {
        var m = sessionMap();
        var key = scope === 'report' && report ? reportKey(report) : STUDIO_KEY;
        if (id) m[key] = id; else delete m[key];
        lsSet(LS_SESSION, JSON.stringify(m));
    }

    // A deep link from an alert email (?assistant=<session id>, minted by
    // the from-alert entry): adopt that conversation for this studio and open
    // the panel. Read at load, before url_sync rewrites the query string, and
    // stripped so a refresh does not repeat it -- the ?rdw=1 shape.
    var _deep = _ctx && /(?:^|[?&])assistant=(\d+)(?:&|$)/.exec(window.location.search);
    if (_deep) {
        storeSession(parseInt(_deep[1], 10));
        lsSet(LS_OPEN, '1');
        try {
            var _sp = new URLSearchParams(window.location.search);
            _sp.delete('assistant');
            var _qs = _sp.toString();
            history.replaceState(history.state, '', window.location.pathname + (_qs ? '?' + _qs : '') + window.location.hash);
        } catch (e) {}
    }

    // Layout breakpoints (px), mirrored by the media queries in assistant.css:
    // bottom sheet at <= BP_SHEET, docked panel at >= BP_DOCK, floating
    // overlay in between.
    var BP_SHEET = 640;
    var BP_DOCK = 1024;
    var W_MIN = 360, W_MAX = 560, W_DEFAULT = 420;

    // ── DOM scaffolding ────────────────────────────────────────────────

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text) e.textContent = text;
        return e;
    }

    // The one glyph the widget uses: a four-point spark in currentColor.
    var SPARK = '<svg class="assistant-spark" viewBox="0 0 16 16" width="16" height="16" ' +
        'fill="currentColor" aria-hidden="true"><path d="M8 0c.5 4.2 2.3 6.9 8 8-5.7 1.1-7.5 3.8-8 8' +
        '-.5-4.2-2.3-6.9-8-8 5.7-1.1 7.5-3.8 8-8z"/></svg>';

    var ask = el('button', 'assistant-ask assistant-hidden');
    ask.id = 'assistantAsk';
    ask.type = 'button';
    ask.title = 'Ask the AI assistant (Ctrl+/)';
    ask.setAttribute('aria-label', 'Ask the AI assistant');
    ask.setAttribute('aria-expanded', 'false');
    ask.setAttribute('aria-controls', 'assistantPanel');
    ask.innerHTML = SPARK + '<span>Ask AI</span>';

    // Phone-only launcher: a full-width pill above the safe area.
    var pill = el('button', 'assistant-pill assistant-hidden');
    pill.id = 'assistantPill';
    pill.type = 'button';
    pill.setAttribute('aria-expanded', 'false');
    pill.setAttribute('aria-controls', 'assistantPanel');
    pill.innerHTML = SPARK + '<span>' + (IS_REPORT ? 'Ask about this report' : 'Ask about this studio') + '</span>';

    var panel = el('div');
    panel.id = 'assistantPanel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'AI assistant');
    panel.innerHTML =
        '<div class="assistant-resize" id="assistantResize" title="Drag to resize"></div>' +
        '<div class="assistant-grab"></div>' +
        '<div class="assistant-header" id="assistantHeader">' +
        '  ' + SPARK +
        '  <div><div class="assistant-title">AI Assistant</div>' +
        '  <div class="assistant-sub">Ask about reports, charts &amp; data</div></div>' +
        '  <span style="flex:1"></span>' +
        '  <button id="assistantTall" class="assistant-sheet-only" title="Expand" aria-label="Expand" aria-pressed="false">⤢</button>' +
        '  <button id="assistantHistory" title="Conversation history" aria-label="Conversation history" aria-haspopup="true" aria-expanded="false">History</button>' +
        '  <button id="assistantNew" title="Start a new conversation" aria-label="Start a new conversation">+</button>' +
        '  <button id="assistantClose" title="Close" aria-label="Close the assistant">✕</button>' +
        '</div>' +
        '<div class="assistant-history" id="assistantHistoryPop" role="menu" aria-label="Conversations" hidden></div>' +
        '<details class="assistant-pinned" id="assistantPinned" hidden open>' +
        '  <summary>Pinned</summary><div class="assistant-pinned-body"></div>' +
        '</details>' +
        '<div class="assistant-about" id="assistantAbout" hidden></div>' +
        '<div class="assistant-messages" id="assistantMessages" aria-live="polite"></div>' +
        '<div class="assistant-input-row">' +
        '  <textarea id="assistantInput" rows="1" placeholder="Ask the AI assistant…" aria-label="Your question"></textarea>' +
        '  <button id="assistantSend" aria-label="Send">Send</button>' +
        '</div>';

    // ── Layout: dock / overlay / sheet ─────────────────────────────────

    function mode() {
        var w = window.innerWidth;
        return w <= BP_SHEET ? 'sheet' : (w >= BP_DOCK ? 'dock' : 'overlay');
    }
    function isOpen() { return panel.classList.contains('assistant-open'); }
    function clampW(w) { return Math.max(W_MIN, Math.min(W_MAX, w)); }
    var panelW = clampW(parseInt(lsGet(LS_WIDTH), 10) || W_DEFAULT);

    var _notifiedPad = '';
    // notify=true dispatches a window resize when the page's reserved width
    // changed so charts (Chart.js responsive) reflow into the new canvas.
    // Skipped during a drag; the pointerup applies it once.
    function applyLayout(notify) {
        var m = mode(), open = isOpen();
        panel.classList.toggle('assistant-dock', m === 'dock');
        panel.classList.toggle('assistant-sheet', m === 'sheet');
        panel.style.width = m === 'sheet' ? '' : panelW + 'px';
        var pad = (open && m === 'dock') ? panelW + 'px' : '';
        document.body.style.paddingRight = pad;
        document.body.style.setProperty('--assistant-w', pad || '0px');
        // Body scroll is locked while the sheet is up (CSS on this class).
        document.body.classList.toggle('assistant-sheet-open', open && m === 'sheet');
        pill.classList.toggle('assistant-hidden', !available || open);
        if (notify && pad !== _notifiedPad) {
            _notifiedPad = pad;
            window.dispatchEvent(new Event('resize'));
        }
    }

    var _opener = null;
    function setExpanded(open) {
        ask.setAttribute('aria-expanded', open ? 'true' : 'false');
        pill.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function openPanel(opener) {
        if (!available || isOpen()) return;
        _opener = opener || document.activeElement;
        panel.classList.add('assistant-open');
        lsSet(LS_OPEN, '1');
        // Phone back button closes the sheet: push one history entry while it
        // is up. ponytail: url_sync.js's replaceState(null) can wipe this
        // state when filters change mid-chat, after which back is a no-op.
        if (mode() === 'sheet' && !(history.state && history.state.assistantSheet)) {
            try {
                history.pushState(Object.assign({}, history.state || {}, {
                    assistantSheet: true
                }), '');
            } catch (e) {}
        }
        applyLayout(true);
        setExpanded(true);
        input().focus();
    }

    // getClientRects, not offsetParent: the latter is null for the fixed pill
    // even when it is on screen.
    function visible(e) { return !!(e && e.isConnected && e.getClientRects().length); }

    function closePanel(fromPop) {
        if (!isOpen()) return;
        panel.classList.remove('assistant-open', 'assistant-tall');
        lsSet(LS_OPEN, '0');
        applyLayout(true);
        setExpanded(false);
        if (!fromPop && history.state && history.state.assistantSheet) {
            try { history.back(); } catch (e) {}
        }
        // Focus returns to whatever opened the panel; the header button is
        // hidden on phones, so fall back to the pill there.
        var back = [_opener, ask, pill].filter(visible)[0];
        if (back) back.focus();
    }

    function togglePanel(opener) {
        if (isOpen()) closePanel(); else openPanel(opener);
    }

    // Report pages: in front of the Options menu. report_menu.js mounts
    // Options as firstChild whenever a sibling widget registers — possibly
    // after us — so keep watching and stay in front of it.
    function placeAsk() {
        var right = document.querySelector('.fw-header-right');
        var tools = document.querySelector('.shell-tools, .tl-console-header');
        if (right) {
            ask.classList.add('assistant-ask-fw');
            var put = function () {
                var opt = byId('fwOptionsWrap');
                if (opt) {
                    if (ask.parentNode !== right || ask.nextSibling !== opt) right.insertBefore(ask, opt);
                } else if (ask.parentNode !== right) {
                    right.insertBefore(ask, right.firstChild);
                }
            };
            put();
            new MutationObserver(put).observe(right, { childList: true });
        } else if (tools) {
            ask.classList.add('assistant-ask-shell');
            var account = tools.querySelector('.tl-console-account');
            tools.insertBefore(ask, account || null);
        } else {
            // ponytail: page without a header cluster — pin it top-right.
            ask.classList.add('assistant-ask-float');
            document.body.appendChild(ask);
        }
    }

    function mount() {
        placeAsk();
        document.body.appendChild(pill);
        document.body.appendChild(panel);

        window.addEventListener('trellum:assistant-context', function (ev) {
            var detail = ev.detail || {};
            var changed = (detail.report || null) !== _contextReport;
            var label = pill.querySelector('span');
            if (label) label.textContent = detail.report
                ? 'Ask about this report' : 'Ask about this studio';
            if (changed) {
                _chart = null;
                if (sessionScope === 'report' && sessionReport !== (detail.report || null)) {
                    resetSession('This report uses a separate conversation. What would you like to know?');
                }
            }
            _contextReport = detail.report || null;
            _hostedDocument = detail.document || null;
            if (available && _hostedDocument) mountSectionAsks(_hostedDocument);
            if (changed) refreshAvailability();
        });

        ask.addEventListener('click', function () { togglePanel(ask); });
        pill.addEventListener('click', function () { openPanel(pill); });
        byId('assistantClose').addEventListener('click', function () { closePanel(); });
        byId('assistantTall').addEventListener('click', function () {
            var tall = panel.classList.toggle('assistant-tall');
            this.title = tall ? 'Shrink' : 'Expand';
            this.setAttribute('aria-label', this.title);
            this.setAttribute('aria-pressed', tall ? 'true' : 'false');
        });
        byId('assistantNew').addEventListener('click', function () { toggleHistory(false); newSession(); });
        byId('assistantHistory').addEventListener('click', function () { toggleHistory(); });
        panel.addEventListener('click', function (ev) {
            if (!historyPop().hidden && !historyPop().contains(ev.target) && !byId('assistantHistory').contains(ev.target)) toggleHistory(false);
        });
        byId('assistantSend').addEventListener('click', send);
        input().addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter' && !ev.shiftKey) {
                ev.preventDefault();
                send();
            }
        });
        input().addEventListener('input', autosize);

        document.addEventListener('keydown', function (ev) {
            if (ev.key === '/' && (ev.ctrlKey || ev.metaKey) && !ev.altKey) {
                ev.preventDefault();
                togglePanel(ask);
            } else if (ev.key === 'Escape' && isOpen()) {
                ev.preventDefault();
                if (!historyPop().hidden) toggleHistory(false); else closePanel();
            }
        });
        window.addEventListener('resize', function () { applyLayout(true); });
        window.addEventListener('popstate', function () {
            if (!(history.state && history.state.assistantSheet)) closePanel(true);
        });

        // Left-edge drag handle: 360–560px, remembered across pages.
        var handle = byId('assistantResize');
        handle.addEventListener('pointerdown', function (ev) {
            if (mode() === 'sheet') return;
            ev.preventDefault();
            handle.setPointerCapture(ev.pointerId);
            panel.classList.add('assistant-resizing');
            var right = panel.getBoundingClientRect().right;
            function move(e) { panelW = clampW(right - e.clientX); applyLayout(false); }
            function up() {
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', up);
                panel.classList.remove('assistant-resizing');
                lsSet(LS_WIDTH, String(panelW));
                applyLayout(true);
            }
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });

        // Sheet: swipe down on the header closes.
        var hdr = byId('assistantHeader'), _ty = null;
        hdr.addEventListener('touchstart', function (e) { _ty = e.touches[0].clientY; }, { passive: true });
        hdr.addEventListener('touchend', function (e) {
            if (_ty !== null && mode() === 'sheet' && e.changedTouches[0].clientY - _ty > 60) closePanel();
            _ty = null;
        });
    }

    function byId(id) { return document.getElementById(id); }
    function messages() { return byId('assistantMessages'); }
    function input() { return byId('assistantInput'); }

    function autosize() {
        var t = input();
        t.style.height = 'auto';
        t.style.height = Math.min(t.scrollHeight, 110) + 'px';
        // Scrollbar only when the content genuinely exceeds the max height.
        t.style.overflowY = t.scrollHeight > 110 ? 'auto' : 'hidden';
    }

    // Empty state: intro line plus the server's page-aware suggestions as
    // chips (click = send); the two generic hints when it sent none.
    var _suggestions = [];
    var _suggestionSerial = 0;
    function pageQuery() {
        return '?path=' + encodeURIComponent(contextPath())
            + '&title=' + encodeURIComponent((_hostedDocument && _hostedDocument.title) || document.title || '');
    }
    function contextPath() {
        var hostedPath = _hostedDocument && _hostedDocument.location
            && _hostedDocument.location.pathname;
        return hostedPath || window.location.pathname;
    }
    function refreshSuggestions() {
        var serial = ++_suggestionSerial;
        _fetch(API_BASE + '/available' + pageQuery())
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) {
                if (serial !== _suggestionSerial || !j || !j.available) return;
                _suggestions = Array.isArray(j.suggestions) ? j.suggestions : [];
                var hint = messages().querySelector('.assistant-empty');
                if (hint) { hint.remove(); appendEmptyHint(); }
            })
            .catch(function () {});
    }
    function refreshAvailability() {
        var serial = ++_suggestionSerial;
        _fetch(API_BASE + '/available' + pageQuery())
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) {
                if (serial !== _suggestionSerial) return;
                if (!j || !j.available) {
                    available = false;
                    ask.classList.add('assistant-hidden');
                    closePanel();
                    return;
                }
                available = true;
                _suggestions = Array.isArray(j.suggestions) ? j.suggestions : [];
                if (typeof j.deadline_s === 'number') _deadline = j.deadline_s;
                ask.classList.remove('assistant-hidden');
                if (_hostedDocument) mountSectionAsks(_hostedDocument);
                var hint = messages().querySelector('.assistant-empty');
                if (hint) { hint.remove(); appendEmptyHint(); }
                if (!sessionId) restoreSession();
                applyLayout(true);
            })
            .catch(function () {});
    }
    function appendEmptyHint(intro) {
        var hint = el('div', 'assistant-empty');
        hint.innerHTML = intro || ('Hi! I can help you find the right report, ' +
            'explain what a chart shows, or pull numbers from the data.');
        if (_suggestions.length) {
            var chips = el('div', 'assistant-chips');
            _suggestions.slice(0, 3).forEach(function (q) {
                var c = el('button', 'assistant-chip', q);
                c.type = 'button';
                c.addEventListener('click', function () { input().value = q; send(); });
                chips.appendChild(c);
            });
            hint.appendChild(chips);
        } else {
            hint.insertAdjacentHTML('beforeend', '<br><br>Try: <i>“which report shows payer churn?”</i> or ' +
                '<i>“how was revenue last week?”</i>');
        }
        messages().appendChild(hint);
    }

    function scrollDown() {
        var m = messages();
        m.scrollTop = m.scrollHeight;
    }

    // ── Minimal markdown renderer (links, tables, code, bold/italic) ──

    function escapeHtml(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function attr(s) { return escapeHtml(String(s)).replace(/"/g, '&quot;'); }

    // Only links back into this portal become anchors; anything pointing at
    // another host stays plain text.
    function sameOrigin(href) {
        try { return new URL(href, window.location.href).origin === window.location.origin; }
        catch (e) { return false; }
    }

    function inlineMd(s) {
        s = escapeHtml(s);
        s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        // Markdown links — internal report links open in a new tab.
        s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, label, href) {
            if (!/^(https?:)?\//.test(href) || !sameOrigin(href)) return label;
            return '<a href="' + attr(href) + '" target="_blank" rel="noopener">' + label + '</a>';
        });
        return s;
    }

    function renderMarkdown(md) {
        var lines = md.split('\n');
        var html = [];
        var i = 0;
        while (i < lines.length) {
            var line = lines[i];
            if (/^```/.test(line)) {
                var code = [];
                i++;
                while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
                i++;
                html.push('<pre>' + escapeHtml(code.join('\n')) + '</pre>');
                continue;
            }
            if (/^\s*\|/.test(line)) {
                var rows = [];
                while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
                html.push(renderTable(rows));
                continue;
            }
            if (/^#{1,4}\s/.test(line)) {
                html.push('<p><strong>' + inlineMd(line.replace(/^#{1,4}\s/, '')) + '</strong></p>');
                i++;
                continue;
            }
            if (/^\s*[-*]\s/.test(line)) {
                var items = [];
                while (i < lines.length && /^\s*[-*]\s/.test(lines[i])) {
                    items.push('<li>' + inlineMd(lines[i].replace(/^\s*[-*]\s/, '')) + '</li>');
                    i++;
                }
                html.push('<ul style="margin:4px 0 8px 18px;padding:0">' + items.join('') + '</ul>');
                continue;
            }
            var para = [];
            while (i < lines.length && lines[i].trim() !== '' &&
                   !/^(```|\s*\||#{1,4}\s|\s*[-*]\s)/.test(lines[i])) {
                para.push(lines[i++]);
            }
            if (para.length) html.push('<p>' + inlineMd(para.join(' ')) + '</p>');
            while (i < lines.length && lines[i].trim() === '') i++;
        }
        return html.join('');
    }

    // "1,234", "€1.24M", "+4.2%", "−0.8pt", "38 410" — a cell that is one
    // number with optional currency/sign/unit dressing.
    function isNumeric(c) {
        return /^[-+−]?[€$£¥]?\s?\d[\d.,\s]*\s?(%|[kKmMbB]|pts?|x)?$/.test(c.trim());
    }

    function renderTable(rows) {
        var cells = rows.map(function (r) {
            return r.replace(/^\s*\||\|\s*$/g, '').split('|').map(function (c) { return c.trim(); });
        });
        var body = cells.filter(function (r) {
            return !r.every(function (c) { return /^:?-{2,}:?$/.test(c); });
        });
        if (!body.length) return '';
        var out = '<table><thead><tr>';
        out += body[0].map(function (c) { return '<th>' + inlineMd(c) + '</th>'; }).join('');
        out += '</tr></thead><tbody>';
        for (var r = 1; r < body.length; r++) {
            out += '<tr>' + body[r].map(function (c) {
                return (isNumeric(c) ? '<td class="num">' : '<td>') + inlineMd(c) + '</td>';
            }).join('') + '</tr>';
        }
        return out + '</tbody></table>';
    }

    // ── Message rendering ──────────────────────────────────────────────

    var _toolLabels = {
        list_reports: 'Browsing the report catalog',
        get_report_details: 'Inspecting a report',
        query_report_data: 'Reading report data',
        read_doc: 'Reading documentation',
        configure_data_source: 'Proposing a data source change',
        test_data_source: 'Proposing a connection test',
        run_report: 'Proposing a build',
        publish_repo_changes: 'Proposing a publish',
        create_alert: 'Proposing an alert',
        update_alert: 'Proposing an alert change'
    };

    function clearEmptyHint() {
        var hints = messages().querySelectorAll('.assistant-empty');
        hints.forEach(function (h) { h.remove(); });
    }

    function appendUser(text) {
        clearEmptyHint();
        var d = el('div', 'assistant-msg user', text);
        messages().appendChild(d);
        scrollDown();
        return d;
    }

    function appendAssistant(mdText) {
        var d = el('div', 'assistant-msg assistant');
        d.innerHTML = renderMarkdown(mdText);
        messages().appendChild(d);
        decorate(d, mdText);
        scrollDown();
        return d;
    }

    // The server forwards the model's text in chunks as it is produced. They
    // belong to ONE reply, so they accumulate into one bubble that re-renders
    // as it grows -- appending a bubble per chunk would shred a sentence into
    // dozens of blocks. closeAssistant() ends the bubble at every boundary
    // that starts new prose: a tool call, the end of the turn, an error.
    var _live = null, _liveMd = '';

    function appendAssistantDelta(text) {
        if (!_live) {
            _live = el('div', 'assistant-msg assistant');
            _liveMd = '';
            messages().appendChild(_live);
        }
        _liveMd += text;
        // ponytail: re-renders the whole message per chunk. Fine at chat
        // length; batch into an animation frame if a very long reply ever
        // feels sluggish while streaming.
        _live.innerHTML = renderMarkdown(_liveMd);
        scrollDown();
    }

    function closeAssistant() {
        if (_live && _liveMd) decorate(_live, _liveMd);
        _live = null;
        _liveMd = '';
    }

    function appendTool(name, id) {
        var d = el('div', 'assistant-tool');
        d.innerHTML = SPARK + '<span></span>';
        d.lastChild.textContent = (_toolLabels[name] || name) + '…';
        d.dataset.tool = name;
        if (id) d.dataset.id = id;
        messages().appendChild(d);
        scrollDown();
        return d;
    }

    // The chip a tool_result belongs to: the latest with that id, else the
    // latest with that name. Latest, because some providers reuse call ids
    // across turns and the result always belongs to the turn in progress.
    function toolChip(ev) {
        var m = messages();
        var all = ev.id ? m.querySelectorAll('.assistant-tool[data-id="' + String(ev.id).replace(/"/g, '') + '"]') : [];
        if (!all.length) all = m.querySelectorAll('.assistant-tool[data-tool="' + ev.name + '"]');
        return all[all.length - 1] || null;
    }

    // Server error codes → what the person can act on.
    function errorText(code, fallback) {
        if (code === 'deadline') {
            return (_deadline ? 'Stopped after ' + _deadline + ' seconds' : 'Stopped at the time limit') +
                ' — try a narrower question.';
        }
        if (code === 'busy') return 'A reply is still streaming in another tab.';
        return fallback || 'Something went wrong.';
    }

    function httpError(status, j) {
        var code = (j && j.code) || (status === 409 ? 'busy' : null);
        var e = new Error(errorText(code, (j && j.error) || ('request failed (' + status + ')')));
        e.code = code;
        return e;
    }

    // A failed turn hands the question back: composer refilled, a banner
    // with Retry, and the orphaned bubble removed if nothing was answered.
    function failTurn(text, userEl, message, detail) {
        if (!_gotText && userEl) userEl.remove();
        input().value = text;
        autosize();
        var d = el('div', 'assistant-error-banner');
        d.appendChild(el('span', null, message));
        if (detail) {
            // Org admins only (the server strips it for everyone else): the
            // technical line plus the way to the settings that caused it.
            var dt = el('div', 'assistant-error-detail', detail + ' ');
            var fix = el('a', null, 'Check the assistant settings \u2192');
            fix.href = '/orgs/' + _ctx[1] + '/settings/assistant';
            dt.appendChild(fix);
            d.appendChild(dt);
        }
        var retry = el('button', 'assistant-retry', 'Retry');
        retry.type = 'button';
        retry.addEventListener('click', function () {
            d.remove();
            input().value = text;
            send();
        });
        d.appendChild(retry);
        messages().appendChild(d);
        scrollDown();
    }

    function appendErrorBanner(text) {
        messages().appendChild(el('div', 'assistant-error-banner', text));
        scrollDown();
    }

    // ── Proposed actions ──────────────────────────────────────────────
    // A mutating tool call never runs during the turn: the server writes a
    // proposal and the panel asks. The secrets an action declares are typed
    // here and posted straight to the approve endpoint -- they never enter
    // the conversation. The decision then goes back to the model as the
    // next user turn so it can carry on (re-test, report the queue, ...).
    function appendProposal(p) {
        var d = el('div', 'assistant-proposal');
        d.dataset.id = p.id;
        d.appendChild(el('div', 'assistant-proposal-title', 'Proposed action #' + p.id));
        d.appendChild(el('div', null, p.summary));
        var keys = Object.keys(p.arguments || {});
        if (keys.length) {
            var dl = el('dl');
            keys.forEach(function (k) {
                var row = el('div');
                row.appendChild(el('dt', null, k.replace(/_/g, ' ')));
                var v = p.arguments[k];
                row.appendChild(el('dd', null, Array.isArray(v) ? (v.join(', ') || '—') : String(v)));
                dl.appendChild(row);
            });
            d.appendChild(dl);
        }
        var inputs = [];
        (p.secret_fields || []).forEach(function (k) {
            var lab = el('label', null, k.replace(/_/g, ' '));
            var inp = el('input');
            inp.type = 'password';
            inp.name = k;
            inp.autocomplete = 'off';
            lab.appendChild(inp);
            d.appendChild(lab);
            inputs.push(inp);
        });
        var row = el('div', 'assistant-proposal-actions');
        var approve = el('button', 'assistant-approve', 'Approve');
        var reject = el('button', 'assistant-reject', 'Reject');
        approve.type = reject.type = 'button';
        row.appendChild(approve);
        row.appendChild(reject);
        d.appendChild(row);
        var status = el('div', 'assistant-proposal-status');
        d.appendChild(status);

        function settle(state, text) {
            d.dataset.status = state;
            d.classList.add('settled');
            approve.disabled = reject.disabled = true;
            inputs.forEach(function (i) { i.disabled = true; i.value = ''; });
            status.textContent = text || '';
        }
        var expires = p.expires_at ? new Date(p.expires_at) : null;
        if (p.status && p.status !== 'proposed') {
            settle(p.status, p.status.charAt(0).toUpperCase() + p.status.slice(1) + (p.result ? ' — ' + p.result : ''));
        } else if (expires && expires < new Date()) {
            settle('expired', 'Expired');
        } else {
            status.textContent = expires ? 'Waiting for your decision — expires ' + expires.toLocaleString() : 'Waiting for your decision';
            approve.disabled = reject.disabled = busy;  // released by setBusy(false)
        }

        function decide(action, body) {
            approve.disabled = reject.disabled = true;
            _fetch(API_BASE + '/proposals/' + encodeURIComponent(p.id) + '/' + action, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            }).then(function (r) {
                if (r.status === 401) throw AuthError();
                return r.json().then(function (j) {
                    if (!r.ok) {
                        // Already decided or expired: the card follows the server.
                        if (j && j.status) { settle(j.status, j.error); return; }
                        throw httpError(r.status, j);
                    }
                    settle(j.status, j.message);
                    sendText(j.message);
                });
            }).catch(function (e) {
                if (e && e.isAuth) { appendLoginPrompt(); return; }
                status.textContent = e.message || String(e);
                approve.disabled = reject.disabled = false;
            });
        }
        approve.addEventListener('click', function () {
            var body = {};
            inputs.forEach(function (i) { if (i.value) body[i.name] = i.value; });
            decide('approve', body);
        });
        reject.addEventListener('click', function () {
            var reason = window.prompt('Reject this action. Why? (optional)', '');
            if (reason === null) return;
            decide('reject', { reason: reason });
        });
        messages().appendChild(d);
        scrollDown();
        return d;
    }

    // Session expired / never logged in: the panel says so and offers the
    // way back instead of failing silently.
    function appendLoginPrompt() {
        clearEmptyHint();
        var d = el('div', 'assistant-error-banner');
        d.innerHTML = 'Your session has expired. ' +
            '<a href="/login?next=' + encodeURIComponent(window.location.pathname) +
            '">Log in to the portal</a> to keep chatting.';
        messages().appendChild(d);
        scrollDown();
    }

    function AuthError() {
        var e = new Error('authentication required');
        e.isAuth = true;
        return e;
    }

    // ── Provenance: how the answer was assembled ──────────────────────
    // tool_result frames carry a provenance object (report, build, dataset,
    // filters, row counts…). They are collected per turn and rendered as one
    // collapsible footer under the turn's final answer.
    var _prov = [];

    function fmtWhen(v) {
        var d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
        return isNaN(d.getTime()) ? String(v) : d.toLocaleString();
    }
    function fmtVal(v) {
        if (v == null || v === '') return '';
        if (Array.isArray(v)) return v.map(fmtVal).join(', ');
        if (typeof v === 'object') {
            return Object.keys(v).map(function (k) { return k + '=' + fmtVal(v[k]); }).join(', ');
        }
        return String(v);
    }
    function provRow(label, html) {
        return html ? '<div><dt>' + label + '</dt><dd>' + html + '</dd></div>' : '';
    }

    function renderProvenance(target, provs) {
        provs = (provs || []).filter(function (p) { return p && typeof p === 'object'; });
        if (!target || !provs.length) return;
        // One report may be described by several tools in a turn; merge
        // them so the footer lists it once.
        var byReport = {}, order = [];
        provs.forEach(function (pv) {
            var k = String(pv.report || pv.report_name || order.length);
            if (!byReport[k]) { byReport[k] = {}; order.push(k); }
            Object.keys(pv).forEach(function (f) { if (pv[f] != null) byReport[k][f] = pv[f]; });
        });
        var body = '', badges = '', link = null;
        order.forEach(function (k) {
            var pv = byReport[k];
            var name = escapeHtml(String(pv.report_name || pv.report || ''));
            if (pv.link && sameOrigin(pv.link)) {
                link = link || pv.link;
                name = '<a href="' + attr(pv.link) + '" target="_blank" rel="noopener">' + name + '</a>';
            }
            body += provRow('Report', name);
            if (pv.built_at) body += provRow('Built', escapeHtml(fmtWhen(pv.built_at)));
            body += provRow('Dataset', escapeHtml(fmtVal(pv.dataset)));
            body += provRow('Filters', escapeHtml(fmtVal(pv.filters)));
            body += provRow('Date range', escapeHtml(fmtVal(pv.date_range)));
            // aggregate arrives as [{column, agg}] and group_by as a list;
            // either may be empty, in which case the row is left out.
            var aggs = (Array.isArray(pv.aggregate) ? pv.aggregate : [pv.aggregate]).map(function (a) {
                return a && typeof a === 'object' && a.agg ? a.agg + '(' + (a.column || '*') + ')' : fmtVal(a);
            }).filter(Boolean).join(', ');
            var by = fmtVal(pv.group_by);
            var agg = [aggs, by ? 'by ' + by : ''].filter(Boolean).join(' ');
            body += provRow('Aggregation', escapeHtml(agg));
            var rows = [
                pv.rows_returned != null ? pv.rows_returned + ' returned' : '',
                pv.rows_after != null ? pv.rows_after + ' after filters' : '',
                pv.rows_total != null ? pv.rows_total + ' total' : ''
            ].filter(Boolean).join(' · ');
            body += provRow('Rows', escapeHtml(rows));
            if (pv.mock) badges += '<span class="assistant-badge warn">Mock data</span>';
            var fails = pv.validation && Number(pv.validation.fail);
            if (fails) badges += '<span class="assistant-badge fail">' + fails + ' failing check' + (fails === 1 ? '' : 's') + '</span>';
        });
        if (!body && !badges) return;
        var d = el('details', 'assistant-prov');
        d.innerHTML = '<summary>How I got this</summary>' +
            (badges ? '<div class="assistant-badges">' + badges + '</div>' : '') +
            '<dl>' + body + '</dl>' +
            (link ? '<a class="assistant-prov-open" href="' + attr(link) + '" target="_blank" rel="noopener">Open in report →</a>' : '');
        // Above the Copy/Pin bar whether the answer is live or re-rendered
        // from history (where the bar is already there).
        var bar = target.querySelector('.assistant-actions');
        if (bar) target.insertBefore(d, bar); else target.appendChild(d);
    }

    function lastAnswer() {
        var all = messages().querySelectorAll('.assistant-msg.assistant');
        return all[all.length - 1] || null;
    }

    var _thinkingEl = null;
    function showThinking() {
        if (_thinkingEl) return;
        _thinkingEl = el('div', 'assistant-thinking', 'Thinking');
        messages().appendChild(_thinkingEl);
        scrollDown();
    }
    function hideThinking() {
        if (_thinkingEl) { _thinkingEl.remove(); _thinkingEl = null; }
    }

    // ── Backend plumbing ──────────────────────────────────────────────

    function ensureSession() {
        if (sessionId) return Promise.resolve(sessionId);
        return _fetch(API_BASE + '/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ report: _contextReport || '' })
        })
            .then(function (r) {
                if (r.status === 401) throw AuthError();
                if (!r.ok) throw new Error('session create failed (' + r.status + ')');
                return r.json();
            })
            .then(function (s) {
                sessionId = s.id;
                sessionScope = s.scope;
                sessionReport = s.report;
                storeSession(sessionId, sessionScope, sessionReport);
                return sessionId;
            });
    }

    function restoreSession() {
        var stored = storedSession();
        if (!stored) return;
        loadSession(stored).catch(function (e) {
            if (e && e.isAuth) {
                appendLoginPrompt();
                return;
            }
            // Session was deleted, or belongs to another studio — start
            // fresh on the next send.
            var m = sessionMap();
            Object.keys(m).forEach(function (k) { if (String(m[k]) === String(stored)) delete m[k]; });
            lsSet(LS_SESSION, JSON.stringify(m));
        });
    }

    function loadSession(id) {
        return _fetch(API_BASE + '/sessions/' + encodeURIComponent(id))
            .then(function (r) {
                if (r.status === 401) throw AuthError();
                if (!r.ok) throw new Error('gone');
                return r.json();
            })
            .then(function (s) {
                if (s.scope === 'report' && s.report !== _contextReport) throw new Error('context changed');
                sessionId = s.id;
                sessionScope = s.scope;
                sessionReport = s.report;
                storeSession(sessionId, sessionScope, sessionReport);
                messages().innerHTML = '';
                setAbout(s.alert_title);
                if ((s.transcript || []).length) renderTranscript(s.transcript);
                else appendEmptyHint(s.alert_title ? 'This conversation starts from the alert above — ask about it.' : null);
                renderPins();
            });
    }

    // The pinned line above a conversation opened from an alert email.
    function setAbout(title) {
        var a = byId('assistantAbout');
        a.textContent = title ? 'About alert: ' + title : '';
        a.hidden = !title;
    }

    function newSession() {
        storeSession(null, sessionScope, sessionReport);
        resetSession('New conversation started. What would you like to know?');
    }

    function resetSession(intro) {
        sessionId = null;
        sessionScope = null;
        sessionReport = null;
        messages().innerHTML = '';
        setAbout(null);
        appendEmptyHint(intro);
        renderPins();
    }

    // ── History popover ───────────────────────────────────────────────

    function relTime(v) {
        if (v == null) return '';
        var d = typeof v === 'number' ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
        if (isNaN(d.getTime())) return '';
        var m = Math.round((Date.now() - d.getTime()) / 60000);
        if (m < 1) return 'just now';
        if (m < 60) return m + ' min ago';
        if (m < 60 * 24) return Math.round(m / 60) + ' h ago';
        if (m < 60 * 24 * 7) return Math.round(m / 1440) + ' d ago';
        return d.toLocaleDateString();
    }

    function historyPop() { return byId('assistantHistoryPop'); }

    function toggleHistory(force) {
        var pop = historyPop(), btn = byId('assistantHistory');
        var show = force != null ? force : pop.hidden;
        pop.hidden = !show;
        btn.setAttribute('aria-expanded', show ? 'true' : 'false');
        if (!show) return;
        pop.innerHTML = '<div class="assistant-hnote">Loading…</div>';
        _fetch(API_BASE + '/sessions')
            .then(function (r) { return r.ok ? r.json() : []; })
            .then(function (list) {
                list = Array.isArray(list) ? list : (list && list.sessions) || [];
                list = list.filter(function (s) {
                    return s.scope !== 'report' || s.report === _contextReport;
                });
                pop.innerHTML = '';
                if (!list.length) {
                    pop.appendChild(el('div', 'assistant-hnote', 'No conversations yet.'));
                    return;
                }
                list.forEach(function (sess) {
                    var row = el('div', 'assistant-hrow' + (sess.id === sessionId ? ' current' : ''));
                    var open = el('button', 'assistant-hopen');
                    open.type = 'button';
                    open.setAttribute('role', 'menuitem');
                    open.appendChild(el('span', 'assistant-htitle', sess.title || 'Untitled'));
                    open.appendChild(el('span', 'assistant-hmeta',
                        [relTime(sess.updated_at), sess.message_count != null ? sess.message_count + ' messages' : '']
                            .filter(Boolean).join(' · ')));
                    open.addEventListener('click', function () {
                        toggleHistory(false);
                        loadSession(sess.id).catch(function () {});
                    });
                    var del = el('button', 'assistant-hdel', '✕');
                    del.type = 'button';
                    del.setAttribute('aria-label', 'Delete conversation');
                    del.addEventListener('click', function () {
                        if (!window.confirm('Delete this conversation?')) return;
                        _fetch(API_BASE + '/sessions/' + encodeURIComponent(sess.id), { method: 'DELETE' })
                            .then(function () {
                                row.remove();
                                if (sess.id === sessionId) newSession();
                            })
                            .catch(function () {});
                    });
                    row.appendChild(open);
                    row.appendChild(del);
                    pop.appendChild(row);
                });
            })
            .catch(function () {
                pop.innerHTML = '<div class="assistant-hnote">Could not load history.</div>';
            });
    }

    // ── Per-answer actions: copy, pin ─────────────────────────────────

    function turnIndex(msgEl) {
        return Array.prototype.indexOf.call(messages().querySelectorAll('.assistant-msg.assistant'), msgEl);
    }

    // Markdown → clipboard text: tables become TSV, everything else is the
    // source as written.
    function copyText(md) {
        return md.split('\n').map(function (line) {
            if (!/^\s*\|/.test(line)) return line;
            var cells = line.replace(/^\s*\||\|\s*$/g, '').split('|').map(function (c) { return c.trim(); });
            if (cells.every(function (c) { return /^:?-{2,}:?$/.test(c); })) return null;
            return cells.join('\t');
        }).filter(function (l) { return l !== null; }).join('\n');
    }

    function pinSet() {
        var all = lsJson(LS_PINS);
        return sessionId && Array.isArray(all[sessionId]) ? all[sessionId] : [];
    }
    function savePins(list) {
        if (!sessionId) return;
        var all = lsJson(LS_PINS);
        if (list.length) all[sessionId] = list; else delete all[sessionId];
        lsSet(LS_PINS, JSON.stringify(all));
    }

    // Pinned answers are copies (minus their own controls) in a collapsible
    // strip above the message list. ponytail: pins live in localStorage,
    // per browser; move them into session metadata if they need to follow
    // the person across devices.
    function renderPins() {
        var strip = byId('assistantPinned'), body = strip.querySelector('.assistant-pinned-body');
        var pins = pinSet(), all = messages().querySelectorAll('.assistant-msg.assistant');
        body.innerHTML = '';
        pins.forEach(function (t) {
            var src = all[t];
            if (!src) return;
            var copy = src.cloneNode(true);
            copy.querySelectorAll('.assistant-actions, .assistant-prov').forEach(function (n) { n.remove(); });
            copy.classList.add('pinned');
            body.appendChild(copy);
        });
        strip.hidden = !body.childNodes.length;
        all.forEach(function (m, i) {
            var b = m.querySelector('.assistant-pin');
            if (b) b.setAttribute('aria-pressed', pins.indexOf(i) >= 0 ? 'true' : 'false');
        });
    }

    function decorate(msgEl, md) {
        if (!msgEl || msgEl.querySelector('.assistant-actions')) return;
        msgEl.dataset.md = md;
        var bar = el('div', 'assistant-actions');
        function btn(cls, label, text) {
            var b = el('button', cls, text);
            b.type = 'button';
            b.setAttribute('aria-label', label);
            b.title = label;
            bar.appendChild(b);
            return b;
        }
        btn('assistant-copy', 'Copy answer', 'Copy').addEventListener('click', function () {
            var self = this;
            var text = copyText(msgEl.dataset.md || '');
            var done = function () { self.textContent = 'Copied'; setTimeout(function () { self.textContent = 'Copy'; }, 1200); };
            if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () {});
        });
        btn('assistant-pin', 'Pin answer', 'Pin').addEventListener('click', function () {
            var t = turnIndex(msgEl), pins = pinSet(), at = pins.indexOf(t);
            if (at >= 0) pins.splice(at, 1); else pins.push(t);
            savePins(pins.sort(function (a, b) { return a - b; }));
            renderPins();
        });
        msgEl.appendChild(bar);
    }

    function renderTranscript(transcript) {
        if (!transcript.length) return;
        closeAssistant();
        messages().innerHTML = '';
        // Provenance from a turn's tool entries lands under that turn's last
        // answer, i.e. it is flushed when the next question starts.
        var pending = [], answer = null;
        function flush() { renderProvenance(answer, pending); pending = []; answer = null; }
        transcript.forEach(function (entry) {
            if (entry.role === 'user' && typeof entry.content === 'string') {
                flush();
                messages().appendChild(el('div', 'assistant-msg user', entry.content));
            } else if (entry.role === 'assistant') {
                var blocks = Array.isArray(entry.content)
                    ? entry.content
                    : [{ type: 'text', text: String(entry.content || '') }];
                blocks.forEach(function (b) {
                    if (b.type === 'text' && b.text) answer = appendAssistant(b.text);
                });
            } else if (entry.role === 'tool' && entry.proposal) {
                appendProposal(entry.proposal);
            } else if (entry.role === 'tool' && entry.provenance) {
                pending.push(entry.provenance);
            }
        });
        flush();
        scrollDown();
    }

    function handleEvent(ev) {
        switch (ev.type) {
            case 'turn_start':
                showThinking();
                break;
            case 'text_delta':
                hideThinking();
                _gotText = true;
                appendAssistantDelta(ev.text);
                break;
            case 'done':
                renderProvenance(_live || lastAnswer(), _prov);
                _prov = [];
                closeAssistant();
                renderPins();
                break;
            case 'tool_use':
                hideThinking();
                closeAssistant();
                appendTool(ev.name, ev.id);
                showThinking();
                break;
            case 'tool_result':
                if (ev.provenance) _prov.push(ev.provenance);
                var chip = toolChip(ev);
                if (chip) {
                    chip.classList.add(ev.is_error ? 'error' : 'done');
                    chip.lastChild.textContent = (_toolLabels[ev.name] || ev.name) + (ev.is_error ? ' hit a problem' : '');
                }
                break;
            case 'proposal':
                closeAssistant();
                appendProposal(ev);
                break;
            case 'rate_limit':
                showThinking();
                break;
            case 'error':
                // Surfaces through send()'s catch as a failed turn (Retry).
                var err = new Error(errorText(ev.code, ev.message));
                err.code = ev.code;
                err.detail = ev.detail;
                throw err;
        }
    }

    function parseFrames(buffer, onEvent) {
        var idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
            var frame = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            // Lines starting with ":" are SSE comments (the server's keepalives).
            var data = frame.split('\n').filter(function (l) { return l.charAt(0) !== ':'; })
                .map(function (l) { return /^data: /.test(l) ? l.slice(6) : null; })
                .filter(function (l) { return l !== null; })
                .join('\n');
            if (!data) continue;
            var ev = null;
            try { ev = JSON.parse(data); } catch (e) { /* skip bad frame */ }
            if (ev) onEvent(ev);
        }
        return buffer;
    }

    // While a reply streams, Send is Stop: it aborts the fetch, which ends
    // the read loop with an AbortError that the catch below treats as "the
    // person changed their mind", not a failure.
    var _abort = null, _reader = null, _gotText = false;

    function setBusy(on) {
        busy = on;
        var b = byId('assistantSend');
        b.textContent = on ? 'Stop' : 'Send';
        b.setAttribute('aria-label', on ? 'Stop the reply' : 'Send');
        b.classList.toggle('assistant-stop', on);
        // A decision edits the transcript a streaming turn is about to save.
        messages().querySelectorAll('.assistant-proposal:not(.settled) button').forEach(function (x) {
            x.disabled = on;
        });
    }

    function stop() {
        // Cancel the body reader too: it ends the read loop even when the
        // response body is not wired to the signal (proxies, stubs).
        if (_reader) _reader.cancel().catch(function () {});
        if (_abort) _abort.abort();
    }

    function send() {
        if (busy) {
            stop();
            return;
        }
        var text = input().value.trim();
        if (!text) return;
        input().value = '';
        autosize();
        sendText(text);
    }

    // One turn: the composer's text, or the automatic follow-up a decided
    // proposal posts so the model hears what happened.
    function sendText(text) {
        if (busy) return;
        setBusy(true);
        var userEl = appendUser(text);
        closeAssistant();
        _gotText = false;
        _prov = [];
        _abort = new AbortController();
        var signal = _abort.signal;
        var page = {
            path: contextPath(),
            title: (_hostedDocument && _hostedDocument.title) || document.title || ''
        };
        if (_chart) { page.chart = _chart; _chart = null; }

        ensureSession()
            .then(function (sid) {
                return _fetch(API_BASE + '/sessions/' + sid + '/message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    signal: signal,
                    body: JSON.stringify({
                        message: text,
                        // Where the user currently is — lets the assistant
                        // resolve "this report" / "this chart" questions.
                        page: page
                    })
                });
            })
            .then(function (resp) {
                if (resp.status === 401) throw AuthError();
                if (!resp.ok) {
                    return resp.json().then(function (j) {
                        throw httpError(resp.status, j);
                    }, function () {
                        throw httpError(resp.status, null);
                    });
                }
                var reader = _reader = resp.body.getReader();
                var decoder = new TextDecoder();
                var buffer = '';
                function pump() {
                    return reader.read().then(function (chunk) {
                        if (chunk.done) return;
                        buffer += decoder.decode(chunk.value, { stream: true });
                        buffer = parseFrames(buffer, handleEvent);
                        return pump();
                    });
                }
                return pump();
            })
            .catch(function (e) {
                if (e && e.name === 'AbortError') return;
                stop();  // a server-side error frame: drop the rest of the stream
                if (e && e.isAuth) appendLoginPrompt();
                else failTurn(text, userEl, e.message || String(e), e.detail);
            })
            .then(function () {
                hideThinking();
                closeAssistant();
                _abort = null;
                _reader = null;
                setBusy(false);
                input().focus();
            });
    }

    // ── "Ask about this chart" ─────────────────────────────────────────
    // A spark in every titled .fw-section header (portal-injected, the
    // framework markup is untouched). It opens the panel with the composer
    // addressed to that section and remembers the section as chart context
    // for the next message.
    function dataAttr(sec, name) {
        var n = sec.hasAttribute('data-' + name) ? sec : sec.querySelector('[data-' + name + ']');
        return n ? n.getAttribute('data-' + name) : null;
    }

    function mountSectionAsks(doc) {
        doc = doc || document;
        if (doc === document && !IS_REPORT) return;
        doc.querySelectorAll('.fw-section').forEach(function (sec, i) {
            var h2 = sec.querySelector(':scope > h2');
            if (!h2 || h2.querySelector('.assistant-section-ask')) return;
            var title = h2.textContent.trim();
            if (!title) return;
            var b = el('button', 'assistant-section-ask');
            b.type = 'button';
            b.innerHTML = SPARK;
            b.title = 'Ask about “' + title + '”';
            b.setAttribute('aria-label', b.title);
            b.addEventListener('click', function (ev) {
                ev.stopPropagation();  // collapsible headers toggle on click
                _chart = { section_id: sec.id || dataAttr(sec, 'section-id') || ('section-' + (i + 1)), title: title };
                var kind = dataAttr(sec, 'kind'), ds = dataAttr(sec, 'dataset-id');
                if (kind) _chart.kind = kind;
                if (ds) _chart.dataset_id = ds;
                openPanel(b);
                var t = input();
                t.value = 'About “' + title + '”: ';
                autosize();
                t.focus();
                t.setSelectionRange(t.value.length, t.value.length);
            });
            h2.appendChild(b);
        });
    }

    // ── Boot ───────────────────────────────────────────────────────────

    function boot() {
        // No studio in the path means there is nothing the assistant could
        // read here — don't even mount.
        if (!API_BASE) return;
        // The persistent console document owns the one assistant panel while
        // it hosts a report. Old report builds receive this bundle at serve
        // time too, so the child must not mount a duplicate widget.
        if (HOSTED_BY_CONSOLE) return;
        mount();
        // The page tells the server what "this report" is so it can answer
        // with report-specific suggestions.
        var serial = ++_suggestionSerial;
        _fetch(API_BASE + '/available' + pageQuery())
            .then(function (r) {
                if (r.status === 401) return { available: false };
                return r.json();
            })
            .then(function (j) {
                if (serial !== _suggestionSerial) return;
                if (!j) return;
                if (!j.available) {
                    // An admin sees where to switch it on; everyone else
                    // sees nothing at all.
                    if (j.can_configure && j.settings_url && ask.parentNode) {
                        var a = el('a', 'assistant-setup');
                        a.innerHTML = SPARK + '<span>Set up AI →</span>';
                        a.setAttribute('aria-label', 'Set up the AI assistant');
                        a.href = j.settings_url;
                        ask.parentNode.insertBefore(a, ask);
                    }
                    return;
                }
                available = true;
                _suggestions = Array.isArray(j.suggestions) ? j.suggestions : [];
                if (typeof j.deadline_s === 'number') _deadline = j.deadline_s;
                appendEmptyHint();
                ask.classList.remove('assistant-hidden');
                mountSectionAsks();
                if (_hostedDocument) {
                    mountSectionAsks(_hostedDocument);
                    refreshSuggestions();
                }
                restoreSession();
                // Re-open the panel if it was open on the previous page so
                // the conversation visibly follows the user around.
                if (lsGet(LS_OPEN) === '1') {
                    panel.classList.add('assistant-open');
                    setExpanded(true);
                }
                applyLayout(true);
            })
            .catch(function () { /* leave hidden */ });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
