/* Ordinary LineChart only. Immediate source neighbours define connectivity;
   null/nonfinite values are gaps, including at measured zero. Scriptable
   options resolve on every update using the current plot scale (resize/zoom).
   Each point does at most two neighbour checks; no observations are removed. */
function _fwLinePointRadius(ctx) {
    var data = ctx.dataset.data, i = ctx.dataIndex;
    if (!Number.isFinite(data[i])) return 0;
    var before = i > 0 && Number.isFinite(data[i - 1]);
    var after = i + 1 < data.length && Number.isFinite(data[i + 1]);
    if (!before && !after) return 2;
    var x = ctx.chart.scales.x;
    if (!x) return 2;
    var at = x.getPixelForValue(i);
    if (before && Math.abs(at - x.getPixelForValue(i - 1)) < 12) return 0;
    if (after && Math.abs(at - x.getPixelForValue(i + 1)) < 12) return 0;
    return 2;
}

function _fwLinePointColor(ctx) {
    return ctx.dataset.borderColor;
}

function _fwLinePointFill(ctx) {
    if (ctx.dataset._fwForecast) {
        return getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
    }
    return ctx.dataset.borderColor;
}

/* Header summary: one statistic over one plotted series. It reads the
   datasets the chart was just given, so it can only ever describe the line on
   screen -- after filters, aggregation and ratio division alike. Missing and
   non-finite points are skipped, never counted as zero. */
function _fwSummaryValue(values, stat) {
    var nums = [];
    for (var i = 0; i < values.length; i++) {
        var v = values[i];
        if (typeof v === 'number' && isFinite(v)) nums.push(v);
    }
    if (!nums.length) return null;
    if (stat === 'last') return nums[nums.length - 1];
    var total = 0, lo = nums[0], hi = nums[0];
    for (var j = 0; j < nums.length; j++) {
        total += nums[j];
        if (nums[j] < lo) lo = nums[j];
        if (nums[j] > hi) hi = nums[j];
    }
    if (stat === 'sum') return total;
    if (stat === 'min') return lo;
    if (stat === 'max') return hi;
    return total / nums.length;
}

function _fwSummarySeries(datasets, series) {
    if (typeof series === 'number') return datasets[series] || null;
    for (var i = 0; i < datasets.length; i++) {
        if (datasets[i].label === series) return datasets[i];
    }
    return null;
}

function _fwUpdateHeaderSummary(id, summary, datasets, chartFmt) {
    var box = document.querySelector('[data-chart-summary="' + id + '"]');
    if (!box || box.dataset.stackedSummary) return;
    var canvas = document.getElementById(id), descriptionId = id + '-summary-scope';
    if (!summary) {
        if (box.dataset.plottedScope) {
            box.style.display = 'none';
            box.querySelector('.fw-chart-summary-label').textContent = '';
            box.querySelector('.fw-chart-summary-value').textContent = '';
            box.removeAttribute('title');
            box.removeAttribute('aria-label');
            box.removeAttribute('role');
            if (canvas) {
                var remaining = (canvas.getAttribute('aria-describedby') || '').split(/\s+/)
                    .filter(function(token) { return token && token !== descriptionId; });
                if (remaining.length) canvas.setAttribute('aria-describedby', remaining.join(' '));
                else canvas.removeAttribute('aria-describedby');
            }
        }
        return;
    }
    if (box.dataset.plottedScope) box.style.removeProperty('display');
    var ds = _fwSummarySeries(datasets || [], summary.series);
    var value = ds ? _fwSummaryValue(ds.data || [], summary.stat) : null;
    if (summary.scope === 'plotted_series') {
        // The new explicit contract never formats overflow as a real statistic.
        if (!Number.isFinite(value)) value = null;
        if (typeof summary.series === 'string' && (datasets || []).filter(function(item) {
            return item.label === summary.series;
        }).length !== 1) { ds = null; value = null; }
        box.dataset.plottedScope = 'true';
        box.id = descriptionId;
        box.setAttribute('role', 'note');
        box.title = 'One final plotted series (' + (ds ? ds.label : summary.series) +
            ') after filters, aggregation, sorting, Top-N and Other. Null and nonfinite values are excluded; zeros count. ' +
            'Last means the final displayed finite entry. Zoom and legend hiding do not change this scope. ' +
            'Stacked areas use the raw band values. Means of displayed ratios are unweighted; existing plotted zero-fill is retained.';
        if (canvas) {
            var described = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
            if (described.indexOf(descriptionId) < 0) described.push(descriptionId);
            canvas.setAttribute('aria-describedby', described.join(' '));
        }
    }
    /* An explicit format wins; otherwise the value reads as the chart's own
       tooltips and axis do. */
    var fmt = summary.format ? window.getFormatter(summary.format) : (chartFmt || window.fmtCompact);
    var label = box.querySelector('.fw-chart-summary-label');
    var out = box.querySelector('.fw-chart-summary-value');
    /* textContent only: the label comes from report configuration. */
    label.textContent = summary.label || '';
    out.textContent = value === null ? '\u2014' : fmt(value);
    if (summary.scope === 'plotted_series') box.setAttribute('aria-label',
        label.textContent + ': ' + out.textContent + '. ' + box.title);
}

/* StackedBar owns a separate original-unit contract. No coercion, skipped
   entries, hidden-series exclusion or normalization is used by this reducer. */
function _fwStackSummarySafe(v) {
    return typeof v === 'number' && Number.isFinite(v) &&
        (!Number.isInteger(v) || Number.isSafeInteger(v));
}
function _fwStackSummaryResult(summary, datasets, inputReason) {
    var matches = (datasets || []).filter(function(ds) {
        return ds.type !== 'line' && ds.yAxisID !== 'y1' && ds.label === summary.series;
    });
    function unavailable(reason) { return {value: null, reason: reason}; }
    if (matches.length !== 1) return unavailable(matches.length ? 'Ambiguous bar-series caption' : 'Selected bar series unavailable');
    if (inputReason) return unavailable(inputReason);
    var values = matches[0]._absData || matches[0].data || [];
    if (!values.length) return unavailable('No displayed entries');
    if (!values.every(_fwStackSummarySafe)) return unavailable('Nonfinite, unsafe or unavailable displayed values');
    var result;
    if (summary.stat === 'last') result = values[values.length - 1];
    else if (summary.stat === 'min' || summary.stat === 'max') {
        result = values[0];
        values.forEach(function(v) { result = summary.stat === 'min' ? Math.min(result, v) : Math.max(result, v); });
    } else {
        // Scaling also protects the mean from avoidable intermediate overflow.
        var scale = values.reduce(function(m, v) { return Math.max(m, Math.abs(v)); }, 0);
        var total = 0, correction = 0;
        values.forEach(function(v) {
            var next = scale ? v / scale - correction : 0;
            var updated = total + next;
            correction = (updated - total) - next; total = updated;
        });
        result = summary.stat === 'mean' ? total / values.length * scale : total * scale;
    }
    return _fwStackSummarySafe(result) ? {value: result, reason: ''} : unavailable('Nonfinite or unsafe summary result');
}
function _fwStackSummaryInputReason(cfg, rows) {
    if (!rows) return cfg.stacked_summary_input_reason || '';
    return rows.some(function(row) {
        return (cfg.value_cols || []).some(function(col) { return !_fwStackSummarySafe(row[col]); });
    }) ? 'Incomplete, nonnumeric, nonfinite or unsafe original bar values' : '';
}
function _fwClearStackSummary(canvas) {
    var owned = canvas._fwStackSummary;
    if (!owned) return;
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(token) {
        return token && token !== owned.description.id;
    });
    if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
    else canvas.removeAttribute('aria-describedby');
    if (owned.observer) owned.observer.disconnect();
    if (owned.box._fwStackSummaryCanvas === canvas) {
        var head = owned.box.parentElement;
        owned.box.remove();
        if (head && head.classList.contains('fw-chart-head') && !head.children.length) head.remove();
    }
    delete canvas._fwStackSummary;
}
function _fwUpdateStackSummary(canvas, summary, datasets, inputReason) {
    if (!summary) { _fwClearStackSummary(canvas); return; }
    var box = document.querySelector('[data-chart-summary="' + canvas.id + '"]');
    if (!box) {
        box = document.createElement('div');
        box.dataset.chartSummary = canvas.id;
        var head = document.createElement('div'); head.className = 'fw-chart-head';
        head.appendChild(box); canvas.parentElement.before(head);
    }
    box._fwStackSummaryCanvas = canvas;
    box.className = 'fw-chart-summary fw-stacked-summary';
    box.dataset.stackedSummary = 'true';
    box.style.removeProperty('display');
    box.removeAttribute('id'); box.removeAttribute('data-plotted-scope');
    var result = _fwStackSummaryResult(summary, datasets, inputReason);
    function span(cls, text) {
        var el = document.createElement('span'); el.className = cls; el.textContent = text; return el;
    }
    var value = result.value === null ? '—' : window.getFormatter(summary.format)(result.value);
    var description = span('fw-stacked-summary-description',
        'One final bar series (' + summary.series + ') in original absolute units, even in Percentage mode. ' +
        'All current filtered supplied bar inputs must be finite native numbers with safe integers. ' +
        'After existing aggregation, pivot, sorting, max_stacks and Other; before percentage normalization. ' +
        'Every displayed entry counts, including plotted zero-fill; none are skipped. Last is the final displayed entry. ' +
        'Legend hiding and zoom do not change scope. Delivered population only; no warehouse completeness claim. ' + result.reason);
    description.id = canvas.id + '-stacked-summary-scope';
    var children = [span('fw-chart-summary-label', summary.label), span('fw-chart-summary-value', value),
        span('fw-stacked-summary-scope', 'Original units · ' + summary.series)];
    if (result.reason) children.push(span('fw-stacked-summary-reason', result.reason));
    children.push(description); box.replaceChildren.apply(box, children);
    box.setAttribute('role', 'note'); box.title = description.textContent;
    box.setAttribute('aria-label', summary.label + ': ' + value + '. ' + description.textContent);
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(token) {
        return token && token !== canvas.id + '-summary-scope';
    });
    if (tokens.indexOf(description.id) < 0) tokens.push(description.id);
    canvas.setAttribute('aria-describedby', tokens.join(' '));
    var owned = canvas._fwStackSummary || {};
    owned.box = box; owned.description = description;
    canvas._fwStackSummary = owned;
    if (!owned.observer) {
        owned.observer = new MutationObserver(function() {
            if (document.getElementById(canvas.id) !== canvas) _fwClearStackSummary(canvas);
        });
        owned.observer.observe(document.body, {childList: true, subtree: true});
    }
}

/* Combo-only context. Use axis + caption identity, preserving intentional
   same-axis pairs. Reconcile nodes so chart.update() retains keyboard focus. */
