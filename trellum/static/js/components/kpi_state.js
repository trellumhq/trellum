/* Opt-in KPI declarations operate only on the supplied, currently filtered rows. */
window._fwKpiState = (function() {
    function finite(v) { return typeof v === 'number' && Number.isFinite(v); }
    function resolve(spec, rows) {
        if (Object.prototype.hasOwnProperty.call(spec, 'value')) return spec.value;
        if (!rows || !rows.length) return spec.agg === 'count' ? 0 : null;
        var cols = spec.agg === 'ratio' ? [spec.numerator, spec.denominator]
            : spec.agg === 'count' ? [] : [spec.column];
        if (rows.some(function(r) { return cols.some(function(c) { return !finite(r[c]); }); })) return null;
        var A = window._fwAggregate, v;
        if (spec.agg === 'count') v = rows.length;
        else if (spec.agg === 'ratio') {
            var den = A.sum(rows, spec.denominator);
            // New ratios support nonnegative denominators; legacy math is untouched.
            if (rows.some(function(r) { return r[spec.denominator] < 0; }) || den <= 0) return null;
            v = A.sum(rows, spec.numerator) / den;
        } else v = spec.agg === 'abssum' ? A.absSum(rows, spec.column) : A.sum(rows, spec.column);
        return finite(v) ? v : null;
    }
    function normalized(spec, rows) {
        var v = resolve(spec, rows);
        return finite(v) ? (spec.unit === 'percent' ? v / 100 : v) : null;
    }
    function comparison(spec, rows) {
        var delta;
        if ('value' in spec) delta = spec.value;
        else {
            var current = normalized(spec.current, rows), baseline = normalized(spec.baseline, rows);
            if (current === null) return {text: '— · ' + (spec.reason || 'current unavailable')};
            if (baseline === null) return {text: '— · ' + (spec.reason || 'baseline unavailable')};
            if (spec.measure === 'relative_percent' && baseline <= 0) {
                return {text: '— · ' + (spec.reason || (baseline === 0 ? 'zero baseline' : 'signed baseline'))};
            }
            delta = spec.measure === 'percentage_points' ? (current - baseline) * 100
                : (current - baseline) / baseline * 100;
        }
        if (!finite(delta)) return {text: '— · ' + (spec.reason || 'comparison unavailable')};
        var precision = spec.precision == null ? 1 : spec.precision;
        // A rounded zero is visually neutral too; calculation above uses unrounded inputs.
        var rounded = Number(delta.toFixed(precision));
        return {text: (rounded > 0 ? '+' : '') + rounded.toFixed(precision)
            + (spec.measure === 'percentage_points' ? ' pp' : '%'),
            direction: rounded === 0 ? '' : delta > 0 ? 'up' : 'down'};
    }
    function note(el, label, reason) {
        var nid = el.id + '_state_note', existing = document.getElementById(nid);
        if (!reason) {
            if (existing) existing.remove();
            el.removeAttribute('aria-describedby');
            return;
        }
        var grid = el.closest('.fw-kpi-grid'), parent;
        if (grid) {
            var notesId = grid.id + '_state_notes';
            parent = document.getElementById(notesId);
            if (!parent) {
                parent = document.createElement('div'); parent.id = notesId;
                parent.className = 'fw-kpi-notes';
                // Native expansion locates the grid immediately before its button.
                var anchor = grid.nextElementSibling;
                if (!anchor || !anchor.classList.contains('fw-kpi-more-btn')) anchor = grid;
                anchor.insertAdjacentElement('afterend', parent);
            }
        }
        if (!existing) {
            existing = document.createElement('div'); existing.id = nid;
            if (parent) parent.appendChild(existing);
            else el.insertAdjacentElement('afterend', existing);
        }
        existing.className = 'fw-kpi-state-note' + (el.classList.contains('fw-kpi-extra') ? ' fw-kpi-note-extra' : '');
        existing.textContent = (label || 'Metric') + ': ' + reason;
        if (parent) parent.appendChild(existing); // Finish each row draw in metric reading order.
        el.setAttribute('aria-describedby', nid);
    }
    function line(el, cls, text) {
        var node = document.createElement('div'); node.className = cls;
        node.textContent = text; node.title = text; el.appendChild(node); return node;
    }
    function render(el, kpi, rows, bound) {
        // Keep the ordinary renderer independent of richer DOM methods used by declarations.
        if (!kpi.availability && !kpi.maturity && !kpi.comparison && !kpi.delta && !el._fwKpiHadState) return;
        var reason = '', a = kpi.availability, m = kpi.maturity, maturity;
        el.classList.toggle('fw-kpi-has-maturity', !!m);
        if (a) {
            if (a.state === 'unavailable') reason = a.reason || 'value unavailable';
            else if (a.required_columns) {
                if (!rows.length && a.empty === 'unavailable') reason = a.reason || 'no matching observations';
                else if (rows.some(function(r) { return a.required_columns.some(function(c) { return !finite(r[c]); }); })) {
                    reason = a.reason || 'incomplete inputs';
                } else if (a.ratio_denominator && (!rows.length || window._fwAggregate.sum(rows, kpi.denominator) === 0)) {
                    reason = a.reason || 'zero denominator';
                }
            }
            if (!reason && Object.prototype.hasOwnProperty.call(kpi, 'value') && kpi.value === null) {
                reason = a.reason || 'value unavailable';
            }
        }
        if (m) {
            var age = m.age, state = m.state, maturityReason = '';
            if (bound) {
                var cohorts = new Set(rows.map(function(r) { return r[m.cohort_column]; }));
                if (!rows.length || cohorts.size !== 1 || rows.some(function(r) { return r[m.cohort_column] == null; })) {
                    maturityReason = m.reason || 'select one cohort';
                }
                if (m.age_column) {
                    var ages = new Set(rows.map(function(r) { return r[m.age_column]; }));
                    age = ages.size === 1 ? rows[0][m.age_column] : null;
                }
                if (m.state_column) {
                    var states = new Set(rows.map(function(r) { return r[m.state_column]; }));
                    state = states.size === 1 ? rows[0][m.state_column] : null;
                }
            }
            if (!finite(age) || age < 0 || ['mature', 'immature'].indexOf(state) < 0
                || (state === 'mature') !== (age >= m.horizon)) {
                maturityReason = maturityReason || m.reason || 'cohort state unavailable';
            } else if (!maturityReason) maturity = {age: age, state: state};
            reason = reason || maturityReason;
        }
        var value = el.querySelector('.fw-kpi-value');
        if (reason || (maturity && maturity.state === 'immature')) {
            value.textContent = '—'; value.title = '—';
        }
        if (maturity && maturity.state === 'immature') {
            var badge = document.createElement('span'); badge.className = 'fw-kpi-age';
            badge.textContent = 'Age ' + maturity.age + 'd · <' + m.horizon + 'd';
            value.appendChild(badge);
            value.title = '— · ' + badge.textContent;
        }
        if (maturity) {
            // Replace the existing two detail slots; do not reserve an absent forecast.
            el.querySelectorAll('.fw-kpi-forecast, .fw-kpi-sub').forEach(function(n) { n.remove(); });
            var observation = normalized(m.observed, rows);
            var fmt = m.observed.unit === 'number' ? (kpi.format === 'percent' ? 'number' : kpi.format || 'number') : 'percent';
            line(el, 'fw-kpi-forecast fw-kpi-observed', 'Observed D' + maturity.age + ' · '
                + (observation === null ? '—' : window._fwKpiFormat(observation, fmt, kpi.full_number, kpi.precision)));
            if (m.forecast) {
                var forecast = normalized(m.forecast, rows);
                var ffmt = m.forecast.unit === 'number' ? (kpi.format === 'percent' ? 'number' : kpi.format || 'number') : 'percent';
                line(el, 'fw-kpi-sub', 'Forecast D' + m.horizon + ' · '
                    + (forecast === null ? '—' : window._fwKpiFormat(forecast, ffmt, kpi.full_number, kpi.precision)));
            }
        }
        if (kpi.delta || kpi.comparison) {
            el.querySelectorAll('.fw-kpi-delta').forEach(function(n) { n.remove(); });
            var result = kpi.delta ? {text: kpi.delta, direction: kpi.delta_direction || kpi.deltaDirection || ''}
                : comparison(kpi.comparison, rows);
            var direction = result.direction;
            if (direction !== undefined && kpi.delta_direction && kpi.delta_direction !== 'auto') direction = kpi.delta_direction;
            var delta = line(el, 'fw-kpi-delta' + (direction ? ' ' + direction : ''), '');
            var detail = el.querySelector('.fw-kpi-forecast, .fw-kpi-sub');
            if (detail) el.insertBefore(delta, detail);
            var change = document.createElement('span'); change.className = 'fw-kpi-change';
            change.textContent = result.text; delta.appendChild(change); delta.title = result.text;
            if (kpi.comparison) delta.classList.add('fw-kpi-declared-comparison');
        }
        note(el, kpi.label, reason);
        el._fwKpiHadState = true;
    }
    return {render: render, resolve: resolve, comparison: comparison};
})();
