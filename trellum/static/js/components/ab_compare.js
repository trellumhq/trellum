(function() {
    function _abDestroyDrills(rootId) {
        if (!window._chartInstances) return;
        Object.keys(window._chartInstances).forEach(function(k) {
            if (k.indexOf(rootId) === 0 && (k.indexOf('_drill') >= 0 || k.indexOf('_abdrill_') >= 0)) {
                try { window._chartInstances[k].destroy(); } catch(e) {}
                delete window._chartInstances[k];
            }
        });
    }

    function _abBuildInner(body, prefix, hdr, cfg) {
        var h = hdr || {};
        var cLab = h.control_label || 'Control';
        var tLab = h.test_label || 'Test';
        var html = '';
        html += '<details class="fw-ab-vol-details"><summary class="fw-ab-vol-summary">Volumes per variant</summary>';
        html += '<div class="fw-table-scroll"><table class="fw-ab-vol-table"><thead><tr><th>Metric</th><th>' + cLab + '</th><th>' + tLab + '</th></tr></thead><tbody>';
        (body.volume_rows || []).forEach(function(r) {
            html += '<tr><td>' + r.metric + '</td><td>' + r.control + '</td><td>' + r.test + '</td></tr>';
        });
        html += '</tbody></table></div></details>';
        html += '<div class="fw-ab-block"><h4>KPI comparison vs ' + cLab + '</h4>';
        html += '<div class="fw-ab-kpi-hdr"><span>Metric</span><span>' + cLab + '</span><span>' + tLab + '</span><span></span><span>vs ' + cLab + '</span></div>';
        (body.kpi_rows || []).forEach(function(kr) {
            var d = kr.delta_pct;
            var cls = 'neutral';
            var barCls = 'neutral';
            var mag = Math.abs(d);
            var barW = typeof kr.bar_width === 'number' && kr.bar_width > 0
                ? Math.min(92, Math.max(2, kr.bar_width))
                : Math.min(90, Math.max(2, 8 + Math.sqrt(mag) * 2.8));
            // ci_pct = [lo, hi] in percentage points. If present and the CI
            // straddles zero, the delta is NOT statistically significant —
            // override the colouring to neutral grey so the eye doesn't read
            // a noise signal as a real effect.
            var ci = Array.isArray(kr.ci_pct) ? kr.ci_pct : null;
            var insignificant = ci && ci[0] <= 0 && ci[1] >= 0;
            if (kr.higher_is_better) {
                if (d > 0.05) { cls = 'up'; barCls = 'positive'; }
                else if (d < -0.05) { cls = 'down'; barCls = 'negative'; }
            } else {
                if (d < -0.05) { cls = 'up'; barCls = 'positive'; }
                else if (d > 0.05) { cls = 'down'; barCls = 'negative'; }
            }
            if (Math.abs(d) < 0.05) barCls = 'neutral';
            if (insignificant) { cls = 'neutral'; barCls = 'neutral'; }
            var arrow = d >= 0 ? '\u25b2' : '\u25bc';
            var sign = d >= 0 ? '+' : '';
            html += '<div class="fw-ab-kpi-row" data-tskey="' + (kr.key || '') + '" data-prefix="' + prefix + '">';
            html += '<div class="fw-ab-kpi-label">' + kr.metric + '</div>';
            html += '<div class="fw-ab-kpi-val">' + (kr.control_val || '-') + '</div>';
            html += '<div class="fw-ab-kpi-val">' + (kr.test_val || '-') + '</div>';
            html += '<div class="fw-ab-kpi-bar-wrap"><div class="fw-ab-kpi-bar ' + barCls + '" style="width:' + barW + '%"></div></div>';
            html += '<div class="fw-ab-kpi-delta-wrap">';
            html += '<div class="fw-ab-kpi-delta ' + cls + '">' + arrow + ' ' + sign + d.toFixed(1) + '%</div>';
            if (ci) {
                var ciLo = ci[0].toFixed(1);
                var ciHi = ci[1].toFixed(1);
                var ciCls = insignificant ? 'insig' : 'sig';
                html += '<div class="fw-ab-kpi-ci ' + ciCls + '">95% CI [' + (ci[0]>=0?'+':'') + ciLo + ', ' + (ci[1]>=0?'+':'') + ciHi + ']</div>';
            }
            html += '</div></div>';
        });
        html += '</div><div class="fw-ab-drilldown" id="' + prefix + '_drillpanel"><div class="fw-ab-drilldown-title"></div>';
        html += '<div class="fw-ab-drilldown-canvas"><canvas id="' + prefix + '_drill"></canvas></div></div>';
        return html;
    }

    window._fwRenderers['ab_compare'] = function renderAbCompare(id, cfg) {
        var el = document.getElementById(id);
        if (!el) return;
        _abDestroyDrills(id);
        var h = cfg.header || {};
        var html = '';
        html += '<div class="fw-ab-header"><h3>' + (h.test_name || cfg.title || 'A/B Test') + '</h3>';
        if (h.description) html += '<p class="fw-ab-header-meta">' + h.description + '</p>';
        html += '<div class="fw-ab-header-meta">';
        if (h.split) html += '<div><strong>Split:</strong> ' + h.split + '</div>';
        if (h.start_date) html += '<div><strong>Test start:</strong> ' + h.start_date + '</div>';
        html += '<div><strong>Groups:</strong> ' + (h.control_label||'Control') + ' vs ' + (h.test_label||'Test') + '</div>';
        if (h.srm) {
            html += '<div><strong>Sample ratio:</strong> ' + h.srm.observed +
                    ' observed vs ' + h.srm.expected + ' expected (n=' +
                    Number(h.srm.n).toLocaleString() + ') ' +
                    (h.srm['pass']
                        ? '<span class="fw-ab-srm ok">SRM \u2713</span>'
                        : '<span class="fw-ab-srm bad">SRM \u2717 \u2014 split mismatch (z=' +
                          h.srm.z + '); do not trust the deltas below</span>');
            html += '</div>';
        }
        html += '</div></div>';
        if (cfg.modes && Object.keys(cfg.modes).length > 0) {
            var mids = Object.keys(cfg.modes);
            var defM = cfg.default_mode && cfg.modes[cfg.default_mode] ? cfg.default_mode : mids[0];
            html += '<div class="fw-ab-mode-toggle">';
            mids.forEach(function(mid, i) {
                var m = cfg.modes[mid];
                html += '<button type="button" class="fw-ab-mode-btn' + (mid === defM ? ' active' : '') + '" data-ab-root="' + id + '" data-ab-mode="' + mid + '">' + (m.label || mid) + '</button>';
            });
            html += '</div><div class="fw-ab-mode-panels">';
            mids.forEach(function(mid) {
                var panCl = (mid === defM) ? ' active' : '';
                var panId = id + '_panel_' + mid;
                html += '<div class="fw-ab-mode-panel' + panCl + '" id="' + panId + '" data-ab-mode-panel="' + mid + '">';
                html += _abBuildInner(cfg.modes[mid], panId, h, cfg);
                html += '</div>';
            });
            html += '</div>';
        } else {
            html += _abBuildInner(cfg.body || {volume_rows:[],kpi_rows:[],timeseries:{}}, id + '_main', h, cfg);
        }
        el.innerHTML = html;
        el.querySelectorAll('.fw-ab-mode-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                var root = btn.getAttribute('data-ab-root');
                if (!root) return;
                var mode = btn.getAttribute('data-ab-mode');
                document.querySelectorAll('[data-ab-root="' + root + '"]').forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                document.querySelectorAll('#' + root + ' .fw-ab-mode-panel').forEach(function(p) {
                    if (p.getAttribute('data-ab-mode-panel') === mode) p.classList.add('active');
                    else p.classList.remove('active');
                });
                if (window._fwUrlSync) window._fwUrlSync.write();
            });
        });
        el.querySelectorAll('.fw-ab-kpi-row').forEach(function(row) {
            row.addEventListener('click', function() {
                var key = row.getAttribute('data-tskey');
                var prefix = row.getAttribute('data-prefix') || '';
                var panel = document.getElementById(prefix + '_drillpanel');
                var canvas = document.getElementById(prefix + '_drill');
                if (!key || !panel || !canvas) return;
                var body = cfg.body;
                if (cfg.modes) {
                    body = null;
                    document.querySelectorAll('#' + id + ' .fw-ab-mode-panel').forEach(function(p) {
                        if (p.classList.contains('active')) {
                            var mid = p.getAttribute('data-ab-mode-panel');
                            body = cfg.modes[mid];
                        }
                    });
                }
                if (!body || !body.timeseries || !body.timeseries[key]) return;
                var ts = body.timeseries[key];
                el.querySelectorAll('.fw-ab-kpi-row').forEach(function(r) { r.classList.remove('selected'); });
                row.classList.add('selected');
                panel.querySelector('.fw-ab-drilldown-title').textContent = key.replace(/_/g, ' ');
                panel.classList.add('visible');
                var drillId = prefix + '_drill';
                if (window._chartInstances[drillId]) {
                    try { window._chartInstances[drillId].destroy(); } catch(e) {}
                    delete window._chartInstances[drillId];
                }
                var _tc = typeof window.getThemeColors === 'function' ? window.getThemeColors() : {};
                var _pal = _tc.chart_colors || [];
                var ctrlCol = _pal[0] || '#4e79a7';
                var testCol = _pal[2] || '#e15759';
                var _gColor = _tc.grid_color || cfg.gridColor;
                var _tColor = _tc.tick_color || cfg.tickColor;
                var events = window._reportData && window._reportData._events;
                var annos = window._buildAnnotations ? window._buildAnnotations(events, ts.labels) : {};
                var ch = new Chart(canvas, {
                    type: 'line',
                    data: {
                        labels: ts.labels,
                        datasets: [
                            { label: h.control_label || 'Control', data: ts.control, borderColor: ctrlCol, backgroundColor: ctrlCol + '22', borderWidth: 2, pointRadius: 1, tension: 0, _themeManaged: true, _fillOpacity: '22' },
                            { label: h.test_label || 'Test', data: ts.test, borderColor: testCol, backgroundColor: testCol + '22', borderWidth: 2, pointRadius: 1, tension: 0, _themeManaged: true, _fillOpacity: '22' }
                        ]
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                            legend: { display: false },
                            fwLegend: { display: true },
                            annotation: { annotations: annos }
                        },
                        scales: {
                            x: { grid: { color: _gColor }, ticks: { color: _tColor, font: { size: window._fwFontPx('--font-size-axis', 12) }, maxRotation: 45 } },
                            y: { grid: { color: _gColor }, ticks: { color: _tColor, font: { size: window._fwFontPx('--font-size-axis', 12) } } }
                        }
                    }
                });
                ch._fwThemeManagedAxes = true;
                ch._fwThemeManagedFonts = true;
                window._chartInstances[drillId] = ch;
            });
        });
    };
})();
