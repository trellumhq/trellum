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
    if (el._fwDisposed || (el._fwWiredDsId && el._fwWiredDsId !== cfg.dataset_id)) {
        if (el._fwDisposeFilterBar) el._fwDisposeFilterBar();
        // Restore native controls before cloning; enhanced controls own popup
        // DOM and listeners outside the bar as well as their local elements.
        var oldTypes = window._fwFilterTypes || {};
        Object.keys(oldTypes).forEach(function(t) {
            if (oldTypes[t].disposeControls) oldTypes[t].disposeControls(el);
        });
        var fresh = el.cloneNode(true);
        el.parentNode.replaceChild(fresh, el);
        el = fresh;
    }
    if (el._fwWired) return;
    el._fwWired = true;
    el._fwWiredDsId = cfg.dataset_id;
    var dsId = cfg.dataset_id;
    var propTargets = cfg.propagate_to || {};
    var engine = window._fwFilterEngine;
    var disposed = false;
    var cascades = [];
    var stopPreparing = null;
    var stopReady = null;
    var stopLifecycle = null;
    var domObserver = null;
    var unregisterUrl = null;
    el._fwDisposeFilterBar = function() {
        if (disposed) return;
        disposed = true;
        el._fwDisposed = true;
        if (domObserver) domObserver.disconnect();
        if (stopPreparing) stopPreparing();
        if (stopReady) stopReady();
        if (stopLifecycle) stopLifecycle();
        if (unregisterUrl) unregisterUrl();
        var types = window._fwFilterTypes || {};
        Object.keys(types).forEach(function(t) {
            if (types[t].disposeControls) types[t].disposeControls(el);
        });
        cascades = [];
    };

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
        if (!disposed && window._fwUrlSync) window._fwUrlSync.write();
    }

    // Order dependencies, never declarations. Siblings are planned against the
    // same predicate state and corrected together. Cycles share a level; every
    // correction only removes selections, and the loop has an explicit bound.
    function _cascadeLayers() {
        var remaining = cascades.filter(function(entry) { return !entry.isDisposed(); });
        remaining.sort(function(a, b) {
            return (a.fc.column + '\0' + a.fc.id).localeCompare(b.fc.column + '\0' + b.fc.id);
        });
        var layers = [];
        while (remaining.length) {
            var layer = remaining.filter(function(entry) {
                return !remaining.some(function(parent) {
                    return entry.fc.depends_on.indexOf(parent.fc.column) !== -1;
                });
            });
            if (!layer.length) { layers.push(remaining); break; }
            layers.push(layer);
            remaining = remaining.filter(function(entry) { return layer.indexOf(entry) === -1; });
        }
        return layers;
    }

    // Only declared parent predicates trigger a cascade. Other active
    // predicates still constrain the option query, but an unrelated empty
    // date/platform result must not erase a meaningful child selection.
    function _parentSignature(columns) {
        var wanted = new Set(columns), predicates = new Set(), visited = new Set();
        var current = dsId;
        while (!visited.has(current)) {
            visited.add(current);
            var state = engine._state[current] || {};
            Object.keys(state).forEach(function(fid) {
                if (!wanted.has(state[fid].column)) return;
                var predicate = engine._normalizeFilter(state[fid]);
                if (predicate) predicates.add(engine._queryValueKey(predicate));
            });
            if (!engine._scopes[current]) break;
            current = engine._scopes[current].parent;
        }
        return JSON.stringify(Array.from(predicates).sort());
    }

    function _prepareCascades(context) {
        if (disposed || !engine.isReady(dsId)) return;
        // Loaded live rows describe the previous server query until its
        // response arrives. They cannot establish availability for a new parent.
        if (context && context.awaitingLiveSnapshot) return;
        var transient = context && context.transient;
        var layers = _cascadeLayers(), changed = false, activated = new Set();
        for (var round = 0; round < 100; round++) {
            changed = false;
            layers.forEach(function(layer) {
                var dropdown = window._fwFilterTypes.dropdown;
                var plans = layer.map(function(entry) {
                    var fc = entry.fc, ss = entry.ss;
                    var parentSig = _parentSignature(fc.depends_on);
                    if (entry.parentSig === parentSig && !entry.refreshAfterLiveResponse && !activated.has(entry)) return null;
                    // Once triggered, finish settling options against sibling
                    // and descendant corrections from this same interaction.
                    activated.add(entry);
                    var clear = fc.clear_on_parent_change && entry.parentSig !== null && entry.parentSig !== parentSig;
                    var available = engine.getDistinctValues(dsId, fc.column, {excludingColumns: [fc.column]});
                    var allowed = new Set(available);
                    var current = dropdown.selectedValues(ss);
                    var preserved = clear ? [] : current.filter(function(v) { return allowed.has(v); });
                    return {entry: entry, parentSig: parentSig, available: available,
                        current: current, preserved: preserved};
                }).filter(function(plan) { return plan !== null; });
                dropdown.batchControls(plans.map(function(plan) { return plan.entry.ss; }), function() {
                    plans.forEach(function(plan) {
                        var entry = plan.entry, optionSig = JSON.stringify(plan.available);
                        var optionsChanged = entry.optionSig !== optionSig;
                        if (optionsChanged || plan.preserved.length !== plan.current.length) {
                            dropdown.setCascadeOptions(entry.ss, entry.fc, plan.available, plan.preserved, optionsChanged);
                        }
                        entry.parentSig = plan.parentSig;
                        entry.optionSig = optionSig;
                        entry.refreshAfterLiveResponse = false;
                    });
                    plans.forEach(function(plan) {
                        if (plan.preserved.length === plan.current.length) return;
                        changed = true;
                        var fc = plan.entry.fc;
                        var set = transient ? engine.setFilterTransient : engine.setFilter;
                        set.call(engine, dsId, fc.id, fc.column, 'in', plan.preserved);
                        var propagate = transient ? _propagateTransient : _propagate;
                        propagate(fc.column, 'in', plan.preserved);
                        if (!transient) _urlWrite();
                    });
                });
            });
            if (engine._awaitingLiveSnapshot(dsId)) {
                // Removing an invalid live selection requests a broader server
                // result. Finish the activated option lists when it arrives.
                activated.forEach(function(entry) { entry.refreshAfterLiveResponse = true; });
                return;
            }
            if (!changed) break;
        }
        if (changed) throw new Error('Cascading filters did not settle');
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
        transaction: function(fn) {
            if (!disposed) return engine.transaction(fn);
        },
        registerCascade: function(fc, ss, isDisposed) {
            var entry = {fc: fc, ss: ss, isDisposed: isDisposed, parentSig: null, optionSig: null};
            cascades.push(entry);
            return function() {
                var i = cascades.indexOf(entry);
                if (i !== -1) cascades.splice(i, 1);
            };
        },
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

    // Initial URL/default writes, control wiring and cascade correction form
    // one interaction too; no consumer should see partly attached controls.
    engine.transaction(function() {
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
                helpers.transaction(function() { handler.onClick(el, fc, dsId, helpers, btn); });
            }
        });

        // Wire type-specific controls (Slim Select, date pickers, presets, etc.)
        var types = window._fwFilterTypes || {};
        Object.keys(types).forEach(function(t) {
            if (types[t].wireControls) {
                types[t].wireControls(el, cfg, dsId, helpers);
            }
        });
        if (cascades.length) {
            stopPreparing = engine.beforeNotify(dsId, 'cascade_' + id, _prepareCascades);
            stopReady = engine.onReady(dsId, function() {
                if (!disposed) engine.prepare(dsId);
            });
            stopLifecycle = engine.subscribeLifecycle(function(event) {
                if (event.dataset_id !== dsId) return;
                if (event.type === 'remove') {
                    // Dataset storage can be replaced while its controls stay
                    // mounted. Retire the engine binding, not the widgets.
                    if (stopPreparing) stopPreparing();
                    if (stopReady) stopReady();
                    stopPreparing = stopReady = null;
                    cascades.forEach(function(entry) { entry.parentSig = null; });
                } else if (!stopPreparing && (event.type === 'init' || event.type === 'scope')) {
                    stopPreparing = engine.beforeNotify(dsId, 'cascade_' + id, _prepareCascades);
                    engine.prepare(dsId);
                }
            });
        }
        // Widget and URL ownership follows the mounted bar, including bars
        // without cascades; those need no dataset lifecycle subscription.
        domObserver = new MutationObserver(function() {
            if (document.getElementById(id) !== el) el._fwDisposeFilterBar();
        });
        domObserver.observe(document.documentElement, {childList: true, subtree: true});

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
            unregisterUrl = window._fwUrlSync.register(dsId, cfg.filters, function() {
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
    });
    // Normal control readiness also wires tables mounted before this bar.
    window.dispatchEvent(new CustomEvent('fw-dropdowns-ready', {detail: {dataset_id: dsId}}));
};
