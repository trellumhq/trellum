    // ── Filter Engine ────────────────────────────────────
    // Uses a columnar storage engine with hash indexes.
    // getFiltered() returns plain row objects for component compatibility.
    window._fwFilterEngine = {
        _datasets: {},
        _columnar: {},
        _state: {},
        _subscribers: {},
        _cache: {},
        _scopes: {},   // childId → { parent: parentId } for ScopedDataSource
        _liveDatasets: {},  // dsId → binder hooks ({onChange}) for LiveDataSource
        _preparers: {},
        _pending: new Set(),
        _preparePending: new Set(),
        _livePending: new Set(),
        _liveSnapshotPending: new Set(),
        _pendingTransient: new Map(),
        _afterTransaction: new Map(),
        _transactionDepth: 0,
        _flushing: false,

        // Transactions settle control corrections before any row consumer or
        // live query sees state. Nested calls belong to the same interaction.
        transaction: function(fn) {
            this._transactionDepth++;
            try { return fn(); }
            finally {
                this._transactionDepth--;
                if (!this._transactionDepth) this._flush();
            }
        },

        afterTransaction: function(id, fn) {
            if (this._transactionDepth || this._flushing) this._afterTransaction.set(id, fn);
            else fn();
        },

        beforeNotify: function(dsId, id, fn) {
            var entries = this._preparers[dsId] || (this._preparers[dsId] = []);
            var entry = {id: id, fn: fn};
            entries.push(entry);
            return function() {
                var index = entries.indexOf(entry);
                if (index !== -1) entries.splice(index, 1);
            };
        },

        _affectedDatasets: function(dsId) {
            var ids = new Set([dsId]), queue = [dsId];
            for (var i = 0; i < queue.length; i++) {
                for (var sid in this._scopes) {
                    if (this._scopes[sid].parent === queue[i] && !ids.has(sid)) {
                        ids.add(sid);
                        queue.push(sid);
                    }
                }
            }
            return queue;
        },

        _scopeDepth: function(dsId) {
            var visited = new Set();
            while (this._scopes[dsId]) {
                if (visited.has(dsId)) throw new Error('Cyclic scoped dataset');
                visited.add(dsId);
                dsId = this._scopes[dsId].parent;
            }
            return visited.size;
        },

        _managed: function(dsId) {
            var self = this;
            return this._transactionDepth || this._flushing || this._affectedDatasets(dsId).some(function(id) {
                return (self._preparers[id] || []).length > 0;
            });
        },

        _queue: function(dsId, transient, liveWrite) {
            var self = this;
            if (liveWrite) this._liveSnapshotPending.add(dsId);
            this._affectedDatasets(dsId).forEach(function(id) {
                self._cache[id] = null;
                self._preparePending.add(id);
                // A committed change dominates transient corrections in the
                // same interaction. Pure slider previews cannot issue queries.
                self._pendingTransient.set(id, transient === true && self._pendingTransient.get(id) !== false);
                // A new server query supersedes queued deliveries of its old
                // response. Later local edits may still filter retained rows.
                if (liveWrite) self._pending.delete(id);
                else self._pending.add(id);
            });
            if (liveWrite && !transient) this._livePending.add(dsId);
        },

        prepare: function(dsId) {
            var self = this;
            this._affectedDatasets(dsId).forEach(function(id) { self._preparePending.add(id); });
            this._flush();
        },

        _awaitingLiveSnapshot: function(dsId) {
            var seen = new Set();
            while (!seen.has(dsId)) {
                seen.add(dsId);
                if (this._liveSnapshotPending.has(dsId)) return true;
                if (!this._scopes[dsId]) break;
                dsId = this._scopes[dsId].parent;
            }
            return false;
        },

        _flush: function() {
            if (this._flushing || this._transactionDepth) return;
            this._flushing = true;
            var self = this, rounds = 0;
            try {
                while (this._pending.size || this._preparePending.size || this._livePending.size) {
                    while (this._preparePending.size) {
                        if (++rounds > 100) throw new Error('Filter preparation did not settle');
                        var dirty = Array.from(this._preparePending).sort(function(a, b) {
                            return self._scopeDepth(a) - self._scopeDepth(b) || a.localeCompare(b);
                        });
                        this._preparePending.clear();
                        dirty.forEach(function(dsId) {
                            (self._preparers[dsId] || []).slice().forEach(function(entry) {
                                if ((self._preparers[dsId] || []).indexOf(entry) !== -1) {
                                    entry.fn({transient: self._pendingTransient.get(dsId) === true,
                                        awaitingLiveSnapshot: self._awaitingLiveSnapshot(dsId)});
                                }
                            });
                        });
                    }
                    var live = Array.from(this._livePending);
                    this._livePending.clear();
                    live.forEach(function(dsId) {
                        var hooks = self._liveDatasets[dsId];
                        if (hooks) hooks.onChange();
                    });
                    // A synchronous live response can add preparation work.
                    if (this._preparePending.size) continue;
                    var pending = Array.from(this._pending);
                    this._pending.clear();
                    pending.forEach(function(dsId) {
                        var subscribers = (self._subscribers[dsId] || []).slice();
                        if (!subscribers.length) return;
                        var filtered = self.getFiltered(dsId);
                        subscribers.forEach(function(entry) {
                            if ((self._subscribers[dsId] || []).indexOf(entry) !== -1) entry.fn(filtered);
                        });
                    });
                    if (++rounds > 100) throw new Error('Filter notifications did not settle');
                }
                var after = Array.from(this._afterTransaction.values());
                this._afterTransaction.clear();
            } finally {
                // A failed preparer/subscriber cannot leak work into the next
                // interaction or leave the engine permanently batching.
                this._pending.clear();
                this._preparePending.clear();
                this._livePending.clear();
                this._pendingTransient.clear();
                this._afterTransaction.clear();
                this._flushing = false;
            }
            after.forEach(function(fn) { fn(); });
        },

        _filtersChanged: function(dsId, transient) {
            var liveHooks = this._liveDatasets[dsId];
            if (this._managed(dsId)) {
                this._queue(dsId, transient, !!liveHooks);
                this._flush();
            } else if (liveHooks) {
                this._liveSnapshotPending.add(dsId);
                if (!transient) liveHooks.onChange();
            } else {
                this._cache[dsId] = null;
                this.notify(dsId);
            }
        },

        // Opt-in structural lifecycle; row notifications retain their existing contract.
        subscribeLifecycle: function(fn) {
            if (!this._lifecycleListeners) this._lifecycleListeners = new Set();
            var entry = {fn: fn};
            this._lifecycleListeners.add(entry);
            var listeners = this._lifecycleListeners;
            return function() { listeners.delete(entry); };
        },

        _emitLifecycle: function(type, dsId) {
            if (!this._lifecycleListeners) return;
            var listeners = this._lifecycleListeners;
            Array.from(listeners).forEach(function(entry) {
                if (listeners.has(entry)) entry.fn({type: type, dataset_id: dsId});
            });
        },

        init: function(dsId, rows) {
            // Deprecated legacy entrypoint: normalize rows into columnar storage.
            this.initColumnar(dsId, this._rowsToColumnar(rows || []));
        },

        _rowsToColumnar: function(rows) {
            var cols = [];
            if (rows && rows.length > 0) cols = Object.keys(rows[0]);
            var data = new Array(rows.length);
            for (var ri = 0; ri < rows.length; ri++) {
                var r = rows[ri] || {};
                var out = new Array(cols.length);
                for (var ci = 0; ci < cols.length; ci++) out[ci] = r[cols[ci]];
                data[ri] = out;
            }
            return { _cols: cols, _data: data, _dict: {} };
        },

        _buildColumnarIndexes: function(col) {
            if (!col._colMap) {
                var cm = {};
                for (var c = 0; c < col._cols.length; c++) cm[col._cols[c]] = c;
                col._colMap = cm;
            }
            var colMap = col._colMap;
            var indexes = {};
            var dict = col._dict || {};
            var data = col._data;
            for (var colName in dict) {
                var ci = colMap[colName];
                if (ci === undefined) continue;
                var vocab = dict[colName];
                var map = {};
                for (var v = 0; v < vocab.length; v++) map[String(vocab[v])] = [];
                for (var ri = 0; ri < data.length; ri++) {
                    map[String(vocab[data[ri][ci]])].push(ri);
                }
                indexes[colName] = map;
            }
            col._indexes = indexes;
            col._queryCache = new Map();
            col._queryCacheRows = 0;
            return col;
        },

        getDatasetIds: function() {
            var out = [];
            for (var dsId in this._columnar) {
                if (this._columnar[dsId]) out.push(dsId);
            }
            for (var sid in this._scopes) {
                if (this._scopes[sid]) out.push(sid);
            }
            return out;
        },

        getDatasetRowCount: function(dsId) {
            if (this._scopes[dsId]) {
                // For scoped children, return the *currently filtered* count
                // (it's a derived view — there is no "raw row count").
                return this.getFiltered(dsId).length;
            }
            var col = this._columnar[dsId];
            return (col && col._data) ? col._data.length : 0;
        },

        clearFilter: function(dsId, filterId) {
            if (this._state[dsId] && this._state[dsId][filterId]) {
                delete this._state[dsId][filterId];
                this._filtersChanged(dsId, false);
            }
        },

        clearAllFilters: function(dsId) {
            if (this._state[dsId]) {
                this._state[dsId] = {};
                this._filtersChanged(dsId, false);
            }
        },

        removeDataset: function(dsId) {
            var liveHooks = this._liveDatasets[dsId];
            if (liveHooks && liveHooks.onDispose) liveHooks.onDispose();
            var self = this;
            this._affectedDatasets(dsId).forEach(function(id) {
                self._cache[id] = null;
                self._pending.delete(id);
                self._preparePending.delete(id);
                self._livePending.delete(id);
                self._liveSnapshotPending.delete(id);
                self._pendingTransient.delete(id);
            });
            delete this._columnar[dsId];
            delete this._datasets[dsId];
            delete this._state[dsId];
            delete this._cache[dsId];
            delete this._subscribers[dsId];
            delete this._scopes[dsId];
            delete this._liveDatasets[dsId];
            delete this._preparers[dsId];
            this._emitLifecycle('remove', dsId);
        },

        getEngineStats: function() {
            var ids = this.getDatasetIds();
            var totalRows = 0;
            for (var i = 0; i < ids.length; i++) totalRows += this.getDatasetRowCount(ids[i]);
            return { datasetCount: ids.length, totalRows: totalRows };
        },

        waitForDataset: function(dsId, timeoutMs) {
            var self = this;
            var maxWait = typeof timeoutMs === 'number' ? timeoutMs : 5000;
            return new Promise(function(resolve) {
                if (self.isReady(dsId)) { resolve(true); return; }
                var elapsed = 0;
                var handle = setInterval(function() {
                    elapsed += 50;
                    if (self.isReady(dsId)) {
                        clearInterval(handle);
                        resolve(true);
                    } else if (elapsed >= maxWait) {
                        clearInterval(handle);
                        resolve(false);
                    }
                }, 50);
            });
        },

        getDatasetMode: function(dsId) {
            if (this._scopes[dsId]) return 'scoped';
            return this._columnar[dsId] ? 'columnar' : 'missing';
        },

        _setColumnarDataset: function(dsId, col) {
            var _tFe = performance.now();
            var colMap = {};
            for (var c = 0; c < col._cols.length; c++) colMap[col._cols[c]] = c;
            col._colMap = colMap;
            delete col._indexes;
            delete col._allRows;
            col._queryCache = new Map();
            col._queryCacheRows = 0;
            var indexBuild = {};
            col._indexBuild = indexBuild;
            this._columnar[dsId] = col;
            this._datasets[dsId] = null;
            var engine = this;
            this._affectedDatasets(dsId).forEach(function(id) { engine._cache[id] = null; });
            if (!this._state[dsId]) this._state[dsId] = {};
            if (!this._subscribers[dsId]) this._subscribers[dsId] = [];
            var self = this;
            var n = (col._data || []).length;
            if (n < 10000) {
                self._buildColumnarIndexes(col);
            } else {
                (window.requestIdleCallback || setTimeout)(function() {
                    if (self._columnar[dsId] !== col || col._indexBuild !== indexBuild) return;
                    self._buildColumnarIndexes(col);
                    self._cache[dsId] = null;
                }, {timeout: 200});
            }
            window._fwTimings = window._fwTimings || {};
            window._fwTimings.filter_init_ms = (window._fwTimings.filter_init_ms || 0) + Math.round(performance.now() - _tFe);
        },

        initColumnar: function(dsId, col) {
            if (!col) {
                this._setColumnarDataset(dsId, { _cols: [], _data: [], _dict: {} });
                this._emitLifecycle('init', dsId);
                return;
            }
            if (!(col._cols && col._data)) col = this._rowsToColumnar(col);
            this._setColumnarDataset(dsId, col);
            this._emitLifecycle('init', dsId);
        },

        setFilter: function(dsId, filterId, column, mode, value, flagValue) {
            this._commitFilter(dsId, filterId, column, mode, value, flagValue, false);
        },

        // Transient commit — the slider 'slide' path. Records state (so the
        // readout and eventual URL write see it) but never queries a live
        // dataset and, for a live dataset, never re-notifies subscribers
        // (there is nothing fresher to show until a real commit lands). For
        // a normal dataset this behaves exactly like setFilter always has —
        // local re-filter + notify on every tick — so client-side drag
        // preview is unchanged.
        setFilterTransient: function(dsId, filterId, column, mode, value, flagValue) {
            this._commitFilter(dsId, filterId, column, mode, value, flagValue, true);
        },

        _commitFilter: function(dsId, filterId, column, mode, value, flagValue, isTransient) {
            if (!this._state[dsId]) this._state[dsId] = {};
            this._state[dsId][filterId] = {
                column: column, mode: mode, value: value, flagValue: flagValue || null
            };
            this._filtersChanged(dsId, isTransient);
        },

        // Registers dsId as server-backed: setFilter routes to hooks.onChange()
        // instead of local re-filtering, and getFiltered() returns the loaded
        // rows verbatim (the server already applied the predicates).
        registerLiveDataset: function(dsId, hooks) {
            this._liveDatasets[dsId] = hooks;
            if (!this._state[dsId]) this._state[dsId] = {};
            if (!this._subscribers[dsId]) this._subscribers[dsId] = [];
        },

        unregisterLiveDataset: function(dsId, hooks) {
            if (this._liveDatasets[dsId] === hooks) {
                delete this._liveDatasets[dsId];
                this._livePending.delete(dsId);
                this._liveSnapshotPending.delete(dsId);
            }
        },

        isLiveDataset: function(dsId) {
            return !!this._liveDatasets[dsId];
        },

        // Feeds a fresh live-query response into the dataset and fans it out
        // to every subscriber (charts/table/KPI rows) via the normal notify()
        // path — no component knows the rows came from a POST rather than a
        // filter. cols/rows are already columnar (array-of-arrays).
        setLiveRows: function(dsId, cols, rows) {
            this.initColumnar(dsId, { _cols: cols, _data: rows, _dict: {} });
            this._liveSnapshotPending.delete(dsId);
            this.notify(dsId);
        },

        isReady: function(dsId) {
            // Scoped child is ready iff its parent is ready (rows are derived).
            if (this._scopes[dsId]) return this.isReady(this._scopes[dsId].parent);
            return !!this._columnar[dsId];
        },

        getFilterState: function(dsId) {
            var src = this._state[dsId] || {};
            var out = {};
            for (var fid in src) {
                var f = src[fid];
                var value = f.value;
                if (Array.isArray(value)) {
                    value = value.slice();
                } else if (value && typeof value === 'object') {
                    value = Object.assign({}, value);
                }
                out[fid] = {
                    column: f.column,
                    mode: f.mode,
                    value: value,
                    flagValue: f.flagValue || null
                };
            }
            return out;
        },

        _localFiltersExcluding: function(dsId, columns) {
            var ignored = new Set((columns || []).map(String));
            var state = this._state[dsId] || {}, filters = Object.create(null);
            Object.keys(state).forEach(function(fid) {
                if (!ignored.has(String(state[fid].column))) filters[fid] = state[fid];
            });
            return filters;
        },

        // Keep primitive types (including null, undefined and non-finite
        // numbers) distinct in cache keys. Object property order is irrelevant.
        _queryValueKey: function(value) {
            if (value === null) return '["null"]';
            var type = typeof value;
            if (type === 'object') {
                var self = this;
                if (Array.isArray(value)) return JSON.stringify(['array', value.map(function(v) {
                    return self._queryValueKey(v);
                })]);
                return JSON.stringify(['object', Object.keys(value).sort().map(function(key) {
                    return [key, self._queryValueKey(value[key])];
                })]);
            }
            return JSON.stringify([type, String(value)]);
        },

        _normalizeFilter: function(f) {
            var self = this, value = f.value, out = {column: f.column, mode: f.mode};
            if (f.mode === 'equals') {
                if (value === '__all__') return null;
                out.value = value;
            } else if (f.mode === 'in') {
                if (!value || !value.length) return null;
                out.value = Array.isArray(value) ? Array.from(new Set(value)).sort(function(a, b) {
                    return self._queryValueKey(a).localeCompare(self._queryValueKey(b));
                }) : value;
            } else if (f.mode === 'flag') {
                if (value !== 'only' && value !== 'exclude') return null;
                out.value = value;
                out.flagValue = f.flagValue;
            } else if (f.mode === 'range' || f.mode === 'numrange') {
                var bounds = value || {};
                var numeric = f.mode === 'numrange';
                var hasMin = numeric ? bounds.min != null : !!bounds.min;
                var hasMax = numeric ? bounds.max != null : !!bounds.max;
                if (!hasMin && !hasMax) return null;
                out.value = {};
                if (hasMin) out.value.min = numeric ? String(Number(bounds.min)) : bounds.min;
                if (hasMax) out.value.max = numeric ? String(Number(bounds.max)) : bounds.max;
            } else return null;
            return out;
        },

        // Canonical effective predicates, independent of filter IDs, ordering,
        // excluded column names and inactive controls, across all ancestors.
        _filterQuery: function(dsId, localFilters) {
            var layers = [], visited = new Set(), id = dsId;
            while (true) {
                if (visited.has(id)) throw new Error('Cyclic scoped dataset');
                visited.add(id);
                // Ancestor getFiltered() reads a live snapshot verbatim. An
                // explicit exclusion query still applies the requested scope's
                // local predicates, including when it is itself live.
                var explicitLocal = id === dsId && localFilters !== undefined;
                if (explicitLocal || !this._liveDatasets[id]) {
                    layers.push({id: id, filters: id === dsId && localFilters !== undefined
                        ? localFilters : (this._state[id] || {})});
                }
                if (!this._scopes[id] || (this._liveDatasets[id] && !explicitLocal)) break;
                id = this._scopes[id].parent;
            }
            var col = this._columnar[id], canonical = new Map(), self = this;
            layers.forEach(function(layer) {
                var filters = layer.filters;
                Object.keys(filters).forEach(function(fid) {
                    var f = filters[fid];
                    if (!col) return;
                    var missing = col._colMap[f.column] === undefined;
                    if (missing && layer.id === id) return;
                    var normalized = self._normalizeFilter(f);
                    if (!normalized) return;
                    // Source predicates historically use dictionary hash
                    // lookups; scoped predicates compare decoded row values.
                    // Keep that distinction (notably null/empty and typed
                    // filter values) while composing both without row objects.
                    if (layer.id === id && col._indexes && col._indexes[f.column] &&
                        (f.mode === 'equals' || f.mode === 'in' || f.mode === 'flag')) normalized._indexed = true;
                    if (missing) normalized._missing = true;
                    canonical.set(self._queryValueKey(normalized), normalized);
                });
            });
            var keys = Array.from(canonical.keys()).sort();
            return {sourceId: id, col: col, key: JSON.stringify(keys),
                filters: keys.map(function(key) { return canonical.get(key); })};
        },

        getFilteredExcluding: function(dsId, columnsToIgnore) {
            var ignored = {};
            var cols = columnsToIgnore || [];
            for (var i = 0; i < cols.length; i++) ignored[String(cols[i])] = true;
            var state = this._state[dsId] || {};
            var filteredState = {};
            for (var fid in state) {
                var f = state[fid];
                if (!ignored[String(f.column)]) filteredState[fid] = f;
            }
            if (this._scopes[dsId]) {
                // Scoped child: parent's own filters still apply in full; only the
                // child's filters listed in columnsToIgnore are dropped.
                var parentRows = this.getFiltered(this._scopes[dsId].parent);
                return this._filterRowObjects(parentRows, filteredState);
            }
            return this._filterWithFilters(dsId, filteredState);
        },

        getFiltered: function(dsId) {
            if (this._cache[dsId]) return this._cache[dsId];
            var _tGf = (!this._firstFilterMeasured) ? performance.now() : 0;
            var result;
            if (this._liveDatasets[dsId]) {
                // Live dataset: the server already applied the predicates.
                // Return the loaded rows verbatim — snapshot at first, then
                // each live payload as setLiveRows() lands it.
                var liveCol = this._columnar[dsId];
                result = liveCol ? this._materializeAll(liveCol) : [];
                this._cache[dsId] = result;
                return result;
            }
            if (this._scopes[dsId]) {
                // Scoped child: start from parent's filtered rows, apply the
                // child's own filters. No columnar storage is used — rows are
                // materialized once per cache miss.
                var parentRows = this.getFiltered(this._scopes[dsId].parent);
                result = this._filterRowObjects(parentRows, this._state[dsId] || {});
            } else {
                result = this._filterWithFilters(dsId, this._state[dsId] || {});
            }
            this._cache[dsId] = result;
            if (!this._firstFilterMeasured) {
                this._firstFilterMeasured = true;
                window._fwTimings = window._fwTimings || {};
                window._fwTimings.first_getFiltered_ms = Math.round(performance.now() - _tGf);
            }
            return result;
        },

        _filterRowObjects: function(rows, filters) {
            // Filter pre-materialized row objects (used for scoped children that
            // inherit from a parent's filtered output). Mirrors _rowMatches but
            // reads named keys instead of indexed columnar values.
            var fKeys = Object.keys(filters);
            if (fKeys.length === 0) return rows.slice();
            var result = [];
            for (var i = 0; i < rows.length; i++) {
                var row = rows[i];
                var match = true;
                for (var k = 0; k < fKeys.length; k++) {
                    var f = filters[fKeys[k]];
                    var val = row[f.column];
                    var sval = String(val != null ? val : '');
                    if (f.mode === 'equals') {
                        if (f.value !== '__all__' && sval !== f.value) { match = false; break; }
                    } else if (f.mode === 'flag') {
                        if (f.value === 'exclude' && sval === f.flagValue) { match = false; break; }
                        if (f.value === 'only' && sval !== f.flagValue) { match = false; break; }
                    } else if (f.mode === 'in') {
                        if (f.value && f.value.length > 0 && f.value.indexOf(sval) === -1) { match = false; break; }
                    } else if (f.mode === 'range') {
                        if (f.value && f.value.min && sval < f.value.min) { match = false; break; }
                        if (f.value && f.value.max && sval > f.value.max) { match = false; break; }
                    } else if (f.mode === 'numrange') {
                        var b = f.value || {};
                        if (b.min != null || b.max != null) {
                            if (val === null || val === undefined) { match = false; break; }
                            var nval = Number(val);
                            if (b.min != null && nval < Number(b.min)) { match = false; break; }
                            if (b.max != null && nval > Number(b.max)) { match = false; break; }
                        }
                    }
                }
                if (match) result.push(row);
            }
            return result;
        },

        _filterWithFilters: function(dsId, filters) {
            var col = this._columnar[dsId];
            if (!col) return [];
            return this._getFilteredColumnar(dsId, col, filters);
        },

        _getFilteredColumnar: function(dsId, col, filters) {
            filters = filters || this._state[dsId] || {};
            if (Object.keys(filters).length === 0) return this._materializeAll(col);
            var indices = this._matchingIndices(this._filterQuery(dsId, filters));
            return this._materializeIndices(col, indices);
        },

        // null denotes all source positions; other results are ascending row
        // indices. Predicate edits select a canonical key, while replacing
        // storage or building dictionary indexes resets the source's cache.
        _matchingIndices: function(query) {
            var col = query.col;
            if (!col) return [];
            var cache = col._queryCache;
            if (cache.has(query.key)) {
                var cached = cache.get(query.key);
                cache.delete(query.key);
                cache.set(query.key, cached);
                return cached;
            }
            var indices = query.filters.length ? this._scanIndices(col, query.filters) : null;
            cache.set(query.key, indices);
            col._queryCacheRows += indices === null ? 0 : indices.length;
            // Bound both metadata and retained row positions per source. At
            // most four full source vectors (or 64k positions for tiny data).
            var rowLimit = Math.max(65536, col._data.length * 4);
            while (cache.size > 16 || col._queryCacheRows > rowLimit) {
                var oldest = cache.keys().next().value;
                var old = cache.get(oldest);
                col._queryCacheRows -= old === null ? 0 : old.length;
                cache.delete(oldest);
            }
            return indices;
        },

        _scanIndices: function(col, filters) {
            var data = col._data, dict = col._dict || {}, colMap = col._colMap;
            var indexes = col._indexes || {}, n = data.length;
            var candidates = null, remaining = [];
            for (var fi = 0; fi < filters.length; fi++) {
                var f = filters[fi], idx = f._indexed && indexes[f.column], matched = null;
                if (idx && f.mode === 'equals') {
                    matched = new Set(idx[f.value] || []);
                } else if (idx && f.mode === 'in') {
                    matched = new Set();
                    for (var vi = 0; vi < f.value.length; vi++) {
                        var bucket = idx[f.value[vi]];
                        if (bucket) for (var bi = 0; bi < bucket.length; bi++) matched.add(bucket[bi]);
                    }
                } else if (idx && f.mode === 'flag') {
                    if (f.value === 'only') matched = new Set(idx[f.flagValue] || []);
                    else if (f.value === 'exclude') {
                        var excluded = new Set(idx[f.flagValue] || []);
                        matched = new Set();
                        for (var ei = 0; ei < n; ei++) if (!excluded.has(ei)) matched.add(ei);
                    }
                }
                if (matched !== null) {
                    if (candidates === null) candidates = matched;
                    else {
                        var inter = new Set();
                        matched.forEach(function(ri) { if (candidates.has(ri)) inter.add(ri); });
                        candidates = inter;
                    }
                } else remaining.push(f);
            }
            var result = [];
            if (candidates === null) {
                for (var si = 0; si < n; si++) {
                    if (this._rowMatches(data[si], remaining, colMap, dict)) result.push(si);
                }
            } else {
                var self = this;
                candidates.forEach(function(ri) {
                    if (!remaining.length || self._rowMatches(data[ri], remaining, colMap, dict)) result.push(ri);
                });
                result.sort(function(a, b) { return a - b; });
            }
            return result;
        },

        getDistinctValues: function(dsId, targetColumn, options) {
            var query = this._filterQuery(dsId, this._localFiltersExcluding(dsId,
                options && options.excludingColumns));
            var col = query.col;
            if (!col || col._colMap[targetColumn] === undefined) return [];
            var indices = this._matchingIndices(query), ci = col._colMap[targetColumn];
            var vocab = (col._dict || {})[targetColumn], seen = new Set();
            var count = indices === null ? col._data.length : indices.length;
            for (var i = 0; i < count; i++) {
                var value = col._data[indices === null ? i : indices[i]][ci];
                if (vocab) value = vocab[value];
                if (value != null && value !== '') seen.add(String(value));
            }
            return Array.from(seen).sort();
        },

        _rowMatches: function(row, filters, colMap, dict) {
            for (var k = 0; k < filters.length; k++) {
                var f = filters[k];
                var ci = colMap[f.column];
                if (ci === undefined && !f._missing) continue;
                var val = row[ci];
                if (dict[f.column]) val = dict[f.column][val];
                var sval = String(val != null ? val : '');
                if (f.mode === 'equals') {
                    if (f.value !== '__all__' && sval !== f.value) return false;
                } else if (f.mode === 'flag') {
                    if (f.value === 'exclude' && sval === f.flagValue) return false;
                    if (f.value === 'only' && sval !== f.flagValue) return false;
                } else if (f.mode === 'in') {
                    if (f.value && f.value.length > 0 && f.value.indexOf(sval) === -1) return false;
                } else if (f.mode === 'range') {
                    if (f.value && f.value.min && sval < f.value.min) return false;
                    if (f.value && f.value.max && sval > f.value.max) return false;
                } else if (f.mode === 'numrange') {
                    var b = f.value || {};
                    if (b.min != null || b.max != null) {
                        if (val === null || val === undefined) return false;
                        var nval = Number(val);
                        if (b.min != null && nval < Number(b.min)) return false;
                        if (b.max != null && nval > Number(b.max)) return false;
                    }
                }
            }
            return true;
        },

        _materializeAll: function(col) {
            if (col._allRows) return col._allRows;
            var cols = col._cols, data = col._data, dict = col._dict || {};
            var dictCols = [];
            for (var ci = 0; ci < cols.length; ci++) {
                if (dict[cols[ci]]) dictCols.push({ idx: ci, vocab: dict[cols[ci]] });
            }
            var rows = new Array(data.length);
            for (var i = 0; i < data.length; i++) {
                var obj = {}, r = data[i];
                for (var j = 0; j < cols.length; j++) obj[cols[j]] = r[j];
                for (var d = 0; d < dictCols.length; d++) {
                    var dc = dictCols[d];
                    obj[cols[dc.idx]] = dc.vocab[r[dc.idx]];
                }
                rows[i] = obj;
            }
            col._allRows = rows;
            return rows;
        },

        _materializeIndices: function(col, indices) {
            var cols = col._cols, data = col._data, dict = col._dict || {};
            var dictCols = [];
            for (var ci = 0; ci < cols.length; ci++) {
                if (dict[cols[ci]]) dictCols.push({ idx: ci, vocab: dict[cols[ci]] });
            }
            var count = indices === null ? data.length : indices.length;
            var result = new Array(count);
            for (var ri = 0; ri < count; ri++) {
                var obj = {}, r = data[indices === null ? ri : indices[ri]];
                for (var j = 0; j < cols.length; j++) obj[cols[j]] = r[j];
                for (var d = 0; d < dictCols.length; d++) {
                    var dc = dictCols[d];
                    obj[cols[dc.idx]] = dc.vocab[r[dc.idx]];
                }
                result[ri] = obj;
            }
            return result;
        },

        subscribe: function(dsId, id, fn) {
            if (!this._subscribers[dsId]) this._subscribers[dsId] = [];
            var entry = {id: id, fn: fn};
            this._subscribers[dsId].push(entry);
            var engine = this;
            return function() {
                var rows = engine._subscribers[dsId] || [];
                var index = rows.indexOf(entry);
                if (index !== -1) rows.splice(index, 1);
            };
        },

        notify: function(dsId) {
            if (this._managed(dsId)) {
                this._queue(dsId, false, false);
                this._flush();
                return;
            }
            // Preserve recursive subscriber ordering for unmanaged callers.
            var filtered = this.getFiltered(dsId);
            var subs = this._subscribers[dsId] || [];
            for (var i = 0; i < subs.length; i++) subs[i].fn(filtered);
            // Cascade: any scoped child of this DS re-derives, so notify them.
            for (var sid in this._scopes) {
                if (this._scopes[sid] && this._scopes[sid].parent === dsId) {
                    this._cache[sid] = null;
                    var sFiltered = this.getFiltered(sid);
                    var sSubs = this._subscribers[sid] || [];
                    for (var j = 0; j < sSubs.length; j++) sSubs[j].fn(sFiltered);
                }
            }
        },

        addScopedChild: function(childId, parentId) {
            // Registers a virtual dataset that derives its rows from parentId's
            // currently-filtered output. The child can have its own FilterBar;
            // its filters AND with the parent's filters automatically.
            // No data duplication — rows are materialized lazily in getFiltered.
            this._scopes[childId] = { parent: parentId };
            if (!this._state[childId]) this._state[childId] = {};
            if (!this._subscribers[childId]) this._subscribers[childId] = [];
            this._cache[childId] = null;
            // If the child is read before any filter change, it should already
            // reflect the parent's initial state — notify() above handles change
            // propagation; for the initial render we rely on getFiltered() being
            // pulled directly by the consuming components.
            this._emitLifecycle('scope', childId);
        },

        onReady: function(dsId, fn) {
            // Fires fn() once dsId is initialized. Replaces the fragile
            //   var fe = window._fwFilterEngine;
            //   if (!fe) { setTimeout(init, 100); return; }
            // polling pattern found in old custom RawHTML.
            var self = this;
            if (this.isReady(dsId)) { fn(); return function() {}; }
            var elapsed = 0;
            var handle = setInterval(function() {
                elapsed += 50;
                if (self.isReady(dsId)) { clearInterval(handle); fn(); }
                else if (elapsed >= 5000) { clearInterval(handle); }
            }, 50);
            return function() { clearInterval(handle); };
        }
    };
