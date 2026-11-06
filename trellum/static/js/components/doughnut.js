/* Doughnut-local formatting and population context. Arc and CSV data retain
   their existing static grain / live groupBy contract. */
function _fwDoughnutPopulation(values) {
    if (!values.length) return { total: null, reason: 'No categories in the current result.' };
    if (!values.every(function(v) { return typeof v === 'number' && Number.isFinite(v); })) {
        return { total: null, reason: 'Total and shares unavailable: incomplete or non-numeric values.' };
    }
    if (values.some(function(v) { return v < 0; })) {
        return { total: null, reason: 'Total and shares unavailable: signed values.' };
    }
    var total = values.reduce(function(a, b) { return a + b; }, 0);
    if (!Number.isFinite(total)) return { total: null, reason: 'Total and shares unavailable: sum exceeds the finite range.' };
    return { total: total, reason: total === 0 ? 'Shares undefined: all supplied values are zero.' : '' };
}

function _fwDoughnutRaw(value) {
    return value === null ? 'null' : value === undefined ? 'missing' : String(value);
}

function _fwDoughnutScientific(value, cfg) {
    var format = cfg.value_format || 'number';
    var text = value.toExponential(cfg.precision == null ? 2 : cfg.precision);
    if (format === 'percent') {
        text = text.replace(/e([+-]?\d+)$/, function(_, exponent) {
            var adjusted = Number(exponent) + 2;
            return 'e' + (adjusted >= 0 ? '+' : '') + adjusted;
        });
    }
    return (format === 'currency' ? '$' : '') + text +
        (format === 'percent' ? '%' : format === 'ratio' ? 'x' : format === 'chips' ? ' chips' : '');
}

function _fwDoughnutFormat(value, cfg) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return _fwDoughnutRaw(value);
    var format = cfg.value_format || 'number';
    if (format === 'percent' && !Number.isFinite(value * 100)) return _fwDoughnutScientific(value, cfg);
    // Use the shared percent/ratio semantics; full number/currency values must
    // remain readable without the compact formatter's magnitude suffixes.
    if (cfg.precision == null && (format === 'percent' || format === 'ratio' || format === 'plain')) {
        return window.getFormatter(format)(value);
    }
    var n = format === 'percent' ? value * 100 : value;
    if (!Number.isFinite(n)) return _fwDoughnutRaw(value);
    var places = cfg.precision == null ? (format === 'currency' || format === 'ratio' || format === 'percent' ? 2 : 12) : cfg.precision;
    return (format === 'currency' ? '$' : '') + n.toLocaleString(undefined, {
        minimumFractionDigits: cfg.precision == null && (format === 'number' || format === 'chips' || format === 'plain') ? 0 : places,
        maximumFractionDigits: places
    }) + (format === 'percent' ? '%' : format === 'ratio' ? 'x' : format === 'chips' ? ' chips' : '');
}

function _fwDoughnutShare(value, population) {
    return population.total > 0 ? (value / population.total * 100).toFixed(1) + '%' : '—';
}

