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
                this._cache[dsId] = null;
                // A live dataset must RE-QUERY when a filter is cleared, exactly
                // as _commitFilter does when one is set — otherwise deselecting
                // (or clearing a date range) only re-fans the stale loaded rows
                // and the report never updates. See _commitFilter's live branch.
                var liveHooks = this._liveDatasets[dsId];
                if (liveHooks) { liveHooks.onChange(); return; }
                this.notify(dsId);
            }
        },

        clearAllFilters: function(dsId) {
            if (this._state[dsId]) {
                this._state[dsId] = {};
                this._cache[dsId] = null;
                var liveHooks = this._liveDatasets[dsId];
                if (liveHooks) { liveHooks.onChange(); return; }
                this.notify(dsId);
            }
        },

        removeDataset: function(dsId) {
            delete this._columnar[dsId];
            delete this._datasets[dsId];
            delete this._state[dsId];
            delete this._cache[dsId];
            delete this._subscribers[dsId];
            delete this._scopes[dsId];
            delete this._liveDatasets[dsId];
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
            this._columnar[dsId] = col;
            this._datasets[dsId] = null;
            this._cache[dsId] = null;
            if (!this._state[dsId]) this._state[dsId] = {};
            if (!this._subscribers[dsId]) this._subscribers[dsId] = [];
            var self = this;
            var n = (col._data || []).length;
            if (n < 10000) {
                self._buildColumnarIndexes(col);
            } else {
                (window.requestIdleCallback || setTimeout)(function() {
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
                return;
            }
            if (!(col._cols && col._data)) col = this._rowsToColumnar(col);
            this._setColumnarDataset(dsId, col);
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
            var liveHooks = this._liveDatasets[dsId];
            if (liveHooks) {
                // The server applies these predicates; re-filtering the
                // currently-loaded rows locally would silently pretend a
                // fetch had happened. A transient (mid-drag) tick never
                // queries — only a real commit does.
                if (!isTransient) liveHooks.onChange();
                return;
            }
            this._cache[dsId] = null;
            this.notify(dsId);
        },

        // Registers dsId as server-backed: setFilter routes to hooks.onChange()
        // instead of local re-filtering, and getFiltered() returns the loaded
        // rows verbatim (the server already applied the predicates).
        registerLiveDataset: function(dsId, hooks) {
            this._liveDatasets[dsId] = hooks;
            if (!this._state[dsId]) this._state[dsId] = {};
            if (!this._subscribers[dsId]) this._subscribers[dsId] = [];
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
            var fKeys = Object.keys(filters);
            var data = col._data;
            var dict = col._dict || {};
            var colMap = col._colMap;
            var indexes = col._indexes || {};
            var n = data.length;

            if (fKeys.length === 0) return this._materializeAll(col);

            var candidates = null;
            var remaining = [];

            for (var fi = 0; fi < fKeys.length; fi++) {
                var f = filters[fKeys[fi]];
                var idx = indexes[f.column];
                var matched = null;

                if (idx && f.mode === 'equals' && f.value !== '__all__') {
                    matched = new Set(idx[f.value] || []);
                } else if (idx && f.mode === 'in' && f.value && f.value.length > 0) {
                    matched = new Set();
                    for (var vi = 0; vi < f.value.length; vi++) {
                        var bucket = idx[f.value[vi]];
                        if (bucket) for (var bi = 0; bi < bucket.length; bi++) matched.add(bucket[bi]);
                    }
                } else if (idx && f.mode === 'flag') {
                    if (f.value === 'exclude') {
                        var excluded = new Set(idx[f.flagValue] || []);
                        matched = new Set();
                        for (var ei = 0; ei < n; ei++) { if (!excluded.has(ei)) matched.add(ei); }
                    } else if (f.value === 'only') {
                        matched = new Set(idx[f.flagValue] || []);
                    }
                }

                if (matched !== null) {
                    if (candidates === null) {
                        candidates = matched;
                    } else {
                        var inter = new Set();
                        matched.forEach(function(ri) { if (candidates.has(ri)) inter.add(ri); });
                        candidates = inter;
                    }
                } else {
                    remaining.push(f);
                }
            }

            var resultIdx = [];
            if (candidates === null) {
                for (var si = 0; si < n; si++) {
                    if (this._rowMatches(data[si], remaining, colMap, dict)) resultIdx.push(si);
                }
            } else if (remaining.length > 0) {
                var self = this;
                candidates.forEach(function(ri) {
                    if (self._rowMatches(data[ri], remaining, colMap, dict)) resultIdx.push(ri);
                });
                resultIdx.sort(function(a, b) { return a - b; });
            } else {
                candidates.forEach(function(ri) { resultIdx.push(ri); });
                resultIdx.sort(function(a, b) { return a - b; });
            }

            return this._materializeIndices(col, resultIdx);
        },

        _rowMatches: function(row, filters, colMap, dict) {
            for (var k = 0; k < filters.length; k++) {
                var f = filters[k];
                var ci = colMap[f.column];
                if (ci === undefined) continue;
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
            var result = new Array(indices.length);
            for (var ri = 0; ri < indices.length; ri++) {
                var obj = {}, r = data[indices[ri]];
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
            this._subscribers[dsId].push({id: id, fn: fn});
        },

        notify: function(dsId) {
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
        },

        onReady: function(dsId, fn) {
            // Fires fn() once dsId is initialized. Replaces the fragile
            //   var fe = window._fwFilterEngine;
            //   if (!fe) { setTimeout(init, 100); return; }
            // polling pattern found in old custom RawHTML.
            var self = this;
            if (this.isReady(dsId)) { fn(); return; }
            var elapsed = 0;
            var handle = setInterval(function() {
                elapsed += 50;
                if (self.isReady(dsId)) { clearInterval(handle); fn(); }
                else if (elapsed >= 5000) { clearInterval(handle); }
            }, 50);
        }
    };
