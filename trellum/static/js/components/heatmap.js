window._fwRenderers['heatmap'] = function renderHeatmap(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;

    var enhanced = cfg.heatmap_style === 'sequential-v1';
    var key = document.querySelector('[data-heatmap-key="' + id + '"]');
    if (key && !enhanced) key.replaceChildren();
    var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
    var valFmt = fmtMap[cfg.value_format] || fmtCompact;

    function _resolveScale(supported) {
        if (cfg.color_scale && cfg.color_scale !== 'auto' && Array.isArray(cfg.color_scale))
            return cfg.color_scale;
        var sty = getComputedStyle(document.documentElement);
        if (enhanced && supported) {
            var hue = _parseHex(window.getThemeColors().chart_colors[0] ||
                sty.getPropertyValue('--accent-blue').trim() || '#3b82f6');
            return [_rgb(_lerpColor(hue, [255,255,255], 0.88)),
                    _rgb(_lerpColor(hue, [0,0,0], 0.30))];
        }
        var lo = sty.getPropertyValue('--accent-green').trim() || '#00b894';
        var mid = sty.getPropertyValue('--accent-yellow').trim() || '#fdcb6e';
        var hi = sty.getPropertyValue('--accent-red').trim() || '#d63031';
        return [lo, mid, hi];
    }

    function _rgb(c) { return 'rgb(' + c.join(',') + ')'; }

    function _parseHex(c) {
        if (c.indexOf('rgb') === 0) return c.match(/[\d.]+/g).slice(0,3).map(Number);
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

    function _exact(v) {
        if (v == null) return 'N/A';
        if (cfg.value_format === 'currency') return '$' + String(v);
        if (cfg.value_format === 'percent') return String(v * 100) + '%';
        return String(v);
    }

    function _buildChart(cells, xLabels, yLabels) {
        cells = cells || []; xLabels = xLabels || []; yLabels = yLabels || [];
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
        var supported = cfg.scale_population !== 'unsupported' && cells.every(function(c) {
            return typeof c.v === 'number' && Number.isFinite(c.v) && c.v >= 0;
        });
        var present = new Set(cells.map(function(c) { return JSON.stringify([c.x, c.y]); }));
        var hasMissing = present.size < xLabels.length * yLabels.length;
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

        var stops, missingPattern, surface, border, labelFont;
        function fill(cell) {
            if (!cell || cell.v == null || cell.v <= 0) {
                if (enhanced && supported && cell && cell.v === 0) return _interpolate(stops, 0);
                return 'rgba(0,0,0,0.05)';
            }
            var t = _cellT(cell);
            // Keep unsupported normalization results intact; an undefined
            // color gets the legacy neutral fill rather than invalid RGB.
            return Number.isFinite(t) ? _interpolate(stops, t) : 'rgba(0,0,0,0.05)';
        }
        function updatePresentation(chart) {
            var sty = getComputedStyle(document.documentElement);
            surface = sty.getPropertyValue('--bg-card').trim();
            border = sty.getPropertyValue('--border-color').trim();
            stops = _resolveScale(supported);
            labelFont = window._fwFontPx('--font-size-label', 12);
            var tile = document.createElement('canvas'); tile.width = tile.height = 8;
            var pen = tile.getContext('2d'); pen.fillStyle = surface; pen.fillRect(0,0,8,8);
            pen.strokeStyle = border; pen.beginPath();
            pen.moveTo(-2,2); pen.lineTo(2,-2); pen.moveTo(0,8); pen.lineTo(8,0);
            pen.moveTo(6,10); pen.lineTo(10,6); pen.stroke();
            missingPattern = chart.ctx.createPattern(tile, 'repeat');
            if (!key || !enhanced) return;
            key.replaceChildren();
            function text(cls, content) {
                var el = document.createElement('span'); el.className = cls;
                el.textContent = content; key.appendChild(el); return el;
            }
            if (!cells.length) { text('fw-heatmap-context', 'No matching observations'); return; }
            if (!supported) {
                text('fw-heatmap-context', 'Signed or nonfinite result: legacy colors; no valid nonnegative scale.');
            } else {
                var relative = !!groupStats;
                var varying = relative && Object.keys(groupStats).some(function(k) {
                    return groupStats[k].min !== groupStats[k].max;
                });
                var allZero = !cells.some(function(c) { return c.v > 0; });
                var logFloor = !relative && useLog && maxV <= 1;
                var constant = allZero || logFloor || (!relative && minV === maxV) || (relative && !varying);
                var gradient = text('fw-heatmap-gradient', '');
                gradient.style.background = constant
                    ? _interpolate(stops, relative && !allZero ? 0.5 : 0)
                    : 'linear-gradient(to right,' + stops.join(',') + ')';
                gradient.setAttribute('aria-hidden', 'true');
                if (allZero) text('fw-heatmap-range', 'All observed cells: ' + _exact(0));
                else if (relative) text('fw-heatmap-range', varying
                    ? 'Low (0) – High (1)' : 'Uniform positive groups: midpoint');
                else text('fw-heatmap-range', minV === maxV
                    ? 'All positive cells: ' + _exact(minV)
                    : logFloor ? 'Positive values ≤ ' + _exact(1) + ': low color'
                    : (useLog && minV < 1 ? '≤ ' + _exact(1) : _exact(minV)) + ' – ' + _exact(maxV));
                var context = relative
                    ? (cfg.normalize === 'row' ? 'Row-relative' : 'Column-relative') +
                      ' linear colors; raw units are not comparable by color. Uniform positive groups use midpoint.'
                    : 'Global ' + (useLog ? 'logarithmic' : 'linear') +
                      ' colors across positive values' + (useLog && cfg.log_scale == null ? ' (automatic)' : '') + '.';
                // The legacy positive-only extent and log math remain unchanged.
                context += ' Zero uses the low color.';
                if (!relative && useLog && minV < 1) context += ' Legacy logarithmic floor: 1.';
                text('fw-heatmap-context', context);
            }
            if (hasMissing) {
                var missing = text('fw-heatmap-missing', ''); missing.setAttribute('aria-hidden', 'true');
                text('fw-heatmap-range', 'No observation (—)');
            }
        }
        function slotWidth(chart) { return chart.chartArea.width / (xLabels.length || 1); }
        function rectHeight(chart) {
            return Math.max(0, Math.min(cellH, chart.chartArea.height / (yLabels.length || 1) - 2));
        }
        function foreground(color, ctx) {
            // Resolve explicit CSS colors as the canvas does, then choose the
            // higher-contrast black/white foreground (WCAG relative luminance).
            ctx.fillStyle = color;
            var resolved = ctx.fillStyle;
            var channels = (resolved.match(/[\d.]+/g) || ['255','255','255']).map(Number);
            var rgb = resolved[0] === '#' ? _parseHex(resolved) : channels.slice(0,3);
            if (resolved.indexOf('rgba') === 0) {
                var bg = _parseHex(surface);
                rgb = rgb.map(function(v, i) { return v * channels[3] + bg[i] * (1 - channels[3]); });
            }
            var lin = rgb.map(function(v) { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
            var lum = lin[0] * 0.2126 + lin[1] * 0.7152 + lin[2] * 0.0722;
            return (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? '#000000' : '#ffffff';
        }
        var missingTooltip;
        var presentation = {
            id: 'fwHeatmapPresentation',
            beforeUpdate: updatePresentation,
            beforeDatasetsDraw: function(chart) {
                if (!enhanced || !hasMissing) return;
                var ctx = chart.ctx, a = chart.chartArea, h = rectHeight(chart);
                ctx.save(); ctx.beginPath(); ctx.rect(a.left,a.top,a.width,a.height); ctx.clip();
                ctx.fillStyle = missingPattern;
                // Paint rows behind the real matrix. No synthetic data entries
                // or Cartesian allocation, even for a very sparse matrix.
                yLabels.forEach(function(y) { ctx.fillRect(a.left,chart.scales.y.getPixelForValue(y)-h/2,a.width,h); });
                ctx.restore();
            },
            afterDatasetsDraw: function(chart) {
                if (!enhanced || cfg.show_values === false) return;
                var ctx = chart.ctx, a = chart.chartArea, w = slotWidth(chart)-2, h = rectHeight(chart);
                ctx.save(); ctx.beginPath(); ctx.rect(a.left,a.top,a.width,a.height); ctx.clip();
                ctx.font = labelFont + 'px ' + getComputedStyle(canvas).fontFamily;
                ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                function label(text, x, y, width, height, color) {
                    if (height < labelFont + 6 || ctx.measureText(text).width + 8 > width) return;
                    ctx.fillStyle = color; ctx.fillText(text,x,y);
                }
                chart.getDatasetMeta(0).data.forEach(function(el, i) {
                    var cell = data[i];
                    var center = el.getCenterPoint();
                    label(valFmt(cell.v),center.x,center.y,el.width,el.height,foreground(fill(cell),ctx));
                });
                // Only visit missing coordinates when a dash can fit. Thus
                // work is bounded by drawable pixels, not sparse X × Y size.
                if (hasMissing && h >= labelFont + 6 && w >= ctx.measureText('—').width + 8) {
                    var missingInk = getComputedStyle(canvas).color;
                    yLabels.forEach(function(y) { xLabels.forEach(function(x) {
                        if (!present.has(JSON.stringify([x,y]))) label('—',chart.scales.x.getPixelForValue(x),
                            chart.scales.y.getPixelForValue(y),w,h,missingInk);
                    }); });
                }
                ctx.restore();
            },
            afterEvent: function(chart, args) {
                if (!enhanced || !hasMissing) return;
                var e = args.event, a = chart.chartArea;
                var inside = e.type !== 'mouseout' && e.x >= a.left && e.x <= a.right && e.y >= a.top && e.y <= a.bottom;
                var x = inside && xLabels[chart.scales.x.getValueForPixel(e.x)];
                var y = inside && yLabels[chart.scales.y.getValueForPixel(e.y)];
                var absent = inside && x !== undefined && y !== undefined && !present.has(JSON.stringify([x,y]));
                if (!absent) { if (missingTooltip) missingTooltip.hidden = true; return; }
                if (!missingTooltip) {
                    missingTooltip = document.createElement('div'); missingTooltip.className = 'fw-heatmap-tooltip';
                    canvas.parentElement.appendChild(missingTooltip);
                }
                missingTooltip.textContent = x + ' / ' + y + ': No observation';
                missingTooltip.hidden = false;
                missingTooltip.style.left = Math.max(0,Math.min(e.x+8,chart.width-missingTooltip.offsetWidth)) + 'px';
                missingTooltip.style.top = Math.max(0,Math.min(e.y+8,chart.height-missingTooltip.offsetHeight)) + 'px';
            },
            afterDestroy: function() { if (missingTooltip) missingTooltip.remove(); }
        };

        var data = cells.map(function(c) {
            return { x: c.x, y: c.y, v: c.v };
        });

        var cellW = Math.max(Math.floor(canvas.parentElement.clientWidth / (xLabels.length || 1)) - 2, 10);
        var cellH = Math.max(20, Math.min(40, Math.floor(300 / (yLabels.length || 1))));
        canvas.parentElement.style.height = Math.max(200, (yLabels.length * (cellH + 2)) + 60) + 'px';

        window._chartInstances[id] = new Chart(canvas, {
            type: 'matrix',
            plugins: [presentation],
            data: {
                datasets: [{
                    label: cfg.title || 'Value',
                    data: data,
                    backgroundColor: function(ctx) {
                        return fill(ctx.dataset.data[ctx.dataIndex]);
                    },
                    width: function(ctx) {
                        // Unsupported populations keep their legacy geometry.
                        return enhanced && supported && ctx.chart.chartArea
                            ? Math.max(0.1, slotWidth(ctx.chart)-2) : cellW;
                    },
                    height: function(ctx) {
                        return enhanced && supported && ctx.chart.chartArea ? rectHeight(ctx.chart) : cellH;
                    }
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: false },
                    fwLegend: { display: false },
                    datalabels: { display: false },
                    tooltip: {
                        mode: 'nearest', intersect: enhanced,
                        callbacks: {
                            title: function(items) {
                                var d = items[0].dataset.data[items[0].dataIndex];
                                return d.x + ' / ' + d.y;
                            },
                            label: function(ctx) {
                                var d = ctx.dataset.data[ctx.dataIndex];
                                return enhanced ? _exact(d.v) : (d.v != null ? valFmt(d.v) : 'N/A');
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

    var bindings = window._fwHeatmapBindings || (window._fwHeatmapBindings = {});
    if (bindings[id]) { bindings[id](); delete bindings[id]; }
    if (window._fwMetricDrawLeases && window._fwMetricDrawLeases[id]) {
        window._fwMetricDrawLeases[id](); delete window._fwMetricDrawLeases[id];
    }
    canvas.ondblclick = null;
    if (cfg.dataset_id) {
        canvas.ondblclick = function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        };
        function draw(rows) {
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
        }
        if (window._fwMetricsWorkspace) {
            window._fwLiveWrap(id, cfg, draw);
        } else {
            var unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, draw);
            var onTheme = function() {
                if (document.getElementById(id) !== canvas) {
                    bindings[id](); delete bindings[id]; return;
                }
                draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            };
            window.addEventListener('fw-theme-change', onTheme);
            bindings[id] = function() {
                unsubscribe(); window.removeEventListener('fw-theme-change', onTheme);
                canvas.ondblclick = null;
            };
            draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        }
        return;
    }

    _buildChart(cfg.cells, cfg.x_labels, cfg.y_labels);
};
