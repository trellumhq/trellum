/* Shared chart factory -- called by both static and live modes.
   spec: { type, labels, datasets, stacked, horizontal, showLegend, yFmt, tooltip } */
function _createChart(id, canvas, spec) {
    if (window._chartInstances[id]) {
        window._chartInstances[id].destroy();
        delete window._chartInstances[id];
    }
    var tc = window.getThemeColors();
    var axisFont = window._fwFontPx('--font-size-axis', 12);
    var labelFont = window._fwFontPx('--font-size-label', 12);
    var events = window._reportData && window._reportData._events;
    var annos = window._buildAnnotations ? window._buildAnnotations(events, spec.labels) : {};
    var plugins = {
        legend: { display: false },
        fwLegend: { display: !!spec.showLegend },
        annotation: { annotations: annos },
        zoom: { zoom: { drag: { enabled: true, threshold: 8 }, mode: 'x' } },
        datalabels: spec.showLabels ? {
            display: true,
            color: function() { return tc.tick_color; },
            font: { size: labelFont, weight: 600 },
            anchor: 'end',
            align: 'top',
            formatter: function(v, ctx) {
                // For combo charts, check if this is a line (y1 axis) or bar (y axis)
                if (ctx.dataset.yAxisID === 'y1' && spec.y1Fmt) {
                    return spec.y1Fmt(v);
                }
                return (spec.yFmt || fmtCompact)(v);
            }
        } : { display: false }
    };
    if (spec.tooltip) {
        plugins.tooltip = spec.tooltip;
    } else if (spec.horizontal) {
        /* Horizontal bars: value lives on X, default Chart.js tooltip
           body uses parsed.y which is the category — show parsed.x instead. */
        var _httFmt = spec.yFmt || function(v) { return fmtCompact(v); };
        plugins.tooltip = { callbacks: { label: function(ctx) {
            var lbl = ctx.dataset.label || '';
            return (lbl ? lbl + ': ' : '') + _httFmt(ctx.parsed.x);
        }}};
    }
    var _numFmt = spec.yFmt || function(v) { return fmtCompact(v); };
    var scales = spec.horizontal ? {
        /* horizontal bars: X carries values, Y carries categories */
        x: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, maxRotation: 0,
                      callback: _numFmt } },
        y: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont } } }
    } : {
        /* vertical bars / lines: X carries categories, Y carries values */
        x: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, maxRotation: 0 } },
        y: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, callback: _numFmt } }
    };
    /* Optional fixed value-axis range — value axis is X for horizontal, Y otherwise */
    var _valueAxis = spec.horizontal ? scales.x : scales.y;
    if (spec.yMin !== undefined) _valueAxis.min = spec.yMin;
    if (spec.yMax !== undefined) _valueAxis.max = spec.yMax;
    if (spec.datasets.some(function(ds) { return ds.yAxisID === 'y1'; })) {
        scales.y1 = {
            position: 'right', stacked: false,
            grid: { drawOnChartArea: false, color: tc.grid_color },
            ticks: { color: tc.tick_color, font: { size: axisFont }, callback: spec.y1Fmt || function(v) { return fmtCompact$(v); } }
        };
        if (spec.y1Max !== undefined) scales.y1.max = spec.y1Max;
    }
    if (spec.labels && spec.labels.length > 0 && !spec.horizontal
            && /^\d{4}-\d{2}-\d{2}/.test(String(spec.labels[0]))) {
        scales.x.ticks.callback = function(val, idx) {
            var lbl = this.getLabelForValue(val);
            if (!lbl) return lbl;
            var parts = String(lbl).split('-');
            return parts[1] + '/' + parts[2];
        };
        scales.x.ticks.maxTicksLimit = 15;
    }
    if ((spec.type || 'bar') === 'bar') {
        spec.datasets.forEach(function(ds) {
            if (ds.borderRadius === undefined) ds.borderRadius = 3;
        });
    }
    if ((spec.type === 'line') && spec.labels && spec.labels.length > 400) {
        spec.datasets.forEach(function(ds) {
            if (ds.pointRadius === undefined || ds.pointRadius > 0) ds.pointRadius = 0;
        });
    }
    var chartOpts = {
        responsive: true, maintainAspectRatio: false,
        animation: false,
        indexAxis: spec.horizontal ? 'y' : 'x',
        plugins: plugins,
        scales: scales
    };

    /* ── Cross-filter click handler ── */
    if (spec.crossFilter && spec.dsId) {
        chartOpts.onClick = function(evt, elements) {
            var chart = window._chartInstances[id];
            if (!chart || !elements.length) return;
            var prev = chart._crossFilterValue || null;
            var el = elements[0];
            var clicked;

            if (spec.crossFilterMode === 'dataset') {
                clicked = String(chart.data.datasets[el.datasetIndex].label);
            } else {
                clicked = String(chart.data.labels[el.index]);
            }

            if (clicked === prev) {
                chart._crossFilterValue = null;
                window._fwCrossFilter(spec.dsId, spec.crossFilter, null);
            } else {
                chart._crossFilterValue = clicked;
                window._fwCrossFilter(spec.dsId, spec.crossFilter, clicked);
            }
        };
    }

    window._chartInstances[id] = new Chart(canvas, {
        type: spec.type || 'bar',
        data: { labels: spec.labels, datasets: spec.datasets },
        options: chartOpts
    });
    window._chartInstances[id]._fwThemeManagedAxes = true;
    window._chartInstances[id]._fwThemeManagedFonts = true;
}

