window._fwRenderers['heatmap'] = function renderHeatmap(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;

    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var valFmt = fmtMap[cfg.value_format] || fmtCompact;

    function _resolveScale() {
        if (cfg.color_scale && cfg.color_scale !== 'auto' && Array.isArray(cfg.color_scale))
            return cfg.color_scale;
        var sty = getComputedStyle(document.documentElement);
        var lo = sty.getPropertyValue('--accent-green').trim() || '#00b894';
        var mid = sty.getPropertyValue('--accent-yellow').trim() || '#fdcb6e';
        var hi = sty.getPropertyValue('--accent-red').trim() || '#d63031';
        return [lo, mid, hi];
    }

    function _parseHex(c) {
        if (c.length === 4) c = '#' + c[1]+c[1]+c[2]+c[2]+c[3]+c[3];
        return [parseInt(c.slice(1,3),16), parseInt(c.slice(3,5),16), parseInt(c.slice(5,7),16)];
    }

    function _lerpColor(a, b, t) {
        return [Math.round(a[0]+(b[0]-a[0])*t), Math.round(a[1]+(b[1]-a[1])*t), Math.round(a[2]+(b[2]-a[2])*t)];
    }

    function _interpolate(stops, t) {
        t = Math.max(0, Math.min(1, t));
        var parsed = stops.map(_parseHex);
        if (parsed.length === 2) {
            var c = _lerpColor(parsed[0], parsed[1], t);
            return 'rgb('+c[0]+','+c[1]+','+c[2]+')';
        }
        var seg = t * (parsed.length - 1);
        var i = Math.min(Math.floor(seg), parsed.length - 2);
        var c = _lerpColor(parsed[i], parsed[i+1], seg - i);
        return 'rgb('+c[0]+','+c[1]+','+c[2]+')';
    }

    function _buildChart(cells, xLabels, yLabels) {
        if (cfg.drop_empty) {
            /* Rows/columns whose every cell is empty or zero are dead space
               (a category that structurally never carries this value).
               Recomputed on every refresh, so a row that becomes non-zero
               under some filter comes back. */
            var liveX = {}, liveY = {};
            cells.forEach(function(c) {
                if (c.v != null && c.v > 0) { liveX[c.x] = true; liveY[c.y] = true; }
            });
            xLabels = xLabels.filter(function(x) { return liveX[x]; });
            yLabels = yLabels.filter(function(y) { return liveY[y]; });
            cells = cells.filter(function(c) { return liveX[c.x] && liveY[c.y]; });
        }
        if (window._chartInstances[id]) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        }
        var tc = window.getThemeColors();
        var axisFont = window._fwFontPx('--font-size-axis', 12);
        var vals = cells.map(function(c) { return c.v; }).filter(function(v) { return v != null && v > 0; });
        var minV = vals.length ? Math.min.apply(null, vals) : 1;
        var maxV = vals.length ? Math.max.apply(null, vals) : 2;
        var useLog = cfg.log_scale != null ? cfg.log_scale : (maxV / Math.max(minV, 1) > 10);
        var logMin = useLog ? Math.log(Math.max(minV, 1)) : minV;
        var logMax = useLog ? Math.log(Math.max(maxV, 1)) : maxV;
        var logRange = logMax - logMin || 1;

        /* normalize: 'row' | 'col' stretches each row/column across its own
           min..max. A matrix whose rows differ structurally in scale (feature
           sizes, spender tiers, cohort day-offsets) is flat per row on a
           global scale -- the largest row owns the palette and the within-row
           pattern, which is what the chart exists to show, disappears.
           Colour becomes relative; the tooltip keeps the raw value. */
        var groupStats = null;
        if (cfg.normalize === 'row' || cfg.normalize === 'col') {
            var keyOf = cfg.normalize === 'row'
                ? function(c) { return c.y; } : function(c) { return c.x; };
            groupStats = {};
            cells.forEach(function(c) {
                if (c.v == null || c.v <= 0) return;
                var k = keyOf(c);
                var s = groupStats[k] || (groupStats[k] = {min: c.v, max: c.v});
                if (c.v < s.min) s.min = c.v;
                if (c.v > s.max) s.max = c.v;
            });
            var _keyOf = keyOf;
            var _normGroup = function(cell) {
                if (cell.v == null || cell.v <= 0) return 0;
                var s = groupStats[_keyOf(cell)];
                if (!s) return 0;
                if (s.max === s.min) return 0.5;   // a truly flat group is mid, not hot
                return (cell.v - s.min) / (s.max - s.min);
            };
        }

        function _norm(v) {
            if (v == null || v <= 0) return 0;
            if (useLog) return (Math.log(v) - logMin) / logRange;
            return (v - minV) / (maxV - minV || 1);
        }

        function _cellT(cell) {
            if (groupStats) return _normGroup(cell);
            return _norm(cell.v);
        }

        var data = cells.map(function(c) {
            return { x: c.x, y: c.y, v: c.v };
        });

        var cellW = Math.max(Math.floor(canvas.parentElement.clientWidth / (xLabels.length || 1)) - 2, 10);
        var cellH = Math.max(20, Math.min(40, Math.floor(300 / (yLabels.length || 1))));
        canvas.parentElement.style.height = Math.max(200, (yLabels.length * (cellH + 2)) + 60) + 'px';

        window._chartInstances[id] = new Chart(canvas, {
            type: 'matrix',
            data: {
                datasets: [{
                    label: cfg.title || 'Value',
                    data: data,
                    backgroundColor: function(ctx) {
                        var v = ctx.dataset.data[ctx.dataIndex];
                        if (!v || v.v == null || v.v <= 0) return 'rgba(0,0,0,0.05)';
                        return _interpolate(_resolveScale(), _cellT(v));
                    },
                    width: function(ctx) { return cellW; },
                    height: function(ctx) { return cellH; }
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: false },
                    fwLegend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function(items) {
                                var d = items[0].dataset.data[items[0].dataIndex];
                                return d.x + ' / ' + d.y;
                            },
                            label: function(ctx) {
                                var d = ctx.dataset.data[ctx.dataIndex];
                                return d.v != null ? valFmt(d.v) : 'N/A';
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'category', labels: xLabels, offset: true,
                        grid: { display: false },
                        ticks: { color: tc.tick_color, font: { size: axisFont }, maxRotation: 45 }
                    },
                    y: {
                        type: 'category', labels: yLabels, offset: true,
                        grid: { display: false },
                        ticks: { color: tc.tick_color, font: { size: axisFont } }
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
            /* Per-cell aggregation: rows that share an (x, y) coordinate
               are summed into a single matrix point. The chartjs-chart-
               matrix plugin renders one rectangle per data entry — if
               multiple entries share the same (x, y) the later one
               overwrites the earlier's color (and tooltips fan out
               across the hidden rows). Aggregating here matches the
               static path and is what callers intuitively expect. */
            var xSet = {}, ySet = {}, xLabels = [], yLabels = [], cellMap = {};
            for (var i = 0; i < rows.length; i++) {
                var r = rows[i];
                var xv = String(r[cfg.x_col] != null ? r[cfg.x_col] : '');
                var yv = String(r[cfg.y_col] != null ? r[cfg.y_col] : '');
                if (!xSet[xv]) { xSet[xv] = true; xLabels.push(xv); }
                if (!ySet[yv]) { ySet[yv] = true; yLabels.push(yv); }
                var key = xv + ' ' + yv;
                var v = +(r[cfg.value_col] || 0);
                cellMap[key] = (cellMap[key] || 0) + v;
            }
            xLabels.sort(); yLabels.sort();
            var cells = [];
            for (var xi = 0; xi < xLabels.length; xi++) {
                for (var yi = 0; yi < yLabels.length; yi++) {
                    var k2 = xLabels[xi] + ' ' + yLabels[yi];
                    if (cellMap[k2] !== undefined) {
                        cells.push({ x: xLabels[xi], y: yLabels[yi], v: cellMap[k2] });
                    }
                }
            }
            _buildChart(cells, xLabels, yLabels);
        });
        return;
    }

    _buildChart(cfg.cells, cfg.x_labels, cfg.y_labels);
};
