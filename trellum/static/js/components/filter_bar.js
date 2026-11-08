// One layout observer owns the report's bars, including bars revealed in tabs
// or replaced by a scope switch. Long phone bars stay in normal page flow.
if (!window._fwWatchFilterBarLayout) {
    (function() {
        var bars = new Set(), pending = false;
        var observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(queue);
        function measure() {
            pending = false;
            var phone = window.matchMedia('(max-width: 640px)').matches;
            bars.forEach(function(bar) {
                if (!bar.isConnected) {
                    if (observer) observer.unobserve(bar);
                    bars.delete(bar);
                    return;
                }
                var height = bar.getBoundingClientRect().height;
                bar.classList.toggle('fw-filter-bar--flow', phone && height > 96);
            });
            // Reserve space only for a currently pinned main bar. Resolve it
            // again after replacement rather than retaining a detached element.
            var main = Array.from(document.querySelectorAll('.fw-filter-bar')).find(function(bar) {
                return !bar.closest('.fw-section');
            });
            var height = main && getComputedStyle(main).position === 'sticky'
                ? Math.ceil(main.getBoundingClientRect().height) : 0;
            document.documentElement.style.setProperty('--fw-main-fb-height', height + 'px');
        }
        function queue() {
            if (!pending) { pending = true; requestAnimationFrame(measure); }
        }
        window.addEventListener('resize', queue, {passive: true});
        new MutationObserver(function(records) {
            if (records.some(function(record) { return record.removedNodes.length; })) queue();
        }).observe(document.body, {childList: true, subtree: true});
        window._fwWatchFilterBarLayout = function(bar) {
            if (!bars.has(bar)) {
                bars.add(bar);
                if (observer) observer.observe(bar);
            }
            queue();
        };
    })();
}
window._fwRenderers['filter_bar'] = function(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    // On scope switch the same DOM element is reused with a new dataset_id.
    // Clone to strip accumulated event listeners from the previous scope,
    // then fall through to full re-wiring for the current scope.
    if (el._fwWiredDsId && el._fwWiredDsId !== cfg.dataset_id) {
        var fresh = el.cloneNode(true);
        el.parentNode.replaceChild(fresh, el);
        el = fresh;
    }
    window._fwWatchFilterBarLayout(el);
    if (el._fwWired) return;
    el._fwWired = true;
    el._fwWiredDsId = cfg.dataset_id;
    var dsId = cfg.dataset_id;
    var propTargets = cfg.propagate_to || {};

    // A FilterBar bound to a LiveDataSource is server-rendered disabled
    // with an honest note (see trellum.components.filterable.FilterBar).
    // attachFilterBar() wires the note/status/chip DOM and reports whether
    // this page is actually armed (host + endpoint + not a share link) --
    // when it isn't, wiring stops here: no SlimSelect/noUiSlider instance,
    // no event listener, no URL-sync registration, so an unarmed live bar
    // issues zero requests no matter how it's interacted with.
    if (cfg.live_dataset) {
        var armed = window._fwLiveQuery && window._fwLiveQuery.attachFilterBar(dsId, el);
        if (!armed) return;
    }

    var urlParams = window._fwUrlSync ? window._fwUrlSync.parse() : {};

    function _propagateWith(engineFn, srcCol, filterType, value, flagValue) {
        var targets = Object.keys(propTargets);
        for (var t = 0; t < targets.length; t++) {
            var tgtDs = targets[t];
            var colMap = propTargets[tgtDs];
            var tgtCol = colMap[srcCol];
            if (!tgtCol) continue;
            var tgtFid = 'prop_' + dsId + '_' + srcCol + '_' + tgtDs;
            if (flagValue !== undefined) {
                engineFn(tgtDs, tgtFid, tgtCol, filterType, value, flagValue);
            } else {
                engineFn(tgtDs, tgtFid, tgtCol, filterType, value);
            }
        }
    }

    function _propagate(srcCol, filterType, value, flagValue) {
        _propagateWith(window._fwFilterEngine.setFilter.bind(window._fwFilterEngine),
            srcCol, filterType, value, flagValue);
    }

    // Mid-drag propagation: a slider dragging locally previews on every
    // propagated NON-live target exactly as setFilterTransient does for
    // its own dataset, and never queries a propagated LIVE target either
    // -- a shared date-range slider must not fire 3 linked queries per
    // animation frame while the handle is still moving.
    function _propagateTransient(srcCol, filterType, value, flagValue) {
        _propagateWith(window._fwFilterEngine.setFilterTransient.bind(window._fwFilterEngine),
            srcCol, filterType, value, flagValue);
    }

    function _urlWrite() {
        if (window._fwUrlSync) window._fwUrlSync.write();
    }

    function _findFc(fid) {
        for (var i = 0; i < cfg.filters.length; i++) {
            if (cfg.filters[i].id === fid) return cfg.filters[i];
        }
        return null;
    }

    function _urlLookup(column) {
        return window._fwUrlSync
            ? window._fwUrlSync.lookup(urlParams, dsId, column)
            : undefined;
    }

    var helpers = {
        setFilter: function(fid, col, mode, val, flagVal) {
            if (flagVal !== undefined) {
                window._fwFilterEngine.setFilter(dsId, fid, col, mode, val, flagVal);
            } else {
                window._fwFilterEngine.setFilter(dsId, fid, col, mode, val);
            }
        },
        // Mid-drag slider ticks: records state (readout, eventual URL) but
        // never queries a live dataset and never re-notifies subscribers
        // for one either -- see filter_engine.js's _commitFilter. Normal
        // (non-live) datasets behave exactly as setFilter always has here.
        setFilterTransient: function(fid, col, mode, val, flagVal) {
            if (flagVal !== undefined) {
                window._fwFilterEngine.setFilterTransient(dsId, fid, col, mode, val, flagVal);
            } else {
                window._fwFilterEngine.setFilterTransient(dsId, fid, col, mode, val);
            }
        },
        propagate: _propagate,
        propagateTransient: _propagateTransient,
        urlWrite: _urlWrite,
        findFc: _findFc,
        urlLookup: _urlLookup
    };

    // Initialize each filter via its type handler
    cfg.filters.forEach(function(fc) {
        var handler = window._fwFilterTypes && window._fwFilterTypes[fc.type];
        if (handler && handler.init) {
            handler.init(el, fc, dsId, helpers);
        }
    });

    // Toggle / flag click handler (shared for toggle-group based filters)
    el.addEventListener('click', function(e) {
        var btn = e.target.closest('.fw-toggle-btn');
        if (!btn) return;
        var group = btn.closest('[data-filter-id]');
        if (!group) return;
        var fid = group.dataset.filterId;
        var fc = _findFc(fid);
        if (!fc) return;
        group.querySelectorAll('.fw-toggle-btn').forEach(function(b) {
            b.classList.remove('active');
            b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        var handler = window._fwFilterTypes && window._fwFilterTypes[fc.type];
        if (handler && handler.onClick) {
            handler.onClick(el, fc, dsId, helpers, btn);
        }
    });

    // Wire type-specific controls (Slim Select, date pickers, presets, etc.)
    var types = window._fwFilterTypes || {};
    Object.keys(types).forEach(function(t) {
        if (types[t].wireControls) {
            types[t].wireControls(el, cfg, dsId, helpers);
        }
    });

    // Normal control readiness also wires tables mounted before this bar.
    window.dispatchEvent(new CustomEvent('fw-dropdowns-ready', {detail: {dataset_id: dsId}}));

    // Share button
    var shareBtn = el.querySelector('.fw-share-btn');
    if (shareBtn) {
        shareBtn.addEventListener('click', function() {
            navigator.clipboard.writeText(location.href).then(function() {
                shareBtn.classList.add('copied');
                setTimeout(function() { shareBtn.classList.remove('copied'); }, 1500);
            });
        });
    }

    // Register with URL sync for serialization
    if (window._fwUrlSync) {
        window._fwUrlSync.register(dsId, cfg.filters, function() {
            var state = [];
            cfg.filters.forEach(function(fc) {
                var fState = window._fwFilterEngine._state[dsId]
                    && window._fwFilterEngine._state[dsId][fc.id];
                if (fState) {
                    state.push({
                        column: fc.column, type: fc.type,
                        value: fState.value, flagValue: fState.flagValue
                    });
                }
            });
            return state;
        });
    }
};
