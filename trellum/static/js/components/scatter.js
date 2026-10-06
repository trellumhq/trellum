window._fwRenderers['scatter'] = function renderScatter(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;

    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var xFmt = fmtMap[cfg.x_format] || fmtCompact;
    var yFmt = fmtMap[cfg.y_format] || fmtCompact;

    function _buildChart(datasets) {
        if (window._chartInstances[id]) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        }
        var tc = window.getThemeColors();
        var isBubble = datasets.some(function(ds) {
            return ds.data.some(function(p) { return p.r != null; });
        });

        var axisFont = window._fwFontPx('--font-size-axis', 12);
        var labelFont = window._fwFontPx('--font-size-label', 12);
        window._chartInstances[id] = new Chart(canvas, {
            type: isBubble ? 'bubble' : 'scatter',
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

    if (cfg.dataset_id) {
        canvas.addEventListener('dblclick', function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        });
        window._fwLiveWrap(id, cfg, function(rows) {
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
            _buildChart(datasets);
        });
        return;
    }

    _buildChart(cfg.datasets);
};
