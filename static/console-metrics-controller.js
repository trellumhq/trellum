(function () {
    'use strict';
    function mountSummary(context) {
        var root = context.root, region = root.querySelector('[data-metric-summary]');
        if (!region) return function () {};
        var cards = Array.from(root.querySelectorAll('[data-definition-id]'));
        var status = region.querySelector('[data-summary-status]');
        var retry = region.querySelector('[data-summary-retry]');
        var reload = region.querySelector('[data-summary-reload]');
        var revision = Number(region.dataset.revision), epoch = 0, request, timeout;
        function text(parent, tag, value) {
            var el = document.createElement(tag); el.textContent = value; parent.appendChild(el); return el;
        }
        function render(item, card) {
            var usage = card.querySelector('[data-metric-usage]');
            usage.replaceChildren();
            text(usage, 'b', item.claim_count + ' indexed report claims');
            if (!item.count_complete) text(usage, 'p', 'Current usage is incomplete or unknown. Indexed claims include historical observations.');
            else if (!item.claim_count) text(usage, 'p', 'No reports claim this definition.');
            item.claims.forEach(function (claim) {
                var row = text(usage, 'div', ''); row.className = 'st';
                var link = text(row, 'a', claim.report); link.href = claim.report_url;
                text(row, 'span', ' · ' + (claim.current ? 'current' : 'unverified or stale') +
                    (claim.source_state !== 'ready' ? ' · source ' + claim.source_state : '') + (!claim.fresh ? ' · stale observation' : ''));
            });
            var details = text(usage, 'a', 'All indexed claims →'); details.href = item.details_url;
            card.dataset.chartState = item.chart_state;
            card.dataset.embed = item.embed_url; card.dataset.chart = item.chart_url;
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
        async function load() {
            if (!cards.length || context.signal.aborted) return;
            var mine = ++epoch;
            if (request) request.abort(); request = new AbortController();
            var signal = request.signal, timedOut = false;
            if (timeout) clearTimeout(timeout);
            timeout = setTimeout(function () { if (mine === epoch) { timedOut = true; request.abort(); } }, 15000);
            status.textContent = 'Report usage is loading. Definitions are ready.'; retry.hidden = true;
            cards.forEach(function (card) { card.dataset.chartState = 'loading'; });
            try {
                var url = new URL(region.dataset.url, location.origin);
                url.searchParams.set('ids', cards.map(function (card) { return card.dataset.definitionId; }).join(','));
                url.searchParams.set('definition_revision', revision);
                var response = await fetch(url, { signal: signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
                var data = await response.json();
                if (mine !== epoch || context.signal.aborted || signal.aborted || !region.isConnected) return;
                if (response.status === 409 && data.state === 'changed') {
                    status.textContent = 'Metrics changed while loading. Reload definitions to retry.';
                    reload.hidden = false; cards.forEach(function (card) { card.dataset.chartState = 'changed'; }); return;
                }
                if (!response.ok || data.definition_revision !== revision || !Array.isArray(data.metrics) ||
                    data.metrics.length !== cards.length || !['ready', 'partial'].includes(data.state)) throw new Error('Invalid summary');
                var byId = new Map(data.metrics.map(function (item) { return [String(item.id), item]; }));
                if (cards.some(function (card) { return !byId.has(card.dataset.definitionId); })) throw new Error('Missing summary');
                cards.forEach(function (card) { render(byId.get(card.dataset.definitionId), card); });
                status.textContent = data.state === 'partial' ? 'Report usage is partial. Some sources are pending or uncertain.' : 'Report usage is ready.';
                reload.hidden = true; retry.hidden = data.state !== 'partial';
            } catch (error) {
                if (mine !== epoch || context.signal.aborted || (signal.aborted && !timedOut)) return;
                status.textContent = 'Report usage could not be loaded. Definitions remain available.'; retry.hidden = false;
                cards.forEach(function (card) { card.dataset.chartState = 'error'; });
            } finally { if (mine === epoch && timeout) { clearTimeout(timeout); timeout = null; } }
        }
        retry.addEventListener('click', load, { signal: context.signal });
        context.signal.addEventListener('abort', function () { ++epoch; if (timeout) clearTimeout(timeout); if (request) request.abort(); }, { once: true });
        if (!cards.length) status.textContent = 'No definitions match this search. Report usage was not requested.';
        // Primary layout is already rendered. Secondary never owns history readiness.
        if (reload.hidden) load();
        return function () { ++epoch; if (timeout) clearTimeout(timeout); if (request) request.abort(); };
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