window._fwRenderers['chart'] = function renderChart(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;

    /* ── Live mode ─────────────────────────────────────────── */
    if (cfg.dataset_id) {
        var activeStackBy = cfg.stack_by || null;
        if (cfg.stack_by_options) {
            var stackSel = cfg.stack_toggle_id
                ? '[data-toggle-id="' + cfg.stack_toggle_id + '"]'
                : '[data-stack-toggle="' + id + '"]';
            var tog = document.querySelector(stackSel);
            if (tog) {
                var initBtn = tog.querySelector('.fw-toggle-btn.active');
                if (initBtn) activeStackBy = cfg.stack_by_options[initBtn.textContent.trim()] || cfg.stack_by;
                tog.addEventListener('click', function(e) {
                    var btn = e.target.closest('.fw-toggle-btn');
                    if (!btn) return;
                    if (!cfg.stack_toggle_id) {
                        tog.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
                        btn.classList.add('active');
                    }
                    activeStackBy = cfg.stack_by_options[btn.textContent.trim()] || cfg.stack_by;
                    _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
                });
            }
        }
        var normalized = false;
        if (cfg.normalize_toggle) {
            var normSel = cfg.normalize_toggle_id
                ? '[data-toggle-id="' + cfg.normalize_toggle_id + '"]'
                : '[data-normalize-toggle="' + id + '"]';
            var normTog = document.querySelector(normSel);
            if (normTog) {
                var initNormBtn = normTog.querySelector('.fw-toggle-btn.active');
                if (initNormBtn) normalized = initNormBtn.textContent.trim() === 'Percentage';
                normTog.addEventListener('click', function(e) {
                    var btn = e.target.closest('.fw-toggle-btn');
                    if (!btn) return;
                    if (!cfg.normalize_toggle_id) {
                        normTog.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
                        btn.classList.add('active');
                    }
                    normalized = btn.textContent.trim() === 'Percentage';
                    _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
                });
            }
        }
        canvas.addEventListener('dblclick', function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        });

        function _mkSpec(spec) {
            if (cfg.crossFilter && cfg.dataset_id) {
                spec.crossFilter = (cfg.crossFilterMode === 'dataset' && activeStackBy)
                    ? activeStackBy : cfg.crossFilter;
                spec.dsId = cfg.dataset_id;
                if (cfg.crossFilterMode) spec.crossFilterMode = cfg.crossFilterMode;
            }
            if (cfg.y_min !== undefined) spec.yMin = cfg.y_min;
            if (cfg.y_max !== undefined) spec.yMax = cfg.y_max;
            if (cfg.y1_max !== undefined) spec.y1Max = cfg.y1_max;
            if (cfg.show_labels) spec.showLabels = true;
            return spec;
        }

        function _drawLive(rows) {
            var colors = window.getThemeColors().chart_colors;
            var isLine = cfg.chartType === 'line';

            if (activeStackBy && isLine) {
                /* LineChart with stack_by — pivot rows by the stack column,
                   produce one line per distinct value. Works with both
                   value_cols (sum each col across pivot, summed into one
                   line per stack value) and ratios (compute numerator /
                   denominator per (x, stack) cell). Long-format friendly. */
                var MAX_LINES = (cfg.max_stacks != null) ? cfg.max_stacks : 20;
                var pvL = window._fwAggregate.pivot(rows, cfg.x, activeStackBy, cfg.value_cols, cfg.stack_sort);
                var lineDatasets = [];
                var stackLimit = Math.min(pvL.stackNames.length, MAX_LINES);

                if (cfg.ratios) {
                    /* One line per (stack, ratio) — but in practice
                       stack_by is used with ONE ratio (e.g. "iap_revenue
                       / dau" split by platform). With multiple ratios
                       the label is "{stackValue} {ratio.label}". */
                    var multipleRatios = cfg.ratios.length > 1;
                    for (var sli = 0; sli < stackLimit; sli++) {
                        var lname = pvL.stackNames[sli];
                        var lcolData = pvL.datasets[lname];
                        for (var lri = 0; lri < cfg.ratios.length; lri++) {
                            var lspec = cfg.ratios[lri];
                            var lRatData = pvL.labels.map(function(_, lxi) {
                                var lnum = (lcolData[lspec.numerator] || [])[lxi] || 0;
                                var lden = (lcolData[lspec.denominator] || [])[lxi] || 0;
                                return lden > 0 ? lnum / lden : null;
                            });
                            var lcolor = window._fwGetLabelColor(cfg.dataset_id, lname, colors);
                            var llabel = multipleRatios ? (lname + ' · ' + lspec.label) : lname;
                            lineDatasets.push({
                                label: llabel, data: lRatData,
                                backgroundColor: lcolor + '22', borderColor: lcolor,
                                borderWidth: 2.5, pointRadius: 2, tension: 0
                            });
                        }
                    }
                    var ratFmtL = {
                        currency: function(v) { return v == null ? '' : '$' + v.toFixed(2); },
                        percent: function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; },
                        minutes: function(v) { return v == null ? '' : v.toFixed(1) + 'm'; },
                        decimal1: function(v) { return v == null ? '' : v.toFixed(1); }
                    };
                    var yFmtL = ratFmtL[cfg.y_format] || undefined;
                    var ttL = yFmtL ? { callbacks: { label: function(ctx) {
                        return ctx.dataset.label + ': ' + yFmtL(ctx.parsed.y);
                    }}} : undefined;
                    _createChart(id, canvas, _mkSpec({
                        type: 'line', labels: pvL.labels, datasets: lineDatasets,
                        showLegend: true, yFmt: yFmtL, tooltip: ttL, showLabels: cfg.show_labels
                    }));
                } else {
                    /* Sum value_cols into one line per stack value. */
                    for (var sli2 = 0; sli2 < stackLimit; sli2++) {
                        var lname2 = pvL.stackNames[sli2];
                        var lcolData2 = pvL.datasets[lname2];
                        var lineData = pvL.labels.map(function(_, lxi2) {
                            var sum = 0;
                            for (var lci = 0; lci < cfg.value_cols.length; lci++) {
                                sum += (lcolData2[cfg.value_cols[lci]] || [])[lxi2] || 0;
                            }
                            return sum;
                        });
                        var lcolor2 = window._fwGetLabelColor(cfg.dataset_id, lname2, colors);
                        lineDatasets.push({
                            label: lname2, data: lineData,
                            backgroundColor: lcolor2 + '22', borderColor: lcolor2,
                            borderWidth: 2.5, pointRadius: 2, tension: 0
                        });
                    }
                    var fmtMapL = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
                    var yFmtL2 = fmtMapL[cfg.y_format] || fmtCompact;
                    var ttL2 = { callbacks: { label: function(ctx) {
                        return ctx.dataset.label + ': ' + yFmtL2(ctx.parsed.y);
                    }}};
                    _createChart(id, canvas, _mkSpec({
                        type: 'line', labels: pvL.labels, datasets: lineDatasets,
                        showLegend: true, yFmt: yFmtL2, tooltip: ttL2, showLabels: cfg.show_labels
                    }));
                }

            } else if (activeStackBy) {
                /* Pivot-based stacked bar */
                var MAX_STACKS = (cfg.max_stacks != null) ? cfg.max_stacks : 20;
                var pv = window._fwAggregate.pivot(rows, cfg.x, activeStackBy, cfg.value_cols, cfg.stack_sort);
                var datasets = [], otherData = null;
                var limit = Math.min(pv.stackNames.length, MAX_STACKS);

                if (cfg.diverging && cfg.value_cols.length >= 2) {
                    /* Diverging: separate datasets per (stack, value_col).
                       Positive cols stack up, negative cols stack down.
                       Datasets sharing a label are legend-paired by fwLegend. */
                    for (var i = 0; i < pv.stackNames.length; i++) {
                        var colData = pv.datasets[pv.stackNames[i]];
                        if (i < limit) {
                            var c = window._fwGetLabelColor(cfg.dataset_id, pv.stackNames[i], colors);
                            for (var ci = 0; ci < cfg.value_cols.length; ci++) {
                                var vdata = [];
                                for (var xi = 0; xi < pv.labels.length; xi++)
                                    vdata.push(colData[cfg.value_cols[ci]][xi] || 0);
                                datasets.push({ label: pv.stackNames[i], data: vdata,
                                    backgroundColor: c + (ci === 0 ? 'cc' : '77'),
                                    borderColor: c, borderWidth: 1 });
                            }
                        } else {
                            if (!otherData) {
                                otherData = {};
                                for (var oi = 0; oi < cfg.value_cols.length; oi++)
                                    otherData[cfg.value_cols[oi]] = new Array(pv.labels.length).fill(0);
                            }
                            for (var oi2 = 0; oi2 < cfg.value_cols.length; oi2++)
                                for (var xi2 = 0; xi2 < pv.labels.length; xi2++)
                                    otherData[cfg.value_cols[oi2]][xi2] += (colData[cfg.value_cols[oi2]][xi2] || 0);
                        }
                    }
                    if (otherData) {
                        var otherLbl = 'Other (' + (pv.stackNames.length - limit) + ')';
                        for (var oi3 = 0; oi3 < cfg.value_cols.length; oi3++)
                            datasets.push({ label: otherLbl, data: otherData[cfg.value_cols[oi3]],
                                backgroundColor: '#888888' + (oi3 === 0 ? 'cc' : '77'),
                                borderColor: '#888888', borderWidth: 1 });
                    }
                } else {
                    /* Net: sum all value_cols per stack into single dataset */
                    for (var i = 0; i < pv.stackNames.length; i++) {
                        var netData = [], colData = pv.datasets[pv.stackNames[i]];
                        for (var xi = 0; xi < pv.labels.length; xi++) {
                            var net = 0;
                            for (var ci = 0; ci < cfg.value_cols.length; ci++) net += (colData[cfg.value_cols[ci]][xi] || 0);
                            netData.push(net);
                        }
                        if (i < limit) {
                            var c = window._fwGetLabelColor(cfg.dataset_id, pv.stackNames[i], colors);
                            datasets.push({ label: pv.stackNames[i], data: netData,
                                backgroundColor: c + 'cc', borderColor: c, borderWidth: 1 });
                        } else {
                            if (!otherData) otherData = new Array(pv.labels.length).fill(0);
                            for (var xi2 = 0; xi2 < pv.labels.length; xi2++) otherData[xi2] += netData[xi2];
                        }
                    }
                    if (otherData)
                        datasets.push({ label: 'Other (' + (pv.stackNames.length - limit) + ')',
                            data: otherData, backgroundColor: '#888888cc', borderColor: '#888888', borderWidth: 1 });
                }
                if (cfg.line_cols) {
                    var grLine = window._fwAggregate.groupBy(rows, cfg.x, cfg.line_cols);
                    var lineLabels = cfg.line_labels || cfg.line_cols;
                    for (var li = 0; li < cfg.line_cols.length; li++) {
                        var lcol = cfg.line_cols[li];
                        var lineData = pv.labels.map(function(l) { return grLine.map[l] ? grLine.map[l][lcol] || 0 : 0; });
                        var lc = colors[(limit + li) % colors.length];
                        datasets.push({ type: 'line', label: lineLabels[li], data: lineData, yAxisID: 'y1',
                            borderColor: lc, backgroundColor: 'transparent',
                            borderWidth: 2.5, pointRadius: 2, tension: 0, order: -1 });
                    }
                }
                if (normalized) {
                    var totals = pv.labels.map(function(_, xi) {
                        var t = 0;
                        for (var si = 0; si < datasets.length; si++) {
                            if (!datasets[si].yAxisID) t += (datasets[si].data[xi] || 0);
                        }
                        return t;
                    });
                    datasets.forEach(function(ds) {
                        if (!ds.yAxisID) {
                            ds._absData = ds.data.slice();
                            ds.data = ds.data.map(function(v, xi) {
                                return totals[xi] ? (v / totals[xi]) * 100 : 0;
                            });
                        }
                    });
                }
                if (cfg.line_cols || cfg.normalize_toggle) {
                    var vf = cfg.value_format || 'currency';
                    var fmtY = vf === 'currency' ? fmtCompact$ : fmtCompact;
                    var tt = { callbacks: {
                        label: function(ctx) {
                            if (ctx.dataset.yAxisID === 'y1') return ctx.dataset.label + ': ' + fmtCompact$(ctx.parsed.y);
                            var v = ctx.parsed.y, idx = ctx.dataIndex;
                            var absVal = ctx.dataset._absData ? ctx.dataset._absData[idx] : v;
                            if (normalized) {
                                return ctx.dataset.label + ': ' + v.toFixed(1) + '% (' + fmtY(absVal) + ')';
                            }
                            var ds = ctx.chart.data.datasets, total = 0;
                            for (var j = 0; j < ds.length; j++) { if (!ds[j].yAxisID) total += (ds[j].data[idx] || 0); }
                            var pct = total !== 0 ? (v / total * 100).toFixed(1) : '0.0';
                            return ctx.dataset.label + ': ' + fmtY(v) + ' (' + pct + '%)';
                        },
                        footer: function(items) {
                            if (!items.length) return '';
                            var idx = items[0].dataIndex, ds = items[0].chart.data.datasets, total = 0;
                            for (var j = 0; j < ds.length; j++) {
                                if (!ds[j].yAxisID) total += (ds[j]._absData ? ds[j]._absData[idx] : ds[j].data[idx]) || 0;
                            }
                            return 'Total: ' + fmtY(total);
                        }
                    }};
                    var yFmt = normalized ? function(v) { return v.toFixed(0) + '%'; }
                        : (vf === 'currency' ? function(v) { return fmtCompact$(v); } : undefined);
                    _createChart(id, canvas, _mkSpec({ labels: pv.labels, datasets: datasets, stacked: true, showLegend: cfg.show_legend !== false, tooltip: tt, yFmt: yFmt }));
                } else {
                    var vfSb = cfg.value_format || 'currency';
                    var fmtYSb = vfSb === 'currency' ? fmtCompact$ : fmtCompact;
                    var ttSb = { callbacks: {
                        label: function(ctx) {
                            if (ctx.dataset.yAxisID === 'y1') return ctx.dataset.label + ': ' + fmtCompact$(ctx.parsed.y);
                            var v = ctx.parsed.y, idx = ctx.dataIndex, ds = ctx.chart.data.datasets;
                            var totPos = 0, totNeg = 0;
                            for (var jsb = 0; jsb < ds.length; jsb++) {
                                if (ds[jsb].yAxisID === 'y1') continue;
                                var r = ds[jsb].data[idx]; if (r >= 0) totPos += r; else totNeg += r;
                            }
                            var tot = v >= 0 ? totPos : totNeg;
                            var pct = tot !== 0 ? (v / tot * 100).toFixed(1) : '0.0';
                            return ctx.dataset.label + ': ' + fmtYSb(v) + ' (' + pct + '%)';
                        },
                        footer: function(items) {
                            if (!items.length) return '';
                            var idx = items[0].dataIndex, ds = items[0].chart.data.datasets;
                            var totPos = 0, totNeg = 0;
                            for (var fsb = 0; fsb < ds.length; fsb++) {
                                if (ds[fsb].yAxisID === 'y1') continue;
                                var r = ds[fsb].data[idx]; if (r >= 0) totPos += r; else totNeg += r;
                            }
                            if (totNeg === 0) return 'Total: ' + fmtYSb(totPos);
                            return 'Positive: ' + fmtYSb(totPos) + '  Negative: ' + fmtYSb(totNeg);
                        }
                    }};
                    _createChart(id, canvas, _mkSpec({ labels: pv.labels, datasets: datasets, stacked: true, showLegend: cfg.show_legend !== false, tooltip: ttSb }));
                }

            } else if (cfg.net_color) {
                /* Net-color bar: sum columns → single green/red bar per label */
                var gr = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                var data = gr.labels.map(function(l) {
                    var s = 0; for (var j = 0; j < cfg.value_cols.length; j++) s += (gr.map[l][cfg.value_cols[j]] || 0); return s;
                });
                var sty = getComputedStyle(document.documentElement);
                var cG = sty.getPropertyValue('--accent-green').trim() || '#00b894';
                var cR = sty.getPropertyValue('--accent-red').trim() || '#d63031';
                _createChart(id, canvas, _mkSpec({ labels: gr.labels, datasets: [{ label: cfg.title, data: data,
                    backgroundColor: data.map(function(v) { return (v >= 0 ? cG : cR) + 'aa'; }),
                    borderColor: data.map(function(v) { return v >= 0 ? cG : cR; }), borderWidth: 1 }] }));

            } else if (cfg.ratios) {
                /* Ratio aggregation: sum numerator & denominator, then divide.
                   Works for both LineChart (chartType='line') and BarChart
                   (chartType='bar'); the only difference is dataset styling
                   and (for bar) optional sort + top_n. */
                var gr3 = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                var ratLabels = gr3.labels.slice();
                if (!isLine && (cfg.sort === 'desc' || cfg.sort === 'asc')) {
                    var sSpec = cfg.ratios[0];
                    var sDir = cfg.sort === 'asc' ? 1 : -1;
                    ratLabels.sort(function(a, b) {
                        var va = gr3.map[a] || {}, vb = gr3.map[b] || {};
                        var ra = (va[sSpec.denominator] || 0) > 0
                            ? (va[sSpec.numerator] || 0) / va[sSpec.denominator] : 0;
                        var rb = (vb[sSpec.denominator] || 0) > 0
                            ? (vb[sSpec.numerator] || 0) / vb[sSpec.denominator] : 0;
                        return (ra - rb) * sDir;
                    });
                }
                if (!isLine && cfg.top_n && ratLabels.length > cfg.top_n) {
                    /* Collapse the tail into a single "Other (N items)" bucket,
                       or drop it entirely if top_n_show_other === false. */
                    var _tailRat = ratLabels.slice(cfg.top_n);
                    ratLabels = ratLabels.slice(0, cfg.top_n);
                    if (cfg.top_n_show_other !== false) {
                        var _otherKey = 'Other (' + _tailRat.length + ' items)';
                        var _otherAgg = {};
                        for (var _ci = 0; _ci < cfg.value_cols.length; _ci++) _otherAgg[cfg.value_cols[_ci]] = 0;
                        for (var _ti = 0; _ti < _tailRat.length; _ti++) {
                            var _trow = gr3.map[_tailRat[_ti]] || {};
                            for (var _cj = 0; _cj < cfg.value_cols.length; _cj++) {
                                _otherAgg[cfg.value_cols[_cj]] += (_trow[cfg.value_cols[_cj]] || 0);
                            }
                        }
                        gr3.map[_otherKey] = _otherAgg;
                        ratLabels.push(_otherKey);
                    }
                }
                var datasets3 = [];
                for (var ri = 0; ri < cfg.ratios.length; ri++) {
                    var spec = cfg.ratios[ri];
                    var ratData = ratLabels.map(function(l) {
                        var num = gr3.map[l][spec.numerator] || 0;
                        var den = gr3.map[l][spec.denominator] || 0;
                        return den > 0 ? num / den : null;
                    });
                    var rc = colors[ri % colors.length];
                    if (isLine) {
                        datasets3.push({ label: spec.label, data: ratData,
                            backgroundColor: rc + '22', borderColor: rc,
                            borderWidth: 2.5, pointRadius: 2, tension: 0 });
                    } else {
                        datasets3.push({ label: spec.label, data: ratData,
                            backgroundColor: rc + 'cc', borderColor: rc,
                            borderWidth: 1 });
                    }
                }
                var ratFmt = {
                    currency: function(v) { return v == null ? '' : '$' + v.toFixed(2); },
                    percent: function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; },
                    minutes: function(v) { return v == null ? '' : v.toFixed(1) + 'm'; },
                    decimal1: function(v) { return v == null ? '' : v.toFixed(1); }
                };
                var yFmt3 = ratFmt[cfg.y_format] || (
                    cfg.y_format === 'number' ? function(v) { return v == null ? '' : window.fmtCompact(v); } : undefined
                );
                var tt3 = yFmt3 ? { callbacks: { label: function(ctx) {
                    var v = (!isLine && cfg.horizontal) ? ctx.parsed.x : ctx.parsed.y;
                    return ctx.dataset.label + ': ' + yFmt3(v);
                }}} : undefined;
                _createChart(id, canvas, _mkSpec({
                    type: cfg.chartType || 'line',
                    labels: ratLabels, datasets: datasets3,
                    showLegend: datasets3.length > 1, yFmt: yFmt3, tooltip: tt3,
                    horizontal: !isLine && cfg.horizontal,
                    showLabels: cfg.show_labels
                }));

            } else if (cfg.combo) {
                /* Combo chart: bars on y, lines on y1 */
                var allCols = cfg.value_cols.concat(cfg.line_cols);
                var gr4 = window._fwAggregate.groupBy(rows, cfg.x, allCols);
                var datasets4 = [], barLabels = cfg.y_labels || cfg.value_cols;
                var lineLabels4 = cfg.line_labels || cfg.line_cols;
                for (var bi = 0; bi < cfg.value_cols.length; bi++) {
                    var bc = cfg.value_cols[bi];
                    var bd = gr4.labels.map(function(l) { return gr4.map[l][bc] || 0; });
                    var bcolor = colors[bi % colors.length];
                    datasets4.push({ label: barLabels[bi] || bc, data: bd,
                        backgroundColor: bcolor + 'cc', borderColor: bcolor,
                        borderWidth: 1, yAxisID: 'y' });
                }
                for (var li = 0; li < cfg.line_cols.length; li++) {
                    var lc = cfg.line_cols[li];
                    var ld = gr4.labels.map(function(l) { return gr4.map[l][lc] || 0; });
                    var lcolor = (cfg.line_colors && cfg.line_colors[li]) 
                        ? cfg.line_colors[li] 
                        : colors[(cfg.value_cols.length + li) % colors.length];
                    datasets4.push({ type: 'line', label: lineLabels4[li] || lc, data: ld,
                        yAxisID: 'y1', borderColor: lcolor, backgroundColor: 'transparent',
                        borderWidth: 2.5, pointRadius: 2, tension: 0, order: -1 });
                }
                var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
                var yFmt4 = fmtMap[cfg.bar_format] || fmtCompact$;
                var y1Fmt4 = fmtMap[cfg.line_format] || fmtCompact;
                var tt4 = { callbacks: { label: function(ctx) {
                    var fmt = ctx.dataset.yAxisID === 'y1' ? y1Fmt4 : yFmt4;
                    return ctx.dataset.label + ': ' + fmt(ctx.parsed.y);
                }}};
                _createChart(id, canvas, _mkSpec({ labels: gr4.labels, datasets: datasets4,
                    stacked: cfg.stacked, showLegend: true, tooltip: tt4,
                    showLabels: cfg.show_labels,
                    yFmt: function(v) { return yFmt4(v); },
                    y1Fmt: function(v) { return y1Fmt4(v); } }));

            } else {
                /* Standard groupBy aggregation for line / bar */
                var gr2 = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                if (cfg.sort === 'desc' || cfg.sort === 'asc') {
                    var _sortKey = cfg.value_cols[0];
                    var _dir = cfg.sort === 'asc' ? 1 : -1;
                    gr2.labels = gr2.labels.slice().sort(function(a, b) {
                        var va = (gr2.map[a] && gr2.map[a][_sortKey]) || 0;
                        var vb = (gr2.map[b] && gr2.map[b][_sortKey]) || 0;
                        return (va - vb) * _dir;
                    });
                }
                if (!isLine && cfg.top_n && gr2.labels.length > cfg.top_n) {
                    /* Collapse the tail into a single "Other (N items)" bucket,
                       or drop it entirely if top_n_show_other === false.
                       Top-N is applied AFTER sorting and AFTER filter-driven
                       aggregation, so the chart stays correct as filters change. */
                    var _tail = gr2.labels.slice(cfg.top_n);
                    gr2.labels = gr2.labels.slice(0, cfg.top_n);
                    if (cfg.top_n_show_other !== false) {
                        var _oKey = 'Other (' + _tail.length + ' items)';
                        var _oAgg = {};
                        for (var _vi = 0; _vi < cfg.value_cols.length; _vi++) _oAgg[cfg.value_cols[_vi]] = 0;
                        for (var _ti2 = 0; _ti2 < _tail.length; _ti2++) {
                            var _trow2 = gr2.map[_tail[_ti2]] || {};
                            for (var _vj = 0; _vj < cfg.value_cols.length; _vj++) {
                                _oAgg[cfg.value_cols[_vj]] += (_trow2[cfg.value_cols[_vj]] || 0);
                            }
                        }
                        gr2.map[_oKey] = _oAgg;
                        gr2.labels.push(_oKey);
                    }
                }
                var datasets2 = [], ylabels = cfg.y_labels || cfg.value_cols;
                var yFmt2 = cfg.y_format === 'currency' ? function(v) { return fmtCompact$(v); }
                    : cfg.y_format === 'percent' ? function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; }
                    : undefined;
                for (var i2 = 0; i2 < cfg.value_cols.length; i2++) {
                    var col2 = cfg.value_cols[i2];
                    var d2 = gr2.labels.map(function(l) { return gr2.map[l][col2] || 0; });
                    var c2 = colors[i2 % colors.length];
                    var ds2 = { label: ylabels[i2] || col2, data: d2,
                        backgroundColor: isLine ? c2 + '22' : c2 + 'cc', borderColor: c2,
                        borderWidth: isLine ? 2.5 : 1, pointRadius: isLine ? 2 : undefined, tension: 0 };
                    if (cfg.area_fill) {
                        ds2.fill = true;
                        ds2.backgroundColor = cfg.stacked ? c2 : c2 + '33';
                        ds2._fillOpacity = cfg.stacked ? '' : '33';
                        ds2.pointRadius = 0;
                        ds2.tension = 0;
                        ds2.borderWidth = cfg.stacked ? 1 : 2;
                    }
                    datasets2.push(ds2);
                }
                var tt2;
                if (yFmt2 || cfg.horizontal) {
                    var _ttFmt = yFmt2 || function(v) { return fmtCompact(v); };
                    tt2 = { callbacks: { label: function(ctx) {
                        var v = cfg.horizontal ? ctx.parsed.x : ctx.parsed.y;
                        return ctx.dataset.label + ': ' + _ttFmt(v);
                    }}};
                }
                _createChart(id, canvas, _mkSpec({ type: cfg.chartType, labels: gr2.labels, datasets: datasets2,
                    stacked: cfg.stacked, horizontal: cfg.horizontal, showLegend: datasets2.length > 1, yFmt: yFmt2, tooltip: tt2 }));
            }
        }
        window._fwLiveWrap(id, cfg, _drawLive);
        return;
    }

    /* ── Static mode ───────────────────────────────────────── */
    var tooltip;
    if (cfg.stacked && cfg.stackTooltipBreakdown) {
        var vf = cfg.valueFormat || 'currency';
        var fmtY = function(v) { return vf === 'currency' ? fmtCompact$(v) : fmtCompact(v); };
        tooltip = { callbacks: {
            label: function(ctx) {
                var val = ctx.parsed.y, idx = ctx.dataIndex, ds = ctx.chart.data.datasets, total = 0;
                for (var i = 0; i < ds.length; i++) { var raw = ds[i].data[idx]; total += (typeof raw === 'number' && !isNaN(raw) ? raw : 0); }
                return ctx.dataset.label + ': ' + fmtY(val) + ' (' + (total > 0 ? (val / total * 100).toFixed(1) : '0.0') + '%)';
            },
            footer: function(items) {
                if (!items.length) return '';
                var idx = items[0].dataIndex, ds = items[0].chart.data.datasets, total = 0;
                for (var i = 0; i < ds.length; i++) { var raw = ds[i].data[idx]; total += (typeof raw === 'number' && !isNaN(raw) ? raw : 0); }
                return 'Total: ' + fmtY(total);
            }
        }};
    }
    var yFmt = cfg.yTickFormat === 'currency' ? function(v) { return fmtCompact$(v); } : undefined;
    _createChart(id, canvas, {
        type: cfg.chartType, labels: cfg.labels, datasets: cfg.datasets,
        stacked: cfg.stacked, horizontal: cfg.horizontal,
        showLegend: cfg.showLegend !== false, yFmt: yFmt, tooltip: tooltip
    });
};

document.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-csv-chart]');
    if (!btn) return;
    var chartId = btn.getAttribute('data-csv-chart');
    var chart = window._chartInstances[chartId];
    if (!chart) return;
    var slug = document.body.getAttribute('data-report-slug') || 'chart';
    var labels = chart.data.labels || [];
    var cols = ['label'];
    chart.data.datasets.forEach(function(ds) { cols.push(ds.label || 'value'); });
    var rows = [];
    for (var i = 0; i < labels.length; i++) {
        var row = [labels[i]];
        chart.data.datasets.forEach(function(ds) { row.push(ds.data[i]); });
        rows.push(row);
    }
    window._fwDownloadCsv(cols, rows, slug + '_chart.csv');
});
