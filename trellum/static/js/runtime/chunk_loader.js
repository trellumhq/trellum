    // ── Chunk Loader ─────────────────────────────────────
    // Lazy-loads older time-period chunks when the user expands the
    // date range beyond what was embedded inline in data.json.
    window._fwChunkLoader = {
        _baseRows: {},    // dsId → decoded plain rows from initial inline data
        _chunkRows: {},   // dsId → { period: [rows...] }
        _loaded: {},      // dsId → Set of loaded period keys
        _fetching: {},    // dsId → { period: Promise }
        _manifests: {},   // dsId → manifest (stored so initColumnar hook can access it)
        _pendingCount: 0,
        _merging: false,    // guard: prevents _onDataSourceReinit re-entring from _mergeAndReinit

        init: function(reportData) {
            var manifests = reportData._chunk_manifests;
            if (!manifests) return;
            var self = this;
            self._manifests = manifests;

            Object.keys(manifests).forEach(function(dsId) {
                var manifest = manifests[dsId];
                self._loaded[dsId] = new Set(manifest.loaded_periods || []);
                self._chunkRows[dsId] = {};
                self._fetching[dsId] = {};
                var col = window._fwFilterEngine._columnar[dsId];
                if (col) {
                    self._baseRows[dsId] = window._fwFilterEngine._materializeAll(col).slice();
                }
                window._fwFilterEngine.subscribe(dsId, '_fw_chunk_' + dsId, function() {
                    self._onFilterChange(dsId, manifest);
                });
                // Fire immediately for DataSources that are already initialized
                // (default scope on page load, or reload with URL state already restored).
                // Non-default scope DataSources aren't loaded yet — their first
                // _onFilterChange fires via the subscription when the FilterBar restores
                // URL state on scope switch.
                if (window._fwFilterEngine.isReady(dsId)) {
                    self._onFilterChange(dsId, manifest);
                }
            });

            // Intercept initColumnar so we know when a scope switch re-initializes
            // a DataSource with fresh 3-month inline data. Without this, the scope
            // switch overwrites the merged full-history data the chunk loader built.
            var origInitColumnar = window._fwFilterEngine.initColumnar.bind(window._fwFilterEngine);
            window._fwFilterEngine.initColumnar = function(dsId, col) {
                origInitColumnar(dsId, col);
                if (!self._merging && self._manifests[dsId]) {
                    self._onDataSourceReinit(dsId);
                }
            };
        },

        // Called whenever a chunked DataSource is (re-)initialized via initColumnar —
        // both on the initial page load (for non-default scopes via scope switch) and
        // every time renderAll cycles back through a scope.
        _onDataSourceReinit: function(dsId) {
            // Snapshot the freshly-loaded inline rows as the new base.
            var col = window._fwFilterEngine._columnar[dsId];
            if (col) {
                this._baseRows[dsId] = window._fwFilterEngine._materializeAll(col).slice();
            }
            // If chunks were already fetched for this DataSource (e.g. user had
            // previously expanded the date range on this scope), merge them back
            // immediately so the scope shows full data without re-fetching.
            if (Object.keys(this._chunkRows[dsId] || {}).length > 0) {
                this._mergeAndReinit(dsId);
                // Do not call _onFilterChange here — _mergeAndReinit + notify will
                // drive the subscription, and the FilterBar URL-restore fires it again.
                return;
            }
            // No chunks loaded yet. The FilterBar will fire _onFilterChange through
            // the subscription once it restores URL state, so nothing extra to do here.
        },

        _onFilterChange: function(dsId, manifest) {
            var state = window._fwFilterEngine.getFilterState(dsId);
            var rangeFilter = null;
            var keys = Object.keys(state);
            for (var i = 0; i < keys.length; i++) {
                if (state[keys[i]].mode === 'range') { rangeFilter = state[keys[i]]; break; }
            }
            var chunks = manifest.chunks;
            var missing = [];
            if (!rangeFilter || !rangeFilter.value || !rangeFilter.value.min) {
                // No active date range filter = ALL selected — queue every un-loaded chunk
                Object.keys(chunks).forEach(function(p) {
                    if (!this._loaded[dsId].has(p)) missing.push(p);
                }, this);
            } else {
                var minDate = rangeFilter.value.min;
                var maxDate = rangeFilter.value.max;
                Object.keys(chunks).forEach(function(p) {
                    if (chunks[p].max >= minDate && chunks[p].min <= maxDate
                            && !this._loaded[dsId].has(p)) {
                        missing.push(p);
                    }
                }, this);
            }
            if (!missing.length) return;
            this._fetchPeriods(dsId, missing, manifest);
        },

        _fetchPeriods: function(dsId, periods, manifest) {
            var self = this;
            self._pendingCount++;
            self._showBanner(periods.length);
            var promises = periods.map(function(p) {
                if (self._fetching[dsId][p]) return self._fetching[dsId][p];
                var file = manifest.chunks[p].file;
                var prom = fetch(file + '?v=' + encodeURIComponent(window._dataVersion || ''))
                    .then(function(r) { return r.json(); })
                    .then(function(d) {
                        var raw = d['_ds_' + dsId];
                        if (!raw) return;
                        self._chunkRows[dsId][p] = self._decodeChunk(raw);
                        self._loaded[dsId].add(p);
                    })
                    .catch(function(e) { console.warn('[fw chunk] Failed:', file, e); });
                self._fetching[dsId][p] = prom;
                return prom;
            });
            Promise.all(promises).then(function() {
                self._pendingCount--;
                self._mergeAndReinit(dsId);
                if (!self._pendingCount) self._hideBanner();
            }).catch(function() {
                self._pendingCount--;
                if (!self._pendingCount) self._hideBanner();
            });
        },

        _showBanner: function(count) {
            var el = document.getElementById('fwChunkBanner');
            if (!el) return;
            var txt = document.getElementById('fwChunkBannerText');
            if (txt) txt.textContent = 'Loading ' + count + ' older period' + (count !== 1 ? 's' : '') + '...';
            el.classList.add('visible');
        },

        _hideBanner: function() {
            var el = document.getElementById('fwChunkBanner');
            if (!el) return;
            el.classList.remove('visible');
        },

        _decodeChunk: function(raw) {
            var cols = raw._cols, data = raw._data, dict = raw._dict || {};
            var rows = new Array(data.length);
            for (var i = 0; i < data.length; i++) {
                var obj = {}, r = data[i];
                for (var j = 0; j < cols.length; j++) {
                    var v = r[j];
                    if (dict[cols[j]]) v = dict[cols[j]][v];
                    obj[cols[j]] = v;
                }
                rows[i] = obj;
            }
            return rows;
        },

        _mergeAndReinit: function(dsId) {
            var baseRows = this._baseRows[dsId] || [];
            var allRows = baseRows.slice();
            var chunkMap = this._chunkRows[dsId] || {};
            Object.keys(chunkMap).forEach(function(p) {
                var cr = chunkMap[p];
                for (var i = 0; i < cr.length; i++) allRows.push(cr[i]);
            });
            // Sort by first column (event_date) so charts maintain time order
            var col = window._fwFilterEngine._columnar[dsId];
            var firstCol = col && col._cols && col._cols[0];
            if (firstCol) {
                allRows.sort(function(a, b) {
                    return a[firstCol] < b[firstCol] ? -1 : a[firstCol] > b[firstCol] ? 1 : 0;
                });
            }
            // Guard: prevent the patched initColumnar from re-entering _onDataSourceReinit
            // while we're in the middle of merging.
            this._merging = true;
            window._fwFilterEngine.init(dsId, allRows);
            this._merging = false;
            window._fwFilterEngine.notify(dsId);
        }
    };
