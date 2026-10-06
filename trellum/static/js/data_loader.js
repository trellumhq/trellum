(function() {
    var loading = document.getElementById('fwLoading');
    window._scopeCache = {};
    window._fwTimings = {};
    var _t0 = performance.now();

    function _loadingMsg(msg) {
        var el = loading && loading.querySelector('.fw-loading-text');
        if (el) el.textContent = msg;
    }

    _loadingMsg('Loading data...');
    /* Cache-busting via ?v=<last_run>: read _meta.json (short cache) to learn
       the current data version, then fetch data.json?v=<version>. data.json
       can then be cached for 24h+ -- a new run produces a new URL. */
    fetch('_meta.json', {cache: 'no-store'})
        .then(function(r) { return r.ok ? r.json() : {}; })
        .catch(function() { return {}; })
        .then(function(meta) {
            window._dataVersion = (meta && meta.last_run) || String(Date.now());
            return fetch('data.json?v=' + encodeURIComponent(window._dataVersion));
        })
        .then(function(r) {
            window._fwTimings.fetch_ms = Math.round(performance.now() - _t0);
            return r.json();
        })
        .then(function(data) {
            window._fwTimings.parse_ms = Math.round(performance.now() - _t0 - window._fwTimings.fetch_ms);
            window._reportData = data;
            window._currentScope = window._currentScope || data.defaultScope;

            var comps = data.components || {};
            if (data.defaultScope) {
                window._scopeCache[data.defaultScope] = comps;
            }

            _loadingMsg('Building filters...');
            var _tRender = performance.now();
            _renderComps(comps);
            window._fwTimings.component_render_ms = Math.round(performance.now() - _tRender);
            if (window._fwChunkLoader && data._chunk_manifests) {
                window._fwChunkLoader.init(data);
            }

            if (loading) {
                loading.classList.add('fade-out');
                setTimeout(function() { loading.style.display = 'none'; }, 300);
            }

            window._fwTimings.total_ms = Math.round(performance.now() - _t0);
            console.log('[fw load]', JSON.parse(JSON.stringify(window._fwTimings)));

            if (typeof window._initToggleVis === 'function') window._initToggleVis();
            if (typeof window._buildAnnoToggleBar === 'function') window._buildAnnoToggleBar();
            if (window._fwUrlSync) window._fwUrlSync.write();
        })
        .catch(function(err) {
            console.error('Failed to load data.json:', err);
            if (loading) {
                loading.querySelector('.fw-loading-text').textContent = 'Failed to load data';
                loading.querySelector('.fw-spinner').style.display = 'none';
            }
        });

    /* ── Load scope data (cached or fetched) ── */
    window._loadScopeData = function(scope, callback) {
        if (window._scopeCache[scope]) {
            callback(window._scopeCache[scope]);
            return;
        }
        var files = (window._reportData && window._reportData._scopeFiles) || {};
        var file = files[scope];
        if (!file) {
            callback(window._reportData.components || {});
            return;
        }
        fetch(file + '?v=' + encodeURIComponent(window._dataVersion || Date.now()))
            .then(function(r) { return r.json(); })
            .then(function(scopeData) {
                var comps = scopeData.components || {};
                window._scopeCache[scope] = comps;
                callback(comps);
            })
            .catch(function(e) {
                console.warn('Failed to load scope data for ' + scope + ':', e);
                callback({});
            });
    };

    /* ── renderAll (used by scope switch + auto-refresh + theme switch) ── */
    window.renderAll = function() {
        var data = window._reportData;
        if (!data) return;

        Object.keys(window._chartInstances).forEach(function(id) {
            window._chartInstances[id].destroy();
            delete window._chartInstances[id];
        });

        var scope = window._currentScope || data.defaultScope;

        window._loadScopeData(scope, function(comps) {
            _renderComps(comps);
            if (typeof window._initToggleVis === 'function') window._initToggleVis();
            if (typeof window._buildAnnoToggleBar === 'function') window._buildAnnoToggleBar();
            if (window._fwUrlSync) window._fwUrlSync.write();
            _applyThemeColors();
            _attachFilterHealthBadges(comps);
        });
    };

    function _applyThemeColors() {
        if (typeof window.getActiveTheme !== 'function' || typeof window._themes === 'undefined') return;
        var activeTheme = window.getActiveTheme();
        var tc = window._themes[activeTheme];
        if (!tc) return;
        var instances = window._chartInstances || {};
        Object.keys(instances).forEach(function(id) {
            var chart = instances[id];
            if (!chart || !chart.data) return;
            var isDoughnut = chart.config.type === 'doughnut' || chart.config.type === 'pie';
            var ds0 = chart.data.datasets[0];
            if (ds0 && ds0._themeManaged && Array.isArray(ds0.backgroundColor)) {
                if (isDoughnut) {
                    ds0.backgroundColor = ds0.backgroundColor.map(function(_, i) {
                        return tc.chart_colors[i % tc.chart_colors.length];
                    });
                    var bgCard = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#fff';
                    ds0.borderColor = bgCard;
                    if (chart.options && chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
                        chart.options.plugins.legend.labels.color = tc.tick_color;
                    }
                } else if (ds0._funnelSegments) {
                    ds0.backgroundColor = ds0.backgroundColor.map(function(_, i) {
                        return tc.chart_colors[i % tc.chart_colors.length] + 'cc';
                    });
                    if (Array.isArray(ds0.borderColor)) {
                        ds0.borderColor = ds0.borderColor.map(function(_, i) {
                            return tc.chart_colors[i % tc.chart_colors.length];
                        });
                    }
                }
            } else if (!isDoughnut) {
                chart.data.datasets.forEach(function(ds, i) {
                    if (!ds._themeManaged) return;
                    var newColor = tc.chart_colors[i % tc.chart_colors.length];
                    ds.borderColor = newColor;
                    if (chart.config.type === 'line') {
                        ds.backgroundColor = newColor + '22';
                    } else {
                        ds.backgroundColor = newColor + 'cc';
                    }
                });
            }
            if (chart.options && chart.options.scales) {
                ['x', 'y'].forEach(function(axis) {
                    if (chart.options.scales[axis]) {
                        if (chart.options.scales[axis].grid) chart.options.scales[axis].grid.color = tc.grid_color;
                        if (chart.options.scales[axis].ticks) chart.options.scales[axis].ticks.color = tc.tick_color;
                    }
                });
            }
            chart.update('none');
        });
    }

    /* ── Two-pass dispatch: priority components first, charts/tables deferred ──

       The invariant: anything that REGISTERS a dataset, or drives filtering,
       must render before anything that READS one.

       Deferring a registrar does not merely delay it. A live consumer in the
       priority pass then reads a dataset the filter engine has never heard of
       and paints zeros -- which looks like real data rather than missing data,
       so nobody notices until they check it against the source. A blank chart
       gets reported; a KPI reading 0 does not.

       Every new data-registering component belongs in this list. */
    var _PRIORITY_TYPES = {
        /* registrars: populate the filter engine */
        data_source: true, scoped_data_source: true, live_data_source: true,
        /* filter UI: must exist before consumers read filtered rows */
        filter_bar: true,
        /* live consumers: cheap to render, and painting them late flashes */
        kpi: true, kpi_row_live: true, mini_kpi: true
    };

    var _lazyQueue = {};
    var _lazyObserver = (typeof IntersectionObserver !== 'undefined')
        ? new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (!entry.isIntersecting) return;
                var id = entry.target.id;
                var pending = _lazyQueue[id];
                if (!pending) return;
                delete _lazyQueue[id];
                _lazyObserver.unobserve(entry.target);
                var renderer = window._fwRenderers[pending.type];
                if (renderer) renderer(id, pending);
            });
        }, {rootMargin: '200px'})
        : null;

    function _renderComps(comps) {
        var deferred = [];
        Object.keys(comps).forEach(function(id) {
            var cfg = comps[id];
            if (_PRIORITY_TYPES[cfg.type]) {
                var el = document.getElementById(id);
                if (!el) return;
                var renderer = window._fwRenderers[cfg.type];
                if (renderer) renderer(id, cfg);
            } else {
                deferred.push(id);
            }
        });
        _attachFilterHealthBadges(comps);
        if (deferred.length === 0) return;
        _loadingMsg('Rendering charts...');
        (window.requestIdleCallback || setTimeout)(function() {
            for (var i = 0; i < deferred.length; i++) {
                var id = deferred[i];
                var cfg = comps[id];
                var el = document.getElementById(id);
                if (!el) continue;
                var inCollapsed = el.closest('.fw-collapsed');
                var rect = el.getBoundingClientRect();
                var offScreen = rect.top > window.innerHeight + 200;
                var toggleVis = el.closest('.fw-toggle-vis');
                var inHiddenToggle = toggleVis && toggleVis.style.display === 'none';
                if (_lazyObserver && (inCollapsed || offScreen || inHiddenToggle)) {
                    _lazyQueue[id] = cfg;
                    _lazyObserver.observe(el);
                } else {
                    var renderer = window._fwRenderers[cfg.type];
                    if (renderer) renderer(id, cfg);
                }
            }
            _attachFilterHealthBadges(comps);
        }, {timeout: 50});
    }

    /* ── Per-chart filter health badges ──────────────────────
       Admin-only — these are diagnostic, not for end-users. The
       badge is hidden by default and only attached when the
       current viewer is an admin, resolved via an /api/auth/me
       request to whatever host is serving the report (a fixed,
       unprefixed path -- see apps/core/views.py auth_me -- so the
       page reports which studio it's asking about via ?path=,
       rather than the endpoint carrying org/studio in its own URL).
       With no host configured (the `extensions` block is empty) no
       request is made at all; force-enable the badges with ?health=1
       in the URL, which is the documented path for local dev and
       direct file:// access.

       Healthy charts get no badge (no visual noise). When at least
       one filter does NOT reach the chart, a visible warning badge
       is injected — text + count, not a cryptic dot. Hover opens a
       styled popover listing each filter with status + reason. */
    function _filterHealthAdminCheck(callback) {
        if (window._fwHealthAdminResolved) {
            callback(window._fwHealthAdmin === true);
            return;
        }
        // ?health=1 / ?health=0 takes precedence over auth.
        try {
            var qp = new URLSearchParams(window.location.search);
            if (qp.get('health') === '1') {
                window._fwHealthAdmin = true;
                window._fwHealthAdminResolved = true;
                callback(true);
                return;
            }
            if (qp.get('health') === '0') {
                window._fwHealthAdmin = false;
                window._fwHealthAdminResolved = true;
                callback(false);
                return;
            }
        } catch (e) {}
        // No host in front of this report means no one to ask, and no
        // request worth making -- standalone reports must not fire at
        // endpoints that aren't there. Use ?health=1 to see the badges.
        if (!window._fwHasHost) {
            window._fwHealthAdmin = false;
            window._fwHealthAdminResolved = true;
            callback(false);
            return;
        }
        // Ask the host who I am. If it doesn't implement the endpoint this
        // 404s/errors and we simply don't show badges -- r.ok/catch both
        // resolve to `me = null` below, never a thrown/unhandled rejection,
        // so this degrades silently on every host that has no such route.
        var meUrl = '/api/auth/me?path=' + encodeURIComponent(window.location.pathname);
        fetch(meUrl, {credentials: 'same-origin'})
            .then(function(r) { return r.ok ? r.json() : null; })
            .catch(function() { return null; })
            .then(function(me) {
                var roles = (me && me.roles) || [];
                var isAdmin = !!(me && (me.is_admin === true || roles.indexOf('admin') !== -1));
                window._fwHealthAdmin = isAdmin;
                window._fwHealthAdminResolved = true;
                callback(isAdmin);
            });
    }
    function _filterHealthCounts(filters) {
        var counts = {active: 0, propagated: 0, inactive: 0, untracked: 0};
        for (var i = 0; i < filters.length; i++) {
            counts[filters[i].status] = (counts[filters[i].status] || 0) + 1;
        }
        return counts;
    }
    function _buildFilterHealthPopover(filters) {
        var pop = document.createElement('div');
        pop.className = 'fw-fhb-popover';
        var head = document.createElement('div');
        head.className = 'fw-fhb-popover-head';
        head.textContent = 'Filter coverage';
        pop.appendChild(head);
        var ul = document.createElement('ul');
        ul.className = 'fw-fhb-popover-list';
        var glyph = {active: '✓', propagated: '↗', inactive: '✗', untracked: '·'};
        for (var i = 0; i < filters.length; i++) {
            var f = filters[i];
            var li = document.createElement('li');
            li.className = 'fw-fhb-popover-item fw-fhb-st-' + f.status;
            var g = document.createElement('span');
            g.className = 'fw-fhb-popover-glyph';
            g.textContent = glyph[f.status] || '?';
            var col = document.createElement('span');
            col.className = 'fw-fhb-popover-col';
            col.textContent = f.column;
            var st = document.createElement('span');
            st.className = 'fw-fhb-popover-status';
            st.textContent = f.status;
            li.appendChild(g);
            li.appendChild(col);
            li.appendChild(st);
            if (f.reason) {
                var reason = document.createElement('div');
                reason.className = 'fw-fhb-popover-reason';
                reason.textContent = f.reason;
                li.appendChild(reason);
            }
            ul.appendChild(li);
        }
        pop.appendChild(ul);
        return pop;
    }
    function _attachFilterHealthBadges(comps) {
        var data = window._reportData;
        if (!data || !data._filter_health) return;
        _filterHealthAdminCheck(function(isAdmin) {
            if (!isAdmin) {
                // Strip any badges that may have been injected before
                // a slower auth response resolved as non-admin.
                document.querySelectorAll('.fw-filter-health-badge').forEach(function(b) {
                    b.remove();
                });
                var oldBanner = document.querySelector('.fw-mock-banner');
                if (oldBanner) oldBanner.remove();
                return;
            }
            _attachMockBanner(data);
            _attachFilterHealthBadgesInner(comps, data._filter_health);
        });
    }
    function _attachMockBanner(data) {
        if (!data._is_mock_data) return;
        if (document.querySelector('.fw-mock-banner')) return; // dedup
        var banner = document.createElement('div');
        banner.className = 'fw-mock-banner';
        banner.textContent = 'Mock data — row counts, totals, and chart values '
            + 'are from synthetic test DataFrames (~30 rows each). '
            + 'Filter wiring is checked, but the numbers below are not real.';
        var host = document.querySelector('.fw-container') || document.body;
        if (host && host.firstChild) {
            host.insertBefore(banner, host.firstChild);
        } else if (host) {
            host.appendChild(banner);
        }
    }
    /* Component types that have no visible chart surface — skipping
       them keeps badges from stacking up before hidden data_source
       divs at the top of each section. */
    var _FHB_SKIP_TYPES = {data_source: 1, filter_bar: 1};
    function _attachFilterHealthBadgesInner(comps, health) {
        Object.keys(comps).forEach(function(id) {
            var cfg = comps[id];
            if (!cfg || !cfg.dataset_id) return;
            if (_FHB_SKIP_TYPES[cfg.type]) return;
            var filters = health[cfg.dataset_id];
            if (!filters || !filters.length) return;
            var el = document.getElementById(id);
            if (!el) return;
            var target;
            if (el.tagName === 'CANVAS') {
                target = el.closest('.fw-chart-container');
                if (!target) target = el.parentNode;
            } else {
                target = el;
            }
            if (!target) return;

            // Drop any badge already sitting just before this target —
            // both the priority pass and the deferred pass call this
            // function, and a previously-rendered scope can leave a
            // stale badge in place.
            var prev = target.previousElementSibling;
            if (prev && prev.classList && prev.classList.contains('fw-filter-health-badge')) {
                prev.remove();
            }

            var counts = _filterHealthCounts(filters);
            // Healthy chart — emit nothing. No green clutter on good charts.
            if (counts.inactive === 0 && counts.untracked === 0) {
                return;
            }

            var status, label;
            if (counts.inactive > 0) {
                status = 'inactive';
                label = '⚠ ' + counts.inactive + '/' + filters.length
                    + (counts.inactive === 1 ? ' filter dead' : ' filters dead');
            } else {
                // Pure untracked (RawHTML / ABCompare with custom JS)
                status = 'untracked';
                label = 'filter wiring untracked';
            }

            var badge = document.createElement('span');
            badge.className = 'fw-filter-health-badge fw-fhb-' + status;
            badge.textContent = label;
            badge.setAttribute('aria-label', 'Filter coverage: ' + status);

            var popover = _buildFilterHealthPopover(filters);
            badge.appendChild(popover);

            // Place badge OUTSIDE the chart container so it can't
            // overlap axis labels or chart content. Insert it as a
            // sibling immediately before the container — sits in
            // its own little row between the title and the chart.
            if (target.parentNode) {
                target.parentNode.insertBefore(badge, target);
            } else {
                target.appendChild(badge);
            }
        });
    }
    window._attachFilterHealthBadges = _attachFilterHealthBadges;

    /* ── Collapsible sections ── */
    document.addEventListener('click', function(e) {
        var toggle = e.target.closest('.fw-collapsible-toggle');
        if (!toggle) return;
        var section = toggle.closest('.fw-collapsible');
        if (section) section.classList.toggle('fw-collapsed');
    });
})();