function _fwDoughnutLegend(chart, root, cfg, population, supplied) {
    if (!root) return;
    var legend = root.querySelector('.fw-doughnut-legend');
    var labels = chart.data.labels, data = chart.data.datasets[0].data;
    function span(cls, text) {
        var el = document.createElement('span'); el.className = cls; el.textContent = text; return el;
    }
    if (!legend.firstChild) {
        var head = document.createElement('div'); head.className = 'fw-doughnut-legend-head';
        head.append(span('', 'Category'), span('', 'Value'), span('', 'Share of all categories'));
        var entries = document.createElement('div'); entries.className = 'fw-doughnut-entries';
        legend.append(head, entries);
    }
    var entries = legend.querySelector('.fw-doughnut-entries');
    var keys = labels.map(function(label, i) { return JSON.stringify([i, label]); });
    // Index + label keeps duplicate static rows separate. Reuse buttons across
    // ordinary updates so toggling visibility never drops keyboard focus.
    if (entries._keys !== JSON.stringify(keys)) {
        var focused = document.activeElement;
        var focusKey = focused && focused.dataset.doughnutKey;
        entries.replaceChildren();
        labels.forEach(function(label, i) {
            var button = document.createElement('button'); button.type = 'button';
            button.className = 'fw-doughnut-entry'; button.dataset.doughnutKey = keys[i];
            var name = span('fw-doughnut-category', '');
            name.append(span('fw-doughnut-swatch', ''), span('fw-doughnut-name', String(label)), span('fw-doughnut-hidden', 'Hidden'));
            button.append(name, span('fw-doughnut-value', ''), span('fw-doughnut-share', ''));
            entries.appendChild(button);
            if (focusKey === keys[i]) button.focus({ preventScroll: true });
        });
        entries._keys = JSON.stringify(keys);
    }
    labels.forEach(function(label, i) {
        var button = entries.children[i], visible = chart.getDataVisibility(i);
        button.setAttribute('aria-pressed', String(visible));
        button.querySelector('.fw-doughnut-hidden').hidden = visible;
        button.querySelector('.fw-doughnut-swatch').style.backgroundColor = chart.data.datasets[0].backgroundColor[i];
        var raw = supplied[i];
        var value = button.querySelector('.fw-doughnut-value');
        value.textContent = raw.invalid ? raw.text : _fwDoughnutFormat(data[i], cfg);
        value.title = 'Supplied values: ' + raw.text + '; chart value: ' + _fwDoughnutRaw(data[i]);
        var share = button.querySelector('.fw-doughnut-share');
        share.textContent = _fwDoughnutShare(data[i], population);
        share.title = population.reason || 'Share of all categories in the current result, including hidden categories.';
        button.setAttribute('aria-label', String(label) + ': value ' + value.textContent +
            '; share of all categories ' + share.textContent + '; ' + (visible ? 'visible' : 'hidden'));
        button.onclick = function() { chart.toggleDataVisibility(i); chart.update(); };
    });
    var hidden = labels.filter(function(_, i) { return !chart.getDataVisibility(i); }).length;
    var allHidden = labels.length > 0 && hidden === labels.length;
    root.querySelector('.fw-doughnut-ring').classList.toggle('is-empty', allHidden || population.total === 0 || !labels.length);
    var center = root.querySelector('.fw-doughnut-center');
    center.hidden = !cfg.center_total;
    if (cfg.center_total) {
        var totalText = population.total == null ? '—' : _fwDoughnutFormat(population.total, cfg);
        center.replaceChildren(
            span('fw-doughnut-center-label', cfg.center_label == null ? 'Total' : cfg.center_label),
            span('fw-doughnut-center-value', totalText.length > 14 ? _fwDoughnutScientific(population.total, cfg) : totalText),
            span('fw-doughnut-center-scope', 'All categories')
        );
        center.title = totalText + ' · ' + (population.reason || 'Total of all categories in the current result, including hidden categories.');
    }
    var context = root.querySelector('.fw-doughnut-context');
    var message = population.reason;
    if (hidden) message += (message ? ' ' : '') + (allHidden ? 'All categories hidden. ' : '') + 'Hiding changes only the ring; values, shares and CSV still include all categories.';
    var note = context.querySelector('span');
    if (!note) { note = span('', ''); context.appendChild(note); }
    note.textContent = message;
    var restore = context.querySelector('button');
    if (allHidden && !restore) {
        restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Restore all'; context.appendChild(restore);
    }
    if (restore) {
        restore.hidden = !allHidden;
        restore.onclick = function() {
            labels.forEach(function(_, i) { if (!chart.getDataVisibility(i)) chart.toggleDataVisibility(i); });
            // The action disappears after restoring. Return focus to the legend.
            entries.firstChild.focus({ preventScroll: true }); chart.update();
        };
    }
}