window._fwComboLegendUpdate = function(chart) {
    var axes = chart.config.options.plugins.fwLegend.comboAxes;
    var root = chart._fwLegendEl;
    if (!root) {
        root = document.createElement('div');
        root.id = chart.canvas.id + '-combo-context';
        root.className = 'fw-chart-legend fw-combo-context';
        chart.canvas.parentElement.before(root);
        chart._fwLegendEl = root;
        var described = chart.canvas.getAttribute('aria-describedby') || '';
        chart.canvas.setAttribute('aria-describedby', (described + ' ' + root.id).trim());
    }
    var keyFor = function(ds, i) {
        return JSON.stringify([ds.yAxisID || 'y', ds.label || 'Series ' + (i + 1)]);
    };
    ['y', 'y1'].forEach(function(axis) {
        var entries = new Map();
        chart.data.datasets.forEach(function(ds, i) {
            if ((ds.yAxisID || 'y') !== axis) return;
            var key = keyFor(ds, i);
            if (!entries.has(key)) entries.set(key, {ds: ds, index: i});
        });
        var group = root.querySelector('[data-axis="' + axis + '"]');
        if (!entries.size) { if (group) group.remove(); return; }
        if (!group) {
            group = document.createElement('div');
            group.className = 'fw-combo-axis';
            group.dataset.axis = axis;
            group.setAttribute('role', 'group');
            var heading = document.createElement('div');
            heading.className = 'fw-combo-heading';
            heading.textContent = axis === 'y' ? 'Left · bars' : 'Right · lines';
            group.setAttribute('aria-label', axis === 'y' ? 'Left axis · bars' : 'Right axis · lines');
            var description = document.createElement('div');
            description.className = 'fw-combo-description';
            var items = document.createElement('div');
            items.className = 'fw-combo-items';
            group.append(heading, description, items);
            root.appendChild(group);
        }
        group.querySelector('.fw-combo-description').textContent =
            (axis === 'y' ? axes.left_label : axes.right_label) ||
            Array.from(entries.values()).map(function(e) {
                return e.ds.label || 'Series ' + (e.index + 1);
            }).join(', ');
        var items = group.querySelector('.fw-combo-items');
        var buttons = new Map(Array.from(items.children).map(function(btn) {
            return [btn.dataset.comboKey, btn];
        }));
        entries.forEach(function(entry, key) {
            var btn = buttons.get(key), ds = entry.ds, i = entry.index;
            if (!btn) {
                btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'cl';
                btn.dataset.comboKey = key;
                btn.title = 'Click to toggle  •  Double-click (or Shift-click) to show only this';
                var symbol = document.createElement('span');
                symbol.className = 'fw-combo-symbol ' + (axis === 'y1' ? 'line' : 'bar');
                symbol.setAttribute('aria-hidden', 'true');
                var label = document.createElement('span');
                label.className = 'fw-combo-label';
                label.textContent = ds.label || 'Series ' + (i + 1);
                btn.append(symbol, label);
                var isolate = function() {
                    var isolated = chart.data.datasets.every(function(d, j) {
                        return (keyFor(d, j) === key) === chart.isDatasetVisible(j);
                    });
                    chart.data.datasets.forEach(function(d, j) {
                        chart.setDatasetVisibility(j, isolated || keyFor(d, j) === key);
                    });
                    chart.update();
                };
                btn.addEventListener('click', function(e) {
                    if (e.shiftKey || e.metaKey) { isolate(); return; }
                    var first = chart.data.datasets.findIndex(function(d, j) { return keyFor(d, j) === key; });
                    var visible = chart.isDatasetVisible(first);
                    chart.data.datasets.forEach(function(d, j) {
                        if (keyFor(d, j) === key) chart.setDatasetVisibility(j, !visible);
                    });
                    chart.update();
                });
                btn.addEventListener('dblclick', function(e) {
                    e.preventDefault(); e.stopPropagation(); isolate();
                });
                items.appendChild(btn);
            }
            var visible = chart.isDatasetVisible(i);
            var color = ds.borderColor || ds.backgroundColor;
            if (typeof color === 'object') color = Array.isArray(color) ? color[0] : '#888';
            btn.style.setProperty('--series-color', color);
            btn.classList.toggle('active', visible);
            btn.setAttribute('aria-pressed', String(visible));
            btn.style.borderColor = visible ? color : 'var(--border-color)';
            btn.style.background = visible ? color + '20' : 'transparent';
        });
        buttons.forEach(function(btn, key) { if (!entries.has(key)) btn.remove(); });
    });
    root.hidden = !root.children.length;
};

/* New labels are opt-in by component tag, never by the shared bar factory.
   Eligibility inspects raw inputs before the legacy aggregators coerce nulls.
   These checks withhold labels; they do not repair or mutate plotted data. */
function _fwStackLabelEligibility(cfg, rows, stackBy, spec) {
    var cols = cfg.value_cols || [], expected = new Map(), reason = '';
    if (new Set(cols).size !== cols.length) return 'duplicate source columns';
    var stacks = new Set();
    function identity(v) { return String(v == null ? '' : v); }
    rows.forEach(function(row) {
        var x = identity(row[cfg.x]), s = stackBy ? identity(row[stackBy]) : '';
        if ((stackBy && (x.includes('||') || s.includes('||'))) ||
                [x, s].some(function(k) { return k in Object.prototype; })) reason = 'ambiguous grouping identity';
        if (stackBy) stacks.add(s);
        var total = expected.get(x) || 0;
        cols.forEach(function(col) {
            var v = row[col];
            if (typeof v !== 'number' || !Number.isFinite(v)) reason = 'incomplete or nonfinite quantities';
            else if (v < 0) reason = 'signed quantities';
            else total += v;
        });
        expected.set(x, total);
    });
    var limit = cfg.max_stacks == null ? 20 : cfg.max_stacks;
    if (stackBy && stacks.size > limit && stacks.has('Other (' + (stacks.size - limit) + ')'))
        reason = 'generated Other identity collides with a source category';
    if (reason) return reason;
    if (expected.size !== spec.labels.length) return 'plotted and supplied populations differ';
    for (var i = 0; i < spec.labels.length; i++) {
        var total = 0;
        for (var j = 0; j < spec.datasets.length; j++) {
            var ds = spec.datasets[j];
            if (ds.type === 'line' || ds.yAxisID === 'y1') continue;
            var v = (ds._absData || ds.data)[i];
            if (!Number.isFinite(v) || v < 0) return 'incomplete, signed or nonfinite plotted quantities';
            total += v;
        }
        var raw = expected.get(String(spec.labels[i]));
        if (!Number.isFinite(total) || !Number.isFinite(raw) || Math.abs(total - raw) > 1e-9 * Math.max(1, Math.abs(raw)))
            return 'plotted and supplied populations differ';
    }
    return '';
}

function _fwStaticStackLabelEligibility(cfg) {
    if (cfg.stack_labels.duplicate_columns) return 'duplicate source columns';
    for (var i = 0; i < cfg.datasets.length; i++) {
        var ds = cfg.datasets[i];
        if (ds.data.length !== cfg.labels.length) return 'plotted and supplied populations differ';
        for (var j = 0; j < ds.data.length; j++) {
            var v = ds.data[j];
            if (!Number.isFinite(v)) return 'incomplete or nonfinite quantities';
            if (v < 0) return 'signed quantities';
        }
    }
    return '';
}

/* Per-chart plugin. Drawing records are also useful to diagnostics: every record
   corresponds to one actual fillText, with measured bounds in canvas CSS pixels. */
