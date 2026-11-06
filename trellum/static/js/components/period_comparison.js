/* Complete bounded UTC daily projection; never fetches or writes filter state. */
(function() {
    'use strict';
    if (window._fwPeriodComparison) return;
    var views = new Map(), DAY = 86400000;
    function date(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || value.slice(0, 4) === '0000') throw Error('Invalid ISO daily source date');
        var time = Date.parse(value + 'T00:00:00Z');
        if (!Number.isFinite(time) || new Date(time).toISOString().slice(0, 10) !== value) throw Error('Invalid Gregorian daily source date');
        return time;
    }
    function dates(window) {
        var start = date(window[0]), end = date(window[1]), out = [];
        for (var t = start; t <= end; t += DAY) out.push(new Date(t).toISOString().slice(0, 10));
        return out;
    }
    function unavailable(reason) { return {value: null, reason: reason, status: 'unavailable'}; }
    function reduce(rows, spec, days, complete, error) {
        if (error) return unavailable(error);
        if (!complete) return unavailable('Incomplete period: required calendar day absent');
        var definition = Object.assign({}, spec.definition), mean = definition.agg === 'calendar_mean';
        if (mean) definition.agg = 'sum';
        var result = window._fwMetricViews.reduceMetric(rows, definition);
        if (mean && result.value !== null) result.value /= days;
        return result;
    }
    function derive(rows, cfg, selected, error) {
        var preset = cfg.presets[selected] || cfg.presets[cfg.default];
        var windows = {}, summary = [], daily = [], byDate = new Map();
        rows.forEach(function(row) {
            var key = row[cfg.date_column];
            if (!byDate.has(key)) byDate.set(key, []);
            byDate.get(key).push(row);
        });
        ['A', 'B'].forEach(function(side) {
            var days = dates(preset[side]);
            windows[side] = {dates: days, rows: [].concat.apply([], days.map(function(d) { return byDate.get(d) || []; })),
                complete: days.every(function(d) { return byDate.has(d); })};
        });
        Object.keys(cfg.metrics).forEach(function(key) {
            var spec = cfg.metrics[key], a = windows.A, b = windows.B;
            var av = reduce(a.rows, spec, a.dates.length, a.complete, error);
            var bv = reduce(b.rows, spec, b.dates.length, b.complete, error), change = null, reason = '';
            if (av.value === null || bv.value === null) reason = 'Comparison unavailable: ' + (av.reason || bv.reason);
            else if (spec.change_measure === 'percentage_points') change = (av.value - bv.value) * 100;
            else if (bv.value === 0) reason = 'Zero baseline: relative change undefined';
            else if (bv.value < 0) reason = 'Negative baseline unsupported for relative change';
            else change = (av.value - bv.value) / bv.value;
            if (change !== null && (!Number.isFinite(change) || Math.abs(change) > Number.MAX_SAFE_INTEGER)) {
                change = null; reason = 'Unsafe comparison';
            }
            summary.push({metric: key, name: spec.name, group: spec.group, A: av.value, B: bv.value,
                change: change, A_reason: av.reason, B_reason: bv.reason, change_reason: reason,
                preset: selected, A_start: preset.A[0], A_end: preset.A[1], B_start: preset.B[0], B_end: preset.B[1],
                A_days: a.dates.length, B_days: b.dates.length});
        });
        for (var i = 0; i < Math.max(windows.A.dates.length, windows.B.dates.length); i++) {
            var row = {day: i + 1};
            ['A', 'B'].forEach(function(side) {
                var original = windows[side].dates[i];
                row[side + '_date'] = original || null;
                row[side + '_window'] = side + ': ' + preset[side][0] + ' – ' + preset[side][1];
                Object.keys(cfg.metrics).forEach(function(key) {
                    if (!cfg.metrics[key].daily) return;
                    var result = !original ? unavailable('Outside declared period') :
                        reduce(byDate.get(original) || [], cfg.metrics[key], 1, byDate.has(original), error);
                    row[key + '_' + side] = result.value;
                    row[key + '_' + side + '_state'] = !original ? 'outside-period' : result.status;
                    row[key + '_' + side + '_reason'] = result.reason;
                });
            });
            daily.push(row);
        }
        var outputs = {}; outputs[cfg.dataset_id] = summary; outputs[cfg.dataset_id + '_daily'] = daily;
        Object.keys(cfg.groups).forEach(function(group) {
            outputs[cfg.dataset_id + '_group_' + group] = summary.filter(function(r) { return r.group === group; });
        });
        return outputs;
    }
    function source(engine, cfg) {
        var scope = engine._scopes[cfg.parent_id], base = scope ? scope.parent : cfg.parent_id;
        var data = engine._columnar[base], report = window._reportData || {};
        var configs = (window._scopeCache || {})[window._currentScope] || report.components || {};
        if (engine._scopes[base] || engine._liveDatasets[base] || engine._liveDatasets[cfg.parent_id] ||
            Object.values(configs).some(function(c) { return (c.type === 'metric_view' || c.type === 'period_comparison') &&
                (c.dataset_id === base || (c.outputs || []).indexOf(base) !== -1); })) throw Error('PeriodComparison requires an ordinary snapshot parent');
        if (!data || !engine.isReady(cfg.parent_id)) throw Error('Period source unavailable');
        if ((report._chunk_manifests || {})[base] || report['_chunks_' + base]) throw Error('Chunked period source unsupported');
        if (Object.values(configs).some(function(c) {return c.type === 'filter_bar' && c.live_dataset &&
            c.propagate_to && (Object.hasOwn(c.propagate_to, base) || Object.hasOwn(c.propagate_to, cfg.parent_id));})) throw Error('Propagated-live period source unsupported');
        if (data._data.length > 10000) throw Error('Period source exceeds 10,000 complete rows');
        if (new Set(data._cols).size !== data._cols.length) throw Error('Period source columns must be unique');
        if (cfg.source_columns.some(function(c) { return data._cols.indexOf(c) < 0; })) throw Error('Required period source column missing');
        var positions = cfg.source_columns.map(function(c) { return data._cols.indexOf(c); });
        var projected = data._data.map(function(row) {
            var out = {};
            cfg.source_columns.forEach(function(c, i) {out[c] = data._dict && data._dict[c] ? data._dict[c][row[positions[i]]] : row[positions[i]];});
            date(out[cfg.date_column]); return out;
        });
        if (new TextEncoder().encode(JSON.stringify(projected)).length > 4 * 1024 * 1024) throw Error('Relevant period source payload exceeds 4 MiB');
        return engine.getFilteredExcluding(cfg.parent_id, []);
    }
    function control(cfg) {
        return Array.from(document.querySelectorAll('.fw-toggle-group[data-toggle-id]')).find(function(n) { return n.dataset.toggleId === cfg.toggle_id; });
    }
    function selected(cfg) {
        var node = control(cfg), active = node && node.querySelector('.fw-toggle-btn.active');
        var value = active && active.textContent.trim();
        return value && Object.hasOwn(cfg.presets, value) ? value : null;
    }
    function dispose(id) {
        var item = views.get(id); if (!item) return;
        views.delete(id); item.rowOff(); item.lifeOff(); item.observer.disconnect();
        var empty = derive([], item.cfg, item.cfg.default, 'PeriodComparison removed');
        item.cfg.outputs.forEach(function(ds) { item.engine.init(ds, empty[ds]); item.engine.notify(ds); item.engine.removeDataset(ds); });
    }
    function register(id, cfg) {
        dispose(id);
        cfg = JSON.parse(JSON.stringify(cfg));
        var engine = window._fwFilterEngine;
        var item = {cfg: cfg, engine: engine, rowOff: function() {}, lifeOff: function() {}, ready: false};
        function update() {
            var rows = [], error = '', choice = selected(cfg);
            try { if (!choice) throw Error('Declared period Toggle unavailable'); rows = source(engine, cfg); }
            catch (e) { error = e.message; }
            item.choice = choice; item.ready = !error; item.reason = error;
            var outputs = derive(rows, cfg, choice || cfg.default, error);
            // Publish every output before notifying any consumer.
            item.publishing = true;
            try { cfg.outputs.forEach(function(ds) { engine.init(ds, outputs[ds]); }); }
            finally { item.publishing = false; }
            cfg.outputs.forEach(function(ds) { engine.notify(ds); });
        }
        function attach() { item.rowOff(); item.rowOff = engine.subscribe(cfg.parent_id, id + ':period', update); }
        item.observer = new MutationObserver(function() {
            if (!document.getElementById(id)) { dispose(id); return; }
            if (selected(cfg) !== item.choice) update();
        });
        views.set(id, item); attach();
        item.lifeOff = engine.subscribeLifecycle(function(event) {
            if (cfg.outputs.indexOf(event.dataset_id) !== -1) {
                if (event.type === 'remove') {item.ready = false; item.reason = 'Period output removed';}
                else if (!item.publishing) update();
                return;
            }
            var scope = engine._scopes[cfg.parent_id];
            if (event.dataset_id !== cfg.parent_id && event.dataset_id !== item.base && !(scope && event.dataset_id === scope.parent)) return;
            attach(); item.base = scope ? scope.parent : cfg.parent_id; update();
        });
        item.base = engine._scopes[cfg.parent_id] ? engine._scopes[cfg.parent_id].parent : cfg.parent_id;
        item.observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['class']});
        update();
    }
    window._fwPeriodComparison = {derive: derive, dispose: dispose, status: function(dataset) {
        var item = Array.from(views.values()).find(function(v) { return v.cfg.outputs.indexOf(dataset) !== -1; });
        return item ? {ready: item.ready, reason: item.reason} : {ready: false, reason: 'Period projection unresolved'};
    }};
    window._fwRenderers.period_comparison = register;
})();