function _createDoughnut(id, canvas, labels, data, cfCfg, showLabels, cfg, population, supplied, categoryKeys) {
    if (window._chartInstances[id]) {
        window._chartInstances[id].destroy();
        delete window._chartInstances[id];
    }
    cfg = cfg || {};
    population = population || _fwDoughnutPopulation(data);
    supplied = supplied || data.map(function(v) { return { text: _fwDoughnutRaw(v), invalid: typeof v !== 'number' || !Number.isFinite(v) }; });
    var root = canvas.closest('.fw-doughnut');
    var tc = window.getThemeColors();
    var labelFont = window._fwFontPx('--font-size-label', 12);
    var colors = labels.map(function(_, i) {
        return window._fwCategoryColor(cfg.category_colors, categoryKeys && categoryKeys[i], tc.chart_colors) ||
            tc.chart_colors[i % tc.chart_colors.length];
    });
    var bgCard = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
    var opts = {
        responsive: true, maintainAspectRatio: false,
        plugins: {
            // Older generated markup has no HTML legend; retain its canvas legend.
            legend: { display: !root, position: 'right', labels: { color: tc.tick_color, padding: 14, font: { size: labelFont } } },
            tooltip: { callbacks: {
                label: function(ctx) {
                    var raw = supplied[ctx.dataIndex];
                    return ctx.label + ': ' + (raw.invalid ? raw.text : _fwDoughnutFormat(ctx.raw, cfg)) +
                        ' (' + _fwDoughnutShare(ctx.raw, population) + ' share of all categories)' +
                        (population.reason ? ' · ' + population.reason : '');
                }
            }},
            datalabels: showLabels ? {
                display: function(ctx) {
                    return population.total > 0 && ctx.dataset.data[ctx.dataIndex] / population.total * 100 >= 3;
                },
                color: '#fff', font: { size: labelFont, weight: 600 },
                formatter: function(value) { return _fwDoughnutShare(value, population); }
            } : { display: false }
        }
    };
    if (root) {
        opts.cutout = '74%';
        // Keep the existing percentage labels within the compact canvas edges.
        if (showLabels) opts.layout = { padding: 8 };
    }
    if (cfCfg && cfCfg.crossFilter && cfCfg.dsId) {
        opts.onClick = function(evt, elements) {
            var chart = window._chartInstances[id];
            if (!chart || !elements.length) return;
            var prev = chart._crossFilterValue || null;
            var clicked = String(chart.data.labels[elements[0].index]);
            if (clicked === prev) {
                chart._crossFilterValue = null;
                window._fwCrossFilter(cfCfg.dsId, cfCfg.crossFilter, null);
            } else {
                chart._crossFilterValue = clicked;
                window._fwCrossFilter(cfCfg.dsId, cfCfg.crossFilter, clicked);
            }
        };
    }
    var dataset = { data: data, backgroundColor: colors, borderWidth: 2, borderColor: bgCard, _themeManaged: true };
    if (cfg.category_colors) {
        dataset._fwCategoryColors = cfg.category_colors;
        dataset._fwCategoryKeys = categoryKeys;
    }
    window._chartInstances[id] = new Chart(canvas, {
        type: 'doughnut',
        data: { labels: labels, datasets: [dataset] },
        options: opts,
        // Runs after shared theme code has set the actual dataset colors.
        plugins: [{ id: 'fwDoughnutLegend', afterUpdate: function(chart) {
            _fwDoughnutLegend(chart, root, cfg, population, supplied);
        } }]
    });
    window._chartInstances[id]._fwThemeManagedFonts = true;
}

