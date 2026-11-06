window._fwRenderers['kpi'] = function renderKpi(id, cfg) {
    var el = document.getElementById(id);
    var label = document.createElement('div');
    label.className = 'fw-kpi-label';
    label.textContent = cfg.label;
    var value = document.createElement('div');
    value.className = 'fw-kpi-value';
    value.textContent = window._fwKpiFormat(cfg.value, cfg.format, cfg.full_number, cfg.precision);
    value.title = value.textContent;
    el.replaceChildren(label, value);
    el.classList.toggle('fw-kpi-centered', cfg.alignment === 'center');
    if (cfg.delta) {
        var cls = cfg.deltaDirection ? ' ' + cfg.deltaDirection : '';
        var delta = document.createElement('div'); delta.className = 'fw-kpi-delta' + cls;
        var change = document.createElement('span'); change.className = 'fw-kpi-change';
        change.textContent = cfg.delta; delta.appendChild(change); el.appendChild(delta);
    }
    if (cfg.forecast != null) {
        var forecast = document.createElement('div'); forecast.className = 'fw-kpi-forecast';
        forecast.textContent = 'Fcst: ' + window._fwKpiFormat(cfg.forecast, cfg.format, cfg.full_number, cfg.precision);
        el.appendChild(forecast);
    }
    if (cfg.sub) {
        var sub = document.createElement('div'); sub.className = 'fw-kpi-sub';
        sub.textContent = cfg.sub; el.appendChild(sub);
    }
    if (window._fwKpiState) window._fwKpiState.render(el, cfg, null, false);
};

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
