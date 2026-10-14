    // ── Live Component Wrapper ────────────────────────────
    // Shared helper for filter-aware components.  Subscribes
    // to the filter engine and calls drawFn on every change.
    window._fwLiveWrap = function(id, cfg, drawFn) {
        if (window._fwMetricsWorkspace) {
            var leases = window._fwMetricDrawLeases || (window._fwMetricDrawLeases = {});
            if (leases[id]) leases[id]();
            var active = true;
            function current() {
                if (active && !window._fwWorkspaceDisposed && document.getElementById(id)) {
                    var started = performance.now(); drawFn(window._fwFilterEngine.getFiltered(cfg.dataset_id));
                    var work = window._fwWorkspaceWork;
                    if (work) {
                        var elapsed = performance.now() - started;
                        work.max_consumer_ms = Math.max(work.max_consumer_ms, elapsed);
                        if (cfg.type === 'chart') work.max_chart_ms = Math.max(work.max_chart_ms, elapsed);
                    }
                }
            }
            var focused = new URLSearchParams(location.search).has('only');
            function queued() {
                // One focused chart preserves immediate filter feedback;
                // multi-chart pages coalesce and yield between consumers.
                if (focused) current(); else window._fwQueueMetricDraw(id, current);
            }
            var unsubscribe = window._fwFilterEngine.subscribe(cfg.dataset_id, id, queued);
            window.addEventListener('fw-theme-change', queued);
            leases[id] = function() {
                active = false; unsubscribe(); window.removeEventListener('fw-theme-change', queued);
            };
            current();
            return;
        }
        window._fwFilterEngine.subscribe(cfg.dataset_id, id, drawFn);
        drawFn(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        window.addEventListener('fw-theme-change', function() {
            drawFn(window._fwFilterEngine.getFiltered(cfg.dataset_id));
        });
    };

    (function() {
        var queue = new Map(), timer = null, disposed = false;
        function drain() {
            timer = null;
            var started = performance.now();
            while (queue.size && !disposed) {
                var entry = queue.entries().next().value;
                queue.delete(entry[0]); entry[1]();
                if (performance.now() - started >= 8) break;
            }
            if (queue.size && !disposed) timer = setTimeout(drain, 16);
        }
        window._fwQueueMetricDraw = function(id, draw) {
            if (disposed) return;
            queue.set(id, draw);
            if (timer === null) timer = setTimeout(drain, 0);
        };
        window.addEventListener('pagehide', function() {
            if (!window._fwMetricsWorkspace) return;
            disposed = true; clearTimeout(timer); queue.clear();
            window._fwWorkspaceDisposed = true;
            Object.values(window._fwMetricDrawLeases || {}).forEach(function(dispose) { dispose(); });
            window._fwMetricDrawLeases = {};
        });
        window.addEventListener('pageshow', function(event) {
            if (!event.persisted || !window._fwMetricsWorkspace) return;
            disposed = false;
            window._fwWorkspaceDisposed = false;
            if (typeof window.renderAll === 'function') window.renderAll();
        });
    })();
