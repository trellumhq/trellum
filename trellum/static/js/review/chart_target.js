    /* ── chart data targeting (Chart.js charts only) ────────── */
    function chartOf(node) {
        if (!node || node.tagName !== 'CANVAS') return null;
        if (!window.Chart || typeof Chart.getChart !== 'function') return null;
        try { return Chart.getChart(node) || null; } catch (e) { return null; }
    }

    function pointValue(raw) {
        if (raw && typeof raw === 'object') {
            return raw.y !== undefined ? raw.y : (raw.v !== undefined ? raw.v : raw.x);
        }
        return raw;
    }

    function pickPoint(chart, evt, plainClick) {
        var els;
        try {
            els = chart.getElementsAtEventForMode(
                evt, 'nearest', { intersect: !!plainClick }, true);
            if (plainClick && (!els || !els.length)) {
                // Exact hits are rare on thin lines and between-bar gaps.
                // Fall back to the nearest element, but only when the click
                // is within the same column (~40px on the x axis) -- a click
                // in open space must stay component-level.
                els = chart.getElementsAtEventForMode(
                    evt, 'nearest', { intersect: false }, true);
                if (els && els.length && els[0].element) {
                    var rect = chart.canvas.getBoundingClientRect();
                    var dx = Math.abs(els[0].element.x - (evt.clientX - rect.left));
                    if (dx > 40) return null;
                }
            }
        } catch (e) { return null; }
        if (!els || !els.length) return null;
        var hit = els[0];
        var ds = chart.data.datasets[hit.datasetIndex] || {};
        var raw = (ds.data || [])[hit.index];
        var lbl = (chart.data.labels || [])[hit.index];
        if (lbl === undefined && raw && typeof raw === 'object') lbl = raw.x;
        var out = {
            type: 'chart-point',
            series: ds.label || ('dataset ' + hit.datasetIndex),
            x: String(lbl),
            value: pointValue(raw)
        };
        // Stacked/multi-series charts: the click means the whole column, so
        // carry every visible series' value at that x.
        var stack = [];
        (chart.data.datasets || []).forEach(function (d2, di) {
            try { if (chart.getDatasetMeta(di).hidden) return; } catch (e2) {}
            stack.push({ label: d2.label || ('dataset ' + di),
                         value: pointValue((d2.data || [])[hit.index]) });
        });
        if (stack.length > 1) out.stack = stack.slice(0, 10);
        return out;
    }

    function pickRange(drag, upEvent) {
        var chart = drag.chart;
        var rect = drag.canvas.getBoundingClientRect();
        var scales = chart.scales || {};
        var xs = scales.x || scales.x1;
        var labels = chart.data.labels || [];
        if (!xs || typeof xs.getValueForPixel !== 'function' || !labels.length) return null;
        var pxA = Math.min(drag.x0, upEvent.clientX) - rect.left;
        var pxB = Math.max(drag.x0, upEvent.clientX) - rect.left;
        var vA = xs.getValueForPixel(pxA), vB = xs.getValueForPixel(pxB);
        if (typeof vA !== 'number' || typeof vB !== 'number') return null;
        var iA = Math.max(0, Math.round(vA));
        var iB = Math.min(labels.length - 1, Math.round(vB));
        if (iB < iA) return null;
        var series = [];
        (chart.data.datasets || []).forEach(function (ds, di) {
            try { if (chart.getDatasetMeta(di).hidden) return; } catch (e) {}
            var pts = [], vals = [];
            for (var i = iA; i <= iB; i++) {
                var v = pointValue((ds.data || [])[i]);
                pts.push({ x: String(labels[i]), v: v });
                var n = Number(v);
                if (isFinite(n)) vals.push(n);
            }
            series.push({
                label: ds.label || ('dataset ' + di),
                n: pts.length,
                min: vals.length ? Math.min.apply(null, vals) : null,
                max: vals.length ? Math.max.apply(null, vals) : null,
                points: pts.slice(0, 40)
            });
        });
        return {
            type: 'chart-range',
            x_from: String(labels[iA]),
            x_to: String(labels[iB]),
            series: series
        };
    }

    document.addEventListener('mousemove', function (e) {
        if (state.drag) {
            var d = state.drag;
            if (!d.active && Math.abs(e.clientX - d.x0) + Math.abs(e.clientY - d.y0) >= 8) {
                d.active = true;
                lasso.style.display = 'block';
            }
            if (d.active) {
                lasso.style.left = Math.min(d.x0, e.clientX) + 'px';
                lasso.style.top = Math.min(d.y0, e.clientY) + 'px';
                lasso.style.width = Math.abs(e.clientX - d.x0) + 'px';
                lasso.style.height = Math.abs(e.clientY - d.y0) + 'px';
            }
            return;
        }
        if (!state.selecting || pop.style.display === 'block') return;
        if (e.target.closest('[data-fw-review-ui]')) { hl.style.display = 'none'; state.target = null; return; }
        var snap = e.target.closest(SNAP);
        var t = snap && identify(snap);
        if (!t) { hl.style.display = 'none'; state.target = null; return; }
        state.target = t;
        var r = docRect(t.el);
        hl.style.display = 'block';
        hl.style.top = (r.top - 3) + 'px';
        hl.style.left = (r.left - 3) + 'px';
        hl.style.width = (r.w + 6) + 'px';
        hl.style.height = (r.h + 6) + 'px';
        var hint = chartOf(e.target)
            ? '<span class="fwrv-mono"> · Alt-click: data point · drag: range</span>' : '';
        hlLbl.innerHTML = esc(t.kind) + (t.title ? ' · ' + esc(t.title) : '') +
            '<span class="fwrv-mono">' + esc(t.selector) + '</span>' + hint;
    });

    document.addEventListener('mousedown', function (e) {
        if (!state.selecting || e.button !== 0 || e.altKey) return;
        if (e.target.closest('[data-fw-review-ui]')) return;
        var chart = chartOf(e.target);
        if (!chart) return;
        // Take the gesture away from the chart's own drag-zoom while
        // selecting; a sub-8px move still falls through as a plain click.
        e.preventDefault();
        e.stopPropagation();
        state.drag = { chart: chart, canvas: e.target, x0: e.clientX, y0: e.clientY, active: false };
    }, true);

    document.addEventListener('mouseup', function (e) {
        var d = state.drag;
        if (!d) return;
        state.drag = null;
        lasso.style.display = 'none';
        if (!d.active) return;                     // it was a click; the click handler takes it
        e.preventDefault();
        e.stopPropagation();
        state.suppressClick = true;                // swallow the click that trails a drag
        var range = pickRange(d, e);
        var t = state.target || identify(d.canvas);
        if (t) showPop(t, e.clientX, e.clientY, range || undefined);
    }, true);

    document.addEventListener('click', function (e) {
        if (!state.selecting) return;
        if (e.target.closest('[data-fw-review-ui]')) return;
        e.preventDefault();
        e.stopPropagation();
        if (state.suppressClick) { state.suppressClick = false; return; }
        if (!state.target) {
            // Touch devices never hover, so the tap itself must resolve
            // the target the way mousemove would have.
            var snap = e.target.closest && e.target.closest(SNAP);
            state.target = snap ? identify(snap) : null;
        }
        if (!state.target) return;
        var chart = chartOf(e.target);
        if (chart) {
            // Alt-click: nearest point, always. Plain click: capture the
            // point too, but only when the click truly lands on a bar or
            // marker -- a user who clicks a bar means THAT bar, and asked
            // us "what day did I select" expecting an answer.
            var pt = pickPoint(chart, e, !e.altKey);
            if (pt) { showPop(state.target, e.clientX, e.clientY, pt); return; }
        }
        showPop(state.target, e.clientX, e.clientY);
    }, true);
