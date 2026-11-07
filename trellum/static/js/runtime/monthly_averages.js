    // Explicit observed-day averages. This module never mutates plotted values.
    window._fwMonthlyAverageSpec = function(cfg, rows, spec) {
        var declaration = cfg.monthly_average;
        if (!declaration) return spec;
        var state = {status: 'unavailable', reason: '', rows: [], groups: []};
        spec.monthlyAverage = state;
        function unavailable(reason) { state.reason = reason; return spec; }
        var start = declaration.start, end = declaration.end;
        if (!window._fwDailyDate(start) || !window._fwDailyDate(end) || start > end)
            return unavailable('The declared reporting interval is invalid.');
        if (spec.datasets.length !== 1 || cfg.ratios || cfg.forecast_series && cfg.forecast_series.length ||
                cfg.stack_by || cfg.stack_by_options || cfg.horizontal || cfg.stacked || cfg.net_color ||
                cfg.sort != null || cfg.top_n != null || cfg.y_format === 'percent' || cfg.y_format === 'ratio')
            return unavailable('This requires one ordinary additive daily series.');
        if (cfg.dataset_id) {
            var engine = window._fwFilterEngine, source = cfg.dataset_id, visited = new Set();
            while (source) {
                if (visited.has(source)) return unavailable('The selected data scope is unsupported.');
                visited.add(source);
                if (engine.isLiveDataset(source)) return unavailable('Live-query reporting scope is not supported.');
                var filters = engine.getFilterState(source);
                for (var id in filters) {
                    var filter = filters[id], value = filter.value;
                    if (filter.column !== cfg.x) continue;
                    if (filter.mode !== 'range') {
                        var noop = filter.mode === 'equals' && value === '__all__' ||
                            filter.mode === 'in' && Array.isArray(value) && !value.length ||
                            filter.mode === 'flag' && ['all', '__all__', '', null].indexOf(value) !== -1;
                        if (noop) continue;
                        return unavailable('Date selection requires daily date-range filters.');
                    }
                    if (value == null) value = {};
                    if (typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(function(k) { return k !== 'min' && k !== 'max'; }))
                        return unavailable('Date selection requires canonical daily bounds.');
                    for (var bound of ['min', 'max']) {
                        var date = value[bound];
                        if (date == null || date === '') continue;
                        if (!window._fwDailyDate(date)) return unavailable('Date selection requires canonical daily bounds.');
                        if (bound === 'min' && date > start) start = date;
                        if (bound === 'max' && date < end) end = date;
                    }
                }
                source = engine._scopes[source] ? engine._scopes[source].parent : null;
            }
            if (!engine.isReady(cfg.dataset_id)) return unavailable('Selected data is not available yet.');
        }
        if (start > end) return unavailable('The selected date ranges have an empty intersection.');
        var parsedStart = window._fwDailyDate(start), parsedEnd = window._fwDailyDate(end);
        if ((parsedEnd.year - parsedStart.year) * 12 + parsedEnd.month - parsedStart.month + 1 > 1200)
            return unavailable('The reporting interval exceeds 1200 calendar months.');
        var labels = spec.labels, calendar = window._fwDailyCalendar(labels);
        if (calendar.reason && calendar.reason !== 'empty') return unavailable('Supplied dates must be valid, unique daily dates in ascending order.');
        if (!Array.isArray(labels)) return unavailable('Supplied dates must be daily reporting dates.');
        if (labels.some(function(day) { return day < start || day > end; }))
            return unavailable('Supplied dates fall outside the selected reporting interval.');
        var buckets = new Map(), metric = cfg.value_cols && cfg.value_cols[0];
        if (cfg.dataset_id) {
            for (var row of rows || []) {
                var day = row[cfg.x], raw = row[metric];
                if (!window._fwDailyDate(day)) return unavailable('Raw observations contain invalid daily reporting dates.');
                if (day < start || day > end) return unavailable('Supplied dates fall outside the selected reporting interval.');
                var bucket = buckets.get(day) || {count: 0, complete: true, sum: 0};
                bucket.count++;
                if (typeof raw !== 'number' || !Number.isFinite(raw)) bucket.complete = false;
                else {
                    bucket.sum += raw;
                    if (!Number.isFinite(bucket.sum)) bucket.complete = false;
                }
                buckets.set(day, bucket);
                if (declaration.aggregation === 'unique' && bucket.count > 1)
                    return unavailable('Unique daily observations contain duplicate contributors.');
            }
        }
        var observed = new Map(), values = spec.datasets[0].data;
        for (var i = 0; i < labels.length; i++) {
            var finalValue = values[i];
            var input = cfg.dataset_id ? buckets.get(labels[i]) : (declaration.inputs || [])[i];
            if (!input) return unavailable('Daily values cannot be verified against supplied inputs.');
            if (!cfg.dataset_id && input.kind === 'invalid') return unavailable('Static daily observations require finite numbers or nulls.');
            var complete = cfg.dataset_id ? input.complete : input.kind === 'finite';
            var expected = cfg.dataset_id ? input.sum : input.value;
            // Declared gaps (including state/extra-input masks) stay unknown.
            if (finalValue === null) continue;
            if (typeof finalValue !== 'number' || !Number.isFinite(finalValue))
                return unavailable('Daily observations require finite numbers or nulls.');
            if (!complete) return unavailable('Daily totals include incomplete observations.');
            if (Math.abs(finalValue - expected) > Math.max(1, Math.abs(finalValue), Math.abs(expected)) * 1e-12)
                return unavailable('Plotted daily values disagree with the declared aggregation.');
            observed.set(labels[i], finalValue);
        }
        var months = [], year = parsedStart.year, month = parsedStart.month;
        while (year < parsedEnd.year || year === parsedEnd.year && month <= parsedEnd.month) {
            var key = String(year).padStart(4, '0') + '-' + String(month).padStart(2, '0');
            var first = key + '-01', days = window._fwDailyDate(first).days;
            var last = key + '-' + String(days).padStart(2, '0');
            var lo = start > first ? start : first, hi = end < last ? end : last;
            months.push({key: key, start: lo, end: hi, selected: +hi.slice(8) - +lo.slice(8) + 1,
                full: lo === first && hi === last, values: [], observed: 0, mean: null});
            if (++month === 13) { year++; month = 1; }
        }
        var byMonth = new Map(months.map(function(row) { return [row.key, row]; }));
        observed.forEach(function(value, day) { byMonth.get(day.slice(0, 7)).values.push(value); });
        for (var summary of months) {
            var numbers = summary.values;
            summary.observed = numbers.length;
            if (numbers.length) {
                // Scaling and compensated summation avoid overflowing a finite mean.
                var scale = numbers.reduce(function(max, v) { return Math.max(max, Math.abs(v)); }, 0);
                var sum = 0, correction = 0;
                numbers.forEach(function(v) {
                    var normalized = scale ? v / scale : 0, next = sum + normalized;
                    correction += Math.abs(sum) >= Math.abs(normalized)
                        ? (sum - next) + normalized : (normalized - next) + sum;
                    sum = next;
                });
                summary.mean = Math.max(-1, Math.min(1, (sum + correction) / numbers.length)) * scale;
                if (!Number.isFinite(summary.mean)) return unavailable('The monthly average is not finite.');
            }
            delete summary.values;
        }
        state.status = 'eligible'; state.start = start; state.end = end;
        state.unit = declaration.unit; state.metric = spec.datasets[0].label || metric || 'Series 1';
        state.rows = months; state.groups = calendar.groups || [];
        return spec;
    };

    window._fwClearMonthlyAverage = function(canvas) {
        var id = canvas.id + '-monthly-average';
        var tokens = (canvas.getAttribute('aria-describedby') || '').split(/\s+/).filter(function(t) { return t && t !== id; });
        if (tokens.length) canvas.setAttribute('aria-describedby', tokens.join(' '));
        else canvas.removeAttribute('aria-describedby');
        var block = document.getElementById(id);
        if (block && block.classList.contains('fw-monthly-average')) block.remove();
    };
    window._fwMonthlyAveragePlugin = function(state) {
        var owner, block, observer;
        function clear() {
            if (block) block.remove();
            block = null;
            if (owner) window._fwClearMonthlyAverage(owner);
        }
        function node(tag, text) { var el = document.createElement(tag); el.textContent = text; return el; }
        function monthName(key) {
            var names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
            return names[+key.slice(5) - 1] + ' ' + key.slice(0, 4);
        }
        return {
            id: 'fwMonthlyAverage',
            afterInit: function(chart) {
                owner = chart.canvas;
                observer = new MutationObserver(function() {
                    if (document.getElementById(owner.id) !== owner) {
                        chart.destroy();
                        if (window._chartInstances[owner.id] === chart) delete window._chartInstances[owner.id];
                    }
                });
                observer.observe(document.body, {childList: true, subtree: true});
            },
            afterUpdate: function(chart) {
                chart._fwMonthlyAverage = state;
                if (!chart.isDatasetVisible(0)) { clear(); return; }
                if (!block) {
                    block = document.createElement('div'); block.className = 'fw-monthly-average';
                    block.id = owner.id + '-monthly-average';
                    if (state.status !== 'eligible') block.append(node('p', 'Monthly average unavailable: ' + state.reason));
                    else {
                        var table = document.createElement('table');
                        table.append(node('caption', state.metric + ' (' + state.unit + '), ' + state.start + ' to ' + state.end +
                            '. Observed-day averaging: zeros included; unknown days excluded. Zoom changes view only.'));
                        var head = document.createElement('thead'), headings = document.createElement('tr');
                        ['Calendar month/year', 'Selected dates', 'Observed / selected days', 'Average per observed day (' + state.unit + ')'].forEach(function(text) {
                            var th = node('th', text); th.scope = 'col'; headings.append(th);
                        });
                        head.append(headings); table.append(head);
                        var body = document.createElement('tbody');
                        state.rows.forEach(function(row) {
                            var tr = document.createElement('tr'), th = node('th', monthName(row.key)); th.scope = 'row'; tr.append(th);
                            tr.append(node('td', row.start + ' to ' + row.end + ' · ' + (row.full ? 'full month' : 'partial month')),
                                node('td', row.observed + ' / ' + row.selected + ' days; ' + (row.selected - row.observed) + ' missing'),
                                node('td', row.mean === null ? '—' : row.mean.toFixed(1)));
                            body.append(tr);
                        });
                        table.append(body); block.append(table);
                    }
                }
                var anchor = document.getElementById(owner.id + '-availability') || document.getElementById(owner.id + '-event-details') ||
                    document.getElementById(owner.id + '-month-context') || owner.parentElement;
                if (anchor.nextElementSibling !== block) anchor.after(block);
                var tokens = (owner.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
                if (tokens.indexOf(block.id) === -1) tokens.push(block.id);
                owner.setAttribute('aria-describedby', tokens.join(' '));
            },
            afterDatasetsDraw: function(chart) {
                state.guides = [];
                if (state.status !== 'eligible' || !chart.isDatasetVisible(0)) return;
                var x = chart.scales.x, y = chart.scales.y, area = chart.chartArea;
                if (!x || x.type !== 'category' || !y || !area) return;
                var ctx = chart.ctx, previousLabel = null;
                ctx.save(); ctx.beginPath(); ctx.rect(area.left, area.top, area.width, area.height); ctx.clip();
                var color = getComputedStyle(owner).getPropertyValue('--text-main').trim() || getComputedStyle(owner).color;
                ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1; ctx.setLineDash([5, 4]);
                ctx.font = '12px ' + getComputedStyle(owner).fontFamily;
                state.groups.forEach(function(group) {
                    var row = state.rows.find(function(r) { return r.key === group.key; });
                    if (!row || row.mean === null) return;
                    var left = Math.max(area.left, window._fwDailyCategoryEdge(chart, group.start));
                    var right = Math.min(area.right, window._fwDailyCategoryEdge(chart, group.end + 1));
                    var pixel = y.getPixelForValue(row.mean);
                    if (right <= left || pixel < area.top || pixel > area.bottom) return;
                    ctx.beginPath(); ctx.moveTo(left, pixel); ctx.lineTo(right, pixel); ctx.stroke();
                    var text = typeof window.fmtCompact === 'function' ? window.fmtCompact(row.mean) : String(Math.round(row.mean * 10) / 10);
                    var width = ctx.measureText(text).width, label = '';
                    var box = {left: left + 4, right: left + 4 + width, top: pixel - 16, bottom: pixel - 3};
                    if (width + 8 <= right - left && box.top >= area.top && (!previousLabel || box.left > previousLabel.right + 6 || box.top > previousLabel.bottom || box.bottom < previousLabel.top)) {
                        ctx.fillText(text, left + 4, pixel - 4); label = text; previousLabel = box;
                    }
                    state.guides.push({key: row.key, left: left, right: right, y: pixel, mean: row.mean, label: label});
                });
                ctx.restore();
            },
            afterDestroy: function(chart) { if (observer) observer.disconnect(); clear(); delete chart._fwMonthlyAverage; }
        };
    };
