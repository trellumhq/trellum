(function () {
var activeSignal;
function listen(target, event, callback) { target.addEventListener(event, callback, {signal: activeSignal}); }
/* Metrics catalog: find a metric, click it, see it (internal planning ticket #114).
 *
 * Two things, both belonging to one page and neither big enough for a file of
 * its own -- same shape as static/report_analytics.js, which carries its
 * page's search and its sorting together. The filter is at the bottom; the
 * overlay is everything above it.
 *
 * The overlay shows one metric's block from the generated metrics report,
 * framed with `?only=metric-<id>` (trellum.rendering.html_builder) so the
 * iframe carries that metric's KPI, its trend with its own breakdown toggle,
 * and the report's shared date range — nothing else. The facts come from the
 * row that was clicked: no fetch, no second copy of the catalog's markup.
 *
 * Loaded by templates/reports/metrics.html only. Same page-local pattern as
 * static/report_analytics.js — a plain script for one management page, not a
 * widget baked into report builds.
 */
function mountOverlay(root, signal) {
    'use strict';

    var modal = root.querySelector("#" + 'metModal');
    var overlay = root.querySelector("#" + 'metOverlay');
    if (!modal || !overlay) return;

    var title = root.querySelector("#" + 'metModalTitle');
    var sub = root.querySelector("#" + 'metModalSub');
    var body = root.querySelector("#" + 'metModalBody');
    var full = root.querySelector("#" + 'metModalFull');
    var overview = root.querySelector("#" + 'metModalOverview');
    var closeBtn = root.querySelector("#" + 'metModalClose');
    var built = modal.dataset.built === '1';
    var lastTrigger = null;
    var frameObserver = null;
    var fitRequest = null;
    var activeFrame = null;
    var frameDeadline = null, frameReader = null;

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : s;
        return d.innerHTML;
    }

    function chartHtml(row) {
        var state = row.dataset.chartState;
        if (state === 'legacy') {
            return '<p class="met-modal-note">This artifact uses the legacy workspace. Rebuild for bounded metric pages. '
                + (row.dataset.legacyUrl ? '<a href="' + esc(row.dataset.legacyUrl) + '">View legacy metric explicitly</a>' : '') + '</p>';
        }
        if (state === 'unbuilt') {
            /* No build to draw from yet. Say that rather than "not
               monitored", which would be a guess. */
            return '<p class="met-modal-note">The Metrics report has not been built yet, '
                + 'so there is no chart to show. Run it and this metric\'s trend appears here.</p>';
        }
        if (state === 'loading' || state === 'unknown') {
            /* The catalog is still reading which charts exist; it keeps
               asking by itself and this panel redraws when the answer lands. */
            return '<p class="met-modal-note" role="status">Loading the chart…</p>';
        }
        if (state === 'error' || state === 'changed') {
            return '<p class="met-modal-note">This metric’s chart is not available right now.</p>';
        }
        var embed = row.dataset.embed;
        if (embed) {
            /* Not loading="lazy": the frame is created only when the overlay
               opens, already on screen, and fitFrame waits on its load. */
            return '<iframe data-workspace-version="' + esc(row.dataset.workspaceVersion || '') + '" src="' + esc(embed) + '" title="'
                + esc(row.dataset.label) + ' — chart"></iframe>';
        }
        if (!built && !state) {
            /* Bound or not, there is no build to draw from yet. Say that
               rather than "not monitored", which would be a guess. */
            return '<p class="met-modal-note">The Metrics report has not been built yet, '
                + 'so there is no chart to show. Run it and this metric\'s trend appears here.</p>';
        }
        return '<p class="met-modal-note"><b>Not monitored:</b> ' + esc(row.dataset.reason)
            + '. Bind it to a dataset in <code>metrics.yaml</code> to chart it here.</p>';
    }

    /* Chart height floor and cap, matching base.css's only-mode rules -- the
       cap is what the chart wants, the floor is where shrinking it stops
       being worth doing and the frame is allowed to scroll instead. */
    var CHART_FLOOR = 140, CHART_CAP = 500;

    /* Short window: the panel stops being a dialog floating on the catalog
       and becomes the screen. Below this there is not enough height to spend
       any of it on a backdrop gutter or a column of facts that are all still
       on the card behind. One query, read
       by the panel (.met-compact), by the framed block (body.fw-compact, so
       the layout lives in base.css beside the rest of only-mode) and by the
       fit below -- no second copy of the number in a stylesheet. */
    var COMPACT = window.matchMedia('(max-height: 560px)');
    function applyCompact(frame) {
        var on = COMPACT.matches;
        modal.classList.toggle('met-compact', on);
        try {
            var b = frame && frame.contentDocument && frame.contentDocument.body;
            if (b) b.classList.toggle('fw-compact', on);
        } catch (e) { /* not reachable yet: the load handler runs this again */ }
    }

    /* The catalog behind a modal is not scrollable content, it is what the
       modal is covering: without this its scrollbar sits alongside the panel
       and the wheel moves the page under it. `overflow: hidden` keeps the
       scroll position, and the gap the vanished scrollbar leaves is padded
       back so nothing shifts sideways as the overlay opens. */
    function lockScroll(on) {
        /* On <html>, not <body>: the portal sets `html { overflow-y: scroll }`
           to keep the gutter reserved, and an overflow on the body only
           reaches the viewport when the root's own is `visible`. Locking the
           body there does nothing at all. */
        var root = document.documentElement;
        if (on) {
            var gap = window.innerWidth - root.clientWidth;
            root.style.overflow = 'hidden';
            if (gap > 0) root.style.paddingRight = gap + 'px';
        } else {
            root.style.overflow = '';
            root.style.paddingRight = '';
        }
    }

    /* The body's height is independent of the iframe viewport in only mode.
       Include collapsed trailing margins (the source notice has one), without
       using root.scrollHeight, which is floored at the previous frame height. */
    function contentHeight(doc) {
        var bottom = doc.body.scrollHeight;
        Array.prototype.forEach.call(doc.body.children, function (el) {
            var style = doc.defaultView.getComputedStyle(el);
            if (style.position === 'fixed') return;
            var rect = el.getBoundingClientRect();
            if (rect.height) bottom = Math.max(bottom, rect.bottom +
                (parseFloat(style.marginBottom) || 0));
        });
        return Math.ceil(bottom);
    }

    function updateLinks(frame) {
        try {
            var url = new URL(frame.contentWindow.location.href);
            var initial = new URL(frame.src);
            if (url.origin !== initial.origin) return;
            var metric = initial.searchParams.get('only');
            // Report entry redirects may change the path; keep the canonical
            // destination and transfer only the framework's current URL state.
            initial.search = url.search;
            initial.searchParams.set('only', metric);
            full.href = initial.href;
            initial.searchParams.delete('only');
            initial.hash = metric;
            overview.href = initial.href;
        } catch (e) { /* An inaccessible/failed frame keeps the initial links. */ }
    }

    /* Shrink only the chart; keep the complete document inside the frame.
       When controls/legends exceed the budget, the modal body is the single
       scroll area. The frame itself never clips controls or gets a scrollbar. */
    function fitFrame(frame) {
        try {
            var doc = frame.contentDocument;
            if (!doc || !doc.body) return;
            applyCompact(frame);
            var bs = window.getComputedStyle(body);
            var chrome = modal.querySelector('.met-modal-head').offsetHeight
                + modal.querySelector('.met-modal-foot').offsetHeight
                + (modal.offsetHeight - modal.clientHeight)
                + parseFloat(bs.paddingTop) + parseFloat(bs.paddingBottom);
            var room = parseFloat(window.getComputedStyle(modal).maxHeight) - chrome;
            var facts = body.querySelector('.met-modal-facts');
            if (bs.flexDirection === 'column' && facts.offsetHeight) {
                room -= facts.offsetHeight + parseFloat(bs.gap);
            }

            var chart = null, boxes = doc.querySelectorAll('.fw-chart-container');
            for (var i = 0; i < boxes.length; i++) {
                if (boxes[i].offsetHeight > 0) { chart = boxes[i]; break; }
            }
            if (chart) {
                var rest = contentHeight(doc) - chart.offsetHeight;
                var budget = Math.max(CHART_FLOOR, Math.min(CHART_CAP, room - rest));
                var cap = Math.round(budget) + 'px';
                if (chart.style.maxHeight !== cap) chart.style.maxHeight = cap;
            }
            var px = contentHeight(doc) + 'px';
            if (frame.style.height !== px) frame.style.height = px;
            updateLinks(frame);
        } catch (e) { /* not reachable: leave it to the stylesheet */ }
    }

    function scheduleFit() {
        if (fitRequest !== null) return;
        fitRequest = window.requestAnimationFrame(function () {
            fitRequest = null;
            if (activeFrame && !modal.hidden) fitFrame(activeFrame);
        });
    }

    function stopWatching() {
        clearTimeout(frameDeadline); frameDeadline = null;
        if (frameReader) frameReader.abort(); frameReader = null;
        if (frameObserver) frameObserver.disconnect();
        frameObserver = null;
        if (fitRequest !== null) window.cancelAnimationFrame(fitRequest);
        fitRequest = null;
        activeFrame = null;
    }

    function watchFrame(frame) {
        activeFrame = frame;
        var modern = frame.dataset.workspaceVersion === '1';
        var failed = false;
        function unavailable() {
            if (failed || activeFrame !== frame || modal.hidden) return;
            failed = true; clearTimeout(frameDeadline);
            if (frameReader) frameReader.abort();
            if (frameObserver) frameObserver.disconnect();
            frame.hidden = true;
            var note = document.createElement('p'); note.className = 'met-modal-note';
            note.setAttribute('role', 'status');
            note.textContent = 'This metric’s chart is unavailable. Close and reopen to retry, or open the metric page explicitly.';
            body.insertBefore(note, frame);
        }
        frameDeadline = setTimeout(unavailable, 12000);
        listen(frame, 'load', function () {
            if (failed || activeFrame !== frame || modal.hidden) return;
            if (frameObserver) frameObserver.disconnect();
            if (frameReader) frameReader.abort();
            frameReader = new AbortController();
            try {
                var doc = frame.contentDocument;
                var connected = false;
                function connect() {
                if (connected || failed || activeFrame !== frame || modal.hidden || frame.contentDocument !== doc) return;
                if (modern && frame.contentWindow._fwWorkspaceState === 'failed') { unavailable(); return; }
                if (modern && frame.contentWindow._fwWorkspaceState !== 'ready') return;
                if (!doc.body.classList.contains('fw-only')) { unavailable(); return; }
                connected = true;
                clearTimeout(frameDeadline); frameDeadline = null;
                frameObserver = new ResizeObserver(scheduleFit);
                frameObserver.observe(doc.body);
                function afterControls() {
                    // Chart URL writes finish after their button handlers.
                    window.setTimeout(function () {
                        if (activeFrame === frame) scheduleFit();
                    }, 0);
                }
                doc.addEventListener('click', afterControls, {signal: frameReader.signal});
                doc.addEventListener('change', afterControls, {signal: frameReader.signal});
                scheduleFit();
                }
                frame.contentWindow.addEventListener('fw-workspace-state', connect, {once: true, signal: frameReader.signal});
                connect();
            } catch (e) { /* no ResizeObserver, or gone: the load fit stands */ }
            scheduleFit();
        });
    }

    listen(window, 'resize', scheduleFit);
    listen(full, 'click', function () { if (activeFrame) updateLinks(activeFrame); });
    listen(overview, 'click', function () { if (activeFrame) updateLinks(activeFrame); });

    var openRow = null, drawnState = null;

    function draw(row) {
        stopWatching();
        var facts = Array.prototype.map.call(
            row.querySelectorAll('.met-fact'), function (f) { return f.outerHTML; }
        ).join('');
        drawnState = row.dataset.chartState;
        body.innerHTML = chartHtml(row) + '<div class="met-modal-facts">' + facts + '</div>';
        var frame = body.querySelector('iframe');
        applyCompact(frame);
        if (frame) watchFrame(frame);
        full.hidden = !row.dataset.chart;
        if (row.dataset.chart) full.href = row.dataset.chart;
        overview.hidden = !row.dataset.overview;
        if (row.dataset.overview) overview.href = row.dataset.overview;
    }

    /* The catalog's own summary request answers after the page is up. If the
       panel was opened before that, redraw it once the state is known. */
    listen(root, 'metric-summary', function (ev) {
        var row = ev.detail && ev.detail.card;
        if (row && row === openRow && !modal.hidden && row.dataset.chartState !== drawnState) draw(row);
    });

    function open(row, trigger) {
        lastTrigger = trigger;
        openRow = row;
        title.textContent = row.dataset.label;
        sub.textContent = row.dataset.metric;
        draw(row);

        overlay.hidden = false;
        modal.hidden = false;
        lockScroll(true);
        closeBtn.focus();
    }

    function close() {
        stopWatching();
        openRow = null;
        modal.hidden = true;
        overlay.hidden = true;
        lockScroll(false);
        body.innerHTML = '';   // stop the iframe loading/animating behind a closed dialog
        if (lastTrigger) lastTrigger.focus({preventScroll: true});
        lastTrigger = null;
    }

    /* The whole card opens it, but the button inside is the real control, so
       keyboard users get one tab stop that does the same thing. Clicks on the
       card's own links and on the "claiming reports" disclosure are left
       alone — they already do something. */
    root.querySelectorAll('.met-card[data-metric]').forEach(function (row) {
        listen(row, 'click', function (ev) {
            if (ev.target.closest('a, summary, details, label, input')) return;
            open(row, row.querySelector('.met-open'));
        });
    });

    listen(closeBtn, 'click', close);
    listen(overlay, 'click', close);
    listen(document, 'keydown', function (ev) {
        if (ev.key === 'Escape' && !modal.hidden) close();
    });
    return function () { lastTrigger = null; close(); };
}

/* Filter the catalog as you type.
 *
 * Its own IIFE, not part of the overlay's: a page that somehow had no modal
 * would still have cards worth filtering, and the overlay bails early.
 *
 * Filters the cards already rendered, which is right up to the 500 the page
 * renders and costs one string compare per card per keystroke over markup the
 * server already sent. Past that the page says it is showing the first 500 and
 * the same box also narrows server-side (`?q=`) after a typing pause.
 */
function mountFilter(root, signal) {
    'use strict';

    var input = root.querySelector("#" + 'metSearch');
    var count = root.querySelector("#" + 'metCount');
    if (count && !count.dataset.idle) count.dataset.idle = count.textContent;
    var empty = root.querySelector("#" + 'metEmpty');
    if (!input || !count || !empty) return;
    var cards = Array.prototype.slice.call(
        root.querySelectorAll('.met-card[data-search]')
    );
    /* Lower-cased once, not once per keystroke. data-search is exactly the
       fields worth matching (see the template) -- name, id, description, tags,
       owner -- so a query never hits the claims zone by accident. */
    var haystacks = cards.map(function (card) {
        return (card.dataset.search || '').toLowerCase();
    });

    /* Nothing else on this page filters the grid — the tag chips are labels,
       not controls. If one ever becomes a control, it belongs in here beside
       the query rather than hiding cards on its own: two things independently
       setting `hidden` would fight over every card they disagree about. */
    function apply() {
        var q = input.value.trim().toLowerCase();
        var shown = 0;
        cards.forEach(function (card, i) {
            var hit = !q || haystacks[i].indexOf(q) !== -1;
            card.hidden = !hit;
            if (hit) shown++;
        });
        empty.hidden = shown > 0;
        /* Silent until there is something to say: announcing "13 of 13" on an
           empty box is noise, and the page header already carries the total. */
        count.textContent = q ? shown + ' of ' + cards.length : (count.dataset.idle || '');
    }

    /* Only for a catalog larger than one page: after ~400 ms without typing,
       ask the server for the matches instead of the rendered subset. */
    var serverUrl = input.dataset.serverFilter, loaded = input.value.trim(), pause = null;
    function narrow() {
        var q = input.value.trim();
        if (q === loaded) return;
        var url = new URL(serverUrl, location.href);
        if (q) url.searchParams.set('q', q);
        try { sessionStorage.setItem('metSearchFocus', '1'); } catch (e) { /* storage blocked */ }
        // A query string is not a console route, so this is an ordinary page load.
        location.assign(url.href);
    }
    if (serverUrl) {
        try {
            if (sessionStorage.getItem('metSearchFocus')) {
                sessionStorage.removeItem('metSearchFocus');
                input.focus(); input.setSelectionRange(input.value.length, input.value.length);
            }
        } catch (e) { /* storage blocked */ }
        signal.addEventListener('abort', function () { clearTimeout(pause); }, {once: true});
    }

    listen(input, 'input', function () {
        apply();
        if (serverUrl) { clearTimeout(pause); pause = setTimeout(narrow, 400); }
    });
    listen(input, 'keydown', function (ev) {
        if (ev.key !== 'Escape') return;
        /* Escape in the box clears the box and NOTHING else. Without this it
           keeps bubbling to the overlay's own Escape handler on document, and
           one keypress both empties the filter and closes an open panel --
           two undos for one press, only one of which was asked for. */
        ev.stopPropagation();
        input.value = '';
        apply();
        if (serverUrl) { clearTimeout(pause); pause = setTimeout(narrow, 400); }
    });
}

window.TrellumMetricsOverlay = { mount: function (root, signal) {
    activeSignal = signal;
    var cleanup = mountOverlay(root, signal);
    mountFilter(root, signal);
    return cleanup || function () {};
} };
if (!window.TrellumNavigation) {
    var fallback = new AbortController();
    var fallbackCleanup = window.TrellumMetricsOverlay.mount(document, fallback.signal);
    window.addEventListener('pagehide', function () { fallback.abort(); fallbackCleanup(); });
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) return;
        fallback = new AbortController();
        fallbackCleanup = window.TrellumMetricsOverlay.mount(document, fallback.signal);
    });
}
}());