function _fwBarLabelPlugin(spec) {
    var stack = spec.stackLabels, bar = spec.barValues;
    var enabledStack = stack && (stack.totals || stack.percentages);
    var context, labelCanvas;
    function font(chart) {
        return '600 ' + window._fwFontPx('--font-size-label', 12) + 'px ' + Chart.defaults.font.family;
    }
    function number(v) { return Number.isFinite(v) ? (spec.yFmt || window.fmtCompact)(v) : '\u2014'; }
    function horizontal() {
        return bar && bar.show !== false && spec.horizontal && !spec.stacked && !spec.barConfiguredStacked &&
            (bar.show === true || !spec.labels.length || !/^\d{4}-\d{2}-\d{2}/.test(String(spec.labels[0])));
    }
    function bars(chart) {
        return chart.data.datasets.map(function(ds, i) { return {ds: ds, meta: chart.getDatasetMeta(i), index: i}; })
            .filter(function(d) { return d.ds.type !== 'line' && d.ds.yAxisID !== 'y1' && chart.isDatasetVisible(d.index); });
    }
    function updateContext(chart) {
        if (!enabledStack) return;
        if (!context) {
            context = document.createElement('div');
            context.id = chart.canvas.id + '-stack-label-context';
            context.dataset.stackLabelContext = chart.canvas.id;
            context.style.cssText = 'font-size:var(--font-size-label);color:var(--text-secondary);margin-bottom:var(--spacing-md);overflow-wrap:anywhere';
            chart.canvas.parentElement.after(context);
            var described = chart.canvas.getAttribute('aria-describedby') || '';
            chart.canvas.setAttribute('aria-describedby', (described + ' ' + context.id).trim());
        }
        var text = stack.totals ? 'Totals include visible bar segments only, in absolute ' + (stack.format || 'currency') + ' units; line overlays are excluded.' : '';
        if (spec.stackNormalized) text += ' Percentages use all matching series, including hidden series. Zero-total buckets have no share.';
        else if (stack.percentages) text += ' Percentage labels are available only for normalized breakdowns.';
        if (spec.stackLabelReason) text += ' Labels withheld: ' + spec.stackLabelReason + '.';
        else if (!chart.data.labels.length) text += ' No observations; no total or share labels.';
        context.textContent = text.trim();
    }
    function contrast(chart, fill) {
        // Browser color parsing + alpha composition against the actual card.
        var ctx = chart.ctx;
        ctx.fillStyle = getComputedStyle(chart.canvas).getPropertyValue('--bg-card').trim() || '#fff';
        var bg = ctx.fillStyle;
        function rgba(color) {
            var probe = document.createElement('canvas'); probe.width = probe.height = 1;
            var p = probe.getContext('2d'); p.fillStyle = color; p.fillRect(0, 0, 1, 1);
            return p.getImageData(0, 0, 1, 1).data;
        }
        var b = rgba(bg), f = rgba(fill), a = f[3] / 255;
        var c = [0, 1, 2].map(function(i) {
            var n = (f[i] * a + b[i] * (1 - a)) / 255;
            return n <= 0.04045 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
        });
        var lum = c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
        return (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? '#000000' : '#ffffff';
    }
    return {
        id: 'fwBarLabels',
        beforeLayout: function(chart) {
            labelCanvas = chart.canvas;
            var ctx = chart.ctx, size = window._fwFontPx('--font-size-label', 12);
            ctx.save(); ctx.font = font(chart);
            var right = 0;
            if (horizontal()) {
                var max = 0;
                bars(chart).forEach(function(d) { d.ds.data.forEach(function(v) { max = Math.max(max, ctx.measureText(number(v)).width); }); });
                var reserve = Math.max(62, Math.ceil(max + 16));
                var category = 0;
                spec.labels.forEach(function(s) { category = Math.max(category, ctx.measureText(String(s)).width); });
                category = Math.min(chart.width / 2, category);
                if (chart.width > 260 && chart.width - category - reserve >= 80 && reserve <= chart.width / 3) right = reserve;
            }
            chart.options.layout.padding = {right: right, top: enabledStack && stack.totals && !spec.stackLabelReason && spec.labels.length ? size + 10 : 0};
            chart._fwBarLabelSpace = right;
            ctx.restore();
        },
        beforeBuildTicks: function(chart, args) {
            var scale = args.scale, padding = chart.options.layout.padding;
            if (spec.horizontal && padding.right && scale.axis === 'y') {
                // Keep the original category-label budget; the value column
                // consumes plot width rather than clipping more category text.
                scale.maxWidth = Math.max(scale.maxWidth, chart.width / 2);
            }
            if (scale.axis !== (spec.horizontal ? 'x' : 'y') || !scale.computeTickLimit) return;
            var extra = spec.horizontal ? padding.right : padding.top;
            if (!extra) return;
            // Keep the pre-label automatic tick budget. Reducing plot space
            // must not expand the numeric domain through coarser nice ticks.
            var compute = scale.computeTickLimit;
            scale._fwBarComputeTickLimit = compute;
            scale.computeTickLimit = function() {
                var budget = Object.create(this);
                if (spec.horizontal) budget.width = this.width + extra;
                else budget.height = this.height + extra;
                return compute.call(budget);
            };
        },
        afterBuildTicks: function(chart, args) {
            var scale = args.scale;
            if (scale._fwBarComputeTickLimit) {
                scale.computeTickLimit = scale._fwBarComputeTickLimit;
                delete scale._fwBarComputeTickLimit;
            }
        },
        afterUpdate: updateContext,
        afterDatasetsDraw: function(chart) {
            var ctx = chart.ctx, area = chart.chartArea, visible = bars(chart);
            var size = window._fwFontPx('--font-size-label', 12), used = [];
            chart._fwBarLabelPaint = [];
            ctx.save(); ctx.font = font(chart); ctx.textBaseline = 'middle';
            function draw(text, x, y, color, kind, index, series) {
                var w = ctx.measureText(text).width, rect = {left: x - w, right: x, top: y - size / 2, bottom: y + size / 2};
                if (rect.left < 0 || rect.right > chart.width || rect.top < 0 || rect.bottom > chart.height ||
                    used.some(function(r) { return rect.left < r.right + 3 && rect.right > r.left - 3 && rect.top < r.bottom + 2 && rect.bottom > r.top - 2; })) return;
                ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.fillText(text, x, y);
                used.push(rect); chart._fwBarLabelPaint.push({text: text, x: x, y: y, bounds: rect, kind: kind, index: index, series: series, color: color});
            }
            if (chart._fwBarLabelSpace) {
                visible.forEach(function(d) { d.meta.data.forEach(function(el, i) {
                    // Null bar elements retain their genuine grouped center Y.
                    if (!Number.isFinite(el.y) || el.y < area.top || el.y > area.bottom) return;
                    draw(number(d.ds.data[i]), chart.width - 4, el.y, window.getThemeColors().tick_color, 'value', i, d.index);
                }); });
            }
            if (enabledStack && !spec.stackLabelReason) {
                var totalFmt = window.getFormatter(stack.format || 'currency');
                chart.data.labels.forEach(function(_, i) {
                    var total = 0, all = 0, top = Infinity, x, width = 0;
                    chart.data.datasets.forEach(function(ds) {
                        if (ds.type !== 'line' && ds.yAxisID !== 'y1') all += (ds._absData || ds.data)[i];
                    });
                    visible.forEach(function(d) {
                        var el = d.meta.data[i], v = (d.ds._absData || d.ds.data)[i];
                        if (!el || !Number.isFinite(v)) return;
                        total += v; top = Math.min(top, el.y, el.base); x = el.x; width = el.width;
                        if (stack.percentages && spec.stackNormalized && all > 0 && v > 0) {
                            var text = Math.round(d.ds.data[i]) + '%', w = ctx.measureText(text).width;
                            var height = Math.abs(el.base - el.y), y = (el.y + el.base) / 2;
                            if (w + 8 <= el.width && size + 6 <= height && el.x - w / 2 >= area.left &&
                                el.x + w / 2 <= area.right && y - size / 2 >= area.top && y + size / 2 <= area.bottom)
                                draw(text, el.x + w / 2, y, contrast(chart, el.options.backgroundColor), 'percentage', i, d.index);
                        }
                    });
                    if (stack.totals && Number.isFinite(top) && Number.isFinite(total) && x >= area.left && x <= area.right) {
                        var text = totalFmt(total), w = ctx.measureText(text).width;
                        // Center above the real visible stack; omit dense labels.
                        if (w + 4 <= Math.max(width, chart.scales.x.width / Math.max(1, chart.data.labels.length)))
                            draw(text, x + w / 2, top - size / 2 - 4, window.getThemeColors().tick_color, 'total', i, null);
                    }
                });
            }
            ctx.restore();
        },
        afterDestroy: function(chart) {
            if (context) { context.remove(); context = null; }
            if (!labelCanvas) return;
            var described = (labelCanvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(id) { return id !== labelCanvas.id + '-stack-label-context'; });
            if (described.length) labelCanvas.setAttribute('aria-describedby', described.join(' '));
            else labelCanvas.removeAttribute('aria-describedby');
        }
    };
}

/* Availability is opt-in and is evaluated from delivered raw contributors,
   never from legacy skip-null subtotals or formatted values. Static rules carry
   invalid source positions so duplicate labels remain independent points. */
function _fwApplyLineAvailability(cfg, rows, spec) {
    var rules = cfg.series_availability;
    if (!rules || !rules.length) return spec;
    var buckets = new Map();
    if (cfg.dataset_id) rows.forEach(function(row) {
        var key = String(row[cfg.x] != null ? row[cfg.x] : '');
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(row);
    });
    spec = Object.assign({}, spec, {datasets: spec.datasets.map(function(ds) {
        return Object.assign({}, ds, {data: ds.data.slice()});
    })});
    spec.availability = [];
    rules.forEach(function(rule) {
        var ds = spec.datasets[rule.series];
        if (!ds) return;
        ds.spanGaps = false;
        var invalid = new Set(rule.unavailable || []), unavailable = [];
        spec.labels.forEach(function(label, index) {
            var kind = '';
            if (!cfg.dataset_id) {
                if (invalid.has(index)) kind = 'inputs';
            } else {
                var contributors = buckets.get(String(label)) || [];
                if (!contributors.length || contributors.some(function(row) {
                    return rule.required_columns.some(function(col) { return !Number.isFinite(row[col]); }) ||
                        (rule.state_column !== undefined && row[rule.state_column] !== 'available');
                })) kind = 'inputs';
                else if (cfg.ratios) {
                    var ratio = cfg.ratios[rule.series];
                    var num = 0, den = 0;
                    contributors.forEach(function(row) { num += row[ratio.numerator]; den += row[ratio.denominator]; });
                    if (!Number.isFinite(den) || den <= 0) kind = 'denominator';
                    else if (!Number.isFinite(num) || !Number.isFinite(num / den)) kind = 'aggregate';
                } else {
                    var total = 0, column = cfg.value_cols[rule.series];
                    contributors.forEach(function(row) { total += row[column]; });
                    if (!Number.isFinite(total)) kind = 'aggregate';
                }
            }
            if (kind) {
                ds.data[index] = null;
                unavailable.push({label: label, kind: kind});
            }
        });
        if (unavailable.length) spec.availability.push({series: rule.series, reason: rule.reason, points: unavailable});
    });
    return spec;
}

function _fwDescribeLineAvailability(canvas, add) {
    var id = canvas.id + '-availability';
    var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/)
        .filter(function(token) { return token && token !== id; });
    if (add) tokens.push(id);
    if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
    else canvas.removeAttribute('aria-describedby');
}

function _fwLineAvailabilityPlugin(availability) {
    var note = null, owner = null, observer = null;
    function clear() {
        if (note) note.remove();
        if (owner) _fwDescribeLineAvailability(owner, false);
        note = null;
    }
    return {
        id: 'fwLineAvailability',
        afterInit: function(chart) {
            owner = chart.canvas;
            observer = new MutationObserver(function() {
                if (document.getElementById(owner.id) === owner) return;
                chart.destroy();
                if (window._chartInstances[owner.id] === chart) delete window._chartInstances[owner.id];
            });
            observer.observe(document.body, {childList: true, subtree: true});
        },
        afterUpdate: function(chart) {
            owner = chart.canvas;
            var visible = availability.filter(function(item) { return chart.isDatasetVisible(item.series); });
            if (!visible.length) { clear(); return; }
            if (!note) {
                note = document.createElement('div');
                note.id = owner.id + '-availability';
                note.className = 'fw-line-availability';
            }
            var lines = visible.map(function(item) {
                var ds = chart.data.datasets[item.series];
                var metric = ds.label || ('Series ' + (item.series + 1));
                var reasons = item.reason ? [item.reason] : Array.from(new Set(item.points.map(function(p) {
                    return p.kind === 'denominator' ? 'No valid positive denominator.'
                        : p.kind === 'aggregate' ? 'The calculated value is not finite.'
                        : 'Required observations are incomplete or unavailable.';
                })));
                var labels = item.points.slice(0, 3).map(function(p) { return String(p.label); }).join(', ');
                var more = item.points.length > 3 ? '; ' + (item.points.length - 3) + ' more' : '';
                return metric + ': ' + item.points.length + ' unavailable ' +
                    (item.points.length === 1 ? 'observation' : 'observations') + ' (' + labels + more + '). ' + reasons.join(' ');
            });
            var text = 'Selected data: ' + lines.join(' ');
            if (note.textContent !== text) note.textContent = text;
            // Follow the native plot/month/event siblings; never move event DOM
            // (it may own keyboard focus) to put our own final sibling in place.
            var anchor = document.getElementById(owner.id + '-event-details') ||
                document.getElementById(owner.id + '-month-context') || owner.parentElement;
            if (anchor && anchor.nextElementSibling !== note) anchor.after(note);
            _fwDescribeLineAvailability(owner, true);
        },
        afterDestroy: function() {
            if (observer) observer.disconnect();
            clear();
        }
    };
}

/* Shared chart factory -- called by both static and live modes.
   spec: { type, labels, datasets, stacked, horizontal, showLegend, yFmt, tooltip } */
function _fwLineResultView(chart) {
    return {signature: JSON.stringify([chart.data.labels, chart.data.datasets.map(function(ds) { return [ds.label, ds.data]; })]),
        hidden: chart.data.datasets.map(function(_, i) { return !chart.isDatasetVisible(i); }),
        min: chart.options.scales.x.min, max: chart.options.scales.x.max};
}