window._fwRenderers['doughnut'] = function renderDoughnut(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    function draw(rows) {
        var activeCfg = canvas._fwDoughnutConfig;
        if (activeCfg.dataset_id) {
            // Inspect original incoming rows BEFORE legacy groupBy turns nulls
            // into zero. Preserve the existing grouped arc/CSV values.
            var original = rows.map(function(r) { return r[activeCfg.value_col]; });
            var population = _fwDoughnutPopulation(original);
            var gr = window._fwAggregate.groupBy(rows, activeCfg.label_col, [activeCfg.value_col]);
            var groups = new Map();
            rows.forEach(function(row) {
                var key = String(row[activeCfg.label_col] == null ? '' : row[activeCfg.label_col]);
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(row[activeCfg.value_col]);
            });
            var data = gr.labels.map(function(l) { return gr.map[l][activeCfg.value_col] || 0; });
            // A finite input can still overflow during grouping or summing.
            if (population.total != null && data.some(function(v) { return typeof v !== 'number' || !Number.isFinite(v); })) {
                population = { total: null, reason: 'Total and shares unavailable: nonfinite grouped result.' };
            } else if (population.total != null) {
                // Use the same grouped denominator as existing chart tooltips.
                population = _fwDoughnutPopulation(data);
            }
            var supplied = gr.labels.map(function(l) {
                var values = groups.get(l) || [];
                return { text: values.map(_fwDoughnutRaw).join(', '), invalid: values.some(function(v) { return typeof v !== 'number' || !Number.isFinite(v); }) };
            });
            var membership = activeCfg.category_colors
                ? window._fwCategoryMembership(rows, activeCfg.label_col) : null;
            var categoryKeys = membership ? gr.labels.map(function(label) {
                return membership.get(label) === true ? label : null;
            }) : null;
            _createDoughnut(id, canvas, gr.labels, data,
                activeCfg.crossFilter ? { crossFilter: activeCfg.crossFilter, dsId: activeCfg.dataset_id } : null,
                activeCfg.show_labels, activeCfg, population, supplied, categoryKeys);
        } else {
            var supplied = activeCfg.data.map(function(v, i) {
                return { text: activeCfg.supplied_values ? activeCfg.supplied_values[i] : _fwDoughnutRaw(v), invalid: typeof v !== 'number' || !Number.isFinite(v) };
            });
            _createDoughnut(id, canvas, activeCfg.labels, activeCfg.data, null, activeCfg.show_labels, activeCfg, null, supplied, activeCfg.category_keys);
        }
    }
    canvas._fwDoughnutConfig = cfg;
    var download = canvas.closest('.fw-chart-container').querySelector('[data-csv-chart]');
    if (download) download.onclick = function(event) {
        // Doughnut-only reports need no ordinary chart renderer. Handle this
        // button locally and avoid a second download from its shared delegate.
        event.stopPropagation();
        var chart = window._chartInstances[id];
        if (!chart) return;
        window._fwDownloadCsv(['label', 'value'], chart.data.labels.map(function(label, i) {
            return [label, chart.data.datasets[0].data[i]];
        }), (document.body.getAttribute('data-report-slug') || 'chart') + '_chart.csv');
    };
    var bindings = window._fwDoughnutBindings || (window._fwDoughnutBindings = {});
    if (bindings[id]) { bindings[id](); delete bindings[id]; }
    if (cfg.dataset_id) {
        canvas.ondblclick = function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        };
        if (window._fwMetricsWorkspace) {
            // The shared wrapper owns queueing, leases and pagehide cleanup.
            window._fwLiveWrap(id, cfg, draw);
        } else {
            // Keep one subscription/theme listener when the same component is
            // reinitialized, including a replaced canvas or dataset binding.
            var unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, draw);
            var onTheme = function() {
                if (document.getElementById(id) !== canvas) {
                    bindings[id](); delete bindings[id]; return;
                }
                draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            };
            window.addEventListener('fw-theme-change', onTheme);
            bindings[id] = function() {
                unsubscribe(); window.removeEventListener('fw-theme-change', onTheme);
            };
            draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        }
    } else draw();
};
