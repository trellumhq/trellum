window._fwRenderers['kpi'] = function renderKpi(id, cfg) {
    var el = document.getElementById(id);
    var fmt = window.getFormatter(cfg.format);
    var html = '<div class="fw-kpi-label">' + cfg.label + '</div>'
             + '<div class="fw-kpi-value">' + fmt(cfg.value) + '</div>';
    if (cfg.delta) {
        var cls = cfg.deltaDirection ? ' ' + cfg.deltaDirection : '';
        html += '<div class="fw-kpi-delta' + cls + '">' + cfg.delta + '</div>';
    }
    if (cfg.forecast != null) {
        html += '<div class="fw-kpi-forecast">Fcst: ' + fmt(cfg.forecast) + '</div>';
    }
    if (cfg.sub) {
        html += '<div class="fw-kpi-sub">' + cfg.sub + '</div>';
    }
    el.innerHTML = html;
};