function _createChart(id, canvas, spec) {
    var previous = window._chartInstances[id];
    if (spec.stackedControlView) {
        if (previous && previous._fwStackControlView) _fwRememberStackControlView(previous);
        var stackHidden = canvas._fwStackControlHidden || {};
        spec.datasets.forEach(function(ds) {
            ds.hidden = stackHidden[JSON.stringify([spec.stackedControlView, ds.label])] === true;
        });
    }
    // A status/theme/reentry draw of the same ordinary result keeps the user's
    // view. A genuinely different result retains the existing reset behavior.
    var lineView = spec.lineResultPresentation ? (previous ? _fwLineResultView(previous) : canvas._fwLineResultView) : null;
    if (lineView && lineView.signature !== JSON.stringify([spec.labels, spec.datasets.map(function(ds) { return [ds.label, ds.data]; })])) lineView = null;
    var eventDetails = spec.eventLabels === 'grouped' && previous && previous._fwTakeEventDetails
        ? previous._fwTakeEventDetails() : (spec.eventLabels === 'grouped' && canvas._fwEventDetailsState);
    var comboFocus = spec.comboAxes && document.activeElement &&
        document.activeElement.closest('.fw-combo-context') &&
        document.activeElement.closest('.fw-combo-context').id === id + '-combo-context'
        ? document.activeElement.dataset.comboKey : (spec.comboAxes && canvas._fwComboFocusKey);
    if (window._chartInstances[id]) {
        window._chartInstances[id].destroy();
        delete window._chartInstances[id];
    }
    delete canvas._fwEventDetailsState;
    delete canvas._fwLineResultView;
    // A cloned replacement canvas may still carry the previous description.
    if (!spec.availability) _fwDescribeLineAvailability(canvas, false);
    if (!spec.monthlyAverage && window._fwClearMonthlyAverage) window._fwClearMonthlyAverage(canvas);
    if (!spec.monthContext && window._fwClearMonthContext) window._fwClearMonthContext(canvas);
    var tc = window.getThemeColors();
    var axisFont = window._fwFontPx('--font-size-axis', 12);
    var labelFont = window._fwFontPx('--font-size-label', 12);
    var events = window._reportData && window._reportData._events;
    var datedEvents = spec.eventLabels === 'grouped' ? [] : null;
    var annos = window._buildAnnotations ? window._buildAnnotations(events, spec.labels, datedEvents) : {};
    var plugins = {
        legend: { display: false },
        fwLegend: { display: !!spec.showLegend },
        annotation: { annotations: annos },
        zoom: { zoom: { drag: { enabled: true, threshold: 8 }, mode: 'x' } },
        datalabels: spec.showLabels ? {
            display: true,
            color: function() { return tc.tick_color; },
            font: { size: labelFont, weight: 600 },
            anchor: 'end',
            align: 'top',
            formatter: function(v, ctx) {
                // For combo charts, check if this is a line (y1 axis) or bar (y axis)
                if (ctx.dataset.yAxisID === 'y1' && spec.y1Fmt) {
                    return spec.y1Fmt(v);
                }
                return (spec.yFmt || fmtCompact)(v);
            }
        } : { display: false }
    };
    if (spec.comboAxes) plugins.fwLegend.comboAxes = spec.comboAxes;
    if (spec.tooltip) {
        plugins.tooltip = spec.tooltip;
    } else if (spec.horizontal) {
        /* Horizontal bars: value lives on X, default Chart.js tooltip
           body uses parsed.y which is the category — show parsed.x instead. */
        var _httFmt = spec.yFmt || function(v) { return fmtCompact(v); };
        plugins.tooltip = { callbacks: { label: function(ctx) {
            var lbl = ctx.dataset.label || '';
            return (lbl ? lbl + ': ' : '') + _httFmt(ctx.parsed.x);
        }}};
    }
    var _numFmt = spec.yFmt || function(v) { return fmtCompact(v); };
    var scales = spec.horizontal ? {
        /* horizontal bars: X carries values, Y carries categories */
        x: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, maxRotation: 0,
                      callback: _numFmt } },
        y: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont } } }
    } : {
        /* vertical bars / lines: X carries categories, Y carries values */
        x: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, maxRotation: 0 } },
        y: { stacked: !!spec.stacked, grid: { color: tc.grid_color },
             ticks: { color: tc.tick_color, font: { size: axisFont }, callback: _numFmt } }
    };
    /* Optional fixed value-axis range — value axis is X for horizontal, Y otherwise */
    var _valueAxis = spec.horizontal ? scales.x : scales.y;
    if (spec.yMin !== undefined) _valueAxis.min = spec.yMin;
    if (spec.yMax !== undefined) _valueAxis.max = spec.yMax;
    if (spec.datasets.some(function(ds) { return ds.yAxisID === 'y1'; })) {
        scales.y1 = {
            position: 'right', stacked: false,
            grid: { drawOnChartArea: false, color: tc.grid_color },
            ticks: { color: tc.tick_color, font: { size: axisFont }, callback: spec.y1Fmt || function(v) { return fmtCompact$(v); } }
        };
        if (spec.y1Max !== undefined) scales.y1.max = spec.y1Max;
    }
    if (spec.labels && spec.labels.length > 0 && !spec.horizontal
            && /^\d{4}-\d{2}-\d{2}/.test(String(spec.labels[0]))) {
        scales.x.ticks.callback = function(val, idx) {
            var lbl = this.getLabelForValue(val);
            if (!lbl) return lbl;
            var parts = String(lbl).split('-');
            return parts[1] + '/' + parts[2];
        };
        scales.x.ticks.maxTicksLimit = 15;
    }
    if ((spec.type || 'bar') === 'bar') {
        spec.datasets.forEach(function(ds) {
            if (ds.borderRadius === undefined) ds.borderRadius = 3;
        });
    }
    (spec.forecastSeries || []).forEach(function(index) {
        if (spec.datasets[index]) spec.datasets[index]._fwForecast = true;
    });
    if (spec.lineStyle === 'adaptive') {
        spec.datasets.forEach(function(ds) {
            ds.pointRadius = _fwLinePointRadius;
            ds.pointBorderColor = _fwLinePointColor;
            ds.pointBackgroundColor = _fwLinePointFill;
            ds.tension = spec.tension;
        });
    }
    spec.datasets.forEach(function(ds) {
        if (ds._fwForecast) {
            ds.borderDash = [7, 4];
            ds.pointBackgroundColor = _fwLinePointFill;
            ds.pointBorderColor = _fwLinePointColor;
        }
    });
    if (spec.lineStyle !== 'adaptive' && (spec.type === 'line') && spec.labels && spec.labels.length > 400) {
        spec.datasets.forEach(function(ds) {
            if (ds.pointRadius === undefined || ds.pointRadius > 0) ds.pointRadius = 0;
        });
    }
    var chartOpts = {
        responsive: true, maintainAspectRatio: false,
        animation: false,
        indexAxis: spec.horizontal ? 'y' : 'x',
        plugins: plugins,
        scales: scales
    };
    if (lineView) {
        spec.datasets.forEach(function(ds, i) { ds.hidden = lineView.hidden[i]; });
        if (lineView.min !== undefined) scales.x.min = lineView.min;
        if (lineView.max !== undefined) scales.x.max = lineView.max;
    }

    /* ── Cross-filter click handler ── */
    if (spec.crossFilter && spec.dsId) {
        chartOpts.onClick = function(evt, elements) {
            var chart = window._chartInstances[id];
            if (!chart || !elements.length) return;
            var prev = chart._crossFilterValue || null;
            var el = elements[0];
            var clicked;

            if (spec.crossFilterMode === 'dataset') {
                clicked = String(chart.data.datasets[el.datasetIndex].label);
            } else {
                clicked = String(chart.data.labels[el.index]);
            }

            if (clicked === prev) {
                chart._crossFilterValue = null;
                window._fwCrossFilter(spec.dsId, spec.crossFilter, null);
            } else {
                chart._crossFilterValue = clicked;
                window._fwCrossFilter(spec.dsId, spec.crossFilter, clicked);
            }
        };
    }

    window._chartInstances[id] = new Chart(canvas, {
        type: spec.type || 'bar',
        data: { labels: spec.labels, datasets: spec.datasets },
        options: chartOpts,
        plugins: (spec.stackedControlView ? [{id: 'fwStackControlView', beforeDestroy: _fwRememberStackControlView}] : []).concat(spec.lineResultPresentation ? [{id: 'fwLineResultView', beforeDestroy: function(chart) {
            if (canvas.isConnected) canvas._fwLineResultView = _fwLineResultView(chart);
        }}] : []).concat(spec.monthContext ? [window._fwMonthContextPlugin()] : []).concat((spec.barValues || spec.stackLabels) ? [_fwBarLabelPlugin(spec)] : []).concat(
            spec.eventLabels === 'grouped' && window._fwLineEventLabelsPlugin
                ? [window._fwLineEventLabelsPlugin(eventDetails, datedEvents)] : []).concat(
            spec.availability ? [_fwLineAvailabilityPlugin(spec.availability)] : []).concat(
            spec.monthlyAverage ? [window._fwMonthlyAveragePlugin(spec.monthlyAverage)] : [])
    });
    if (spec.stackedControlView) window._chartInstances[id]._fwStackControlView = spec.stackedControlView;
    window._chartInstances[id]._fwThemeManagedAxes = true;
    window._chartInstances[id]._fwThemeManagedFonts = true;
    if (comboFocus) {
        var context = window._chartInstances[id]._fwLegendEl;
        var focused = context && Array.from(context.querySelectorAll('button')).find(function(btn) {
            return btn.dataset.comboKey === comboFocus;
        });
        if (focused) focused.focus({ preventScroll: true });
    }
    if (spec.comboAxes) delete canvas._fwComboFocusKey;
    if (spec.stackedSummary) _fwUpdateStackSummary(canvas, spec.stackedSummary, spec.datasets, spec.stackedSummaryInputReason);
    else _fwUpdateHeaderSummary(id, spec.headerSummary, spec.datasets, spec.yFmt);
}

// Resolve only the engine's registered parent edges. Never infer a live source
// from controls or recursively call isReady on an unchecked scope chain.
function _fwLineResultSource(engine, id) {
    var seen = new Set(), current = id;
    for (var depth = 0; current && depth < 64; depth++) {
        if (seen.has(current)) return null;
        seen.add(current);
        var scope = engine && engine._scopes[current];
        if (!scope) return {id: current, subset: current !== id};
        current = scope.parent;
    }
    return null;
}

function _fwLineResultParams(params) {
    if (params === null || params === undefined) return 'unknown';
    var keys = Object.keys(params).sort();
    return keys.length ? keys.map(function(key) {
        return key + '=' + JSON.stringify(params[key]);
    }).join(', ') : '(no parameters)';
}

