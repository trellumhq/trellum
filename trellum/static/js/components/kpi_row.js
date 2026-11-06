if (!window._fwKpiFormat) {
    window._fwKpiFormat = function(value, format, fullNumber, precision) {
        if (!fullNumber && precision == null) return window.getFormatter(format || 'number')(value);
        if (value == null || !Number.isFinite(Number(value))) return '-';
        var n = Number(value);
        if (format === 'percent') n *= 100;
        var prefix = format === 'currency' ? '$' : '';
        if (!fullNumber && (format === 'number' || format === 'currency')) {
            var magnitude = Math.abs(n);
            // Keep the existing compact thresholds, including rounded cents.
            if (format === 'currency' && Math.round(magnitude * 100) >= 100000) {
                magnitude = Math.max(magnitude, 1000);
            }
            var units = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'k']];
            for (var i = 0; i < units.length; i++) {
                if (magnitude >= units[i][0]) {
                    return prefix + (n < 0 ? '-' : '')
                        + (magnitude / units[i][0]).toFixed(precision) + units[i][1];
                }
            }
        }
        var places = precision == null ? (format === 'number' ? 12 : 2) : precision;
        return prefix + n.toLocaleString(undefined, {
            minimumFractionDigits: precision == null && format === 'number' ? 0 : places,
            maximumFractionDigits: places
        }) + (format === 'percent' ? '%' : format === 'ratio' ? 'x' : '');
    };
}

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
                        ? (kpi.full_number || kpi.precision != null
                            ? window._fwKpiFormat(kpi.value / 100, 'percent', kpi.full_number,
                                kpi.precision == null ? 1 : kpi.precision)
                            : kpi.value.toFixed(1) + '%') : kpi.value;
                } else {
                    sdisp = window._fwKpiFormat(kpi.value, kpi.format || 'number', kpi.full_number, kpi.precision);
                }
                _renderKpiValue(el, kpi, sdisp);
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
            } else if (agg === 'last_by_date') {
                // The declared time axis uses sortable ISO values, like the
                // date filter and chart labels. Never guess a missing axis.
                var lastDate = null, lastRows = [];
                for (var li = 0; li < rows.length; li++) {
                    var date = rows[li][kpi.date_col];
                    if (date == null || date === '') continue;
                    if (lastDate === null || date > lastDate) {
                        lastDate = date;
                        lastRows = [rows[li]];
                    } else if (date === lastDate) {
                        lastRows.push(rows[li]);
                    }
                }
                val = kpi.last_agg === 'abssum'
                    ? A.absSum(lastRows, kpi.column) : A.sum(lastRows, kpi.column);
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
            if (kpi.format === 'percent') {
                disp = val != null ? (kpi.full_number || kpi.precision != null
                    ? window._fwKpiFormat(val / 100, 'percent', kpi.full_number,
                        kpi.precision == null ? 1 : kpi.precision)
                    : val.toFixed(1) + '%') : '-';
            }
            else { disp = window._fwKpiFormat(val, kpi.format || 'number', kpi.full_number, kpi.precision); }
            var deltaText = '', deltaDirection = '';
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
                deltaText = dStr;
                deltaDirection = dDir;
            }
            _renderKpiValue(el, kpi, disp);
            if (deltaText) {
                var delta = document.createElement('div');
                delta.className = 'fw-kpi-delta ' + deltaDirection;
                var change = document.createElement('span');
                change.className = 'fw-kpi-change';
                change.textContent = deltaText;
                delta.appendChild(change);
                el.appendChild(delta);
            }
        }
    }
    window._fwLiveWrap(id, cfg, _render);
};

function _renderKpiValue(el, kpi, display) {
    var label = document.createElement('div');
    label.className = 'fw-kpi-label';
    label.textContent = kpi.label || '';
    var value = document.createElement('div');
    value.className = 'fw-kpi-value';
    value.textContent = display;
    value.title = display;
    el.replaceChildren(label, value);
    el.classList.toggle('fw-kpi-centered', kpi.alignment === 'center');
}
