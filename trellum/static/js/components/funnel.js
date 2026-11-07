/* Current stage entries only: no cross-stage sum or inferred sequence. */
function _fwFunnelSummaryResult(labels, values, original, summary, groups) {
    if (summary.stat === 'count') return { value: labels.length, reason: 'Entry count; independent of amount completeness.' };
    var amounts = original, selected = values;
    if (summary.stat === 'stage') {
        var matches = [];
        labels.forEach(function(label, i) { if (String(label) === summary.stage) matches.push(i); });
        if (!matches.length) return { value: null, reason: 'Selected stage is absent from current entries.' };
        if (matches.length !== 1) return { value: null, reason: 'Selected stage label is ambiguous across entries.' };
        selected = [values[matches[0]]];
        amounts = groups ? (groups.get(summary.stage) || []) : [original[matches[0]]];
    } else if (!labels.length) return { value: null, reason: 'No stage entries in current result.' };
    function eligible(v) { return typeof v === 'number' && Number.isFinite(v) && v >= 0; }
    if (!amounts.length || !amounts.every(eligible)) {
        return { value: null, reason: 'Supplied stage amounts must be finite, numeric and nonnegative.' };
    }
    if (!selected.every(eligible)) return { value: null, reason: 'Grouped stage amounts must be finite, numeric and nonnegative.' };
    // A sum across different stages can overflow without invalidating max.
    return { value: selected.reduce(function(a, b) { return Math.max(a, b); }, 0), reason: '' };
}

function _fwFunnelSummary(canvas, cfg, labels, values, original, groups) {
    var id = canvas.id, descriptionId = id + '-funnel-summary-scope';
    var box = document.querySelector('[data-chart-summary="' + id + '"]');
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) {
        return t && t !== descriptionId;
    });
    if (cfg.header_summary) {
        var container = canvas.parentElement;
        if (!box) {
            var before = container.previousElementSibling;
            if (before && before.classList.contains('fw-chart-header-context')) before = before.previousElementSibling;
            var head = document.createElement('div'); head.className = 'fw-chart-head';
            if (before && before.classList.contains('fw-chart-title')) {
                before.before(head); head.appendChild(before); before.style.marginBottom = '';
            } else container.before(head);
            box = document.createElement('div'); box.dataset.chartSummary = id; head.appendChild(box);
        }
        box.className = 'fw-chart-summary fw-funnel-summary';
        var summary = cfg.header_summary;
        var result = _fwFunnelSummaryResult(labels, values, original, summary, groups);
        function span(cls, text) {
            var el = document.createElement('span'); el.className = cls; el.textContent = text; return el;
        }
        var text = result.value == null ? '—' : summary.stat === 'count' ? String(result.value)
            : window.getFormatter(summary.format)(result.value);
        var description = span('fw-funnel-summary-description',
            'Current supplied or report-filtered stage entries, including hidden entries and all categories outside the zoom window. ' +
            (summary.stat === 'stage' ? 'Selected displayed label: ' + summary.stage + '. ' :
                summary.stat === 'max' ? 'Maximum single stage-entry count. ' : '') +
            (summary.stat === 'count' ? 'Counts final stage entries, not unique people, distinct labels or original facts. ' :
                'Stage amounts may overlap people; no cross-stage total or conversion is inferred. ') +
            'Warehouse completeness and upstream missingness or precision are not established. ' + result.reason);
        description.id = descriptionId;
        box.replaceChildren(span('fw-chart-summary-label', summary.label), span('fw-chart-summary-value', text),
            span('fw-funnel-summary-scope', 'Current stage entries' + (summary.stat === 'stage' ? ' · ' + summary.stage : '')), span('fw-funnel-summary-reason', result.reason), description);
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

function _createFunnel(id, canvas, labels, values, showPct) {
    if (window._chartInstances[id]) {
        window._chartInstances[id].destroy();
        delete window._chartInstances[id];
    }
    var tc = window.getThemeColors();
    var labelFont = window._fwFontPx('--font-size-label', 12);
    var axisFont = window._fwFontPx('--font-size-axis', 12);
    var colors = tc.chart_colors;
    var bgColors = labels.map(function(_, i) { return colors[i % colors.length] + 'cc'; });
    var borderColors = labels.map(function(_, i) { return colors[i % colors.length]; });
    function shortBar(ctx) {
        var meta = ctx.chart.getDatasetMeta(ctx.datasetIndex);
        var bar = meta && meta.data[ctx.dataIndex];
        if (!bar) return false;
        var width = bar.getProps ? bar.getProps(['width'], true).width : bar.width;
        return width < labelFont * 10;
    }

    var plugins = {
        legend: { display: false },
        fwLegend: { display: false },
        tooltip: {
            callbacks: {
                label: function(ctx) {
                    var val = ctx.raw;
                    var idx = ctx.dataIndex;
                    var pct = idx === 0 ? '100%' : (values[0] > 0
                        ? (val / values[0] * 100).toFixed(1) + '% of top'
                        : '');
                    return ctx.label + ': ' + fmtCompact(val) + (pct ? ' (' + pct + ')' : '');
                }
            }
        },
        datalabels: {
            display: showPct !== false,
            color: function() { return getThemeColors().tick_color; },
            font: { size: labelFont, weight: 600 },
            anchor: function(ctx) { return shortBar(ctx) ? 'end' : 'center'; },
            align: function(ctx) { return shortBar(ctx) ? 'end' : 'center'; },
            offset: 6,
            clamp: true,
            clip: false,
            formatter: function(val, ctx) {
                var idx = ctx.dataIndex;
                var line1 = fmtCompact(val);
                if (idx === 0) return line1;
                var prev = values[idx - 1];
                if (prev > 0) {
                    return line1 + ' (' + (val / prev * 100).toFixed(1) + '%)';
                }
                return line1;
            }
        }
    };

    canvas.parentElement.style.height = Math.max(250, labels.length * 50 + 60) + 'px';

    window._chartInstances[id] = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: bgColors,
                borderColor: borderColors,
                borderWidth: 1,
                borderRadius: 4,
                barPercentage: 0.85,
                categoryPercentage: 0.9,
                _themeManaged: true,
                _funnelSegments: true
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true, maintainAspectRatio: false,
            animation: false,
            plugins: plugins,
            scales: {
                x: {
                    grid: { color: tc.grid_color },
                    ticks: { color: tc.tick_color, font: { size: axisFont }, callback: function(v) { return fmtCompact(v); } }
                },
                y: {
                    grid: { display: false },
                    ticks: { color: tc.tick_color, font: { size: axisFont } }
                }
            }
        }
    });
    window._chartInstances[id]._fwThemeManagedAxes = true;
    window._chartInstances[id]._fwThemeManagedFonts = true;
}

