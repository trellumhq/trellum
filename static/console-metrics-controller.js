(function () {
    'use strict';
    // The summary endpoint takes at most this many definitions per request.
    var CHUNK = 50;
    // Quiet retries for usage that is still being indexed: nothing to click.
    var RETRY_DELAYS = [5000, 15000, 30000, 60000];

    function mountSummary(context) {
        var root = context.root, region = root.querySelector('[data-metric-summary]');
        if (!region) return function () {};
        var cards = Array.from(region.querySelectorAll('[data-definition-id]'));
        if (!cards.length) return function () {};
        var revision = Number(region.dataset.revision);
        var disposed = false, request = null, timer = null, attempt = 0, waiting = null;
        // "N defined, M claimed": unknown (not zero) while reports are still being indexed.
        var claimedLabel = root.querySelector('[data-metric-claimed]');

        function el(parent, tag, value, className) {
            var node = document.createElement(tag);
            if (value != null) node.textContent = value;
            if (className) node.className = className;
            parent.appendChild(node);
            return node;
        }
        function plural(count, word) { return count + ' ' + word + (count === 1 ? '' : 's'); }
        function version(claim) { return claim.claimed_version ? 'v' + claim.claimed_version : 'an unknown version'; }
        function builtAt(value) { return value ? String(value).slice(0, 10) + ' ' + String(value).slice(11, 16) : 'never built'; }

        function renderUsage(item, card) {
            var usage = card.querySelector('[data-metric-usage]');
            usage.replaceChildren();
            usage.title = item.count_complete ? '' : 'Some report sources have not been re-checked yet, so this count can lag.';
            var tags = card.querySelector('[data-metric-tags]');
            var unused = tags && tags.querySelector('[data-unused]');
            if (!item.claim_count && item.usage_state === 'measuring') {
                // Nothing indexed claims it yet, but reports are still being indexed: not "unused".
                usage.title = 'Report usage is still being measured. This fills in by itself.';
                el(usage, 'span', 'measuring usage…', 'ui-chip mute');
                if (unused) unused.remove();
                return;
            }
            if (!item.claim_count) {
                el(usage, 'span', 'no reports claim this', 'ui-chip mute');
                if (!unused) {
                    if (!tags) {
                        tags = document.createElement('div'); tags.className = 'met-tags'; tags.dataset.metricTags = '';
                        var owner = card.querySelector('.met-owner'), facts = card.querySelector('.met-fact');
                        facts.insertBefore(tags, owner);
                    }
                    el(tags, 'span', 'unused', 'ui-chip mute').dataset.unused = '';
                }
                return;
            }
            if (unused) unused.remove();
            var claims = item.claims, total = item.claim_count;
            el(usage, 'b', plural(total, 'report'));
            var allCurrent = claims.length === total && claims.every(function (claim) { return claim.current; });
            if (allCurrent) {
                var all = el(usage, 'div', null, 'st');
                el(all, 'span', null, 'met-dot g'); all.appendChild(document.createTextNode(' all current'));
            } else {
                claims.forEach(function (claim) {
                    var row = el(usage, 'div', null, 'st');
                    el(row, 'span', null, 'met-dot ' + (claim.current ? 'g' : 'w'));
                    row.appendChild(document.createTextNode(' ' + claim.report));
                    if (!claim.current) {
                        row.appendChild(document.createTextNode(' · '));
                        el(row, 'b', 'built against ' + version(claim));
                    }
                });
            }
            var details = el(usage, 'details', null, 'met-expand');
            el(details, 'summary', plural(total, 'claiming report') + ' — details');
            var table = el(details, 'table', null, 'met-expand-table');
            var head = el(table, 'tr');
            ['Report', 'Last build', 'Definition', ''].forEach(function (label) { el(head, 'th', label); });
            claims.forEach(function (claim) {
                var row = el(table, 'tr');
                el(row, 'td', claim.report);
                el(row, 'td', builtAt(claim.built_at));
                var state = el(row, 'td');
                if (claim.current) el(state, 'span', 'current · v' + card.dataset.version, 'ui-chip acc');
                else el(state, 'span', 'stale — built against ' + version(claim), 'ui-chip warn');
                var open = el(el(row, 'td', null, 'ui-row-actions'), 'a', 'open report', 'ui-btn ghost');
                open.href = claim.report_url;
            });
            if (total > claims.length) {
                var more = el(details, 'p', null, 'met-expand-more');
                var link = el(more, 'a', 'All ' + total + ' indexed claims →');
                link.href = card.dataset.detailsUrl;
            }
        }

        function render(item, card) {
            renderUsage(item, card);
            var before = card.dataset.chartState;
            card.dataset.chartState = item.chart_state;
            card.dataset.embed = item.embed_url; card.dataset.chart = item.chart_url;
            card.dataset.workspaceVersion = item.workspace_version == null ? '' : String(item.workspace_version);
            card.dataset.workspaceQuality = item.workspace_quality || 'unknown';
            card.dataset.legacyUrl = item.legacy_url || '';
            card.dataset.overview = item.overview_url;
            card.querySelectorAll('.met-spark').forEach(function (spark) { spark.remove(); });
            if (item.spark && item.chart_state === 'ready') {
                var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                svg.setAttribute('class', 'met-spark'); svg.setAttribute('width', '140'); svg.setAttribute('height', '36');
                svg.setAttribute('viewBox', '0 0 140 36'); svg.setAttribute('aria-hidden', 'true');
                var line = document.createElementNS(svg.namespaceURI, 'polyline');
                line.setAttribute('points', item.spark); line.setAttribute('fill', 'none');
                line.setAttribute('stroke', 'currentColor'); line.setAttribute('stroke-width', '1.5');
                line.setAttribute('vector-effect', 'non-scaling-stroke'); svg.appendChild(line);
                card.querySelector('[data-metric-spark]').replaceChildren(svg);
            }
            if (before !== item.chart_state) {
                root.dispatchEvent(new CustomEvent('metric-summary', { detail: { card: card } }));
            }
        }

        // Definitions changed since this page rendered: take the new page,
        // once, instead of asking the person to reload.
        function reloadOnce() {
            try {
                var last = Number(sessionStorage.getItem('metrics-summary-reload') || 0);
                if (Date.now() - last < 30000) return false;
                sessionStorage.setItem('metrics-summary-reload', String(Date.now()));
            } catch (e) { /* storage blocked: reload anyway, once per mount */ }
            location.reload();
            return true;
        }

        function renderClaimed(data) {
            if (!claimedLabel || data.usage_pending || typeof data.claimed_count !== 'number') return;
            var count = data.claimed_count, text = document.createElement('b');
            text.textContent = claimedLabel.dataset.defined + ' defined, ' + count + ' claimed by report' + (count === 1 ? '' : 's');
            claimedLabel.replaceChildren(text);
            delete claimedLabel.dataset.pending;
        }

        async function readChunk(batch, signal) {
            var timedOut = false, flight = new AbortController();
            var stop = function () { flight.abort(); };
            signal.addEventListener('abort', stop, { once: true });
            var deadline = setTimeout(function () { timedOut = true; flight.abort(); }, 15000);
            try {
                var url = new URL(region.dataset.url, location.origin);
                url.searchParams.set('ids', batch.map(function (card) { return card.dataset.definitionId; }).join(','));
                url.searchParams.set('definition_revision', revision);
                if (claimedLabel && claimedLabel.dataset.pending !== undefined) url.searchParams.set('claimed', '1');
                var response = await fetch(url, { signal: flight.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
                var data = await response.json();
                if (signal.aborted || !region.isConnected) return null;
                if (response.status === 409 && data.state === 'changed') return { changed: true };
                if (!response.ok || data.definition_revision !== revision || !Array.isArray(data.metrics) ||
                    data.metrics.length !== batch.length || !['ready', 'partial'].includes(data.state)) throw new Error('Invalid summary');
                var byId = new Map(data.metrics.map(function (item) { return [String(item.id), item]; }));
                if (batch.some(function (card) { return !byId.has(card.dataset.definitionId); })) throw new Error('Missing summary');
                batch.forEach(function (card) { render(byId.get(card.dataset.definitionId), card); });
                renderClaimed(data);
                return { again: data.state === 'partial' || data.usage_pending === true ||
                    data.metrics.some(function (item) { return item.chart_state === 'unknown'; }),
                    measuring: data.usage_pending === true };
            } catch (error) {
                if (signal.aborted && !timedOut) return null;
                return { again: true };
            } finally {
                clearTimeout(deadline);
                signal.removeEventListener('abort', stop);
            }
        }

        async function run(list) {
            if (disposed) return;
            if (request) request.abort();
            request = new AbortController();
            var signal = request.signal, again = [], measuring = false;
            for (var i = 0; i < list.length; i += CHUNK) {
                var batch = list.slice(i, i + CHUNK);
                var result = await readChunk(batch, signal);
                if (signal.aborted || disposed) return;
                if (result && result.changed) { if (reloadOnce()) return; again = []; list.forEach(function (card) { card.dataset.chartState = 'error'; }); return; }
                if (result && result.again) again = again.concat(batch);
                if (result && result.measuring) measuring = true;
            }
            schedule(again, measuring);
        }

        // Usage that is still being measured keeps being asked for, quietly and at the slowest pace,
        // for as long as the page is open and visible: nobody should have to reload to see it.
        function schedule(pending, measuring) {
            if (!pending.length) return;
            if (attempt >= RETRY_DELAYS.length && !measuring) {
                pending.forEach(function (card) {
                    if (['loading', 'unknown'].includes(card.dataset.chartState)) {
                        card.dataset.chartState = 'error';
                        root.dispatchEvent(new CustomEvent('metric-summary', { detail: { card: card } }));
                    }
                });
                return;
            }
            timer = setTimeout(function () {
                timer = null;
                if (disposed) return;
                if (!document.hidden) { run(pending); return; }
                // Polling stops while the tab is hidden and resumes with it.
                waiting = function () {
                    if (document.hidden) return;
                    document.removeEventListener('visibilitychange', waiting); waiting = null;
                    run(pending);
                };
                document.addEventListener('visibilitychange', waiting);
            }, RETRY_DELAYS[Math.min(attempt++, RETRY_DELAYS.length - 1)]);
        }

        function dispose() {
            disposed = true;
            if (timer) clearTimeout(timer);
            if (waiting) document.removeEventListener('visibilitychange', waiting);
            if (request) request.abort();
        }
        context.signal.addEventListener('abort', dispose, { once: true });
        // Primary layout is already rendered. Secondary never owns history readiness.
        run(cards);
        return dispose;
    }

    /* Metrics Overview and Comparison of one studio swap in place, with Back and Forward. Every other link and
       page is an ordinary page load. The flag below tells the Metrics scripts loaded after this one not to mount
       themselves, so the first mount waits for them. */
    var main = document.querySelector('main[data-console-page]');
    var lifecycle = null, unmountPage = null, request = null, generation = 0, ready = Promise.resolve();
    var here = location.href, pending = false, restoring = false;
    var index = (history.state && history.state.trellumNavigation && history.state.trellumNavigation.index) || 0;
    window.TrellumNavigation = { navigate: function (href) { var url = new URL(href, location.href); if (inPair(url)) navigate(url); } };

    // Same studio as the page being left; a query or hash keeps filters and anchors an ordinary page load.
    function inPair(url) {
        var studio = /^(\/s\/[^/]+\/[^/]+)\/metrics(?:\/comparison)?\/?$/.exec(url.pathname);
        return !!studio && url.origin === location.origin && !url.search && !url.hash &&
            new URL(here).pathname.indexOf(studio[1] + '/') === 0;
    }
    function entry(position) {
        return Object.assign({}, history.state || {}, { trellumNavigation: { url: here, index: index, position: position || [scrollX, scrollY] } });
    }
    function savePosition() { if (!pending && !restoring) history.replaceState(entry(), '', location.href); }
    function leave() { return !window.TrellumSettingsForms || window.TrellumSettingsForms.confirmLeave(document); }
    function mark(title, url) {
        document.title = title + ' — trellum';
        if (window.TrellumConsoleShell) window.TrellumConsoleShell.setPageTitle(title);
        document.querySelectorAll('.tl-console-nav-link').forEach(function (link) {
            var path = new URL(link.href).pathname;
            var active = path === url.pathname || (/\/metrics\/?$/.test(path) && /\/metrics\/comparison\/?$/.test(url.pathname));
            link.classList.toggle('active', active);
            if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
        });
        var close = document.querySelector('[data-console-close]');
        if (close) close.click();
    }
    function unmount() {
        if (lifecycle) lifecycle.abort();
        if (unmountPage) unmountPage();
        lifecycle = unmountPage = null;
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.unmount();
    }
    function mount() {
        lifecycle = new AbortController();
        var signal = lifecycle.signal, overlay = window.TrellumMetricsOverlay.mount(main, signal);
        var workspace = window.TrellumMetricsWorkspace.mount(main, signal), summary = mountSummary({ root: main, signal: signal });
        unmountPage = function () { summary(); overlay(); if (workspace) workspace(); };
        ready = Promise.resolve(workspace && workspace.ready);
        if (window.TrellumSettingsForms) window.TrellumSettingsForms.init(main);
        if (window.TrellumChoices) window.TrellumChoices.init(main);
        if (window.TrellumRecipients) window.TrellumRecipients.init(main);
    }
    function focusHeading() {
        var heading = main.querySelector('h1');
        if (!heading || !heading.getClientRects().length) heading = document.querySelector('[data-console-page-title]');
        if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    }
    // Static, server-authored markup only: scripts, inline handlers and javascript: URLs are never kept.
    function clean(html) {
        var template = document.createElement('template'); template.innerHTML = html;
        template.content.querySelectorAll('script').forEach(function (node) { node.remove(); });
        template.content.querySelectorAll('*').forEach(function (node) {
            Array.from(node.attributes).forEach(function (attribute) {
                if (/^on/i.test(attribute.name) || (/^(href|src|action)$/i.test(attribute.name) && /^\s*javascript:/i.test(attribute.value))) node.removeAttribute(attribute.name);
            });
        });
        return template.content;
    }
    function navigate(url, options) {
        options = options || {};
        if (!options.guarded && !leave()) return;
        if (!options.pop) savePosition();
        if (request) request.abort();
        var id = ++generation; request = new AbortController();
        unmount(); pending = true; here = url.href;
        if (!options.pop) { ++index; history.pushState(entry([0, 0]), '', url.href); }
        mark('Metrics', url);
        main.innerHTML = '<h1>Metrics</h1><p class="tl-console-navigation-status" role="status">Loading Metrics…</p>';
        main.setAttribute('aria-busy', 'true'); focusHeading(); window.scrollTo(0, 0);
        var timeout = setTimeout(function () { if (id === generation) request.abort(); }, 30000);
        fetch(url.href, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal,
            headers: { 'X-Trellum-Navigation': '1', Accept: 'application/json' }
        }).then(function (response) {
            if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('Navigation failed');
            return response.json();
        }).then(function (data) {
            if (id !== generation) return;
            if (new URL(data.url, location.origin).href !== url.href || typeof data.html !== 'string') throw new Error('Invalid fragment');
            main.replaceChildren(clean(data.html)); main.removeAttribute('aria-busy'); mark(data.title, url);
            mount();
            return ready.then(function () {
                if (id !== generation) return;
                pending = false;
                requestAnimationFrame(function () {
                    if (id !== generation) return;
                    if (options.position) window.scrollTo(options.position[0], options.position[1]); else focusHeading();
                    document.dispatchEvent(new CustomEvent('console:navigated', {detail: {url: url.href}}));
                });
            });
        }).catch(function () {
            if (id !== generation) return;
            pending = false; main.removeAttribute('aria-busy');
            var waiting = main.querySelector('.tl-console-navigation-status'); if (waiting) waiting.remove();
            main.insertAdjacentHTML('beforeend', '<p role="alert">This page could not load. Open it directly to retry or sign in again.</p>');
            var link = document.createElement('a'); link.href = url.href; link.dataset.consoleNative = '1';
            link.textContent = 'Open page directly'; main.append(link);
        }).finally(function () { clearTimeout(timeout); });
    }

    // The shared leave guard in settings-forms.js has already run for this click.
    document.addEventListener('click', function (event) {
        var link = event.target.closest('a[href]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey ||
            link.hasAttribute('download') || link.hasAttribute('data-console-native') || (link.target && link.target !== '_self')) return;
        var url = new URL(link.href);
        if (inPair(url)) { event.preventDefault(); navigate(url, { guarded: true }); }
    });
    window.addEventListener('popstate', function (event) {
        if (restoring) { restoring = false; return; }
        var saved = event.state && event.state.trellumNavigation;
        if (!saved || saved.url !== location.href) return; // another history owner
        var url = new URL(saved.url);
        if (!inPair(url)) { location.reload(); return; }
        if (!leave()) { restoring = true; history.go(index - saved.index); return; }
        index = saved.index;
        navigate(url, { pop: true, guarded: true, position: saved.position });
    });
    window.addEventListener('scroll', savePosition, { passive: true });
    window.addEventListener('pagehide', function () { savePosition(); ++generation; if (request) request.abort(); unmount(); });
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) return;
        if (pending) navigate(new URL(here), { pop: true, guarded: true }); else mount();
    });
    // A click that beat the first mount has already started a navigation; that one owns the page.
    function start() { if (generation) return; mount(); history.replaceState(entry(), '', here); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
}());
