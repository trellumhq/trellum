/* Authored UTC calendar context. No rebucketing, aggregation, URL or value writes. */
(function() {
    if (window._fwCalendarCoverage) return;
    var DAY = 86400000;
    function stamp(day) {
        if (typeof day !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
        var value = Date.parse(day + 'T00:00:00Z');
        return Number.isFinite(value) && new Date(value).toISOString().slice(0, 10) === day ? value : null;
    }
    function iso(value) { return new Date(value).toISOString().slice(0, 10); }
    function boundsFor(day, grain) {
        var t = stamp(day), date = new Date(t);
        if (grain === 'daily') return [t,t];
        if (grain === 'weekly') { var start = t - ((date.getUTCDay()+6)%7)*DAY; return [start,start+6*DAY]; }
        var month = date.getUTCMonth();
        if (grain === 'quarterly') month -= month%3;
        date.setUTCMonth(month,1); var first = +date;
        date.setUTCMonth(month+(grain === 'quarterly'?3:1),0);
        return [first,+date];
    }
    function unavailable(reason, ready) { return {status: 'unavailable', reason: reason, ready: ready !== false, buckets: []}; }
    function sourceInfo(d) {
        var e = window._fwFilterEngine, ids = [d.dataset_id], parent = e._scopes[d.dataset_id];
        if (parent) ids.push(parent.parent);
        if (ids.length === 2 && e._scopes[ids[1]]) return {reason: 'Chained scopes are unsupported.'};
        var base = ids[ids.length - 1];
        if (ids.some(function(id) { return e.isLiveDataset(id); })) return {reason: 'Live sources are unsupported.'};
        var configs = Object.assign({}, window._reportData && window._reportData.components,
            (window._scopeCache || {})[window._currentScope] || {});
        var source = Object.values(configs).find(function(c) { return c.dataset_id === base && ['data_source', 'metric_view', 'live_data_source'].includes(c.type); });
        var manifests = window._reportData && window._reportData._chunk_manifests || {};
        if (!source || source.type !== 'data_source' || source.chunk_by || source._chunked || manifests[base] || Object.values(manifests).some(function(m) { return m.dataset_id === base; }))
            return {reason: 'A complete unchunked original snapshot is required; projections and live sources are unsupported.'};
        return {ids: ids, base: base, columnar: e._columnar[base]};
    }
    function rows(d) {
        var e = window._fwFilterEngine;
        // The public exclusion API reads the parent's current filtered population
        // without relying on a scoped cache left behind by source replacement.
        return e._scopes[d.dataset_id] ? e.getFilteredExcluding(d.dataset_id, []) : e.getFiltered(d.dataset_id);
    }
    function compute(d, selectedRows) {
        var e = window._fwFilterEngine, info = sourceInfo(d);
        if (info.reason) return unavailable(info.reason);
        if (!e.isReady(d.dataset_id)) return unavailable('Source snapshot is not available.', false);
        var lo = d.start, hi = d.end, start = stamp(lo), end = stamp(hi);
        if (d.timezone !== 'UTC' || d.week_start !== 'Monday' || !['daily','weekly','monthly','quarterly'].includes(d.grain) ||
                start === null || end === null || start > end || (end - start) / DAY >= 3660)
            return unavailable('Unsupported calendar declaration; use bounded UTC whole days and Monday weeks.');
        if (!Array.isArray(d.input_columns) || d.input_columns.length > 32) return unavailable('Declared input columns are unsupported.');
        if (d.horizon && (typeof d.horizon.name !== 'string' || !d.horizon.name || !Number.isInteger(d.horizon.days) || d.horizon.days < 0 || d.horizon.days > 3660 || stamp(d.horizon.observed_through) === null))
            return unavailable('Horizon requires a name, bounded whole days and an observed-through date.');
        var entries = Object.entries(d.buckets || {});
        if (!entries.length || entries.length > 3660) return unavailable('Authored bucket count must be 1..3660.');
        var previousEnd = null, includedDays = 0;
        for (var entry of entries) {
            var bounds = entry[1];
            if (!entry[0] || entry[0].length > 160 || !Array.isArray(bounds) || bounds.length !== 2 || stamp(bounds[0]) === null || stamp(bounds[1]) === null)
                return unavailable('Bucket keys and Gregorian date bounds are invalid.');
            var a = stamp(bounds[0]), b = stamp(bounds[1]), expected = boundsFor(bounds[0],d.grain);
            if (a !== expected[0] || b !== expected[1] || previousEnd !== null && a <= previousEnd || b < start || a > end)
                return unavailable('Buckets require full canonical ordered calendar bounds.');
            previousEnd = b; includedDays += (Math.min(b,end)-Math.max(a,start))/DAY+1;
        }
        if (includedDays !== (end-start)/DAY+1) return unavailable('Bucket mapping does not cover the exact declared reporting interval.');
        var cols = info.columnar;
        if (!cols || cols._data.length > 10000) return unavailable('Source exceeds 10,000 facts or is absent.');
        var relevant = [d.date_column, d.bucket_column].concat(d.input_columns || []);
        if (relevant.some(function(c) { return !cols._cols.includes(c); })) return unavailable('Declared source columns are missing.');
        var indices = relevant.map(function(c) { return cols._cols.indexOf(c); });
        var bytes = 0;
        for (var fact of cols._data) {
            var values = indices.map(function(index, i) {
                var dict = (cols._dict || {})[relevant[i]];
                return dict ? dict[fact[index]] : fact[index];
            });
            bytes += new TextEncoder().encode(JSON.stringify(values)).length;
            if (bytes > 4 * 1024 * 1024) return unavailable('Relevant source columns exceed 4 MiB.');
            var day = values[0], bounds = typeof values[1] === 'string' && Object.prototype.hasOwnProperty.call(d.buckets,values[1]) ? d.buckets[values[1]] : null;
            if (stamp(day) === null || day < d.start || day > d.end || !bounds || day < bounds[0] || day > bounds[1])
                return unavailable('Original dates or exact bucket keys disagree with the authored mapping.');
        }
        for (var id of info.ids) {
            for (var filter of Object.values(e.getFilterState(id))) {
                if (filter.column !== d.date_column) continue;
                var value = filter.value;
                if (filter.mode !== 'range') {
                    if (filter.mode === 'equals' && value === '__all__' || filter.mode === 'in' && Array.isArray(value) && !value.length) continue;
                    return unavailable('Date context requires canonical daily range filters.');
                }
                if (value == null) value = {};
                if (typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(function(k) { return !['min','max'].includes(k); }))
                    return unavailable('Date filter bounds are unsupported.');
                if (value.min && value.max && value.min > value.max) return unavailable('Date filter bounds are reversed.');
                for (var bound of ['min', 'max']) {
                    var v = value[bound];
                    if (v == null || v === '') continue;
                    if (stamp(v) === null) return unavailable('Date filters require genuine Gregorian whole days.');
                    if (bound === 'min' && v > lo) lo = v;
                    if (bound === 'max' && v < hi) hi = v;
                }
            }
        }
        var state = {status: 'eligible', ready: true, start: lo, end: hi, buckets: [], horizon: d.horizon, inputsKnown: !!d.input_columns.length};
        if (lo > hi) { state.empty = true; return state; }
        var dates = new Map();
        for (var row of selectedRows || rows(d)) {
            var date = row[d.date_column];
            if (date < lo || date > hi) continue;
            var observed = dates.get(date) || {inputs: true};
            if (d.input_columns.some(function(c) { return typeof row[c] !== 'number' || !Number.isFinite(row[c]) || Math.abs(row[c]) > Number.MAX_SAFE_INTEGER; })) observed.inputs = false;
            dates.set(date, observed);
        }
        for (var entry of entries) {
            var fullStart = entry[1][0], fullEnd = entry[1][1], a = lo > fullStart ? lo : fullStart, b = hi < fullEnd ? hi : fullEnd;
            if (a > b) continue;
            var full = (stamp(fullEnd) - stamp(fullStart)) / DAY + 1, selected = (stamp(b) - stamp(a)) / DAY + 1;
            var bucket = {key: entry[0], start: fullStart, end: fullEnd, days: full, includedStart: a, includedEnd: b,
                selected: selected, partial: selected !== full, observed: 0, missing: 0, inputMissing: 0, mature: 0};
            for (var t = stamp(a); t <= stamp(b); t += DAY) {
                var observedDate = dates.get(iso(t));
                if (observedDate) { bucket.observed++; if (!observedDate.inputs) bucket.inputMissing++; }
                if (d.horizon && t + d.horizon.days * DAY <= stamp(d.horizon.observed_through)) bucket.mature++;
            }
            bucket.missing = selected - bucket.observed;
            state.buckets.push(bucket);
        }
        return state;
    }
    function bucketText(state, b) {
        var text = ['Calendar: ' + b.start + ' to ' + b.end + ' (' + b.days + ' days)',
            'Included: ' + b.includedStart + ' to ' + b.includedEnd + ' (' + b.selected + '/' + b.days + (b.partial ? ', partial calendar edge' : ', full calendar bucket') + ')',
            'Dates: ' + b.observed + ' observed, ' + b.missing + ' missing (unknown, never measured zero)'];
        text.push(state.inputsKnown ? 'Declared inputs: ' + b.inputMissing + ' observed dates have missing/null/unsafe inputs; ' + b.missing + ' absent dates unknown' : 'Metric input completeness unknown (not declared)');
        if (state.horizon) text.push('Horizon ' + state.horizon.name + ': ' + state.horizon.days + ' days; ' + b.mature + '/' + b.selected + ' selected dates mature as of ' + state.horizon.observed_through);
        return text;
    }
    function spec(cfg, selectedRows, completed) {
        if (!cfg.bucket_context) return completed;
        var state = compute(cfg.bucket_context, selectedRows);
        if (cfg.dataset_id !== cfg.bucket_context.dataset_id || cfg.x !== cfg.bucket_context.bucket_column || cfg.horizontal || cfg.stacked || cfg.net_color || cfg.sort != null || cfg.top_n != null || cfg.stack_by || cfg.combo)
            state = unavailable('Calendar context requires ordinary unsorted vertical bars on exact canonical bucket keys.');
        var byKey = new Map(state.buckets.map(function(b) { return [b.key, b]; }));
        var expectedKeys = state.buckets.map(function(b) { return b.key; }), previousKey = -1;
        if (state.status === 'eligible' && completed.labels.some(function(key) { var index = expectedKeys.indexOf(key), invalid = index <= previousKey; previousKey = index; return invalid; }))
            state = unavailable('Plotted categories do not match exact authored bucket keys.');
        completed.calendarCoverage = state;
        var tooltip = completed.tooltip || {}, callbacks = tooltip.callbacks || {}, title = callbacks.title;
        completed.tooltip = Object.assign({}, tooltip, {callbacks: Object.assign({}, callbacks, {title: function(items) {
            var original = title ? title.apply(this, arguments) : items.map(function(item) { return item.label; }).filter(function(v, i, a) { return a.indexOf(v) === i; });
            var result = Array.isArray(original) ? original.slice() : [original];
            if (state.status !== 'eligible') return result.concat(['Calendar context unavailable: ' + state.reason]);
            // Identity comes from the actual tooltip's exact category label.
            var bucket = items.length && byKey.get(items[0].label);
            return result.concat(bucket ? bucketText(state, bucket) : ['Calendar context unavailable: unknown bucket key.']);
        }})});
        var external = tooltip.external || window.Chart && Chart.defaults.plugins.tooltip.external;
        if (external) completed.tooltip.external = function(context) {
            var el = document.querySelector('.fw-chart-tooltip');
            function adopt() {
                if (!el) return;
                el.classList.add('fw-calendar-tooltip'); el.dataset.fwCalendarOwner = context.chart.canvas.id;
            }
            adopt(); external.call(this,context);
            // The existing external renderer creates its element on first hover.
            if (!el) { el = document.querySelector('.fw-chart-tooltip'); adopt(); external.call(this,context); }
            if (!el) return;
            if (!context.tooltip || !context.tooltip.opacity) { clearTooltip(context.chart.canvas); return; }
            var minX = window.scrollX+8, minY = window.scrollY+8;
            el.style.left = Math.max(minX,Math.min(parseFloat(el.style.left),minX+document.documentElement.clientWidth-el.offsetWidth-16))+'px';
            el.style.top = Math.max(minY,Math.min(parseFloat(el.style.top),minY+document.documentElement.clientHeight-el.offsetHeight-16))+'px';
        };
        return completed;
    }
    function clearTooltip(canvas) {
        var el = document.querySelector('.fw-chart-tooltip');
        if (el && el.dataset.fwCalendarOwner === canvas.id) {
            el.classList.remove('fw-calendar-tooltip'); delete el.dataset.fwCalendarOwner; el.style.opacity = '0';
        }
    }
    function plugin() {
        return {id:'fwCalendarCoverage',beforeDestroy:function(chart) { clearTooltip(chart.canvas); delete chart.canvas._fwCalendarCoverage; }};
    }
    function bind(canvas, cfg, redraw) {
        if (canvas._fwCalendarDispose) canvas._fwCalendarDispose();
        if (!cfg.bucket_context) return;
        var e = window._fwFilterEngine, scope = window._currentScope;
        var offRecovery = function() {};
        var off = e.subscribeLifecycle(function(event) {
            var info = sourceInfo(cfg.bucket_context);
            if (event.dataset_id !== cfg.dataset_id && !(info.ids || []).includes(event.dataset_id)) return;
            if (event.type === 'remove' && event.dataset_id === cfg.dataset_id) offRecovery();
            if (event.type === 'init' && event.dataset_id === cfg.dataset_id && !(e._subscribers[cfg.dataset_id] || []).some(function(s) { return s.id === canvas.id; })) {
                offRecovery(); offRecovery = e.subscribe(cfg.dataset_id, canvas.id + '-calendar-recovery', function() { redraw(rows(cfg.bucket_context)); });
            }
            redraw(rows(cfg.bucket_context));
        });
        var observer = new MutationObserver(function() {
            if (document.getElementById(canvas.id) !== canvas || window._currentScope !== scope) dispose();
        });
        observer.observe(document.body, {subtree: true, childList: true});
        function dispose() { off(); offRecovery(); observer.disconnect(); delete canvas._fwCalendarCoverage; if (canvas._fwCalendarDispose === dispose) delete canvas._fwCalendarDispose; }
        canvas._fwCalendarDispose = dispose;
    }
    function reconcile(components) {
        document.querySelectorAll('.fw-calendar-coverage').forEach(function(el) {
            var cfg = components[el.id];
            if ((!cfg || cfg.type !== 'calendar_coverage' || cfg !== el._fwCoverageConfig ||
                    JSON.stringify(cfg.coverage) !== el._fwCoverageSignature) && el._fwCoverageDispose) {
                el._fwCoverageDispose(); el.replaceChildren(); el.dataset.coverageReady = 'false';
            }
        });
    }
    function captureError(target, components) {
        var roots = [target].concat(Array.from(target.querySelectorAll('.fw-calendar-coverage,canvas')));
        for (var el of roots) {
            if (!el.getClientRects().length) continue;
            var cfg = components[el.id];
            if (cfg && cfg.type === 'calendar_coverage' && el.dataset.coverageReady !== 'true' ||
                    cfg && cfg.bucket_context && (!el._fwCalendarCoverage || !el._fwCalendarCoverage.ready))
                return 'Calendar coverage is still resolving its source snapshot.';
        }
        return '';
    }
    window._fwCalendarCoverage = {captureError: captureError, plugin: plugin, reconcile: reconcile, compute: compute, spec: spec, bind: bind, rows: rows};
    window._fwRenderers['calendar_coverage'] = function(id, cfg) {
        var el = document.getElementById(id);
        if (!el) return;
        if (el._fwCoverageDispose) el._fwCoverageDispose();
        var e = window._fwFilterEngine, scope = window._currentScope, offRows = function() {};
        function draw() {
            if (document.getElementById(id) !== el || window._currentScope !== scope) { dispose(); return; }
            var state = compute(cfg.coverage); el._fwCoverageState = state;
            el.dataset.coverageReady = String(state.ready); el.replaceChildren();
            function p(text) { var child = document.createElement('p'); child.textContent = text; el.append(child); }
            if (state.status !== 'eligible') { p(cfg.coverage.caption + ' unavailable: ' + state.reason); return; }
            if (state.empty) { p(cfg.coverage.caption + ': empty selected date intersection.'); return; }
            var buckets = state.buckets;
            var observed = buckets.reduce(function(n,b) { return n+b.observed; },0), missing = buckets.reduce(function(n,b) { return n+b.missing; },0);
            p(cfg.coverage.caption + ' · UTC, Monday weeks · ' + state.start + ' to ' + state.end + '. ' + buckets.length + ' ' + cfg.coverage.grain +
                ' buckets; ' + buckets.filter(function(b) { return b.partial; }).length + ' partial calendar edges. ' + observed + ' distinct observed dates; ' + missing + ' missing dates (unknown).');
            var inputs = buckets.reduce(function(n,b) { return n+b.inputMissing; },0);
            p(state.inputsKnown ? 'Declared numeric inputs: ' + inputs + ' observed dates have missing/null/unsafe inputs; absent dates remain unknown. Measured zeros count as observed inputs.' : 'Metric input completeness unknown: no numeric input columns declared.');
            if (state.horizon) p('Declared horizon ' + state.horizon.name + ' (' + state.horizon.days + ' days), observed through ' + state.horizon.observed_through + ': ' +
                buckets.reduce(function(n,b) { return n+b.mature; },0) + ' selected dates mature. Calendar coverage is separate from horizon maturity; no forecast or production completeness is inferred.');
        }
        function subscribe() { offRows(); offRows = e.subscribe(cfg.dataset_id, id, draw); }
        subscribe();
        var offLife = e.subscribeLifecycle(function(event) {
            var info = sourceInfo(cfg.coverage);
            if (event.dataset_id === cfg.dataset_id || (info.ids || []).includes(event.dataset_id)) { subscribe(); draw(); }
        });
        var observer = new MutationObserver(function() { if (document.getElementById(id) !== el || window._currentScope !== scope) dispose(); });
        observer.observe(document.body, {childList: true, subtree: true});
        function dispose() { offRows(); offLife(); observer.disconnect(); delete el._fwCoverageState; delete el._fwCoverageConfig; delete el._fwCoverageSignature; if (el._fwCoverageDispose === dispose) delete el._fwCoverageDispose; }
        el._fwCoverageConfig = cfg; el._fwCoverageSignature = JSON.stringify(cfg.coverage);
        el._fwCoverageDispose = dispose; draw();
    };
})();