window._fwRenderers['funnel'] = function renderFunnel(id, cfg) {
    var canvas = document.getElementById(id);
    var bindings = window._fwFunnelBindings || (window._fwFunnelBindings = new Map());
    if (bindings.has(id)) bindings.get(id)();
    if (window._fwMetricDrawLeases && window._fwMetricDrawLeases[id]) {
        window._fwMetricDrawLeases[id](); delete window._fwMetricDrawLeases[id];
    }
    if (!canvas) return;
    function draw(rows) {
        if (document.getElementById(id) !== canvas) return;
        if (cfg.dataset_id) {
            // Inspect contributions before legacy groupBy zero-fills/coerces them.
            var groups = cfg.header_summary ? new Map() : null;
            var original = cfg.header_summary ? rows.map(function(row) {
                var key = String(row[cfg.label_col] != null ? row[cfg.label_col] : '');
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(row[cfg.value_col]);
                return row[cfg.value_col];
            }) : null;
            var gr = window._fwAggregate.groupBy(rows, cfg.label_col, [cfg.value_col]);
            var labels = gr.labels;
            var values = labels.map(function(l) { return gr.map[l][cfg.value_col] || 0; });
            labels.sort(function(a, b) { return (gr.map[b][cfg.value_col] || 0) - (gr.map[a][cfg.value_col] || 0); });
            values = labels.map(function(l) { return gr.map[l][cfg.value_col] || 0; });
            _createFunnel(id, canvas, labels, values, cfg.show_pct);
            _fwFunnelSummary(canvas, cfg, labels, values, original, groups);
        } else {
            _createFunnel(id, canvas, cfg.labels, cfg.values, cfg.show_pct);
            _fwFunnelSummary(canvas, cfg, cfg.labels, cfg.values, cfg.values);
        }
    }
    // Assignment owns a single handler and preserves the shared CSV schema.
    var download = canvas.parentElement.querySelector('[data-csv-chart]');
    if (download) download.onclick = function(event) {
        event.stopPropagation();
        var chart = window._chartInstances[id];
        if (!chart) return;
        window._fwDownloadCsv(['label', 'value'], chart.data.labels.map(function(label, i) {
            return [label, chart.data.datasets[0].data[i]];
        }), (document.body.getAttribute('data-report-slug') || 'chart') + '_chart.csv');
    };
    if (cfg.dataset_id) {
        if (window._fwMetricsWorkspace) {
            window._fwLiveWrap(id, cfg, draw);
        } else {
            // Same delivered rows/theme redraw as LiveWrap; one scoped binding
            // also lets off -> on -> off and source/canvas replacement retire it.
            function current() {
                if (document.getElementById(id) !== canvas) { dispose(); return; }
                draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            }
            var unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, current);
            var observer = new MutationObserver(function() {
                if (document.getElementById(id) !== canvas) dispose();
            });
            function dispose() {
                unsubscribe(); window.removeEventListener('fw-theme-change', current); observer.disconnect();
                if (bindings.get(id) === dispose) bindings.delete(id);
                if (document.getElementById(id) !== canvas) {
                    _fwFunnelSummary(canvas, {}, [], [], []);
                    var chart = window._chartInstances[id];
                    if (chart && chart.canvas === canvas) { chart.destroy(); delete window._chartInstances[id]; }
                }
            }
            bindings.set(id, dispose);
            observer.observe(document.body, {childList: true, subtree: true});
            window.addEventListener('fw-theme-change', current);
            current();
        }
    } else {
        draw();
        if (cfg.header_summary) {
            var staticObserver = new MutationObserver(function() {
                if (document.getElementById(id) !== canvas) disposeStatic();
            });
            function disposeStatic() {
                staticObserver.disconnect();
                if (bindings.get(id) === disposeStatic) bindings.delete(id);
                if (document.getElementById(id) !== canvas) {
                    _fwFunnelSummary(canvas, {}, [], [], []);
                    var chart = window._chartInstances[id];
                    if (chart && chart.canvas === canvas) { chart.destroy(); delete window._chartInstances[id]; }
                }
            }
            bindings.set(id, disposeStatic);
            staticObserver.observe(document.body, {childList: true, subtree: true});
        }
    }
};
