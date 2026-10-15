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

        async function readChunk(batch, signal) {
            var timedOut = false, flight = new AbortController();
            var stop = function () { flight.abort(); };
            signal.addEventListener('abort', stop, { once: true });
            var deadline = setTimeout(function () { timedOut = true; flight.abort(); }, 15000);
            try {
                var url = new URL(region.dataset.url, location.origin);
                url.searchParams.set('ids', batch.map(function (card) { return card.dataset.definitionId; }).join(','));
                url.searchParams.set('definition_revision', revision);
                var response = await fetch(url, { signal: flight.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
                var data = await response.json();
                if (signal.aborted || !region.isConnected) return null;
                if (response.status === 409 && data.state === 'changed') return { changed: true };
                if (!response.ok || data.definition_revision !== revision || !Array.isArray(data.metrics) ||
                    data.metrics.length !== batch.length || !['ready', 'partial'].includes(data.state)) throw new Error('Invalid summary');
                var byId = new Map(data.metrics.map(function (item) { return [String(item.id), item]; }));
                if (batch.some(function (card) { return !byId.has(card.dataset.definitionId); })) throw new Error('Missing summary');
                batch.forEach(function (card) { render(byId.get(card.dataset.definitionId), card); });
                return { again: data.state === 'partial' || data.metrics.some(function (item) { return item.chart_state === 'unknown'; }) };
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
            var signal = request.signal, again = [];
            for (var i = 0; i < list.length; i += CHUNK) {
                var batch = list.slice(i, i + CHUNK);
                var result = await readChunk(batch, signal);
                if (signal.aborted || disposed) return;
                if (result && result.changed) { if (reloadOnce()) return; again = []; list.forEach(function (card) { card.dataset.chartState = 'error'; }); return; }
                if (result && result.again) again = again.concat(batch);
            }
            schedule(again);
        }

        function schedule(pending) {
            if (!pending.length) return;
            if (attempt >= RETRY_DELAYS.length) {
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
            }, RETRY_DELAYS[attempt++]);
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
    function mount(context) {
        var overlay = window.TrellumMetricsOverlay.mount(context.root, context.signal);
        var workspace = window.TrellumMetricsWorkspace.mount(context.root, context.signal);
        var summary = mountSummary(context);
        return { unmount: function () { summary(); overlay(); if (workspace) workspace(); },
                 ready: workspace && workspace.ready || Promise.resolve() };
    }
    if (window.TrellumNavigation) window.TrellumNavigation.register('metrics', { mount: mount });
    else {
        var controller = new AbortController(), summary = mountSummary({root: document, signal: controller.signal});
        window.addEventListener('pagehide', function () { controller.abort(); summary(); });
        window.addEventListener('pageshow', function (event) {
            if (event.persisted) { controller = new AbortController(); summary = mountSummary({root: document, signal: controller.signal}); }
        });
    }
}());
