    // ── Toggle visibility helper ─────────────────────────
    function _updateToggleVis(toggleId, activeValue) {
        var targets = document.querySelectorAll('[data-toggle-target="' + toggleId + '"]');
        targets.forEach(function(el) {
            var isShowing = el.dataset.toggleValue === activeValue;
            var wasHidden = el.style.display === 'none';
            el.style.display = isShowing ? '' : 'none';
            // Charts rendered while the element was hidden (display:none) get
            // a 0×0 canvas from Chart.js. Resize them now that the canvas is visible.
            if (isShowing && wasHidden) {
                el.querySelectorAll('canvas').forEach(function(canvas) {
                    var chart = window._chartInstances && window._chartInstances[canvas.id];
                    if (chart) chart.resize();
                });
            }
        });
    }

    function _initToggleVis() {
        document.querySelectorAll('.fw-toggle-group').forEach(function(g) {
            var id = g.dataset.toggleId;
            if (!id) return;
            var active = g.querySelector('.fw-toggle-btn.active');
            if (active) _updateToggleVis(id, active.textContent.trim());
        });
    }

    window._initToggleVis = _initToggleVis;

    // ── Toggle click handler ──────────────────────────────
    document.addEventListener('click', function(e) {
        var btn = e.target.closest('.fw-toggle-btn');
        if (!btn) return;
        var group = btn.closest('.fw-toggle-group');
        if (!group) return;
        group.querySelectorAll('.fw-toggle-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var toggleId = group.dataset.toggleId;

        if (toggleId === '__scope__') {
            window._currentScope = btn.dataset.scopeKey || btn.textContent.trim();
            if (typeof window.renderAll === 'function') window.renderAll();
            if (typeof window._refreshAllAnnotations === 'function') {
                window._refreshAllAnnotations();
            }
            _updateToggleVis(toggleId, btn.textContent.trim());
            if (typeof window.onScopeChange === 'function') {
                window.onScopeChange(window._currentScope);
            }
            return;
        }

        _updateToggleVis(toggleId, btn.textContent.trim());

        if (toggleId && window._fwUrlSync && window._fwUrlSync._toggles[toggleId] !== undefined) {
            window._fwUrlSync.write();
        }

        if (toggleId && typeof window.onToggleChange === 'function') {
            window.onToggleChange(toggleId, btn.textContent.trim());
        }
    });

    // ── Tab click handler (button mode) ────────────────────
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.fw-tab-btn');
        if (!btn) return;
        const group = btn.closest('.fw-tab-group');
        if (!group) return;
        const btns = Array.from(group.querySelectorAll('.fw-tab-btn'));
        btns.forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        const idx = btns.indexOf(btn);
        const panels = group.parentElement.querySelectorAll('.fw-tab-panel');
        panels.forEach(function(p, i) { p.style.display = i === idx ? '' : 'none'; });
        if (window._fwUrlSync && group.dataset.tabId) window._fwUrlSync.write();
    });

    // ── Tab dropdown handler ─────────────────────────────
    document.addEventListener('change', function(e) {
        const sel = e.target.closest('.fw-tab-dropdown');
        if (!sel) return;
        const idx = parseInt(sel.value, 10);
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
        if (window._fwUrlSync && sel.dataset.tabId) window._fwUrlSync.write();
    });

    // ── Init Slim Select on tab dropdowns ──────────────────
    document.addEventListener('DOMContentLoaded', function() {
        if (typeof SlimSelect === 'undefined') return;
        document.querySelectorAll('.fw-tab-dropdown').forEach(function(sel) {
            var ssWrap = sel.parentElement;
            var ss = new SlimSelect({
                select: sel,
                settings: {
                    showSearch: false,
                    allowDeselect: false
                },
                events: {
                    afterChange: function(newVal) {
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            });
            if (ssWrap) ssWrap.classList.add('fw-tab-ss-wrap');
        });
    });

    // ── Theme dropdown handler (event delegation) ─────
    document.addEventListener('change', function(e) {
        if (e.target && e.target.id === 'fwThemeSelect') {
            switchTheme(e.target.value);
        }
    });

    // Sync theme dropdown value once DOM is ready
    document.addEventListener('DOMContentLoaded', function() {
        var sel = document.getElementById('fwThemeSelect');
        if (sel) {
            var active = _lsGet('fw-theme') || getActiveTheme();
            if (active && window._themes[active]) sel.value = active;
        }
    });

    // Refresh annotation chip colours whenever the theme changes so label
    // text (tick_color) and line colours re-resolve for the new theme.
    window.addEventListener('fw-theme-change', function() {
        if (typeof window._refreshAllAnnotations === 'function') {
            window._refreshAllAnnotations();
        }
    });

    // ── KPI mobile collapse toggle ───────────────────────
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.fw-kpi-more-btn');
        if (!btn) return;
        const grid = btn.previousElementSibling;
        if (!grid || !grid.classList.contains('fw-kpi-grid')) return;
        const open = grid.classList.toggle('open');
        btn.setAttribute('aria-expanded', open);
        const count = grid.querySelectorAll('.fw-kpi-extra').length;
        btn.textContent = open ? '− Show less' : '+ ' + count + ' more';
    });
