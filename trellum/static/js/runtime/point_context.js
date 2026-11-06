/* Line-only opt-in; records never participate in plotted-value aggregation. */
(function() {
    'use strict';
    function record(date, window) {
        if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
            date.slice(0, 4) === '0000' || !Number.isFinite(Date.parse(date + 'T00:00:00Z')) || new Date(date + 'T00:00:00Z').toISOString().slice(0, 10) !== date ||
            typeof window !== 'string' || !window.trim() || window.length > 240 || /[\x00-\x1f]/.test(window)) return null;
        return {date: date, window: window};
    }
    window._fwLinePointContextSpec = function(cfg, rows, spec) {
        if (!cfg.point_context || cfg.line_style !== 'adaptive') return spec;
        var contexts = {};
        cfg.point_context.forEach(function(rule) {
            var records;
            if (rows) {
                var byX = new Map();
                rows.forEach(function(row) {
                    var x = String(row[cfg.x]), r = record(row[rule.date_column], row[rule.window_column]);
                    if (!byX.has(x)) byX.set(x, r);
                    else if (JSON.stringify(byX.get(x)) !== JSON.stringify(r)) byX.set(x, null);
                });
                records = spec.labels.map(function(x) { return byX.get(String(x)) || null; });
            } else records = rule.records.map(function(r) { return r ? record(r.date, r.window) : null; });
            contexts[rule.series] = records;
        });
        spec.linePointContext = contexts;
        return spec;
    };
    window._fwLinePointContextTooltip = function(contexts, tooltip) {
        var out = Object.assign({}, tooltip || {}), callbacks = Object.assign({}, out.callbacks || {});
        var originalTitle = callbacks.title, originalLabel = callbacks.label;
        function context(item) { return item && contexts[item.datasetIndex] && contexts[item.datasetIndex][item.dataIndex]; }
        callbacks.title = function(items) {
            var item = items && items[0], r = context(item);
            if (r) return r.date;
            return originalTitle ? originalTitle.call(this, items) : item ? item.label : '';
        };
        callbacks.label = function(item) {
            var label = originalLabel || (window.Chart && Chart.defaults.plugins.tooltip.callbacks.label);
            var text = label ? label.call(this, item) : (item.dataset.label || '') + ': ' + item.formattedValue;
            var r = context(item);
            if (!r) return text;
            // The existing external tooltip joins title lines with spaces. Keep
            // the exact date in its short title and literal window in body lines.
            var at = item.element ? item.element.x : item.chart.width / 2;
            var limit = Math.max(12, Math.floor((Math.max(at, item.chart.width - at) - 48) / 8));
            var lines = Array.isArray(text) ? text.slice() : [text], words = r.window.split(/\s+/), line = '';
            words.forEach(function(word) {
                if (line && line.length + word.length + 1 > limit) { lines.push(line); line = ''; }
                while (word.length > limit) {
                    if (line) { lines.push(line); line = ''; }
                    lines.push(word.slice(0, limit)); word = word.slice(limit);
                }
                line += (line ? ' ' : '') + word;
            });
            if (line) lines.push(line);
            return lines;
        };
        out.callbacks = callbacks;
        return out;
    };
})();