// Classification uses the full unhidden plotted vectors, before legend/zoom.
// It never edits chart data, aggregates rows, or changes capture admission.
function _fwLineResultText(cfg, spec, rows, source, state, engine) {
    var usable = (spec.datasets || []).some(function(ds) {
        return (ds.data || []).some(function(v) { return Number.isFinite(v); });
    });
    var live = !!state || !!(source && (source.live || (engine && engine.isLiveDataset(source.id))));
    var transition = state && (state.phase === 'pending' || state.phase === 'error');
    var requested = state ? _fwLineResultParams(state.requested_params) : 'unknown';
    var displayed = state ? _fwLineResultParams(state.displayed_params) : 'unknown';
    var same = state && state.displayed_params !== null && requested === displayed;
    var action = state && state.phase === 'error' ? 'Could not load requested data' : 'Loading requested data';
    var context = '';
    if (usable && state && state.has_result && transition && !same) {
        context = action + '. Requested: ' + requested + '\nDisplayed ' +
            (state.origin === 'snapshot' ? 'snapshot' : 'query') + ': ' + displayed +
            (source.subset ? '\nLocal subset of the displayed live parent (' + source.id + ').' : '');
    }
    if (usable && (!live || (state && state.has_result))) return {overlay: '', context: context};
    if (transition) return {overlay: action + '\nRequested: ' + requested, context: ''};
    if (live && (!state || !state.has_result)) return {overlay: 'Result unavailable', context: ''};
    if (cfg.dataset_id && (!source || !engine || !engine._columnar[source.id])) {
        return {overlay: 'Data not ready', context: ''};
    }
    var count = cfg.dataset_id ? (rows || []).length : (spec.labels || []).length;
    if (count) {
        var reasons = (spec.availability || []).map(function(item) { return item.reason; }).filter(Boolean);
        var unavailable = (spec.availability || []).length > 0;
        return {overlay: (unavailable ? 'Observations unavailable' : 'No usable observations') +
            (unavailable ? '\n' + (reasons.length ? Array.from(new Set(reasons)).join(' ') :
                'Required observations are incomplete or unavailable.') : ''), context: ''};
    }
    var supplied = !cfg.dataset_id || (!live && !source.subset && engine.getDatasetRowCount(source.id) === 0);
    return {overlay: supplied ? 'No data supplied' : 'No matching data', context: ''};
}

function _fwUpdateLineResult(canvas, cfg, spec, rows) {
    var enabled = cfg.line_result_presentation === true && cfg.chartType === 'line' &&
        !cfg.area_fill && !cfg.combo_axes && !window._fwMetricsWorkspace;
    var owned = canvas._fwLineResult;
    if (owned && (!enabled || owned.cfg !== cfg)) { owned.dispose(); owned = null; }
    if (!enabled) return spec;
    spec.lineResultPresentation = true;
    if (!owned) {
        var box = canvas.parentElement;
        var context = document.createElement('div'), overlay = document.createElement('div');
        context.className = 'fw-line-result-context';
        overlay.className = 'fw-line-result-overlay';
        context.id = canvas.id + '-result-context'; overlay.id = canvas.id + '-result';
        [context, overlay].forEach(function(el) {
            el.hidden = true; el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
            el.setAttribute('aria-atomic', 'true');
        });
        box.before(context); box.appendChild(overlay);
        function retire() {
            owned.dispose();
            var bindings = window._fwCategoryChartBindings;
            if (bindings && bindings.has(canvas.id)) bindings.get(canvas.id)();
            delete canvas._fwBarLive;
        }
        var off = null, observer = new MutationObserver(function() {
            if (document.getElementById(canvas.id) !== canvas ||
                    (owned.scope && owned.scope !== window._currentScope)) retire();
        });
        function describe() {
            var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(token) {
                return token && token !== context.id && token !== overlay.id;
            });
            if (!context.hidden) tokens.push(context.id);
            if (!overlay.hidden) tokens.push(overlay.id);
            if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
            else canvas.removeAttribute('aria-describedby');
        }
        owned = {
            cfg: cfg,
            paint: function(state) {
                if (document.getElementById(canvas.id) !== canvas) { owned.dispose(); return; }
                // Native report scope switches retire the live owner while
                // keeping the old scope's canvas connected but hidden.
                if (!state && owned.source && owned.source.live) {
                    retire();
                    return;
                }
                var text = _fwLineResultText(cfg, owned.spec, owned.rows, owned.source, state, window._fwFilterEngine);
                [[context, text.context], [overlay, text.overlay]].forEach(function(pair) {
                    if (pair[0].textContent !== pair[1]) pair[0].textContent = pair[1];
                    pair[0].hidden = !pair[1];
                });
                describe();
            },
            update: function(next, selectedRows) {
                owned.spec = next; owned.rows = selectedRows;
                owned.scope = window._currentScope;
                owned.source = _fwLineResultSource(window._fwFilterEngine, cfg.dataset_id);
                if (owned.source) owned.source.live = !!window._fwFilterEngine.isLiveDataset(owned.source.id);
                // Renew on native draws/reentry, including retired/replaced sources.
                // Status callbacks only paint text and never rebind or redraw.
                if (off) { off(); off = null; }
                if (owned.source && owned.source.live && window._fwLiveQuery) {
                    off = window._fwLiveQuery.subscribeDataset(owned.source.id, owned.paint);
                } else owned.paint(null);
            },
            dispose: function() {
                if (off) { off(); off = null; }
                observer.disconnect(); context.hidden = overlay.hidden = true; describe();
                context.remove(); overlay.remove();
                delete canvas._fwLineResultView;
                if (canvas._fwLineResult === owned) delete canvas._fwLineResult;
            }
        };
        canvas._fwLineResult = owned;
        observer.observe(document.body, {childList: true, subtree: true});
        var scopePanel = canvas.closest('[data-toggle-target="__scope__"]');
        if (scopePanel) observer.observe(scopePanel, {attributes: true, attributeFilter: ['style']});
    }
    owned.update(spec, rows);
    return spec;
}

// Shared-control views retain each chart's own label visibility, never the
// sibling chart's. The denominator still uses all matching series.
function _fwRememberStackControlView(chart) {
    var key = chart._fwStackControlView;
    if (!key) return;
    var hidden = chart.canvas._fwStackControlHidden || (chart.canvas._fwStackControlHidden = {});
    chart.data.datasets.forEach(function(ds, i) {
        hidden[JSON.stringify([key, ds.label])] = !chart.isDatasetVisible(i);
    });
}

