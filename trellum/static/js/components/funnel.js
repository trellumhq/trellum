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
    if (!canvas) return;

    if (cfg.dataset_id) {
        window._fwLiveWrap(id, cfg, function(rows) {
            var gr = window._fwAggregate.groupBy(rows, cfg.label_col, [cfg.value_col]);
            var labels = gr.labels;
            var values = labels.map(function(l) { return gr.map[l][cfg.value_col] || 0; });
            labels.sort(function(a, b) { return (gr.map[b][cfg.value_col] || 0) - (gr.map[a][cfg.value_col] || 0); });
            values = labels.map(function(l) { return gr.map[l][cfg.value_col] || 0; });
            _createFunnel(id, canvas, labels, values, cfg.show_pct);
        });
        return;
    }

    _createFunnel(id, canvas, cfg.labels, cfg.values, cfg.show_pct);
};
