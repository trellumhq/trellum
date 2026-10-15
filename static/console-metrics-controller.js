(function () {
    'use strict';
    /* Report usage is the page's secondary read: the cards are already there
       with their definitions, and this fills in who claims each one, 25
       definitions to a request. It says nothing while it works and shows no
       controls: a read that is partial or fails is simply asked again, a few
       times, slower each time, while the tab is visible. */
    var BATCH = 25, RETRY_MS = [4000, 8000, 16000, 30000, 60000];
    function mountSummary(context) {
        var root = context.root, grid = root.querySelector('.met-grid[data-summary-url]');
        if (!grid) return function () {};
        var cards = Array.from(grid.querySelectorAll('[data-definition-id]'));
        var revision = Number(grid.dataset.revision), epoch = 0, request, timeout, retryTimer, attempt = 0;
        var batches = [];
        for (var at = 0; at < cards.length; at += BATCH) batches.push(cards.slice(at, at + BATCH));
        function el(parent, tag, className, value) {
            var node = document.createElement(tag);
            if (className) node.className = className;
            if (value != null) node.textContent = value;
            if (parent) parent.appendChild(node);
            return node;
        }
        function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
        function two(n) { return (n < 10 ? '0' : '') + n; }
        function built(iso) {
            var date = iso ? new Date(iso) : null;
            if (!date || isNaN(date)) return 'never built';
            return date.getUTCFullYear() + '-' + two(date.getUTCMonth() + 1) + '-' + two(date.getUTCDate()) +
                ' ' + two(date.getUTCHours()) + ':' + two(date.getUTCMinutes());
        }
        function version(claim) { return claim.claimed_version ? 'v' + claim.claimed_version : 'an unknown version'; }
        function unused(card, show) {
            var fact = card.querySelector('.met-fact'), tags = fact.querySelector('.met-tags');
            var chip = tags && tags.querySelector('[data-unused]');
            if (show && !chip) {
                if (!tags) {
                    tags = document.createElement('div'); tags.className = 'met-tags';
                    fact.insertBefore(tags, fact.querySelector('.met-owner'));
                }
                el(tags, 'span', 'ui-chip mute', 'unused').dataset.unused = '1';
            } else if (!show && chip) {
                chip.remove();
                if (!tags.children.length) tags.remove();
            }
        }
        /* The usage block as the catalog has always drawn it, for what is known
           so far. Claims still being indexed show up on a later read, which
           replaces the block without a word about it. */
        function renderUsage(item, card) {
            var usage = card.querySelector('[data-metric-usage]'), claims = item.claims || [];
            var signature = JSON.stringify([item.claim_count, claims]);
            if (usage.dataset.signature === signature) return;
            usage.dataset.signature = signature;
            var open = usage.querySelector('details');
            open = !!(open && open.open);
            usage.replaceChildren();
            unused(card, !claims.length);
            if (!claims.length) { el(usage, 'span', 'ui-chip mute', 'no reports claim this'); return; }
            var total = item.claim_count || claims.length;
            el(usage, 'b', null, plural(total, 'report'));
            if (claims.every(function (claim) { return claim.current; })) {
                el(el(usage, 'div', 'st'), 'span', 'met-dot g').after(' all current');
            } else {
                claims.forEach(function (claim) {
                    var row = el(usage, 'div', 'st');
                    el(row, 'span', 'met-dot ' + (claim.current ? 'g' : 'w'));
                    row.append(' ' + claim.report);
                    if (!claim.current) { row.append(' · '); el(row, 'b', null, 'built against ' + version(claim)); }
                });
            }
            var details = el(usage, 'details', 'met-expand');
            el(details, 'summary', null, plural(total, 'claiming report') + ' — details');
            var table = el(el(details, 'table', 'met-expand-table'), 'tbody'), head = el(table, 'tr');
            ['Report', 'Last build', 'Definition', ''].forEach(function (name) { el(head, 'th', null, name); });
            claims.forEach(function (claim) {
                var row = el(table, 'tr');
                el(row, 'td', null, claim.report);
                el(row, 'td', null, built(claim.built_at));
                var state = el(row, 'td');
                if (claim.current) el(state, 'span', 'ui-chip acc', 'current · v' + card.dataset.version);
                else el(state, 'span', 'ui-chip warn', 'stale — built against ' + version(claim));
                var link = el(el(row, 'td', 'ui-row-actions'), 'a', 'ui-btn ghost', 'open report');
                link.href = claim.report_url;
            });
            if (total > claims.length) {
                var more = el(el(el(table, 'tr'), 'td'), 'a', null, plural(total - claims.length, 'more report') + ' — all claims →');
                more.parentNode.colSpan = 4; more.href = usage.dataset.claimsUrl;
            }
            if (open) details.open = true;
        }
        function render(item, card) {
            renderUsage(item, card);
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
        }
        /* The definitions changed under the page (a sync landed): fetch the page
           again, once a minute at most so a busy sync cannot loop it. */
        function reloadDefinitions() {
            try {
                var last = Number(sessionStorage.getItem('metricsReloadedAt') || 0);
                if (Date.now() - last < 60000) return;
                sessionStorage.setItem('metricsReloadedAt', String(Date.now()));
            } catch (error) { /* storage unavailable: reload anyway, once per page load */ }
            location.reload();
        }
        async function readBatch(group, signal) {
            var url = new URL(grid.dataset.summaryUrl, location.origin);
            url.searchParams.set('ids', group.map(function (card) { return card.dataset.definitionId; }).join(','));
            url.searchParams.set('definition_revision', revision);
            var response = await fetch(url, { signal: signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
            var data = await response.json();
            if (response.status === 409 && data.state === 'changed') return { changed: true };
            if (!response.ok || data.definition_revision !== revision || !Array.isArray(data.metrics) ||
                data.metrics.length !== group.length || !['ready', 'partial'].includes(data.state)) throw new Error('Invalid summary');
            var byId = new Map(data.metrics.map(function (item) { return [String(item.id), item]; }));
            if (group.some(function (card) { return !byId.has(card.dataset.definitionId); })) throw new Error('Missing summary');
            return { byId: byId, complete: data.state === 'ready' };
        }
        var unsettled = batches.slice();
        async function load() {
            if (!unsettled.length || context.signal.aborted || !grid.isConnected) return;
            if (document.hidden) { document.addEventListener('visibilitychange', load, { once: true, signal: context.signal }); return; }
            var mine = ++epoch;
            if (request) request.abort(); request = new AbortController();
            var signal = request.signal, timedOut = false, left = [];
            unsettled.forEach(function (group) { group.forEach(function (card) { if (!card.dataset.chartState || card.dataset.chartState === 'unknown') card.dataset.chartState = 'loading'; }); });
            try {
                for (var i = 0; i < unsettled.length; i++) {
                    var group = unsettled[i], result;
                    clearTimeout(timeout);
                    timeout = setTimeout(function () { if (mine === epoch) { timedOut = true; request.abort(); } }, 15000);
                    try { result = await readBatch(group, signal); }
                    catch (error) {
                        if (mine !== epoch || context.signal.aborted || (signal.aborted && !timedOut)) return;
                        group.forEach(function (card) { card.dataset.chartState = 'error'; });
                        left.push(group); continue;
                    }
                    if (mine !== epoch || context.signal.aborted || signal.aborted || !grid.isConnected) return;
                    if (result.changed) {
                        group.forEach(function (card) { card.dataset.chartState = 'changed'; });
                        reloadDefinitions(); return;
                    }
                    group.forEach(function (card) { render(result.byId.get(card.dataset.definitionId), card); });
                    if (!result.complete) left.push(group);
                }
                unsettled = left;
            } finally { if (mine === epoch && timeout) { clearTimeout(timeout); timeout = null; } }
            if (left.length && attempt < RETRY_MS.length) retryTimer = setTimeout(load, RETRY_MS[attempt++]);
        }
        context.signal.addEventListener('abort', function () { ++epoch; clearTimeout(timeout); clearTimeout(retryTimer); if (request) request.abort(); }, { once: true });
        // Primary layout is already rendered. Secondary never owns history readiness.
        load();
        return function () { ++epoch; clearTimeout(timeout); clearTimeout(retryTimer); if (request) request.abort(); };
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
