/* Bounded calculations over delivered snapshot facts. No fetching or filter writes. */
(function() {
    'use strict';
    if (window._fwMetricViews) return;
    var views = new Map(), minis = new Map(), MAX = Number.MAX_SAFE_INTEGER;
    function columns(cfg) {
        var cols = new Set();
        cfg.groups.forEach(function(g) { if (g.where) cols.add(g.where.column); });
        Object.values(cfg.metrics).forEach(function(m) {
            ['column', 'numerator', 'denominator'].forEach(function(k) { if (m[k]) cols.add(m[k]); });
        });
        return Array.from(cols).sort();
    }
    function safe(value) {
        return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= MAX;
    }
    // Pure reducer: a later bounded comparison can reuse this exact missing/ratio policy.
    function reduceMetric(rows, spec) {
        if (spec.agg === 'count') return {value: rows.length, status: 'available', reason: ''};
        function unavailable(reason) { return {value: null, status: 'unavailable', reason: reason}; }
        if (!rows.length) return unavailable('No matching facts');
        var cols = spec.agg === 'sum' ? [spec.column] : [spec.numerator, spec.denominator];
        var sums = [];
        for (var c = 0; c < cols.length; c++) {
            var sum = 0;
            for (var r = 0; r < rows.length; r++) {
                var input = rows[r][cols[c]];
                if (typeof input === 'number' && Number.isInteger(input) && !Number.isSafeInteger(input))
                    return unavailable('Unsafe numeric input: ' + cols[c] + '; prepare precision-safe units');
                if (!safe(input)) return unavailable('Incomplete or incompatible input: ' + cols[c]);
                sum += rows[r][cols[c]];
                if (!safe(sum)) return unavailable('Unsafe aggregate: ' + cols[c]);
            }
            sums.push(sum);
        }
        if (spec.agg === 'sum') return {value: sums[0], status: 'available', reason: ''};
        if (sums[1] === 0) return unavailable('Zero denominator: ' + spec.denominator);
        var ratio = sums[0] / sums[1];
        return safe(ratio) ? {value: ratio, status: 'available', reason: ''} : unavailable('Unsafe ratio');
    }
    function derive(rows, cfg, error) {
        return cfg.groups.map(function(group) {
            var matching = error ? [] : rows.filter(function(row) {
                return !group.where || group.where.values.indexOf(row[group.where.column]) !== -1;
            });
            var result = {group_id: group.id, group_label: group.label, matching_rows: error ? null : matching.length};
            var notes = [];
            Object.keys(cfg.metrics).forEach(function(key) { result[key] = null; });
            Object.keys(cfg.metrics).forEach(function(key) {
                var metric = error ? {value: null, status: 'unavailable', reason: error} : reduceMetric(matching, cfg.metrics[key]);
                result[key] = metric.value;
                result[key + '_status'] = metric.status;
                result[key + '_reason'] = metric.reason;
                if (metric.reason) notes.push(key + ': ' + metric.reason);
            });
            result.Notes = notes.join('; ');
            return result;
        });
    }
    function source(engine, cfg) {
        var parent = cfg.parent_id, scope = engine._scopes[parent];
        var base = scope ? scope.parent : parent;
        if (engine._scopes[base] || engine._liveDatasets[parent] || engine._liveDatasets[base]
                || Array.from(views.values()).some(function(v) { return v.cfg.dataset_id === base; }))
            throw Error('MetricView requires an ordinary unchunked snapshot parent');
        var data = engine._columnar[base];
        if (!data || !engine.isReady(parent)) throw Error('Source unavailable');
        var report = window._reportData || {};
        var configs = (window._scopeCache || {})[window._currentScope] || report.components || {};
        if (Object.values(configs).some(function(c) {
            return c.type === 'filter_bar' && c.live_dataset && c.propagate_to &&
                (Object.prototype.hasOwnProperty.call(c.propagate_to, parent) || Object.prototype.hasOwnProperty.call(c.propagate_to, base));
        })) throw Error('Propagated-live source unsupported');
        if ((report._chunk_manifests || {})[base] || report['_chunks_' + base])
            throw Error('Chunked source unsupported');
        if (data._data.length > 10000) throw Error('Source exceeds 10,000 complete rows');
        if (new Set(data._cols).size !== data._cols.length) throw Error('Source columns must be unique');
        var required = columns(cfg);
        if (required.some(function(c) { return data._cols.indexOf(c) === -1; })) throw Error('Required source column missing');
        var positions = required.map(function(c) { return data._cols.indexOf(c); });
        var projected = data._data.map(function(row) {
            var out = {};
            required.forEach(function(c, i) {
                var value = row[positions[i]];
                if (data._dict && data._dict[c]) value = data._dict[c][value];
                out[c] = value;
            });
            return out;
        });
        if (new TextEncoder().encode(JSON.stringify(projected)).length > 4 * 1024 * 1024)
            throw Error('Relevant source payload exceeds 4 MiB');
        return {base: base, rows: engine.getFilteredExcluding(parent, [])};
    }
    function disposeView(id) {
        var item = views.get(id);
        if (!item) return;
        views.delete(id);
        item.rowOff(); item.lifeOff();
        // Consumers see an explicit unavailable state before their dataset disappears.
        item.engine.init(item.cfg.dataset_id, derive([], item.cfg, 'MetricView removed'));
        item.engine.notify(item.cfg.dataset_id);
        item.engine.removeDataset(item.cfg.dataset_id);
    }
    function register(id, cfg) {
        var old = views.get(id);
        if (old && old.cfg.dataset_id !== cfg.dataset_id) disposeView(id);
        else if (old) { old.rowOff(); old.lifeOff(); views.delete(id); }
        cfg = JSON.parse(JSON.stringify(cfg));
        var engine = window._fwFilterEngine;
        var item = {cfg: cfg, engine: engine, rowOff: function() {}, lifeOff: function() {}, ready: false};
        views.set(id, item);
        function attach() {
            item.rowOff();
            item.rowOff = engine.subscribe(cfg.parent_id, id + ':metric-view', update);
        }
        function update() {
            var rows = [], error = '';
            try { rows = source(engine, cfg).rows; } catch (e) { error = e.message; }
            item.ready = !error; item.reason = error;
            engine.init(cfg.dataset_id, derive(rows, cfg, error));
            engine.notify(cfg.dataset_id);
        }
        attach();
        item.lifeOff = engine.subscribeLifecycle(function(event) {
            var scope = engine._scopes[cfg.parent_id];
            if (event.dataset_id === cfg.dataset_id) {
                if (event.type === 'remove') { item.ready = false; item.reason = 'Metric result removed'; }
                return; // Own publications never recurse.
            }
            if (event.dataset_id !== cfg.parent_id && event.dataset_id !== item.base &&
                    !(scope && event.dataset_id === scope.parent)) return;
            attach();
            item.base = scope ? scope.parent : cfg.parent_id;
            update();
        });
        item.base = engine._scopes[cfg.parent_id] ? engine._scopes[cfg.parent_id].parent : cfg.parent_id;
        update();
    }
    function disposeMini(id) {
        var owned = minis.get(id);
        if (owned) { owned.off(); owned.lifeOff(); minis.delete(id); }
    }
    function bindMini(id, cfg, paint) {
        disposeMini(id);
        var engine = window._fwFilterEngine, binding = cfg.binding;
        function update() {
            var row = engine.getFiltered(binding.dataset_id).find(function(r) { return r.group_id === binding.group; });
            var reason = !row ? 'Metric result unavailable' : row[binding.metric + '_reason'];
            var available = row && row[binding.metric + '_status'] === 'available' && safe(row[binding.metric]);
            paint(available ? row[binding.metric] : null, available ? '' : reason || 'Metric unavailable');
        }
        var owned = {off: function() {}, lifeOff: function() {}};
        function attach() { owned.off(); owned.off = engine.subscribe(binding.dataset_id, id + ':mini', update); }
        attach();
        owned.lifeOff = engine.subscribeLifecycle(function(event) {
            if (event.dataset_id !== binding.dataset_id) return;
            // init is followed by the view's one ordinary notification.
            if (event.type === 'remove') update();
            if (event.type === 'init') attach();
        });
        minis.set(id, owned); update();
    }
    // DOM removal and scope replacement dispose only subscriptions owned here.
    new MutationObserver(function() {
        views.forEach(function(item, id) {
            if (!document.getElementById(id)) disposeView(id);
        });
        minis.forEach(function(item, id) { if (!document.getElementById(id)) disposeMini(id); });
    }).observe(document.documentElement, {childList: true, subtree: true});
    window._fwMetricViews = {reduceMetric: reduceMetric, derive: derive, bindMini: bindMini,
        disposeMini: disposeMini, dispose: disposeView,
        status: function(dataset) {
            var item = Array.from(views.values()).find(function(v) { return v.cfg.dataset_id === dataset; });
            return item ? {ready: item.ready, reason: item.reason} : {ready: false, reason: 'Metric result unresolved'};
        }};
    window._fwRenderers.metric_view = register;
})();
