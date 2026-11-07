/* Point-entry arithmetic never edits the supplied points or normalized radii. */
function _fwScatterSummaryResult(datasets, summary) {
    var count = 0, valid = true, values = [], scale = 0;
    datasets.forEach(function(ds) {
        ds.data.forEach(function(p) {
            count++;
            if (summary.stat === 'count') return;
            if (!p || typeof p.x !== 'number' || !Number.isFinite(p.x) ||
                    typeof p.y !== 'number' || !Number.isFinite(p.y)) valid = false;
            else { values.push(p[summary.axis]); scale = Math.max(scale, Math.abs(p[summary.axis])); }
        });
    });
    if (summary.stat === 'count') return {value: count, reason: 'Entry count; independent of coordinate completeness.'};
    if (!count) return {value: null, reason: 'No point entries in current result.'};
    if (!valid) return {value: null, reason: 'Every point entry needs finite numeric x and y coordinates.'};
    var result;
    if (summary.stat === 'min' || summary.stat === 'max') {
        result = values.reduce(function(a, b) { return summary.stat === 'min' ? Math.min(a, b) : Math.max(a, b); });
    } else {
        // Add in source units when the absolute bound is safe, preserving
        // small exact sums. Otherwise normalize before compensated addition
        // and rescale only the mean, avoiding cross-point sum overflow.
        var normalized = scale > Number.MAX_VALUE / count;
        var sum = 0, correction = 0;
        values.forEach(function(v) {
            var term = normalized ? v / scale : v;
            var adjusted = term - correction, next = sum + adjusted;
            correction = (next - sum) - adjusted; sum = next;
        });
        result = normalized ? scale * (sum / count) : sum / count;
    }
    // Rounding at floating-point extremes cannot certify a nonfinite result.
    return Number.isFinite(result) ? {value: result, reason: ''}
        : {value: null, reason: 'Coordinate result is not finite at supplied precision.'};
}

function _fwScatterSummary(canvas, cfg, datasets) {
    var id = canvas.id, descriptionId = id + '-scatter-summary-scope';
    var box = document.querySelector('[data-chart-summary="' + id + '"]');
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) {
        return t && t !== descriptionId;
    });
    if (cfg.header_summary) {
        if (!box) {
            var container = canvas.parentElement;
            if (!container) return;
            var before = container.previousElementSibling;
            if (before && before.classList.contains('fw-chart-header-context')) before = before.previousElementSibling;
            var head = document.createElement('div'); head.className = 'fw-chart-head';
            if (before && before.classList.contains('fw-chart-title')) {
                before.before(head); head.appendChild(before); before.style.marginBottom = '';
            } else container.before(head);
            box = document.createElement('div'); box.dataset.chartSummary = id; head.appendChild(box);
        }
        box.className = 'fw-chart-summary fw-scatter-summary';
        var summary = cfg.header_summary, result = _fwScatterSummaryResult(datasets, summary);
        function span(cls, text) {
            var el = document.createElement('span'); el.className = cls; el.textContent = text; return el;
        }
        var text = result.value == null ? '—' : summary.stat === 'count' ? String(result.value)
            : window.getFormatter(summary.format)(result.value);
        var description = span('fw-scatter-summary-description',
            'Current supplied or report-filtered point entries across all datasets, including legend-hidden entries and entries outside zoom. ' +
            'Duplicate observations remain separate; entries are not distinct names, source facts, painted dots or unique people. ' +
            (summary.stat === 'count' ? 'Entry count is independent of coordinate and bubble-size validity. ' :
                'Coordinate pairs only; bubble drawing and size eligibility are not certified. ' +
                (summary.stat === 'mean' ? 'Unweighted arithmetic mean of ' + summary.axis + '. ' : 'Selected coordinate: ' + summary.axis + '. ')) +
            'Warehouse completeness and upstream missingness or precision are not established. ' + result.reason);
        description.id = descriptionId;
        var children = [span('fw-chart-summary-label', summary.label), span('fw-chart-summary-value', text),
            span('fw-scatter-summary-scope', 'Current point entries' + (summary.axis ? ' · ' + summary.axis : ''))];
        if (result.reason) children.push(span('fw-scatter-summary-reason', result.reason));
        children.push(description); box.replaceChildren.apply(box, children);
        box.title = description.textContent; tokens.push(descriptionId);
    } else if (box) {
        var head = box.parentElement;
        box.remove();
        if (head.classList.contains('fw-chart-head')) {
            var title = head.querySelector('.fw-chart-title');
            if (title) { title.style.marginBottom = '4px'; head.replaceWith(title); }
            else if (!head.children.length) head.remove();
        }
    }
    if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
    else canvas.removeAttribute('aria-describedby');
}