window._fwRenderers['chart'] = function renderChart(id, cfg) {
    var canvas = document.getElementById(id);
    if (!canvas) return;
    if (canvas._fwLineResult && (cfg.line_result_presentation !== true || window._fwMetricsWorkspace)) canvas._fwLineResult.dispose();
    if (!cfg.stacked_summary) _fwClearStackSummary(canvas);
    if (!cfg.header_summary) _fwUpdateHeaderSummary(id, null);
    var previousBindings = window._fwCategoryChartBindings;
    var ownsAvailability = !!(cfg.series_availability && cfg.series_availability.length);
    if (ownsAvailability && !canvas._fwAvailabilityOwned) {
        canvas._fwAvailabilityOwned = true;
        // First adoption also installs the scoped detached-canvas cleanup.
        delete canvas._fwBarLive;
    }
    // Modern ordinary lines/bars may gain/remove declarations during renderAll.
    // Keep one scoped binding before opt-in so adoption cannot retain an old
    // anonymous theme handler or a second subscription.
    var scopedLive = cfg.stack_labels || cfg.stacked_bar_controls || cfg.category_colors || cfg.line_style === 'adaptive' || cfg.bar_values || canvas._fwAvailabilityOwned;
    // Tagged StackedBars own local/shared controls from the first draw, so
    // adding/removing summaries cannot strand anonymous legacy handlers.
    // Legacy untagged payloads and workspace leasing keep their existing path.
    var stackOwner = !!(cfg.stack_labels || cfg.stacked_bar_controls === true) && !window._fwMetricsWorkspace;
    var stackScope = window._currentScope;
    var stackSource = stackOwner && window._fwFilterEngine._columnar[cfg.dataset_id];
    var stackGroup = stackOwner && Array.from(document.querySelectorAll('[data-toggle-id], [data-stack-toggle], [data-normalize-toggle]')).find(function(el) {
        return cfg.stack_toggle_id ? el.dataset.toggleId === cfg.stack_toggle_id : el.dataset.stackToggle === id;
    });
    var normalizeGroup = stackOwner && Array.from(document.querySelectorAll('[data-toggle-id], [data-stack-toggle], [data-normalize-toggle]')).find(function(el) {
        return cfg.normalize_toggle_id ? el.dataset.toggleId === cfg.normalize_toggle_id : el.dataset.normalizeToggle === id;
    });
    var oldStack = canvas._fwBarLive;
    if (stackOwner && previousBindings && previousBindings.has(id) && (!oldStack || oldStack.cfg !== cfg ||
            oldStack.scope !== stackScope || oldStack.source !== stackSource ||
            oldStack.stackGroup !== stackGroup || oldStack.normalizeGroup !== normalizeGroup)) {
        previousBindings.get(id)();
    }
    if ((!scopedLive || !cfg.dataset_id || window._fwMetricsWorkspace) && previousBindings && previousBindings.has(id)) {
        previousBindings.get(id)(); previousBindings.delete(id);
        // The cached draw belonged to the binding just disposed. A reused
        // StackedBar config still has stack_labels, so it must bind again.
        delete canvas._fwBarLive;
    }

    /* ── Live mode ─────────────────────────────────────────── */
    if (cfg.dataset_id) {
        var taggedBars = cfg.bar_values || cfg.stack_labels || scopedLive;
        // Ordinary renderAll reuses the existing live subscription and controls.
        // Workspaces retain _fwLiveWrap's leased disposal/recreation behavior.
        if (taggedBars && !window._fwMetricsWorkspace && canvas._fwBarLive && canvas._fwBarLive.cfg === cfg) {
            canvas._fwBarLive.draw(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            return;
        }
        if (taggedBars && canvas._fwBarControls) canvas._fwBarControls.abort();
        var barSignals = taggedBars ? new AbortController() : null;
        if (barSignals) canvas._fwBarControls = barSignals;
        var barListener = barSignals ? {signal: barSignals.signal} : undefined;
        var activeStackBy = cfg.stack_by || null;
        if (cfg.stack_by_options) {
            var stackSel = cfg.stack_toggle_id
                ? '[data-toggle-id="' + cfg.stack_toggle_id + '"]'
                : '[data-stack-toggle="' + id + '"]';
            var tog = stackOwner ? stackGroup : document.querySelector(stackSel);
            if (tog) {
                var initBtn = tog.querySelector('.fw-toggle-btn.active');
                if (initBtn) activeStackBy = cfg.stack_by_options[initBtn.textContent.trim()] || cfg.stack_by;
                tog.addEventListener('click', function(e) {
                    var btn = e.target.closest('.fw-toggle-btn');
                    if (!btn) return;
                    if (!cfg.stack_toggle_id) {
                        tog.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
                        btn.classList.add('active');
                    }
                    activeStackBy = cfg.stack_by_options[btn.textContent.trim()] || cfg.stack_by;
                    _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
                }, barListener);
            }
        }
        var normalized = false;
        if (cfg.normalize_toggle) {
            var normSel = cfg.normalize_toggle_id
                ? '[data-toggle-id="' + cfg.normalize_toggle_id + '"]'
                : '[data-normalize-toggle="' + id + '"]';
            var normTog = stackOwner ? normalizeGroup : document.querySelector(normSel);
            if (normTog) {
                var initNormBtn = normTog.querySelector('.fw-toggle-btn.active');
                if (initNormBtn) normalized = initNormBtn.textContent.trim() === 'Percentage';
                normTog.addEventListener('click', function(e) {
                    var btn = e.target.closest('.fw-toggle-btn');
                    if (!btn) return;
                    if (!cfg.normalize_toggle_id) {
                        normTog.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
                        btn.classList.add('active');
                    }
                    normalized = btn.textContent.trim() === 'Percentage';
                    _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
                }, barListener);
            }
        }
        canvas.addEventListener('dblclick', function() {
            var c = window._chartInstances[id]; if (c && c.resetZoom) c.resetZoom();
        }, barListener);

        function _mkSpec(spec) {
            // Preserve the existing shared-only legend retention contract.
            if (stackOwner && cfg.stacked_bar_controls === true) spec.stackedControlView = [cfg.dataset_id, activeStackBy];
            if (cfg.month_context === true) spec.monthContext = true;
            if (cfg.event_labels) spec.eventLabels = cfg.event_labels;
            if (cfg.bar_values) {
                spec.barValues = cfg.bar_values;
                spec.barConfiguredStacked = cfg.stacked;
            }
            if (cfg.stack_labels) {
                spec.stackLabels = cfg.stack_labels;
                if (cfg.stack_labels.totals || cfg.stack_labels.percentages) {
                    spec.stackNormalized = !!(activeStackBy && cfg.normalize_toggle && normalized);
                    spec.stackLabelReason = _fwStackLabelEligibility(cfg, labelRows, activeStackBy, spec);
                }
            }
            if (cfg.line_style === 'adaptive') {
                spec.lineStyle = cfg.line_style;
                spec.tension = cfg.tension;
            }
            if (cfg.forecast_series) spec.forecastSeries = cfg.forecast_series;
            if (cfg.crossFilter && cfg.dataset_id) {
                spec.crossFilter = (cfg.crossFilterMode === 'dataset' && activeStackBy)
                    ? activeStackBy : cfg.crossFilter;
                spec.dsId = cfg.dataset_id;
                if (cfg.crossFilterMode) spec.crossFilterMode = cfg.crossFilterMode;
            }
            if (cfg.y_min !== undefined) spec.yMin = cfg.y_min;
            if (cfg.y_max !== undefined) spec.yMax = cfg.y_max;
            if (cfg.y1_max !== undefined) spec.y1Max = cfg.y1_max;
            if (cfg.show_labels) spec.showLabels = true;
            if (cfg.stacked_summary) {
                spec.stackedSummary = cfg.stacked_summary;
                spec.stackedSummaryInputReason = _fwStackSummaryInputReason(cfg, labelRows);
            }
            if (cfg.header_summary) spec.headerSummary = cfg.header_summary;
            return _fwUpdateLineResult(canvas, cfg,
                window._fwMonthlyAverageSpec(cfg, labelRows, _fwApplyLineAvailability(cfg, labelRows, spec)), labelRows);
        }

        var labelRows;
        function _drawLive(rows) {
            labelRows = rows;
            var colors = window.getThemeColors().chart_colors;
            var declaration = cfg.category_colors && cfg.category_colors.column === activeStackBy
                ? cfg.category_colors : null;
            var membership = declaration ? window._fwCategoryMembership(rows, activeStackBy) : null;
            function categoryColor(category) {
                var fallback = window._fwGetLabelColor(cfg.dataset_id, category, colors);
                return declaration ? window._fwCategoryColor(declaration, category, colors, membership) || fallback : fallback;
            }
            var isLine = cfg.chartType === 'line';

            if (activeStackBy && isLine) {
                /* LineChart with stack_by — pivot rows by the stack column,
                   produce one line per distinct value. Works with both
                   value_cols (sum each col across pivot, summed into one
                   line per stack value) and ratios (compute numerator /
                   denominator per (x, stack) cell). Long-format friendly. */
                var MAX_LINES = (cfg.max_stacks != null) ? cfg.max_stacks : 20;
                var pvL = window._fwAggregate.pivot(rows, cfg.x, activeStackBy, cfg.value_cols, cfg.stack_sort);
                var lineDatasets = [];
                var stackLimit = Math.min(pvL.stackNames.length, MAX_LINES);

                if (cfg.ratios) {
                    /* One line per (stack, ratio) — but in practice
                       stack_by is used with ONE ratio (e.g. "iap_revenue
                       / dau" split by platform). With multiple ratios
                       the label is "{stackValue} {ratio.label}". */
                    var multipleRatios = cfg.ratios.length > 1;
                    for (var sli = 0; sli < stackLimit; sli++) {
                        var lname = pvL.stackNames[sli];
                        var lcolData = pvL.datasets[lname];
                        for (var lri = 0; lri < cfg.ratios.length; lri++) {
                            var lspec = cfg.ratios[lri];
                            var lRatData = pvL.labels.map(function(_, lxi) {
                                var lnum = (lcolData[lspec.numerator] || [])[lxi] || 0;
                                var lden = (lcolData[lspec.denominator] || [])[lxi] || 0;
                                return lden > 0 ? lnum / lden : null;
                            });
                            var lcolor = categoryColor(lname);
                            var llabel = multipleRatios ? (lname + ' · ' + lspec.label) : lname;
                            lineDatasets.push({
                                label: llabel, data: lRatData,
                                backgroundColor: lcolor + '22', borderColor: lcolor,
                                borderWidth: 2.5, pointRadius: 2, tension: 0
                            });
                        }
                    }
                    var ratFmtL = {
                        currency: function(v) { return v == null ? '' : '$' + v.toFixed(2); },
                        percent: function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; },
                        minutes: function(v) { return v == null ? '' : v.toFixed(1) + 'm'; },
                        decimal1: function(v) { return v == null ? '' : v.toFixed(1); }
                    };
                    var yFmtL = ratFmtL[cfg.y_format] || undefined;
                    var ttL = yFmtL ? { callbacks: { label: function(ctx) {
                        return ctx.dataset.label + ': ' + yFmtL(ctx.parsed.y);
                    }}} : undefined;
                    _createChart(id, canvas, _mkSpec({
                        type: 'line', labels: pvL.labels, datasets: lineDatasets,
                        showLegend: true, yFmt: yFmtL, tooltip: ttL, showLabels: cfg.show_labels
                    }));
                } else {
                    /* Sum value_cols into one line per stack value. */
                    for (var sli2 = 0; sli2 < stackLimit; sli2++) {
                        var lname2 = pvL.stackNames[sli2];
                        var lcolData2 = pvL.datasets[lname2];
                        var lineData = pvL.labels.map(function(_, lxi2) {
                            var sum = 0;
                            for (var lci = 0; lci < cfg.value_cols.length; lci++) {
                                sum += (lcolData2[cfg.value_cols[lci]] || [])[lxi2] || 0;
                            }
                            return sum;
                        });
                        var lcolor2 = categoryColor(lname2);
                        lineDatasets.push({
                            label: lname2, data: lineData,
                            backgroundColor: lcolor2 + '22', borderColor: lcolor2,
                            borderWidth: 2.5, pointRadius: 2, tension: 0
                        });
                    }
                    var fmtMapL = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
                    var yFmtL2 = fmtMapL[cfg.y_format] || fmtCompact;
                    var ttL2 = { callbacks: { label: function(ctx) {
                        return ctx.dataset.label + ': ' + yFmtL2(ctx.parsed.y);
                    }}};
                    _createChart(id, canvas, _mkSpec({
                        type: 'line', labels: pvL.labels, datasets: lineDatasets,
                        showLegend: true, yFmt: yFmtL2, tooltip: ttL2, showLabels: cfg.show_labels
                    }));
                }

            } else if (activeStackBy) {
                /* Pivot-based stacked bar */
                var MAX_STACKS = (cfg.max_stacks != null) ? cfg.max_stacks : 20;
                var pv = window._fwAggregate.pivot(rows, cfg.x, activeStackBy, cfg.value_cols, cfg.stack_sort);
                var datasets = [], otherData = null;
                var limit = Math.min(pv.stackNames.length, MAX_STACKS);

                if (cfg.diverging && cfg.value_cols.length >= 2) {
                    /* Diverging: separate datasets per (stack, value_col).
                       Positive cols stack up, negative cols stack down.
                       Datasets sharing a label are legend-paired by fwLegend. */
                    for (var i = 0; i < pv.stackNames.length; i++) {
                        var colData = pv.datasets[pv.stackNames[i]];
                        if (i < limit) {
                            var c = categoryColor(pv.stackNames[i]);
                            for (var ci = 0; ci < cfg.value_cols.length; ci++) {
                                var vdata = [];
                                for (var xi = 0; xi < pv.labels.length; xi++)
                                    vdata.push(colData[cfg.value_cols[ci]][xi] || 0);
                                datasets.push({ label: pv.stackNames[i], data: vdata,
                                    backgroundColor: c + (ci === 0 ? 'cc' : '77'),
                                    borderColor: c, borderWidth: 1 });
                            }
                        } else {
                            if (!otherData) {
                                otherData = {};
                                for (var oi = 0; oi < cfg.value_cols.length; oi++)
                                    otherData[cfg.value_cols[oi]] = new Array(pv.labels.length).fill(0);
                            }
                            for (var oi2 = 0; oi2 < cfg.value_cols.length; oi2++)
                                for (var xi2 = 0; xi2 < pv.labels.length; xi2++)
                                    otherData[cfg.value_cols[oi2]][xi2] += (colData[cfg.value_cols[oi2]][xi2] || 0);
                        }
                    }
                    if (otherData) {
                        var otherLbl = 'Other (' + (pv.stackNames.length - limit) + ')';
                        for (var oi3 = 0; oi3 < cfg.value_cols.length; oi3++)
                            datasets.push({ label: otherLbl, data: otherData[cfg.value_cols[oi3]],
                                backgroundColor: '#888888' + (oi3 === 0 ? 'cc' : '77'),
                                borderColor: '#888888', borderWidth: 1 });
                    }
                } else {
                    /* Net: sum all value_cols per stack into single dataset */
                    for (var i = 0; i < pv.stackNames.length; i++) {
                        var netData = [], colData = pv.datasets[pv.stackNames[i]];
                        for (var xi = 0; xi < pv.labels.length; xi++) {
                            var net = 0;
                            for (var ci = 0; ci < cfg.value_cols.length; ci++) net += (colData[cfg.value_cols[ci]][xi] || 0);
                            netData.push(net);
                        }
                        if (i < limit) {
                            var c = categoryColor(pv.stackNames[i]);
                            datasets.push({ label: pv.stackNames[i], data: netData,
                                backgroundColor: c + 'cc', borderColor: c, borderWidth: 1 });
                        } else {
                            if (!otherData) otherData = new Array(pv.labels.length).fill(0);
                            for (var xi2 = 0; xi2 < pv.labels.length; xi2++) otherData[xi2] += netData[xi2];
                        }
                    }
                    if (otherData)
                        datasets.push({ label: 'Other (' + (pv.stackNames.length - limit) + ')',
                            data: otherData, backgroundColor: '#888888cc', borderColor: '#888888', borderWidth: 1 });
                }
                if (cfg.line_cols) {
                    var grLine = window._fwAggregate.groupBy(rows, cfg.x, cfg.line_cols);
                    var lineLabels = cfg.line_labels || cfg.line_cols;
                    for (var li = 0; li < cfg.line_cols.length; li++) {
                        var lcol = cfg.line_cols[li];
                        var lineData = pv.labels.map(function(l) { return grLine.map[l] ? grLine.map[l][lcol] || 0 : 0; });
                        var lc = colors[(limit + li) % colors.length];
                        datasets.push({ type: 'line', label: lineLabels[li], data: lineData, yAxisID: 'y1',
                            borderColor: lc, backgroundColor: 'transparent',
                            borderWidth: 2.5, pointRadius: 2, tension: 0, order: -1 });
                    }
                }
                if (normalized) {
                    var totals = pv.labels.map(function(_, xi) {
                        var t = 0;
                        for (var si = 0; si < datasets.length; si++) {
                            if (!datasets[si].yAxisID) t += (datasets[si].data[xi] || 0);
                        }
                        return t;
                    });
                    datasets.forEach(function(ds) {
                        if (!ds.yAxisID) {
                            ds._absData = ds.data.slice();
                            ds.data = ds.data.map(function(v, xi) {
                                return totals[xi] ? (v / totals[xi]) * 100 : 0;
                            });
                        }
                    });
                }
                if (cfg.line_cols || cfg.normalize_toggle) {
                    var vf = cfg.value_format || 'currency';
                    var fmtY = vf === 'currency' ? fmtCompact$ : fmtCompact;
                    var tt = { callbacks: {
                        label: function(ctx) {
                            if (ctx.dataset.yAxisID === 'y1') return ctx.dataset.label + ': ' + fmtCompact$(ctx.parsed.y);
                            var v = ctx.parsed.y, idx = ctx.dataIndex;
                            var absVal = ctx.dataset._absData ? ctx.dataset._absData[idx] : v;
                            if (normalized) {
                                return ctx.dataset.label + ': ' + v.toFixed(1) + '% (' + fmtY(absVal) + ')';
                            }
                            var ds = ctx.chart.data.datasets, total = 0;
                            for (var j = 0; j < ds.length; j++) { if (!ds[j].yAxisID) total += (ds[j].data[idx] || 0); }
                            var pct = total !== 0 ? (v / total * 100).toFixed(1) : '0.0';
                            return ctx.dataset.label + ': ' + fmtY(v) + ' (' + pct + '%)';
                        },
                        footer: function(items) {
                            if (!items.length) return '';
                            var idx = items[0].dataIndex, ds = items[0].chart.data.datasets, total = 0;
                            for (var j = 0; j < ds.length; j++) {
                                if (!ds[j].yAxisID) total += (ds[j]._absData ? ds[j]._absData[idx] : ds[j].data[idx]) || 0;
                            }
                            return 'Total: ' + fmtY(total);
                        }
                    }};
                    var yFmt = normalized ? function(v) { return v.toFixed(0) + '%'; }
                        : (vf === 'currency' ? function(v) { return fmtCompact$(v); } : undefined);
                    _createChart(id, canvas, _mkSpec({ labels: pv.labels, datasets: datasets, stacked: true, showLegend: cfg.show_legend !== false, tooltip: tt, yFmt: yFmt }));
                } else {
                    var vfSb = cfg.value_format || 'currency';
                    var fmtYSb = vfSb === 'currency' ? fmtCompact$ : fmtCompact;
                    var ttSb = { callbacks: {
                        label: function(ctx) {
                            if (ctx.dataset.yAxisID === 'y1') return ctx.dataset.label + ': ' + fmtCompact$(ctx.parsed.y);
                            var v = ctx.parsed.y, idx = ctx.dataIndex, ds = ctx.chart.data.datasets;
                            var totPos = 0, totNeg = 0;
                            for (var jsb = 0; jsb < ds.length; jsb++) {
                                if (ds[jsb].yAxisID === 'y1') continue;
                                var r = ds[jsb].data[idx]; if (r >= 0) totPos += r; else totNeg += r;
                            }
                            var tot = v >= 0 ? totPos : totNeg;
                            var pct = tot !== 0 ? (v / tot * 100).toFixed(1) : '0.0';
                            return ctx.dataset.label + ': ' + fmtYSb(v) + ' (' + pct + '%)';
                        },
                        footer: function(items) {
                            if (!items.length) return '';
                            var idx = items[0].dataIndex, ds = items[0].chart.data.datasets;
                            var totPos = 0, totNeg = 0;
                            for (var fsb = 0; fsb < ds.length; fsb++) {
                                if (ds[fsb].yAxisID === 'y1') continue;
                                var r = ds[fsb].data[idx]; if (r >= 0) totPos += r; else totNeg += r;
                            }
                            if (totNeg === 0) return 'Total: ' + fmtYSb(totPos);
                            return 'Positive: ' + fmtYSb(totPos) + '  Negative: ' + fmtYSb(totNeg);
                        }
                    }};
                    _createChart(id, canvas, _mkSpec({ labels: pv.labels, datasets: datasets, stacked: true, showLegend: cfg.show_legend !== false, tooltip: ttSb }));
                }

            } else if (cfg.net_color) {
                /* Net-color bar: sum columns → single green/red bar per label */
                var gr = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                var data = gr.labels.map(function(l) {
                    var s = 0; for (var j = 0; j < cfg.value_cols.length; j++) s += (gr.map[l][cfg.value_cols[j]] || 0); return s;
                });
                var sty = getComputedStyle(document.documentElement);
                var cG = sty.getPropertyValue('--accent-green').trim() || '#00b894';
                var cR = sty.getPropertyValue('--accent-red').trim() || '#d63031';
                _createChart(id, canvas, _mkSpec({ labels: gr.labels, datasets: [{ label: cfg.title, data: data,
                    backgroundColor: data.map(function(v) { return (v >= 0 ? cG : cR) + 'aa'; }),
                    borderColor: data.map(function(v) { return v >= 0 ? cG : cR; }), borderWidth: 1 }] }));

            } else if (cfg.ratios) {
                /* Ratio aggregation: sum numerator & denominator, then divide.
                   Works for both LineChart (chartType='line') and BarChart
                   (chartType='bar'); the only difference is dataset styling
                   and (for bar) optional sort + top_n. */
                var gr3 = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                var ratLabels = gr3.labels.slice();
                if (!isLine && (cfg.sort === 'desc' || cfg.sort === 'asc')) {
                    var sSpec = cfg.ratios[0];
                    var sDir = cfg.sort === 'asc' ? 1 : -1;
                    ratLabels.sort(function(a, b) {
                        var va = gr3.map[a] || {}, vb = gr3.map[b] || {};
                        var ra = (va[sSpec.denominator] || 0) > 0
                            ? (va[sSpec.numerator] || 0) / va[sSpec.denominator] : 0;
                        var rb = (vb[sSpec.denominator] || 0) > 0
                            ? (vb[sSpec.numerator] || 0) / vb[sSpec.denominator] : 0;
                        return (ra - rb) * sDir;
                    });
                }
                if (!isLine && cfg.top_n && ratLabels.length > cfg.top_n) {
                    /* Collapse the tail into a single "Other (N items)" bucket,
                       or drop it entirely if top_n_show_other === false. */
                    var _tailRat = ratLabels.slice(cfg.top_n);
                    ratLabels = ratLabels.slice(0, cfg.top_n);
                    if (cfg.top_n_show_other !== false) {
                        var _otherKey = 'Other (' + _tailRat.length + ' items)';
                        var _otherAgg = {};
                        for (var _ci = 0; _ci < cfg.value_cols.length; _ci++) _otherAgg[cfg.value_cols[_ci]] = 0;
                        for (var _ti = 0; _ti < _tailRat.length; _ti++) {
                            var _trow = gr3.map[_tailRat[_ti]] || {};
                            for (var _cj = 0; _cj < cfg.value_cols.length; _cj++) {
                                _otherAgg[cfg.value_cols[_cj]] += (_trow[cfg.value_cols[_cj]] || 0);
                            }
                        }
                        gr3.map[_otherKey] = _otherAgg;
                        ratLabels.push(_otherKey);
                    }
                }
                var datasets3 = [];
                for (var ri = 0; ri < cfg.ratios.length; ri++) {
                    var spec = cfg.ratios[ri];
                    var ratData = ratLabels.map(function(l) {
                        var num = gr3.map[l][spec.numerator] || 0;
                        var den = gr3.map[l][spec.denominator] || 0;
                        return den > 0 ? num / den : null;
                    });
                    var rc = colors[ri % colors.length];
                    if (isLine) {
                        datasets3.push({ label: spec.label, data: ratData,
                            backgroundColor: rc + '22', borderColor: rc,
                            borderWidth: 2.5, pointRadius: 2, tension: 0 });
                    } else {
                        datasets3.push({ label: spec.label, data: ratData,
                            backgroundColor: rc + 'cc', borderColor: rc,
                            borderWidth: 1 });
                    }
                }
                var ratFmt = {
                    currency: function(v) { return v == null ? '' : '$' + v.toFixed(2); },
                    percent: function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; },
                    minutes: function(v) { return v == null ? '' : v.toFixed(1) + 'm'; },
                    decimal1: function(v) { return v == null ? '' : v.toFixed(1); }
                };
                var yFmt3 = ratFmt[cfg.y_format] || (
                    cfg.y_format === 'number' ? function(v) { return v == null ? '' : window.fmtCompact(v); } : undefined
                );
                var tt3 = yFmt3 ? { callbacks: { label: function(ctx) {
                    var v = (!isLine && cfg.horizontal) ? ctx.parsed.x : ctx.parsed.y;
                    return ctx.dataset.label + ': ' + yFmt3(v);
                }}} : undefined;
                _createChart(id, canvas, _mkSpec({
                    type: cfg.chartType || 'line',
                    labels: ratLabels, datasets: datasets3,
                    showLegend: datasets3.length > 1, yFmt: yFmt3, tooltip: tt3,
                    horizontal: !isLine && cfg.horizontal,
                    showLabels: cfg.show_labels
                }));

            } else if (cfg.combo) {
                /* Combo chart: bars on y, lines on y1 */
                var allCols = cfg.value_cols.concat(cfg.line_cols);
                var gr4 = window._fwAggregate.groupBy(rows, cfg.x, allCols);
                var datasets4 = [], barLabels = cfg.y_labels || cfg.value_cols;
                var lineLabels4 = cfg.line_labels || cfg.line_cols;
                for (var bi = 0; bi < cfg.value_cols.length; bi++) {
                    var bc = cfg.value_cols[bi];
                    var bd = gr4.labels.map(function(l) { return gr4.map[l][bc] || 0; });
                    var bcolor = colors[bi % colors.length];
                    datasets4.push({ label: barLabels[bi] || bc, data: bd,
                        backgroundColor: bcolor + 'cc', borderColor: bcolor,
                        borderWidth: 1, yAxisID: 'y' });
                }
                for (var li = 0; li < cfg.line_cols.length; li++) {
                    var lc = cfg.line_cols[li];
                    var ld = gr4.labels.map(function(l) { return gr4.map[l][lc] || 0; });
                    var lcolor = (cfg.line_colors && cfg.line_colors[li]) 
                        ? cfg.line_colors[li] 
                        : colors[(cfg.value_cols.length + li) % colors.length];
                    datasets4.push({ type: 'line', label: lineLabels4[li] || lc, data: ld,
                        yAxisID: 'y1', borderColor: lcolor, backgroundColor: 'transparent',
                        borderWidth: 2.5, pointRadius: 2, tension: 0, order: -1 });
                }
                var fmtMap = { currency: fmtCompact$, number: fmtCompact, percent: fmtPercent };
                var yFmt4 = fmtMap[cfg.bar_format] || fmtCompact$;
                var y1Fmt4 = fmtMap[cfg.line_format] || fmtCompact;
                var tt4 = { callbacks: { label: function(ctx) {
                    var fmt = ctx.dataset.yAxisID === 'y1' ? y1Fmt4 : yFmt4;
                    return ctx.dataset.label + ': ' + fmt(ctx.parsed.y);
                }}};
                _createChart(id, canvas, _mkSpec({ labels: gr4.labels, datasets: datasets4,
                    comboAxes: cfg.combo_axes,
                    stacked: cfg.stacked, showLegend: true, tooltip: tt4,
                    showLabels: cfg.show_labels,
                    yFmt: function(v) { return yFmt4(v); },
                    y1Fmt: function(v) { return y1Fmt4(v); } }));

            } else {
                /* Standard groupBy aggregation for line / bar */
                var gr2 = window._fwAggregate.groupBy(rows, cfg.x, cfg.value_cols);
                if (cfg.sort === 'desc' || cfg.sort === 'asc') {
                    var _sortKey = cfg.value_cols[0];
                    var _dir = cfg.sort === 'asc' ? 1 : -1;
                    gr2.labels = gr2.labels.slice().sort(function(a, b) {
                        var va = (gr2.map[a] && gr2.map[a][_sortKey]) || 0;
                        var vb = (gr2.map[b] && gr2.map[b][_sortKey]) || 0;
                        return (va - vb) * _dir;
                    });
                }
                if (!isLine && cfg.top_n && gr2.labels.length > cfg.top_n) {
                    /* Collapse the tail into a single "Other (N items)" bucket,
                       or drop it entirely if top_n_show_other === false.
                       Top-N is applied AFTER sorting and AFTER filter-driven
                       aggregation, so the chart stays correct as filters change. */
                    var _tail = gr2.labels.slice(cfg.top_n);
                    gr2.labels = gr2.labels.slice(0, cfg.top_n);
                    if (cfg.top_n_show_other !== false) {
                        var _oKey = 'Other (' + _tail.length + ' items)';
                        var _oAgg = {};
                        for (var _vi = 0; _vi < cfg.value_cols.length; _vi++) _oAgg[cfg.value_cols[_vi]] = 0;
                        for (var _ti2 = 0; _ti2 < _tail.length; _ti2++) {
                            var _trow2 = gr2.map[_tail[_ti2]] || {};
                            for (var _vj = 0; _vj < cfg.value_cols.length; _vj++) {
                                _oAgg[cfg.value_cols[_vj]] += (_trow2[cfg.value_cols[_vj]] || 0);
                            }
                        }
                        gr2.map[_oKey] = _oAgg;
                        gr2.labels.push(_oKey);
                    }
                }
                var datasets2 = [], ylabels = cfg.y_labels || cfg.value_cols;
                var yFmt2 = cfg.y_format === 'currency' ? function(v) { return fmtCompact$(v); }
                    : cfg.y_format === 'percent' ? function(v) { return v == null ? '' : (v * 100).toFixed(2) + '%'; }
                    : undefined;
                for (var i2 = 0; i2 < cfg.value_cols.length; i2++) {
                    var col2 = cfg.value_cols[i2];
                    var d2 = gr2.labels.map(function(l) { return gr2.map[l][col2] || 0; });
                    var c2 = colors[i2 % colors.length];
                    var ds2 = { label: ylabels[i2] || col2, data: d2,
                        backgroundColor: isLine ? c2 + '22' : c2 + 'cc', borderColor: c2,
                        borderWidth: isLine ? 2.5 : 1, pointRadius: isLine ? 2 : undefined, tension: 0 };
                    if (cfg.area_fill) {
                        ds2.fill = cfg.stacked && i2 > 0 ? '-1' : 'origin';
                        ds2.backgroundColor = c2 + (cfg.stacked ? 'a6' : '1f');
                        ds2._fillOpacity = cfg.stacked ? 'a6' : '1f';
                        ds2._areaFill = true;
                        ds2.pointRadius = 0;
                        ds2.tension = 0;
                        ds2.borderWidth = cfg.stacked ? 1.5 : 2;
                    }
                    datasets2.push(ds2);
                }
                (cfg.forecast_series || []).forEach(function(index) {
                    if (datasets2[index]) datasets2[index]._fwForecast = true;
                });
                var tt2;
                if (yFmt2 || cfg.horizontal) {
                    var _ttFmt = yFmt2 || function(v) { return fmtCompact(v); };
                    tt2 = { callbacks: { label: function(ctx) {
                        var v = cfg.horizontal ? ctx.parsed.x : ctx.parsed.y;
                        return ctx.dataset.label + ': ' + _ttFmt(v);
                    }}};
                }
                _createChart(id, canvas, _mkSpec({ type: cfg.chartType, labels: gr2.labels, datasets: datasets2,
                    stacked: cfg.stacked, horizontal: cfg.horizontal, showLegend: datasets2.length > 1, yFmt: yFmt2, tooltip: tt2 }));
            }
        }
        if (taggedBars) canvas._fwBarLive = stackOwner ? {cfg: cfg, draw: _drawLive, scope: stackScope,
            source: stackSource, stackGroup: stackGroup, normalizeGroup: normalizeGroup} : {cfg: cfg, draw: _drawLive};
        if (scopedLive && !window._fwMetricsWorkspace) {
            // Opted-in charts own one binding across renderAll, changed configs
            // and canvas replacement. Untagged live charts retain their path.
            var bindings = window._fwCategoryChartBindings || (window._fwCategoryChartBindings = new Map());
            if (bindings.has(id)) bindings.get(id)();
            function stackRetired() {
                return stackOwner && (window._currentScope !== stackScope ||
                    (stackGroup && !stackGroup.isConnected) || (normalizeGroup && !normalizeGroup.isConnected));
            }
            function current() {
                if (document.getElementById(id) !== canvas || stackRetired()) { dispose(); return; }
                _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            }
            var unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, current);
            var lifeOff = stackOwner ? window._fwFilterEngine.subscribeLifecycle(function(event) {
                if (event.dataset_id !== cfg.dataset_id) return;
                if (event.type === 'init' || event.type === 'scope') {
                    unsubscribe();
                    unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, current);
                }
                current();
            }) : null;
            function onTheme() {
                if (document.getElementById(id) !== canvas || stackRetired()) {
                    bindings.get(id)(); bindings.delete(id); return;
                }
                _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
            }
            var observer = (stackOwner || cfg.line_style === 'adaptive' || canvas._fwAvailabilityOwned || cfg.bar_values || cfg.monthly_average) ? new MutationObserver(function() {
                if (document.getElementById(id) !== canvas || stackRetired()) dispose();
            }) : null;
            function dispose() {
                unsubscribe(); window.removeEventListener('fw-theme-change', onTheme);
                if (lifeOff) lifeOff();
                if (stackOwner) _fwClearStackSummary(canvas);
                if (barSignals) barSignals.abort();
                if (stackOwner && canvas._fwBarLive && canvas._fwBarLive.cfg === cfg) delete canvas._fwBarLive;
                if (observer) observer.disconnect();
                if (bindings.get(id) === dispose) bindings.delete(id);
                var chart = window._chartInstances[id];
                if (document.getElementById(id) !== canvas && chart && chart.canvas === canvas) {
                    chart.destroy(); delete window._chartInstances[id];
                }
            }
            bindings.set(id, dispose);
            if (observer) observer.observe(document.body, {childList: true, subtree: true});
            if (stackOwner && observer) {
                var stackPanel = canvas.closest('[data-toggle-target="__scope__"]');
                if (stackPanel) observer.observe(stackPanel, {attributes: true, attributeFilter: ['style']});
            }
            window.addEventListener('fw-theme-change', onTheme);
            _drawLive(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        } else window._fwLiveWrap(id, cfg, _drawLive);
        return;
    }

    /* ── Static mode ───────────────────────────────────────── */
    var tooltip;
    if (cfg.stacked && cfg.stackTooltipBreakdown) {
        var vf = cfg.valueFormat || 'currency';
        var fmtY = function(v) { return vf === 'currency' ? fmtCompact$(v) : fmtCompact(v); };
        tooltip = { callbacks: {
            label: function(ctx) {
                var val = ctx.parsed.y, idx = ctx.dataIndex, ds = ctx.chart.data.datasets, total = 0;
                for (var i = 0; i < ds.length; i++) { var raw = ds[i].data[idx]; total += (typeof raw === 'number' && !isNaN(raw) ? raw : 0); }
                return ctx.dataset.label + ': ' + fmtY(val) + ' (' + (total > 0 ? (val / total * 100).toFixed(1) : '0.0') + '%)';
            },
            footer: function(items) {
                if (!items.length) return '';
                var idx = items[0].dataIndex, ds = items[0].chart.data.datasets, total = 0;
                for (var i = 0; i < ds.length; i++) { var raw = ds[i].data[idx]; total += (typeof raw === 'number' && !isNaN(raw) ? raw : 0); }
                return 'Total: ' + fmtY(total);
            }
        }};
    }
    var yFmt = cfg.yTickFormat === 'currency' ? function(v) { return fmtCompact$(v); } : undefined;
    _createChart(id, canvas, _fwUpdateLineResult(canvas, cfg, window._fwMonthlyAverageSpec(cfg, null, _fwApplyLineAvailability(cfg, null, {
        type: cfg.chartType, labels: cfg.labels, datasets: cfg.datasets,
        stacked: cfg.stacked, horizontal: cfg.horizontal,
        showLegend: cfg.showLegend !== false, yFmt: yFmt, tooltip: tooltip,
        lineStyle: cfg.line_style, tension: cfg.tension,
        forecastSeries: cfg.forecast_series,
        monthContext: cfg.month_context === true,
        eventLabels: cfg.event_labels,
        headerSummary: cfg.header_summary,
        stackedSummary: cfg.stacked_summary,
        stackedSummaryInputReason: _fwStackSummaryInputReason(cfg, null),
        comboAxes: cfg.combo_axes,
        barValues: cfg.bar_values,
        stackLabels: cfg.stack_labels,
        stackNormalized: false,
        stackLabelReason: cfg.stack_labels && (cfg.stack_labels.totals || cfg.stack_labels.percentages)
            ? _fwStaticStackLabelEligibility(cfg) : ''
    })), null));
};

document.addEventListener('click', function(e) {
    var btn = e.target.closest('[data-csv-chart]');
    if (!btn) return;
    var chartId = btn.getAttribute('data-csv-chart');
    var chart = window._chartInstances[chartId];
    if (!chart) return;
    var slug = document.body.getAttribute('data-report-slug') || 'chart';
    var labels = chart.data.labels || [];
    var cols = ['label'];
    chart.data.datasets.forEach(function(ds) { cols.push(ds.label || 'value'); });
    var rows = [];
    for (var i = 0; i < labels.length; i++) {
        var row = [labels[i]];
        chart.data.datasets.forEach(function(ds) { row.push(ds.data[i]); });
        rows.push(row);
    }
    window._fwDownloadCsv(cols, rows, slug + '_chart.csv');
});
