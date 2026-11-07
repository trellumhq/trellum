window._fwRenderers['cohort_table'] = function(id, cfg) {
    'use strict';
    var root = document.getElementById(id);
    if (!root) return;
    if (root._fwCohortDispose) root._fwCohortDispose();
    var content = root.querySelector('.fw-cohort-content'), button = root.querySelector('[data-csv-target]');
    var engine = window._fwFilterEngine, active = true, unsubscribe = null, selected = [];
    var source = [], groups = new Map(), cells = new Map(), safe = new Set(), error = '';
    var format = getFormatter(cfg.value_format);
    function escape(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c];
    }); }
    function key(row) { return JSON.stringify([row[cfg.cohort], row[cfg.horizon]]); }
    function finite(v) { return typeof v === 'number' && Number.isFinite(v); }
    function state(row) { return row[cfg.observed_state] === 'observed' ? 'observed' :
        cfg.forecast && row[cfg.forecast] != null ? 'forecast' : row[cfg.observed_state]; }
    function reason(row) { var supplied = cfg.reason && row[cfg.reason];
        return supplied && supplied.trim() ? supplied : row[cfg.observed_state] === 'not_mature' ?
            'Not mature as of the declared snapshot' : row[cfg.observed_state] === 'unavailable' ?
            'Observation unavailable as of the declared snapshot' : 'Author-declared observation'; }
    function context() {
        var p = cfg.population;
        return '<p class="fw-cohort-context">Population: ' + escape(p.label) + ' · Snapshot: ' +
            escape(p.as_of) + ' · Timezone: ' + escape(p.timezone) +
            (p.source_time ? ' · Source time: ' + escape(p.source_time) : '') + '</p>';
    }
    function fail(message) {
        var focused = content.contains(document.activeElement);
        selected = []; button.disabled = true;
        content.innerHTML = context() + '<p class="fw-cohort-error" role="alert" tabindex="-1">Cohort scope invalid: ' + escape(message) + '</p>';
        if (focused) content.querySelector('.fw-cohort-error').focus({preventScroll:true});
    }
    try {
        if (!Array.isArray(cfg.data) || cfg.data.length > 7680 ||
            new TextEncoder().encode(JSON.stringify(cfg)).length > 4 * 1024 * 1024)
            throw new Error('The snapshot exceeds the supported record or byte bound.');
        source = cfg.data.map(function(row) { return Object.assign({}, row); });
        var horizons = new Set();
        source.forEach(function(row) {
            var identity = row[cfg.cohort], month = row[cfg.cohort_month], day = row[cfg.horizon];
            if (typeof identity !== 'string' || !identity.trim() || identity.length > 2048 ||
                typeof month !== 'string' || !/^(?!0000)[0-9]{4}-(0[1-9]|1[0-2])$/.test(month) ||
                !Number.isSafeInteger(day) || day < 0 || cells.has(key(row)))
                throw new Error('The snapshot contains an invalid or duplicate cohort/horizon identity.');
            var observedState = row[cfg.observed_state], observed = row[cfg.observed];
            if (!['observed', 'unavailable', 'not_mature'].includes(observedState) ||
                (observedState === 'observed' ? !finite(observed) : observed !== null) ||
                (cfg.forecast && row[cfg.forecast] !== null && !finite(row[cfg.forecast])))
                throw new Error('The snapshot contains an invalid observation or forecast state/value.');
            var group = groups.get(identity);
            if (group && group.month !== month) throw new Error('A cohort has inconsistent months.');
            if (!group) { group = {month:month, rows:[]}; groups.set(identity, group); }
            group.rows.push(row); cells.set(key(row), row); horizons.add(day);
        });
        if (groups.size > 120 || horizons.size > 64) throw new Error('The snapshot exceeds 120 cohorts or 64 horizons.');
        var forbidden = [cfg.horizon, cfg.observed, cfg.observed_state, cfg.forecast, cfg.reason];
        cfg.source_columns.forEach(function(col) {
            if (forbidden.includes(col)) return;
            var constant = true;
            groups.forEach(function(g) {
                if (g.rows.some(function(row) { return JSON.stringify(row[col]) !== JSON.stringify(g.rows[0][col]); })) constant = false;
            });
            if (constant) safe.add(col);
        });
    } catch (e) { error = e.message; }

    function scopeError(rows) {
        if (error) return error;
        if (!Array.isArray(rows) || rows.length > 7680) return 'Selected source records exceed the supported bound.';
        if (cfg.dataset_id) {
            var ds = cfg.dataset_id, seen = new Set();
            while (ds) {
                if (seen.has(ds)) return 'Cyclic source ancestry is unsupported.';
                seen.add(ds);
                if (engine.isLiveDataset(ds)) return 'LiveDataSource ancestry is unsupported.';
                var filters = engine.getFilterState(ds);
                for (var filterId in filters) {
                    if (!safe.has(filters[filterId].column)) return 'Filter on ' + filters[filterId].column + ' can slice precomputed cohort cells.';
                }
                ds = engine._scopes[ds] ? engine._scopes[ds].parent : null;
            }
            // Snapshot validity requires every propagation-connected endpoint
            // to remain a snapshot, in either direction of the mapping.
            var configs = (window._reportData || {}).components || {}, graph = new Map();
            function connect(a,b) {
                if (!graph.has(a)) graph.set(a,new Set());
                if (!graph.has(b)) graph.set(b,new Set());
                graph.get(a).add(b); graph.get(b).add(a);
            }
            for (var cid in configs) {
                var c = configs[cid];
                if (c.type === 'scoped_data_source') connect(c.dataset_id,c.parent_id);
                if (c.type === 'filter_bar' && c.propagate_to) {
                    for (var target of Object.keys(c.propagate_to)) connect(c.dataset_id,target);
                }
            }
            var pending = Array.from(seen), connected = new Set();
            while (pending.length) {
                var node = pending.pop();
                if (connected.has(node)) continue;
                connected.add(node);
                if (engine.isLiveDataset(node)) return 'Live-connected filter propagation is unsupported.';
                if (graph.has(node)) graph.get(node).forEach(function(next) { pending.push(next); });
            }
        }
        var counts = new Map(), selectedKeys = new Set();
        for (var row of rows) {
            if (!row || typeof row !== 'object' || Array.isArray(row)) return 'Selected source fields changed or contain invalid records.';
            var k = key(row), expected = cells.get(k);
            if (!expected || selectedKeys.has(k) || cfg.source_columns.some(function(col) {
                return !Object.prototype.hasOwnProperty.call(row,col) || !Object.is(row[col],expected[col]);
            }))
                return 'Selected source fields changed or contain unknown/duplicate cells.';
            selectedKeys.add(k);
            counts.set(row[cfg.cohort], (counts.get(row[cfg.cohort]) || 0) + 1);
        }
        for (var entry of counts) {
            if (entry[1] !== groups.get(entry[0]).rows.length)
                return 'Partial cohort selection: ' + entry[0] + ' must retain every declared cell.';
        }
        return '';
    }
    function draw(rows) {
        if (!active) return;
        if (!root.isConnected) { dispose(); return; }
        var invalid = scopeError(rows);
        if (invalid) { fail(invalid); return; }
        selected = rows.slice(); button.disabled = selected.length === 0;
        var oldDetails = content.querySelector('details'), wasOpen = oldDetails && oldDetails.open;
        var focused = oldDetails && oldDetails.contains(document.activeElement);
        var scroll = content.querySelector('.fw-cohort-scroll'), left = scroll ? scroll.scrollLeft : 0;
        var scrollFocused = scroll && scroll.contains(document.activeElement);
        if (!rows.length) {
            var contentFocused = content.contains(document.activeElement);
            content.innerHTML = context() + '<p class="fw-cohort-empty" role="status" tabindex="-1">No cohorts match the current selection.</p>';
            if (contentFocused) content.querySelector('.fw-cohort-empty').focus({preventScroll:true});
            return;
        }
        var identities = Array.from(new Set(rows.map(function(r) { return r[cfg.cohort]; })));
        // Stable sort retains first-seen identities within the same month.
        identities = Array.from(groups.keys()).filter(function(c) { return identities.includes(c); })
            .sort(function(a,b) { return groups.get(a).month.localeCompare(groups.get(b).month); });
        var days = Array.from(new Set(rows.map(function(r) { return r[cfg.horizon]; }))).sort(function(a,b) { return a-b; });
        var lookup = new Map(rows.map(function(r) { return [key(r), r]; }));
        var years = [], months = new Map();
        identities.forEach(function(c) {
            var month = groups.get(c).month, year = month.slice(0,4), last = years[years.length-1];
            if (last && last.year === year) last.count++; else years.push({year:year,count:1});
            months.set(month, (months.get(month) || 0) + 1);
        });
        var header = '<tr><th rowspan="2" scope="col">Horizon</th>' + years.map(function(y) {
            return '<th scope="colgroup" colspan="' + y.count + '">' + y.year + '</th>';
        }).join('') + '</tr><tr>' + identities.map(function(c) {
            var month = groups.get(c).month;
            return '<th scope="col" title="' + escape(c) + '">' + escape(month.slice(5)) +
                (months.get(month) > 1 ? '<span class="fw-cohort-identity">' + escape(c) + '</span>' : '') + '</th>';
        }).join('') + '</tr>';
        function cell(c, day) {
            var row = lookup.get(JSON.stringify([c, day]));
            if (!row) return '<td data-state="no_record"><span aria-label="No record">—</span><span class="fw-cohort-cell-reason">No record</span></td>';
            var s = state(row), label = s === 'not_mature' ? 'Not mature' : s === 'unavailable' ? 'Unavailable' : s;
            var value = s === 'observed' ? row[cfg.observed] : s === 'forecast' ? row[cfg.forecast] : null;
            return '<td data-state="' + s + '"><span class="fw-cohort-value" title="' + escape(label + ': ' + reason(row)) + '">' +
                (value === null ? '—' : escape(format(value))) + (s === 'forecast' ? ' <span aria-label="Forecast">F</span>' : '') +
                '</span>' + (value === null ? '<span class="fw-cohort-cell-reason">' + escape(label) + '</span>' : '') + '</td>';
        }
        var body = days.map(function(d) { return '<tr><th scope="row">D' + d + '</th>' +
            identities.map(function(c) { return cell(c,d); }).join('') + '</tr>'; }).join('');
        var foot = '';
        if (cfg.milestone) {
            foot = '<tfoot><tr><th scope="row">' + escape(cfg.milestone.label) + '</th>' + identities.map(function(c) {
                var records = rows.filter(function(r) { return r[cfg.cohort] === c; }).sort(function(a,b) { return a[cfg.horizon]-b[cfg.horizon]; });
                var observed = records.find(function(r) { return r[cfg.observed_state] === 'observed' && r[cfg.observed] >= cfg.milestone.threshold; });
                // Only forecasts actually displayed qualify for a displayed milestone.
                var forecast = records.find(function(r) { return state(r) === 'forecast' && r[cfg.forecast] >= cfg.milestone.threshold; });
                var found = observed || forecast;
                return '<td>' + (found ? 'D' + found[cfg.horizon] + ' · ' + (observed ? 'Observed' : 'Forecast F') : 'No displayed crossing') + '</td>';
            }).join('') + '</tr></tfoot>';
        }
        var details = rows.map(function(row) {
            return '<li>' + escape(row[cfg.cohort]) + ' · D' + row[cfg.horizon] +
                ' · Observed state: ' + escape(row[cfg.observed_state]) + ' · Observed: ' +
                escape(row[cfg.observed] == null ? 'none' : row[cfg.observed]) +
                ' · Forecast: ' + escape(!cfg.forecast || row[cfg.forecast] == null ? 'none' : row[cfg.forecast]) +
                ' · Display: ' + state(row) + ' · ' + escape(reason(row)) + '</li>';
        }).join('');
        var provenance = cfg.forecast_metadata ? '<p>Supplied forecast · Model: ' + escape(cfg.forecast_metadata.model) +
            ' · Scenario: ' + escape(cfg.forecast_metadata.scenario) + ' · Issued: ' + escape(cfg.forecast_metadata.issued_at) +
            ' · Scope: ' + escape(cfg.forecast_metadata.scope) + '</p>' : '<p>No forecast column configured.</p>';
        content.innerHTML = context() + '<p class="fw-cohort-legend">Observed: supplied measurement · F: supplied forecast (dotted, italic) · —: unavailable, not mature, or no record</p>' +
            '<div class="fw-cohort-scroll" role="region" aria-label="Cohort observations by day horizon" tabindex="0"><table class="fw-cohort-table">' +
            '<caption>' + (cfg.title ? escape(cfg.title) + ' · ' : '') + 'Precomputed cohort cells · ' + escape(cfg.value_format) + '</caption><thead>' + header + '</thead><tbody>' + body + '</tbody>' + foot + '</table></div>' +
            (cfg.milestone ? '<p class="fw-cohort-note">' + escape(cfg.milestone.label) + ': first displayed horizon at or above ' + escape(format(cfg.milestone.threshold)) +
                ', observed first, otherwise supplied forecast. This is not an exact crossing date. Missing earlier horizons or values may conceal an earlier crossing. No displayed crossing is not failure.</p>' : '') +
            '<details class="fw-cohort-details"' + (wasOpen ? ' open' : '') + '><summary>Cell details and provenance</summary>' + provenance +
            '<p>Values and states are author-declared as of the supplied snapshot. No ratios, maturity, pooling, model fitting or validity policy are calculated.</p><ul>' + details + '</ul></details>';
        content.querySelector('.fw-cohort-scroll').scrollLeft = left;
        if (focused) content.querySelector('summary').focus({preventScroll:true});
        else if (scrollFocused) content.querySelector('.fw-cohort-scroll').focus({preventScroll:true});
    }
    button.onclick = function() {
        if (!selected.length || scopeError(selected)) { draw(selected); return; }
        var columns = ['cohort', 'cohort_month', 'horizon', 'observed', 'forecast', 'observed_state', 'display_state', 'reason', 'value_format',
            'population_label', 'population_as_of', 'population_timezone', 'population_source_time',
            'forecast_model', 'forecast_scenario', 'forecast_issued_at', 'forecast_scope'];
        var p = cfg.population, f = cfg.forecast_metadata || {};
        var rows = selected.slice().sort(function(a,b) {
            return a[cfg.cohort_month].localeCompare(b[cfg.cohort_month]) ||
                Array.from(groups.keys()).indexOf(a[cfg.cohort])-Array.from(groups.keys()).indexOf(b[cfg.cohort]) || a[cfg.horizon]-b[cfg.horizon];
        }).map(function(r) { return [r[cfg.cohort],r[cfg.cohort_month],r[cfg.horizon],r[cfg.observed],cfg.forecast ? r[cfg.forecast] : null,
            r[cfg.observed_state],state(r),reason(r),cfg.value_format,p.label,p.as_of,p.timezone,p.source_time || null,
            f.model || null,f.scenario || null,f.issued_at || null,f.scope || null]; });
        rows = rows.map(function(row) { return row.map(function(v) { return Object.is(v,-0) ? '-0' : v; }); });
        window._fwDownloadCsv(columns, rows, (document.body.getAttribute('data-report-slug') || 'data') + '_cohorts.csv');
    };
    function dispose() {
        if (!active) return;
        active = false;
        if (unsubscribe) unsubscribe();
        observer.disconnect(); button.onclick = null;
        if (root._fwCohortDispose === dispose) delete root._fwCohortDispose;
    }
    var observer = new MutationObserver(function() { if (!root.isConnected) dispose(); });
    observer.observe(document.body, {childList:true, subtree:true});
    root._fwCohortDispose = dispose;
    if (cfg.dataset_id) {
        engine.onReady(cfg.dataset_id, function() {
            if (!active || !root.isConnected) return;
            unsubscribe = engine.subscribe(cfg.dataset_id, id + '_cohorts', draw);
            draw(engine.getFiltered(cfg.dataset_id));
        });
    } else draw(source);
};