window._fwRenderers['scatter'] = function renderScatter(id, cfg) {
    var canvas = document.getElementById(id);
    var bindings = window._fwScatterBindings || (window._fwScatterBindings = new Map());
    if (bindings.has(id)) bindings.get(id)();
    if (window._fwMetricDrawLeases && window._fwMetricDrawLeases[id]) {
        window._fwMetricDrawLeases[id](); delete window._fwMetricDrawLeases[id];
    }
    if (!canvas) return;
    var active = true, unsubscribe = function() {}, lifeOff = null, lease = null;
    function resetZoom() {
        var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
    }
    function current() {
        if (!active || document.getElementById(id) !== canvas) return;
        draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
    }
    var observer = new MutationObserver(function() {
        if (document.getElementById(id) !== canvas) dispose();
    });
    function dispose() {
        active = false; unsubscribe(); observer.disconnect();
        if (lifeOff) lifeOff();
        canvas.removeEventListener('dblclick', resetZoom);
        window.removeEventListener('fw-theme-change', current);
        if (lease) {
            lease();
            if (window._fwMetricDrawLeases[id] === lease) delete window._fwMetricDrawLeases[id];
        }
        if (bindings.get(id) === dispose) bindings.delete(id);
        if (document.getElementById(id) !== canvas) {
            _fwScatterSummary(canvas, {}, []);
            var chart = window._chartInstances[id];
            if (chart && chart.canvas === canvas) { chart.destroy(); delete window._chartInstances[id]; }
        }
    }
    bindings.set(id, dispose);
    observer.observe(document.body, {childList: true, subtree: true});

    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var xFmt = fmtMap[cfg.x_format] || fmtCompact;
    var yFmt = fmtMap[cfg.y_format] || fmtCompact;

    function _buildChart(datasets, rows) {
        if (window._chartInstances[id]) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        }
        if (!cfg.trend_line) delete canvas._fwTrendView;
        var trends = cfg.trend_line ? window._fwTrends.result(cfg, rows, {datasets: datasets}, true, cfg.color_by) : null;
        if (trends) trends.yFormat = cfg.y_format;
        _fwScatterSummary(canvas, cfg, datasets);
        var tc = window.getThemeColors();
        var isBubble = datasets.some(function(ds) {
            return ds.data.some(function(p) { return p.r != null; });
        });

        var axisFont = window._fwFontPx('--font-size-axis', 12);
        var labelFont = window._fwFontPx('--font-size-label', 12);
        window._chartInstances[id] = new Chart(canvas, {
            type: isBubble ? 'bubble' : 'scatter',
            plugins: trends ? [window._fwTrends.plugin(trends)] : [],
            data: { datasets: datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: false,
                scales: {
                    x: {
                        grid: { color: tc.grid_color },
                        ticks: { color: tc.tick_color, font: { size: axisFont }, callback: function(v) { return xFmt(v); } },
                        title: { display: !!cfg.x_col, text: cfg.x_col || '', color: tc.tick_color, font: { size: labelFont } }
                    },
                    y: {
                        grid: { color: tc.grid_color },
                        ticks: { color: tc.tick_color, font: { size: axisFont }, callback: function(v) { return yFmt(v); } },
                        title: { display: !!cfg.y_col, text: cfg.y_col || '', color: tc.tick_color, font: { size: labelFont } }
                    }
                },
                plugins: {
                    legend: { display: datasets.length > 1 },
                    fwLegend: { display: datasets.length > 1 },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                var p = ctx.raw;
                                var s = (ctx.dataset.label || '') + ': (' + xFmt(p.x) + ', ' + yFmt(p.y) + ')';
                                if (p.r != null) s += ' size: ' + fmtCompact(p._rawSize != null ? p._rawSize : p.r);
                                return s;
                            }
                        }
                    },
                    zoom: {
                        zoom: { drag: { enabled: true }, mode: 'xy' },
                        pan: { enabled: true, mode: 'xy' }
                    }
                }
            }
        });
        window._chartInstances[id]._fwThemeManagedAxes = true;
        window._chartInstances[id]._fwThemeManagedFonts = true;
    }

    function draw(rows) {
        if (!active || document.getElementById(id) !== canvas) return;
        if (cfg.dataset_id) {
            var tc = window.getThemeColors();
            var datasets = [];
            if (cfg.color_by) {
                var groups = {}, groupOrder = [];
                for (var i = 0; i < rows.length; i++) {
                    var g = String(rows[i][cfg.color_by] != null ? rows[i][cfg.color_by] : 'Other');
                    if (!groups[g]) { groups[g] = []; groupOrder.push(g); }
                    groups[g].push(rows[i]);
                }
                var sizeVals = cfg.size_col ? rows.map(function(r) { return r[cfg.size_col]; }).filter(function(v) { return v != null; }) : [];
                var sizeMax = sizeVals.length ? Math.max.apply(null, sizeVals) : 1;
                for (var gi = 0; gi < groupOrder.length; gi++) {
                    var gn = groupOrder[gi], gr = groups[gn];
                    var color = tc.chart_colors[gi % tc.chart_colors.length];
                    var pts = gr.map(function(r) {
                        var pt = { x: r[cfg.x_col], y: r[cfg.y_col] };
                        if (cfg.size_col) {
                            var sv = r[cfg.size_col] || 0;
                            pt.r = Math.max(3, Math.sqrt(sv / Math.max(sizeMax, 1)) * 20);
                            pt._rawSize = sv;
                        }
                        return pt;
                    });
                    datasets.push({
                        label: gn, data: pts,
                        backgroundColor: color + '99', borderColor: color, borderWidth: 1,
                        _themeManaged: true, _fillOpacity: '99'
                    });
                }
            } else {
                var color = tc.chart_colors[0];
                var sizeVals2 = cfg.size_col ? rows.map(function(r) { return r[cfg.size_col]; }).filter(function(v) { return v != null; }) : [];
                var sizeMax2 = sizeVals2.length ? Math.max.apply(null, sizeVals2) : 1;
                var pts = rows.map(function(r) {
                    var pt = { x: r[cfg.x_col], y: r[cfg.y_col] };
                    if (cfg.size_col) {
                        var sv = r[cfg.size_col] || 0;
                        pt.r = Math.max(3, Math.sqrt(sv / Math.max(sizeMax2, 1)) * 20);
                        pt._rawSize = sv;
                    }
                    return pt;
                });
                datasets.push({
                    label: cfg.title || 'Data', data: pts,
                    backgroundColor: color + '99', borderColor: color, borderWidth: 1,
                    _themeManaged: true, _fillOpacity: '99'
                });
            }
            _buildChart(datasets, rows);
        } else _buildChart(cfg.datasets);
    }
    if (cfg.dataset_id) {
        canvas.addEventListener('dblclick', resetZoom);
        if (window._fwMetricsWorkspace) {
            // Queue and workspace disposal stay with the existing leased wrapper.
            window._fwLiveWrap(id, cfg, draw);
            lease = window._fwMetricDrawLeases[id];
        } else {
            unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, current);
            window.addEventListener('fw-theme-change', current);
            if (cfg.trend_line) lifeOff = window._fwFilterEngine.subscribeLifecycle(function(event) {
                var engine = window._fwFilterEngine, source = cfg.dataset_id, seen = new Set();
                for (var depth = 0; source && depth < 64 && !seen.has(source); depth++) {
                    seen.add(source);
                    source = engine._scopes[source] && engine._scopes[source].parent;
                }
                if (!seen.has(event.dataset_id)) return;
                if (event.dataset_id !== cfg.dataset_id) engine._cache[cfg.dataset_id] = null;
                if (event.type === 'init' || event.type === 'scope') {
                    unsubscribe(); unsubscribe = engine.subscribe(cfg.dataset_id, id, current);
                }
                current();
            });
            current();
        }
    } else draw();
};
