    // ── Auto-refresh ──────────────────────────────────────
    var REFRESH_MS = _fwConfig.refreshMs;
    var _lastRefresh = Date.now();
    var _refreshTimer = null;
    var _refreshAbort = null;
    var _refreshSerial = 0;

    function _scheduleRefresh(delay) {
        clearTimeout(_refreshTimer);
        _refreshTimer = null;
        if (document.visibilityState === 'hidden') return;
        _refreshTimer = setTimeout(refreshData, Math.max(0, delay));
    }

    function refreshData() {
        // One request in flight; a newer refresh supersedes an older one.
        if (_refreshAbort) _refreshAbort.abort();
        var abort = typeof AbortController === 'function' ? new AbortController() : null;
        var serial = ++_refreshSerial;
        _refreshAbort = abort;
        _lastRefresh = Date.now();
        _scheduleRefresh(REFRESH_MS);
        fetch('data.json?t=' + Date.now(), { cache: 'no-store', signal: abort ? abort.signal : undefined })
            .then(function(resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function(newData) {
                if (serial !== _refreshSerial) return;
                _refreshAbort = null;
                // Capture state at apply time so interactions made while the
                // request was in flight survive the re-render.
                var state = _saveState();
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
                if (typeof window._fwUpdateFreshness === 'function') window._fwUpdateFreshness();
            })
            .catch(function(e) {
                if (serial !== _refreshSerial) return;
                _refreshAbort = null;
                if (e && e.name === 'AbortError') return;
                console.warn('Refresh failed:', e);
            });
    }

    // Only arm the timers when a refresh interval was configured; with
    // refreshMs at 0 this whole block used to be omitted from the page.
    if (REFRESH_MS > 0) {
        _scheduleRefresh(REFRESH_MS);

        // Mobile browsers throttle/suspend timers when backgrounded, so the
        // loop pauses while hidden and resumes on return: refresh straight
        // away if due, otherwise wait out the rest of the interval.
        function _resume() {
            var due = REFRESH_MS - (Date.now() - _lastRefresh);
            if (due <= 0) refreshData(); else _scheduleRefresh(due);
        }
        document.addEventListener('visibilitychange', function() {
            if (document.visibilityState === 'visible') {
                _resume();
            } else {
                clearTimeout(_refreshTimer);
                _refreshTimer = null;
            }
        });
        window.addEventListener('pageshow', function(e) {
            if (e.persisted) _resume();
        });
    }
