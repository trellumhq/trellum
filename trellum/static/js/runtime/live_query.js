    // ── Live query binder ────────────────────────────────
    // A live query is a filter-engine DATASET (see LiveDataSource /
    // FilterBar in trellum.components.filterable), not a bespoke control.
    // This module is the piece that makes that true at runtime: it maps
    // engine filter state -> declared query params, debounces + coalesces
    // + sequence-guards the network, POSTs the unchanged {query_id, params}
    // contract, and feeds the response back into the engine so every
    // subscribed component (chart/table/KPI row) redraws through its
    // normal dataset_id path. No component knows any of this happened.
    //
    // Arms only when the page has a host (window._fwHasHost), that host
    // advertised an endpoint (window._fwLiveQueryUrl), AND this isn't an
    // anonymous share-link view (window._fwShareLink, stamped server-side
    // by the share routes -- see apps/reports/views.py _share_head_extra).
    // Standalone -- no host, an email snapshot, an old host, or a share
    // link -- nothing here ever fires and the page shows exactly the
    // build-time snapshot. Commits are explicit for text (Enter/blur) and
    // transient for a dragging slider (see setFilterTransient); every
    // other control auto-queries on change, which is just what setFilter
    // already means for a dropdown/toggle/date preset.
    window._fwLiveQuery = (function() {
        var _url = (typeof window._fwLiveQueryUrl === 'string')
            ? window._fwLiveQueryUrl : '';
        var _shareLink = !!window._fwShareLink;
        var _armed = !!(window._fwHasHost && _url && !_shareLink);

        var SHARE_NOTE = "Live lookup isn't available on shared reports";
        var DEBOUNCE_MS = 250;
        // Pacing heuristic only, NOT a security boundary -- the server is
        // the enforcement point and now reads a per-org configurable limit
        // (apps.reports.livequery / OrgLiveQueryPolicy) rather than this
        // fixed constant. The client cannot know that per-org value without
        // an extra round trip, so it paces against the historical default;
        // when N live datasets share a page their combined request rate is
        // divided by N, so a shared filter change across several linked
        // queries doesn't race the full budget from every one of them at
        // once. The server's 429 + Retry-After remains authoritative
        // regardless of how well this guess lines up.
        var CLIENT_BUDGET_PER_MINUTE = 30;

        var _registry = Object.create(null);  // dsId -> binder state (see registerDataset)
        var _appliedRevision = 0;
        var _generation = 0;
        // Initial snapshots retain capture revision zero. Keep only numeric
        // history after disposal so a returning dataset cannot rewind revision.
        var _revisions = Object.create(null);
        var _listeners = Object.create(null);
        var _suspended = false;
        var _ownerObserver = null;
        var REQUEST_DEADLINE_MS = 30000;

        function _cookie(name) {
            var m = document.cookie.match(
                new RegExp('(?:^|; )' + name + '=([^;]*)'));
            return m ? decodeURIComponent(m[1]) : '';
        }

        function _datasetCount() {
            var n = 0;
            for (var k in _registry) if (Object.prototype.hasOwnProperty.call(_registry, k)) n++;
            return n || 1;
        }

        // Sliding-window budget, per dataset: at most (budget / N) requests
        // in any trailing 60s window. A window (not a fixed minimum gap
        // between every pair of requests) so ordinary bursty interaction --
        // a viewer clicking through a couple of filters in quick succession
        // -- is never held up; the throttle only engages once a dataset has
        // genuinely used its whole per-minute share, which is exactly the
        // sustained-hammering case this exists to soften.
        var _requestTimestamps = {};  // dsId -> [ms, ...] recent request times

        function _budgetFor(dsId) {
            var perDataset = CLIENT_BUDGET_PER_MINUTE / _datasetCount();
            return Math.max(1, Math.floor(perDataset));
        }

        function _recentRequests(dsId) {
            var now = Date.now();
            var arr = (_requestTimestamps[dsId] || []).filter(function(t) {
                return now - t < 60000;
            });
            _requestTimestamps[dsId] = arr;
            return arr;
        }

        function _withinBudget(dsId) {
            return _recentRequests(dsId).length < _budgetFor(dsId);
        }

        function _recordRequest(dsId) {
            _recentRequests(dsId).push(Date.now());
        }

        // Public state contains data identity only. Never expose the binder,
        // raw payload, owner DOM, configuration or transport to consumers.
        function _copy(value) { return JSON.parse(JSON.stringify(value)); }
        function getDatasetState(dsId) {
            var entry = _registry[dsId];
            return entry ? _copy(entry.state) : null;
        }
        function _deliver(callback, state) {
            try { callback(state === null ? null : _copy(state)); }
            catch (err) { console.warn('Live result subscriber failed:', err); }
        }
        function _publish(entry) {
            var encoded = JSON.stringify(entry.state);
            if (encoded === entry.published) return;
            entry.published = encoded;
            (_listeners[entry.id] || []).slice().forEach(function(listener) {
                if (listener.active) _deliver(listener.callback, entry.state);
            });
        }
        function subscribeDataset(dsId, callback) {
            var listener = {callback: callback, active: true};
            var list = _listeners[dsId] || (_listeners[dsId] = []);
            list.push(listener);
            _deliver(callback, getDatasetState(dsId));
            return function() {
                listener.active = false;
                var index = list.indexOf(listener);
                if (index >= 0) list.splice(index, 1);
                if (!list.length && _listeners[dsId] === list) delete _listeners[dsId];
            };
        }
        function _validRows(columns, rows) {
            return Array.isArray(columns) && columns.every(function(c) { return typeof c === 'string'; }) &&
                Array.isArray(rows) && rows.every(function(row) {
                    return Array.isArray(row) && row.length === columns.length;
                });
        }
        function _validSnapshot(raw) {
            if (!raw) return false;
            if (Array.isArray(raw)) return raw.every(function(row) {
                return row && typeof row === 'object' && !Array.isArray(row);
            });
            return _validRows(raw._cols, raw._data) && (!raw._dict ||
                (typeof raw._dict === 'object' && !Array.isArray(raw._dict) &&
                Object.keys(raw._dict).every(function(key) { return Array.isArray(raw._dict[key]); })));
        }
        function _cancelWork(entry) {
            clearTimeout(entry.debounceTimer);
            entry.debounceTimer = null;
            var work = entry.work;
            entry.work = null;  // retire before abort can reject a promise
            if (work) {
                clearTimeout(work.deadline);
                clearTimeout(work.retryTimer);
                if (work.controller) work.controller.abort();
            }
        }
        function _dispose(entry) {
            if (_registry[entry.id] !== entry) return;
            _cancelWork(entry);
            delete _registry[entry.id];
            window._fwFilterEngine.unregisterLiveDataset(entry.id, entry.hooks);
            var listeners = _listeners[entry.id] || [];
            delete _listeners[entry.id];
            listeners.slice().forEach(function(listener) {
                if (listener.active) _deliver(listener.callback, null);
                listener.active = false;
            });
            if (_ownerObserver && !Object.keys(_registry).length) {
                _ownerObserver.disconnect();
                _ownerObserver = null;
            }
        }
        function _observeOwners() {
            if (_ownerObserver || typeof MutationObserver === 'undefined') return;
            _ownerObserver = new MutationObserver(function() {
                Object.keys(_registry).forEach(function(id) {
                    var entry = _registry[id];
                    if (entry.owner && !entry.owner.isConnected) _dispose(entry);
                });
            });
            _ownerObserver.observe(document.body, {childList: true, subtree: true});
        }

        // The registrar delegates snapshot initialization atomically. Same raw
        // artifact + report object + root + public config means the same source;
        // theme/render re-entry must not overwrite already queried rows.
        // Legacy two-argument registration adopts existing engine data, with
        // unknown displayed parameters (no raw snapshot ownership was supplied).
        function registerDataset(dsId, liveCfg, source) {
            // Scope rendering can reuse the hidden root for another dataset.
            // Retire that root's old binder before admitting the new owner.
            var replacedOwner = false;
            if (source && source.owner) Object.keys(_registry).forEach(function(id) {
                var other = _registry[id];
                if (id !== dsId && other.owner === source.owner) {
                    replacedOwner = true;
                    _dispose(other);
                }
            });
            var previous = _registry[dsId];
            var signature = JSON.stringify(liveCfg);
            if (previous && previous.signature === signature &&
                ((!source && !previous.source) || (source && previous.source &&
                previous.source.raw === source.raw && previous.source.report === source.report &&
                previous.owner === source.owner))) return;
            if (previous) _cancelWork(previous);
            var schema = liveCfg.params || [];
            var suppliedDefaults = liveCfg.defaults || {};
            var defaults = {};
            for (var i = 0; i < schema.length; i++) {
                var name = schema[i].name;
                if (Object.prototype.hasOwnProperty.call(suppliedDefaults, name)) {
                    defaults[name] = suppliedDefaults[name];
                }
            }
            defaults = _copy(defaults);
            var snapshotParamsKnown = schema.every(function(param) {
                return Object.prototype.hasOwnProperty.call(defaults, param.name);
            });
            var engine = window._fwFilterEngine;
            var valid = source ? _validSnapshot(source.raw) : engine.isReady(dsId);
            var revision = Object.prototype.hasOwnProperty.call(_revisions, dsId) ? _revisions[dsId] : 0;
            if (valid && (replacedOwner || Object.prototype.hasOwnProperty.call(_revisions, dsId))) revision = ++_appliedRevision;
            _revisions[dsId] = revision;
            var entry = {
                id: dsId, cfg: _copy(liveCfg), signature: signature,
                defaults: _copy(defaults), source: source, owner: source ? source.owner : null,
                seq: 0, work: null, debounceTimer: null, bar: previous ? previous.bar : null,
                state: {
                    dataset_id: dsId, generation: ++_generation,
                    revision: revision,
                    phase: valid ? 'ready' : 'unavailable', origin: valid ? 'snapshot' : null,
                    requested_params: {}, displayed_params: valid && source && snapshotParamsKnown ? _copy(defaults) : null,
                    has_result: valid,
                    row_count: valid ? (source ? (Array.isArray(source.raw) ? source.raw.length : source.raw._data.length)
                        : engine.getDatasetRowCount(dsId)) : null,
                    truncated: null, completed_at: null, error: null
                }
            };
            _registry[dsId] = entry;
            entry.hooks = {onChange: function() {
                if (_registry[dsId] === entry) _schedule(dsId);
            }, onDispose: function() { _dispose(entry); }};
            engine.registerLiveDataset(dsId, entry.hooks);
            if (source) engine.initColumnar(dsId, valid ? source.raw : {_cols: [], _data: [], _dict: {}});
            entry.state.requested_params = _copy(_collect(dsId));
            _paint(entry);
            _publish(entry);
            if (source) engine.notify(dsId);
            if (entry.owner) _observeOwners();
            if (source && _armed && (!valid || !entry.state.displayed_params ||
                !_sameParams(entry.state.requested_params, entry.state.displayed_params))) _schedule(dsId);
        }

        // ── FilterBar attachment ──────────────────────────
        // Called once by filter_bar.js's renderer for a FilterBar whose
        // dataset_id is live. Wires the note/status/chip DOM and decides
        // whether this bar arms at all.
        function attachFilterBar(dsId, barEl) {
            var entry = _registry[dsId];
            if (!entry) return false;
            entry.bar = barEl;
            if (!_armed) {
                barEl.setAttribute('data-live-state', 'standalone');
                if (_shareLink) {
                    var note = barEl.querySelector('.fw-live-note');
                    if (note) note.textContent = SHARE_NOTE;
                }
                return false;
            }
            _paint(entry);
            // The bar is server-rendered disabled (bake_disabled() in
            // trellum/components/live_filterable.py injects `disabled` onto
            // every <select>/<input>/<button> tag a filter plugin renders,
            // regardless of which plugin -- toggle/flag's .fw-toggle-btn,
            // date_range's .fw-date-trigger and .fw-date-preset, any future
            // plugin's own buttons) so a standalone page reads as honestly
            // non-interactive even before this runs. Undo that here -- the
            // mirror image of the M1 control's own attach(). Plain `button`
            // (not `button.fw-toggle-btn`): scoping this to one filter
            // type's button class left every OTHER plugin's buttons
            // disabled forever once armed -- the date-range trigger/presets
            // included, which is why that popover would never open.
            var controls = barEl.querySelectorAll('select, input, button');
            for (var i = 0; i < controls.length; i++) controls[i].disabled = false;
            return true;
        }

        function isArmed() { return _armed; }

        // ── Collection: engine filter state -> query params ──
        function _paramSchema(entry) {
            var byName = {};
            (entry.cfg.params || []).forEach(function(p) { byName[p.name] = p; });
            return byName;
        }

        function _coerceScalar(paramDef, raw) {
            if (raw === null || raw === undefined || raw === '') return undefined;
            var type = paramDef ? paramDef.type : 'str';
            if (type === 'int') {
                var n = parseInt(raw, 10);
                return isNaN(n) ? undefined : n;
            }
            if (type === 'float') {
                var f = parseFloat(raw);
                return (isNaN(f) || !isFinite(f)) ? undefined : f;
            }
            if (type === 'date') {
                return String(raw).slice(0, 10);
            }
            var cap = (paramDef && paramDef.max_length) || 200;
            return String(raw).slice(0, cap);
        }

        function _applyBinding(params, b, f, schema) {
            var paramDef = b.param ? schema[b.param] : null;
            switch (b.filter_type) {
            case 'dropdown': {
                var arr = f.value || [];
                if (!arr.length) {
                    if (b.sentinel !== undefined) params[b.param] = b.sentinel;
                    return;
                }
                var v = arr[0];
                params[b.param] = (paramDef && paramDef.type !== 'enum')
                    ? _coerceScalar(paramDef, v) : v;
                break;
            }
            case 'toggle': {
                if (f.value === '__all__') {
                    if (b.sentinel !== undefined) params[b.param] = b.sentinel;
                } else {
                    params[b.param] = f.value;
                }
                break;
            }
            case 'flag': {
                params[b.param] = f.value || 'all';
                break;
            }
            case 'slider': {
                if (f.mode === 'numrange') {
                    var rv = f.value || {};
                    if (b.min_param && rv.min !== undefined && rv.min !== null) {
                        params[b.min_param] = _coerceScalar(schema[b.min_param], rv.min);
                    }
                    if (b.max_param && rv.max !== undefined && rv.max !== null) {
                        params[b.max_param] = _coerceScalar(schema[b.max_param], rv.max);
                    }
                } else if (b.param) {
                    params[b.param] = _coerceScalar(paramDef, f.value);
                }
                break;
            }
            case 'date_range': {
                var dv = f.value || {};
                // Truncated to YYYY-MM-DD regardless of the filter's own
                // hourly display -- the `date` param type is strict ISO
                // dates in v1 (hourly live binding is a documented
                // follow-up, validated against at build time).
                if (b.min_param && dv.min) params[b.min_param] = String(dv.min).slice(0, 10);
                if (b.max_param && dv.max) params[b.max_param] = String(dv.max).slice(0, 10);
                break;
            }
            case 'text': {
                var raw = (f.value === null || f.value === undefined) ? '' : String(f.value).trim();
                if (!raw) {
                    if (b.sentinel !== undefined) params[b.param] = b.sentinel;
                    return;
                }
                var coerced = paramDef ? _coerceScalar(paramDef, raw) : raw;
                if (coerced === undefined) return;  // bad numeric input -- keep prior value
                params[b.param] = coerced;
                break;
            }
            }
        }

        function _collect(dsId) {
            var entry = _registry[dsId];
            var bindings = entry.cfg.bindings || [];
            var schema = _paramSchema(entry);
            var state = window._fwFilterEngine.getFilterState(dsId);
            var byColumn = {};
            // Resolve by COLUMN, not filter id: a propagated filter carries
            // a synthetic prop_... id (filter_bar.js's _propagate), so a
            // binding declared on the target dataset must match by the
            // column it names, not by whose filter id set it.
            for (var fid in state) {
                if (state.hasOwnProperty(fid)) byColumn[state[fid].column] = state[fid];
            }
            var params = {};
            for (var k in entry.defaults) params[k] = entry.defaults[k];
            for (var bi = 0; bi < bindings.length; bi++) {
                var b = bindings[bi];
                var f = byColumn[b.column];
                if (!f) continue;  // never committed -- stays at the declared default
                _applyBinding(params, b, f, schema);
            }
            // Use the exact JSON representation sent on the wire (e.g. an
            // undefined numeric binding is omitted, as it always was in POST).
            return _copy(params);
        }

        function _sameParams(a, b) {
            var ak = Object.keys(a), bk = Object.keys(b);
            if (ak.length !== bk.length) return false;
            for (var i = 0; i < ak.length; i++) {
                if (a[ak[i]] !== b[ak[i]]) return false;
            }
            return true;
        }

        // Keep the existing 250ms coalescing window and per-dataset budget.
        // Committed selection identity changes immediately, before that window.
        function _schedule(dsId, keepCommittedSelection) {
            var entry = _registry[dsId];
            if (!entry) return;
            var params = keepCommittedSelection ? entry.state.requested_params : _collect(dsId);
            var changed = !_sameParams(params, entry.state.requested_params);
            entry.state.requested_params = params;
            if (!_armed || _suspended) {
                if (_armed && _suspended) {
                    entry.resumePending = !entry.state.displayed_params ||
                        !_sameParams(params, entry.state.displayed_params);
                    entry.state.phase = entry.state.has_result ? 'ready' :
                        (entry.state.phase === 'unavailable' ? 'unavailable' : 'idle');
                    entry.state.error = null;
                }
                _publish(entry);
                return;
            }
            if (entry.state.displayed_params && _sameParams(params, entry.state.displayed_params)) {
                _cancelWork(entry);
                entry.state.phase = 'ready';
                entry.state.error = null;
            } else {
                if (changed && entry.work && entry.work.retryTimer) _cancelWork(entry);
                entry.state.phase = 'pending';
                entry.state.error = null;
                if (!entry.work && !entry.debounceTimer) {
                    entry.debounceTimer = setTimeout(function() {
                        entry.debounceTimer = null;
                        if (_registry[dsId] === entry) _fire(entry);
                    }, DEBOUNCE_MS);
                }
            }
            _paint(entry);
            _publish(entry);
        }
        function _fire(entry) {
            if (_registry[entry.id] !== entry || entry.work || _suspended || !_armed) return;
            if (!_withinBudget(entry.id)) {
                entry.debounceTimer = setTimeout(function() {
                    entry.debounceTimer = null;
                    _fire(entry);
                }, 1000);
                return;
            }
            var params = _copy(entry.state.requested_params);
            if (entry.state.displayed_params && _sameParams(params, entry.state.displayed_params)) {
                entry.state.phase = 'ready';
                entry.state.error = null;
                _paint(entry); _publish(entry);
                return;
            }
            var work = {params: params, seq: ++entry.seq, generation: entry.state.generation,
                controller: null, retryTimer: null, deadline: null};
            entry.work = work;
            _send(entry, work, false);
        }

        // ── Status / freshness DOM (lives on the paired FilterBar) ──
        function _statusEl(entry) {
            return entry.bar ? entry.bar.querySelector('.fw-live-status') : null;
        }
        function _status(entry, msg, isError) {
            var el = _statusEl(entry);
            if (!el) return;
            el.textContent = msg || '';
            el.hidden = !msg;
            el.className = 'fw-live-status' + (isError ? ' fw-live-error' : '');
        }
        function _freshness(entry, msg) {
            var el = entry.bar ? entry.bar.querySelector('.fw-live-freshness') : null;
            if (el) el.textContent = msg;
        }

        function _paint(entry) {
            if (!entry.bar) return;
            entry.bar.setAttribute('data-live-state', !_armed ? 'standalone' :
                (entry.state.origin === 'query' ? 'live' : 'ready'));
            if (entry.state.phase === 'pending') {
                if (entry.work && entry.work.retryTimer) _status(entry, 'busy — retrying', true);
                else _status(entry, 'updating…', false);
            }
            else _status(entry, entry.state.error || '', !!entry.state.error);
            _freshness(entry, entry.state.origin === 'query' ?
                'live · just now' + (entry.state.truncated ? ' · truncated' : '') : '');
        }

        // Work retains ownership across headers, asynchronous body parsing and
        // its one initial-429 retry. Retired callbacks cannot release new work.
        function _owns(entry, work) {
            return _registry[entry.id] === entry && entry.work === work &&
                entry.seq === work.seq && entry.state.generation === work.generation &&
                (!entry.owner || entry.owner.isConnected) && !_suspended;
        }
        // requested_params is the last normalized collection committed by the
        // engine hook. Mid-drag slider state is intentionally not a selection
        // commit, including while a response body or debounce is pending.
        function _matches(entry, work) {
            return _owns(entry, work) && _sameParams(work.params, entry.state.requested_params);
        }
        function _finish(entry, work, error) {
            if (!_owns(entry, work)) return;
            clearTimeout(work.deadline);
            clearTimeout(work.retryTimer);
            entry.work = null;
            if (work.controller) work.controller.abort();
            if (!_sameParams(work.params, entry.state.requested_params)) {
                _schedule(entry.id, true);
                return;
            }
            if (error) {
                entry.state.phase = 'error';
                entry.state.error = (error && error.message) || 'Lookup failed';
            }
            _paint(entry); _publish(entry);
        }
        function _send(entry, work, isRetry) {
            if (!_matches(entry, work)) { _finish(entry, work); return; }
            _recordRequest(entry.id);
            work.controller = new AbortController();
            work.deadline = setTimeout(function() {
                if (!_owns(entry, work)) return;
                work.controller.abort();
                _finish(entry, work, new Error('Lookup timed out after 30 seconds'));
            }, REQUEST_DEADLINE_MS);
            var headers = {'Content-Type': 'application/json'};
            var slug = document.body.getAttribute('data-report-slug') || '';
            if (slug) headers['X-Trellum-Report'] = slug;
            var csrf = _cookie('csrftoken');
            if (csrf) headers['X-CSRFToken'] = csrf;
            Promise.resolve().then(function() {
                if (!_matches(entry, work)) return null;
                return fetch(_url, {
                    method: 'POST', credentials: 'same-origin', headers: headers,
                    signal: work.controller.signal,
                    body: JSON.stringify({query_id: entry.cfg.query_id, params: work.params})
                });
            }).then(function(r) {
                if (!r || !_matches(entry, work)) { _finish(entry, work); return; }
                if (r.status === 429 && !isRetry) {
                    clearTimeout(work.deadline);
                    work.deadline = null;
                    work.controller.abort();
                    var ra = parseInt(r.headers.get('Retry-After') || '1', 10);
                    if (isNaN(ra) || ra < 0) ra = 1;
                    _status(entry, 'busy — retrying', true);
                    work.retryTimer = setTimeout(function() {
                        work.retryTimer = null;
                        if (_matches(entry, work)) _send(entry, work, true);
                        else _finish(entry, work);
                    }, Math.min(ra, 30) * 1000);
                    return;
                }
                return r.json()['catch'](function(err) {
                    if (r.ok) throw err;
                    return {};
                }).then(function(data) {
                    if (!_matches(entry, work)) { _finish(entry, work); return; }
                    if (!r.ok) throw new Error((data && typeof data.error === 'string' && data.error) ||
                        ('Lookup failed (HTTP ' + r.status + ')'));
                    if (!data || !_validRows(data.columns, data.rows) ||
                        (data.truncated !== undefined && typeof data.truncated !== 'boolean')) {
                        throw new Error('Lookup returned an invalid result');
                    }
                    // Update identity before notifying the existing data consumers.
                    entry.state.phase = 'ready';
                    entry.state.error = null;
                    entry.state.origin = 'query';
                    entry.state.displayed_params = _copy(work.params);
                    entry.state.has_result = true;
                    entry.state.row_count = data.rows.length;
                    entry.state.truncated = !!data.truncated;
                    entry.state.completed_at = new Date().toISOString();
                    entry.state.revision = ++_appliedRevision;
                    _revisions[entry.id] = entry.state.revision;
                    clearTimeout(work.deadline);
                    entry.work = null;
                    window._fwFilterEngine.setLiveRows(entry.id, data.columns, data.rows);
                    // Consumers can synchronously commit a new selection on notify.
                    if (_registry[entry.id] === entry) { _paint(entry); _publish(entry); }
                });
            })['catch'](function(err) {
                if (!_owns(entry, work)) return;
                _finish(entry, work, err);
            });
        }

        window.addEventListener('pagehide', function() {
            _suspended = true;
            Object.keys(_registry).forEach(function(id) {
                var entry = _registry[id];
                entry.resumePending = entry.state.phase === 'pending';
                _cancelWork(entry);
                if (entry.state.phase === 'pending') {
                    entry.state.phase = entry.state.has_result ? 'ready' : 'idle';
                    _paint(entry); _publish(entry);
                }
            });
        });
        window.addEventListener('pageshow', function() {
            if (!_suspended) return;
            _suspended = false;
            Object.keys(_registry).forEach(function(id) {
                var entry = _registry[id];
                if (entry.owner && !entry.owner.isConnected) _dispose(entry);
                else if (entry.resumePending) {
                    entry.resumePending = false;
                    _schedule(id, true);
                }
            });
        });

        return {
            registerDataset: registerDataset,
            attachFilterBar: attachFilterBar,
            isArmed: isArmed,
            getDatasetState: getDatasetState,
            subscribeDataset: subscribeDataset,
            getCaptureStatus: function() {
                var pending = false, failure = '';
                Object.keys(_registry).forEach(function(id) {
                    var state = _registry[id].state;
                    if (state.phase === 'pending') pending = true;
                    else if (state.phase === 'error') failure = failure || state.error;
                });
                return {pending: pending, error: failure, revision: _appliedRevision};
            }
        };
    })();
