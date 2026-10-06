    // ── URL Filter Sync ────────────────────────────────
    // Reads/writes FilterBar state from/to URL query params
    // so users can share links with pre-applied filters.
    window._fwUrlSync = {
        _bars: [],

        register: function(dsId, filters, getState) {
            this._bars.push({dsId: dsId, filters: filters, getState: getState});
        },

        parse: function() {
            var params = {};
            var sp = new URLSearchParams(location.search);
            sp.forEach(function(val, key) { params[key] = val; });
            return params;
        },

        write: function() {
            var sp = new URLSearchParams();
            var multiBar = this._bars.length > 1;
            for (var b = 0; b < this._bars.length; b++) {
                var bar = this._bars[b];
                var state = bar.getState();
                for (var s = 0; s < state.length; s++) {
                    var e = state[s];
                    var key = multiBar ? bar.dsId + '.' + e.column : e.column;
                    if (e.type === 'toggle') {
                        if (e.value === '__all__') continue;
                        sp.set(key, e.value);
                    } else if (e.type === 'dropdown') {
                        if (!e.value || !e.value.length) continue;
                        sp.set(key, e.value.join(','));
                    } else if (e.type === 'flag') {
                        if (e.value === 'all') continue;
                        sp.set(key, e.value);
                    } else if (e.type === 'date_range') {
                        if (!e.value) continue;
                        var presetLabel = null;
                        document.querySelectorAll('.fw-date-range').forEach(function(dr) {
                            var ap = dr.querySelector('.fw-date-preset.active');
                            if (ap) presetLabel = ap.textContent.trim();
                        });
                        sp.set(key, presetLabel || (e.value.min + '..' + e.value.max));
                    } else if (e.type === 'slider') {
                        if (e.value === null || e.value === undefined || e.value === '') continue;
                        // Numeric range mode commits {min, max}; ordinal range
                        // mode commits the contiguous span as an array via
                        // 'in' -- encoded the same way, first..last category;
                        // single mode (numeric or ordinal) commits a plain
                        // stringified value via the 'equals' engine mode.
                        if (Array.isArray(e.value)) {
                            if (!e.value.length) continue;
                            sp.set(key, e.value[0] + '..' + e.value[e.value.length - 1]);
                        } else if (typeof e.value === 'object') {
                            sp.set(key, e.value.min + '..' + e.value.max);
                        } else {
                            sp.set(key, e.value);
                        }
                    }
                }
            }
            var toggles = this._toggles;
            var toggleIds = Object.keys(toggles);
            for (var ti = 0; ti < toggleIds.length; ti++) {
                var tid = toggleIds[ti];
                var tg = document.querySelector('[data-toggle-id="' + tid + '"]');
                if (!tg) continue;
                var activeBtn = tg.querySelector('.fw-toggle-btn.active');
                if (!activeBtn) continue;
                var val = activeBtn.textContent.trim();
                if (val !== toggles[tid]) sp.set('t.' + tid, val);
            }
            // Tabs (button mode)
            var tabs = this._tabs;
            document.querySelectorAll('.fw-tab-group[data-tab-id]').forEach(function(g) {
                var id = g.dataset.tabId;
                if (!id || !tabs.hasOwnProperty(id)) return;
                var activeBtn = g.querySelector('.fw-tab-btn.active');
                if (!activeBtn) return;
                var val = activeBtn.textContent.trim();
                if (val !== tabs[id]) sp.set('tab.' + id, val);
            });
            // Tabs (dropdown mode)
            document.querySelectorAll('.fw-tab-dropdown[data-tab-id]').forEach(function(sel) {
                var id = sel.dataset.tabId;
                if (!id || !tabs.hasOwnProperty(id)) return;
                if (sel.value !== tabs[id]) sp.set('tab.' + id, sel.value);
            });
            // AB modes
            document.querySelectorAll('.fw-ab-mode-toggle').forEach(function(toggle) {
                var btns = toggle.querySelectorAll('.fw-ab-mode-btn');
                if (!btns.length) return;
                var rootId = btns[0].getAttribute('data-ab-root');
                if (!rootId) return;
                btns.forEach(function(btn) {
                    if (btn.classList.contains('active')) {
                        var mode = btn.getAttribute('data-ab-mode');
                        var defBtn = toggle.querySelector('.fw-ab-mode-btn');
                        var defMode = defBtn ? defBtn.getAttribute('data-ab-mode') : '';
                        if (mode !== defMode) sp.set('ab.' + rootId, mode);
                    }
                });
            });
            // Annotations
            if (window._annoVisible) {
                var visTypes = [];
                var hasAnno = false;
                var defs = this._annoDefaults;
                var differs = false;
                for (var aType in window._annoVisible) {
                    if (aType === '_storageKey') continue;
                    hasAnno = true;
                    if (window._annoVisible[aType]) visTypes.push(aType);
                    if (defs && defs[aType] !== undefined && !!defs[aType] !== !!window._annoVisible[aType]) differs = true;
                }
                if (hasAnno && differs) sp.set('anno', visTypes.join(','));
            }
            // Custom state
            var customs = this._custom;
            for (var cKey in customs) {
                var cVal = customs[cKey]();
                if (cVal !== undefined && cVal !== null && cVal !== '') sp.set(cKey, cVal);
            }
            // `only` is not filter state, but it IS the page: rewriting the
            // URL without it would turn a deep link (or an embedded frame's
            // src) into the whole report on the next reload.
            var only = new URLSearchParams(location.search).get('only');
            if (only) sp.set('only', only);
            var qs = sp.toString();
            var newUrl = location.pathname + (qs ? '?' + qs : '');
            history.replaceState(null, '', newUrl);
        },

        decode: function(rawValue, filterType) {
            if (filterType === 'dropdown') {
                return rawValue ? rawValue.split(',') : [];
            } else if (filterType === 'date_range') {
                if (rawValue.indexOf('..') === -1) {
                    return { preset: rawValue };
                }
                var parts = rawValue.split('..');
                return { min: parts[0] || '', max: parts[1] || parts[0] || '' };
            } else if (filterType === 'slider') {
                if (rawValue.indexOf('..') === -1) {
                    return rawValue; // single mode: the raw stringified value
                }
                // Range mode: {min, max} as strings. Numeric or ordinal --
                // slider_filter.js's init() knows which (fc.ordinal) and
                // resolves ordinal min/max back to positions via fc.values.
                var sParts = rawValue.split('..');
                return { min: sParts[0] || '', max: sParts[1] || sParts[0] || '' };
            }
            return rawValue;
        },

        lookup: function(params, dsId, column) {
            if (params[dsId + '.' + column] !== undefined) return params[dsId + '.' + column];
            if (params[column] !== undefined) return params[column];
            return undefined;
        },

        _toggles: {},
        _tabs: {},
        _custom: {},
        _annoDefaults: null,

        registerToggle: function(id, defaultValue) {
            this._toggles[id] = defaultValue;
        },

        registerTab: function(id, defaultValue) {
            this._tabs[id] = defaultValue;
        },

        registerCustom: function(key, getter) {
            this._custom[key] = getter;
        },

        initToggles: function() {
            var params = this.parse();
            document.querySelectorAll('.fw-toggle-group[data-toggle-id]').forEach(function(g) {
                var id = g.dataset.toggleId;
                if (!id || id === '__scope__') return;
                var btns = g.querySelectorAll('.fw-toggle-btn');
                var defaultBtn = g.querySelector('.fw-toggle-btn.active');
                var defaultVal = defaultBtn ? defaultBtn.textContent.trim() : '';
                window._fwUrlSync.registerToggle(id, defaultVal);
                var urlVal = params['t.' + id];
                if (urlVal !== undefined) {
                    btns.forEach(function(btn) {
                        if (btn.textContent.trim() === urlVal) {
                            btns.forEach(function(b) { b.classList.remove('active'); });
                            btn.classList.add('active');
                        }
                    });
                }
            });
        },

        initTabs: function() {
            var params = this.parse();
            document.querySelectorAll('.fw-tab-group[data-tab-id]').forEach(function(g) {
                var id = g.dataset.tabId;
                if (!id) return;
                var btns = Array.from(g.querySelectorAll('.fw-tab-btn'));
                var activeBtn = g.querySelector('.fw-tab-btn.active');
                var defaultVal = activeBtn ? activeBtn.textContent.trim() : '';
                window._fwUrlSync.registerTab(id, defaultVal);
                var urlVal = params['tab.' + id];
                if (urlVal !== undefined) {
                    var idx = -1;
                    btns.forEach(function(btn, i) {
                        if (btn.textContent.trim() === urlVal) {
                            btns.forEach(function(b) { b.classList.remove('active'); });
                            btn.classList.add('active');
                            idx = i;
                        }
                    });
                    if (idx >= 0) {
                        var panels = g.parentElement.querySelectorAll('.fw-tab-panel');
                        panels.forEach(function(p, i) { p.style.display = i === idx ? '' : 'none'; });
                    }
                }
            });
            document.querySelectorAll('.fw-tab-dropdown[data-tab-id]').forEach(function(sel) {
                var id = sel.dataset.tabId;
                if (!id) return;
                window._fwUrlSync.registerTab(id, sel.value);
                var urlVal = params['tab.' + id];
                if (urlVal !== undefined) {
                    sel.value = urlVal;
                    var idx = parseInt(urlVal, 10);
                    if (!isNaN(idx)) {
                        var wrapper = sel.closest('.ss-main');
                        var el = (wrapper ? wrapper.parentElement : sel).nextElementSibling;
                        var pi = 0;
                        while (el) {
                            if (el.classList && el.classList.contains('fw-tab-panel')) {
                                el.style.display = pi === idx ? '' : 'none';
                                pi++;
                            }
                            el = el.nextElementSibling;
                        }
                    }
                }
            });
        },

        initAbModes: function() {
            var params = this.parse();
            document.querySelectorAll('.fw-ab-mode-toggle').forEach(function(toggle) {
                var btns = toggle.querySelectorAll('.fw-ab-mode-btn');
                if (!btns.length) return;
                var rootId = btns[0].getAttribute('data-ab-root');
                if (!rootId) return;
                var urlVal = params['ab.' + rootId];
                if (urlVal !== undefined) {
                    btns.forEach(function(btn) {
                        var mode = btn.getAttribute('data-ab-mode');
                        btn.classList.toggle('active', mode === urlVal);
                    });
                    document.querySelectorAll('#' + rootId + ' .fw-ab-mode-panel').forEach(function(p) {
                        p.classList.toggle('active', p.getAttribute('data-ab-mode-panel') === urlVal);
                    });
                }
            });
        },

        initAll: function() {
            this.initToggles();
            this.initTabs();
            this.initAbModes();
        }
    };

    document.addEventListener('DOMContentLoaded', function() {
        window._fwUrlSync.initAll();
    });
