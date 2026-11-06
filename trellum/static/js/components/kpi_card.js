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
        delta.textContent = cfg.delta; el.appendChild(delta);
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
};

window._fwKpiFormat = function(value, format, fullNumber, precision) {
    if (!fullNumber && precision == null) return window.getFormatter(format || 'number')(value);
    if (value == null || !Number.isFinite(Number(value))) return '-';
    var n = Number(value);
    if (format === 'percent') n *= 100;
    if (format === 'currency') return new Intl.NumberFormat(undefined, {
        style: 'currency', currency: 'USD',
        minimumFractionDigits: precision == null ? 2 : precision,
        maximumFractionDigits: precision == null ? 2 : precision
    }).format(n);
    var defaultPlaces = format === 'number' ? 12 : 2;
    return n.toLocaleString(undefined, {
        minimumFractionDigits: precision == null ? 0 : precision,
        maximumFractionDigits: precision == null ? defaultPlaces : precision
    }) + (format === 'percent' ? '%' : format === 'ratio' ? 'x' : '');
};
