window._fwRenderers['kpi_row_live'] = function(id, cfg) {
    function _render(rows) {
        var A = window._fwAggregate;
        for (var i = 0; i < cfg.kpis.length; i++) {
            var kpi = cfg.kpis[i], el = document.getElementById(id + '_k' + i);
            if (!el) continue;
            // Static value: render Python-computed constants that sit next
            // to live-aggregated siblings in the same KpiRow. Skip the agg
            // path so we don't collapse to sum(rows, undefined) === 0.
            if (kpi.value !== undefined && kpi.value !== null
                && !kpi.agg && !kpi.column && !kpi.columns
                && !kpi.numerator) {
                var sdisp;
                if (kpi.format === 'percent') {
                    sdisp = (typeof kpi.value === 'number')
                        ? kpi.value.toFixed(1) + '%' : kpi.value;
                } else {
                    sdisp = window.getFormatter(kpi.format || 'number')(kpi.value);
                }
                el.innerHTML = '<div class="fw-kpi-label">' + (kpi.label || '')
                             + '</div><div class="fw-kpi-value">' + sdisp + '</div>';
                continue;
            }
            var val, agg = kpi.agg || 'sum';
            if (agg === 'sum') {
                val = kpi.columns ? A.sumCols(rows, kpi.columns) : A.sum(rows, kpi.column);
            } else if (agg === 'abssum') {
                val = A.absSum(rows, kpi.column);
            } else if (agg === 'ratio') {
                /* The x100 is DISPLAY, not aggregation. A ratio is num/den;
                   only a PERCENT reading of it is that times a hundred. Baked
                   into the agg it made every non-percent ratio -- ARPDAU, an
                   average order value, sessions per user -- wrong by 100x,
                   which is why such metrics had to be computed in Python and
                   passed in as a static `value=` instead of being claimed
                   live. Charts have always divided raw and scaled in the
                   formatter (chart_base.js); this is the card catching up. */
                var num = A.sum(rows, kpi.numerator);
                var den = Math.abs(A.sum(rows, kpi.denominator));
                val = den > 0 ? (num / den) : 0;
                if (kpi.format === 'percent') val *= 100;
            } else if (agg === 'avg_by_date') {
                var _dcol = kpi.date_col || 'event_date';
                var _dates = {};
                for (var _ri = 0; _ri < rows.length; _ri++) { var _d = rows[_ri][_dcol]; if (_d != null) _dates[_d] = 1; }
                var _nd = Object.keys(_dates).length;
                val = _nd > 0 ? A.sum(rows, kpi.column) / _nd : 0;
            } else if (agg === 'count') {
                val = rows.length;
            } else if (agg === 'purchase_pct') {
                var total = 0, matched = 0;
                var mvs = kpi.match_values || [];
                for (var ri = 0; ri < rows.length; ri++) {
                    var sv = rows[ri][kpi.source_col] || 0;
                    if (sv <= 0) continue;
                    total += sv;
                    if (mvs.indexOf(String(rows[ri][kpi.type_col])) >= 0) matched += sv;
                }
                val = total > 0 ? (matched / total * 100) : 0;
            }
            var disp;
            if (kpi.format === 'percent') { disp = val != null ? val.toFixed(1) + '%' : '-'; }
            else { disp = window.getFormatter(kpi.format || 'number')(val); }
            var deltaHtml = '';
            if (kpi.delta_col) {
                var dVal;
                var dAgg = kpi.delta_agg || kpi.agg || 'sum';
                var dFmt = kpi.delta_format || 'percent';
                if (dAgg === 'ratio') {
                    var dn = A.sum(rows, kpi.delta_numerator || kpi.delta_col);
                    var dd = Math.abs(A.sum(rows, kpi.delta_denominator || kpi.denominator));
                    dVal = dd > 0 ? (dn / dd) : 0;          // display scaling below
                    if (dFmt === 'percent') dVal *= 100;
                } else if (dAgg === 'abssum') {
                    dVal = A.absSum(rows, kpi.delta_col);
                } else {
                    dVal = A.sum(rows, kpi.delta_col);
                }
                var dStr;
                if (dFmt === 'percent') dStr = (dVal >= 0 ? '+' : '') + dVal.toFixed(1) + '%';
                else if (dFmt === 'currency') dStr = (dVal >= 0 ? '+' : '') + fmtCompact$(dVal);
                else dStr = (dVal >= 0 ? '+' : '') + fmtCompact(dVal);
                var dDir = kpi.delta_direction || 'auto';
                if (dDir === 'auto') dDir = dVal >= 0 ? 'up' : 'down';
                deltaHtml = '<div class="fw-kpi-delta ' + dDir + '">' + dStr + '</div>';
            }
            el.innerHTML = '<div class="fw-kpi-label">' + (kpi.label || '') + '</div>'
                         + '<div class="fw-kpi-value">' + disp + '</div>' + deltaHtml;
        }
    }
    window._fwLiveWrap(id, cfg, _render);
};
