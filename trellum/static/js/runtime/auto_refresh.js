    // ── Auto-refresh ──────────────────────────────────────
    var REFRESH_MS = _fwConfig.refreshMs;
    var _lastRefresh = Date.now();

    function refreshData() {
        var state = _saveState();
        _lastRefresh = Date.now();
        fetch('data.json?t=' + Date.now())
            .then(function(resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function(newData) {
                window._reportData = newData;
                window._scopeCache = {};
                if (newData.defaultScope) {
                    window._scopeCache[newData.defaultScope] = newData.components || {};
                }
                if (typeof window.renderAll === 'function') window.renderAll();
                _restoreState(state);
                if (typeof window.onScopeChange === 'function') {
                    window.onScopeChange(window._currentScope);
                }
            })
            .catch(function(e) { console.warn('Refresh failed:', e); });
    }

    // Only arm the timers when a refresh interval was configured; with
    // refreshMs at 0 this whole block used to be omitted from the page.
    if (REFRESH_MS > 0) {
        setInterval(refreshData, REFRESH_MS);

        // Mobile browsers throttle/suspend setInterval when backgrounded —
        // force a refresh when the tab becomes visible again if stale.
        document.addEventListener('visibilitychange', function() {
            if (document.visibilityState === 'visible' && Date.now() - _lastRefresh >= REFRESH_MS) {
                refreshData();
            }
        });
        window.addEventListener('pageshow', function(e) {
            if (e.persisted && Date.now() - _lastRefresh >= REFRESH_MS) {
                refreshData();
            }
        });
    }
