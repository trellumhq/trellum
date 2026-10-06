/* Shared doughnut factory */
function _createDoughnut(id, canvas, labels, data, cfCfg, showLabels) {
    if (window._chartInstances[id]) {
        window._chartInstances[id].destroy();
        delete window._chartInstances[id];
    }
    var tc = window.getThemeColors();
    var labelFont = window._fwFontPx('--font-size-label', 12);
    var pal = tc.chart_colors;
    var colors = labels.map(function(_, i) { return pal[i % pal.length]; });
    var bgCard = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
    var opts = {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            legend: { display: true, position: 'right', labels: { color: tc.tick_color, padding: 14, font: { size: labelFont } } },
            tooltip: { callbacks: {
                label: function(ctx) {
                    var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                    var pct = ((ctx.raw / total) * 100).toFixed(1);
                    return ctx.label + ': ' + fmtCompact(ctx.raw) + ' (' + pct + '%)';
                }
            }},
            datalabels: showLabels ? {
                display: function(ctx) {
                    var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                    var pct = (ctx.dataset.data[ctx.dataIndex] / total) * 100;
                    return pct >= 3;  // Only show labels for segments >= 3%
                },
                color: '#fff',
                font: { size: labelFont, weight: 600 },
                formatter: function(value, ctx) {
                    var total = ctx.dataset.data.reduce(function(a, b) { return a + b; }, 0);
                    var pct = ((value / total) * 100).toFixed(1);
                    return pct + '%';
                }
            } : { display: false }
        }
    };
    if (cfCfg && cfCfg.crossFilter && cfCfg.dsId) {
        opts.onClick = function(evt, elements) {
            var chart = window._chartInstances[id];
            if (!chart || !elements.length) return;
            var prev = chart._crossFilterValue || null;
            var clicked = String(chart.data.labels[elements[0].index]);
            if (clicked === prev) {
                chart._crossFilterValue = null;
                window._fwCrossFilter(cfCfg.dsId, cfCfg.crossFilter, null);
            } else {
                chart._crossFilterValue = clicked;
                window._fwCrossFilter(cfCfg.dsId, cfCfg.crossFilter, clicked);
            }
        };
    }
    window._chartInstances[id] = new Chart(canvas, {
        type: 'doughnut',
        data: { labels: labels, datasets: [{ data: data, backgroundColor: colors, borderWidth: 2, borderColor: bgCard, _themeManaged: true }] },
        options: opts
    });
    window._chartInstances[id]._fwThemeManagedFonts = true;
}

window._fwRenderers['doughnut'] = function renderDoughnut(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;

    var dCfCfg = (cfg.crossFilter && cfg.dataset_id)
        ? { crossFilter: cfg.crossFilter, dsId: cfg.dataset_id } : null;
    var showLabels = cfg.show_labels || false;

    if (cfg.dataset_id) {
        canvas.addEventListener('dblclick', function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        });
        window._fwLiveWrap(id, cfg, function(rows) {
            var gr = window._fwAggregate.groupBy(rows, cfg.label_col, [cfg.value_col]);
            _createDoughnut(id, canvas, gr.labels, gr.labels.map(function(l) { return gr.map[l][cfg.value_col] || 0; }), dCfCfg, showLabels);
        });
        return;
    }

    _createDoughnut(id, canvas, cfg.labels, cfg.data, null, showLabels);
};
