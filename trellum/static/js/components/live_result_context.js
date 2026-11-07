/* Presentation only: result identity belongs to the live-query binder. */
(function() {
    if (window._fwLiveResultContext) return;
    var groups = [], observer = null, serial = 0, scheduled = false;

    function eligible(cfg) {
        return cfg && cfg.live_context === true && cfg.dataset_id && !cfg.comparison &&
            cfg.workspacePageSize !== 25 && (cfg.type === 'kpi_row_live' || cfg.type === 'table');
    }
    function ancestry(id, engine, components) {
        var seen = new Set(), path = [], current = id;
        // The engine's actual scope graph is authoritative. Never recurse into
        // getFiltered on a cyclic or excessively deep graph.
        while (current && path.length < 64 && !seen.has(current)) {
            seen.add(current); path.push(current);
            var scope = engine._scopes[current];
            if (scope) { current = scope.parent; continue; }
            var live = window._fwLiveQuery.getDatasetState(current);
            var declared = Object.keys(components).some(function(key) {
                var cfg = components[key];
                return cfg.type === 'live_data_source' && cfg.dataset_id === current &&
                    document.getElementById(key);
            });
            return live || declared ? {parent: current, path: path} : null;
        }
        return null;
    }
    function valueText(value) {
        if (value === null) return 'null';
        if (typeof value === 'object') return JSON.stringify(value);
        return String(value);
    }
    function paramsText(params) {
        if (params === null || params === undefined) return 'unknown';
        var keys = Object.keys(params).sort();
        return keys.length ? keys.map(function(key) { return key + '=' + valueText(params[key]); }).join(' · ') : '(no parameters)';
    }
    function sameParams(a, b) {
        if (a == null || b == null) return a === b;
        var keys = Object.keys(a).sort(), other = Object.keys(b).sort();
        return JSON.stringify(keys) === JSON.stringify(other) && keys.every(function(key) {
            return JSON.stringify(a[key]) === JSON.stringify(b[key]);
        });
    }
    function localText(group) {
        var parts = [], engine = window._fwFilterEngine;
        group.path.slice(0, -1).reverse().forEach(function(id) {
            var filters = engine.getFilterState(id);
            Object.keys(filters).sort().forEach(function(key) {
                var filter = filters[key];
                if (filter.mode === 'equals' && filter.value === '__all__') return;
                if (filter.value !== null && filter.value !== '' &&
                    !(Array.isArray(filter.value) && !filter.value.length))
                    parts.push(filter.column + '=' + valueText(filter.value));
            });
        });
        return parts.length ? parts.join(' · ') : 'all displayed parent rows';
    }
    function setText(el, text) {
        // Preserve the live region across draws and suppress repeated announcements.
        if (el.textContent !== text) el.textContent = text;
        el.hidden = !text;
    }
    function draw(group, state) {
        if (!state && group.state) { group.retired = true; schedule(); }
        group.state = state;
        if (!state) { if (group.block) group.block.remove(); return; }
        if (!group.block) {
            var block = group.block = document.createElement('div');
            block.className = 'fw-live-result-context';
            block.dataset.fwDataset = group.dataset;
            block.setAttribute('role', 'note');
            block.setAttribute('aria-label', 'Displayed live result');
            group.origin = document.createElement('div');
            group.scope = document.createElement('div');
            group.status = document.createElement('div');
            group.status.setAttribute('role', 'status');
            group.status.setAttribute('aria-live', 'polite');
            group.status.setAttribute('aria-atomic', 'true');
            block.append(group.origin, group.scope, group.status);
        }
        var origin, scope, status = '';
        if (!state.has_result) {
            origin = 'No displayed result · displayed parameters: unknown';
            scope = 'Returned row scope unavailable.';
        } else {
            origin = (state.origin === 'snapshot' ? 'Build snapshot' : 'Query result') +
                ' · displayed: ' + paramsText(state.displayed_params);
            if (state.origin === 'query' && state.completed_at) origin += ' · response received ' + state.completed_at;
            if (state.truncated === true) {
                scope = 'Limited to ' + state.row_count + ' returned rows; associated aggregates cover only these rows.';
            } else if (state.origin === 'snapshot') {
                scope = (state.row_count === 0 ? 'Valid empty snapshot (0 rows)' : state.row_count + ' snapshot rows') + ' · completeness unknown.';
            } else {
                scope = (state.row_count === 0 ? 'Valid empty returned result (0 rows)' : state.row_count + ' returned rows') +
                    ' · associated aggregates cover returned rows.';
            }
            if (group.path.length > 1) {
                scope += ' Local subset: ' + localText(group) + ' · ' +
                    window._fwFilterEngine.getFiltered(group.dataset).length + ' of ' + state.row_count + ' displayed live-parent rows.';
            }
        }
        var different = !sameParams(state.requested_params, state.displayed_params);
        if (state.phase === 'pending') {
            status = 'Loading requested: ' + paramsText(state.requested_params) +
                (state.has_result ? ' · retaining displayed result.' : ' · no result yet.');
        } else if (state.phase === 'error') {
            var error = String(state.error || 'Query failed');
            status = 'Request failed for ' + paramsText(state.requested_params) + ': ' +
                error.slice(0, 320) + (error.length > 320 ? '…' : '') +
                (state.has_result ? ' · retaining displayed result.' : ' · no displayed result.');
        } else if (different || !state.has_result) {
            status = 'Requested: ' + paramsText(state.requested_params);
        }
        setText(group.origin, origin); setText(group.scope, scope); setText(group.status, status);
        place(group);
    }
    function anchorFor(el, owner) {
        // Keep context outside a KPI grid, table scroller or grid cell. A nested
        // Section is its own owner; a Panel remains inside its native Section.
        var anchor = el;
        while (anchor.parentElement && anchor.parentElement !== owner &&
               !anchor.parentElement.classList.contains('fw-collapsible-body')) anchor = anchor.parentElement;
        if (anchor === el && el.classList.contains('fw-table-scroll') &&
            el.previousElementSibling && el.previousElementSibling.classList.contains('fw-data-table-header'))
            anchor = el.previousElementSibling;
        return anchor;
    }
    function place(group) {
        if (!group.block || !group.state || !group.members.length) return;
        var first = group.members.slice().sort(function(a, b) {
            return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_PRECEDING ? 1 : -1;
        })[0];
        var anchor = anchorFor(first, group.owner);
        var next = group.block.nextElementSibling;
        while (next && next.classList.contains('fw-live-result-context')) next = next.nextElementSibling;
        if (next !== anchor) anchor.before(group.block);
    }
    function dispose(group) {
        if (group.off) group.off();
        (group.engineOff || []).forEach(function(off) { off(); });
        if (group.block) group.block.remove();
    }
    function reconcile() {
        scheduled = false;
        var engine = window._fwFilterEngine, binder = window._fwLiveQuery;
        var components = (window._reportData || {}).components || {};
        var next = [], candidates = 0;
        if (engine && binder && binder.getDatasetState && !window._fwMetricsWorkspace) {
            Object.keys(components).forEach(function(id) {
                var cfg = components[id], el = document.getElementById(id);
                if (!eligible(cfg) || !el) return;
                candidates++;
                var owner = el.closest('.fw-section') || el.parentElement;
                var lineage = ancestry(cfg.dataset_id, engine, components);
                if (!lineage) return;
                var group = next.find(function(g) { return g.owner === owner && g.dataset === cfg.dataset_id; });
                if (!group) {
                    group = groups.find(function(g) {
                        return g.owner === owner && g.dataset === cfg.dataset_id &&
                            g.parent === lineage.parent && g.path.join('\0') === lineage.path.join('\0');
                    });
                    if (!group) group = {owner: owner, dataset: cfg.dataset_id, parent: lineage.parent, path: lineage.path};
                    group.members = []; next.push(group);
                }
                group.members.push(el);
            });
        }
        groups.forEach(function(group) { if (next.indexOf(group) === -1) dispose(group); });
        groups = next;
        groups.forEach(function(group) {
            var localIds = group.path.slice(0, -1);
            var replacedEngine = group.engineArrays && localIds.some(function(id, index) {
                return group.engineArrays[index] !== engine._subscribers[id];
            });
            if (group.retired || replacedEngine) {
                dispose(group); group.off = null; group.engineOff = []; group.retired = false;
            }
            if (!group.off) {
                group.off = binder.subscribeDataset(group.parent, function(state) { draw(group, state); });
                group.engineOff = localIds.map(function(id) {
                    return engine.subscribe(id, 'live-result-context-' + (++serial), function() {
                        draw(group, binder.getDatasetState(group.parent));
                    });
                });
                group.engineArrays = localIds.map(function(id) { return engine._subscribers[id]; });
            }
            draw(group, binder.getDatasetState(group.parent));
        });
        if (!candidates && observer) { observer.disconnect(); observer = null; }
    }
    function schedule(records) {
        if (records && records.every(function(record) {
            return record.target.nodeType === 1 && record.target.closest('.fw-live-result-context');
        })) return;
        if (!scheduled) { scheduled = true; queueMicrotask(reconcile); }
    }
    window._fwLiveResultContext = {
        attach: function() {
            reconcile();
            if (groups.length && !observer) {
                observer = new MutationObserver(schedule);
                observer.observe(document.body, {childList: true, subtree: true});
            }
        }
    };
})();
