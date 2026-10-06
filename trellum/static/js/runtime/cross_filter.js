    // ── Cross-filter bridge ──────────────────────────────
    // Finds the FilterBar dropdown for (dsId, column) and
    // programmatically updates its Slim Select selection.
    // This keeps the filter UI, engine state, URL, and propagation in sync.
    window._fwCrossFilter = function(dsId, column, value) {
        var bars = document.querySelectorAll('[data-fw-filter-bar]');
        for (var b = 0; b < bars.length; b++) {
            var bar = bars[b];
            if (bar.dataset.fwDsId !== dsId) continue;
            var sel = bar.querySelector('select[data-filter-col="' + column + '"]');
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
