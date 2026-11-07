/* Ordinary OLS fits are derived render state, never Chart.js source datasets.
   No cache crosses a draw: selection/result replacement always recomputes. */
(function() {
    if (window._fwTrends) return;
    var MAX_PAIRS = 50000, MAX_SERIES = 8, MAX_CONTRIBUTORS = 50000;
    var BUDGET_REASON = 'Linear trend budget: at most 50,000 candidate pairs, 50,000 raw contributors and eight selected series; no sampling.';
    function sum(values) {
        var total = 0, correction = 0;
        for (var i = 0; i < values.length; i++) {
            var v = values[i], next = total + v;
            correction += Math.abs(total) >= Math.abs(v) ? (total - next) + v : (v - next) + total;
            total = next;
        }
        return total + correction;
    }
    function unavailable(reason, n, excluded) {
        return {reason: reason, n: n || 0, excluded: excluded || 0};
    }
    function fit(pairs, excluded) {
        if (pairs.length > MAX_PAIRS) return unavailable('Linear trend exceeds 50,000 candidate pairs.', 0, excluded);
        if (!pairs.every(function(p) { return p && Number.isFinite(p.x) && Number.isFinite(p.y); }))
            return unavailable('Fit inputs must be finite numeric pairs.', 0, excluded);
        var n = pairs.length;
        if (n < 3) return unavailable('Linear trend needs at least three eligible observations.', n, excluded);
        var xmin = pairs[0].x, xmax = xmin, y0 = pairs[0].y;
        pairs.forEach(function(p) { xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); });
        var sx = xmax - xmin, sy = 0;
        if (sx === 0) return unavailable('Linear trend needs two distinct x values.', n, excluded);
        pairs.forEach(function(p) { sy = Math.max(sy, Math.abs(p.y - y0)); });
        if (!Number.isFinite(sx) || !Number.isFinite(sy))
            return unavailable('Coordinate range exceeds finite fitting precision.', n, excluded);
        sy = sy || 1;
        // Subtract a readable origin BEFORE scaling; large epochs must not erase
        // small differences. All subsequent moment/residual sums are normalized.
        var xs = pairs.map(function(p) { return (p.x - xmin) / sx; });
        var ys = pairs.map(function(p) { return (p.y - y0) / sy; });
        var mx = sum(xs) / n, my = sum(ys) / n;
        var xx = sum(xs.map(function(x) { return (x - mx) * (x - mx); }));
        var yy = sum(ys.map(function(y) { return (y - my) * (y - my); }));
        var xy = sum(xs.map(function(x, i) { return (x - mx) * (ys[i] - my); }));
        if (!(xx > 0)) return unavailable('X variation is unavailable at supplied precision.', n, excluded);
        var beta = xy / xx;
        var slope = beta * (sy / sx);
        if (!Number.isFinite(slope)) slope = (beta * sy) / sx;
        var start = y0 + sy * (my - beta * mx);
        var end = y0 + sy * (my + beta * (1 - mx));
        var residual = sum(xs.map(function(x, i) { var e = ys[i] - my - beta * (x - mx); return e * e; }));
        var r2 = yy === 0 ? null : 1 - residual / yy;
        if ((slope === 0 && beta !== 0) || ![beta, slope, start, end, residual, xx, yy, xy].every(Number.isFinite) ||
                (r2 !== null && !Number.isFinite(r2)))
            return unavailable('Coefficients or residuals exceed finite fitting precision.', n, excluded);
        return {reason: '', n: n, excluded: excluded || 0, origin: xmin, xMin: xmin, xMax: xmax,
            valueAtOrigin: start, end: end, slope: slope, r2: r2};
    }
    function day(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
        var ms = Date.parse(value + 'T00:00:00.000Z');
        return Number.isFinite(ms) && new Date(ms).toISOString().slice(0, 10) === value ? ms / 86400000 : null;
    }
    function sourceReason(cfg) {
        if (cfg.trend_line.unavailable) return cfg.trend_line.unavailable;
        if (!cfg.dataset_id) return '';
        var e = window._fwFilterEngine, id = cfg.dataset_id, seen = new Set();
        for (var depth = 0; id && depth < 64; depth++) {
            if (seen.has(id)) return 'Linear trend source scope is cyclic.';
            seen.add(id);
            if ((e._liveDatasets || {})[id] || (window._reportData._live_datasets || {})[id])
                return 'Linear trends do not support live-connected results.';
            if ((window._reportData._chunk_manifests || {})[id] ||
                    (window._fwChunkLoader && window._fwChunkLoader._manifests[id]))
                return 'Linear trends do not support chunked populations.';
            var scope = e._scopes[id];
            if (!scope) break;
            id = scope.parent;
        }
        if (!id || depth === 64 || !e._columnar[id]) return 'Linear trend requires a complete ordinary snapshot.';
        var livePropagation = Object.values(window._reportData.components || {}).some(function(c) {
            return c.live_dataset && Object.keys(c.propagate_to || {}).some(function(target) { return seen.has(target); });
        });
        return livePropagation ? 'Linear trends do not support live-propagated populations.' : '';
    }
    function identityKey(identity) {
        return JSON.stringify(Object.keys(identity).sort().map(function(key) { return [key, identity[key]]; }));
    }
    function result(cfg, rows, spec, scatter, split) {
        if (!cfg.trend_line) return null;
        var requested = cfg.trend_line.series;
        if ((rows && rows.length > MAX_CONTRIBUTORS) ||
                (requested === 'each' ? spec.datasets.length > MAX_SERIES : requested.length > MAX_SERIES))
            return {reason: BUDGET_REASON, series: [], daily: !scatter, options: cfg.trend_line};
        var reason = sourceReason(cfg), selected = [];
        if (spec.stacked || cfg.stacked || cfg.area_fill || cfg.combo || spec.datasets.some(function(ds) { return ds.yAxisID && ds.yAxisID !== 'y'; }))
            reason = 'Linear trends support ordinary unstacked single-axis Line and numeric Scatter only.';
        var cols = cfg.trend_line.columns;
        spec.datasets.forEach(function(ds, i) {
            var identity = ds._fwTrendSource || (scatter
                ? Object.assign({column: cols[0]}, cfg.trend_line.group_column ? {group: ds.label} : {})
                : cfg.ratios ? {ratio: cfg.ratios[i].label} : {column: cols[i]});
            if (requested === 'each' || requested.some(function(ref) { return identityKey(ref) === identityKey(identity); }))
                selected.push({index: i, identity: identity, label: ds.label || 'Series ' + (i + 1), fit: null});
        });
        if (requested !== 'each') requested.forEach(function(ref) {
            if (!selected.some(function(item) { return identityKey(item.identity) === identityKey(ref); }))
                selected.push({index: -1, identity: ref, label: Object.values(ref).join(' · '),
                    fit: unavailable('Selected source series is absent from this result.')});
        });
        var count = selected.reduce(function(n, item) { return n + (item.index < 0 ? 0 : spec.datasets[item.index].data.length); }, 0);
        if (selected.length > MAX_SERIES || count > MAX_PAIRS || (rows && rows.length > MAX_CONTRIBUTORS))
            reason = BUDGET_REASON;
        if (!scatter && !reason) {
            var previous = null;
            for (var i = 0; i < spec.labels.length; i++) {
                var d = day(spec.labels[i]);
                if (d === null || (previous !== null && d !== previous + 1)) {
                    reason = 'Linear trends require a regular daily UTC date grid, retaining null date slots; compressed or irregular grids are unsupported.';
                    break;
                }
                previous = d;
            }
        }
        // The source renderer may expose duplicate captions as one legend item.
        // Exact identities still cannot certify two source datasets with one key.
        var keys = selected.map(function(item) { return identityKey(item.identity); });
        if (!reason && new Set(keys).size !== keys.length) reason = 'Source series identities are ambiguous.';
        if (!selected.length && !reason) reason = 'No selected source series in the current result.';
        if (reason) return {reason: reason, series: [], daily: !scatter, options: cfg.trend_line, yFormat: cfg.y_format || cfg.trend_line.y_format};
        var buckets = new Map();
        if (!scatter && rows) rows.forEach(function(row) {
            var key = JSON.stringify([String(row[cfg.x] == null ? '' : row[cfg.x]), split ? String(row[split] == null ? '' : row[split]) : null]);
            if (!buckets.has(key)) buckets.set(key, []);
            buckets.get(key).push(row);
        });
        selected.forEach(function(item) {
            if (item.fit) return;
            var ds = spec.datasets[item.index], pairs = [], excluded = 0;
            if (ds._fwForecast || (cfg.forecast_series || []).indexOf(item.index) >= 0) {
                item.fit = unavailable('Supplied forecast observations are excluded.', 0, ds.data.length); return;
            }
            var ratio = cfg.ratios && cfg.ratios.find(function(r) { return r.label === item.identity.ratio; });
            var required = ratio ? [ratio.numerator, ratio.denominator]
                : item.identity.column ? [item.identity.column] : cfg.value_cols || cols;
            ds.data.forEach(function(value, i) {
                var x, y, valid;
                if (scatter) {
                    x = value && value.x; y = value && value.y;
                    valid = Number.isFinite(x) && Number.isFinite(y);
                } else {
                    x = day(spec.labels[i]); y = value;
                    valid = x !== null && Number.isFinite(y);
                    if (rows) {
                        var contributors = buckets.get(JSON.stringify([String(spec.labels[i]), split ? item.identity.group : null])) || [];
                        valid = valid && contributors.length > 0 && contributors.every(function(row) {
                            return required.every(function(col) { return Number.isFinite(row[col]); });
                        });
                        if (valid && ratio) {
                            var den = sum(contributors.map(function(row) { return row[ratio.denominator]; }));
                            valid = Number.isFinite(den) && den > 0;
                        }
                    }
                }
                if (valid) pairs.push({x: x, y: y}); else excluded++;
            });
            item.fit = fit(pairs, excluded);
        });
        return {reason: '', series: selected, daily: !scatter, gridOrigin: !scatter ? day(spec.labels[0]) : null,
            options: cfg.trend_line, yFormat: cfg.y_format || cfg.trend_line.y_format, xName: cfg.x_col || 'x'};
    }
    function line(cfg, rows, spec, split) {
        spec.trends = result(cfg, rows, spec, false, split);
        return spec;
    }
    function number(value) { return value === 0 ? '0' : Number(value.toPrecision(6)).toString(); }
    function ordinate(value, format) {
        if (format === 'currency') return '$' + number(value);
        if (format === 'percent') return number(value * 100) + '%';
        if (format === 'minutes') return number(value) + ' min';
        return number(value) + (format === 'chips' ? ' chips' : '');
    }
    function dateLabel(value) { return new Date(value * 86400000).toISOString().slice(0, 10); }
    function plugin(state) {
        var box, button, details, content, status, observer, controller, canvas, view;
        function cleanup(chart) {
            if (controller) controller.abort();
            if (observer) observer.disconnect();
            if (box) box.remove();
            if (canvas) {
                var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) {
                    return t && t !== canvas.id + '-trends';
                });
                if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
                else canvas.removeAttribute('aria-describedby');
            }
            if (chart) delete chart._fwTrends;
            box = button = details = content = status = observer = controller = canvas = state = view = null;
        }
        function describe(chart) {
            if (!content) return;
            content.replaceChildren();
            if (state.reason || state.axisReason) { content.textContent = state.reason || state.axisReason; return; }
            state.series.forEach(function(item) {
                var f = item.fit, p = document.createElement('p');
                var text = item.label + ': ';
                if (f.reason) text += f.reason + ' n=' + f.n + '; excluded=' + f.excluded + '.';
                else {
                    var origin = state.daily ? dateLabel(f.origin) : String(f.origin);
                    text += 'OLS linear; ' + (state.daily ? dateLabel(f.xMin) + ' to ' + dateLabel(f.xMax) : String(f.xMin) + ' to ' + String(f.xMax)) +
                        '; n=' + f.n + '; excluded=' + f.excluded + '. Slope ' + ordinate(f.slope, state.yFormat) +
                        (state.daily ? '/day' : '/' + state.xName + ' unit') + '. y = ' + ordinate(f.valueAtOrigin, state.yFormat) +
                        ' + (' + ordinate(f.slope, state.yFormat) + ') × (x − ' + origin + (state.daily ? ', elapsed UTC days' : '') + '). ' +
                        'R² ' + (f.r2 === null ? 'unavailable (constant response)' : number(f.r2)) + '.';
                    if (Math.min(f.valueAtOrigin, f.end) < chart.scales.y.min || Math.max(f.valueAtOrigin, f.end) > chart.scales.y.max)
                        text += ' Fitted values outside the source axis are clipped; source bounds are unchanged.';
                    if (!chart.isDatasetVisible(item.index)) text += ' Source hidden; paired trend hidden.';
                }
                p.textContent = text; content.appendChild(p);
            });
        }
        return {
            id: 'fwLinearTrends',
            afterInit: function(chart) {
                canvas = chart.canvas;
                view = canvas._fwTrendView || {visible: true, open: false, focus: null};
                delete canvas._fwTrendView;
                chart._fwTrends = state;
                box = document.createElement('div'); box.className = 'fw-chart-trends'; box.id = canvas.id + '-trends';
                var key = document.createElement('span'); key.className = 'fw-trend-key';
                var sample = document.createElement('span'); sample.className = 'fw-trend-sample'; sample.setAttribute('aria-hidden', 'true');
                for (var segment = 0; segment < 4; segment++) sample.appendChild(document.createElement('span'));
                key.append(sample, document.createTextNode('Linear trend')); box.appendChild(key);
                button = document.createElement('button'); button.type = 'button'; button.dataset.trendAction = 'visibility';
                box.appendChild(button);
                content = document.createElement('div'); content.className = 'fw-trend-details';
                if (state.options.details && !state.reason) {
                    details = document.createElement('details'); details.open = view.open;
                    var summary = document.createElement('summary'); summary.textContent = 'Trend details'; summary.dataset.trendAction = 'details';
                    details.append(summary, content);
                    var note = document.createElement('p');
                    note.textContent = 'Selected observations after aggregation; zeros count, incomplete inputs and forecasts do not. Ratios and bubble points are unweighted. R² is descriptive, not a forecast guarantee or causal evidence. Zoom crops the same fit; no extrapolation.';
                    details.appendChild(note); box.appendChild(details);
                } else if (state.reason || state.series.some(function(item) { return item.fit.reason; })) box.appendChild(content);
                status = document.createElement('p'); status.className = 'fw-trend-status'; status.hidden = true;
                box.appendChild(status);
                canvas.parentElement.after(box);
                var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
                tokens.push(box.id); canvas.setAttribute('aria-describedby', Array.from(new Set(tokens)).join(' '));
                controller = new AbortController();
                button.addEventListener('click', function() { view.visible = !view.visible; chart.update('none'); }, {signal: controller.signal});
                observer = new MutationObserver(function() {
                    if (!canvas || document.getElementById(canvas.id) === canvas) return;
                    var id = canvas.id;
                    chart.destroy();
                    if (window._chartInstances[id] === chart) delete window._chartInstances[id];
                });
                observer.observe(document.body, {childList: true, subtree: true});
            },
            afterUpdate: function(chart) {
                state.axisReason = chart.scales.y.type !== 'linear' || chart.scales.x.type !== (state.daily ? 'category' : 'linear')
                    ? 'Linear trends require ordinary linear value axes (and a daily category grid for Line).' : '';
                button.textContent = (view.visible ? 'Hide' : 'Show') + ' trend';
                button.setAttribute('aria-pressed', String(view.visible));
                describe(chart);
                status.textContent = state.axisReason || (details ? state.series.filter(function(item) { return item.fit.reason; })
                    .map(function(item) { return item.label + ': ' + item.fit.reason; }).join(' ') : '');
                status.hidden = !status.textContent;
                if (view.focus) {
                    var focus = box.querySelector('[data-trend-action="' + view.focus + '"]');
                    if (focus) focus.focus({preventScroll: true});
                    view.focus = null;
                }
            },
            afterDatasetsDraw: function(chart) {
                if (!view.visible || state.reason || state.axisReason) return;
                var ctx = chart.ctx, area = chart.chartArea;
                ctx.save(); ctx.beginPath(); ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top); ctx.clip();
                ctx.lineWidth = 1.5; ctx.setLineDash([6, 3, 1, 3]);
                state.series.forEach(function(item) {
                    var f = item.fit;
                    if (f.reason || !chart.isDatasetVisible(item.index)) return;
                    var xmin = state.daily ? f.xMin - state.gridOrigin : f.xMin;
                    var xmax = state.daily ? f.xMax - state.gridOrigin : f.xMax;
                    var coords = [chart.scales.x.getPixelForValue(xmin), chart.scales.y.getPixelForValue(f.valueAtOrigin),
                        chart.scales.x.getPixelForValue(xmax), chart.scales.y.getPixelForValue(f.end)];
                    if (!coords.every(Number.isFinite)) return;
                    ctx.strokeStyle = chart.data.datasets[item.index].borderColor;
                    ctx.beginPath(); ctx.moveTo(coords[0], coords[1]); ctx.lineTo(coords[2], coords[3]); ctx.stroke();
                });
                ctx.restore();
            },
            beforeDestroy: function(chart) {
                if (canvas.isConnected) {
                    var active = document.activeElement;
                    canvas._fwTrendView = {visible: view.visible, open: !!(details && details.open),
                        focus: box.contains(active) ? active.dataset.trendAction : null};
                }
            },
            afterDestroy: cleanup
        };
    }
    window._fwTrends = {fit: fit, day: day, line: line, result: result, plugin: plugin,
        limits: {pairs: MAX_PAIRS, series: MAX_SERIES, contributors: MAX_CONTRIBUTORS}};
})();
