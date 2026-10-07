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

        var _registry = {};  // dsId -> binder state (see registerDataset)
        var _appliedRevision = 0;

        function _cookie(name) {
            var m = document.cookie.match(
                new RegExp('(?:^|; )' + name + '=([^;]*)'));
            return m ? decodeURIComponent(m[1]) : '';
        }

        function _datasetCount() {
            var n = 0;
            for (var k in _registry) if (_registry.hasOwnProperty(k)) n++;
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

        // ── Registration ──────────────────────────────────
        // Called once per LiveDataSource by its renderer
        // (components/live_data_source.js). Idempotent: a scope switch or
        // any other re-invocation of the whole render pass must not lose
        // in-flight/lastApplied state or double-register with the engine.
        function registerDataset(dsId, liveCfg) {
            if (_registry[dsId]) return;
            var schema = liveCfg.params || [];
            var suppliedDefaults = liveCfg.defaults || {};
            var defaults = {};
            for (var i = 0; i < schema.length; i++) {
                var name = schema[i].name;
                if (Object.prototype.hasOwnProperty.call(suppliedDefaults, name)) {
                    defaults[name] = suppliedDefaults[name];
                }
            }
            var entry = {
                cfg: liveCfg,
                defaults: defaults,
                lastApplied: defaults,
                seq: 0,
                latestSeq: 0,
                inflight: false,
                dirty: false,
                debounceTimer: null,
                retryTimer: null,
                failed: false,
                errorMessage: '',
                bar: null
            };
            _registry[dsId] = entry;
            window._fwFilterEngine.registerLiveDataset(dsId, {
                onChange: function() { _schedule(dsId); }
            });
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
            barEl.setAttribute('data-live-state', 'ready');
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
            return params;
        }

        function _sameParams(a, b) {
            var ak = Object.keys(a), bk = Object.keys(b);
            if (ak.length !== bk.length) return false;
            for (var i = 0; i < ak.length; i++) {
                if (a[ak[i]] !== b[ak[i]]) return false;
            }
            return true;
        }

        // ── Scheduling: trailing debounce + single in-flight + budget window ──
        function _schedule(dsId) {
            var entry = _registry[dsId];
            if (!entry || !_armed) return;
            if (entry.debounceTimer) return;  // a trailing debounce is already pending
            entry.debounceTimer = setTimeout(function() {
                entry.debounceTimer = null;
                _fire(dsId);
            }, DEBOUNCE_MS);
        }

        function _fire(dsId) {
            var entry = _registry[dsId];
            if (!entry) return;
            if (entry.inflight) { entry.dirty = true; return; }
            if (!_withinBudget(dsId)) {
                // This dataset has used its whole sliding-window share --
                // wait a beat and let the window slide rather than
                // hammering the server into its own 429 (which still
                // applies as the authoritative backstop regardless).
                entry.debounceTimer = setTimeout(function() {
                    entry.debounceTimer = null;
                    _fire(dsId);
                }, 1000);
                return;
            }
            var params = _collect(dsId);
            if (_sameParams(params, entry.lastApplied)) return;  // no-op suppression
            _recordRequest(dsId);
            _send(dsId, params, false);
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

        // ── Network ──
        function _send(dsId, params, isRetry) {
            var entry = _registry[dsId];
            entry.inflight = true;
            var mySeq = ++entry.seq;
            _status(entry, 'updating…', false);
            var headers = {'Content-Type': 'application/json'};
            var slug = document.body.getAttribute('data-report-slug') || '';
            if (slug) headers['X-Trellum-Report'] = slug;
            var csrf = _cookie('csrftoken');
            if (csrf) headers['X-CSRFToken'] = csrf;
            fetch(_url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: headers,
                body: JSON.stringify({query_id: entry.cfg.query_id, params: params})
            }).then(function(r) {
                if (r.status === 429 && !isRetry) {
                    var ra = parseInt(r.headers.get('Retry-After') || '1', 10);
                    if (isNaN(ra) || ra < 0) ra = 1;
                    _status(entry, 'busy — retrying', true);
                    entry.retryTimer = setTimeout(function() {
                        entry.retryTimer = null;
                        entry.inflight = false;
                        _send(dsId, params, true);
                    }, Math.min(ra, 30) * 1000);
                    return null;
                }
                if (!r.ok) {
                    return r.json()['catch'](function() { return {}; })
                        .then(function(body) {
                            throw new Error((body && body.error)
                                || ('Lookup failed (HTTP ' + r.status + ')'));
                        });
                }
                return r.json();
            }).then(function(data) {
                entry.inflight = false;
                if (!data) return;  // 429 path already rescheduled its own retry
                if (mySeq < entry.latestSeq) return;  // stale response for older params
                entry.latestSeq = mySeq;
                entry.lastApplied = params;
                entry.failed = false;
                entry.errorMessage = '';
                window._fwFilterEngine.setLiveRows(dsId, data.columns || [], data.rows || []);
                _appliedRevision++;
                if (entry.bar) entry.bar.setAttribute('data-live-state', 'live');
                _freshness(entry, 'live · just now' + (data.truncated ? ' · truncated' : ''));
                _status(entry, '', false);
            })['catch'](function(err) {
                entry.inflight = false;
                entry.failed = true;
                entry.errorMessage = (err && err.message) || 'Lookup failed';
                _status(entry, entry.errorMessage, true);
            }).then(function() {
                if (entry.dirty) {
                    entry.dirty = false;
                    _schedule(dsId);
                }
            });
        }

        return {
            registerDataset: registerDataset,
            attachFilterBar: attachFilterBar,
            isArmed: isArmed,
            getCaptureStatus: function() {
                var pending = false;
                var failure = '';
                for (var dsId in _registry) {
                    if (!_registry.hasOwnProperty(dsId)) continue;
                    var entry = _registry[dsId];
                    if (entry.debounceTimer || entry.retryTimer || entry.inflight || entry.dirty)
                        pending = true;
                    else if (entry.failed)
                        failure = failure || entry.errorMessage || 'A live data query failed.';
                }
                return {pending: pending, error: failure, revision: _appliedRevision};
            }
        };
    })();
