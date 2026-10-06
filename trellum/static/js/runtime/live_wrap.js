    // ── Live Component Wrapper ────────────────────────────
    // Shared helper for filter-aware components.  Subscribes
    // to the filter engine and calls drawFn on every change.
    window._fwLiveWrap = function(id, cfg, drawFn) {
        window._fwFilterEngine.subscribe(cfg.dataset_id, id, drawFn);
        drawFn(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        window.addEventListener('fw-theme-change', function() {
            drawFn(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        });
    };
