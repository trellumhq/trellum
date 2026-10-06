    // ── Theme Registry ───────────────────────────────────
    var _fwDefaultTheme = _fwConfig.defaultTheme;
    window._themes = _fwConfig.themes;

    function getActiveTheme() {
        return document.documentElement.getAttribute('data-theme') || _fwDefaultTheme;
    }
    window.getActiveTheme = getActiveTheme;

    function getThemeColors() {
        var name = getActiveTheme();
        return window._themes[name] || window._themes[_fwDefaultTheme] || window._themes[Object.keys(window._themes)[0]];
    }
    window.getThemeColors = getThemeColors;

    /* Let the browser resolve px/rem/em/calc()/var() exactly as report CSS
       does. Canvas APIs need a numeric pixel size, so reading the custom
       property directly with parseFloat is incorrect for relative units. */
    function _fontPx(property, fallback) {
        var probe = document.createElement('span');
        probe.style.cssText = 'position:absolute;visibility:hidden;font-size:var(' +
            property + ',' + fallback + 'px)';
        document.body.appendChild(probe);
        var pixels = parseFloat(getComputedStyle(probe).fontSize);
        probe.remove();
        return isFinite(pixels) ? pixels : fallback;
    }
    window._fwFontPx = _fontPx;

    // ── Visibility tracking for lazy chart theme updates ───
    var _visibleCharts = {};
    var _chartObserver = null;
    if (typeof IntersectionObserver !== 'undefined') {
        _chartObserver = new IntersectionObserver(function(entries) {
            for (var e = 0; e < entries.length; e++) {
                var eid = entries[e].target.id;
                if (eid) {
                    _visibleCharts[eid] = entries[e].isIntersecting;
                    if (entries[e].isIntersecting && entries[e].target._fwPendingTheme) {
                        var pc = (window._chartInstances || {})[eid];
                        if (pc) {
                            _applyThemeToSingleChart(pc, entries[e].target._fwPendingTheme);
                            entries[e].target._fwPendingTheme = null;
                        }
                    }
                }
            }
        }, { threshold: 0 });
    }

    function _applyThemeToSingleChart(chart, tc) {
        if (!chart || !chart.canvas || !chart.ctx || !chart.data) return;
        /* `chart.options` is Chart.js' resolved Proxy. Structured values read
           from it must not be assigned back: that stores a resolver inside
           the raw config and later descriptor lookup receives Symbol keys.
           Mutate the plain config so update() can resolve it afresh. */
        var options = chart.config && chart.config.options;
        var isDoughnut = chart.config.type === 'doughnut' || chart.config.type === 'pie';
        var ds0 = chart.data.datasets[0];
        if (ds0 && ds0._themeManaged && Array.isArray(ds0.backgroundColor)) {
            if (isDoughnut) {
                ds0.backgroundColor = ds0.backgroundColor.map(function(_, i) {
                    return tc.chart_colors[i % tc.chart_colors.length];
                });
                var bgCard = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
                ds0.borderColor = bgCard;
                if (options && options.plugins && options.plugins.legend && options.plugins.legend.labels) {
                    options.plugins.legend.labels.color = tc.tick_color;
                }
            } else if (ds0._funnelSegments) {
                ds0.backgroundColor = ds0.backgroundColor.map(function(_, i) {
                    return tc.chart_colors[i % tc.chart_colors.length] + 'cc';
                });
                if (Array.isArray(ds0.borderColor)) {
                    ds0.borderColor = ds0.borderColor.map(function(_, i) {
                        return tc.chart_colors[i % tc.chart_colors.length];
                    });
                }
            }
        } else if (!isDoughnut) {
            chart.data.datasets.forEach(function(ds, i) {
                if (!ds._themeManaged) return;
                var newColor = tc.chart_colors[i % tc.chart_colors.length];
                ds.borderColor = newColor;
                if ('_fillOpacity' in ds) {
                    ds.backgroundColor = ds._fillOpacity ? newColor + ds._fillOpacity : newColor;
                } else if (chart.config.type === 'line') {
                    ds.backgroundColor = newColor + '22';
                } else {
                    ds.backgroundColor = newColor + 'cc';
                }
            });
        }
        if (chart._fwThemeManagedAxes && options && options.scales) {
            Object.keys(options.scales).forEach(function(axis) {
                var scale = options.scales[axis];
                if (scale.grid) scale.grid.color = tc.grid_color;
                if (scale.ticks) {
                    scale.ticks.color = tc.tick_color;
                    scale.ticks.font = scale.ticks.font || {};
                    scale.ticks.font.size = _fontPx('--font-size-axis', 12);
                }
                if (scale.title) {
                    scale.title.color = tc.tick_color;
                    scale.title.font = scale.title.font || {};
                    scale.title.font.size = _fontPx('--font-size-label', 12);
                }
            });
        }
        if (chart._fwThemeManagedFonts && options && options.plugins) {
            var plugins = options.plugins;
            if (plugins.legend && plugins.legend.display !== false) {
                plugins.legend.labels = plugins.legend.labels || {};
                plugins.legend.labels.color = tc.tick_color;
                plugins.legend.labels.font = plugins.legend.labels.font || {};
                plugins.legend.labels.font.size = _fontPx('--font-size-label', 12);
            }
            if (plugins.datalabels && plugins.datalabels.display !== false) {
                plugins.datalabels.font = plugins.datalabels.font || {};
                plugins.datalabels.font.size = _fontPx('--font-size-label', 12);
            }
            if (plugins.annotation && plugins.annotation.annotations) {
                Object.keys(plugins.annotation.annotations).forEach(function(key) {
                    var annotation = plugins.annotation.annotations[key];
                    if (!annotation.label) return;
                    annotation.label.font = annotation.label.font || {};
                    annotation.label.font.size = _fontPx('--font-size-label', 12);
                });
            }
            if (chart.config.type === 'treemap' && chart.data.datasets[0] &&
                    chart.data.datasets[0].labels) {
                var labels = chart.data.datasets[0].labels;
                labels.font = labels.font || {};
                labels.font.size = _fontPx('--font-size-label', 12);
            }
        }
        chart.update('none');
    }

    function _applyThemeToCharts(themeName) {
        var tc = window._themes[themeName];
        if (!tc) return;
        if (typeof Chart !== 'undefined') {
            Chart.defaults.color = tc.tick_color;
            Chart.defaults.borderColor = tc.grid_color;
        }
        var instances = window._chartInstances || {};
        var ids = Object.keys(instances);
        var visible = [], deferred = [];
        for (var _ti = 0; _ti < ids.length; _ti++) {
            var chart = instances[ids[_ti]];
            if (!chart || !chart.canvas) continue;
            if (_visibleCharts[chart.canvas.id] === false) {
                chart.canvas._fwPendingTheme = tc;
                deferred.push(chart);
            } else {
                visible.push(chart);
            }
        }
        if (visible.length === 0) return;
        var _batch = 0;
        function _updateBatch() {
            var end = Math.min(_batch + 3, visible.length);
            for (var _bi = _batch; _bi < end; _bi++) {
                _applyThemeToSingleChart(visible[_bi], tc);
            }
            _batch = end;
            if (_batch < visible.length) requestAnimationFrame(_updateBatch);
        }
        requestAnimationFrame(_updateBatch);
    }

    function switchTheme(name) {
        if (!window._themes[name]) return;
        document.documentElement.setAttribute('data-theme', name);
        _lsSet('fw-theme', name);
        _applyThemeToCharts(name);
        var sel = document.getElementById('fwThemeSelect');
        if (sel) sel.value = name;
        window.dispatchEvent(new CustomEvent('fw-theme-change', { detail: { theme: name } }));
    }
    window.switchTheme = switchTheme;

    // Sync data-theme from localStorage immediately (dropdown synced later after DOM ready)
    (function() {
        var saved = _lsGet('fw-theme');
        if (saved && window._themes[saved]) {
            document.documentElement.setAttribute('data-theme', saved);
        }
    })();
