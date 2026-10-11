/* One studio comparison; each focused report keeps its own runtime and scale. */
(function () {
    'use strict';
    var root = document.getElementById('mcComparison');
    if (!root) return;
    var state = JSON.parse(document.getElementById('mcState').textContent);
    var from = document.getElementById('mcFrom'), to = document.getElementById('mcTo');
    var status = document.getElementById('mcStatus');
    var frames = [], applying = false, fitRequest = null;
    var keys = ['a', 'b', 'from', 'to', 'split_a', 'split_b'];
    from.value = state.from; to.value = state.to;

    function urlFor(next) {
        var url = new URL(location.href);
        url.search = '';
        keys.forEach(function (key) { if (next[key]) url.searchParams.set(key, next[key]); });
        return url;
    }
    function remember() {
        var url = urlFor(state);
        if (url.href === location.href) return;
        var entry = Object.assign({}, history.state, {scroll: [scrollX, scrollY]});
        history.replaceState(entry, '', location.href);
        history.pushState(entry, '', url.href);
    }
    function fit() {
        if (fitRequest !== null) cancelAnimationFrame(fitRequest);
        fitRequest = null;
        frames.forEach(function (item) {
            if (!item.ready || !item.frame.isConnected) return;
            var doc = item.frame.contentDocument;
            var chart = item.section.querySelector('.fw-chart-container');
            if (chart) chart.style.maxHeight = '420px';
            var bottom = doc.body.scrollHeight;
            Array.from(doc.body.children).forEach(function (el) {
                var css = doc.defaultView.getComputedStyle(el), box = el.getBoundingClientRect();
                if (css.position !== 'fixed' && box.height) bottom = Math.max(bottom, box.bottom + (parseFloat(css.marginBottom) || 0));
            });
            var height = Math.ceil(bottom) + 'px';
            if (item.frame.style.height !== height) item.frame.style.height = height;
        });
    }
    function scheduleFit() {
        if (fitRequest === null) fitRequest = requestAnimationFrame(fit);
    }
    function apply(item) {
        if (!item.ready) return;
        var fw = item.frame.contentWindow.fw;
        var min = state.from || item.min, max = state.to || item.max;
        // Replace the generated main date range, including its propagated
        // copies, so wider shared dates do not intersect a hidden default.
        item.bars.forEach(function (bar) {
            bar.filters.filter(function (fc) { return fc.type === 'date_range'; }).forEach(function (fc) {
                var range = {min: min, max: max + '\uffff'};
                fw.filterEngine.setFilter(bar.dataset_id, fc.id, fc.column, 'range', range);
                Object.keys(bar.propagate_to || {}).forEach(function (ds) {
                    var col = bar.propagate_to[ds][fc.column];
                    if (col) fw.filterEngine.setFilter(ds, 'prop_' + bar.dataset_id + '_' + fc.column + '_' + ds, col, 'range', range);
                });
            });
        });
        var split = state['split_' + item.slot] || 'Total';
        var buttons = Array.from(item.section.querySelectorAll('[data-stack-toggle] .fw-toggle-btn'));
        var button = buttons.find(function (btn) { return btn.textContent.trim() === split; });
        var missing = split !== 'Total' && !button;
        item.missingSplit = missing || item.missingSplit;
        if (missing) button = buttons[0];
        if (button && !button.classList.contains('active')) {
            if (item.frame.contentWindow.Chart.getChart(item.section.querySelector('canvas'))) button.click();
            else buttons.forEach(function (btn) { btn.classList.toggle('active', btn === button); });
        }
        state['split_' + item.slot] = button && button.textContent.trim() !== 'Total' ? button.textContent.trim() : '';
        var count = fw.filterEngine.getFiltered(item.chart.dataset_id).length;
        var metric = (fw.data._metrics || {})[item.id] || {};
        var note = 'Built metric format: ' + (metric.format || 'unknown') + '; aggregation: ' + (metric.agg || 'unknown') + '. ';
        note += 'Available loaded dates: ' + (item.min ? item.min + ' – ' + item.max : 'no time axis') + '. ';
        if (!item.hasTime) note += 'Shared dates do not apply to this snapshot chart. ';
        else if (!count) note += 'No data in the shared date range. ';
        else if (min < item.min || max > item.max) note += 'Shared dates exceed this metric’s available range; only available observations are plotted. ';
        note += 'Filters: ' + (item.hasTime ? min + ' – ' + max : 'none') + '; split: ' + (button ? button.textContent.trim() : 'not available') + '.';
        if (item.missingSplit) note += ' Requested split is unavailable; using the default.';
        item.note.textContent = note;
        scheduleFit();
    }
    function applyAll() {
        applying = true;
        frames.forEach(apply);
        applying = false;
        status.textContent = !state.a || !state.b ? 'Choose two metrics to compare.'
            : frames.some(function (item) { return !item.ready && !item.failed; }) ? 'Loading selected charts…'
            : frames.some(function (item) { return item.ready; }) ? 'Shared dates apply to available time charts. Splits apply to one card.'
            : 'Selected metrics have no available charts. Choose a monitored metric or rebuild the Metrics report.';
    }
    function settle() {
        if (!state.from && frames.every(function (other) { return other.ready || other.failed; })) {
            var ranges = frames.filter(function (other) { return other.ready && other.min; });
            if (ranges.length) {
                state.from = ranges.map(function (other) { return other.min; }).sort()[0];
                state.to = ranges.map(function (other) { return other.max; }).sort().pop();
                from.value = state.from; to.value = state.to;
            }
        }
        applyAll();
        history.replaceState(history.state, '', urlFor(state));
    }
    function failed(item) {
        item.failed = true;
        item.frame.hidden = true;
        clearTimeout(item.timeout);
        if (item.loading) item.loading.disconnect();
        item.note.textContent = 'This metric’s chart is unavailable in the current build.';
        settle();
    }
    function ready(item) {
        if (item.ready || !item.frame.isConnected) return;
        var win = item.frame.contentWindow, doc = item.frame.contentDocument;
        if (!win.fw || !win.fw.data) return;
        var section = doc.getElementById('metric-' + item.id);
        var chartId = section && section.querySelector('canvas') && section.querySelector('canvas').id;
        var chart = (win.fw.data.components || {})[chartId];
        if (!section || !chart || !win.fw.filterEngine.isReady(chart.dataset_id)) return;
        item.section = section; item.chart = chart;
        item.bars = Object.values(win.fw.data.components).filter(function (cfg) { return cfg.type === 'filter_bar'; });
        item.hasTime = item.bars.some(function (bar) {
            return bar.filters.some(function (fc) { return fc.type === 'date_range' &&
                ((bar.dataset_id === chart.dataset_id && fc.column === chart.x) ||
                 (bar.propagate_to || {})[chart.dataset_id] && bar.propagate_to[chart.dataset_id][fc.column] === chart.x); });
        });
        item.bars.forEach(function (bar) {
            bar.filters.filter(function (fc) { return fc.type === 'date_range'; }).forEach(function (fc) {
                win.fw.filterEngine.clearFilter(bar.dataset_id, fc.id);
                Object.keys(bar.propagate_to || {}).forEach(function (ds) {
                    win.fw.filterEngine.clearFilter(ds, 'prop_' + bar.dataset_id + '_' + fc.column + '_' + ds);
                });
            });
        });
        var dates = item.hasTime ? win.fw.filterEngine.getFiltered(chart.dataset_id).map(function (row) {
            return String(row[chart.x]).slice(0, 10);
        }).sort() : [];
        item.min = dates[0] || ''; item.max = dates[dates.length - 1] || '';
        item.ready = true;
        item.loading.disconnect(); clearTimeout(item.timeout);
        item.frame.style.visibility = '';
        item.frame.dataset.ready = '1';
        // Fit before paint: another animation frame briefly scrolls a new legend.
        item.resize = new ResizeObserver(fit); item.resize.observe(doc.body);
        section.addEventListener('click', function (event) {
            if (event.target.closest('[data-stack-toggle] .fw-toggle-btn')) item.missingSplit = false;
        });
        var originalWrite = win.fw.urlSync.write;
        win.fw.urlSync.write = function () {
            originalWrite.call(this);
            if (!item.frame.isConnected || applying) return;
            var active = section.querySelector('[data-stack-toggle] .fw-toggle-btn.active');
            state['split_' + item.slot] = active && active.textContent.trim() !== 'Total' ? active.textContent.trim() : '';
            applyAll(); remember();
        };
        // Wait for both loaded ranges before choosing a common initial range.
        settle();
    }
    Array.from(root.querySelectorAll('[data-embed]')).forEach(function (card) {
        if (!card.dataset.embed) return;
        var destination = new URL(card.dataset.embed, location.href);
        if (destination.origin !== location.origin || !destination.pathname.startsWith(location.pathname.split('/metrics')[0] + '/r/')) return;
        var frame = document.createElement('iframe'); frame.style.visibility = 'hidden';
        frame.title = card.querySelector('h2').textContent + ' chart';
        var item = {frame: frame, slot: card.dataset.slot, id: card.dataset.id, note: card.querySelector('.mc-range')};
        frames.push(item);
        frame.addEventListener('load', function () {
            if (!frame.isConnected) return;
            try {
                var resolved = new URL(frame.contentWindow.location.href);
                if (resolved.origin !== destination.origin || !resolved.pathname.startsWith(destination.pathname)
                    || !frame.contentDocument.body.classList.contains('fw-only')) throw new Error('unavailable');
                var style = frame.contentDocument.createElement('style');
                style.textContent = '.fw-kpi-grid,.fw-filter-bar{display:none!important}';
                frame.contentDocument.head.appendChild(style);
                item.loading = new MutationObserver(function () { ready(item); });
                item.loading.observe(frame.contentDocument.body, {childList: true, subtree: true});
                ready(item);
            } catch (error) { failed(item); }
        });
        item.timeout = setTimeout(function () {
            if (item.ready) return;
            failed(item);
        }, 12000);
        frame.src = destination.href;
        card.querySelector('.mc-chart').appendChild(frame);
    });
    if (!frames.length) settle();
    document.getElementById('mcDates').addEventListener('submit', function (event) {
        event.preventDefault();
        if (!from.value || !to.value || from.value > to.value) { status.textContent = 'Choose a start date before the end date.'; return; }
        state.from = from.value; state.to = to.value;
        applyAll(); remember();
    });
    document.getElementById('mcSelection').addEventListener('submit', function (event) {
        event.preventDefault();
        var a = document.getElementById('mc-a').value, b = document.getElementById('mc-b').value;
        if (a && a === b) { status.textContent = 'Choose two different metrics.'; return; }
        var next = Object.assign({}, state, {a: a, b: b});
        if (a !== state.a) next.split_a = '';
        if (b !== state.b) next.split_b = '';
        location.assign(urlFor(next).href);
    });
    window.addEventListener('popstate', function () {
        var params = new URL(location.href).searchParams;
        keys.forEach(function (key) { state[key] = params.get(key) || ''; });
        from.value = state.from; to.value = state.to;
        frames.forEach(function (item) { item.missingSplit = false; });
        applyAll();
        var position = history.state && history.state.scroll;
        if (position) requestAnimationFrame(function () { scrollTo(position[0], position[1]); });
    });
    window.addEventListener('resize', scheduleFit);
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) return;
        frames.forEach(function (item) {
            if (item.ready && item.resize) item.resize.observe(item.frame.contentDocument.body);
            else if (!item.failed) {
                if (item.loading) item.loading.observe(item.frame.contentDocument.body, {childList: true, subtree: true});
                ready(item);
                item.timeout = setTimeout(function () { if (!item.ready) failed(item); }, 12000);
            }
        });
        applyAll();
    });
    window.addEventListener('pagehide', function () {
        frames.forEach(function (item) {
            clearTimeout(item.timeout);
            if (item.loading) item.loading.disconnect();
            if (item.resize) item.resize.disconnect();
        });
        if (fitRequest !== null) cancelAnimationFrame(fitRequest);
        fitRequest = null;
    });
})();
