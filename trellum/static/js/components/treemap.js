window._fwRenderers['treemap'] = function renderTreemap(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var valFmt = fmtMap[cfg.value_format] || fmtCompact;

    function _buildTreemap(rows) {
        if (window._chartInstances[id]) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        }
        var groupCols = cfg.group_cols;
        var valueCol = cfg.value_col;

        var tree = rows.map(function(r) {
            var obj = {};
            for (var g = 0; g < groupCols.length; g++) {
                obj[groupCols[g]] = r[groupCols[g]] != null ? String(r[groupCols[g]]) : '';
            }
            obj._value = r[valueCol] || 0;
            if (cfg.color_col && r[cfg.color_col] != null) obj._color = r[cfg.color_col];
            return obj;
        });

        var colorCol = cfg.color_col;
        var rawColorVals = colorCol ? tree.map(function(t) { return t._color; }).filter(function(v) { return v != null; }) : [];
        var colorIsNumeric = rawColorVals.length > 0 && rawColorVals.every(function(v) {
            return typeof v === 'number' || (typeof v === 'string' && v !== '' && !isNaN(Number(v)));
        });
        var colorVals = colorIsNumeric ? rawColorVals.map(Number) : rawColorVals;
        var cMin = colorIsNumeric && colorVals.length ? Math.min.apply(null, colorVals) : 0;
        var cMax = colorIsNumeric && colorVals.length ? Math.max.apply(null, colorVals) : 1;
        var cRange = cMax - cMin || 1;
        /* Stable category-to-index map for non-numeric color columns */
        var catIndex = {};
        if (!colorIsNumeric) {
            var seen = [];
            rawColorVals.forEach(function(v) { if (seen.indexOf(v) === -1) seen.push(v); });
            seen.forEach(function(v, i) { catIndex[v] = i; });
        }

        function _hexToRgb(hex) {
            if (hex.length === 4) hex = '#' + hex[1]+hex[1]+hex[2]+hex[2]+hex[3]+hex[3];
            return [parseInt(hex.slice(1,3),16), parseInt(hex.slice(3,5),16), parseInt(hex.slice(5,7),16)];
        }

        window._chartInstances[id] = new Chart(canvas, {
            type: 'treemap',
            data: {
                datasets: [{
                    tree: tree,
                    key: '_value',
                    groups: groupCols,
                    borderWidth: 2,
                    borderColor: function() {
                        var sty = getComputedStyle(document.documentElement);
                        return sty.getPropertyValue('--bg-card').trim() || '#ffffff';
                    },
                    spacing: 2,
                    backgroundColor: function(ctx) {
                        var pal = getThemeColors().chart_colors;
                        var n = pal.length;
                        if (!ctx.raw || !ctx.raw._data) return pal[0] + 'dd';
                        if (colorCol && ctx.raw._data.children && ctx.raw._data.children[0]) {
                            var child = ctx.raw._data.children[0];
                            var cv = child._color;
                            if (cv == null) {
                                var giNull = ctx.dataIndex % n;
                                return pal[(giNull * 7) % n] + 'dd';
                            }
                            if (colorIsNumeric) {
                                var t = (Number(cv) - cMin) / cRange;
                                var lo = _hexToRgb(pal[0]);
                                var hi = _hexToRgb(pal[n - 1] || pal[0]);
                                var r = Math.round(lo[0] + (hi[0]-lo[0])*t);
                                var g = Math.round(lo[1] + (hi[1]-lo[1])*t);
                                var b = Math.round(lo[2] + (hi[2]-lo[2])*t);
                                return 'rgba('+r+','+g+','+b+',0.87)';
                            }
                            /* Categorical: stable color per distinct value */
                            var ci = catIndex[cv];
                            if (ci == null) ci = 0;
                            return pal[ci % n] + 'dd';
                        }
                        var gi = ctx.dataIndex % n;
                        var pi = (gi * 7) % n;
                        return pal[pi] + 'dd';
                    },
                    labels: {
                        display: true,
                        align: 'left',
                        position: 'top',
                        color: function(ctx) {
                            var sty = getComputedStyle(document.documentElement);
                            return sty.getPropertyValue('--text-main').trim() || '#2d3436';
                        },
                        font: { size: window._fwFontPx('--font-size-label', 12), weight: 700 },
                        formatter: function(ctx) {
                            if (!ctx || !ctx.raw || !ctx.raw._data) return '';
                            var g = ctx.raw._data.children ? ctx.raw.g : '';
                            return g + '  ' + valFmt(ctx.raw.v);
                        },
                        hoverColor: function() { return getThemeColors().tick_color; }
                    }
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
                                var raw = items[0] && items[0].raw;
                                if (!raw || !raw._data) return '';
                                return raw.g || '';
                            },
                            label: function(ctx) {
                                var raw = ctx.raw;
                                if (!raw) return '';
                                return valFmt(raw.v);
                            }
                        }
                    }
                }
            }
        });
        window._chartInstances[id]._fwThemeManagedFonts = true;
    }

    if (cfg.dataset_id) {
        window._fwLiveWrap(id, cfg, function(rows) {
            _buildTreemap(rows);
        });
        return;
    }

    _buildTreemap(cfg.rows);
};
