    // ── Cross-filter bridge ──────────────────────────────
    // Finds the FilterBar dropdown for (dsId, column) and
    // programmatically updates its Slim Select selection.
    // This keeps the filter UI, engine state, URL, and propagation in sync.
    window._fwCrossFilter = function(dsId, column, value) {
        var bars = document.querySelectorAll('[data-fw-filter-bar]');
        for (var b = 0; b < bars.length; b++) {
            var bar = bars[b];
            if (bar.dataset.fwDsId !== dsId) continue;
            var sel = Array.from(bar.querySelectorAll('select[data-filter-col]')).find(function(node) {
                return node.dataset.filterCol === column;
            });
            if (!sel || !sel._fwSlimSelect) continue;
            var ss = sel._fwSlimSelect;
            if (value === null) {
                ss.setSelected([]);
            } else {
                ss.setSelected([String(value)]);
            }
            return true;
        }
        return false;
    };

    // Private owner lookup for declared table actions. Exact DOM comparisons
    // avoid treating authored column names or raw keys as CSS/HTML syntax.
    window._fwTableDropdownOwner = function(dsId, column) {
        var found = [];
        document.querySelectorAll('[data-fw-filter-bar]').forEach(function(bar) {
            if (bar.dataset.fwDsId !== dsId) return;
            bar.querySelectorAll('[data-filter-col]').forEach(function(node) {
                if (node.dataset.filterCol === column) found.push(node);
            });
        });
        if (found.length !== 1 || found[0].tagName !== 'SELECT')
            return {reason: 'Selection needs one existing dropdown owner.'};
        var node = found[0], engine = window._fwFilterEngine;
        if (node.closest('[data-live-state]') || engine._liveDatasets[dsId])
            return {reason: 'Live drill-down is unavailable.'};
        if (!node._fwSlimSelect || !engine.isReady(dsId))
            return {reason: 'Selection dropdown is not ready.'};
        var state = engine.getFilterState(dsId), current = state[node.dataset.filterId];
        return {node: node, values: current && Array.isArray(current.value) ? current.value.slice() : [],
            select: function(values) { node._fwSlimSelect.setSelected(values); },
            available: function(key) { return Array.from(node.options).some(function(option) {
                return option.value === key && !option.disabled;
            }); }};
    };
