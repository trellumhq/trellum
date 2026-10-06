window._fwRenderers['filter_bar'] = function(id, cfg) {
    var el = document.getElementById(id);
    if (!el) return;
    // Measure the MAIN FilterBar's height once and publish it as a
    // CSS custom property so section-scoped FilterBars (.fw-section
    // .fw-filter-bar) can stick BELOW it instead of overlapping. A
    // FilterBar is "main" when it is NOT inside any .fw-section
    // ancestor (i.e. it lives in an untitled top section). If there
    // are several FilterBars rendered, the first one on the page
    // wins — this is fine because the validator forbids more than one
    // main FilterBar.
    if (!el.closest('.fw-section') && !window._fwMainFbMeasured) {
        var mainFb = el;
        var setOffset = function() {
            var h = Math.ceil(mainFb.getBoundingClientRect().height) || 0;
            document.documentElement.style.setProperty(
                '--fw-main-fb-height', h + 'px'
            );
        };
        setOffset();
        // Re-measure once after layout settles (filter dropdowns etc.).
        requestAnimationFrame(setOffset);
        // And on resize — wrapping changes the bar's row count.
        window.addEventListener('resize', setOffset, {passive: true});
        window._fwMainFbMeasured = true;
    }
    // On scope switch the same DOM element is reused with a new dataset_id.
    // Clone to strip accumulated event listeners from the previous scope,
    // then fall through to full re-wiring for the current scope.
    if (el._fwWiredDsId && el._fwWiredDsId !== cfg.dataset_id) {
        var fresh = el.cloneNode(true);
        el.parentNode.replaceChild(fresh, el);
        el = fresh;
    }
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
        group.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
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
