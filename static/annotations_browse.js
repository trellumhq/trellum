/* Annotation browser enhancement. The server-rendered page is the fallback;
   this controller only replaces the bounded pages with JSON reads.
   Mutations are never replayed automatically: ambiguous outcomes are observed
   through the C03 poller and the user retries explicitly with the same
   operation id. */
(function () {
    'use strict';
    var root = document.querySelector('[data-anno-root]');
    if (!root || !root.dataset.dataUrl) return;
    var main = root.querySelector('[data-anno-main]');
    var statusNode = root.querySelector('[data-anno-status]');
    var STORAGE = 'anno-cal:' + root.dataset.org + '/' + root.dataset.studio;
    var CHUNK = 25, TOUCH_MS = 5 * 60 * 1000;
    var active = true, gen = 0, controllers = { main: null, facet: null }, searchTimer = null;
    var poller = null, lastTouch = Date.now(), restoring = null, op = null;
    var q = { view: root.dataset.view || 'list', q: '', scope: null, state: root.dataset.state || '',
              revision: root.dataset.revision || '', defaults: false, month: '', date: '', cursor: '' };

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = String(text);
        return node;
    }
    function say(text, error) {
        statusNode.textContent = text || '';
        statusNode.toggleAttribute('data-error', !!error);
    }
    function busy(on) {
        root.dataset.annoReady = on ? 'false' : 'true';
        root.toggleAttribute('data-anno-busy', on);
        main.setAttribute('aria-busy', on ? 'true' : 'false');
    }

    /* ── persisted intent (same key/shape as the legacy calendar) ── */
    function saved() {
        try {
            var raw = localStorage.getItem(STORAGE);
            if (raw === null) return null;
            var value = JSON.parse(raw);
            return value && typeof value === 'object' ? value : {};
        } catch (_) { return null; }
    }
    function persist(change) {
        try {
            var value = saved() || {};
            Object.keys(change).forEach(function (k) { value[k] = change[k]; });
            localStorage.setItem(STORAGE, JSON.stringify(value));
        } catch (_) { /* storage may be unavailable */ }
    }
    function uploadable(value) {
        if (typeof value === 'number' || typeof value === 'boolean') value = String(value);
        if (typeof value !== 'string' || value.length > 256 || value.indexOf('\u0000') >= 0) return null;
        if (/[\ud800-\udbff](?![\udc00-\udfff])|(?:^|[^\ud800-\udbff])[\udc00-\udfff]/.test(value)) return null;
        return value;
    }

    /* ── reads ── */
    function params(extra) {
        var u = new URLSearchParams();
        u.set('mode', q.view);
        if (q.q) u.set('q', q.q);
        if (q.scope !== null) u.set('scope', q.scope);
        if (q.state) { u.set('state', q.state); if (q.revision) u.set('revision', q.revision); }
        else if (q.defaults) u.set('defaults', '1');
        if (q.month && q.view === 'month') u.set('month', q.month);
        if (q.date && q.view === 'day') u.set('date', q.date);
        if (q.cursor && q.view !== 'month') u.set('cursor', q.cursor);
        Object.keys(extra || {}).forEach(function (k) { u.set(k, extra[k]); });
        return u;
    }
    function fetchJSON(slot, query, generation) {
        if (controllers[slot]) controllers[slot].abort();
        var controller = new AbortController();
        controllers[slot] = controller;
        var timer = setTimeout(function () { controller.abort(); }, 15000);
        return fetch(root.dataset.dataUrl + '?' + query, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal, headers: { Accept: 'application/json' } })
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (body) { return { status: response.status, body: body }; });
            })
            .then(function (result) {
                if (!active || generation !== gen || controller !== controllers[slot] || controller.signal.aborted) return null;
                return result;
            })
            .finally(function () { clearTimeout(timer); if (controllers[slot] === controller) controllers[slot] = null; });
    }
    function failure(result) {
        var code = result.body && result.body.code;
        if (result.status === 410) { say('The saved filter expired. Restoring your saved selection…'); restore(); return; }
        if (result.status === 409) {
            say('The filter changed elsewhere. Load the latest filter or rebuild it from this browser.', true);
            actions([['Load latest', loadLatest], ['Rebuild from this browser', function () { restore(); }]]);
            return;
        }
        if (result.status === 403 || result.status === 404) {
            main.querySelector('[data-anno-rows]').replaceChildren(el('li', 'anno-empty', 'You no longer have access to these annotations.'));
            say('Access changed; content was cleared.', true);
            return;
        }
        say('Could not load annotations (' + (code || result.status) + '). Showing the last loaded page.', true);
        actions([['Retry', function () { load(); }]]);
    }
    function actions(list) {
        var bar = el('span', 'anno-actions');
        list.forEach(function (item) {
            var b = el('button', '', item[0]);
            b.type = 'button';
            b.addEventListener('click', function () { item[1](); });
            bar.append(b);
        });
        statusNode.append(' ', bar);
    }
    function loadLatest() { q.revision = ''; load(); }

    function load(keepPage) {
        if (!active) return;
        var generation = ++gen;
        busy(true);
        say('Loading…');
        var primary = fetchJSON('main', params().toString(), generation);
        var facets = fetchJSON('facet', params({ mode: 'types' }).toString(), generation).then(function (r) {
            if (r && r.status === 200) renderTypes(r.body);
            return r && fetchJSON('facet', params({ mode: 'scopes' }).toString(), generation);
        }).then(function (r) { if (r && r.status === 200) renderScopes(r.body); }).catch(function () { /* primary stays usable */ });
        return primary.then(function (r) {
            if (!r) return;
            if (r.status !== 200) { busy(false); failure(r); return; }
            q.state = r.body.filter.state || ''; q.revision = r.body.filter.revision || '';
            render(r.body);
            busy(false);
            say('');
            if (!keepPage) writeUrl();
        }).catch(function (error) {
            if (error.name === 'AbortError' || generation !== gen) return;
            busy(false);
            say('Could not load annotations. Showing the last loaded page.', true);
            actions([['Retry', function () { load(); }]]);
        }).then(function () { return facets; });
    }
    function writeUrl() {
        var u = params(); u.delete('mode'); u.set('view', q.view);
        var href = location.pathname + '?' + u.toString();
        if (href !== location.pathname + location.search) history.pushState({ anno: true }, '', href);
    }

    /* ── rendering (textContent only) ── */
    function render(body) {
        root.dataset.view = q.view;
        var rows = main.querySelector('[data-anno-rows]');
        rows.replaceChildren();
        if (!body.rows.length) {
            var complete = body.coverage ? body.coverage.complete : true;
            var empty = el('li', 'anno-empty', complete ? 'No events match these filters.' : 'No indexed events match these filters yet.');
            empty.setAttribute('data-anno-empty', '');
            rows.append(empty);
        }
        body.rows.forEach(function (ev) {
            var li = el('li', 'anno-row');
            li.dataset.eventId = ev.id;
            li.append(el('span', 'anno-dates', ev.dates_display), el('span', 'anno-dot t-' + ev.type_css), el('span', 'anno-label', ev.label),
                el('span', 'anno-badge', ev.scope || '(empty scope)'));
            if (ev.retained) li.append(el('span', 'anno-badge', 'retained'));
            if (ev.report_url) { var a = el('a', '', ev.source_label); a.href = ev.report_url; li.append(a); } else li.append(el('span', '', ev.source_label));
            rows.append(li);
        });
        var total = main.querySelector('[data-anno-total]');
        total.textContent = body.total + (body.total === 1 ? ' event' : ' events') + (body.overflow ? ' in this grid; showing the first ' + body.rows.length : '');
        var grid = main.querySelector('[data-anno-grid]');
        if (grid && body.days) renderGrid(grid, body);
        var pager = main.querySelector('.anno-pager');
        pager.replaceChildren();
        if (q.cursor) pager.append(pagerLink('First page', function () { q.cursor = ''; load(); }));
        if (body.next_cursor) { var n = pagerLink('Next 25', function () { q.cursor = body.next_cursor; load(); }); n.setAttribute('data-anno-next', ''); pager.append(n); }
        var banner = root.querySelector('[data-anno-coverage]');
        if (banner && body.coverage && body.coverage.complete) banner.remove();
    }
    function pagerLink(label, handler) {
        var a = el('a', '', label);
        a.href = '#'; a.addEventListener('click', function (e) { e.preventDefault(); handler(); });
        return a;
    }
    function renderGrid(grid, body) {
        var tbody = grid.querySelector('tbody');
        tbody.replaceChildren();
        for (var i = 0; i < body.days.length; i += 7) {
            var tr = el('tr');
            body.days.slice(i, i + 7).forEach(function (d) {
                var td = el('td'), a = el('a');
                if (d.date.slice(0, 7) !== body.month) td.className = 'outside';
                a.href = '#';
                a.append(el('span', '', Number(d.date.slice(8))));
                if (d.count) a.append(' ', el('b', 'anno-count', d.count));
                a.addEventListener('click', function (e) { e.preventDefault(); q.view = 'day'; q.date = d.date; q.cursor = ''; load(); });
                td.append(a); tr.append(td);
            });
            tbody.append(tr);
        }
    }
    function renderTypes(body) {
        var list = root.querySelector('[data-anno-types] ul');
        list.replaceChildren();
        body.rows.forEach(function (t) {
            var li = el('li'), b = el('button');
            b.type = 'button'; b.dataset.type = t.value;
            b.setAttribute('aria-pressed', t.hidden ? 'false' : 'true');
            b.append(el('span', 'anno-dot t-' + t.css), t.label + ' ', el('span', 'anno-count', t.count), ' ', el('span', 'anno-state', t.hidden ? 'hidden' : 'shown'));
            b.addEventListener('click', function () { toggle(t.value, !t.hidden); });
            li.append(b); list.append(li);
        });
    }
    function renderScopes(body) {
        var list = root.querySelector('[data-anno-scopes]');
        list.replaceChildren();
        function item(label, value, count) {
            var li = el('li'), a = el('a', '', label + (count === undefined ? '' : ' ' + count));
            a.href = '#';
            if (q.scope === value) a.setAttribute('aria-current', 'true');
            a.addEventListener('click', function (e) { e.preventDefault(); q.scope = value; q.cursor = ''; persist({ scope: value === null ? '' : value }); load(); });
            li.append(a); list.append(li);
        }
        item('All', null);
        body.rows.forEach(function (s) { item(s.value || '(empty scope)', s.value, s.count); });
    }

    /* ── state mutations (single flight, explicit retry) ── */
    function post(body) {
        var controller = new AbortController();
        var timer = setTimeout(function () { controller.abort(); }, 10000);
        var csrf = root.querySelector('input[name=csrfmiddlewaretoken]');
        return fetch(root.dataset.filterUrl, {
            method: 'POST', credentials: 'same-origin', signal: controller.signal,
            headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' },
            body: JSON.stringify(body)
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (b) { return { status: response.status, body: b }; });
        }).finally(function () { clearTimeout(timer); });
    }
    function opId(base) { return base + '-' + Math.random().toString(16).slice(2, 10); }
    function touch() {
        if (!q.state || Date.now() - lastTouch < TOUCH_MS) return;
        lastTouch = Date.now();
        post({ op: 'touch', state: q.state, operation_id: opId('touch') }).catch(function () { /* next interaction may renew */ });
    }
    function observe(operation, token, then) {
        if (poller) poller.stop();
        var reads = 0;
        poller = window.TrellumActions.createPoller({
            intervalMs: 2000, maxIntervalMs: 30000, timeoutMs: 10000,
            read: function (ctx) {
                var u = root.dataset.statusUrl + '?state=' + encodeURIComponent(token) + '&operation_id=' + encodeURIComponent(operation);
                return fetch(u, { credentials: 'same-origin', cache: 'no-store', signal: ctx.signal }).then(function (r) { return r.json(); });
            },
            onStatus: function (result) {
                reads++;
                if (result.action && result.action.state === 'succeeded') { poller.stop(); then(result); }
                else if (reads >= 3) { poller.stop(); say('The change was not recorded. Try it again.', true); busy(false); }
            },
            onError: function () { say('Could not confirm the change. Try it again; it will not be applied twice.', true); },
            isTerminal: function (r) { return r.action && r.action.state === 'succeeded'; }
        });
        poller.start();
    }
    function step(body, then) {
        var attempt = post(body);
        return attempt.then(function (r) {
            if (r.status === 200) return then(r.body);
            failure(r);
            busy(false);
        }).catch(function (error) {
            if (!active) return;
            busy(false);
            say('The outcome is unconfirmed. Checking…');
            var token = body.state || (op && op.token);
            if (token) observe(body.operation_id, token, function (res) { then({ result: Object.assign({ token: token }, res.result), action: res.action }); });
            else say('The outcome is unconfirmed and no state was created yet. Try again.', true);
        });
    }
    function ensureState() {
        if (q.state) return Promise.resolve();
        busy(true); say('Saving filter…');
        var base = opId('c');
        return post({ op: 'create', operation_id: base, mode: q.defaults ? 'defaults' : 'explicit', expected_items: 0, expected_chain_digest: EMPTY })
            .then(function (r) {
                if (r.status !== 200) { failure(r); throw new Error('stop'); }
                return post({ op: 'finalize', state: r.body.result.token, operation_id: base + '-f' });
            }).then(function (r) {
                if (r.status !== 200) { failure(r); throw new Error('stop'); }
                q.state = r.body.result.token; q.revision = String(r.body.result.revision); q.defaults = false;
            });
    }
    function toggle(type, hide) {
        if (restoring) return;
        touch();
        ensureState().then(function () {
            busy(true); say('Saving filter…');
            return step({ op: 'delta', state: q.state, operation_id: opId('d'), base_revision: Number(q.revision), changes: [[type, hide]] }, function (body) {
                q.revision = String(body.result.revision);
                var s = saved() || {}, hidden = Array.isArray(s.hiddenTypes) ? s.hiddenTypes.filter(function (v) { return v !== type; }) : [];
                if (hide) hidden.push(type);
                persist({ hiddenTypes: hidden });
                q.cursor = '';
                return load();
            });
        }).catch(function (error) { if (error.message !== 'stop') throw error; });
    }

    /* ── restore: exact saved intent in bounded, sequential, idempotent chunks ── */
    var EMPTY = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
    function hex(buffer) { return Array.prototype.map.call(new Uint8Array(buffer), function (b) { return ('0' + b.toString(16)).slice(-2); }).join(''); }
    function unhex(text) { var out = new Uint8Array(32); for (var i = 0; i < 32; i++) out[i] = parseInt(text.substr(i * 2, 2), 16); return out; }
    async function chain(previous, keys) {
        var body = new TextEncoder().encode(JSON.stringify(keys)), joined = new Uint8Array(32 + body.length);
        joined.set(unhex(previous)); joined.set(body, 32);
        return hex(await crypto.subtle.digest('SHA-256', joined));
    }
    async function restore() {
        if (restoring) return;
        var s = saved(), raw = s && Array.isArray(s.hiddenTypes) ? s.hiddenTypes : [];
        var keys = raw.map(uploadable).filter(function (v) { return v !== null; });
        var chunks = [], digest = EMPTY;
        for (var i = 0; i < keys.length; i += CHUNK) { var c = keys.slice(i, i + CHUNK); chunks.push(c); digest = await chain(digest, c); }
        var base = opId('r');
        restoring = { total: chunks.length };
        busy(true);
        try {
            var created = await post({ op: 'create', operation_id: base, mode: 'explicit', expected_items: keys.length, expected_chain_digest: digest });
            if (created.status !== 200) { failure(created); return; }
            var token = created.body.result.token;
            for (var n = 0; n < chunks.length; n++) {
                say('Restoring saved filters (' + (n + 1) + ' of ' + chunks.length + ')…');
                var r = await post({ op: 'append', state: token, operation_id: base + '-' + n, chunk_index: n, keys: chunks[n] });
                if (r.status !== 200) { failure(r); return; }
            }
            var done = await post({ op: 'finalize', state: token, operation_id: base + '-f' });
            if (done.status !== 200) { failure(done); return; }
            q.state = token; q.revision = String(done.body.result.revision); q.defaults = false; q.cursor = '';
        } catch (_) {
            say('Restoring the saved filter was interrupted; the first page is still usable. Reload to try again.', true);
            return;
        } finally { restoring = null; if (!statusNode.hasAttribute('data-error')) busy(false); }
        await load();
    }

    /* ── wiring ── */
    function localQuery(link) {
        var u = new URL(link.href, location.href), p = u.searchParams;
        if (u.pathname !== location.pathname) return false;
        q.view = p.get('view') || 'list'; q.q = p.get('q') || ''; q.scope = p.has('scope') ? p.get('scope') : null;
        q.state = p.get('state') || ''; q.revision = p.get('revision') || ''; q.defaults = p.get('defaults') === '1';
        q.month = p.get('month') || ''; q.date = p.get('date') || ''; q.cursor = p.get('cursor') || '';
        return true;
    }
    root.addEventListener('click', function (event) {
        var link = event.target.closest('.anno-tabs a, .anno-monthbar a');
        if (link && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && localQuery(link)) { event.preventDefault(); touch(); load(); }
    });
    var form = root.querySelector('[data-anno-search]');
    var input = form && form.querySelector('input[name=q]');
    if (form) {
        form.addEventListener('submit', function (e) { e.preventDefault(); clearTimeout(searchTimer); q.q = input.value; q.cursor = ''; touch(); load(); });
        input.addEventListener('input', function () { clearTimeout(searchTimer); searchTimer = setTimeout(function () { q.q = input.value; q.cursor = ''; load(); }, 200); });
    }
    root.querySelectorAll('[data-anno-types] button[name=toggle]').forEach(function (b) {
        b.addEventListener('click', function (e) { e.preventDefault(); var v = b.value; toggle(v.slice(2), v[0] === '1'); });
    });
    window.addEventListener('popstate', function () {
        var u = new URL(location.href);
        localQuery({ href: u.href }); load(true);
    });
    function suspend() { active = false; gen++; Object.keys(controllers).forEach(function (k) { if (controllers[k]) controllers[k].abort(); }); clearTimeout(searchTimer); if (poller) poller.pause(); }
    function resume() { if (active) return; active = true; if (poller) poller.resume(); load(true); }
    document.addEventListener('console:native-start', suspend);
    document.addEventListener('console:native-restored', resume);
    window.addEventListener('pagehide', suspend);

    /* First enhanced paint: adopt a saved selection once, else use project defaults. */
    var s0 = saved();
    var params0 = new URL(location.href).searchParams;
    var explicit = params0.has('state') || params0.has('defaults') || params0.has('notice');
    root.dataset.annoReady = 'true';
    if (!explicit && !q.state) {
        if (s0 && Array.isArray(s0.hiddenTypes) && s0.hiddenTypes.length) restore();
        else if (!s0 || !Array.isArray(s0.hiddenTypes)) { q.defaults = true; load(true); }
    }
})();
